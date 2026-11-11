from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
from typing import Any

from autoresearch.artifacts import resolve_artifact, verify_artifact
from autoresearch.contracts import JsonObject
from autoresearch.governance import (
    RESERVATION_BUDGET_FIELDS,
    budget_cap_allows,
    budget_cap_exhausted,
    remaining_budget,
)
from autoresearch.io import append_jsonl_locked, atomic_write_json, read_json, read_jsonl
from autoresearch.project import ProjectLayout, load_graph, load_research_state
from autoresearch.workflow import (
    _reject_other_pending_intents,
    initialize_baseline_state,
    state_mutation_transaction_id,
    validate_active_evaluation_spec,
    validate_baseline_state_commit,
)


CANDIDATE_BUDGET_FIELDS = (
    RESERVATION_BUDGET_FIELDS - {"loop-subagent-bound"}
)


CANDIDATE_FIELDS = frozenset(
    {
        "candidate_id",
        "kind",
        "status",
        "parent_id",
        "hypothesis_id",
        "search_role",
        "mechanism",
        "primary_change",
        "data_intervention",
        "objective",
        "expected_observation",
        "falsification_condition",
        "evaluation_spec_id",
        "budget_slice",
        "code_artifact_ids",
        "configuration_artifact_ids",
        "run_artifact_ids",
        "evaluation_report_ids",
        "scientific_depth",
        "debug_depth",
    }
)
CANDIDATE_KINDS = frozenset(
    {
        "baseline",
        "draft",
        "architecture-model",
        "objective-loss",
        "data-augmentation",
        "optimization-hyperparameters",
        "inference-post-processing",
        "efficiency-systems",
        "refine",
        "debug",
        "ablate",
        "replicate",
        "pivot",
    }
)
CANDIDATE_STATUSES = frozenset(
    {"draft", "official", "completed", "discarded", "inconclusive"}
)
SEARCH_ROLES = frozenset({"exploit", "explore", "falsify"})
PATCH_FIELDS = frozenset(
    {
        "status",
        "evaluation_spec_id",
        "budget_slice",
        "code_artifact_ids",
        "configuration_artifact_ids",
        "run_artifact_ids",
        "evaluation_report_ids",
    }
)
ARTIFACT_FIELDS = (
    "code_artifact_ids",
    "configuration_artifact_ids",
    "run_artifact_ids",
    "evaluation_report_ids",
)
VERDICTS = frozenset(
    {"keep", "discard", "inconclusive", "debuggable", "needs_user", "failed"}
)
VERDICT_STATUSES = {
    "keep": "completed",
    "discard": "discarded",
    "inconclusive": "inconclusive",
    "debuggable": "completed",
    "needs_user": "official",
    "failed": "discarded",
}
CANDIDATE_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "transaction_id",
        "phase",
        "event",
        "state_transaction_id",
        "baseline_pointer",
    }
)


def create_candidate(layout: ProjectLayout, proposal: Mapping[str, Any]) -> JsonObject:
    candidate = _normalize_candidate(proposal)
    event: JsonObject = {
        "schema_version": 1,
        "record_type": "candidate-create",
        "candidate": candidate,
    }
    intent_path = _candidate_operation_path(layout, event)
    with _exclusive_lock(_workflow_lock_path(layout)):
        if intent_path.is_file():
            intent = _load_candidate_operation(intent_path, event)
        else:
            _reject_other_pending_intents(layout, None)
            existing = load_candidates(layout)
            known = existing.get(candidate["candidate_id"])
            if known is not None:
                raise ValueError("candidate id already exists")
            _validate_new_candidate(layout, candidate, existing)
            _validate_official_envelope(layout, candidate, existing)
            state_transaction_id = None
            baseline_pointer = None
            if candidate["kind"] == "baseline":
                state = load_research_state(layout)
                baseline_pointer = {
                    "candidate_id": candidate["candidate_id"],
                    "report_artifact_id": None,
                    "evaluation_cycle_checkpoint_id": None,
                    "promoted_at": datetime.now(timezone.utc).isoformat(),
                }
                state_transaction_id = state_mutation_transaction_id(
                    "initialize-baseline",
                    state["revision"],
                    {
                        "initial_baseline": candidate["candidate_id"],
                        "current_best": baseline_pointer,
                    },
                )
            intent = _prepare_candidate_operation(
                intent_path,
                event,
                state_transaction_id,
                baseline_pointer,
            )
        _reject_other_pending_intents(
            layout, _candidate_allowed_transaction_ids(intent)
        )
        intent = _resume_candidate_event(layout, intent_path, intent)
    if candidate["kind"] == "baseline" and intent["phase"] != "committed":
        _recover_baseline_pointer(
            layout,
            candidate,
            intent["baseline_pointer"],
            intent["transaction_id"],
        )
        with _exclusive_lock(_workflow_lock_path(layout)):
            intent = _load_candidate_operation(intent_path, event)
            _reject_other_pending_intents(
                layout, _candidate_allowed_transaction_ids(intent)
            )
            intent = _commit_baseline_candidate_operation(
                layout, intent_path, intent
            )
    return load_candidates(layout)[candidate["candidate_id"]]


