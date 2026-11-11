from __future__ import annotations
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from importlib import import_module
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
    ensure_current_budget_schema,
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
from autoresearch.skills import preflight_graph_skills

SAFE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


HEADING_PATTERN = re.compile(r"^## (.+?)\s*$")


TRANSITION_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "transaction_id",
        "phase",
        "source",
        "target",
        "source_revision",
        "target_revision",
        "source_state_hash",
        "target_state_hash",
        "graph_hash",
        "budget_hash",
        "checkpoint_hash",
        "receipt_hash",
        "checkpoint",
        "receipt",
        "next_state",
    }
)


CHECKPOINT_FIELDS = frozenset(
    {
        "checkpoint_id",
        "created_at",
        "revision",
        "reason",
        "state",
        "state_hash",
        "graph_hash",
        "budget_hash",
        "ledger_line_counts",
        "checkpoint_path",
    }
)


TRANSITION_RECEIPT_FIELDS = frozenset(
    {
        "record_type",
        "transaction_id",
        "receipt_id",
        "transitioned_at",
        "source",
        "target",
        "source_revision",
        "target_revision",
        "source_state_hash",
        "target_state_hash",
        "checkpoint_id",
        "checkpoint_path",
        "checkpoint_hash",
        "graph_hash",
        "budget_hash",
    }
)


STATE_MUTATION_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "transaction_id",
        "phase",
        "operation",
        "patch",
        "source_revision",
        "target_revision",
        "source_state_hash",
        "target_state_hash",
        "graph_hash",
        "budget_hash",
        "checkpoint_hash",
        "receipt_hash",
        "checkpoint",
        "receipt",
        "next_state",
    }
)


STATE_MUTATION_RECEIPT_FIELDS = frozenset(
    {
        "record_type",
        "transaction_id",
        "receipt_id",
        "operation",
        "source_revision",
        "target_revision",
        "source_state_hash",
        "target_state_hash",
        "checkpoint_id",
        "checkpoint_path",
        "checkpoint_hash",
        "graph_hash",
        "budget_hash",
    }
)


LEDGER_COUNT_FIELDS = frozenset(
    {
        "gates",
        "journal",
        "model_calls",
        "artifact_index",
        "budget_settlements",
    }
)


GRAPH_REVISION_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "transaction_id",
        "phase",
        "gate_id",
        "gate_payload_hash",
        "source_graph_text",
        "source_graph_hash",
        "target_graph_text",
        "target_graph_hash",
        "source_state",
        "source_state_hash",
        "target_state",
        "target_state_hash",
        "checkpoint",
        "checkpoint_hash",
        "receipt",
        "receipt_hash",
    }
)
GRAPH_REVISION_PHASES = frozenset(
    {
        "prepared",
        "checkpoint_written",
        "receipt_appended",
        "graph_written",
        "state_written",
        "committed",
    }
)
GRAPH_REVISION_CHECKPOINT_FIELDS = CHECKPOINT_FIELDS
GRAPH_REVISION_RECEIPT_FIELDS = frozenset(
    {
        "record_type",
        "transaction_id",
        "receipt_id",
        "gate_id",
        "reason",
        "source_revision",
        "target_revision",
        "source_state_hash",
        "target_state_hash",
        "source_graph_hash",
        "target_graph_hash",
        "graph_hash",
        "checkpoint_id",
        "checkpoint_path",
        "checkpoint_hash",
        "budget_hash",
    }
)


class _DuplicateYamlKeyError(yaml.YAMLError):
    def __init__(self, key: Any):
        super().__init__(f"duplicate YAML key: {key}")
        self.key = key


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in seen
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "mapping keys must be scalar hashable values",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise _DuplicateYamlKeyError(key)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _graph_revision_transaction_identity(
    gate_id: str,
    gate_payload_hash: str,
    source_graph_hash: str,
    target_graph_hash: str,
    source_state_hash: str,
) -> str:
    identity = _atomic_json_hash(
        {
            "gate_id": gate_id,
            "gate_payload_hash": gate_payload_hash,
            "source_graph_hash": source_graph_hash,
            "target_graph_hash": target_graph_hash,
            "source_state_hash": source_state_hash,
        }
    )
    return f"graph-revision-{identity}"


