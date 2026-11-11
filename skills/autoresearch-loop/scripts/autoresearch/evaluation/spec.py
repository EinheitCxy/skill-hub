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
from autoresearch.governance import (
    EVALUATION_GATE_ENVELOPE_FIELDS,
    _normalize_evaluation_gate_payload,
    read_approved_gate,
)
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

EVALUATION_SPEC_FIELDS = frozenset(
    {
        "evaluation_spec_id",
        "dataset_id",
        "split_id",
        "baseline_candidate_id",
        "primary_metric",
        "secondary_metrics",
        "unacceptable_regressions",
        "slice_requirements",
        "qualitative_requirements",
        "resource_metrics",
        "promotion_rule",
        "seed_policy",
        "sample_count_policy",
        "hard_gates",
        "robustness_requirements",
    }
)
EVALUATION_SPEC_OPTIONAL_FIELDS = frozenset(
    {
        "exact_sample_count",
        "manifest_artifact_id",
        "uncertainty_method",
        "evaluation_command",
        "host_requirements",
    }
)


HARD_GATES = (
    ("execution validity", "execution_valid"),
    ("contract comparability", "comparable"),
    ("evidence completeness", "evidence_complete"),
)


METRIC_FIELDS = frozenset({"name", "direction", "noise"})


PROMOTION_RULE_FIELDS = frozenset(
    {"minimum_primary_gain", "allow_unacceptable_regression"}
)


UNACCEPTABLE_REGRESSION_RULE_FIELDS = frozenset(
    {"source", "direction", "threshold"}
)