def update_candidate(
    layout: ProjectLayout, candidate_id: str, patch: Mapping[str, Any]
) -> JsonObject:
    candidate_id = _nonempty_string(candidate_id, "candidate_id")
    normalized_patch = _normalize_patch(patch)
    event: JsonObject = {
        "schema_version": 1,
        "record_type": "candidate-update",
        "candidate_id": candidate_id,
        "patch": normalized_patch,
    }
    intent_path = _candidate_operation_path(layout, event)
    with _exclusive_lock(_workflow_lock_path(layout)):
        if intent_path.is_file():
            intent = _load_candidate_operation(intent_path, event)
        else:
            _reject_other_pending_intents(layout, None)
            candidates = load_candidates(layout)
            current = candidates.get(candidate_id)
            if current is None:
                raise ValueError(f"unknown candidate: {candidate_id}")
            updated = dict(current)
            updated.update(normalized_patch)
            _validate_update(current, updated, normalized_patch)
            _validate_artifact_references(layout, updated)
            if updated["status"] == "official":
                _validate_official_candidate(layout, updated)
                _validate_official_envelope(layout, updated, candidates)
            intent = _prepare_candidate_operation(
                intent_path, event, None, None
            )
        _reject_other_pending_intents(
            layout, intent["transaction_id"]
        )
        _resume_candidate_event(layout, intent_path, intent)
        return load_candidates(layout)[candidate_id]


def record_verdict(
    layout: ProjectLayout,
    candidate_id: str,
    verdict: str,
    evidence_ids: Sequence[str],
) -> JsonObject:
    candidate_id = _nonempty_string(candidate_id, "candidate_id")
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict: {verdict}")
    normalized_evidence = _normalize_artifact_ids(evidence_ids, "evidence_ids")
    event: JsonObject = {
        "schema_version": 1,
        "record_type": "candidate-verdict",
        "candidate_id": candidate_id,
        "verdict": verdict,
        "evidence_ids": normalized_evidence,
    }
    intent_path = _candidate_operation_path(layout, event)
    with _exclusive_lock(_workflow_lock_path(layout)):
        if intent_path.is_file():
            intent = _load_candidate_operation(intent_path, event)
        else:
            _reject_other_pending_intents(layout, None)
            candidates = load_candidates(layout)
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise ValueError(f"unknown candidate: {candidate_id}")
            if candidate["status"] != "official":
                raise ValueError("only official candidates accept a verdict")
            if candidate["kind"] == "debug" and verdict == "keep":
                raise ValueError("debug candidate cannot be kept as a scientific result")
            _validate_artifact_ids(layout, normalized_evidence)
            intent = _prepare_candidate_operation(
                intent_path, event, None, None
            )
        _reject_other_pending_intents(
            layout, intent["transaction_id"]
        )
        _resume_candidate_event(layout, intent_path, intent)
    return event


def load_candidates(layout: ProjectLayout) -> dict[str, JsonObject]:
    candidates: dict[str, JsonObject] = {}
    for row in read_jsonl(layout.journal):
        record_type = row.get("record_type")
        if record_type == "candidate-create":
            _replay_create(row, candidates)
        elif record_type == "candidate-update":
            _replay_update(row, candidates)
        elif record_type == "candidate-verdict":
            _replay_verdict(row, candidates)
    return candidates


