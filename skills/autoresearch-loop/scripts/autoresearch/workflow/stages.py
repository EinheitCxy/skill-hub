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
    record_coordinator_gate_receipt,
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

from .checkpoints import _build_checkpoint
from .recovery import (
    _atomic_json_hash,
    _atomic_write_research_state,
    _capture_graph,
    _exclusive_lock,
    _graph_stage,
    _hash_payload,
    _reject_other_pending_intents,
    _required_string,
    _safe_id,
    _source_revision_graph_anchor,
    _state_integrity_error,
    _timestamp,
    _transition_intent_path,
    _transition_transaction_id,
    _validate_transition_evidence,
    _validate_transition_intent_identity,
    _validate_transition_ledger_counts,
    _workflow_lock_path,
)
from .tasks import _task_records

def transition_stage(
    layout: ProjectLayout,
    source: str,
    target: str,
    expected_revision: int,
) -> TransitionResult:
    source = _safe_id(source, "source")
    target = _safe_id(target, "target")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise ValueError("expected_revision must be a non-negative integer")

    with _exclusive_lock(_workflow_lock_path(layout)):
        intent_path = _transition_intent_path(
            layout, source, target, expected_revision
        )
        if intent_path.is_file():
            try:
                intent = read_json(intent_path)
                _validate_transition_intent_identity(
                    intent, source, target, expected_revision
                )
                _reject_other_pending_intents(
                    layout, intent["transaction_id"]
                )
                return _resume_transition(layout, intent_path, intent)
            except (OSError, ValueError) as error:
                return TransitionResult(
                    "invalid",
                    _current_revision_or_invalid(layout),
                    None,
                    str(error),
                )
        try:
            _reject_other_pending_intents(layout, None)
        except (OSError, ValueError) as error:
            return TransitionResult(
                "invalid",
                _current_revision_or_invalid(layout),
                None,
                str(error),
            )
        try:
            state = load_research_state(layout)
        except (OSError, ValueError) as error:
            return TransitionResult("invalid", -1, None, str(error))
        revision = state.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            return TransitionResult(
                "invalid", -1, None, "state revision is invalid"
            )
        if revision != expected_revision:
            return TransitionResult(
                "invalid",
                revision,
                None,
                (
                    "state revision conflict: "
                    f"expected {expected_revision}, found {revision}"
                ),
            )
        if state.get("current_stage") != source:
            return TransitionResult(
                "invalid",
                revision,
                None,
                (
                    f"source {source} is not current stage "
                    f"{state.get('current_stage')}"
                ),
            )
        source_hash = sha256_file(layout.state)
        try:
            graph, graph_hash = _capture_graph(layout)
        except (OSError, ValueError) as error:
            return TransitionResult(
                "invalid", revision, None, f"invalid research graph: {error}"
            )
        integrity_error = _state_integrity_error(
            layout, state, source_hash, graph_hash
        )
        if integrity_error is not None:
            return TransitionResult(
                "invalid", revision, None, integrity_error
            )

        try:
            source_stage = _graph_stage(graph, source)
            _graph_stage(graph, target)
        except (OSError, ValueError) as error:
            return TransitionResult(
                "invalid", revision, None, f"invalid research graph: {error}"
            )
        next_stages = source_stage.get("next")
        if (
            not isinstance(next_stages, list)
            or target not in next_stages
        ):
            return TransitionResult(
                "invalid",
                revision,
                None,
                f"research graph edge does not exist: {source} -> {target}",
            )

        task_error = _required_task_artifact_error(
            layout, source_stage
        )
        if task_error is not None:
            return TransitionResult(
                "blocked", revision, None, task_error
            )
        coordinator_payload = None
        if source_stage.get("gate_authority") == "coordinator":
            coordinator_payload = _coordinator_gate_payload(
                layout,
                source_stage,
                revision,
                source_hash,
                graph_hash,
            )
        gate_error = _applicable_gate_error(
            layout, source_stage, coordinator_payload
        )
        if gate_error is not None and coordinator_payload is not None:
            conflict = _coordinator_gate_conflict_error(
                layout, source_stage, coordinator_payload
            )
            if conflict is None:
                record_coordinator_gate_receipt(
                    layout, source_stage["gate_type"], coordinator_payload
                )
                gate_error = _applicable_gate_error(
                    layout, source_stage, coordinator_payload
                )
            else:
                gate_error = conflict
        if gate_error is not None:
            return TransitionResult(
                "blocked", revision, None, gate_error
            )

        checkpoint = _build_checkpoint(
            layout,
            f"before transition {source} -> {target}",
            state,
            source_hash,
            graph_hash,
        )
        checkpoint_hash = _atomic_json_hash(checkpoint)
        receipt_id = f"transition-{uuid4().hex}"
        next_state = dict(state)
        next_state.update(
            {
                "revision": revision + 1,
                "current_stage": target,
                "previous_state_hash": source_hash,
                "last_transition_id": receipt_id,
                "pending_gate": None,
            }
        )
        next_state = validate_research_state_document(next_state, graph)
        target_hash = _atomic_json_hash(next_state)
        receipt: JsonObject = {
            "record_type": "stage-transition",
            "transaction_id": _transition_transaction_id(
                source, target, expected_revision
            ),
            "receipt_id": receipt_id,
            "transitioned_at": _timestamp(),
            "source": source,
            "target": target,
            "source_revision": revision,
            "target_revision": revision + 1,
            "source_state_hash": source_hash,
            "target_state_hash": target_hash,
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_path": checkpoint["checkpoint_path"],
            "checkpoint_hash": checkpoint_hash,
            "graph_hash": checkpoint["graph_hash"],
            "budget_hash": checkpoint["budget_hash"],
        }
        receipt_hash = _atomic_json_hash(receipt)
        intent: JsonObject = {
            "schema_version": 1,
            "kind": "stage-transition",
            "transaction_id": receipt["transaction_id"],
            "phase": "prepared",
            "source": source,
            "target": target,
            "source_revision": revision,
            "target_revision": revision + 1,
            "source_state_hash": source_hash,
            "target_state_hash": target_hash,
            "graph_hash": checkpoint["graph_hash"],
            "budget_hash": checkpoint["budget_hash"],
            "checkpoint_hash": checkpoint_hash,
            "receipt_hash": receipt_hash,
            "checkpoint": checkpoint,
            "receipt": receipt,
            "next_state": next_state,
        }
        atomic_write_json(intent_path, intent)
        return _resume_transition(
            layout,
            intent_path,
            intent,
            captured_graph_hash=graph_hash,
        )


