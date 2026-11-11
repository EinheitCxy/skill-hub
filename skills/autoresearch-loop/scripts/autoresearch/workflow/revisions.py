from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autoresearch.contracts import JsonObject
from autoresearch.governance import read_approved_gate, request_gate
from autoresearch.io import (
    append_jsonl_locked,
    atomic_write_json,
    read_json,
    read_jsonl,
    sha256_file,
)
from autoresearch.project import ProjectLayout, validate_research_state_document
from autoresearch.skills import preflight_graph_skills

from .checkpoints import _build_checkpoint
from .recovery import (
    _atomic_json_hash,
    _atomic_write_research_state,
    _atomic_write_text,
    _capture_graph,
    _exclusive_lock,
    _graph_revision_transaction_identity,
    _read_graph_revision_text,
    _reject_other_pending_intents,
    _safe_id,
    _state_integrity_error,
    _validate_graph_revision_evidence,
    _workflow_lock_path,
    _workflow_transactions_root,
)
def request_graph_revision(
    layout: ProjectLayout, graph_path: Path, reason: str
) -> JsonObject:
    target_graph, _, target_hash = _read_graph_document(graph_path)
    preflight_graph_skills(target_graph)
    if not isinstance(reason, str) or not reason:
        raise ValueError("graph revision reason must be a non-empty string")
    with _exclusive_lock(_workflow_lock_path(layout)):
        _reject_other_pending_intents(layout, None)
        state = read_json(layout.state)
        source_graph, source_hash = _capture_graph(layout)
        state = validate_research_state_document(state, source_graph)
        integrity_error = _state_integrity_error(
            layout, state, sha256_file(layout.state), source_hash
        )
        if integrity_error is not None:
            raise ValueError(integrity_error)
        _reject_open_tasks(layout)
        validate_research_state_document(state, target_graph)
        payload: JsonObject = {
            "schema_version": 1,
            "base_graph_hash": source_hash,
            "target_graph_hash": target_hash,
            "state_revision": state["revision"],
            "reason": reason,
        }
    return request_gate(layout, "graph", payload)


def apply_graph_revision(
    layout: ProjectLayout, graph_path: Path | None, gate_id: str
) -> JsonObject:
    gate_id = _safe_id(gate_id, "graph revision gate_id")
    with _exclusive_lock(_workflow_lock_path(layout)):
        source_graph, _ = _capture_graph(layout)
        preflight_graph_skills(source_graph)
        intent_path, intent = _existing_revision_intent(layout, gate_id)
        if intent is not None:
            _validate_graph_revision_intent(layout, intent)
            if graph_path is not None:
                _, target_text, target_hash = _read_graph_document(graph_path)
                if (
                    intent["target_graph_hash"] != target_hash
                    or intent["target_graph_text"] != target_text
                ):
                    raise ValueError(
                        "graph revision target conflicts with durable intent"
                    )
            return _resume_graph_revision(layout, intent_path, intent)

        if graph_path is None:
            raise ValueError("graph path is required for a new graph revision")
        target_graph, target_text, target_hash = _read_graph_document(graph_path)
        _reject_other_pending_intents(layout, None)
        gate = read_approved_gate(layout, gate_id, "graph")
        payload = gate["payload"]
        source_graph, source_hash = _capture_graph(layout)
        source_state = validate_research_state_document(
            read_json(layout.state), source_graph
        )
        source_state_hash = sha256_file(layout.state)
        integrity_error = _state_integrity_error(
            layout, source_state, source_state_hash, source_hash
        )
        if integrity_error is not None:
            raise ValueError(integrity_error)
        if (
            payload["base_graph_hash"] != source_hash
            or payload["target_graph_hash"] != target_hash
            or payload["state_revision"] != source_state["revision"]
        ):
            raise ValueError("approved graph revision no longer matches project state")
        _reject_open_tasks(layout)
        validate_research_state_document(source_state, target_graph)

        transaction_id = _graph_revision_transaction_id(
            gate_id,
            gate["payload_hash"],
            source_hash,
            target_hash,
            source_state_hash,
        )
        receipt_id = (
            f"graph-revision-receipt-"
            f"{transaction_id.removeprefix('graph-revision-')[:32]}"
        )
        checkpoint = _build_checkpoint(
            layout,
            f"graph revision: {payload['reason']}",
            source_state,
            source_state_hash,
            source_hash,
        )
        checkpoint_hash = _atomic_json_hash(checkpoint)
        target_state = dict(source_state)
        target_state.update(
            {
                "revision": source_state["revision"] + 1,
                "previous_state_hash": source_state_hash,
                "last_transition_id": receipt_id,
            }
        )
        target_state = validate_research_state_document(target_state, target_graph)
        target_state_hash = _atomic_json_hash(target_state)
        receipt: JsonObject = {
            "record_type": "graph-revision",
            "transaction_id": transaction_id,
            "receipt_id": receipt_id,
            "gate_id": gate_id,
            "reason": payload["reason"],
            "source_revision": source_state["revision"],
            "target_revision": target_state["revision"],
            "source_state_hash": source_state_hash,
            "target_state_hash": target_state_hash,
            "source_graph_hash": source_hash,
            "target_graph_hash": target_hash,
            "graph_hash": target_hash,
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_path": checkpoint["checkpoint_path"],
            "checkpoint_hash": checkpoint_hash,
            "budget_hash": checkpoint["budget_hash"],
        }
        intent = {
            "schema_version": 1,
            "kind": "graph-revision",
            "transaction_id": transaction_id,
            "phase": "prepared",
            "gate_id": gate_id,
            "gate_payload_hash": gate["payload_hash"],
            "source_graph_text": layout.graph.read_text(encoding="utf-8"),
            "source_graph_hash": source_hash,
            "target_graph_text": target_text,
            "target_graph_hash": target_hash,
            "source_state": source_state,
            "source_state_hash": source_state_hash,
            "target_state": target_state,
            "target_state_hash": target_state_hash,
            "checkpoint": checkpoint,
            "checkpoint_hash": checkpoint_hash,
            "receipt": receipt,
            "receipt_hash": _atomic_json_hash(receipt),
        }
        intent_path = _workflow_transactions_root(layout) / f"{transaction_id}.json"
        atomic_write_json(intent_path, intent)
        return _resume_graph_revision(layout, intent_path, intent)