def select_parallel_candidates(
    candidates: Sequence[Mapping[str, Any]], slots: int = 3
) -> list[JsonObject]:
    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 0:
        raise ValueError("slots must be a non-negative integer")
    selected: list[JsonObject] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    exploit = _pick_best(candidates, "exploit", seen, selected)
    if exploit is not None and len(selected) < slots:
        selected.append(exploit)
        seen.add(_duplicate_key(exploit))
    if len(selected) < slots:
        explore = _pick_best(candidates, "explore", seen, selected)
        if explore is not None:
            selected.append(explore)
            seen.add(_duplicate_key(explore))
    if len(selected) < slots:
        falsify = _pick_best_union(candidates, seen, selected)
        if falsify is not None:
            selected.append(falsify)
    return selected[:slots]


def evaluate_stop_conditions(
    state: Mapping[str, Any], budget: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
) -> list[str]:
    if not isinstance(state, Mapping):
        raise ValueError("state must be an object")
    if not isinstance(budget, Mapping):
        raise ValueError("budget must be an object")
    reasons: list[str] = []
    if state.get("success_reached") is True:
        reasons.append("success criterion reached")
    if state.get("pending_gate") or state.get("boundary_change"):
        reasons.append("pending human gate")
    remaining = _remaining_budget(budget)
    if "wall_seconds" in remaining and budget_cap_exhausted(
        remaining["wall_seconds"]
    ):
        reasons.append("time budget exhausted")
    explicit_remaining = budget.get("remaining")
    if (
        isinstance(explicit_remaining, Mapping)
        and "official_candidates" in explicit_remaining
    ):
        official_remaining = explicit_remaining["official_candidates"]
    else:
        limits = budget.get("limits")
        official_limit = (
            limits.get("official_candidates")
            if isinstance(limits, Mapping)
            else None
        )
        official_remaining = (
            official_limit - _official_candidate_count(candidates)
            if isinstance(official_limit, int)
            and not isinstance(official_limit, bool)
            else None
        )
    if budget_cap_exhausted(official_remaining):
        reasons.append("official candidate budget exhausted")
    if state.get("converged_without_material_gain") is True:
        reasons.append("convergence without material gain")
    if not select_parallel_candidates(candidates, slots=1):
        reasons.append("no valid independent candidate")
    if state.get("user_stop_requested") is True:
        reasons.append("user stop requested")
    return reasons


def _normalize_candidate(proposal: Mapping[str, Any]) -> JsonObject:
    if not isinstance(proposal, Mapping):
        raise ValueError("candidate proposal must be an object")
    missing = CANDIDATE_FIELDS - proposal.keys()
    unknown = proposal.keys() - CANDIDATE_FIELDS
    if missing:
        raise ValueError(f"missing candidate field: {sorted(missing)[0]}")
    if unknown:
        raise ValueError(f"unknown candidate field: {sorted(unknown)[0]}")
    candidate: JsonObject = {
        field: proposal[field] for field in CANDIDATE_FIELDS
    }
    for field in (
        "candidate_id",
        "hypothesis_id",
        "primary_change",
        "data_intervention",
        "objective",
        "expected_observation",
        "falsification_condition",
    ):
        candidate[field] = _nonempty_string(candidate[field], field)
    mechanism = candidate["mechanism"]
    if not isinstance(mechanism, str):
        raise ValueError("mechanism must be a string")
    if not isinstance(candidate["kind"], str) or candidate["kind"] not in CANDIDATE_KINDS:
        raise ValueError(f"unknown candidate kind: {candidate['kind']}")
    if not isinstance(candidate["status"], str) or candidate["status"] not in CANDIDATE_STATUSES:
        raise ValueError(f"unknown candidate status: {candidate['status']}")
    parent_id = candidate["parent_id"]
    if parent_id is not None:
        candidate["parent_id"] = _nonempty_string(parent_id, "parent_id")
    role = candidate["search_role"]
    if role is not None and (not isinstance(role, str) or role not in SEARCH_ROLES):
        raise ValueError("search_role must be null, exploit, explore, or falsify")
    evaluation_spec = candidate["evaluation_spec_id"]
    if evaluation_spec is not None:
        candidate["evaluation_spec_id"] = _nonempty_string(
            evaluation_spec, "evaluation_spec_id"
        )
    candidate["budget_slice"] = _normalize_budget_slice(candidate["budget_slice"])
    for field in ARTIFACT_FIELDS:
        candidate[field] = _normalize_artifact_ids(candidate[field], field)
    for field in ("scientific_depth", "debug_depth"):
        depth = candidate[field]
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
            raise ValueError(f"{field} must be a non-negative integer")
    if candidate["kind"] == "debug" and candidate["debug_depth"] == 0:
        raise ValueError("debug candidate must have positive debug_depth")
    return _json_copy(candidate, "candidate")


