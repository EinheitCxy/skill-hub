from __future__ import annotations
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from numbers import Real
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4
import yaml
from autoresearch.artifacts import (
    list_artifacts,
    register_artifact,
    resolve_artifact,
    verify_artifact,
)
from autoresearch.contracts import JsonObject, TransitionResult
from autoresearch.governance import (
    ADDITIVE_BUDGET_FIELDS,
    inspect_budget_settlement,
    plan_budget_settlement,
    read_approved_gate,
    require_execution_approval,
    reserve_budget,
    resolve_budget_settlement,
    settle_budget,
    settle_budget_for_completion,
)
from autoresearch.io import (
    append_jsonl_locked,
    atomic_write_json,
    ensure_within,
    read_json,
    read_jsonl,
    sha256_file,
)
from autoresearch.project import (
    ProjectLayout,
    load_research_state,
    validate_graph,
    validate_research_state_document,
)

from .recovery import (
    _capture_graph,
    _exclusive_lock,
    _hash_payload,
    _is_rfc3339,
    _json_copy,
    _reject_other_pending_intents,
    _require_exact_fields,
    _require_nonnegative_integer,
    _require_sha256,
    _required_string,
    _safe_id,
    _state_integrity_error,
    _timestamp,
    _validate_intent_header,
    _workflow_lock_path,
    _workflow_transactions_root,
)

EVALUATION_CYCLE_CHECKPOINT_FIELDS = frozenset(
    {
        "schema_version",
        "record_type",
        "checkpoint_id",
        "cycle_id",
        "evaluation_spec_id",
        "evaluation_spec_artifact_id",
        "evaluation_spec_artifact_sha256",
        "comparison_candidate_id",
        "comparison_report_artifact_id",
        "comparison_state_revision",
        "comparison_state_hash",
        "bound_at",
    }
)


EVALUATION_CYCLE_IDENTITY_FIELDS = (
    "schema_version",
    "record_type",
    "cycle_id",
    "evaluation_spec_id",
    "evaluation_spec_artifact_id",
    "evaluation_spec_artifact_sha256",
    "comparison_candidate_id",
    "comparison_report_artifact_id",
    "comparison_state_revision",
    "comparison_state_hash",
)


EVALUATION_CYCLE_BIND_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "transaction_id",
        "phase",
        "checkpoint_hash",
        "checkpoint",
        "artifact_id",
    }
)


CONTROL_SNAPSHOT_IDENTITY_FIELDS = (
    "revision",
    "state_hash",
    "graph_hash",
    "budget_hash",
    "ledger_line_counts",
)


def create_checkpoint(
    layout: ProjectLayout, reason: str
) -> JsonObject:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("checkpoint reason must be a non-empty string")
    with _exclusive_lock(_workflow_lock_path(layout)):
        _reject_other_pending_intents(layout, None)
        state = load_research_state(layout)
        state_hash = sha256_file(layout.state)
        _, graph_hash = _capture_graph(layout)
        integrity_error = _state_integrity_error(
            layout, state, state_hash, graph_hash
        )
        if integrity_error is not None:
            raise ValueError(integrity_error)
        return _create_checkpoint_unlocked(
            layout,
            reason.strip(),
            state,
            state_hash,
            graph_hash,
        )


