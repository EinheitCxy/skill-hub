from __future__ import annotations
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from autoresearch.artifacts import (
    list_artifacts,
    register_artifact,
    resolve_artifact,
    verify_artifact,
)
from autoresearch.candidates import load_candidates, update_candidate
from autoresearch.contracts import JsonObject
from autoresearch.governance import read_approved_gate
from autoresearch.io import read_json, read_jsonl
from autoresearch.project import ProjectLayout, load_research_state
from autoresearch.workflow import (
    bind_evaluation_cycle_checkpoint,
    promote_current_best,
    promote_provisional_best,
    resolve_evaluation_cycle_checkpoint,
    set_active_evaluation_spec,
    validate_evaluation_cycle_checkpoint,
    validate_active_evaluation_spec,
)

from .hard_gates import _first_hard_gate_failure
from .pareto import (
    _normalize_pareto_report,
    pareto_frontier,
)
from .reports import (
    _validate_evaluation_evidence,
    _validate_report_checkpoint,
    _verified_json_artifact,
    _verified_json_payload,
)
from .spec import (
    _finite_number,
    _nonempty_string,
    _normalize_evaluation_spec,
    _revision,
)

def promote_provisional(
    layout: ProjectLayout, candidate_id: str, report_id: str
) -> JsonObject:
    candidate_id = _nonempty_string(candidate_id, "candidate_id")
    report_id = _nonempty_string(report_id, "report_id")
    candidate = _official_candidate(layout, candidate_id)
    spec = _normalize_evaluation_spec(
        validate_active_evaluation_spec(
            layout, candidate["evaluation_spec_id"]
        )
    )
    artifact, report = _verified_json_artifact(
        layout,
        report_id,
        expected_type="evaluation-report",
        candidate_id=candidate_id,
        evaluation_spec_id=spec["evaluation_spec_id"],
    )
    state = load_research_state(layout)
    checkpoint = _validate_official_evaluation(
        layout, spec, report, state
    )
    _require_pareto_membership(
        layout,
        report_id,
        spec,
        checkpoint["checkpoint_id"],
    )

    evidence = list(candidate["evaluation_report_ids"])
    provisional_best = state["provisional_best"]
    if (
        provisional_best is not None
        and provisional_best["candidate_id"] == candidate_id
        and provisional_best["report_artifact_id"] == report_id
        and provisional_best["evaluation_cycle_checkpoint_id"]
        == checkpoint["checkpoint_id"]
        and report_id in evidence
    ):
        return state
    if provisional_best is not None:
        raise ValueError(
            "Provisional Best must be explicitly cleared or rejected "
            "before overwrite"
        )
    if report_id not in evidence:
        evidence.append(report_id)
        update_candidate(
            layout,
            candidate_id,
            {"evaluation_report_ids": evidence},
        )
    return promote_provisional_best(
        layout,
        candidate_id,
        report_id,
        checkpoint["checkpoint_id"],
        checkpoint["comparison_candidate_id"],
        _revision(state),
    )


def promote_current(
    layout: ProjectLayout,
    candidate_id: str,
    robustness_report_ids: Sequence[str],
) -> JsonObject:
    candidate_id = _nonempty_string(candidate_id, "candidate_id")
    normalized_ids = _artifact_ids(
        robustness_report_ids, "robustness_report_ids"
    )
    candidate = _official_candidate(layout, candidate_id)
    spec = _normalize_evaluation_spec(
        validate_active_evaluation_spec(
            layout, candidate["evaluation_spec_id"]
        )
    )
    state = load_research_state(layout)
    evidence = list(candidate["evaluation_report_ids"])
    current_best = state["current_best"]
    provisional_best = state["provisional_best"]
    if provisional_best is None:
        if (
            current_best is None
            or current_best["candidate_id"] != candidate_id
        ):
            raise ValueError(
                "candidate must equal the current Provisional Best"
            )
        promotion_pointer = dict(current_best)
    else:
        if provisional_best["candidate_id"] != candidate_id:
            raise ValueError(
                "candidate must equal the current Provisional Best"
            )
        promotion_pointer = dict(provisional_best)
    checkpoint_id = _nonempty_string(
        promotion_pointer["evaluation_cycle_checkpoint_id"],
        "BestPointer.evaluation_cycle_checkpoint_id",
    )
    robustness: list[tuple[JsonObject, JsonObject]] = [
        _verified_json_artifact(
            layout,
            artifact_id,
            expected_type=None,
            candidate_id=candidate_id,
            evaluation_spec_id=spec["evaluation_spec_id"],
            evaluation_cycle_checkpoint_id=checkpoint_id,
        )
        for artifact_id in normalized_ids
    ]
    actual_types = sorted(artifact["type"] for artifact, _ in robustness)
    required_types = sorted(spec["robustness_requirements"])
    if actual_types != required_types:
        raise ValueError(
            "robustness contract requires artifact types "
            f"{required_types}, received {actual_types}"
        )

    if (
        current_best is not None
        and current_best == promotion_pointer
        and provisional_best is None
        and all(artifact_id in evidence for artifact_id in normalized_ids)
    ):
        return state
    official_artifact_id = _nonempty_string(
        promotion_pointer["report_artifact_id"],
        "BestPointer.report_artifact_id",
    )
    _, official_report = _verified_json_artifact(
        layout,
        official_artifact_id,
        expected_type="evaluation-report",
        candidate_id=candidate_id,
        evaluation_spec_id=spec["evaluation_spec_id"],
        evaluation_cycle_checkpoint_id=checkpoint_id,
    )
    checkpoint = _validate_official_evaluation(
        layout, spec, official_report, state
    )
    _require_pareto_membership(
        layout,
        official_artifact_id,
        spec,
        checkpoint["checkpoint_id"],
    )
    changed = False
    for artifact_id in normalized_ids:
        if artifact_id not in evidence:
            evidence.append(artifact_id)
            changed = True
    if changed:
        update_candidate(
            layout,
            candidate_id,
            {"evaluation_report_ids": evidence},
        )
    return promote_current_best(
        layout, candidate_id, promotion_pointer, _revision(state)
    )