def _read_graph_revision_text(text: Any) -> tuple[JsonObject, str, str]:
    if not isinstance(text, str):
        raise ValueError("graph revision graph text is invalid")
    try:
        graph = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"could not load YAML: {error}") from error
    if not isinstance(graph, Mapping):
        raise ValueError("YAML root must be a mapping")
    captured = dict(graph)
    errors = validate_graph(captured)
    if errors:
        raise ValueError(f"invalid research graph: {'; '.join(errors)}")
    return captured, text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_graph_revision_evidence(
    layout: ProjectLayout, value: Mapping[str, Any]
) -> JsonObject:
    if set(value) != GRAPH_REVISION_INTENT_FIELDS:
        raise ValueError("graph revision intent schema is invalid")
    intent = dict(value)
    if intent["schema_version"] != 1 or intent["kind"] != "graph-revision":
        raise ValueError("graph revision intent identity is invalid")
    _safe_id(intent["transaction_id"], "graph revision transaction_id")
    _safe_id(intent["gate_id"], "graph revision gate_id")
    if intent["phase"] not in GRAPH_REVISION_PHASES:
        raise ValueError("graph revision intent phase is invalid")
    for field in (
        "gate_payload_hash",
        "source_graph_hash",
        "target_graph_hash",
        "source_state_hash",
        "target_state_hash",
        "checkpoint_hash",
        "receipt_hash",
    ):
        _require_sha256(intent[field], field)
    gate = read_approved_gate(
        layout, intent["gate_id"], "graph", require_latest=False
    )
    payload = gate["payload"]
    if gate["payload_hash"] != intent["gate_payload_hash"]:
        raise ValueError("graph revision gate payload evidence conflicts")
    source_graph, source_text, source_hash = _read_graph_revision_text(
        intent["source_graph_text"]
    )
    target_graph, target_text, target_hash = _read_graph_revision_text(
        intent["target_graph_text"]
    )
    if (
        source_text != intent["source_graph_text"]
        or source_hash != intent["source_graph_hash"]
        or target_text != intent["target_graph_text"]
        or target_hash != intent["target_graph_hash"]
    ):
        raise ValueError("graph revision graph evidence conflicts")
    source_state = validate_research_state_document(
        intent["source_state"], source_graph
    )
    target_state = validate_research_state_document(
        intent["target_state"], target_graph
    )
    if (
        _atomic_json_hash(source_state) != intent["source_state_hash"]
        or _atomic_json_hash(target_state) != intent["target_state_hash"]
    ):
        raise ValueError("graph revision state evidence conflicts")
    source_revision = source_state["revision"]
    target_revision = target_state["revision"]
    if target_revision != source_revision + 1:
        raise ValueError("graph revision state revision evidence conflicts")
    checkpoint = intent["checkpoint"]
    receipt = intent["receipt"]
    if not isinstance(checkpoint, Mapping) or not isinstance(receipt, Mapping):
        raise ValueError("graph revision durable evidence is invalid")
    _require_exact_fields(
        checkpoint,
        GRAPH_REVISION_CHECKPOINT_FIELDS,
        "graph revision checkpoint",
    )
    _require_exact_fields(
        receipt,
        GRAPH_REVISION_RECEIPT_FIELDS,
        "graph revision receipt",
    )
    for field in (
        "checkpoint_id",
        "created_at",
        "reason",
        "state_hash",
        "graph_hash",
        "budget_hash",
        "checkpoint_path",
    ):
        _required_string(checkpoint, field)
    _safe_id(checkpoint["checkpoint_id"], "graph revision checkpoint_id")
    _require_nonnegative_integer(
        checkpoint["revision"], "graph revision checkpoint revision"
    )
    checkpoint_state = checkpoint["state"]
    ledger_counts = checkpoint["ledger_line_counts"]
    if not isinstance(checkpoint_state, Mapping) or not isinstance(ledger_counts, Mapping):
        raise ValueError("graph revision checkpoint content is invalid")
    _require_exact_fields(
        ledger_counts,
        LEDGER_COUNT_FIELDS,
        "graph revision checkpoint ledger evidence",
    )
    for field, count in ledger_counts.items():
        _require_nonnegative_integer(
            count, f"graph revision checkpoint ledger {field}"
        )
    checkpoint_path = Path(checkpoint["checkpoint_path"])
    ensure_within(layout.checkpoints, checkpoint_path)
    expected_checkpoint_path = (
        layout.checkpoints
        / f"{source_revision:06d}-{checkpoint['checkpoint_id']}.json"
    ).resolve()
    if checkpoint_path.resolve() != expected_checkpoint_path:
        raise ValueError("graph revision checkpoint path evidence conflicts")
    if checkpoint["reason"] != f"graph revision: {payload['reason']}":
        raise ValueError("graph revision checkpoint reason evidence conflicts")
    if checkpoint["revision"] != source_revision:
        raise ValueError("graph revision checkpoint revision evidence conflicts")
    if dict(checkpoint_state) != dict(source_state):
        raise ValueError("graph revision checkpoint state evidence conflicts")
    if (
        checkpoint["state_hash"] != intent["source_state_hash"]
        or checkpoint["graph_hash"] != intent["source_graph_hash"]
        or _atomic_json_hash(checkpoint_state) != checkpoint["state_hash"]
    ):
        raise ValueError("graph revision checkpoint hash evidence conflicts")
    if _atomic_json_hash(checkpoint) != intent["checkpoint_hash"]:
        raise ValueError("graph revision checkpoint evidence conflicts")
    if _atomic_json_hash(receipt) != intent["receipt_hash"]:
        raise ValueError("graph revision receipt evidence conflicts")
    for field in GRAPH_REVISION_RECEIPT_FIELDS - {
        "source_revision",
        "target_revision",
    }:
        _required_string(receipt, field)
    _require_nonnegative_integer(
        receipt["source_revision"], "graph revision receipt source_revision"
    )
    _require_nonnegative_integer(
        receipt["target_revision"], "graph revision receipt target_revision"
    )
    expected_transaction_id = _graph_revision_transaction_identity(
        intent["gate_id"],
        intent["gate_payload_hash"],
        intent["source_graph_hash"],
        intent["target_graph_hash"],
        intent["source_state_hash"],
    )
    expected_receipt_id = (
        "graph-revision-receipt-"
        f"{expected_transaction_id.removeprefix('graph-revision-')[:32]}"
    )
    expected_target_state = dict(source_state)
    expected_target_state.update(
        {
            "revision": source_revision + 1,
            "previous_state_hash": intent["source_state_hash"],
            "last_transition_id": expected_receipt_id,
        }
    )
    expected = {
        "gate.base_graph_hash": (payload["base_graph_hash"], intent["source_graph_hash"]),
        "gate.target_graph_hash": (payload["target_graph_hash"], intent["target_graph_hash"]),
        "gate.state_revision": (payload["state_revision"], source_revision),
        "receipt.record_type": (receipt["record_type"], "graph-revision"),
        "receipt.transaction_id": (receipt["transaction_id"], expected_transaction_id),
        "receipt.receipt_id": (receipt["receipt_id"], expected_receipt_id),
        "receipt.gate_id": (receipt["gate_id"], intent["gate_id"]),
        "receipt.reason": (receipt["reason"], payload["reason"]),
        "receipt.source_revision": (receipt["source_revision"], source_revision),
        "receipt.target_revision": (receipt["target_revision"], target_revision),
        "receipt.source_state_hash": (receipt["source_state_hash"], intent["source_state_hash"]),
        "receipt.target_state_hash": (receipt["target_state_hash"], intent["target_state_hash"]),
        "receipt.source_graph_hash": (receipt["source_graph_hash"], intent["source_graph_hash"]),
        "receipt.target_graph_hash": (receipt["target_graph_hash"], intent["target_graph_hash"]),
        "receipt.graph_hash": (receipt["graph_hash"], intent["target_graph_hash"]),
        "receipt.checkpoint_id": (receipt["checkpoint_id"], checkpoint["checkpoint_id"]),
        "receipt.checkpoint_path": (receipt["checkpoint_path"], checkpoint["checkpoint_path"]),
        "receipt.checkpoint_hash": (receipt["checkpoint_hash"], intent["checkpoint_hash"]),
        "receipt.budget_hash": (receipt["budget_hash"], checkpoint["budget_hash"]),
    }
    for field, (actual, wanted) in expected.items():
        if actual != wanted:
            raise ValueError(f"graph revision evidence conflicts at {field}")
    if intent["transaction_id"] != expected_transaction_id:
        raise ValueError("graph revision transaction identity conflicts")
    if dict(target_state) != expected_target_state:
        raise ValueError("graph revision target state content conflicts")
    return intent


def state_mutation_transaction_id(
    operation: str,
    source_revision: int,
    patch: Mapping[str, Any],
) -> str:
    operation = _safe_id(operation, "state mutation operation")
    if (
        isinstance(source_revision, bool)
        or not isinstance(source_revision, int)
        or source_revision < 0
    ):
        raise ValueError("source_revision must be a non-negative integer")
    patch_copy = _json_copy(patch, "state mutation patch")
    identity_patch = _state_mutation_identity_patch(
        operation, patch_copy
    )
    identity = _hash_payload(
        {
            "kind": "state-mutation",
            "operation": operation,
            "source_revision": source_revision,
            "patch": identity_patch,
        }
    )
    return f"state-mutation-{identity}"


def _state_mutation_identity_patch(
    operation: str, patch: Mapping[str, Any]
) -> JsonObject:
    patch_copy = _json_copy(patch, "state mutation patch")
    if operation != "promote-provisional-best":
        return patch_copy
    pointer = patch_copy.get("provisional_best")
    if not isinstance(pointer, Mapping):
        raise ValueError(
            "Provisional Best promotion patch is invalid"
        )
    required = {
        "candidate_id",
        "report_artifact_id",
        "evaluation_cycle_checkpoint_id",
        "promoted_at",
    }
    if set(pointer) != required:
        raise ValueError(
            "Provisional Best promotion pointer is invalid"
        )
    return {
        "provisional_best": {
            field: pointer[field]
            for field in (
                "candidate_id",
                "report_artifact_id",
                "evaluation_cycle_checkpoint_id",
            )
        }
    }