def bind_evaluation_cycle_checkpoint(
    layout: ProjectLayout, cycle_id: str
) -> JsonObject:
    cycle_id = _safe_id(cycle_id, "cycle_id")
    transaction_id = "evaluation-cycle-bind-" + _hash_payload(
        {"cycle_id": cycle_id}
    )
    intent_path = (
        _workflow_transactions_root(layout) / f"{transaction_id}.json"
    )
    checkpoint_path = (
        layout.cycles / cycle_id / "evaluation-comparison.json"
    )
    with _exclusive_lock(_workflow_lock_path(layout)):
        _reject_other_pending_intents(layout, transaction_id)
        state = load_research_state(layout)
        state_hash = sha256_file(layout.state)
        _, graph_hash = _capture_graph(layout)
        integrity_error = _state_integrity_error(
            layout, state, state_hash, graph_hash
        )
        if integrity_error is not None:
            raise ValueError(integrity_error)
        anchor = state.get("active_evaluation_spec")
        if not isinstance(anchor, Mapping):
            raise ValueError("active EvaluationSpec anchor is required")
        normalized_anchor, specification = _validate_evaluation_spec_anchor(
            layout, anchor
        )
        current_best = state.get("current_best")
        if not isinstance(current_best, Mapping):
            raise ValueError("Current Best is not initialized")
        comparison_candidate_id = _safe_id(
            current_best.get("candidate_id"),
            "current_best.candidate_id",
        )
        if specification.get("baseline_candidate_id") != state.get(
            "initial_baseline"
        ):
            raise ValueError(
                "EvaluationSpec baseline_candidate_id does not match "
                "initial baseline"
            )
        comparison_report_artifact_id = current_best.get(
            "report_artifact_id"
        )
        current_checkpoint_id = current_best.get(
            "evaluation_cycle_checkpoint_id"
        )
        if comparison_report_artifact_id is None:
            if (
                comparison_candidate_id != state.get("initial_baseline")
                or current_checkpoint_id is not None
            ):
                raise ValueError(
                    "null comparison report is allowed only for baseline "
                    "bootstrap"
                )
        else:
            _safe_id(
                comparison_report_artifact_id,
                "current_best.report_artifact_id",
            )
            _safe_id(
                current_checkpoint_id,
                "current_best.evaluation_cycle_checkpoint_id",
            )
            _validate_current_best_comparison_evidence(
                layout, current_best
            )
        identity: JsonObject = {
            "schema_version": 1,
            "record_type": "evaluation-cycle-checkpoint",
            "cycle_id": cycle_id,
            "evaluation_spec_id": normalized_anchor[
                "evaluation_spec_id"
            ],
            "evaluation_spec_artifact_id": normalized_anchor["artifact_id"],
            "evaluation_spec_artifact_sha256": normalized_anchor[
                "artifact_sha256"
            ],
            "comparison_candidate_id": comparison_candidate_id,
            "comparison_report_artifact_id": (
                comparison_report_artifact_id
            ),
            "comparison_state_revision": state["revision"],
            "comparison_state_hash": state_hash,
        }
        checkpoint_id = "evaluation-cycle-" + _hash_payload(identity)
        if intent_path.is_file():
            intent = _validate_evaluation_cycle_bind_intent(
                layout, intent_path, read_json(intent_path)
            )
            checkpoint = intent["checkpoint"]
            if any(
                checkpoint[field] != identity[field]
                for field in EVALUATION_CYCLE_IDENTITY_FIELDS
            ):
                raise ValueError(
                    "evaluation cycle checkpoint identity conflicts"
                )
            return _resume_evaluation_cycle_bind(
                layout, intent_path, intent
            )
        if checkpoint_path.exists():
            raise ValueError(
                "evaluation cycle checkpoint exists without durable intent"
            )
        checkpoint = {
            **identity,
            "checkpoint_id": checkpoint_id,
            "bound_at": _timestamp(),
        }
        checkpoint = validate_evaluation_cycle_checkpoint(
            checkpoint,
            initial_baseline_candidate_id=state["initial_baseline"],
        )
        intent: JsonObject = {
            "schema_version": 1,
            "kind": "evaluation-cycle-bind",
            "transaction_id": transaction_id,
            "phase": "prepared",
            "checkpoint_hash": _hash_payload(checkpoint),
            "checkpoint": checkpoint,
            "artifact_id": None,
        }
        atomic_write_json(intent_path, intent)
        return _resume_evaluation_cycle_bind(
            layout, intent_path, intent
        )


def validate_evaluation_cycle_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    initial_baseline_candidate_id: str | None = None,
) -> JsonObject:
    if not isinstance(checkpoint, Mapping):
        raise ValueError("evaluation cycle checkpoint must be an object")
    _require_exact_fields(
        checkpoint,
        EVALUATION_CYCLE_CHECKPOINT_FIELDS,
        "evaluation cycle checkpoint",
    )
    normalized = _json_copy(
        checkpoint, "evaluation cycle checkpoint"
    )
    schema_version = normalized["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise ValueError(
            "evaluation cycle checkpoint schema_version must be integer 1"
        )
    if normalized["record_type"] != "evaluation-cycle-checkpoint":
        raise ValueError(
            "evaluation cycle checkpoint record_type is invalid"
        )
    for field in (
        "cycle_id",
        "evaluation_spec_id",
        "evaluation_spec_artifact_id",
        "comparison_candidate_id",
    ):
        _safe_id(normalized[field], f"evaluation cycle checkpoint {field}")
    for field in (
        "evaluation_spec_artifact_sha256",
        "comparison_state_hash",
    ):
        _require_sha256(
            normalized[field], f"evaluation cycle checkpoint {field}"
        )
    report_artifact_id = normalized["comparison_report_artifact_id"]
    if report_artifact_id is not None:
        _safe_id(
            report_artifact_id,
            "evaluation cycle checkpoint comparison_report_artifact_id",
        )
    else:
        initial_baseline_candidate_id = _safe_id(
            initial_baseline_candidate_id,
            "initial_baseline_candidate_id",
        )
        if (
            normalized["comparison_candidate_id"]
            != initial_baseline_candidate_id
        ):
            raise ValueError(
                "evaluation cycle checkpoint null comparison report is "
                "allowed only for baseline bootstrap"
            )
    _require_nonnegative_integer(
        normalized["comparison_state_revision"],
        "evaluation cycle checkpoint comparison_state_revision",
    )
    checkpoint_id = normalized["checkpoint_id"]
    expected_id = "evaluation-cycle-" + _hash_payload(
        {
            field: normalized[field]
            for field in EVALUATION_CYCLE_IDENTITY_FIELDS
        }
    )
    if checkpoint_id != expected_id:
        raise ValueError(
            "evaluation cycle checkpoint deterministic ID conflicts"
        )
    if not _is_rfc3339(normalized["bound_at"]):
        raise ValueError(
            "evaluation cycle checkpoint bound_at must be RFC-3339"
        )
    return normalized