def _validate_new_candidate(
    layout: ProjectLayout, candidate: Mapping[str, Any], existing: Mapping[str, JsonObject]
) -> None:
    is_baseline = candidate["kind"] == "baseline"
    if is_baseline:
        if candidate["parent_id"] is not None or candidate["mechanism"]:
            raise ValueError("baseline requires null parent_id and empty mechanism")
        if any(item["kind"] == "baseline" for item in existing.values()):
            raise ValueError("second baseline requires an explicit new research cycle")
    else:
        parent_id = candidate["parent_id"]
        if parent_id is None:
            raise ValueError("non-baseline candidate requires parent_id")
        if parent_id == candidate["candidate_id"]:
            raise ValueError("candidate cannot be its own parent")
        if parent_id not in existing:
            raise ValueError(f"unknown parent candidate: {parent_id}")
        debug_status = search_status(layout)
        if parent_id in debug_status["blocked_candidate_ids"]:
            parent = existing[parent_id]
            if (
                parent_id not in debug_status["pending_debug_parent_ids"]
                or candidate["kind"] != "debug"
                or candidate["debug_depth"] != parent["debug_depth"] + 1
            ):
                raise ValueError("pending debug child is required")
        if not candidate["mechanism"]:
            raise ValueError("non-baseline candidate requires mechanism")
    _validate_artifact_references(layout, candidate)
    if candidate["status"] == "official":
        _validate_official_candidate(layout, candidate)
    _validate_debug_budget(layout, candidate, existing)


def _validate_update(
    current: Mapping[str, Any], updated: Mapping[str, Any], patch: Mapping[str, Any]
) -> None:
    if "status" in patch:
        if current["status"] != "draft" or updated["status"] != "official":
            raise ValueError("candidate status is immutable outside draft-to-official")
    if current["status"] in {"completed", "discarded", "inconclusive"}:
        raise ValueError("terminal candidate cannot be updated")
    if current["status"] == "official" and (
        "budget_slice" in patch or "evaluation_spec_id" in patch
    ):
        raise ValueError("official candidate configuration is immutable")
    for field in ARTIFACT_FIELDS:
        if field in patch and current[field] != updated[field][: len(current[field])]:
            raise ValueError(f"{field} is append-only")
    _validate_debug_budget_placeholder(updated)


def _validate_official_candidate(layout: ProjectLayout, candidate: Mapping[str, Any]) -> None:
    if candidate["kind"] == "baseline":
        return
    evaluation_spec_id = candidate["evaluation_spec_id"]
    if evaluation_spec_id is None:
        raise ValueError("official non-baseline candidate requires evaluation_spec_id")
    validate_active_evaluation_spec(layout, evaluation_spec_id)
    if not candidate["budget_slice"]:
        raise ValueError("official non-baseline candidate requires budget_slice")


def _validate_official_envelope(
    layout: ProjectLayout,
    candidate: Mapping[str, Any],
    existing: Mapping[str, JsonObject],
) -> None:
    if candidate["status"] != "official" or candidate["kind"] == "baseline":
        return
    _validate_official_candidate(layout, candidate)
    budget = read_json(layout.budget)
    if budget.get("status") != "approved":
        raise PermissionError("budget approval required")
    limits = budget.get("limits", {})
    reserved = budget.get("reserved", {})
    consumed = budget.get("consumed", {})
    for field, amount in candidate["budget_slice"].items():
        if field == "gpu_type":
            continue
        if not budget_cap_allows(
            limits.get(field),
            consumed.get(field, 0),
            reserved.get(field, 0),
            amount,
        ):
            raise ValueError("candidate budget_slice exceeds remaining budget")
    if (
        "gpu_type" in candidate["budget_slice"]
        and candidate["budget_slice"]["gpu_type"] != limits.get("gpu_type")
    ):
        raise ValueError("candidate budget_slice.gpu_type does not match approved policy")
    projected = dict(existing)
    projected[candidate["candidate_id"]] = dict(candidate)
    official_count = _official_candidate_count(projected.values())
    if official_count > limits.get("official_candidates", 0):
        raise ValueError("official candidate budget exhausted")