def _resume_transition(
    layout: ProjectLayout,
    intent_path: Path,
    intent: JsonObject,
    *,
    captured_graph_hash: str | None = None,
) -> TransitionResult:
    _validate_transition_evidence(layout, intent)
    graph_anchor = _source_revision_graph_anchor(layout, intent)
    graph_hash = captured_graph_hash
    if graph_hash is None:
        _, graph_hash = _capture_graph(layout)
    checkpoint = intent["checkpoint"]
    receipt = intent["receipt"]
    if any(
        value != graph_anchor
        for value in (
            graph_hash,
            intent.get("graph_hash"),
            checkpoint.get("graph_hash"),
            receipt.get("graph_hash"),
        )
    ):
        raise ValueError(
            "pending transition graph anchor evidence conflicts"
        )
    if sha256_file(layout.budget) != intent.get("budget_hash"):
        raise ValueError(
            "pending transition budget hash evidence conflicts"
        )
    _validate_transition_ledger_counts(layout, intent)
    next_state = intent.get("next_state")
    if (
        not isinstance(checkpoint, Mapping)
        or not isinstance(receipt, Mapping)
        or not isinstance(next_state, Mapping)
    ):
        raise ValueError("pending transition evidence is invalid")
    checkpoint_path = Path(
        _required_string(checkpoint, "checkpoint_path")
    )
    ensure_within(layout.checkpoints, checkpoint_path)
    if checkpoint_path.is_file():
        if read_json(checkpoint_path) != checkpoint:
            raise ValueError(
                "pending transition checkpoint evidence conflicts"
            )
    else:
        atomic_write_json(checkpoint_path, checkpoint)
    if intent.get("phase") == "prepared":
        intent["phase"] = "checkpoint_written"
        atomic_write_json(intent_path, intent)

    matching = [
        row
        for row in read_jsonl(layout.journal)
        if row.get("record_type") in {"stage-transition", "state-mutation"}
        and row.get("transaction_id") == intent["transaction_id"]
    ]
    if not matching:
        append_jsonl_locked(layout.journal, receipt)
    elif len(matching) != 1 or matching[0] != receipt:
        raise ValueError(
            "pending transition receipt evidence conflicts"
        )
    if intent.get("phase") == "checkpoint_written":
        intent["phase"] = "receipt_appended"
        atomic_write_json(intent_path, intent)

    current = load_research_state(layout)
    current_hash = sha256_file(layout.state)
    if current == next_state and current_hash == intent["target_state_hash"]:
        pass
    elif (
        current.get("revision") == intent["source_revision"]
        and current.get("current_stage") == intent["source"]
        and current_hash == intent["source_state_hash"]
    ):
        _atomic_write_research_state(layout, next_state)
        if sha256_file(layout.state) != intent["target_state_hash"]:
            raise OSError(
                "atomic state hash does not match transition receipt"
            )
    else:
        raise ValueError(
            "pending transition state evidence conflicts"
        )
    if intent.get("phase") == "receipt_appended":
        intent["phase"] = "state_written"
        atomic_write_json(intent_path, intent)
    if intent.get("phase") != "committed":
        intent["phase"] = "committed"
        atomic_write_json(intent_path, intent)
    return TransitionResult(
        "applied",
        intent["target_revision"],
        layout.journal.resolve(),
        "transition applied",
    )