def _same_state_mutation_patch(
    operation: str,
    left: Any,
    right: Mapping[str, Any],
) -> bool:
    if not isinstance(left, Mapping):
        return False
    if operation != "promote-provisional-best":
        return dict(left) == dict(right)
    return _state_mutation_identity_patch(
        operation, left
    ) == _state_mutation_identity_patch(operation, right)


def _validate_state_mutation_evidence(
    layout: ProjectLayout,
    intent_path: Path,
    intent: Mapping[str, Any],
    *,
    historical: bool = False,
) -> None:
    _validate_intent_header(intent, intent_path)
    _require_exact_fields(
        intent, STATE_MUTATION_INTENT_FIELDS, "state mutation intent"
    )
    checkpoint = intent.get("checkpoint")
    receipt = intent.get("receipt")
    next_state = intent.get("next_state")
    patch = intent.get("patch")
    if not all(
        isinstance(value, Mapping)
        for value in (checkpoint, receipt, next_state, patch)
    ):
        raise ValueError("state mutation evidence is invalid")
    _require_exact_fields(
        checkpoint, CHECKPOINT_FIELDS, "state mutation checkpoint"
    )
    _require_exact_fields(
        receipt, STATE_MUTATION_RECEIPT_FIELDS, "state mutation receipt"
    )
    operation = _safe_id(intent.get("operation"), "state mutation operation")
    source_revision = intent.get("source_revision")
    target_revision = intent.get("target_revision")
    _require_nonnegative_integer(
        source_revision, "state mutation source_revision"
    )
    _require_nonnegative_integer(
        target_revision, "state mutation target_revision"
    )
    if target_revision != source_revision + 1:
        raise ValueError("state mutation revision evidence conflicts")
    expected_transaction_id = state_mutation_transaction_id(
        operation, source_revision, patch
    )
    if intent.get("transaction_id") != expected_transaction_id:
        raise ValueError("state mutation operation identity conflicts")
    for field in (
        "source_state_hash",
        "target_state_hash",
        "graph_hash",
        "budget_hash",
        "checkpoint_hash",
        "receipt_hash",
    ):
        _require_sha256(
            intent.get(field), f"state mutation intent {field}"
        )
    for field in (
        "checkpoint_id",
        "created_at",
        "reason",
        "state_hash",
        "graph_hash",
        "budget_hash",
        "checkpoint_path",
    ):
        _required_string(checkpoint, field)
    for field in STATE_MUTATION_RECEIPT_FIELDS - {
        "source_revision",
        "target_revision",
    }:
        _required_string(receipt, field)
    if receipt.get("record_type") != "state-mutation":
        raise ValueError("state mutation receipt record_type is invalid")
    for field in ("source_revision", "target_revision"):
        _require_nonnegative_integer(
            receipt.get(field), f"state mutation receipt {field}"
        )
    checkpoint_state = checkpoint.get("state")
    ledger_counts = checkpoint.get("ledger_line_counts")
    if not isinstance(checkpoint_state, Mapping):
        raise ValueError("state mutation checkpoint.state is invalid")
    if not isinstance(ledger_counts, Mapping):
        raise ValueError("state mutation checkpoint ledger evidence is invalid")
    _require_exact_fields(
        ledger_counts,
        LEDGER_COUNT_FIELDS,
        "state mutation checkpoint ledger evidence",
    )
    for field, value in ledger_counts.items():
        _require_nonnegative_integer(
            value, f"state mutation checkpoint ledger {field}"
        )
    _require_nonnegative_integer(
        checkpoint.get("revision"), "state mutation checkpoint revision"
    )
    checkpoint_path = Path(
        _required_string(checkpoint, "checkpoint_path")
    )
    ensure_within(layout.checkpoints, checkpoint_path)
    expected_checkpoint_path = (
        layout.checkpoints
        / (
            f"{source_revision:06d}-"
            f"{checkpoint['checkpoint_id']}.json"
        )
    ).resolve()
    if checkpoint_path.resolve() != expected_checkpoint_path:
        raise ValueError("state mutation checkpoint path evidence conflicts")
    if checkpoint["reason"] != operation:
        raise ValueError("state mutation checkpoint reason evidence conflicts")
    if _atomic_json_hash(checkpoint) != intent["checkpoint_hash"]:
        raise ValueError("state mutation checkpoint canonical hash conflicts")
    if _atomic_json_hash(receipt) != intent["receipt_hash"]:
        raise ValueError("state mutation receipt canonical hash conflicts")
    checkpoint_state_hash = _atomic_json_hash(checkpoint_state)
    if (
        checkpoint_state_hash != checkpoint.get("state_hash")
        or checkpoint_state_hash != intent.get("source_state_hash")
    ):
        raise ValueError(
            "state mutation checkpoint.state hash evidence conflicts"
        )
    if checkpoint_state.get("revision") != source_revision:
        raise ValueError("state mutation checkpoint revision conflicts")
    expected_next_state = dict(checkpoint_state)
    expected_next_state.update(dict(patch))
    expected_next_state.update(
        {
            "revision": target_revision,
            "previous_state_hash": intent["source_state_hash"],
            "last_transition_id": receipt["receipt_id"],
        }
    )
    if dict(next_state) != expected_next_state:
        raise ValueError("state mutation target state content conflicts")
    if _atomic_json_hash(next_state) != intent["target_state_hash"]:
        raise ValueError("state mutation target state hash conflicts")
    expected = {
        "checkpoint.revision": (
            checkpoint.get("revision"),
            source_revision,
        ),
        "checkpoint.state_hash": (
            checkpoint.get("state_hash"),
            intent.get("source_state_hash"),
        ),
        "checkpoint.graph_hash": (
            checkpoint.get("graph_hash"),
            intent.get("graph_hash"),
        ),
        "checkpoint.budget_hash": (
            checkpoint.get("budget_hash"),
            intent.get("budget_hash"),
        ),
        "receipt.transaction_id": (
            receipt.get("transaction_id"),
            intent.get("transaction_id"),
        ),
        "receipt.operation": (
            receipt.get("operation"),
            operation,
        ),
        "receipt.source_revision": (
            receipt.get("source_revision"),
            source_revision,
        ),
        "receipt.target_revision": (
            receipt.get("target_revision"),
            target_revision,
        ),
        "receipt.source_state_hash": (
            receipt.get("source_state_hash"),
            intent.get("source_state_hash"),
        ),
        "receipt.target_state_hash": (
            receipt.get("target_state_hash"),
            intent.get("target_state_hash"),
        ),
        "receipt.checkpoint_id": (
            receipt.get("checkpoint_id"),
            checkpoint.get("checkpoint_id"),
        ),
        "receipt.checkpoint_path": (
            receipt.get("checkpoint_path"),
            checkpoint.get("checkpoint_path"),
        ),
        "receipt.checkpoint_hash": (
            receipt.get("checkpoint_hash"),
            intent.get("checkpoint_hash"),
        ),
        "receipt.graph_hash": (
            receipt.get("graph_hash"),
            intent.get("graph_hash"),
        ),
        "receipt.budget_hash": (
            receipt.get("budget_hash"),
            intent.get("budget_hash"),
        ),
    }
    for field, (actual, wanted) in expected.items():
        if actual != wanted:
            raise ValueError(
                f"state mutation evidence conflicts at {field}"
            )
    checkpoint_exists = checkpoint_path.is_file()
    matching = [
        row for row in read_jsonl(layout.journal)
        if row.get("record_type") == "state-mutation"
        and row.get("transaction_id") == intent["transaction_id"]
    ]
    if len(matching) > 1 or (matching and matching[0] != receipt):
        raise ValueError("state mutation receipt ledger conflicts")
    phase = intent["phase"]
    if phase != "prepared" and not checkpoint_exists:
        raise ValueError("state mutation declared phase checkpoint is missing")
    if phase in {"receipt_appended", "state_written", "committed"} and not matching:
        raise ValueError("state mutation declared phase receipt is missing")
    if phase == "prepared" and matching:
        raise ValueError("state mutation declared phase conflicts with receipt")
    if historical:
        if phase != "committed":
            raise ValueError("state mutation intent is not committed")
        if not checkpoint_exists or read_json(checkpoint_path) != checkpoint:
            raise ValueError("state mutation committed checkpoint conflicts")
        return
    if checkpoint_exists and read_json(checkpoint_path) != checkpoint:
        raise ValueError("state mutation checkpoint conflicts")
    _, current_graph_hash = _capture_graph(layout)
    if current_graph_hash != intent["graph_hash"]:
        raise ValueError("state mutation graph hash evidence conflicts")
    if sha256_file(layout.budget) != intent["budget_hash"]:
        raise ValueError("state mutation budget hash evidence conflicts")
    _validate_state_mutation_ledger_counts(layout, intent, matching)
    current = load_research_state(layout)
    current_hash = sha256_file(layout.state)
    is_source = (
        current.get("revision") == source_revision
        and current_hash == intent["source_state_hash"]
        and dict(current) == dict(checkpoint_state)
    )
    is_target = (
        current.get("revision") == target_revision
        and current_hash == intent["target_state_hash"]
        and dict(current) == dict(next_state)
    )
    if not is_source and not is_target:
        raise ValueError("state mutation current state evidence conflicts")
    if phase in {"state_written", "committed"} and not is_target:
        raise ValueError("state mutation declared phase state is missing")
    if phase in {"prepared", "checkpoint_written"} and is_target:
        raise ValueError("state mutation declared phase conflicts with state")


