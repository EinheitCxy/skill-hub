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

from .checkpoints import (
    _build_checkpoint,
    _validate_evaluation_spec_anchor,
)
from .recovery import (
    _atomic_json_hash,
    _atomic_write_research_state,
    _capture_graph,
    _committed_state_mutation_intent_for_receipt,
    _exclusive_lock,
    _json_copy,
    _reject_other_pending_intents,
    _required_string,
    _safe_id,
    _same_state_mutation_patch,
    _state_integrity_error,
    _validate_state_mutation_evidence,
    _workflow_lock_path,
    _workflow_transactions_root,
    state_mutation_transaction_id,
)

def initialize_baseline_state(
    layout: ProjectLayout,
    candidate_id: str,
    current_best: Mapping[str, Any],
    expected_revision: int,
    owner_transaction_id: str | None = None,
) -> JsonObject:
    """Commit the first baseline pointers through the shared state kernel."""
    candidate_id = _safe_id(candidate_id, "candidate_id")
    return _apply_state_mutation(
        layout,
        operation="initialize-baseline",
        expected_revision=expected_revision,
        allowed_transaction_id=owner_transaction_id,
        patch={
            "initial_baseline": candidate_id,
            "current_best": dict(current_best),
        },
    )


def validate_baseline_state_commit(
    layout: ProjectLayout,
    candidate_id: str,
    state_transaction_id: str,
    current_best: Mapping[str, Any],
) -> JsonObject:
    candidate_id = _safe_id(candidate_id, "candidate_id")
    state_transaction_id = _required_string(
        {"state_transaction_id": state_transaction_id},
        "state_transaction_id",
    )
    state = load_research_state(layout)
    if (
        state.get("initial_baseline") != candidate_id
        or state.get("current_best") != current_best
        or state.get("provisional_best") is not None
    ):
        raise ValueError("baseline state pointers are not committed")
    _, graph_hash = _capture_graph(layout)
    state_hash = sha256_file(layout.state)
    integrity_error = _state_integrity_error(
        layout, state, state_hash, graph_hash
    )
    if integrity_error is not None:
        raise ValueError(integrity_error)
    matching = [
        row
        for row in read_jsonl(layout.journal)
        if row.get("record_type") == "state-mutation"
        and row.get("receipt_id") == state.get("last_transition_id")
    ]
    if len(matching) != 1:
        raise ValueError("baseline state mutation receipt is missing")
    receipt = matching[0]
    if (
        receipt.get("transaction_id") != state_transaction_id
        or receipt.get("operation") != "initialize-baseline"
    ):
        raise ValueError("baseline state mutation receipt conflicts")
    intent = _committed_state_mutation_intent_for_receipt(layout, receipt)
    if (
        intent.get("patch")
        != {
            "initial_baseline": candidate_id,
            "current_best": dict(current_best),
        }
        or intent.get("next_state") != state
    ):
        raise ValueError("baseline state mutation intent conflicts")
    return dict(state)


def set_active_evaluation_spec(
    layout: ProjectLayout,
    anchor: Mapping[str, Any],
    expected_revision: int,
) -> JsonObject:
    normalized_anchor, _ = _validate_evaluation_spec_anchor(layout, anchor)
    return _apply_state_mutation(
        layout,
        operation="set-active-evaluation-spec",
        expected_revision=expected_revision,
        patch={"active_evaluation_spec": normalized_anchor},
    )


def validate_active_evaluation_spec(
    layout: ProjectLayout,
    evaluation_spec_id: str,
) -> JsonObject:
    evaluation_spec_id = _safe_id(
        evaluation_spec_id, "evaluation_spec_id"
    )
    state = load_research_state(layout)
    anchor = state.get("active_evaluation_spec")
    if not isinstance(anchor, Mapping):
        raise ValueError("active EvaluationSpec anchor is required")
    normalized_anchor, specification = _validate_evaluation_spec_anchor(
        layout, anchor
    )
    if normalized_anchor["evaluation_spec_id"] != evaluation_spec_id:
        raise ValueError(
            "active EvaluationSpec anchor does not match candidate"
        )
    return specification


