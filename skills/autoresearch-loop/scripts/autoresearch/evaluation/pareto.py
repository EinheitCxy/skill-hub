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

from .spec import (
    _finite_number,
    _json_object,
    _required_string,
)

PARETO_HIGHER_IS_BETTER = (
    "objective",
    "robustness",
    "evidence_strength",
    "information_gain",
)


PARETO_LOWER_IS_BETTER = ("cost", "complexity", "confound_penalty")


def pareto_frontier(
    reports: Sequence[Mapping[str, Any]],
) -> list[JsonObject]:
    if isinstance(reports, (str, bytes)) or not isinstance(reports, Sequence):
        raise ValueError("Pareto reports must be a list")
    normalized = [_normalize_pareto_report(report) for report in reports]
    return [
        report
        for index, report in enumerate(normalized)
        if not any(
            other_index != index and _dominates(other, report)
            for other_index, other in enumerate(normalized)
        )
    ]


def _normalize_pareto_report(report: Mapping[str, Any]) -> JsonObject:
    normalized = _json_object(report, "Pareto report")
    normalized["candidate_id"] = _required_string(
        normalized, "candidate_id", "Pareto report"
    )
    for field in (*PARETO_HIGHER_IS_BETTER, *PARETO_LOWER_IS_BETTER):
        if field not in normalized:
            raise ValueError(f"Pareto report missing required field: {field}")
        normalized[field] = _finite_number(
            normalized[field], f"Pareto report.{field}"
        )
    return normalized


def _dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    no_worse = all(
        left[field] >= right[field] for field in PARETO_HIGHER_IS_BETTER
    ) and all(
        left[field] <= right[field] for field in PARETO_LOWER_IS_BETTER
    )
    strictly_better = any(
        left[field] > right[field] for field in PARETO_HIGHER_IS_BETTER
    ) or any(
        left[field] < right[field] for field in PARETO_LOWER_IS_BETTER
    )
    return no_worse and strictly_better