def _current_revision_or_invalid(layout: ProjectLayout) -> int:
    try:
        revision = load_research_state(layout).get("revision")
    except (OSError, ValueError):
        return -1
    if isinstance(revision, bool) or not isinstance(revision, int):
        return -1
    return revision


def _required_task_artifact_error(
    layout: ProjectLayout, source_stage: Mapping[str, Any]
) -> str | None:
    stage_id = source_stage["id"]
    tasks = [
        task
        for task in _task_records(layout)
        if task.get("stage_id") == stage_id
    ]
    if not tasks:
        return None
    for task in tasks:
        if task.get("status") != "completed":
            return (
                f"required task {task.get('task_id')} is "
                f"{task.get('status')}"
            )

    artifact_types: set[str] = set()
    for task in tasks:
        report_artifact_id = task.get("report_artifact_id")
        if not isinstance(report_artifact_id, str):
            return (
                f"required task {task.get('task_id')} has no "
                "task-report artifact"
            )
        try:
            report_verification = verify_artifact(
                layout, report_artifact_id
            )
        except ValueError:
            return (
                f"required task-report artifact is unknown: "
                f"{report_artifact_id}"
            )
        if (
            report_verification.get("available") is not True
            or report_verification.get("valid") is not True
        ):
            return (
                f"required task-report artifact is invalid: "
                f"{report_artifact_id}"
            )
        for artifact_id in task.get("artifact_ids", []):
            try:
                record = resolve_artifact(layout, artifact_id)
                verification = verify_artifact(layout, artifact_id)
            except ValueError:
                return f"required artifact is unknown: {artifact_id}"
            if (
                verification.get("available") is not True
                or verification.get("valid") is not True
            ):
                return f"required artifact is invalid: {artifact_id}"
            artifact_types.add(record["type"])

    outputs = source_stage.get("outputs")
    if not isinstance(outputs, list) or not all(
        isinstance(output, str) and output for output in outputs
    ):
        return f"stage {stage_id} outputs are invalid"
    missing = [output for output in outputs if output not in artifact_types]
    if missing:
        return f"required stage artifact is missing: {missing[0]}"
    return None


def _coordinator_gate_payload(
    layout: ProjectLayout,
    source_stage: Mapping[str, Any],
    revision: int,
    source_state_hash: str,
    graph_hash: str,
) -> JsonObject:
    """Bind a routine gate to the verified outputs of this exact stage."""
    output_types = set(source_stage["outputs"])
    artifacts: dict[str, JsonObject] = {}
    for task in _task_records(layout):
        if task.get("stage_id") != source_stage["id"]:
            continue
        for artifact_id in task.get("artifact_ids", []):
            record = resolve_artifact(layout, artifact_id)
            if record.get("type") in output_types:
                artifacts[artifact_id] = {
                    "artifact_id": artifact_id,
                    "sha256": record["sha256"],
                }
    return {
        "stage_id": source_stage["id"],
        "source_revision": revision,
        "source_state_hash": source_state_hash,
        "graph_hash": graph_hash,
        "output_artifacts": [artifacts[key] for key in sorted(artifacts)],
    }


def _applicable_gate_error(
    layout: ProjectLayout,
    source_stage: Mapping[str, Any],
    coordinator_payload: Mapping[str, Any] | None = None,
) -> str | None:
    gate_after = source_stage.get("gate_after")
    if gate_after is False:
        return None
    if gate_after is not True:
        return f"stage {source_stage.get('id')} gate_after is invalid"
    gate_type = source_stage.get("gate_type")
    if not isinstance(gate_type, str) or not gate_type:
        return f"stage {source_stage.get('id')} gate_type is invalid"
    gate_authority = source_stage.get("gate_authority")
    if gate_authority not in {"coordinator", "user"}:
        return f"stage {source_stage.get('id')} gate_authority is invalid"
    approved = [
        row
        for row in read_jsonl(layout.gates)
        if row.get("gate_type") == gate_type
        and row.get("status") == "approved"
    ]
    for receipt in reversed(approved):
        gate_id = receipt.get("gate_id")
        if not isinstance(gate_id, str):
            continue
        try:
            payload_record = read_json(
                layout.root
                / ".research"
                / "gate-payloads"
                / f"{gate_id}.json"
            )
        except OSError:
            continue
        payload = payload_record.get("payload")
        if not isinstance(payload, Mapping):
            continue
        payload_hash = _hash_payload(payload)
        if (
            payload_record.get("gate_type") != gate_type
            or payload_record.get("payload_hash") != payload_hash
            or receipt.get("payload_hash") != payload_hash
        ):
            continue
        if payload.get("stage_id") != source_stage.get("id"):
            continue
        if gate_authority == "coordinator":
            if (
                receipt.get("approval_source") == "Coordinator"
                and coordinator_payload is not None
                and payload == coordinator_payload
            ):
                return None
            continue
        if receipt.get("approval_source") != "Coordinator":
            return None
    return (
        f"{gate_type} gate approval required after "
        f"{source_stage.get('id')}"
    )


def _coordinator_gate_conflict_error(
    layout: ProjectLayout,
    source_stage: Mapping[str, Any],
    expected_payload: Mapping[str, Any],
) -> str | None:
    """Reject a tampered Coordinator receipt instead of silently replacing it."""
    for receipt in read_jsonl(layout.gates):
        if (
            receipt.get("gate_type") != source_stage.get("gate_type")
            or receipt.get("status") != "approved"
            or receipt.get("approval_source") != "Coordinator"
        ):
            continue
        gate_id = receipt.get("gate_id")
        if not isinstance(gate_id, str):
            continue
        try:
            payload_record = read_json(
                layout.root
                / ".research"
                / "gate-payloads"
                / f"{gate_id}.json"
            )
        except OSError:
            continue
        payload = payload_record.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if (
            payload.get("stage_id") == expected_payload["stage_id"]
            and payload.get("source_revision")
            == expected_payload["source_revision"]
            and payload != expected_payload
        ):
            return (
                "Coordinator gate evidence conflicts for "
                f"{source_stage.get('id')}"
            )
    return None