def lock_evaluation_spec(
    layout: ProjectLayout, spec: Mapping[str, Any]
) -> JsonObject:
    normalized = _normalize_evaluation_spec(spec)
    approved = _latest_approved_evaluation(layout)
    supplied_payload_hash = _canonical_payload_hash(normalized)
    approved_spec_hash = _approved_spec_hash(approved["payload"])
    if approved_spec_hash != supplied_payload_hash:
        raise ValueError(
            "latest approved evaluation gate payload hash does not match "
            "supplied EvaluationSpec"
        )

    staging_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="evaluation-spec-",
            dir=layout.artifact_index.parent,
            delete=False,
        ) as staging:
            json.dump(
                normalized,
                staging,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            staging.write("\n")
            staging_path = Path(staging.name)
        artifact = register_artifact(
            layout,
            staging_path,
            {
                "type": "evaluation-spec",
                "producer": "research-coordinator",
                "storage": "snapshot",
                "provenance_tags": ["approved-evaluation-contract"],
                "comparability_tags": [
                    normalized["dataset_id"],
                    normalized["split_id"],
                    normalized["evaluation_spec_id"],
                ],
            },
        )
    finally:
        if staging_path is not None:
            staging_path.unlink(missing_ok=True)

    artifact_payload = _json_object(
        read_json(Path(artifact["path"])),
        "registered EvaluationSpec artifact payload",
    )
    if _canonical_payload_hash(artifact_payload) != supplied_payload_hash:
        raise ValueError(
            "registered EvaluationSpec artifact payload hash mismatch"
        )
    anchor = {
        "evaluation_spec_id": normalized["evaluation_spec_id"],
        "artifact_id": artifact["artifact_id"],
        "artifact_sha256": artifact["sha256"],
        "gate_id": approved["gate_id"],
        "gate_payload_hash": approved["payload_hash"],
    }
    state = load_research_state(layout)
    return set_active_evaluation_spec(
        layout, anchor, _revision(state)
    )


def _latest_approved_evaluation(layout: ProjectLayout) -> JsonObject:
    approved = [
        row
        for row in read_jsonl(layout.gates)
        if row.get("gate_type") == "evaluation"
        and row.get("status") == "approved"
    ]
    if not approved:
        raise PermissionError(
            "latest approved evaluation gate is required"
        )
    gate_id = approved[-1].get("gate_id")
    if not isinstance(gate_id, str) or not gate_id:
        raise ValueError("latest approved evaluation gate is invalid")
    return read_approved_gate(layout, gate_id, "evaluation")


def _approved_spec_hash(payload: Mapping[str, Any]) -> str:
    """Read either the split v1 gate envelope or the legacy direct gate.

    The envelope branch delegates to governance's
    ``_normalize_evaluation_gate_payload`` so stage evidence and anchor
    validation are shared with the write side.  Legacy direct gates remain
    readable for recovery, but new callers should use the wrapper so stage
    evidence and static contract hash cannot drift.
    """
    if set(payload) == EVALUATION_GATE_ENVELOPE_FIELDS:
        normalized = _normalize_evaluation_gate_payload(payload)
        return normalized["evaluation_spec"]["payload_hash"]
    return _canonical_payload_hash(_normalize_evaluation_spec(payload))


def _normalize_evaluation_spec(spec: Mapping[str, Any]) -> JsonObject:
    normalized = _json_object(spec, "EvaluationSpec")
    missing = EVALUATION_SPEC_FIELDS - normalized.keys()
    unknown = normalized.keys() - EVALUATION_SPEC_FIELDS - EVALUATION_SPEC_OPTIONAL_FIELDS
    if missing:
        raise ValueError(
            "EvaluationSpec missing required field: "
            f"{sorted(missing)[0]}"
        )
    if unknown:
        raise ValueError(
            f"EvaluationSpec has unknown field: {sorted(unknown)[0]}"
        )
    for field in (
        "evaluation_spec_id",
        "dataset_id",
        "split_id",
        "baseline_candidate_id",
    ):
        normalized[field] = _required_string(
            normalized, field, "EvaluationSpec"
        )
    normalized["primary_metric"] = _normalize_metric(
        normalized["primary_metric"], "primary_metric"
    )
    secondary = normalized["secondary_metrics"]
    if not isinstance(secondary, list):
        raise ValueError("secondary_metrics must be a list")
    normalized["secondary_metrics"] = [
        _normalize_metric(metric, f"secondary_metrics[{index}]")
        for index, metric in enumerate(secondary)
    ]
    normalized["unacceptable_regressions"] = (
        _normalize_unacceptable_regressions(
            normalized["unacceptable_regressions"]
        )
    )
    for field in (
        "slice_requirements",
        "qualitative_requirements",
        "resource_metrics",
        "robustness_requirements",
    ):
        normalized[field] = _string_list(normalized[field], field)

    promotion_rule = _json_object(
        normalized["promotion_rule"], "promotion_rule"
    )
    if set(promotion_rule) != PROMOTION_RULE_FIELDS:
        raise ValueError(
            "promotion_rule must contain minimum_primary_gain and "
            "allow_unacceptable_regression"
        )
    promotion_rule["minimum_primary_gain"] = _finite_number(
        promotion_rule["minimum_primary_gain"],
        "promotion_rule.minimum_primary_gain",
    )
    if promotion_rule["minimum_primary_gain"] < 0:
        raise ValueError(
            "promotion_rule.minimum_primary_gain must be non-negative"
        )
    if not isinstance(
        promotion_rule["allow_unacceptable_regression"], bool
    ):
        raise ValueError(
            "promotion_rule.allow_unacceptable_regression must be a boolean"
        )
    normalized["promotion_rule"] = promotion_rule

    seed_policy = _json_object(normalized["seed_policy"], "seed_policy")
    if set(seed_policy) != {"seeds"}:
        raise ValueError("seed_policy must contain only seeds")
    seeds = seed_policy["seeds"]
    if (
        not isinstance(seeds, list)
        or not seeds
        or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds)
        or len(seeds) != len(set(seeds))
    ):
        raise ValueError("seed_policy.seeds must be unique integers")
    normalized["seed_policy"] = {"seeds": list(seeds)}

    sample_policy = _json_object(
        normalized["sample_count_policy"], "sample_count_policy"
    )
    minimum = sample_policy.get("minimum")
    if (
        set(sample_policy) != {"minimum"}
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or minimum <= 0
    ):
        raise ValueError(
            "sample_count_policy.minimum must be a positive integer"
        )
    normalized["sample_count_policy"] = {"minimum": minimum}
    normalized["hard_gates"] = _string_list(
        normalized["hard_gates"], "hard_gates"
    )
    if normalized["hard_gates"] != [name for name, _ in HARD_GATES]:
        raise ValueError(
            "hard_gates must be execution validity, contract comparability, "
            "and evidence completeness in that order"
        )
    if "exact_sample_count" in normalized:
        count = normalized["exact_sample_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("exact_sample_count must be a positive integer")
    if "manifest_artifact_id" in normalized:
        normalized["manifest_artifact_id"] = _required_string(normalized, "manifest_artifact_id", "EvaluationSpec")
    if "uncertainty_method" in normalized:
        normalized["uncertainty_method"] = _required_string(normalized, "uncertainty_method", "EvaluationSpec")
    if "evaluation_command" in normalized:
        normalized["evaluation_command"] = _string_list(normalized["evaluation_command"], "evaluation_command")
    if "host_requirements" in normalized:
        normalized["host_requirements"] = _string_list(normalized["host_requirements"], "host_requirements")
    return normalized


def _normalize_metric(value: Any, field: str) -> JsonObject:
    metric = _json_object(value, field)
    if set(metric) != METRIC_FIELDS:
        raise ValueError(f"{field} must contain name, direction, and noise")
    metric["name"] = _required_string(metric, "name", field)
    if metric["direction"] not in {"maximize", "minimize"}:
        raise ValueError(f"{field}.direction must be maximize or minimize")
    metric["noise"] = _finite_number(metric["noise"], f"{field}.noise")
    if metric["noise"] < 0:
        raise ValueError(f"{field}.noise must be non-negative")
    return metric


def _normalize_unacceptable_regressions(value: Any) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError("unacceptable_regressions must be an object")
    normalized: JsonObject = {}
    for name, raw_rule in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(
                "unacceptable_regressions keys must be non-empty strings"
            )
        if not isinstance(raw_rule, Mapping):
            raise ValueError(
                f"unacceptable_regressions.{name} must be an object"
            )
        rule = dict(raw_rule)
        if set(rule) != UNACCEPTABLE_REGRESSION_RULE_FIELDS:
            raise ValueError(
                f"unacceptable_regressions.{name} must contain exactly "
                "source, direction, and threshold"
            )
        if rule["source"] not in {"metrics", "resources"}:
            raise ValueError(
                f"unacceptable_regressions.{name}.source must be "
                "metrics or resources"
            )
        if rule["direction"] not in {"maximize", "minimize"}:
            raise ValueError(
                f"unacceptable_regressions.{name}.direction must be "
                "maximize or minimize"
            )
        rule["threshold"] = _finite_number(
            rule["threshold"],
            f"unacceptable_regressions.{name}.threshold",
        )
        normalized[name] = rule
    return normalized


def _revision(state: Mapping[str, Any]) -> int:
    revision = state.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError("state revision is invalid")
    return revision


def _json_object(value: Any, field: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    try:
        copied = deepcopy(json.loads(json.dumps(value, allow_nan=False)))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must contain JSON values") from error
    return dict(copied)


def _required_string(
    value: Mapping[str, Any], field: str, context: str
) -> str:
    if field not in value:
        raise ValueError(f"{context} missing required field: {field}")
    return _nonempty_string(value[field], f"{context}.{field}")


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    return list(value)


def _finite_number(value: Any, field: str) -> float | int:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field} must be a finite number")
    return value


def _canonical_payload_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