def _validate_debug_budget(
    layout: ProjectLayout,
    candidate: Mapping[str, Any],
    existing: Mapping[str, JsonObject],
) -> None:
    _validate_debug_budget_placeholder(candidate)
    if candidate["kind"] != "debug":
        return
    graph = load_graph(layout.graph)
    graph_limits = graph.get("limits")
    max_debug_depth = (
        graph_limits.get("max_debug_depth")
        if isinstance(graph_limits, Mapping)
        else None
    )
    if (
        isinstance(max_debug_depth, bool)
        or not isinstance(max_debug_depth, int)
        or max_debug_depth < 0
    ):
        raise ValueError(
            "graph limits.max_debug_depth must be a non-negative integer"
        )
    if candidate["debug_depth"] > max_debug_depth:
        raise ValueError("debug depth exceeds graph limits.max_debug_depth")
    budget = read_json(layout.budget)
    if budget.get("status") != "approved":
        return
    limits = budget.get("limits")
    debug_limit = (
        limits.get("debug_candidates")
        if isinstance(limits, Mapping)
        else None
    )
    if (
        isinstance(debug_limit, bool)
        or not isinstance(debug_limit, int)
        or debug_limit < 0
    ):
        raise ValueError(
            "debug_candidates must be a non-negative integer"
        )
    debug_count = sum(
        item["kind"] == "debug" for item in existing.values()
    )
    if candidate["candidate_id"] not in existing:
        debug_count += 1
    if debug_count > debug_limit:
        raise ValueError("debug candidate budget exhausted")


def _validate_debug_budget_placeholder(candidate: Mapping[str, Any]) -> None:
    if candidate["kind"] == "debug" and candidate["debug_depth"] == 0:
        raise ValueError("debug candidate must have positive debug_depth")


def _validate_artifact_references(layout: ProjectLayout, candidate: Mapping[str, Any]) -> None:
    for field in ARTIFACT_FIELDS:
        _validate_artifact_ids(layout, candidate[field])


def _validate_artifact_ids(layout: ProjectLayout, artifact_ids: Sequence[str]) -> None:
    for artifact_id in artifact_ids:
        resolve_artifact(layout, artifact_id)
        verification = verify_artifact(layout, artifact_id)
        if verification["available"] is not True or verification["valid"] is not True:
            raise ValueError(f"artifact is not available and valid: {artifact_id}")


def _normalize_patch(patch: Mapping[str, Any]) -> JsonObject:
    if not isinstance(patch, Mapping) or not patch:
        raise ValueError("candidate patch must be a non-empty object")
    unknown = patch.keys() - PATCH_FIELDS
    if unknown:
        if next(iter(unknown)) in CANDIDATE_FIELDS:
            raise ValueError(f"immutable candidate field: {sorted(unknown)[0]}")
        raise ValueError(f"unknown candidate patch field: {sorted(unknown)[0]}")
    normalized: JsonObject = {}
    for field, value in patch.items():
        if field == "status":
            if value not in CANDIDATE_STATUSES:
                raise ValueError(f"unknown candidate status: {value}")
            normalized[field] = value
        elif field == "evaluation_spec_id":
            normalized[field] = (
                None if value is None else _nonempty_string(value, field)
            )
        elif field == "budget_slice":
            normalized[field] = _normalize_budget_slice(value)
        else:
            normalized[field] = _normalize_artifact_ids(value, field)
    return _json_copy(normalized, "candidate patch")


def _normalize_budget_slice(value: Any) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError("budget_slice must be an object")
    unknown = value.keys() - CANDIDATE_BUDGET_FIELDS
    if unknown:
        raise ValueError(f"budget_slice has unknown field: {sorted(unknown)[0]}")
    normalized: JsonObject = {}
    for field, amount in value.items():
        if field == "gpu_type":
            if amount is not None and (not isinstance(amount, str) or not amount):
                raise ValueError("budget_slice.gpu_type must be null or a non-empty string")
        elif (
            isinstance(amount, bool)
            or not isinstance(amount, Real)
            or not math.isfinite(amount)
            or amount < 0
        ):
            raise ValueError(f"budget_slice.{field} must be a non-negative finite number")
        normalized[field] = amount
    return normalized


def _normalize_artifact_ids(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty artifact ids")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicate artifact ids")
    return list(value)