def _validate_state_mutation_ledger_counts(
    layout: ProjectLayout,
    intent: Mapping[str, Any],
    matching_receipts: Sequence[Mapping[str, Any]],
) -> None:
    checkpoint = intent["checkpoint"]
    actual = {
        "gates": len(read_jsonl(layout.gates)),
        "journal": len(read_jsonl(layout.journal)) - len(matching_receipts),
        "model_calls": len(read_jsonl(layout.model_calls)),
        "artifact_index": len(read_jsonl(layout.artifact_index)),
        "budget_settlements": len(
            read_jsonl(layout.budget_settlements)
        ),
    }
    if actual != checkpoint["ledger_line_counts"]:
        raise ValueError("state mutation ledger count evidence conflicts")


def _source_revision_graph_anchor(
    layout: ProjectLayout,
    intent: Mapping[str, Any],
) -> str:
    checkpoint = intent["checkpoint"]
    checkpoint_state = checkpoint["state"]
    source_revision = intent["source_revision"]
    source_hash = intent["source_state_hash"]
    current_state = load_research_state(layout)
    current_hash = sha256_file(layout.state)
    if (
        current_hash == source_hash
        and current_state.get("revision") == source_revision
    ):
        if dict(current_state) != dict(checkpoint_state):
            raise ValueError(
                "pending transition source state anchor conflicts"
            )
    elif (
        current_hash == intent["target_state_hash"]
        and current_state == intent["next_state"]
    ):
        pass
    else:
        raise ValueError(
            "pending transition current state anchor conflicts"
        )
    if source_revision == 0:
        initial = read_json(
            layout.checkpoints / "000000-initial.json"
        )
        anchor = initial.get("graph_hash")
        if (
            initial.get("revision") != 0
            or initial.get("state_hash") != source_hash
        ):
            raise ValueError(
                "pending transition initial graph anchor conflicts"
            )
    else:
        prior_receipt_id = checkpoint_state.get("last_transition_id")
        prior = [
            row
            for row in read_jsonl(layout.journal)
            if row.get("record_type")
            in {"stage-transition", "state-mutation", "graph-revision"}
            and row.get("receipt_id") == prior_receipt_id
            and row.get("target_revision") == source_revision
            and row.get("target_state_hash") == source_hash
        ]
        if len(prior) != 1:
            raise ValueError(
                "pending transition committed graph anchor is missing"
            )
        if prior[0].get("record_type") == "state-mutation":
            prior_intent = _committed_state_mutation_intent_for_receipt(
                layout, prior[0]
            )
            anchor = _historical_state_mutation_graph_anchor(
                layout, prior_intent, set()
            )
        elif prior[0].get("record_type") == "graph-revision":
            anchor = prior[0].get("target_graph_hash")
        else:
            prior_intent = _committed_transition_intent_for_receipt(
                layout, prior[0]
            )
            anchor = _historical_transition_graph_anchor(
                layout, prior_intent, set()
            )
    _require_sha256(anchor, "pending transition graph anchor")
    return anchor


def _committed_transition_intent_for_receipt(
    layout: ProjectLayout, receipt: Mapping[str, Any]
) -> JsonObject:
    source = _required_string(receipt, "source")
    target = _required_string(receipt, "target")
    source_revision = receipt.get("source_revision")
    if (
        isinstance(source_revision, bool)
        or not isinstance(source_revision, int)
        or source_revision < 0
    ):
        raise ValueError(
            "pending transition prior receipt revision is invalid"
        )
    path = _transition_intent_path(
        layout, source, target, source_revision
    )
    if not path.is_file():
        raise ValueError(
            "pending transition committed graph anchor intent is missing"
        )
    prior_intent = read_json(path)
    _validate_transition_intent_identity(
        prior_intent, source, target, source_revision
    )
    _validate_transition_evidence(layout, prior_intent)
    if (
        prior_intent.get("phase") != "committed"
        or prior_intent.get("receipt") != receipt
    ):
        raise ValueError(
            "pending transition committed graph anchor receipt conflicts"
        )
    return prior_intent