def _resume_graph_revision(
    layout: ProjectLayout, intent_path: Path, intent: Mapping[str, Any]
) -> JsonObject:
    intent = _validate_graph_revision_intent(layout, intent)
    checkpoint = intent["checkpoint"]
    receipt = intent["receipt"]
    checkpoint_path = Path(checkpoint["checkpoint_path"])
    if checkpoint_path.is_file():
        if read_json(checkpoint_path) != checkpoint:
            raise ValueError("graph revision checkpoint conflicts")
    else:
        atomic_write_json(checkpoint_path, checkpoint)
    if intent["phase"] == "prepared":
        intent = _set_phase(intent_path, intent, "checkpoint_written")

    matching = [
        row
        for row in read_jsonl(layout.journal)
        if row.get("record_type") == "graph-revision"
        and row.get("transaction_id") == intent["transaction_id"]
    ]
    if not matching:
        append_jsonl_locked(layout.journal, receipt)
    elif len(matching) != 1 or matching[0] != receipt:
        raise ValueError("graph revision receipt conflicts")
    if intent["phase"] == "checkpoint_written":
        intent = _set_phase(intent_path, intent, "receipt_appended")

    current_graph_hash = sha256_file(layout.graph)
    current_state = read_json(layout.state)
    current_state_hash = sha256_file(layout.state)
    if current_graph_hash == intent["source_graph_hash"]:
        if (
            current_state != intent["source_state"]
            or current_state_hash != intent["source_state_hash"]
        ):
            raise ValueError("graph revision source state conflicts")
        _atomic_write_text(layout.graph, intent["target_graph_text"])
        current_graph_hash = sha256_file(layout.graph)
    if current_graph_hash != intent["target_graph_hash"]:
        raise ValueError("graph revision graph conflicts")
    if intent["phase"] == "receipt_appended":
        intent = _set_phase(intent_path, intent, "graph_written")

    if current_state == intent["source_state"]:
        if current_state_hash != intent["source_state_hash"]:
            raise ValueError("graph revision source state hash conflicts")
        _atomic_write_research_state(layout, intent["target_state"])
        current_state = read_json(layout.state)
        current_state_hash = sha256_file(layout.state)
    if (
        current_state != intent["target_state"]
        or current_state_hash != intent["target_state_hash"]
    ):
        raise ValueError("graph revision target state conflicts")
    if intent["phase"] == "graph_written":
        intent = _set_phase(intent_path, intent, "state_written")
    if intent["phase"] != "committed":
        intent = _set_phase(intent_path, intent, "committed")
    return {
        "status": "applied",
        "transaction_id": intent["transaction_id"],
        "gate_id": intent["gate_id"],
        "source_graph_hash": intent["source_graph_hash"],
        "target_graph_hash": intent["target_graph_hash"],
        "receipt_id": receipt["receipt_id"],
    }


def _existing_revision_intent(
    layout: ProjectLayout, gate_id: str
) -> tuple[Path | None, JsonObject | None]:
    matches: list[tuple[Path, JsonObject]] = []
    for path in sorted(_workflow_transactions_root(layout).glob("*.json")):
        value = read_json(path)
        if value.get("kind") == "graph-revision" and value.get("gate_id") == gate_id:
            matches.append((path, value))
    if len(matches) > 1:
        raise ValueError("multiple graph revision intents share one gate")
    return matches[0] if matches else (None, None)


def _graph_revision_transaction_id(
    gate_id: str,
    gate_payload_hash: str,
    source_graph_hash: str,
    target_graph_hash: str,
    source_state_hash: str,
) -> str:
    return _graph_revision_transaction_identity(
        gate_id,
        gate_payload_hash,
        source_graph_hash,
        target_graph_hash,
        source_state_hash,
    )


def _validate_graph_revision_intent(
    layout: ProjectLayout, value: Mapping[str, Any]
) -> JsonObject:
    return _validate_graph_revision_evidence(layout, value)


def _read_graph_document(path: Path) -> tuple[JsonObject, str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"could not read proposed research graph: {error}") from error
    return _read_graph_text(text)


def _read_graph_text(text: Any) -> tuple[JsonObject, str, str]:
    return _read_graph_revision_text(text)


def _reject_open_tasks(layout: ProjectLayout) -> None:
    for path in layout.cycles.glob("*/tasks/*/task.json"):
        if read_json(path).get("status") == "open":
            raise ValueError("graph revision requires no open tasks")


def _set_phase(
    intent_path: Path, intent: Mapping[str, Any], phase: str
) -> JsonObject:
    updated = dict(intent)
    updated["phase"] = phase
    atomic_write_json(intent_path, updated)
    return updated
