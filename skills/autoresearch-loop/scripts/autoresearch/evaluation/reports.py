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
from .spec import (
    _finite_number,
    _json_object,
    _normalize_evaluation_spec,
    _required_string,
)

def check_evaluation_report(
    layout: ProjectLayout, report: Mapping[str, Any]
) -> list[str]:
    normalized = _json_object(report, "evaluation report")
    evaluation_spec_id = _required_string(
        normalized, "evaluation_spec_id", "evaluation report"
    )
    spec = validate_active_evaluation_spec(layout, evaluation_spec_id)
    spec = _normalize_evaluation_spec(spec)
    gate_failure = _first_hard_gate_failure(report)
    if gate_failure is not None:
        return [gate_failure]
    state = load_research_state(layout)
    _validate_report_checkpoint(
        layout, spec, normalized, state, require_current=False
    )
    _validate_evaluation_evidence(spec, normalized)
    return []


def _validate_report_checkpoint(
    layout: ProjectLayout,
    spec: Mapping[str, Any],
    report: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    require_current: bool,
) -> JsonObject:
    checkpoint_id = _required_string(
        report,
        "evaluation_cycle_checkpoint_id",
        "evaluation report",
    )
    checkpoint = resolve_evaluation_cycle_checkpoint(layout, checkpoint_id)
    anchor = state.get("active_evaluation_spec")
    if not isinstance(anchor, Mapping):
        raise ValueError("active EvaluationSpec anchor is required")
    if (
        checkpoint["evaluation_spec_id"] != spec["evaluation_spec_id"]
        or checkpoint["evaluation_spec_artifact_id"]
        != anchor.get("artifact_id")
        or checkpoint["evaluation_spec_artifact_sha256"]
        != anchor.get("artifact_sha256")
    ):
        raise ValueError(
            "evaluation cycle checkpoint does not match active "
            "EvaluationSpec"
        )
    if not require_current:
        return checkpoint
    current_best = state.get("current_best")
    if not isinstance(current_best, Mapping):
        raise ValueError("Current Best is not initialized")
    if (
        current_best.get("candidate_id")
        != checkpoint["comparison_candidate_id"]
        or current_best.get("report_artifact_id")
        != checkpoint["comparison_report_artifact_id"]
    ):
        raise ValueError(
            "evaluation cycle checkpoint is stale for mutation-before "
            "Current Best"
        )
    return checkpoint


def _validate_evaluation_evidence(
    spec: Mapping[str, Any], report: Mapping[str, Any]
) -> None:
    metrics = report.get("metrics")
    primary_name = spec["primary_metric"]["name"]
    if not isinstance(metrics, Mapping) or primary_name not in metrics:
        raise ValueError(
            f"evaluation metrics must include primary metric {primary_name}"
        )
    _finite_number(
        metrics[primary_name], f"evaluation metrics.{primary_name}"
    )
    if not isinstance(report.get("uncertainty"), Mapping):
        raise ValueError("evaluation uncertainty evidence is required")
    for report_field, requirement_field in (
        ("slices", "slice_requirements"),
        ("qualitative", "qualitative_requirements"),
        ("resources", "resource_metrics"),
    ):
        evidence = report.get(report_field)
        if not isinstance(evidence, Mapping):
            raise ValueError(f"evaluation {report_field} evidence is required")
        missing = [
            name
            for name in spec[requirement_field]
            if name not in evidence
        ]
        if missing:
            raise ValueError(
                f"evaluation {report_field} evidence missing: {missing[0]}"
            )
    resources = report["resources"]
    for name in spec["resource_metrics"]:
        _finite_number(resources[name], f"evaluation resources.{name}")


def _verified_json_artifact(
    layout: ProjectLayout,
    artifact_id: str,
    *,
    expected_type: str | None,
    candidate_id: str,
    evaluation_spec_id: str,
    evaluation_cycle_checkpoint_id: str | None = None,
) -> tuple[JsonObject, JsonObject]:
    artifact, payload = _verified_json_payload(
        layout, artifact_id, expected_type=expected_type
    )
    if payload.get("candidate_id") != candidate_id:
        raise ValueError(
            f"artifact candidate_id does not match promotion candidate: "
            f"{artifact_id}"
        )
    if payload.get("evaluation_spec_id") != evaluation_spec_id:
        raise ValueError(
            f"artifact evaluation_spec_id does not match active "
            f"EvaluationSpec: {artifact_id}"
        )
    if (
        evaluation_cycle_checkpoint_id is not None
        and payload.get("evaluation_cycle_checkpoint_id")
        != evaluation_cycle_checkpoint_id
    ):
        raise ValueError(
            "artifact evaluation_cycle_checkpoint_id does not match "
            f"promotion checkpoint: {artifact_id}"
        )
    if artifact.get("candidate_id") not in {None, candidate_id}:
        raise ValueError(
            f"artifact metadata candidate_id conflicts: {artifact_id}"
        )
    return artifact, payload


def _verified_json_payload(
    layout: ProjectLayout,
    artifact_id: str,
    *,
    expected_type: str | None,
) -> tuple[JsonObject, JsonObject]:
    artifact = resolve_artifact(layout, artifact_id)
    verification = verify_artifact(layout, artifact_id)
    if (
        verification.get("available") is not True
        or verification.get("valid") is not True
    ):
        raise ValueError(f"artifact integrity verification failed: {artifact_id}")
    if expected_type is not None and artifact.get("type") != expected_type:
        raise ValueError(
            f"artifact must have type {expected_type}: {artifact_id}"
        )
    try:
        payload = read_json(Path(artifact["path"]))
    except (OSError, json.JSONDecodeError, TypeError) as error:
        raise ValueError(
            f"artifact JSON payload is invalid: {artifact_id}: {error}"
        ) from error
    payload = _json_object(payload, "artifact JSON payload")
    metadata_report_id = artifact.get("report_id")
    payload_report_id = payload.get("report_id")
    if (
        not isinstance(metadata_report_id, str)
        or not metadata_report_id
        or payload_report_id != metadata_report_id
    ):
        raise ValueError(
            f"artifact report_id does not match JSON payload: {artifact_id}"
        )
    return artifact, payload