def _validate_official_evaluation(
    layout: ProjectLayout,
    spec: Mapping[str, Any],
    report: Mapping[str, Any],
    state: Mapping[str, Any],
) -> JsonObject:
    gate_failure = _first_hard_gate_failure(report)
    if gate_failure is not None:
        raise ValueError(
            f"evaluation hard gates failed: {gate_failure}"
        )
    if report.get("official") is not True:
        raise ValueError("official evaluation report is required")
    checkpoint = _validate_report_checkpoint(
        layout, spec, report, state, require_current=True
    )
    _validate_evaluation_evidence(spec, report)
    primary_gain = _finite_number(
        report.get("primary_gain"), "evaluation report.primary_gain"
    )
    minimum_gain = spec["promotion_rule"]["minimum_primary_gain"]
    if primary_gain < minimum_gain:
        raise ValueError(
            "evaluation does not meet minimum primary gain"
        )
    regressions = _unacceptable_regression_violations(spec, report)
    if (
        regressions
        and not spec["promotion_rule"]["allow_unacceptable_regression"]
    ):
        raise ValueError("evaluation has an unacceptable regression")
    return checkpoint


def _unacceptable_regression_violations(
    spec: Mapping[str, Any], report: Mapping[str, Any]
) -> list[str]:
    violations: list[str] = []
    for name, rule in spec["unacceptable_regressions"].items():
        source = rule["source"]
        section = report.get(source)
        if not isinstance(section, Mapping) or name not in section:
            raise ValueError(
                f"locked unacceptable regression value is missing: "
                f"{source}.{name}"
            )
        value = _finite_number(
            section[name],
            f"locked unacceptable regression value {source}.{name}",
        )
        threshold = rule["threshold"]
        if (
            rule["direction"] == "maximize"
            and value < threshold
        ) or (
            rule["direction"] == "minimize"
            and value > threshold
        ):
            violations.append(name)
    return violations


def _require_pareto_membership(
    layout: ProjectLayout,
    target_artifact_id: str,
    spec: Mapping[str, Any],
    evaluation_cycle_checkpoint_id: str,
) -> None:
    pool: list[JsonObject] = []
    for artifact in list_artifacts(
        layout, {"type": "evaluation-report"}
    ):
        artifact_id = artifact["artifact_id"]
        try:
            registered, payload = _verified_json_payload(
                layout,
                artifact_id,
                expected_type="evaluation-report",
            )
            payload_candidate_id = payload.get("candidate_id")
            if (
                payload.get("official") is not True
                or payload.get("evaluation_spec_id")
                != spec["evaluation_spec_id"]
                or payload.get("evaluation_cycle_checkpoint_id")
                != evaluation_cycle_checkpoint_id
                or not isinstance(payload_candidate_id, str)
                or not payload_candidate_id
                or registered.get("candidate_id")
                not in {None, payload_candidate_id}
                or _first_hard_gate_failure(payload) is not None
            ):
                continue
            pareto_report = _normalize_pareto_report(payload)
            pareto_report["_artifact_id"] = artifact_id
        except (OSError, ValueError):
            continue
        pool.append(pareto_report)

    frontier_artifact_ids = {
        row["_artifact_id"] for row in pareto_frontier(pool)
    }
    if target_artifact_id not in frontier_artifact_ids:
        raise ValueError("evaluation is not a Pareto member")


def _official_candidate(
    layout: ProjectLayout, candidate_id: str
) -> JsonObject:
    candidate = load_candidates(layout).get(candidate_id)
    if candidate is None:
        raise ValueError(f"unknown candidate: {candidate_id}")
    if candidate["status"] != "official":
        raise ValueError("promotion requires an official candidate")
    if not isinstance(candidate.get("evaluation_spec_id"), str):
        raise ValueError("promotion candidate requires an EvaluationSpec")
    return candidate


def _artifact_ids(value: Sequence[str], field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a list of artifact ids")
    normalized = [
        _nonempty_string(artifact_id, field) for artifact_id in value
    ]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized
