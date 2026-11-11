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
    HARD_GATES,
    _normalize_evaluation_spec,
)

def evaluate_hard_gates(
    spec: Mapping[str, Any], report: Mapping[str, Any]
) -> list[str]:
    normalized = _normalize_evaluation_spec(spec)
    if normalized["hard_gates"] != [name for name, _ in HARD_GATES]:
        raise ValueError("EvaluationSpec hard gates must use the required order")
    if not isinstance(report, Mapping):
        raise ValueError("evaluation report must be an object")
    return [
        f"{name} failed"
        for name, field in HARD_GATES
        if report.get(field) is not True
    ]


def _first_hard_gate_failure(
    report: Mapping[str, Any],
) -> str | None:
    for name, field in HARD_GATES:
        if report.get(field) is not True:
            return f"{name} failed"
    return None