def resolve_evaluation_cycle_checkpoint(
    layout: ProjectLayout, checkpoint_id: str
) -> JsonObject:
    checkpoint_id = _safe_id(checkpoint_id, "checkpoint_id")
    state = load_research_state(layout)
    matches: list[JsonObject] = []
    for path in sorted(
        layout.cycles.glob("*/evaluation-comparison.json")
    ):
        ensure_within(layout.cycles, path)
        checkpoint = validate_evaluation_cycle_checkpoint(
            read_json(path),
            initial_baseline_candidate_id=state["initial_baseline"],
        )
        if checkpoint["checkpoint_id"] == checkpoint_id:
            matches.append(checkpoint)
    if len(matches) != 1:
        raise ValueError(
            f"evaluation cycle checkpoint is not uniquely resolvable: "
            f"{checkpoint_id}"
        )
    checkpoint = matches[0]
    _require_evaluation_cycle_artifact(layout, checkpoint)
    return checkpoint


def _resume_evaluation_cycle_bind(
    layout: ProjectLayout,
    intent_path: Path,
    intent: Mapping[str, Any],
) -> JsonObject:
    intent = _validate_evaluation_cycle_bind_intent(
        layout, intent_path, intent
    )
    checkpoint = intent["checkpoint"]
    checkpoint_path = (
        layout.cycles
        / checkpoint["cycle_id"]
        / "evaluation-comparison.json"
    )
    if checkpoint_path.is_file():
        if read_json(checkpoint_path) != checkpoint:
            raise ValueError("evaluation cycle checkpoint file conflicts")
    else:
        atomic_write_json(checkpoint_path, checkpoint)
    if intent["phase"] == "prepared":
        intent = dict(intent)
        intent["phase"] = "checkpoint_written"
        atomic_write_json(intent_path, intent)

    artifact = _find_evaluation_cycle_artifact(layout, checkpoint)
    if artifact is None:
        artifact = register_artifact(
            layout,
            checkpoint_path,
            {
                "type": "evaluation-cycle-checkpoint",
                "producer": "research-coordinator",
                "storage": "snapshot",
                "cycle_id": checkpoint["cycle_id"],
                "provenance_tags": [
                    "evaluation-comparison",
                    checkpoint["checkpoint_id"],
                ],
                "comparability_tags": [
                    checkpoint["evaluation_spec_id"],
                    checkpoint["comparison_candidate_id"],
                ],
            },
        )
    artifact_id = artifact["artifact_id"]
    if intent["artifact_id"] not in {None, artifact_id}:
        raise ValueError(
            "evaluation cycle checkpoint artifact identity conflicts"
        )
    if intent["phase"] == "checkpoint_written":
        intent = dict(intent)
        intent["artifact_id"] = artifact_id
        intent["phase"] = "artifact_registered"
        atomic_write_json(intent_path, intent)
    if intent["phase"] != "committed":
        intent = dict(intent)
        intent["artifact_id"] = artifact_id
        intent["phase"] = "committed"
        atomic_write_json(intent_path, intent)
    return dict(checkpoint)