def _historical_transition_graph_anchor(
    layout: ProjectLayout,
    intent: Mapping[str, Any],
    seen: set[str],
) -> str:
    transaction_id = _required_string(intent, "transaction_id")
    if transaction_id in seen:
        raise ValueError("pending transition graph anchor cycle detected")
    seen.add(transaction_id)
    checkpoint = intent["checkpoint"]
    checkpoint_state = checkpoint["state"]
    source_revision = intent["source_revision"]
    source_hash = intent["source_state_hash"]
    checkpoint_path = Path(
        _required_string(checkpoint, "checkpoint_path")
    )
    ensure_within(layout.checkpoints, checkpoint_path)
    if (
        not checkpoint_path.is_file()
        or read_json(checkpoint_path) != checkpoint
    ):
        raise ValueError(
            "pending transition committed graph anchor checkpoint conflicts"
        )
    if source_revision == 0:
        initial = read_json(
            layout.checkpoints / "000000-initial.json"
        )
        if (
            initial.get("revision") != 0
            or initial.get("state_hash") != source_hash
        ):
            raise ValueError(
                "pending transition initial graph anchor conflicts"
            )
        anchor = initial.get("graph_hash")
    else:
        prior_receipt_id = checkpoint_state.get("last_transition_id")
        matching = [
            row
            for row in read_jsonl(layout.journal)
            if row.get("record_type") in {
                "stage-transition",
                "state-mutation",
                "graph-revision",
            }
            and row.get("receipt_id") == prior_receipt_id
            and row.get("target_revision") == source_revision
            and row.get("target_state_hash") == source_hash
        ]
        if len(matching) != 1:
            raise ValueError(
                "pending transition committed graph anchor history is missing"
            )
        if matching[0].get("record_type") == "state-mutation":
            prior_intent = _committed_state_mutation_intent_for_receipt(
                layout, matching[0]
            )
            anchor = _historical_state_mutation_graph_anchor(
                layout, prior_intent, seen
            )
        elif matching[0].get("record_type") == "graph-revision":
            anchor = matching[0].get("target_graph_hash")
        else:
            prior_intent = _committed_transition_intent_for_receipt(
                layout, matching[0]
            )
            anchor = _historical_transition_graph_anchor(
                layout, prior_intent, seen
            )
    _require_sha256(anchor, "pending transition graph anchor")
    if any(
        value != anchor
        for value in (
            intent.get("graph_hash"),
            checkpoint.get("graph_hash"),
            intent["receipt"].get("graph_hash"),
        )
    ):
        raise ValueError(
            "pending transition committed graph anchor evidence conflicts"
        )
    return anchor


def _committed_state_mutation_intent_for_receipt(
    layout: ProjectLayout,
    receipt: Mapping[str, Any],
) -> JsonObject:
    transaction_id = _required_string(receipt, "transaction_id")
    path = _workflow_transactions_root(layout) / f"{transaction_id}.json"
    if not path.is_file():
        raise ValueError(
            "committed state mutation anchor intent is missing"
        )
    intent = read_json(path)
    _validate_state_mutation_evidence(
        layout, path, intent, historical=True
    )
    if intent.get("receipt") != receipt:
        raise ValueError(
            "committed state mutation anchor receipt conflicts"
        )
    return intent


def _historical_state_mutation_graph_anchor(
    layout: ProjectLayout,
    intent: Mapping[str, Any],
    seen: set[str],
) -> str:
    transaction_id = _required_string(intent, "transaction_id")
    if transaction_id in seen:
        raise ValueError("state mutation graph anchor cycle detected")
    seen.add(transaction_id)
    checkpoint = intent["checkpoint"]
    checkpoint_state = checkpoint["state"]
    source_revision = intent["source_revision"]
    source_hash = intent["source_state_hash"]
    if source_revision == 0:
        initial = read_json(layout.checkpoints / "000000-initial.json")
        if (
            initial.get("revision") != 0
            or initial.get("state_hash") != source_hash
        ):
            raise ValueError("state mutation initial graph anchor conflicts")
        anchor = initial.get("graph_hash")
    else:
        prior_receipt_id = checkpoint_state.get("last_transition_id")
        matching = [
            row
            for row in read_jsonl(layout.journal)
            if row.get("record_type") in {
                "stage-transition",
                "state-mutation",
                "graph-revision",
            }
            and row.get("receipt_id") == prior_receipt_id
            and row.get("target_revision") == source_revision
            and row.get("target_state_hash") == source_hash
        ]
        if len(matching) != 1:
            raise ValueError(
                "state mutation committed graph anchor history is missing"
            )
        if matching[0].get("record_type") == "state-mutation":
            prior_intent = _committed_state_mutation_intent_for_receipt(
                layout, matching[0]
            )
            anchor = _historical_state_mutation_graph_anchor(
                layout, prior_intent, seen
            )
        elif matching[0].get("record_type") == "graph-revision":
            anchor = matching[0].get("target_graph_hash")
        else:
            prior_intent = _committed_transition_intent_for_receipt(
                layout, matching[0]
            )
            anchor = _historical_transition_graph_anchor(
                layout, prior_intent, seen
            )
    _require_sha256(anchor, "state mutation graph anchor")
    if any(
        value != anchor
        for value in (
            intent.get("graph_hash"),
            checkpoint.get("graph_hash"),
            intent["receipt"].get("graph_hash"),
        )
    ):
        raise ValueError("state mutation graph anchor evidence conflicts")
    return anchor


def _validate_transition_ledger_counts(
    layout: ProjectLayout,
    intent: Mapping[str, Any],
) -> None:
    checkpoint = intent["checkpoint"]
    receipt = intent["receipt"]
    expected = checkpoint["ledger_line_counts"]
    journal_rows = read_jsonl(layout.journal)
    matching_receipts = [
        row
        for row in journal_rows
        if row.get("record_type")
        in {"stage-transition", "state-mutation", "graph-revision"}
        and row.get("transaction_id") == intent["transaction_id"]
    ]
    if matching_receipts and (
        len(matching_receipts) != 1
        or matching_receipts[0] != receipt
    ):
        raise ValueError(
            "pending transition receipt ledger evidence conflicts"
        )
    actual = {
        "gates": len(read_jsonl(layout.gates)),
        "journal": len(journal_rows) - len(matching_receipts),
        "model_calls": len(read_jsonl(layout.model_calls)),
        "artifact_index": len(read_jsonl(layout.artifact_index)),
        "budget_settlements": len(
            read_jsonl(layout.budget_settlements)
        ),
    }
    if actual != expected:
        raise ValueError(
            "pending transition ledger count evidence conflicts"
        )


def _transition_transaction_id(
    source: str, target: str, expected_revision: int
) -> str:
    digest = _hash_payload(
        {
            "kind": "stage-transition",
            "source": source,
            "target": target,
            "source_revision": expected_revision,
        }
    )
    return f"stage-transition-{digest}"


def _transition_intent_path(
    layout: ProjectLayout,
    source: str,
    target: str,
    expected_revision: int,
) -> Path:
    return (
        _workflow_transactions_root(layout)
        / (
            f"{_transition_transaction_id(source, target, expected_revision)}"
            ".json"
        )
    )


def _validate_transition_intent_identity(
    intent: Mapping[str, Any],
    source: str,
    target: str,
    expected_revision: int,
) -> None:
    _validate_intent_header(intent)
    expected = {
        "kind": "stage-transition",
        "transaction_id": _transition_transaction_id(
            source, target, expected_revision
        ),
        "source": source,
        "target": target,
        "source_revision": expected_revision,
        "target_revision": expected_revision + 1,
    }
    for field, value in expected.items():
        if intent.get(field) != value:
            raise ValueError(
                f"pending transition identity conflicts at {field}"
            )