def _apply_state_mutation(
    layout: ProjectLayout,
    *,
    operation: str,
    expected_revision: int,
    patch: Mapping[str, Any],
    allowed_transaction_id: str | None = None,
) -> JsonObject:
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError("expected_revision must be an integer")
    operation = _safe_id(operation, "state mutation operation")
    patch_copy = _json_copy(patch, "state mutation patch")
    transaction_id = state_mutation_transaction_id(
        operation, expected_revision, patch_copy
    )
    identity = transaction_id.removeprefix("state-mutation-")
    with _exclusive_lock(_workflow_lock_path(layout)):
        intent_path = _workflow_transactions_root(layout) / f"{transaction_id}.json"
        if intent_path.is_file():
            intent = read_json(intent_path)
            if (
                intent.get("kind") != "state-mutation"
                or intent.get("transaction_id") != transaction_id
                or intent.get("operation") != operation
                or not _same_state_mutation_patch(
                    operation, intent.get("patch"), patch_copy
                )
            ):
                raise ValueError("state mutation intent conflicts")
            return _resume_state_mutation(layout, intent_path, intent)
        _reject_other_pending_intents(layout, allowed_transaction_id)
        state = load_research_state(layout)
        if state.get("revision") != expected_revision:
            raise ValueError("state revision conflict")
        graph, graph_hash = _capture_graph(layout)
        source_hash = sha256_file(layout.state)
        integrity_error = _state_integrity_error(layout, state, source_hash, graph_hash)
        if integrity_error is not None:
            raise ValueError(integrity_error)
        if operation == "initialize-baseline" and (
            state.get("initial_baseline") is not None
            or state.get("provisional_best") is not None
            or state.get("current_best") is not None
        ):
            raise ValueError("baseline state pointers conflict")
        checkpoint = _build_checkpoint(
            layout, operation, state, source_hash, graph_hash
        )
        checkpoint_hash = _atomic_json_hash(checkpoint)
        receipt_id = f"state-receipt-{identity[:32]}"
        next_state = dict(state)
        next_state.update(patch_copy)
        next_state.update(
            {
                "revision": expected_revision + 1,
                "previous_state_hash": source_hash,
                "last_transition_id": receipt_id,
            }
        )
        if (
            state["initial_baseline"] is not None
            and next_state["initial_baseline"] != state["initial_baseline"]
        ):
            raise ValueError("initial_baseline is immutable once set")
        next_state = validate_research_state_document(next_state, graph)
        target_hash = _atomic_json_hash(next_state)
        receipt: JsonObject = {
            "record_type": "state-mutation",
            "transaction_id": transaction_id,
            "receipt_id": receipt_id,
            "operation": operation,
            "source_revision": expected_revision,
            "target_revision": expected_revision + 1,
            "source_state_hash": source_hash,
            "target_state_hash": target_hash,
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_path": checkpoint["checkpoint_path"],
            "checkpoint_hash": checkpoint_hash,
            "graph_hash": graph_hash,
            "budget_hash": checkpoint["budget_hash"],
        }
        intent: JsonObject = {
            "schema_version": 1,
            "kind": "state-mutation",
            "transaction_id": transaction_id,
            "phase": "prepared",
            "operation": operation,
            "patch": patch_copy,
            "source_revision": expected_revision,
            "target_revision": expected_revision + 1,
            "source_state_hash": source_hash,
            "target_state_hash": target_hash,
            "graph_hash": graph_hash,
            "budget_hash": checkpoint["budget_hash"],
            "checkpoint_hash": checkpoint_hash,
            "receipt_hash": _atomic_json_hash(receipt),
            "checkpoint": checkpoint,
            "receipt": receipt,
            "next_state": next_state,
        }
        atomic_write_json(intent_path, intent)
        return _resume_state_mutation(layout, intent_path, intent)


def _resume_state_mutation(
    layout: ProjectLayout, intent_path: Path, intent: Mapping[str, Any]
) -> JsonObject:
    _validate_state_mutation_evidence(layout, intent_path, intent)
    checkpoint = intent["checkpoint"]
    receipt = intent["receipt"]
    next_state = intent["next_state"]
    checkpoint_path = Path(checkpoint["checkpoint_path"])
    if checkpoint_path.is_file():
        if read_json(checkpoint_path) != checkpoint:
            raise ValueError("state mutation checkpoint conflicts")
    else:
        atomic_write_json(checkpoint_path, checkpoint)
    if intent["phase"] == "prepared":
        intent = dict(intent)
        intent["phase"] = "checkpoint_written"
        atomic_write_json(intent_path, intent)
    matching = [
        row for row in read_jsonl(layout.journal)
        if row.get("record_type") == "state-mutation"
        and row.get("transaction_id") == intent["transaction_id"]
    ]
    if not matching:
        append_jsonl_locked(layout.journal, receipt)
    elif len(matching) != 1 or matching[0] != receipt:
        raise ValueError("state mutation receipt conflicts")
    if intent["phase"] == "checkpoint_written":
        intent = dict(intent)
        intent["phase"] = "receipt_appended"
        atomic_write_json(intent_path, intent)
    current = load_research_state(layout)
    current_hash = sha256_file(layout.state)
    if current == next_state:
        if current_hash != intent["target_state_hash"]:
            raise ValueError("state mutation target state hash conflicts")
    elif (
        current.get("revision") == intent["source_revision"]
        and current_hash == intent["source_state_hash"]
    ):
        _atomic_write_research_state(layout, next_state)
        if sha256_file(layout.state) != intent["target_state_hash"]:
            raise OSError("atomic state hash does not match state mutation receipt")
    else:
        raise ValueError("state mutation state conflicts")
    if intent["phase"] == "receipt_appended":
        intent = dict(intent)
        intent["phase"] = "state_written"
        atomic_write_json(intent_path, intent)
    if intent["phase"] != "committed":
        intent = dict(intent)
        intent["phase"] = "committed"
        atomic_write_json(intent_path, intent)
    return dict(next_state)