def _recover_baseline_pointer(
    layout: ProjectLayout,
    candidate: Mapping[str, Any],
    baseline_pointer: Mapping[str, Any],
    owner_transaction_id: str | None = None,
) -> None:
    if candidate["kind"] != "baseline":
        return
    state = load_research_state(layout)
    initial = state.get("initial_baseline")
    provisional = state.get("provisional_best")
    current = state.get("current_best")
    if (
        initial == candidate["candidate_id"]
        and current == baseline_pointer
        and provisional is None
    ):
        return
    if initial is not None or provisional is not None or current is not None:
        raise ValueError("baseline state pointers conflict")
    initialize_baseline_state(
        layout,
        candidate["candidate_id"],
        baseline_pointer,
        state["revision"],
        owner_transaction_id,
    )


def _replay_create(row: Mapping[str, Any], candidates: dict[str, JsonObject]) -> None:
    if set(row) != {"schema_version", "record_type", "candidate"}:
        raise ValueError("candidate-create event has invalid schema")
    if row["schema_version"] != 1:
        raise ValueError("candidate-create event has unsupported schema version")
    candidate = _normalize_candidate(row["candidate"])
    candidate_id = candidate["candidate_id"]
    if candidate_id in candidates:
        raise ValueError(f"duplicate candidate-create event: {candidate_id}")
    candidates[candidate_id] = candidate


def _replay_update(row: Mapping[str, Any], candidates: dict[str, JsonObject]) -> None:
    if set(row) != {"schema_version", "record_type", "candidate_id", "patch"}:
        raise ValueError("candidate-update event has invalid schema")
    if row["schema_version"] != 1:
        raise ValueError("candidate-update event has unsupported schema version")
    candidate_id = _nonempty_string(row["candidate_id"], "candidate_id")
    current = candidates.get(candidate_id)
    if current is None:
        raise ValueError(f"candidate-update event has unknown candidate: {candidate_id}")
    patch = _normalize_patch(row["patch"])
    updated = dict(current)
    updated.update(patch)
    _validate_update(current, updated, patch)
    candidates[candidate_id] = updated


def _replay_verdict(row: Mapping[str, Any], candidates: dict[str, JsonObject]) -> None:
    required = {"schema_version", "record_type", "candidate_id", "verdict", "evidence_ids"}
    if set(row) != required:
        raise ValueError("candidate-verdict event has invalid schema")
    if row["schema_version"] != 1 or row["verdict"] not in VERDICTS:
        raise ValueError("candidate-verdict event is invalid")
    candidate_id = _nonempty_string(row["candidate_id"], "candidate_id")
    _normalize_artifact_ids(row["evidence_ids"], "evidence_ids")
    candidate = candidates.get(candidate_id)
    if candidate is None or candidate["status"] != "official":
        raise ValueError("candidate-verdict event has invalid candidate status")
    if candidate["kind"] == "debug" and row["verdict"] == "keep":
        raise ValueError("debug candidate cannot be kept as a scientific result")
    updated = dict(candidate)
    updated["status"] = VERDICT_STATUSES[row["verdict"]]
    candidates[candidate_id] = updated


def _pick_best(
    all_candidates: Sequence[Mapping[str, Any]],
    role: str,
    seen: set[tuple[str, str, str, str, str]],
    selected: Sequence[Mapping[str, Any]],
) -> JsonObject | None:
    choices: list[JsonObject] = []
    selected_mechanisms = {
        _normalized_text(item.get("mechanism", "")) for item in selected
    }
    for candidate in all_candidates:
        if not isinstance(candidate, Mapping) or not _is_selectable(candidate, role):
            continue
        key = _duplicate_key(candidate)
        mechanism = _normalized_text(candidate.get("mechanism", ""))
        if key in seen or (role == "explore" and mechanism in selected_mechanisms):
            continue
        choices.append(dict(candidate))
    if not choices:
        return None
    return sorted(
        choices,
        key=lambda item: (-_priority(item), item["candidate_id"]),
    )[0]


def _pick_best_union(
    all_candidates: Sequence[Mapping[str, Any]],
    seen: set[tuple[str, str, str, str, str]],
    selected: Sequence[Mapping[str, Any]],
) -> JsonObject | None:
    choices = [
        dict(candidate)
        for candidate in all_candidates
        if isinstance(candidate, Mapping)
        and (
            _is_selectable(candidate, "falsify")
            or _is_selectable(candidate, "ablate")
        )
        and _duplicate_key(candidate) not in seen
    ]
    if not choices:
        return None
    return sorted(choices, key=lambda item: (-_priority(item), item["candidate_id"]))[0]