def _validate_transition_evidence(
    layout: ProjectLayout, intent: Mapping[str, Any]
) -> None:
    _validate_intent_header(intent)
    _require_exact_fields(
        intent, TRANSITION_INTENT_FIELDS, "transition intent"
    )
    checkpoint = intent.get("checkpoint")
    receipt = intent.get("receipt")
    next_state = intent.get("next_state")
    if (
        not isinstance(checkpoint, Mapping)
        or not isinstance(receipt, Mapping)
        or not isinstance(next_state, Mapping)
    ):
        raise ValueError("pending transition evidence is invalid")
    _require_exact_fields(
        checkpoint, CHECKPOINT_FIELDS, "transition checkpoint"
    )
    _require_exact_fields(
        receipt, TRANSITION_RECEIPT_FIELDS, "transition receipt"
    )
    for field in (
        "transaction_id",
        "source",
        "target",
        "source_state_hash",
        "target_state_hash",
        "graph_hash",
        "budget_hash",
        "checkpoint_hash",
        "receipt_hash",
    ):
        _required_string(intent, field)
    for field in (
        "source_state_hash",
        "target_state_hash",
        "graph_hash",
        "budget_hash",
        "checkpoint_hash",
        "receipt_hash",
    ):
        _require_sha256(intent[field], f"transition intent {field}")
    for field in ("source_revision", "target_revision"):
        _require_nonnegative_integer(
            intent.get(field), f"transition intent {field}"
        )
    if intent["target_revision"] != intent["source_revision"] + 1:
        raise ValueError("pending transition revision evidence conflicts")
    for field in (
        "checkpoint_id",
        "created_at",
        "reason",
        "state_hash",
        "graph_hash",
        "budget_hash",
        "checkpoint_path",
    ):
        _required_string(checkpoint, field)
    for field in (
        "record_type",
        "transaction_id",
        "receipt_id",
        "transitioned_at",
        "source",
        "target",
        "source_state_hash",
        "target_state_hash",
        "checkpoint_id",
        "checkpoint_path",
        "graph_hash",
        "budget_hash",
        "checkpoint_hash",
    ):
        _required_string(receipt, field)
    if receipt["record_type"] != "stage-transition":
        raise ValueError("pending transition receipt record_type is invalid")
    for field in ("source_revision", "target_revision"):
        _require_nonnegative_integer(
            receipt.get(field), f"transition receipt {field}"
        )
    checkpoint_state = checkpoint.get("state")
    ledger_counts = checkpoint.get("ledger_line_counts")
    if not isinstance(checkpoint_state, Mapping):
        raise ValueError("pending transition checkpoint.state is invalid")
    if not isinstance(ledger_counts, Mapping):
        raise ValueError(
            "pending transition checkpoint ledger evidence is invalid"
        )
    _require_exact_fields(
        ledger_counts,
        LEDGER_COUNT_FIELDS,
        "transition checkpoint ledger evidence",
    )
    for field, value in ledger_counts.items():
        _require_nonnegative_integer(
            value, f"transition checkpoint ledger {field}"
        )
    _require_nonnegative_integer(
        checkpoint.get("revision"), "transition checkpoint revision"
    )
    checkpoint_path = Path(
        _required_string(checkpoint, "checkpoint_path")
    )
    ensure_within(layout.checkpoints, checkpoint_path)
    expected_checkpoint_path = (
        layout.checkpoints
        / (
            f"{intent['source_revision']:06d}-"
            f"{checkpoint['checkpoint_id']}.json"
        )
    ).resolve()
    if checkpoint_path.resolve() != expected_checkpoint_path:
        raise ValueError(
            "pending transition checkpoint path evidence conflicts"
        )
    if checkpoint["reason"] != (
        f"before transition {intent['source']} -> {intent['target']}"
    ):
        raise ValueError(
            "pending transition checkpoint reason evidence conflicts"
        )
    if _atomic_json_hash(checkpoint) != intent["checkpoint_hash"]:
        raise ValueError(
            "pending transition checkpoint canonical hash conflicts"
        )
    if _atomic_json_hash(receipt) != intent["receipt_hash"]:
        raise ValueError(
            "pending transition receipt canonical hash conflicts"
        )
    checkpoint_state_hash = _atomic_json_hash(checkpoint_state)
    if (
        checkpoint_state_hash != checkpoint.get("state_hash")
        or checkpoint_state_hash != intent.get("source_state_hash")
    ):
        raise ValueError(
            "pending transition checkpoint.state hash evidence conflicts"
        )
    if (
        checkpoint_state.get("revision") != intent["source_revision"]
        or checkpoint_state.get("current_stage") != intent["source"]
    ):
        raise ValueError(
            "pending transition checkpoint.state identity conflicts"
        )
    expected_next_state = dict(checkpoint_state)
    expected_next_state.update(
        {
            "revision": intent["target_revision"],
            "current_stage": intent["target"],
            "previous_state_hash": intent["source_state_hash"],
            "last_transition_id": receipt["receipt_id"],
            "pending_gate": None,
        }
    )
    if dict(next_state) != expected_next_state:
        raise ValueError(
            "pending transition next_state content evidence conflicts"
        )
    expected = {
        "checkpoint.revision": (
            checkpoint.get("revision"),
            intent.get("source_revision"),
        ),
        "checkpoint.state_hash": (
            checkpoint.get("state_hash"),
            intent.get("source_state_hash"),
        ),
        "checkpoint.graph_hash": (
            checkpoint.get("graph_hash"),
            intent.get("graph_hash"),
        ),
        "checkpoint.budget_hash": (
            checkpoint.get("budget_hash"),
            intent.get("budget_hash"),
        ),
        "receipt.transaction_id": (
            receipt.get("transaction_id"),
            intent.get("transaction_id"),
        ),
        "receipt.source": (
            receipt.get("source"),
            intent.get("source"),
        ),
        "receipt.target": (
            receipt.get("target"),
            intent.get("target"),
        ),
        "receipt.source_revision": (
            receipt.get("source_revision"),
            intent.get("source_revision"),
        ),
        "receipt.target_revision": (
            receipt.get("target_revision"),
            intent.get("target_revision"),
        ),
        "receipt.source_state_hash": (
            receipt.get("source_state_hash"),
            intent.get("source_state_hash"),
        ),
        "receipt.target_state_hash": (
            receipt.get("target_state_hash"),
            intent.get("target_state_hash"),
        ),
        "receipt.checkpoint_id": (
            receipt.get("checkpoint_id"),
            checkpoint.get("checkpoint_id"),
        ),
        "receipt.checkpoint_path": (
            receipt.get("checkpoint_path"),
            checkpoint.get("checkpoint_path"),
        ),
        "receipt.checkpoint_hash": (
            receipt.get("checkpoint_hash"),
            intent.get("checkpoint_hash"),
        ),
        "receipt.graph_hash": (
            receipt.get("graph_hash"),
            intent.get("graph_hash"),
        ),
        "receipt.budget_hash": (
            receipt.get("budget_hash"),
            intent.get("budget_hash"),
        ),
        "next_state.revision": (
            next_state.get("revision"),
            intent.get("target_revision"),
        ),
        "next_state.current_stage": (
            next_state.get("current_stage"),
            intent.get("target"),
        ),
        "next_state.previous_state_hash": (
            next_state.get("previous_state_hash"),
            intent.get("source_state_hash"),
        ),
        "next_state.last_transition_id": (
            next_state.get("last_transition_id"),
            receipt.get("receipt_id"),
        ),
    }
    for field, (actual, wanted) in expected.items():
        if actual != wanted:
            raise ValueError(
                f"pending transition evidence conflicts at {field}"
            )
    if _atomic_json_hash(next_state) != intent.get("target_state_hash"):
        raise ValueError(
            "pending transition target state hash evidence conflicts"
        )