def _validate_evaluation_cycle_bind_intent(
    layout: ProjectLayout,
    intent_path: Path,
    intent: Mapping[str, Any],
) -> JsonObject:
    _validate_intent_header(intent, intent_path)
    _require_exact_fields(
        intent,
        EVALUATION_CYCLE_BIND_INTENT_FIELDS,
        "evaluation cycle bind intent",
    )
    state = load_research_state(layout)
    checkpoint = validate_evaluation_cycle_checkpoint(
        intent.get("checkpoint"),
        initial_baseline_candidate_id=state["initial_baseline"],
    )
    normalized = _json_copy(
        intent, "evaluation cycle bind intent"
    )
    normalized["checkpoint"] = checkpoint
    expected_transaction_id = "evaluation-cycle-bind-" + _hash_payload(
        {"cycle_id": checkpoint["cycle_id"]}
    )
    if normalized["transaction_id"] != expected_transaction_id:
        raise ValueError(
            "evaluation cycle bind transaction identity conflicts"
        )
    if normalized["checkpoint_hash"] != _hash_payload(checkpoint):
        raise ValueError(
            "evaluation cycle bind checkpoint hash conflicts"
        )
    artifact_id = normalized["artifact_id"]
    if artifact_id is not None:
        _safe_id(artifact_id, "evaluation cycle bind artifact_id")
    phase = normalized["phase"]
    if phase in {"artifact_registered", "committed"} and artifact_id is None:
        raise ValueError(
            "evaluation cycle bind phase requires artifact identity"
        )
    if phase in {"prepared", "checkpoint_written"} and artifact_id is not None:
        raise ValueError(
            "evaluation cycle bind artifact identity conflicts with phase"
        )
    return normalized


def _find_evaluation_cycle_artifact(
    layout: ProjectLayout,
    checkpoint: Mapping[str, Any],
) -> JsonObject | None:
    candidates = [
        artifact
        for artifact in list_artifacts(
            layout, {"type": "evaluation-cycle-checkpoint"}
        )
        if artifact.get("cycle_id") == checkpoint["cycle_id"]
    ]
    matching: list[JsonObject] = []
    for artifact in candidates:
        verification = verify_artifact(
            layout, artifact["artifact_id"]
        )
        if (
            verification.get("available") is True
            and verification.get("valid") is True
            and read_json(Path(artifact["path"])) == checkpoint
        ):
            matching.append(artifact)
    if len(matching) > 1:
        raise ValueError(
            "evaluation cycle checkpoint has duplicate artifacts"
        )
    if candidates and not matching:
        raise ValueError(
            "evaluation cycle checkpoint artifact conflicts"
        )
    return matching[0] if matching else None


def _require_evaluation_cycle_artifact(
    layout: ProjectLayout,
    checkpoint: Mapping[str, Any],
) -> JsonObject:
    artifact = _find_evaluation_cycle_artifact(layout, checkpoint)
    if artifact is None:
        raise ValueError(
            "evaluation cycle checkpoint artifact is missing"
        )
    return artifact


def _validate_current_best_comparison_evidence(
    layout: ProjectLayout,
    current_best: Mapping[str, Any],
) -> None:
    candidate_id = _safe_id(
        current_best.get("candidate_id"),
        "current_best.candidate_id",
    )
    report_artifact_id = _safe_id(
        current_best.get("report_artifact_id"),
        "current_best.report_artifact_id",
    )
    checkpoint_id = _safe_id(
        current_best.get("evaluation_cycle_checkpoint_id"),
        "current_best.evaluation_cycle_checkpoint_id",
    )
    resolve_evaluation_cycle_checkpoint(layout, checkpoint_id)
    artifact = resolve_artifact(layout, report_artifact_id)
    verification = verify_artifact(layout, report_artifact_id)
    if (
        artifact.get("type") != "evaluation-report"
        or verification.get("available") is not True
        or verification.get("valid") is not True
    ):
        raise ValueError("Current Best evaluation report is invalid")
    payload = read_json(Path(_required_string(artifact, "path")))
    if (
        not isinstance(payload, Mapping)
        or payload.get("candidate_id") != candidate_id
        or payload.get("evaluation_cycle_checkpoint_id") != checkpoint_id
        or artifact.get("candidate_id") not in {None, candidate_id}
    ):
        raise ValueError(
            "Current Best evaluation report provenance conflicts"
        )