def search_status(layout: ProjectLayout) -> JsonObject:
    candidates = load_candidates(layout)
    pending = sorted(
        candidate_id
        for candidate_id, candidate in candidates.items()
        if candidate["status"] == "completed"
        and _last_candidate_verdict(layout, candidate_id) == "debuggable"
        and not _has_satisfying_debug_child(candidates, candidate_id)
    )
    blocked = set(pending)
    changed = True
    while changed:
        changed = False
        for candidate_id, candidate in candidates.items():
            if (
                candidate_id not in blocked
                and candidate.get("parent_id") in blocked
            ):
                blocked.add(candidate_id)
                changed = True
    obligations = [
        {
            "parent_id": candidate_id,
            "required_kind": "debug",
            "required_debug_depth": candidates[candidate_id][
                "debug_depth"
            ]
            + 1,
        }
        for candidate_id in pending
    ]
    return {
        "pending_debug_parent_ids": pending,
        "pending_debug_obligations": obligations,
        "blocked_candidate_ids": sorted(blocked),
    }


def _last_candidate_verdict(layout: ProjectLayout, candidate_id: str) -> str | None:
    verdicts = [
        row.get("verdict") for row in read_jsonl(layout.journal)
        if row.get("record_type") == "candidate-verdict"
        and row.get("candidate_id") == candidate_id
    ]
    return verdicts[-1] if verdicts else None


def _has_satisfying_debug_child(
    candidates: Mapping[str, JsonObject], parent_id: str
) -> bool:
    parent = candidates[parent_id]
    return any(
        child["parent_id"] == parent_id
        and child["kind"] == "debug"
        and child["debug_depth"] == parent["debug_depth"] + 1
        for child in candidates.values()
    )


def _is_selectable(candidate: Mapping[str, Any], role: str) -> bool:
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        return False
    status = candidate.get("status")
    if status in {"completed", "discarded", "inconclusive"}:
        return False
    if role == "ablate":
        return candidate.get("kind") == "ablate"
    return candidate.get("search_role") == role


def _priority(candidate: Mapping[str, Any]) -> float:
    value = candidate.get("priority", 0)
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        return 0.0
    return float(value)