def _state_integrity_error(
    layout: ProjectLayout,
    state: Mapping[str, Any],
    state_hash: str,
    graph_hash: str,
) -> str | None:
    revision = state["revision"]
    if revision == 0:
        initial_path = layout.checkpoints / "000000-initial.json"
        try:
            initial = read_json(initial_path)
        except OSError as error:
            return f"initial checkpoint is unavailable: {error}"
        if (
            state.get("last_transition_id") is not None
            or state.get("previous_state_hash") is not None
            or initial.get("state_hash") != state_hash
        ):
            return "state hash mismatch with initial checkpoint"
        if initial.get("graph_hash") != graph_hash:
            return "graph hash mismatch with initial checkpoint"
        return None

    transitions = [
        row
        for row in read_jsonl(layout.journal)
        if row.get("record_type")
        in {"stage-transition", "state-mutation", "graph-revision"}
    ]
    if not transitions:
        return "state hash mismatch: transition receipt is missing"
    latest = transitions[-1]
    if latest.get("receipt_id") != state.get("last_transition_id"):
        return "state hash mismatch with latest transition receipt"
    if latest.get("target_revision") != revision:
        return "state revision mismatch with latest transition receipt"
    if latest.get("target_state_hash") != state_hash:
        return "state hash mismatch with latest transition receipt"
    if latest.get("source_state_hash") != state.get("previous_state_hash"):
        return "previous state hash mismatch with latest transition receipt"
    if latest.get("graph_hash") != graph_hash:
        return "graph hash mismatch with latest transition receipt"
    if latest.get("record_type") == "graph-revision":
        try:
            _committed_graph_revision_intent_for_receipt(layout, latest)
        except (OSError, ValueError) as error:
            return f"graph revision evidence is invalid: {error}"
    if latest.get("record_type") == "state-mutation":
        try:
            _committed_state_mutation_intent_for_receipt(layout, latest)
        except (OSError, ValueError) as error:
            return (
                "latest committed state mutation evidence is invalid: "
                f"{error}"
            )
    return None


def _committed_graph_revision_intent_for_receipt(
    layout: ProjectLayout, receipt: Mapping[str, Any]
) -> JsonObject:
    transaction_id = _safe_id(
        receipt.get("transaction_id"), "graph revision transaction_id"
    )
    intent_path = _workflow_transactions_root(layout) / f"{transaction_id}.json"
    intent = _validate_graph_revision_evidence(
        layout, read_json(intent_path)
    )
    if intent.get("phase") != "committed":
        raise ValueError("graph revision evidence is not committed")
    if intent.get("receipt") != receipt:
        raise ValueError("graph revision receipt conflicts with durable intent")
    checkpoint_path = Path(intent["checkpoint"]["checkpoint_path"])
    if (
        not checkpoint_path.is_file()
        or read_json(checkpoint_path) != intent["checkpoint"]
    ):
        raise ValueError("graph revision committed checkpoint conflicts")
    return intent


def validate_project_integrity(layout: ProjectLayout) -> list[str]:
    """Validate the shared pre-mutation project, ledger and intent boundary."""
    try:
        with _exclusive_lock(_workflow_lock_path(layout)):
            budget = ensure_current_budget_schema(layout)
            state = load_research_state(layout)
            graph, graph_hash = _capture_graph(layout)
            preflight_graph_skills(graph)
            integrity_error = _state_integrity_error(
                layout, state, sha256_file(layout.state), graph_hash
            )
            if integrity_error is not None:
                return [integrity_error]
            _reject_other_pending_intents(layout, None)
            _validate_reservation_task_consistency(layout, budget)
    except (OSError, ValueError) as error:
        return [str(error)]
    return []


def inspect_pending_intents(layout: ProjectLayout) -> list[JsonObject]:
    """Read pending workflow/budget intents without attempting recovery.

    Malformed intents are not fatal: each is returned as an isolatable entry
    with ``kind: "malformed"`` and the validation error so the caller can
    quarantine it without losing the other pending intents.
    """
    pending: list[JsonObject] = []
    for category, root in (
        ("workflow", _workflow_transactions_root(layout)),
        ("budget", layout.budget_transactions),
    ):
        if not root.exists():
            continue
        if not root.is_dir():
            raise ValueError(f"{category} transaction store is invalid")
        for path in sorted(root.glob("*.json")):
            try:
                intent = read_json(path)
                if category == "workflow":
                    _validate_intent_header(intent, path)
                elif intent.get("record_type") != "budget-settlement-intent":
                    raise ValueError("budget transaction intent is invalid")
            except (ValueError, TypeError) as error:
                pending.append(
                    {
                        "intent_id": path.stem,
                        "category": category,
                        "kind": "malformed",
                        "phase": "malformed",
                        "path": str(path),
                        "error": str(error),
                    }
                )
                continue
            if intent.get("phase") != "committed":
                pending.append(
                    {
                        "intent_id": path.stem,
                        "category": category,
                        "kind": intent.get("kind", intent.get("record_type")),
                        "phase": intent.get("phase"),
                        "path": str(path),
                    }
                )
    return pending


def quarantine_pending_intent(
    layout: ProjectLayout, intent_id: str, reason: str
) -> JsonObject:
    if not isinstance(intent_id, str) or SAFE_ID_PATTERN.fullmatch(intent_id) is None:
        raise ValueError("intent id must be a safe non-empty identifier")
    if not isinstance(reason, str) or not reason:
        raise ValueError("quarantine reason must be a non-empty string")
    # Inspect and atomically relocate under the workflow lock so a concurrent
    # preflight or recovery cannot remove or settle the intent between the
    # inspect and the os.replace (TOCTOU).
    with _exclusive_lock(_workflow_lock_path(layout)):
        matches = [item for item in inspect_pending_intents(layout) if item["intent_id"] == intent_id]
        if len(matches) != 1:
            raise ValueError("pending intent is unknown or ambiguous")
        item = matches[0]
        source = Path(item["path"])
        destination = layout.quarantine / item["category"] / source.name
        if destination.exists():
            raise ValueError("quarantine destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # os.replace is an atomic rename within the project filesystem; intent data
        # is never discarded, and the receipt records why automation stopped.
        os.replace(source, destination)
        receipt = {
            "schema_version": 1,
            "record_type": "intent-quarantine",
            "intent_id": intent_id,
            "category": item["category"],
            "kind": item["kind"],
            "reason": reason,
            "source_path": str(source),
            "quarantine_path": str(destination),
            "quarantined_at": _timestamp(),
        }
        append_jsonl_locked(layout.quarantine / "receipts.jsonl", receipt)
    return receipt


def retry_pending_intent(layout: ProjectLayout, intent_id: str) -> JsonObject:
    matches = [item for item in inspect_pending_intents(layout) if item["intent_id"] == intent_id]
    if len(matches) != 1:
        raise ValueError("pending intent is unknown or ambiguous")
    item = matches[0]
    intent = read_json(Path(item["path"]))
    if item["category"] == "budget":
        # Resume only the single settlement intent named by --intent-id; a
        # blanket resume of all pending settlements raises when more than one
        # is outstanding and contradicts the requested intent.
        from autoresearch.governance import (
            _budget_lock_path,
            _resume_settlement_unlocked,
            _validate_settlement_intent,
        )
        with _exclusive_lock(_budget_lock_path(layout)):
            _resume_settlement_unlocked(
                layout, _validate_settlement_intent(layout, intent)
            )
        return {"intent_id": intent_id, "status": "resumed"}
    if intent.get("kind") == "stage-transition":
        transition_stage = import_module(
            "autoresearch.workflow.stages"
        ).transition_stage
        result = transition_stage(
            layout, intent["source"], intent["target"], intent["source_revision"]
        )
        return {"intent_id": intent_id, "status": result.status}
    if intent.get("kind") == "task-completion":
        complete_task = import_module(
            "autoresearch.workflow.tasks"
        ).complete_task
        result = complete_task(layout, intent["task_id"], Path(intent["report_path"]))
        return {"intent_id": intent_id, "status": result["status"]}
    raise ValueError("intent kind requires its owning controller retry")


def _validate_reservation_task_consistency(
    layout: ProjectLayout, budget: Mapping[str, Any]
) -> None:
    reservations = budget.get("reservations")
    if not isinstance(reservations, Mapping):
        raise ValueError("budget reservations are invalid")
    for reservation_id in reservations:
        if not isinstance(reservation_id, str):
            raise ValueError("budget reservation identity is invalid")
        if reservation_id.startswith("run:"):
            # A run reservation may exist for a run that never produced state
            # (e.g. GPU readiness failed before launch). The budget record is
            # authoritative; missing run state is recoverable via detach, not a
            # preflight blocker.
            continue
        if reservation_id.startswith("task:"):
            segments = reservation_id.split(":")
            if len(segments) != 3 or not segments[1] or not segments[2]:
                raise ValueError(
                    "task reservation id must be task:<cycle-id>:<task-id>"
                )
            _, cycle_id, task_id = segments
            task_path = layout.cycles / cycle_id / "tasks" / task_id / "task.json"
            if not task_path.is_file():
                raise ValueError("task reservation has no task record")
            task = read_json(task_path)
            if task.get("reservation_id") != reservation_id:
                raise ValueError("task reservation identity conflicts")


def _atomic_write_research_state(
    layout: ProjectLayout, state: Mapping[str, Any]
) -> None:
    graph, _ = _capture_graph(layout)
    validated = validate_research_state_document(state, graph)
    atomic_write_json(layout.state, validated)


def _capture_graph(
    layout: ProjectLayout,
) -> tuple[JsonObject, str]:
    graph_bytes = layout.graph.read_bytes()
    graph_hash = hashlib.sha256(graph_bytes).hexdigest()
    try:
        graph = yaml.load(
            graph_bytes.decode("utf-8"),
            Loader=_UniqueKeySafeLoader,
        )
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"could not load YAML: {error}") from error
    if not isinstance(graph, Mapping):
        raise ValueError("YAML root must be a mapping")
    captured = dict(graph)
    errors = validate_graph(captured)
    if errors:
        raise ValueError(f"invalid research graph: {'; '.join(errors)}")
    return captured, graph_hash