def _validate_evaluation_spec_anchor(
    layout: ProjectLayout,
    anchor: Mapping[str, Any],
) -> tuple[JsonObject, JsonObject]:
    required = {
        "evaluation_spec_id",
        "artifact_id",
        "artifact_sha256",
        "gate_id",
        "gate_payload_hash",
    }
    if not isinstance(anchor, Mapping) or set(anchor) != required:
        raise ValueError("active EvaluationSpec anchor has invalid schema")
    normalized_anchor = _json_copy(anchor, "active EvaluationSpec anchor")
    for field in required:
        _required_string(normalized_anchor, field)
    artifact = resolve_artifact(layout, normalized_anchor["artifact_id"])
    verification = verify_artifact(layout, normalized_anchor["artifact_id"])
    if (
        artifact.get("type") != "evaluation-spec"
        or artifact.get("sha256") != normalized_anchor["artifact_sha256"]
        or verification.get("available") is not True
        or verification.get("valid") is not True
    ):
        raise ValueError("active EvaluationSpec artifact is invalid")
    try:
        specification = read_json(Path(_required_string(artifact, "path")))
    except (OSError, json.JSONDecodeError, TypeError) as error:
        raise ValueError(
            f"active EvaluationSpec artifact JSON is invalid: {error}"
        ) from error
    if not isinstance(specification, Mapping):
        raise ValueError("active EvaluationSpec artifact JSON must be an object")
    specification = _json_copy(
        specification, "active EvaluationSpec artifact payload"
    )
    approved = read_approved_gate(
        layout, normalized_anchor["gate_id"], "evaluation"
    )
    if approved["payload_hash"] != normalized_anchor["gate_payload_hash"]:
        raise ValueError("active EvaluationSpec gate payload hash mismatch")
    gate_payload = approved["payload"]
    if set(gate_payload) == {"schema_version", "stage_gate", "evaluation_spec"}:
        gate_anchor = gate_payload.get("evaluation_spec")
        specification_hash = _hash_payload(specification)
        if (
            gate_payload.get("schema_version") != 1
            or not isinstance(gate_anchor, Mapping)
            or gate_anchor.get("evaluation_spec_id")
            != normalized_anchor["evaluation_spec_id"]
            or gate_anchor.get("payload_hash") != specification_hash
        ):
            raise ValueError("active EvaluationSpec payload does not match approved gate")
    elif specification != gate_payload:
        raise ValueError("active EvaluationSpec payload does not match approved gate")
    artifact_spec_id = specification.get("evaluation_spec_id")
    if (
        not isinstance(artifact_spec_id, str)
        or not artifact_spec_id
        or artifact_spec_id != normalized_anchor["evaluation_spec_id"]
    ):
        raise ValueError("active EvaluationSpec ID mismatch")
    return normalized_anchor, specification


def _create_checkpoint_unlocked(
    layout: ProjectLayout,
    reason: str,
    state: Mapping[str, Any],
    state_hash: str,
    graph_hash: str,
) -> JsonObject:
    checkpoint = _build_checkpoint(
        layout, reason, state, state_hash, graph_hash
    )
    existing = _find_matching_control_checkpoint(layout, checkpoint)
    if existing is not None:
        return existing
    atomic_write_json(Path(checkpoint["checkpoint_path"]), checkpoint)
    return checkpoint


def _find_matching_control_checkpoint(
    layout: ProjectLayout, checkpoint: Mapping[str, Any]
) -> JsonObject | None:
    for path in sorted(layout.checkpoints.glob("*.json")):
        try:
            existing = read_json(path)
        except (OSError, ValueError):
            continue
        if not all(
            existing.get(field) == checkpoint[field]
            for field in CONTROL_SNAPSHOT_IDENTITY_FIELDS
        ):
            continue
        if existing.get("state") != checkpoint["state"]:
            continue
        if existing.get("checkpoint_path") != str(path.resolve()):
            continue
        if not isinstance(existing.get("checkpoint_id"), str):
            continue
        return existing
    return None


def _build_checkpoint(
    layout: ProjectLayout,
    reason: str,
    state: Mapping[str, Any],
    state_hash: str,
    graph_hash: str,
) -> JsonObject:
    revision = state.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("state revision must be a non-negative integer")
    checkpoint_id = f"checkpoint-{uuid4().hex}"
    checkpoint_path = (
        layout.checkpoints
        / f"{revision:06d}-{checkpoint_id}.json"
    )
    checkpoint: JsonObject = {
        "checkpoint_id": checkpoint_id,
        "created_at": _timestamp(),
        "revision": revision,
        "reason": reason,
        "state": dict(state),
        "state_hash": state_hash,
        "graph_hash": graph_hash,
        "budget_hash": sha256_file(layout.budget),
        "ledger_line_counts": {
            "gates": len(read_jsonl(layout.gates)),
            "journal": len(read_jsonl(layout.journal)),
            "model_calls": len(read_jsonl(layout.model_calls)),
            "artifact_index": len(read_jsonl(layout.artifact_index)),
            "budget_settlements": len(
                read_jsonl(layout.budget_settlements)
            ),
        },
        "checkpoint_path": str(checkpoint_path.resolve()),
    }
    return checkpoint
