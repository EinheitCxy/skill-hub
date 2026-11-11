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

from .mutations import _apply_state_mutation
from .recovery import (
    _json_copy,
    _safe_id,
    _timestamp,
)

def promote_provisional_best(
    layout: ProjectLayout,
    candidate_id: str,
    report_artifact_id: str,
    evaluation_cycle_checkpoint_id: str,
    comparison_candidate_id: str,
    expected_revision: int,
) -> JsonObject:
    """Set Provisional Best without exposing a general state patch."""
    candidate_id = _safe_id(candidate_id, "candidate_id")
    report_artifact_id = _safe_id(
        report_artifact_id, "report_artifact_id"
    )
    evaluation_cycle_checkpoint_id = _safe_id(
        evaluation_cycle_checkpoint_id,
        "evaluation_cycle_checkpoint_id",
    )
    comparison_candidate_id = _safe_id(
        comparison_candidate_id, "comparison_candidate_id"
    )
    state = load_research_state(layout)
    if state.get("revision") != expected_revision:
        raise ValueError("state revision conflict")
    current_best = state["current_best"]
    if (
        current_best is None
        or current_best["candidate_id"] != comparison_candidate_id
    ):
        raise ValueError(
            "comparison checkpoint does not match mutation-before Current Best"
        )
    provisional_best = state["provisional_best"]
    pointer_identity = {
        "candidate_id": candidate_id,
        "report_artifact_id": report_artifact_id,
        "evaluation_cycle_checkpoint_id": (
            evaluation_cycle_checkpoint_id
        ),
    }
    if provisional_best is not None:
        if all(
            provisional_best.get(field) == value
            for field, value in pointer_identity.items()
        ):
            return state
        raise ValueError(
            "Provisional Best must be explicitly cleared or rejected "
            "before overwrite"
        )
    pointer = {
        **pointer_identity,
        "promoted_at": _timestamp(),
    }
    return _apply_state_mutation(
        layout,
        operation="promote-provisional-best",
        expected_revision=expected_revision,
        patch={"provisional_best": pointer},
    )


def promote_current_best(
    layout: ProjectLayout,
    candidate_id: str,
    provisional_pointer: Mapping[str, Any],
    expected_revision: int,
) -> JsonObject:
    """Promote only the current Provisional Best and clear its pointer."""
    candidate_id = _safe_id(candidate_id, "candidate_id")
    pointer = _json_copy(
        provisional_pointer, "Provisional Best pointer"
    )
    state = load_research_state(layout)
    if state.get("revision") != expected_revision:
        raise ValueError("state revision conflict")
    provisional_best = state["provisional_best"]
    if (
        provisional_best is None
        and state["current_best"] == pointer
        and pointer.get("candidate_id") == candidate_id
    ):
        return state
    if (
        provisional_best is None
        or provisional_best != pointer
        or pointer.get("candidate_id") != candidate_id
    ):
        raise ValueError(
            "candidate and pointer must equal the current Provisional Best"
        )
    return _apply_state_mutation(
        layout,
        operation="promote-current-best",
        expected_revision=expected_revision,
        patch={
            "current_best": pointer,
            "provisional_best": None,
        },
    )