def _graph_stage(
    graph: Mapping[str, Any], stage_id: str
) -> JsonObject:
    stages = graph.get("stages")
    if not isinstance(stages, list):
        raise ValueError("research graph stages must be a list")
    matches = [
        dict(stage)
        for stage in stages
        if isinstance(stage, Mapping) and stage.get("id") == stage_id
    ]
    if not matches:
        raise ValueError(f"unknown stage: {stage_id}")
    if len(matches) > 1:
        raise ValueError(f"duplicate stage: {stage_id}")
    return matches[0]


def _atomic_json_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or value in {".", ".."}
        or SAFE_ID_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be a safe non-empty identifier")
    return value


def _required_string(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string")
    return item


def _require_exact_fields(
    value: Mapping[str, Any],
    expected: frozenset[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} schema is invalid")


def _require_nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return list(value)


def _nonnegative_number(value: Any, field: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be a non-negative finite number")
    return value


def _json_copy(value: Mapping[str, Any], name: str) -> JsonObject:
    try:
        encoded = json.dumps(
            dict(value), ensure_ascii=False, allow_nan=False
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain JSON values") from error
    if not isinstance(copied, dict):
        raise ValueError(f"{name} must be an object")
    return copied


def _hash_payload(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        suffix=".tmp",
        delete=False,
    ) as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
        temporary_path = Path(stream.name)
    os.replace(temporary_path, path)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as source_stream, NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        suffix=".tmp",
        delete=False,
    ) as destination_stream:
        shutil.copyfileobj(source_stream, destination_stream)
        destination_stream.flush()
        os.fsync(destination_stream.fileno())
        temporary_path = Path(destination_stream.name)
    os.replace(temporary_path, destination)


def _workflow_lock_path(layout: ProjectLayout) -> Path:
    return layout.root / ".research" / "workflow.lock"


def _workflow_transactions_root(layout: ProjectLayout) -> Path:
    return layout.root / ".research" / "workflow-transactions"


def _reject_other_pending_intents(
    layout: ProjectLayout,
    allowed_transaction_id: str | Sequence[str] | None,
) -> None:
    if allowed_transaction_id is None:
        allowed_transaction_ids: set[str] = set()
    elif isinstance(allowed_transaction_id, str):
        allowed_transaction_ids = {allowed_transaction_id}
    elif all(
        isinstance(transaction_id, str) and transaction_id
        for transaction_id in allowed_transaction_id
    ):
        allowed_transaction_ids = set(allowed_transaction_id)
    else:
        raise ValueError("allowed workflow transaction identities are invalid")
    transactions_root = _workflow_transactions_root(layout)
    if not transactions_root.exists():
        return
    if not transactions_root.is_dir():
        raise ValueError("workflow transaction store is invalid")
    for intent_path in sorted(transactions_root.glob("*.json")):
        ensure_within(transactions_root, intent_path)
        intent = read_json(intent_path)
        _validate_intent_header(intent, intent_path)
        transaction_id = intent.get("transaction_id")
        phase = intent.get("phase")
        if (
            phase != "committed"
            and transaction_id not in allowed_transaction_ids
        ):
            raise ValueError(
                "conflicting pending transition or workflow transaction: "
                f"{transaction_id}"
            )


def _validate_intent_header(
    intent: Mapping[str, Any], intent_path: Path | None = None
) -> None:
    phases = {
        "task-completion": {
            "prepared",
            "artifact_registered",
            "settlement_confirmed",
            "receipt_appended",
            "task_state_written",
            "committed",
        },
        "stage-transition": {
            "prepared",
            "checkpoint_written",
            "receipt_appended",
            "state_written",
            "committed",
        },
        "state-mutation": {
            "prepared",
            "checkpoint_written",
            "receipt_appended",
            "state_written",
            "committed",
        },
        "graph-revision": {
            "prepared",
            "checkpoint_written",
            "receipt_appended",
            "graph_written",
            "state_written",
            "committed",
        },
        "candidate-operation": {
            "prepared",
            "event_appended",
            "state_committed",
            "committed",
        },
        "evaluation-cycle-bind": {
            "prepared",
            "checkpoint_written",
            "artifact_registered",
            "committed",
        },
    }
    kind = intent.get("kind")
    transaction_id = intent.get("transaction_id")
    phase = intent.get("phase")
    if (
        intent.get("schema_version") != 1
        or kind not in phases
        or not isinstance(transaction_id, str)
        or not transaction_id
        or not isinstance(phase, str)
        or phase not in phases[kind]
    ):
        raise ValueError("workflow transaction intent is invalid")
    if (
        intent_path is not None
        and intent_path.name != f"{transaction_id}.json"
    ):
        raise ValueError(
            "workflow transaction filename conflicts with its identity"
        )


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_rfc3339(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None