def _duplicate_key(candidate: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    primary_change = _normalized_text(candidate.get("primary_change", ""))
    return (
        _normalized_text(candidate.get("hypothesis_id", "")),
        _normalized_text(candidate.get("kind", "")),
        hashlib.sha256(primary_change.encode("utf-8")).hexdigest(),
        _normalized_text(candidate.get("data_intervention", "")),
        _normalized_text(candidate.get("objective", "")),
    )


def _normalized_text(value: Any) -> str:
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def _remaining_budget(budget: Mapping[str, Any]) -> Mapping[str, Any]:
    return remaining_budget(budget)


def _official_candidate_count(
    candidates: Sequence[Mapping[str, Any]],
) -> int:
    return sum(
        candidate.get("kind") not in {"baseline", "debug"}
        and candidate.get("status") != "draft"
        for candidate in candidates
    )


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _json_copy(value: Mapping[str, Any], name: str) -> JsonObject:
    try:
        copied = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain JSON values") from error
    return copied


def _candidate_operation_path(layout: ProjectLayout, event: Mapping[str, Any]) -> Path:
    identity = hashlib.sha256(
        json.dumps(
            dict(event),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return layout.root / ".research" / "workflow-transactions" / f"candidate-{identity}.json"


def _prepare_candidate_operation(
    path: Path,
    event: Mapping[str, Any],
    state_transaction_id: str | None,
    baseline_pointer: Mapping[str, Any] | None,
) -> JsonObject:
    intent: JsonObject = {
        "schema_version": 1,
        "kind": "candidate-operation",
        "transaction_id": path.stem,
        "phase": "prepared",
        "event": dict(event),
        "state_transaction_id": state_transaction_id,
        "baseline_pointer": (
            dict(baseline_pointer) if baseline_pointer is not None else None
        ),
    }
    atomic_write_json(path, intent)
    return intent


def _candidate_allowed_transaction_ids(
    intent: Mapping[str, Any],
) -> list[str]:
    allowed = [intent["transaction_id"]]
    state_transaction_id = intent.get("state_transaction_id")
    if isinstance(state_transaction_id, str):
        allowed.append(state_transaction_id)
    return allowed


def _load_candidate_operation(
    path: Path,
    event: Mapping[str, Any],
) -> JsonObject:
    intent = read_json(path)
    is_baseline = (
        event.get("record_type") == "candidate-create"
        and event.get("candidate", {}).get("kind") == "baseline"
    )
    baseline_pointer = intent.get("baseline_pointer")
    if (
        set(intent) != CANDIDATE_INTENT_FIELDS
        or intent.get("schema_version") != 1
        or intent.get("kind") != "candidate-operation"
        or intent.get("transaction_id") != path.stem
        or intent.get("event") != event
        or intent.get("phase")
        not in {
            "prepared",
            "event_appended",
            "state_committed",
            "committed",
        }
        or (
            is_baseline
            and not isinstance(intent.get("state_transaction_id"), str)
        )
        or (
            is_baseline
            and (
                not isinstance(baseline_pointer, Mapping)
                or set(baseline_pointer) != {
                    "candidate_id",
                    "report_artifact_id",
                    "evaluation_cycle_checkpoint_id",
                    "promoted_at",
                }
                or baseline_pointer.get("candidate_id")
                != event["candidate"]["candidate_id"]
                or baseline_pointer.get("report_artifact_id") is not None
                or baseline_pointer.get("evaluation_cycle_checkpoint_id")
                is not None
                or not isinstance(baseline_pointer.get("promoted_at"), str)
                or not baseline_pointer["promoted_at"]
            )
        )
        or (
            not is_baseline
            and (
                intent.get("state_transaction_id") is not None
                or baseline_pointer is not None
            )
        )
    ):
        raise ValueError("candidate operation intent conflicts")
    return intent


def _resume_candidate_event(
    layout: ProjectLayout,
    path: Path,
    intent: JsonObject,
) -> JsonObject:
    event = intent["event"]
    matching = [row for row in read_jsonl(layout.journal) if row == event]
    if len(matching) > 1:
        raise ValueError("candidate operation was appended more than once")
    if not matching:
        if intent["phase"] != "prepared":
            raise ValueError("candidate operation event phase conflicts")
        append_jsonl_locked(layout.journal, event)
    if intent["phase"] == "prepared":
        intent = dict(intent)
        intent["phase"] = "event_appended"
        atomic_write_json(path, intent)
    is_baseline = (
        event.get("record_type") == "candidate-create"
        and event.get("candidate", {}).get("kind") == "baseline"
    )
    if is_baseline:
        if intent["phase"] not in {
            "event_appended",
            "state_committed",
            "committed",
        }:
            raise ValueError("baseline candidate operation phase conflicts")
        return intent
    if intent["phase"] == "state_committed":
        raise ValueError("non-baseline candidate has state phase")
    if intent["phase"] == "event_appended":
        intent = dict(intent)
        intent["phase"] = "committed"
        atomic_write_json(path, intent)
    return intent


def _commit_baseline_candidate_operation(
    layout: ProjectLayout,
    path: Path,
    intent: JsonObject,
) -> JsonObject:
    intent = _load_candidate_operation(path, intent["event"])
    if intent["phase"] == "committed":
        validate_baseline_state_commit(
            layout,
            intent["event"]["candidate"]["candidate_id"],
            intent["state_transaction_id"],
            intent["baseline_pointer"],
        )
        return intent
    if intent["phase"] == "event_appended":
        validate_baseline_state_commit(
            layout,
            intent["event"]["candidate"]["candidate_id"],
            intent["state_transaction_id"],
            intent["baseline_pointer"],
        )
        intent = dict(intent)
        intent["phase"] = "state_committed"
        atomic_write_json(path, intent)
    if intent["phase"] == "state_committed":
        validate_baseline_state_commit(
            layout,
            intent["event"]["candidate"]["candidate_id"],
            intent["state_transaction_id"],
            intent["baseline_pointer"],
        )
        intent = dict(intent)
        intent["phase"] = "committed"
        atomic_write_json(path, intent)
    return intent


def _workflow_lock_path(layout: ProjectLayout) -> Path:
    return layout.root / ".research" / "workflow.lock"


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
