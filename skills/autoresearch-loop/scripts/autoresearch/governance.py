from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from autoresearch.contracts import JsonObject
from autoresearch.io import (
    append_jsonl_locked,
    atomic_write_json,
    read_json,
    read_jsonl,
)
from autoresearch.project import ProjectLayout


GATE_TYPES = frozenset(
    {
        "modification",
        "budget",
        "graph",
        "charter",
        "evaluation",
        "publication",
    }
)
COORDINATOR_GATE_TYPES = frozenset({"charter", "evaluation"})
GRAPH_REVISION_FIELDS = frozenset(
    {
        "schema_version",
        "base_graph_hash",
        "target_graph_hash",
        "state_revision",
        "reason",
    }
)
MODIFICATION_FIELDS = frozenset(
    {
        "editable_paths",
        "append_only_paths",
        "read_only_paths",
        "allowed_actions",
        "allowed_commands",
        "dependency_policy",
        "model_policy",
        "network",
        "approval_required_actions",
    }
)
NESTED_MODIFICATION_FIELDS = {
    "dependency_policy": frozenset({"allowed", "undeclared_requires_gate"}),
    "model_policy": frozenset(
        {"allowed_families", "external_service_change_requires_gate"}
    ),
    "network": frozenset({"mode", "allowed_services"}),
}
ADDITIVE_BUDGET_FIELDS = (
    "wall_seconds",
    "cpu_seconds",
    "gpu_seconds",
    "storage_bytes",
)
QUOTA_BUDGET_FIELDS = (
    "official_candidates",
    "retries",
    "debug_candidates",
)
POLICY_BUDGET_FIELDS = ("gpu_type", "loop-subagent-bound")
BUDGET_LIMIT_FIELDS = frozenset(
    (
        *ADDITIVE_BUDGET_FIELDS,
        *QUOTA_BUDGET_FIELDS,
        *POLICY_BUDGET_FIELDS,
    )
)
BUDGET_PAYLOAD_FIELDS = frozenset(
    {"schema_version", "limits", "storage_accounting"}
)
STORAGE_ACCOUNTING_FIELDS = frozenset(
    {"count_snapshot_copy_once", "excluded_artifact_types"}
)
STORAGE_EXCLUDED_ARTIFACT_TYPES = ("model-weight",)
RESERVATION_BUDGET_FIELDS = frozenset(
    (*ADDITIVE_BUDGET_FIELDS, *POLICY_BUDGET_FIELDS)
)
GATE_ID_PATTERN = re.compile(r"gate-[0-9a-f]{32}")
COORDINATOR_GATE_PAYLOAD_FIELDS = frozenset(
    {
        "stage_id",
        "source_revision",
        "source_state_hash",
        "graph_hash",
        "output_artifacts",
    }
)
EVALUATION_GATE_ENVELOPE_FIELDS = frozenset(
    {"schema_version", "stage_gate", "evaluation_spec"}
)
EVALUATION_SPEC_ANCHOR_FIELDS = frozenset(
    {"evaluation_spec_id", "payload_hash"}
)
SETTLEMENT_ID_PATTERN = re.compile(r"budget-settlement-[0-9a-f]{64}")
SETTLEMENT_PHASES = frozenset(
    {"prepared", "budget_written", "receipt_appended", "committed"}
)
SETTLEMENT_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "record_type",
        "settlement_id",
        "reservation_id",
        "owner_transaction_id",
        "phase",
        "reservation",
        "actual",
        "budget_before",
        "budget_after",
        "receipt",
    }
)
SETTLEMENT_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "record_type",
        "settlement_id",
        "reservation_id",
        "reservation",
        "actual",
        "settled_at",
    }
)
RUN_SETTLEMENT_RECEIPT_FIELDS = frozenset(
    {*SETTLEMENT_RECEIPT_FIELDS, "run_id"}
)
LEGACY_V0_BUDGET_FIELDS = frozenset({"cost_usd", "model_tokens"})
CURRENT_BUDGET_FIELDS = frozenset(
    {
        "status", "gate_id", "schema_version", "limits",
        "storage_accounting", "reserved", "reservations", "consumed",
        "migration",
    }
)
# Memoize the migrated/current budget within one process so a CLI invocation
# that calls several governance functions does not re-read and re-validate the
# schema on every call. Keyed by (mtime, size); every budget write in this
# module also explicitly invalidates the entry.
_BUDGET_SCHEMA_CACHE: dict[Path, tuple[float, int, JsonObject]] = {}


def _invalidate_budget_schema_cache(layout: ProjectLayout) -> None:
    _BUDGET_SCHEMA_CACHE.pop(layout.budget, None)


def ensure_current_budget_schema(layout: ProjectLayout) -> JsonObject:
    """Migrate the only supported legacy ledger exactly once.

    Legacy controllers wrote provider-cost and token counters under either v0
    or early v1. Current accounting is wall/resource-only, so their historical
    values are retained only in the append-only migration receipt and never
    converted to zero-valued usage. The migrated budget is memoized per path
    within the process; budget writes invalidate the cache.
    """
    path = layout.budget
    try:
        stat = path.stat()
    except OSError:
        stat = None
    if stat is not None:
        cached = _BUDGET_SCHEMA_CACHE.get(path)
        if (
            cached is not None
            and cached[0] == stat.st_mtime
            and cached[1] == stat.st_size
        ):
            return cached[2]
    result = _ensure_current_budget_schema_locked(layout)
    try:
        stat = path.stat()
    except OSError:
        stat = None
    if stat is not None:
        _BUDGET_SCHEMA_CACHE[path] = (stat.st_mtime, stat.st_size, result)
    return result


def _ensure_current_budget_schema_locked(layout: ProjectLayout) -> JsonObject:
    with _exclusive_lock(_budget_lock_path(layout)):
        raw = _require_mapping(read_json(layout.budget), "budget")
        if raw.get("status") == "unapproved":
            # A newly initialized project has no active ledger schema yet.
            return raw
        version = raw.get("schema_version")
        legacy_fields_present = any(
            bool(set(_require_mapping(raw.get(field), f"budget {field}")) & LEGACY_V0_BUDGET_FIELDS)
            for field in ("limits", "reserved", "consumed")
        ) or any(
            bool(set(_require_mapping(value, "budget reservation")) & LEGACY_V0_BUDGET_FIELDS)
            for value in _require_mapping(raw.get("reservations"), "budget reservations").values()
        )
        if version == 1 and not legacy_fields_present:
            return _validated_approved_budget_value(raw)
        if version not in {0, 1} or not legacy_fields_present:
            raise ValueError("unknown budget schema_version or legacy format; migration refused")
        if raw.get("status") != "approved":
            raise ValueError("legacy budget must be approved before migration")
        legacy_expected = (
            CURRENT_BUDGET_FIELDS - {"migration"} | {"payload_hash"}
        )
        if set(raw) != legacy_expected:
            raise ValueError("legacy budget schema is not recognized")
        gate_id = raw.get("gate_id")
        if not isinstance(gate_id, str):
            raise ValueError("legacy budget approval identity is invalid")
        legacy_limits = _require_mapping(raw.get("limits"), "legacy budget limits")
        legacy_counters = set(ADDITIVE_BUDGET_FIELDS) | set(LEGACY_V0_BUDGET_FIELDS)
        if set(legacy_limits) != set(BUDGET_LIMIT_FIELDS) | set(LEGACY_V0_BUDGET_FIELDS):
            raise ValueError("legacy budget limits schema is not recognized")
        legacy_reserved = _require_mapping(raw.get("reserved"), "legacy reserved counters")
        legacy_consumed = _require_mapping(raw.get("consumed"), "legacy consumed counters")
        if set(legacy_reserved) != legacy_counters or set(legacy_consumed) != legacy_counters:
            raise ValueError("legacy budget counters schema is not recognized")
        for name, counters in (("reserved", legacy_reserved), ("consumed", legacy_consumed)):
            for field in legacy_counters:
                _normalize_additive_amount(counters[field], f"legacy {name}.{field}")
        legacy_reservations = _require_mapping(raw.get("reservations"), "legacy reservations")
        normalized_reservations: JsonObject = {}
        historical_reservations: JsonObject = {}
        for reservation_id, value in legacy_reservations.items():
            record = _require_mapping(value, "legacy reservation")
            allowed = set(RESERVATION_BUDGET_FIELDS) | {"id"} | set(LEGACY_V0_BUDGET_FIELDS)
            if set(record) - allowed:
                raise ValueError("legacy reservation schema is not recognized")
            historical_reservations[str(reservation_id)] = _json_copy(record, "legacy reservation")
            stripped = {key: item for key, item in record.items() if key not in LEGACY_V0_BUDGET_FIELDS}
            normalized = _normalize_reservation(stripped, require_id=True)
            if normalized["id"] != reservation_id:
                raise ValueError("legacy budget reservation identity is inconsistent")
            normalized_reservations[reservation_id] = normalized
        target_payload = {
            "schema_version": 1,
            "limits": _normalize_budget_limits(
                {key: value for key, value in legacy_limits.items() if key not in LEGACY_V0_BUDGET_FIELDS}
            ),
            "storage_accounting": _normalize_storage_accounting(
                _require_mapping(raw.get("storage_accounting"), "legacy storage accounting")
            ),
        }
        receipt = {
            "schema_version": 1,
            "record_type": "budget-schema-migration",
            "migration_id": f"budget-v{version}-legacy-to-v1-" + _hash_payload(raw)[:32],
            "from_schema_version": version,
            "to_schema_version": 1,
            "gate_id": gate_id,
            "legacy_usage": {
                "reserved": {field: legacy_reserved[field] for field in LEGACY_V0_BUDGET_FIELDS},
                "consumed": {field: legacy_consumed[field] for field in LEGACY_V0_BUDGET_FIELDS},
                "reservations": historical_reservations,
            },
            "migrated_at": _timestamp(),
        }
        migrated = {
            "status": "approved",
            "gate_id": gate_id,
            **target_payload,
            "reserved": {field: legacy_reserved[field] for field in ADDITIVE_BUDGET_FIELDS},
            "reservations": normalized_reservations,
            "consumed": {field: legacy_consumed[field] for field in ADDITIVE_BUDGET_FIELDS},
            "migration": receipt,
        }
        atomic_write_json(layout.budget, migrated)
        _invalidate_budget_schema_cache(layout)
        append_jsonl_locked(layout.budget_migrations, receipt)
        return _validated_approved_budget_value(migrated)


def request_gate(
    layout: ProjectLayout, gate_type: str, payload: Mapping[str, Any]
) -> JsonObject:
    if gate_type not in GATE_TYPES:
        raise ValueError(f"unknown gate type: {gate_type}")
    normalized_payload = _normalize_gate_payload(layout, gate_type, payload)
    payload_hash = _hash_payload(normalized_payload)
    gate_id = f"gate-{uuid4().hex}"
    requested_at = _timestamp()
    record: JsonObject = {
        "gate_id": gate_id,
        "gate_type": gate_type,
        "status": "requested",
        "payload_hash": payload_hash,
        "requested_at": requested_at,
        "decided_at": None,
        "approval_source": None,
        "approval_evidence": None,
    }
    payload_record = {
        "gate_id": gate_id,
        "gate_type": gate_type,
        "payload_hash": payload_hash,
        "payload": normalized_payload,
    }
    with _exclusive_lock(_gate_lock_path(layout)):
        atomic_write_json(_gate_payload_path(layout, gate_id), payload_record)
        append_jsonl_locked(layout.gates, record)
    return record


def record_gate_receipt(
    layout: ProjectLayout,
    gate_id: str,
    approval_evidence: Mapping[str, Any],
) -> JsonObject:
    if not isinstance(gate_id, str) or GATE_ID_PATTERN.fullmatch(gate_id) is None:
        raise ValueError(f"invalid gate id: {gate_id}")
    evidence = _require_mapping(approval_evidence, "approval evidence")
    source = evidence.get("source")
    detail = evidence.get("evidence")
    if not isinstance(source, str) or not source:
        raise ValueError("approval evidence source must be a non-empty string")
    if not isinstance(detail, str) or not detail:
        raise ValueError("approval evidence must be a non-empty string")
    review_input = evidence.get("review_input")
    if review_input is not None and not isinstance(review_input, Mapping):
        raise ValueError("approval review_input must be an object")

    with _exclusive_lock(_gate_lock_path(layout)):
        records = read_jsonl(layout.gates)
        matching = [record for record in records if record.get("gate_id") == gate_id]
        if not matching:
            raise ValueError(f"unknown gate id: {gate_id}")
        request = matching[0]
        if request.get("status") != "requested":
            raise ValueError(f"gate request is invalid: {gate_id}")
        if matching[-1].get("status") != "requested":
            raise ValueError(f"gate already decided: {gate_id}")

        payload_record = _require_mapping(
            read_json(_gate_payload_path(layout, gate_id)), "gate payload record"
        )
        gate_type = request.get("gate_type")
        if gate_type not in GATE_TYPES:
            raise ValueError(f"gate has invalid type: {gate_id}")
        payload = _normalize_gate_payload(
            layout,
            gate_type,
            _require_mapping(payload_record.get("payload"), "gate payload"),
        )
        payload_hash = _hash_payload(payload)
        if (
            request.get("payload_hash") != payload_hash
            or payload_record.get("payload_hash") != payload_hash
        ):
            raise ValueError(f"gate payload hash mismatch: {gate_id}")

        receipt: JsonObject = {
            "gate_id": gate_id,
            "gate_type": gate_type,
            "status": "approved",
            "payload_hash": payload_hash,
            "requested_at": request.get("requested_at"),
            "decided_at": _timestamp(),
            "approval_source": source,
            "approval_evidence": detail,
        }
        if review_input is not None:
            receipt["review_input"] = dict(review_input)
        if gate_type == "modification":
            atomic_write_json(
                _modification_envelope_path(layout),
                {
                    "status": "approved",
                    "gate_id": gate_id,
                    "payload_hash": payload_hash,
                    "payload": payload,
                },
            )
        elif gate_type == "budget":
            _activate_budget(layout, gate_id, payload)
        append_jsonl_locked(layout.gates, receipt)
    return receipt


def record_coordinator_gate_receipt(
    layout: ProjectLayout, gate_type: str, payload: Mapping[str, Any]
) -> JsonObject:
    """Append an auditable routine-gate request and Coordinator approval."""
    if gate_type not in COORDINATOR_GATE_TYPES:
        raise PermissionError(
            "Coordinator may record only charter or evaluation gates"
        )
    normalized = _normalize_coordinator_gate_payload(payload, gate_type)
    payload_hash = _hash_payload(normalized)
    request = _pending_coordinator_gate_request(
        layout, gate_type, normalized, payload_hash
    )
    if request is None:
        request = request_gate(layout, gate_type, normalized)
    return record_gate_receipt(
        layout,
        request["gate_id"],
        {
            "source": "Coordinator",
            "evidence": "routine gate criteria verified by Coordinator",
        },
    )


def _pending_coordinator_gate_request(
    layout: ProjectLayout,
    gate_type: str,
    payload: Mapping[str, Any],
    payload_hash: str,
) -> JsonObject | None:
    records = read_jsonl(layout.gates)
    for request in reversed(records):
        if (
            request.get("gate_type") != gate_type
            or request.get("status") != "requested"
            or request.get("payload_hash") != payload_hash
        ):
            continue
        gate_id = request.get("gate_id")
        if not isinstance(gate_id, str):
            continue
        matching = [
            record for record in records if record.get("gate_id") == gate_id
        ]
        if matching[-1].get("status") != "requested":
            continue
        try:
            payload_record = _require_mapping(
                read_json(_gate_payload_path(layout, gate_id)),
                "gate payload record",
            )
        except (OSError, ValueError):
            continue
        if payload_record.get("payload") == payload:
            return request
    return None


def reject_gate(
    layout: ProjectLayout,
    gate_id: str,
    rejection_evidence: Mapping[str, Any],
) -> JsonObject:
    return _record_gate_terminal_decision(
        layout, gate_id, "rejected", rejection_evidence
    )


def cancel_gate(
    layout: ProjectLayout,
    gate_id: str,
    cancellation_evidence: Mapping[str, Any],
) -> JsonObject:
    return _record_gate_terminal_decision(
        layout, gate_id, "cancelled", cancellation_evidence
    )


def _record_gate_terminal_decision(
    layout: ProjectLayout,
    gate_id: str,
    status: str,
    evidence: Mapping[str, Any],
) -> JsonObject:
    if not isinstance(gate_id, str) or GATE_ID_PATTERN.fullmatch(gate_id) is None:
        raise ValueError(f"invalid gate id: {gate_id}")
    if status not in {"rejected", "cancelled"}:
        raise ValueError("gate terminal status is invalid")
    evidence = _require_mapping(evidence, f"{status} gate evidence")
    source = evidence.get("source")
    reason = evidence.get("reason")
    if not isinstance(source, str) or not source:
        raise ValueError(f"{status} gate source must be a non-empty string")
    if not isinstance(reason, str) or not reason:
        raise ValueError(f"{status} gate reason must be a non-empty string")

    with _exclusive_lock(_gate_lock_path(layout)):
        records = read_jsonl(layout.gates)
        matching = [row for row in records if row.get("gate_id") == gate_id]
        if not matching:
            raise ValueError(f"unknown gate id: {gate_id}")
        request = matching[0]
        if request.get("status") != "requested" or matching[-1].get("status") != "requested":
            raise ValueError(f"gate already decided: {gate_id}")
        gate_type = request.get("gate_type")
        if gate_type not in GATE_TYPES:
            raise ValueError(f"gate request is invalid: {gate_id}")
        payload_record = _require_mapping(
            read_json(_gate_payload_path(layout, gate_id)), "gate payload record"
        )
        payload = _normalize_gate_payload(
            layout,
            gate_type,
            _require_mapping(payload_record.get("payload"), "gate payload"),
        )
        payload_hash = _hash_payload(payload)
        if (
            payload_record.get("gate_id") != gate_id
            or payload_record.get("gate_type") != gate_type
            or payload_record.get("payload_hash") != payload_hash
            or request.get("payload_hash") != payload_hash
        ):
            raise ValueError(f"gate payload hash mismatch: {gate_id}")
        prefix = "rejection" if status == "rejected" else "cancellation"
        receipt: JsonObject = {
            "gate_id": gate_id,
            "gate_type": gate_type,
            "status": status,
            "payload_hash": payload_hash,
            "requested_at": request.get("requested_at"),
            "decided_at": _timestamp(),
            f"{prefix}_source": source,
            f"{prefix}_reason": reason,
        }
        append_jsonl_locked(layout.gates, receipt)
    return receipt


def read_approved_gate(
    layout: ProjectLayout,
    gate_id: str,
    gate_type: str,
    *,
    require_latest: bool = True,
) -> JsonObject:
    """Read one latest approved gate with its hash-bound canonical payload."""
    if not isinstance(gate_id, str) or GATE_ID_PATTERN.fullmatch(gate_id) is None:
        raise ValueError(f"invalid gate id: {gate_id}")
    if gate_type not in GATE_TYPES:
        raise ValueError(f"unknown gate type: {gate_type}")
    with _exclusive_lock(_gate_lock_path(layout)):
        records = read_jsonl(layout.gates)
        matching = [row for row in records if row.get("gate_id") == gate_id]
        if len(matching) != 2:
            raise ValueError(f"approved gate receipt is invalid: {gate_id}")
        request, receipt = matching
        if (
            request.get("gate_id") != gate_id
            or request.get("gate_type") != gate_type
            or request.get("status") != "requested"
            or receipt.get("gate_id") != gate_id
            or receipt.get("gate_type") != gate_type
            or receipt.get("status") != "approved"
        ):
            raise ValueError(f"approved gate receipt is invalid: {gate_id}")
        latest = _latest_approved_by_type(records).get(gate_type)
        if require_latest and latest != receipt:
            raise ValueError(
                f"gate is not the latest approved {gate_type} receipt: {gate_id}"
            )
        payload_record = _require_mapping(
            read_json(_gate_payload_path(layout, gate_id)),
            "gate payload record",
        )
        if set(payload_record) != {
            "gate_id",
            "gate_type",
            "payload_hash",
            "payload",
        }:
            raise ValueError(f"gate payload record is invalid: {gate_id}")
        payload = _normalize_gate_payload(
            layout,
            gate_type,
            _require_mapping(payload_record.get("payload"), "gate payload"),
        )
        payload_hash = _hash_payload(payload)
        if (
            payload_record.get("gate_id") != gate_id
            or payload_record.get("gate_type") != gate_type
            or payload_record.get("payload_hash") != payload_hash
            or request.get("payload_hash") != payload_hash
            or receipt.get("payload_hash") != payload_hash
        ):
            raise ValueError(f"gate payload hash mismatch: {gate_id}")
        return {
            "gate_id": gate_id,
            "gate_type": gate_type,
            "payload_hash": payload_hash,
            "payload": payload,
            "request": request,
            "receipt": receipt,
        }


def require_execution_approval(layout: ProjectLayout) -> None:
    with _exclusive_lock(_gate_lock_path(layout)):
        records = read_jsonl(layout.gates)
        approved = _latest_approved_by_type(records)
        modification_receipt = approved.get("modification")
        budget_receipt = approved.get("budget")
        if modification_receipt is None or budget_receipt is None:
            raise PermissionError("modification and budget approval required")

        try:
            active_modification = _require_mapping(
                read_json(_modification_envelope_path(layout)),
                "active modification envelope",
            )
            modification_payload = _normalize_modification_payload(
                layout,
                _require_mapping(
                    active_modification.get("payload"), "modification payload"
                ),
            )
        except (OSError, ValueError) as error:
            raise PermissionError(
                f"modification approval hash could not be verified: {error}"
            ) from error
        modification_hash = _hash_payload(modification_payload)
        if (
            active_modification.get("status") != "approved"
            or active_modification.get("gate_id")
            != modification_receipt.get("gate_id")
            or active_modification.get("payload_hash") != modification_hash
            or modification_receipt.get("payload_hash") != modification_hash
        ):
            raise PermissionError("modification approval hash mismatch")

        try:
            budget = ensure_current_budget_schema(layout)
        except (OSError, ValueError) as error:
            raise PermissionError(
                f"budget approval could not be verified: {error}"
            ) from error
        if (
            budget.get("status") != "approved"
            or budget.get("gate_id") != budget_receipt.get("gate_id")
            or not isinstance(budget.get("limits"), Mapping)
        ):
            raise PermissionError("budget approval is invalid")


def reserve_budget(
    layout: ProjectLayout, reservation: Mapping[str, Any]
) -> JsonObject:
    ensure_current_budget_schema(layout)
    normalized = _normalize_reservation(reservation, require_id=True)
    reservation_id = normalized["id"]
    with _exclusive_lock(_budget_lock_path(layout)):
        _resume_pending_settlement_unlocked(layout)
        budget = _load_approved_budget(layout)
        if _settlement_intent_path(layout, reservation_id).is_file():
            intent = _load_settlement_intent(layout, reservation_id)
            if intent["phase"] == "committed":
                _resolve_budget_settlement_unlocked(
                    layout, intent["settlement_id"]
                )
                raise ValueError(
                    f"reservation id was historically settled: {reservation_id}"
                )
            raise ValueError(
                f"reservation has a pending settlement: {reservation_id}"
            )
        reservations = _require_mapping(
            budget.get("reservations"), "budget reservations"
        )
        if reservation_id in reservations:
            raise ValueError(f"reservation already exists: {reservation_id}")

        limits = _require_mapping(budget["limits"], "budget limits")
        reserved = _require_mapping(budget.get("reserved"), "reserved counters")
        consumed = _require_mapping(budget.get("consumed"), "consumed counters")
        for field in ADDITIVE_BUDGET_FIELDS:
            requested = normalized.get(field, 0)
            if not budget_cap_allows(
                limits[field],
                consumed.get(field, 0),
                reserved.get(field, 0),
                requested,
            ):
                raise ValueError(f"{field} reservation exceeds remaining budget")

        requested_gpu = normalized.get("gpu_type")
        if "gpu_type" in normalized and requested_gpu != limits["gpu_type"]:
            raise ValueError(
                f"gpu_type {requested_gpu!r} does not match approved "
                f"{limits['gpu_type']!r}"
            )

        requested_slots = normalized.get("loop-subagent-bound", 0)
        active_slots = sum(
            _require_mapping(active, "active reservation").get(
                "loop-subagent-bound", 0
            )
            for active in reservations.values()
        )
        if active_slots + requested_slots > limits["loop-subagent-bound"]:
            raise ValueError(
                "loop-subagent-bound reservation exceeds active worker limit"
            )

        updated_reservations = dict(reservations)
        updated_reservations[reservation_id] = normalized
        budget["reservations"] = updated_reservations
        budget["reserved"] = {
            field: reserved.get(field, 0) + normalized.get(field, 0)
            for field in ADDITIVE_BUDGET_FIELDS
        }
        atomic_write_json(layout.budget, budget)
        _invalidate_budget_schema_cache(layout)
        return budget


def settle_budget(
    layout: ProjectLayout,
    reservation_id: str,
    actual: Mapping[str, Any],
) -> JsonObject:
    return _settle_budget(
        layout,
        reservation_id,
        actual,
        settled_at=None,
        expected_settlement_id=None,
        owner_transaction_id=None,
    )


def detach_budget_reservation(
    layout: ProjectLayout, reservation_id: str, reason: str
) -> JsonObject:
    """Release an unmeasured reservation without inventing zero usage."""
    if not isinstance(reservation_id, str) or not reservation_id:
        raise ValueError("reservation id must be a non-empty string")
    if not isinstance(reason, str) or not reason:
        raise ValueError("reservation detach reason must be a non-empty string")
    ensure_current_budget_schema(layout)
    with _exclusive_lock(_budget_lock_path(layout)):
        budget = _load_approved_budget(layout)
        # Detach releases an unmeasured reservation; it must not resume and
        # commit a pending settlement for the same reservation (that would
        # invent measured usage then raise "unknown reservation"). A pending
        # or committed settlement for this reservation blocks detach: settle
        # it, or quarantine the settlement intent first.
        if _settlement_intent_path(layout, reservation_id).is_file():
            intent = _load_settlement_intent(layout, reservation_id)
            if intent["phase"] == "committed":
                _resolve_budget_settlement_unlocked(
                    layout, intent["settlement_id"]
                )
                raise ValueError(
                    f"reservation id was historically settled: {reservation_id}"
                )
            raise ValueError(
                f"reservation has a pending budget settlement; quarantine or "
                f"settle it before detaching: {reservation_id}"
            )
        reservations = _require_mapping(budget.get("reservations"), "budget reservations")
        reservation = reservations.get(reservation_id)
        if not isinstance(reservation, Mapping):
            raise ValueError(f"unknown reservation: {reservation_id}")
        normalized = _normalize_reservation(reservation, require_id=True)
        reserved = _require_mapping(budget.get("reserved"), "reserved counters")
        updated = dict(budget)
        updated["reserved"] = {
            field: _subtract_exact(reserved.get(field, 0), normalized.get(field, 0))
            for field in ADDITIVE_BUDGET_FIELDS
        }
        updated_reservations = dict(reservations)
        del updated_reservations[reservation_id]
        updated["reservations"] = updated_reservations
        receipt = {
            "schema_version": 1,
            "record_type": "budget-reservation-detach",
            "reservation_id": reservation_id,
            "reservation": normalized,
            "usage_status": "unknown",
            "reason": reason,
            "detached_at": _timestamp(),
        }
        atomic_write_json(layout.budget, updated)
        _invalidate_budget_schema_cache(layout)
        append_jsonl_locked(layout.reservation_detachments, receipt)
        return receipt


def plan_budget_settlement(
    layout: ProjectLayout,
    reservation_id: str,
    actual: Mapping[str, Any],
    *,
    settled_at: str | None = None,
    owner_transaction_id: str | None = None,
) -> JsonObject:
    if not isinstance(reservation_id, str) or not reservation_id:
        raise ValueError("reservation id must be a non-empty string")
    ensure_current_budget_schema(layout)
    normalized_actual = _normalize_reservation(actual, require_id=False)
    with _exclusive_lock(_budget_lock_path(layout)):
        target_path = _settlement_intent_path(layout, reservation_id)
        if target_path.is_file():
            intent = _load_settlement_intent(layout, reservation_id)
            _require_same_settlement_actual(intent, normalized_actual)
            _require_same_settlement_owner(
                intent, owner_transaction_id
            )
            if intent["phase"] == "committed":
                _resolve_budget_settlement_unlocked(
                    layout, intent["settlement_id"]
                )
            return _settlement_plan(intent)

        _resume_pending_settlement_unlocked(layout)
        budget = _load_approved_budget(layout)
        intent = _prepare_settlement_intent(
            layout,
            budget,
            reservation_id,
            normalized_actual,
            settled_at=settled_at,
            owner_transaction_id=owner_transaction_id,
        )
        atomic_write_json(target_path, intent)
        return _settlement_plan(intent)


def inspect_budget_settlement(
    layout: ProjectLayout,
    reservation_id: str,
    actual: Mapping[str, Any],
    *,
    owner_transaction_id: str | None = None,
) -> JsonObject:
    if not isinstance(reservation_id, str) or not reservation_id:
        raise ValueError("reservation id must be a non-empty string")
    normalized_actual = _normalize_reservation(actual, require_id=False)
    with _exclusive_lock(_budget_lock_path(layout)):
        path = _settlement_intent_path(layout, reservation_id)
        if not path.is_file():
            raise ValueError(
                f"missing budget settlement intent: {reservation_id}"
            )
        intent = _load_settlement_intent(layout, reservation_id)
        _require_same_settlement_actual(intent, normalized_actual)
        _require_same_settlement_owner(intent, owner_transaction_id)
        if intent["phase"] == "committed":
            _resolve_budget_settlement_unlocked(
                layout, intent["settlement_id"]
            )
        return _settlement_plan(intent)


def settle_budget_for_completion(
    layout: ProjectLayout,
    reservation_id: str,
    actual: Mapping[str, Any],
    *,
    settled_at: str,
    expected_settlement_id: str,
    owner_transaction_id: str,
) -> JsonObject:
    return _settle_budget(
        layout,
        reservation_id,
        actual,
        settled_at=settled_at,
        expected_settlement_id=expected_settlement_id,
        owner_transaction_id=owner_transaction_id,
    )


def resolve_budget_settlement(
    layout: ProjectLayout, settlement_id: str
) -> JsonObject:
    with _exclusive_lock(_budget_lock_path(layout)):
        return _resolve_budget_settlement_unlocked(layout, settlement_id)


def _settle_budget(
    layout: ProjectLayout,
    reservation_id: str,
    actual: Mapping[str, Any],
    *,
    settled_at: str | None,
    expected_settlement_id: str | None,
    owner_transaction_id: str | None,
) -> JsonObject:
    if not isinstance(reservation_id, str) or not reservation_id:
        raise ValueError("reservation id must be a non-empty string")
    ensure_current_budget_schema(layout)
    normalized_actual = _normalize_reservation(actual, require_id=False)
    with _exclusive_lock(_budget_lock_path(layout)):
        target_path = _settlement_intent_path(layout, reservation_id)
        if target_path.is_file():
            intent = _load_settlement_intent(layout, reservation_id)
            _require_same_settlement_actual(intent, normalized_actual)
            _require_same_settlement_owner(
                intent, owner_transaction_id
            )
        else:
            _resume_pending_settlement_unlocked(layout)
            budget = _load_approved_budget(layout)
            intent = _prepare_settlement_intent(
                layout,
                budget,
                reservation_id,
                normalized_actual,
                settled_at=settled_at,
                owner_transaction_id=owner_transaction_id,
            )
            atomic_write_json(target_path, intent)
        if (
            expected_settlement_id is not None
            and intent["settlement_id"] != expected_settlement_id
        ):
            raise ValueError("budget settlement identity conflicts")
        _resume_settlement_unlocked(layout, intent)
        return _load_approved_budget(layout)


def _prepare_settlement_intent(
    layout: ProjectLayout,
    budget: Mapping[str, Any],
    reservation_id: str,
    actual: Mapping[str, Any],
    *,
    settled_at: str | None,
    owner_transaction_id: str | None,
) -> JsonObject:
    reservations = _require_mapping(
        budget.get("reservations"), "budget reservations"
    )
    if reservation_id not in reservations:
        raise ValueError(f"unknown reservation: {reservation_id}")
    reservation = _normalize_reservation(
        _require_mapping(
            reservations[reservation_id], "budget reservation"
        ),
        require_id=True,
    )
    if reservation["id"] != reservation_id:
        raise ValueError("budget reservation identity is inconsistent")
    normalized_actual = _normalize_reservation(actual, require_id=False)
    budget_before = _json_copy(budget, "pre-settlement budget")
    budget_after = _updated_budget_after_settlement(
        budget_before, reservation_id, normalized_actual
    )
    settlement_id = _budget_settlement_id(reservation_id)
    receipt: JsonObject = {
        "schema_version": 1,
        "record_type": "budget-settlement",
        "settlement_id": settlement_id,
        "reservation_id": reservation_id,
        "reservation": reservation,
        "actual": normalized_actual,
        "settled_at": _normalize_settled_at(settled_at),
    }
    run_id = _run_id_from_reservation(reservation_id)
    if run_id is not None:
        receipt["run_id"] = run_id
    return {
        "schema_version": 1,
        "record_type": "budget-settlement-intent",
        "settlement_id": settlement_id,
        "reservation_id": reservation_id,
        "owner_transaction_id": _normalize_owner_transaction_id(
            owner_transaction_id
        ),
        "phase": "prepared",
        "reservation": reservation,
        "actual": normalized_actual,
        "budget_before": budget_before,
        "budget_after": budget_after,
        "receipt": receipt,
    }


def _updated_budget_after_settlement(
    budget: Mapping[str, Any],
    reservation_id: str,
    actual: Mapping[str, Any],
) -> JsonObject:
    budget = _validated_approved_budget_value(budget)
    reservations = _require_mapping(
        budget.get("reservations"), "budget reservations"
    )
    if reservation_id not in reservations:
        raise ValueError(f"unknown reservation: {reservation_id}")
    reservation = _normalize_reservation(
        _require_mapping(
            reservations[reservation_id], "budget reservation"
        ),
        require_id=True,
    )
    if reservation["id"] != reservation_id:
        raise ValueError("budget reservation identity is inconsistent")
    normalized_actual = _normalize_reservation(actual, require_id=False)

    limits = _require_mapping(budget.get("limits"), "budget limits")
    reserved = _require_mapping(budget.get("reserved"), "reserved counters")
    consumed = _require_mapping(budget.get("consumed"), "consumed counters")
    for field in ADDITIVE_BUDGET_FIELDS:
        remaining_reserved = reserved.get(field, 0) - reservation.get(field, 0)
        if remaining_reserved < 0:
            raise ValueError(f"{field} reserved counter is inconsistent")
        if not budget_cap_allows(
            limits[field],
            consumed.get(field, 0),
            remaining_reserved,
            normalized_actual.get(field, 0),
        ):
            raise ValueError(f"{field} actual exceeds approved budget")
    if (
        "gpu_type" in normalized_actual
        and normalized_actual["gpu_type"] != reservation.get("gpu_type")
    ):
        raise ValueError("actual gpu_type does not match reservation")
    if (
        "loop-subagent-bound" in normalized_actual
        and normalized_actual["loop-subagent-bound"]
        > reservation.get("loop-subagent-bound", 0)
    ):
        raise ValueError("actual loop-subagent-bound exceeds reservation")

    budget["reserved"] = {
        field: _subtract_exact(
            reserved.get(field, 0), reservation.get(field, 0)
        )
        for field in ADDITIVE_BUDGET_FIELDS
    }
    budget["consumed"] = {
        field: consumed.get(field, 0) + normalized_actual.get(field, 0)
        for field in ADDITIVE_BUDGET_FIELDS
    }
    updated_reservations = dict(reservations)
    del updated_reservations[reservation_id]
    budget["reservations"] = updated_reservations
    return budget


def _resume_pending_settlement_unlocked(layout: ProjectLayout) -> None:
    intents: list[JsonObject] = []
    for path in sorted(layout.budget_transactions.glob("*.json")):
        intent = _load_settlement_intent_path(layout, path)
        if intent["phase"] != "committed":
            intents.append(intent)
        else:
            _resolve_budget_settlement_unlocked(
                layout, intent["settlement_id"]
            )
    if len(intents) > 1:
        raise ValueError("multiple pending budget settlements require recovery")
    if intents:
        if intents[0]["owner_transaction_id"] is not None:
            raise ValueError(
                "owner-bound budget settlement requires owning workflow retry"
            )
        _resume_settlement_unlocked(layout, intents[0])


def _resume_settlement_unlocked(
    layout: ProjectLayout, intent: Mapping[str, Any]
) -> None:
    intent = _validate_settlement_intent(layout, intent)
    path = _settlement_intent_path(
        layout, _required_settlement_string(intent, "reservation_id")
    )
    if intent["phase"] == "committed":
        _resolve_budget_settlement_unlocked(
            layout, intent["settlement_id"]
        )
        return

    budget = _validated_approved_budget_value(
        _require_mapping(read_json(layout.budget), "budget")
    )
    if budget == intent["budget_before"]:
        atomic_write_json(layout.budget, intent["budget_after"])
        _invalidate_budget_schema_cache(layout)
    elif budget != intent["budget_after"]:
        raise ValueError(
            "pending budget settlement conflicts with current budget"
        )

    if intent["phase"] == "prepared":
        intent["phase"] = "budget_written"
        atomic_write_json(path, intent)

    matching = _matching_settlement_receipts(
        layout, intent["settlement_id"]
    )
    if not matching:
        append_jsonl_locked(layout.budget_settlements, intent["receipt"])
    elif len(matching) != 1 or matching[0] != intent["receipt"]:
        raise ValueError("budget settlement receipt evidence conflicts")

    if intent["phase"] == "budget_written":
        intent["phase"] = "receipt_appended"
        atomic_write_json(path, intent)
    if intent["phase"] == "receipt_appended":
        intent["phase"] = "committed"
        atomic_write_json(path, intent)


def _load_settlement_intent(
    layout: ProjectLayout, reservation_id: str
) -> JsonObject:
    return _load_settlement_intent_path(
        layout, _settlement_intent_path(layout, reservation_id)
    )


def _load_settlement_intent_path(
    layout: ProjectLayout, path: Path
) -> JsonObject:
    intent = _validate_settlement_intent(
        layout, _require_mapping(read_json(path), "budget settlement intent")
    )
    expected_path = _settlement_intent_path(
        layout, intent["reservation_id"]
    )
    if path.resolve() != expected_path.resolve():
        raise ValueError("budget settlement intent path conflicts")
    return intent


def _validate_settlement_intent(
    layout: ProjectLayout, intent: Mapping[str, Any]
) -> JsonObject:
    intent = _require_mapping(intent, "budget settlement intent")
    if set(intent) != SETTLEMENT_INTENT_FIELDS:
        raise ValueError("budget settlement intent has invalid fields")
    if (
        intent.get("schema_version") != 1
        or intent.get("record_type") != "budget-settlement-intent"
        or intent.get("phase") not in SETTLEMENT_PHASES
    ):
        raise ValueError("budget settlement intent is invalid")
    reservation_id = _required_settlement_string(
        intent, "reservation_id"
    )
    owner_transaction_id = _normalize_owner_transaction_id(
        intent.get("owner_transaction_id")
    )
    settlement_id = _required_settlement_string(intent, "settlement_id")
    if (
        SETTLEMENT_ID_PATTERN.fullmatch(settlement_id) is None
        or settlement_id != _budget_settlement_id(reservation_id)
    ):
        raise ValueError("budget settlement identity conflicts")
    reservation = _normalize_reservation(
        _require_mapping(intent.get("reservation"), "settlement reservation"),
        require_id=True,
    )
    actual = _normalize_reservation(
        _require_mapping(intent.get("actual"), "settlement actual"),
        require_id=False,
    )
    if reservation != intent["reservation"] or actual != intent["actual"]:
        raise ValueError("budget settlement normalized evidence conflicts")
    if reservation["id"] != reservation_id:
        raise ValueError("budget settlement reservation identity conflicts")
    budget_before = _validated_approved_budget_value(
        _require_mapping(
            intent.get("budget_before"), "settlement budget_before"
        )
    )
    budget_after = _validated_approved_budget_value(
        _require_mapping(
            intent.get("budget_after"), "settlement budget_after"
        )
    )
    expected_after = _updated_budget_after_settlement(
        budget_before, reservation_id, actual
    )
    if budget_after != expected_after:
        raise ValueError("budget settlement after evidence conflicts")
    receipt = _validate_settlement_receipt(
        _require_mapping(intent.get("receipt"), "settlement receipt")
    )
    if (
        receipt["settlement_id"] != settlement_id
        or receipt["reservation_id"] != reservation_id
        or receipt["reservation"] != reservation
        or receipt["actual"] != actual
    ):
        raise ValueError("budget settlement receipt evidence conflicts")
    intent["reservation"] = reservation
    intent["actual"] = actual
    intent["owner_transaction_id"] = owner_transaction_id
    intent["budget_before"] = budget_before
    intent["budget_after"] = budget_after
    intent["receipt"] = receipt
    return intent


def _validate_settlement_receipt(receipt: Mapping[str, Any]) -> JsonObject:
    receipt = _require_mapping(receipt, "budget settlement receipt")
    reservation_id = receipt.get("reservation_id")
    run_id = (
        _run_id_from_reservation(reservation_id)
        if isinstance(reservation_id, str)
        else None
    )
    expected_fields = (
        RUN_SETTLEMENT_RECEIPT_FIELDS
        if run_id is not None
        else SETTLEMENT_RECEIPT_FIELDS
    )
    if set(receipt) != expected_fields:
        raise ValueError("budget settlement receipt has invalid fields")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("record_type") != "budget-settlement"
    ):
        raise ValueError("budget settlement receipt is invalid")
    settlement_id = _required_settlement_string(receipt, "settlement_id")
    reservation_id = _required_settlement_string(receipt, "reservation_id")
    if run_id is not None and receipt.get("run_id") != run_id:
        raise ValueError("budget settlement run identity conflicts")
    if (
        SETTLEMENT_ID_PATTERN.fullmatch(settlement_id) is None
        or settlement_id != _budget_settlement_id(reservation_id)
    ):
        raise ValueError("budget settlement receipt identity conflicts")
    reservation = _normalize_reservation(
        _require_mapping(receipt.get("reservation"), "receipt reservation"),
        require_id=True,
    )
    actual = _normalize_reservation(
        _require_mapping(receipt.get("actual"), "receipt actual"),
        require_id=False,
    )
    if reservation != receipt["reservation"] or actual != receipt["actual"]:
        raise ValueError("budget settlement receipt normalized evidence conflicts")
    if reservation["id"] != reservation_id:
        raise ValueError("budget settlement receipt reservation conflicts")
    _required_settlement_string(receipt, "settled_at")
    receipt["reservation"] = reservation
    receipt["actual"] = actual
    return receipt


def _run_id_from_reservation(reservation_id: str) -> str | None:
    prefix = "run:"
    if not reservation_id.startswith(prefix):
        return None
    run_id = reservation_id[len(prefix) :]
    if not run_id:
        raise ValueError("run reservation must contain a run id")
    return run_id


def _resolve_budget_settlement_unlocked(
    layout: ProjectLayout, settlement_id: str
) -> JsonObject:
    if (
        not isinstance(settlement_id, str)
        or SETTLEMENT_ID_PATTERN.fullmatch(settlement_id) is None
    ):
        raise ValueError("invalid budget settlement id")
    reservation_id = _reservation_id_for_settlement(
        layout, settlement_id
    )
    intent = _load_settlement_intent(layout, reservation_id)
    if (
        intent["settlement_id"] != settlement_id
        or intent["phase"] != "committed"
    ):
        raise ValueError("budget settlement is not committed")
    matching = _matching_settlement_receipts(layout, settlement_id)
    if len(matching) != 1 or matching[0] != intent["receipt"]:
        raise ValueError("budget settlement receipt evidence conflicts")
    return {
        "settlement_id": settlement_id,
        "receipt": intent["receipt"],
    }


def _reservation_id_for_settlement(
    layout: ProjectLayout, settlement_id: str
) -> str:
    matches: list[str] = []
    for path in sorted(layout.budget_transactions.glob("*.json")):
        intent = _load_settlement_intent_path(layout, path)
        if intent["settlement_id"] == settlement_id:
            matches.append(intent["reservation_id"])
    if len(matches) != 1:
        raise ValueError("unknown or ambiguous budget settlement")
    return matches[0]


def _matching_settlement_receipts(
    layout: ProjectLayout, settlement_id: str
) -> list[JsonObject]:
    matching: list[JsonObject] = []
    for value in read_jsonl(layout.budget_settlements):
        receipt = _validate_settlement_receipt(value)
        if receipt["settlement_id"] == settlement_id:
            matching.append(receipt)
    return matching


def _settlement_plan(intent: Mapping[str, Any]) -> JsonObject:
    return {
        "settlement_id": intent["settlement_id"],
        "receipt": _json_copy(intent["receipt"], "settlement receipt"),
    }


def _require_same_settlement_actual(
    intent: Mapping[str, Any], actual: Mapping[str, Any]
) -> None:
    if intent["actual"] != actual:
        raise ValueError(
            "reservation was already settled with different actual usage"
        )


def _require_same_settlement_owner(
    intent: Mapping[str, Any], owner_transaction_id: str | None
) -> None:
    expected_owner = _normalize_owner_transaction_id(owner_transaction_id)
    if intent["owner_transaction_id"] != expected_owner:
        raise ValueError("budget settlement owner transaction conflicts")


def _normalize_owner_transaction_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(
            "owner_transaction_id must be null or a non-empty string"
        )
    return value


def _budget_settlement_id(reservation_id: str) -> str:
    return "budget-settlement-" + _hash_payload(
        {
            "record_type": "budget-settlement-identity",
            "reservation_id": reservation_id,
        }
    )


def _settlement_intent_path(
    layout: ProjectLayout, reservation_id: str
) -> Path:
    return (
        layout.budget_transactions
        / f"{_budget_settlement_id(reservation_id)}.json"
    )


def _normalize_settled_at(value: str | None) -> str:
    if value is None:
        return _timestamp()
    if not isinstance(value, str) or not value:
        raise ValueError("settled_at must be a non-empty string")
    return value


def _required_settlement_string(
    value: Mapping[str, Any], field: str
) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"budget settlement {field} must be a non-empty string")
    return result


def _validated_approved_budget_value(
    value: Mapping[str, Any]
) -> JsonObject:
    budget = _json_copy(value, "budget")
    unknown = set(budget) - CURRENT_BUDGET_FIELDS
    if unknown:
        raise ValueError(f"budget has unknown field: {sorted(unknown)[0]}")
    if budget.get("status") != "approved":
        raise PermissionError("budget approval required")
    payload = _normalize_active_budget_payload(budget)
    budget["schema_version"] = payload["schema_version"]
    budget["limits"] = payload["limits"]
    budget["storage_accounting"] = payload["storage_accounting"]
    budget["reserved"] = _normalize_counters(
        _require_mapping(budget.get("reserved"), "reserved counters"),
        "reserved",
    )
    budget["consumed"] = _normalize_counters(
        _require_mapping(budget.get("consumed"), "consumed counters"),
        "consumed",
    )
    reservations = _require_mapping(
        budget.get("reservations"), "budget reservations"
    )
    normalized_reservations: JsonObject = {}
    for reservation_id, value in reservations.items():
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ValueError("budget reservation id must be a non-empty string")
        reservation = _normalize_reservation(
            _require_mapping(value, "budget reservation"),
            require_id=True,
        )
        if reservation["id"] != reservation_id:
            raise ValueError("budget reservation identity is inconsistent")
        normalized_reservations[reservation_id] = reservation
    budget["reservations"] = normalized_reservations
    migration = budget.get("migration")
    if migration is not None:
        receipt = _require_mapping(migration, "budget migration receipt")
        if (
            receipt.get("record_type") != "budget-schema-migration"
            or receipt.get("from_schema_version") not in {0, 1}
            or receipt.get("to_schema_version") != 1
        ):
            raise ValueError("budget migration receipt is invalid")
    return budget


def _activate_budget(
    layout: ProjectLayout,
    gate_id: str,
    payload: Mapping[str, Any],
) -> None:
    normalized_payload = _normalize_budget_payload(payload)
    limits = normalized_payload["limits"]
    with _exclusive_lock(_budget_lock_path(layout)):
        _resume_pending_settlement_unlocked(layout)
        current = _require_mapping(read_json(layout.budget), "budget")
        if current.get("status") == "approved":
            current = _validated_approved_budget_value(current)
            reserved = dict(
                _require_mapping(current.get("reserved"), "reserved counters")
            )
            consumed = dict(
                _require_mapping(current.get("consumed"), "consumed counters")
            )
            reservations = dict(
                _require_mapping(current.get("reservations"), "reservations")
            )
            for field in ADDITIVE_BUDGET_FIELDS:
                if not budget_cap_allows(
                    limits[field],
                    reserved.get(field, 0),
                    consumed.get(field, 0),
                ):
                    raise ValueError(
                        f"approved {field} limit is below tracked usage"
                    )
            active_slots = sum(
                _require_mapping(item, "active reservation").get(
                    "loop-subagent-bound", 0
                )
                for item in reservations.values()
            )
            if active_slots > limits["loop-subagent-bound"]:
                raise ValueError(
                    "approved loop-subagent-bound is below active reservations"
                )
            active_gpu_types = {
                _require_mapping(item, "active reservation").get("gpu_type")
                for item in reservations.values()
                if "gpu_type" in item
            }
            if active_gpu_types - {limits["gpu_type"]}:
                raise ValueError("approved gpu_type conflicts with active reservation")
        else:
            reserved = _zero_counters()
            consumed = _zero_counters()
            reservations = {}
        atomic_write_json(
            layout.budget,
            {
                "status": "approved",
                "gate_id": gate_id,
                "schema_version": normalized_payload["schema_version"],
                "limits": limits,
                "storage_accounting": normalized_payload[
                    "storage_accounting"
                ],
                "reserved": reserved,
                "reservations": reservations,
                "consumed": consumed,
            },
        )
        _invalidate_budget_schema_cache(layout)


def _load_approved_budget(layout: ProjectLayout) -> JsonObject:
    return _validated_approved_budget_value(
        _require_mapping(read_json(layout.budget), "budget")
    )


def _latest_approved_by_type(
    records: list[JsonObject],
) -> dict[str, JsonObject]:
    latest_by_id: dict[str, tuple[int, JsonObject]] = {}
    for index, record in enumerate(records):
        gate_id = record.get("gate_id")
        if isinstance(gate_id, str):
            latest_by_id[gate_id] = (index, record)
    approved: dict[str, JsonObject] = {}
    for _, record in sorted(latest_by_id.values()):
        gate_type = record.get("gate_type")
        if record.get("status") == "approved" and gate_type in GATE_TYPES:
            approved[gate_type] = record
    return approved


def _normalize_gate_payload(
    layout: ProjectLayout, gate_type: str, payload: Mapping[str, Any]
) -> JsonObject:
    payload = _require_mapping(payload, "gate payload")
    if gate_type == "modification":
        return _normalize_modification_payload(layout, payload)
    if gate_type == "budget":
        return _normalize_budget_payload(payload)
    if gate_type == "graph":
        return _normalize_graph_revision_payload(payload)
    if gate_type == "publication":
        return _normalize_publication_payload(payload)
    if gate_type == "evaluation":
        if set(payload) == EVALUATION_GATE_ENVELOPE_FIELDS:
            return _normalize_evaluation_gate_payload(payload)
        # Legacy direct gates (payload IS the full EvaluationSpec) remain
        # readable for recovery; new callers should use the split envelope.
        return _json_copy(payload, "gate payload")
    return _json_copy(payload, "gate payload")


def _normalize_coordinator_gate_payload(
    payload: Mapping[str, Any], gate_type: str
) -> JsonObject:
    if gate_type == "evaluation":
        return _normalize_evaluation_gate_payload(payload)
    return _normalize_stage_gate(payload)


def _normalize_stage_gate(stage_gate: Mapping[str, Any]) -> JsonObject:
    normalized = _json_copy(stage_gate, "stage gate")
    if set(normalized) != COORDINATOR_GATE_PAYLOAD_FIELDS:
        raise ValueError("stage gate payload schema is invalid")
    if not isinstance(normalized["stage_id"], str) or not normalized["stage_id"]:
        raise ValueError("stage gate stage_id must be a non-empty string")
    revision = normalized["source_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("stage gate source_revision must be a non-negative integer")
    for field in ("source_state_hash", "graph_hash"):
        value = normalized[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"stage gate {field} must be a SHA-256")
    artifacts = normalized["output_artifacts"]
    if not isinstance(artifacts, list):
        raise ValueError("stage gate output_artifacts must be a list")
    artifact_ids: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"artifact_id", "sha256"}:
            raise ValueError("stage gate output artifact binding is invalid")
        artifact_id = artifact["artifact_id"]
        digest = artifact["sha256"]
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("stage gate output artifact_id must be a non-empty string")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("stage gate output sha256 must be a SHA-256")
        if artifact_id in artifact_ids:
            raise ValueError("stage gate output artifact IDs must be unique")
        artifact_ids.add(artifact_id)
    normalized["output_artifacts"] = sorted(
        artifacts, key=lambda artifact: artifact["artifact_id"]
    )
    return normalized


def _normalize_evaluation_gate_payload(
    payload: Mapping[str, Any],
) -> JsonObject:
    """Validate and normalize the split v1 evaluation gate envelope.

    The envelope binds the stage evidence (stage_gate) to the static contract
    hash (evaluation_spec.payload_hash) so they cannot drift independently.
    Legacy direct gates remain readable for recovery via ``_approved_spec_hash``
    in ``evaluation.spec``, but new callers must use this envelope form.
    """
    normalized = _json_copy(payload, "evaluation gate envelope")
    if set(normalized) != EVALUATION_GATE_ENVELOPE_FIELDS:
        raise ValueError("evaluation gate envelope schema is invalid")
    if normalized["schema_version"] != 1:
        raise ValueError("evaluation gate envelope schema_version must equal 1")
    stage_gate = _normalize_stage_gate(
        _require_mapping(normalized["stage_gate"], "evaluation stage gate")
    )
    anchor = _json_copy(
        _require_mapping(normalized["evaluation_spec"], "evaluation spec anchor"),
        "evaluation spec anchor",
    )
    if set(anchor) != EVALUATION_SPEC_ANCHOR_FIELDS:
        raise ValueError("evaluation spec anchor is invalid")
    if (
        not isinstance(anchor["evaluation_spec_id"], str)
        or not anchor["evaluation_spec_id"]
    ):
        raise ValueError(
            "evaluation_spec.evaluation_spec_id must be a non-empty string"
        )
    if (
        not isinstance(anchor["payload_hash"], str)
        or re.fullmatch(r"[0-9a-f]{64}", anchor["payload_hash"]) is None
    ):
        raise ValueError("evaluation_spec.payload_hash must be a SHA-256")
    return {
        "schema_version": 1,
        "stage_gate": stage_gate,
        "evaluation_spec": anchor,
    }


def build_evaluation_gate_envelope(
    *,
    stage_id: str,
    source_revision: int,
    source_state_hash: str,
    graph_hash: str,
    output_artifacts: Sequence[Mapping[str, Any]],
    evaluation_spec_id: str,
    payload_hash: str,
) -> JsonObject:
    """Construct a split v1 evaluation gate envelope from stage evidence and a
    static EvaluationSpec payload hash. New callers should use this helper so
    stage evidence and the contract hash cannot drift.
    """
    return {
        "schema_version": 1,
        "stage_gate": {
            "stage_id": stage_id,
            "source_revision": source_revision,
            "source_state_hash": source_state_hash,
            "graph_hash": graph_hash,
            "output_artifacts": [dict(artifact) for artifact in output_artifacts],
        },
        "evaluation_spec": {
            "evaluation_spec_id": evaluation_spec_id,
            "payload_hash": payload_hash,
        },
    }


def _normalize_publication_payload(
    payload: Mapping[str, Any],
) -> JsonObject:
    normalized = _json_copy(payload, "publication payload")
    if "sensitive" in normalized:
        _require_bool(normalized["sensitive"], "sensitive")
    return normalized


def _normalize_graph_revision_payload(
    payload: Mapping[str, Any],
) -> JsonObject:
    normalized = _json_copy(payload, "graph revision payload")
    if set(normalized) != GRAPH_REVISION_FIELDS:
        raise ValueError("graph revision payload schema is invalid")
    if normalized["schema_version"] != 1:
        raise ValueError("graph revision schema_version must be integer 1")
    for field in ("base_graph_hash", "target_graph_hash"):
        value = normalized[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"graph revision {field} must be a SHA-256")
    revision = normalized["state_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("graph revision state_revision must be a non-negative integer")
    if not isinstance(normalized["reason"], str) or not normalized["reason"]:
        raise ValueError("graph revision reason must be a non-empty string")
    return normalized


def _normalize_modification_payload(
    layout: ProjectLayout, payload: Mapping[str, Any]
) -> JsonObject:
    missing = MODIFICATION_FIELDS - payload.keys()
    unknown = payload.keys() - MODIFICATION_FIELDS
    if missing:
        raise ValueError(
            f"modification envelope missing required field: {sorted(missing)[0]}"
        )
    if unknown:
        raise ValueError(
            f"modification envelope has unknown field: {sorted(unknown)[0]}"
        )

    normalized = _json_copy(payload, "modification envelope")
    for field in ("editable_paths", "append_only_paths", "read_only_paths"):
        paths = _string_list(normalized[field], field)
        normalized[field] = [
            str(
                (
                    Path(path)
                    if Path(path).is_absolute()
                    else layout.root / path
                ).resolve()
            )
            for path in paths
        ]
    for field in (
        "allowed_actions",
        "allowed_commands",
        "approval_required_actions",
    ):
        normalized[field] = _string_list(normalized[field], field)

    for parent, required_fields in NESTED_MODIFICATION_FIELDS.items():
        nested = _require_mapping(normalized[parent], parent)
        missing_nested = required_fields - nested.keys()
        unknown_nested = nested.keys() - required_fields
        if missing_nested:
            raise ValueError(
                f"{parent} missing required field: {sorted(missing_nested)[0]}"
            )
        if unknown_nested:
            raise ValueError(
                f"{parent} has unknown field: {sorted(unknown_nested)[0]}"
            )
        normalized[parent] = dict(nested)

    dependency_policy = normalized["dependency_policy"]
    dependency_policy["allowed"] = _string_list(
        dependency_policy["allowed"], "dependency_policy.allowed"
    )
    _require_bool(
        dependency_policy["undeclared_requires_gate"],
        "dependency_policy.undeclared_requires_gate",
    )
    model_policy = normalized["model_policy"]
    model_policy["allowed_families"] = _string_list(
        model_policy["allowed_families"], "model_policy.allowed_families"
    )
    _require_bool(
        model_policy["external_service_change_requires_gate"],
        "model_policy.external_service_change_requires_gate",
    )
    network = normalized["network"]
    if not isinstance(network["mode"], str) or not network["mode"]:
        raise ValueError("network.mode must be a non-empty string")
    network["allowed_services"] = _string_list(
        network["allowed_services"], "network.allowed_services"
    )
    return normalized


def _normalize_budget_limits(limits: Mapping[str, Any]) -> JsonObject:
    missing = BUDGET_LIMIT_FIELDS - limits.keys()
    unknown = limits.keys() - BUDGET_LIMIT_FIELDS
    if missing:
        raise ValueError(f"budget limits missing required field: {sorted(missing)[0]}")
    if unknown:
        raise ValueError(f"budget limits has unknown field: {sorted(unknown)[0]}")

    normalized: JsonObject = {}
    for field in ADDITIVE_BUDGET_FIELDS:
        value = limits[field]
        if value is None:
            normalized[field] = None
            continue
        normalized[field] = _normalize_additive_amount(value, field)
    for field in QUOTA_BUDGET_FIELDS:
        normalized[field] = _nonnegative_integer(limits[field], field)
    gpu_type = limits["gpu_type"]
    if gpu_type is not None and (
        not isinstance(gpu_type, str) or not gpu_type
    ):
        raise ValueError("gpu_type must be null or a non-empty string")
    normalized["gpu_type"] = gpu_type
    slots = limits["loop-subagent-bound"]
    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 0:
        raise ValueError("loop-subagent-bound must be a non-negative integer")
    normalized["loop-subagent-bound"] = slots
    return normalized


def _normalize_budget_payload(payload: Mapping[str, Any]) -> JsonObject:
    payload = _require_mapping(payload, "budget payload")
    missing = BUDGET_PAYLOAD_FIELDS - payload.keys()
    unknown = payload.keys() - BUDGET_PAYLOAD_FIELDS
    if missing:
        raise ValueError(
            f"budget payload missing required field: {sorted(missing)[0]}"
        )
    if unknown:
        raise ValueError(
            f"budget payload has unknown field: {sorted(unknown)[0]}"
        )
    schema_version = payload["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise ValueError("budget schema_version must equal 1")
    return {
        "schema_version": 1,
        "limits": _normalize_budget_limits(
            _require_mapping(payload["limits"], "budget limits")
        ),
        "storage_accounting": _normalize_storage_accounting(
            _require_mapping(
                payload["storage_accounting"], "storage_accounting"
            )
        ),
    }


def _normalize_active_budget_payload(
    budget: Mapping[str, Any],
) -> JsonObject:
    return _normalize_budget_payload(
        {
            "schema_version": budget.get("schema_version"),
            "limits": budget.get("limits"),
            "storage_accounting": budget.get("storage_accounting"),
        }
    )


def _normalize_storage_accounting(
    policy: Mapping[str, Any],
) -> JsonObject:
    missing = STORAGE_ACCOUNTING_FIELDS - policy.keys()
    unknown = policy.keys() - STORAGE_ACCOUNTING_FIELDS
    if missing:
        raise ValueError(
            "storage_accounting missing required field: "
            f"{sorted(missing)[0]}"
        )
    if unknown:
        raise ValueError(
            "storage_accounting has unknown field: "
            f"{sorted(unknown)[0]}"
        )
    if policy["count_snapshot_copy_once"] is not True:
        raise ValueError(
            "storage_accounting.count_snapshot_copy_once must be true"
        )
    excluded = policy["excluded_artifact_types"]
    if (
        not isinstance(excluded, list)
        or tuple(excluded) != STORAGE_EXCLUDED_ARTIFACT_TYPES
    ):
        raise ValueError(
            "storage_accounting.excluded_artifact_types must equal "
            "['model-weight']"
        )
    return {
        "count_snapshot_copy_once": True,
        "excluded_artifact_types": list(
            STORAGE_EXCLUDED_ARTIFACT_TYPES
        ),
    }


def _normalize_reservation(
    reservation: Mapping[str, Any], *, require_id: bool
) -> JsonObject:
    reservation = _require_mapping(reservation, "reservation")
    allowed = (
        RESERVATION_BUDGET_FIELDS | {"id"}
        if require_id
        else RESERVATION_BUDGET_FIELDS
    )
    unknown = reservation.keys() - allowed
    if unknown:
        raise ValueError(f"reservation has unknown field: {sorted(unknown)[0]}")
    normalized: JsonObject = {}
    if require_id:
        reservation_id = reservation.get("id")
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ValueError("reservation id must be a non-empty string")
        normalized["id"] = reservation_id
    for field in ADDITIVE_BUDGET_FIELDS:
        if field in reservation:
            normalized[field] = _normalize_additive_amount(
                reservation[field], field
            )
    if "gpu_type" in reservation:
        gpu_type = reservation["gpu_type"]
        if gpu_type is not None and (
            not isinstance(gpu_type, str) or not gpu_type
        ):
            raise ValueError("gpu_type must be null or a non-empty string")
        normalized["gpu_type"] = gpu_type
    if "loop-subagent-bound" in reservation:
        slots = reservation["loop-subagent-bound"]
        if isinstance(slots, bool) or not isinstance(slots, int) or slots < 0:
            raise ValueError(
                "loop-subagent-bound must be a non-negative integer"
            )
        normalized["loop-subagent-bound"] = slots
    return normalized


def _nonnegative_number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a non-negative finite number")
    return value


def _normalize_additive_amount(value: Any, field: str) -> int | float:
    normalized = _nonnegative_number(value, field)
    if field == "storage_bytes" and not isinstance(
        normalized, int
    ):
        raise ValueError(f"{field} must be an integer")
    return normalized


def _zero_counters() -> JsonObject:
    return {
        field: 0
        for field in ADDITIVE_BUDGET_FIELDS
    }


def budget_cap_remaining(
    limit: Any, *amounts: Any
) -> int | float | None:
    if limit is not None:
        limit = _nonnegative_number(limit, "budget limit")
    total: int | float = 0
    for amount in amounts:
        total += _nonnegative_number(amount, "budget usage")
        if not math.isfinite(total):
            raise ValueError("budget usage total must be finite")
    if limit is None:
        return None
    return _subtract_exact(limit, total)


def budget_cap_allows(limit: Any, *amounts: Any) -> bool:
    remaining = budget_cap_remaining(limit, *amounts)
    return remaining is None or remaining >= 0


def budget_cap_exhausted(remaining: Any) -> bool:
    if remaining is None:
        return False
    if (
        isinstance(remaining, bool)
        or not isinstance(remaining, Real)
        or not math.isfinite(remaining)
    ):
        raise ValueError("remaining budget must be a finite number or null")
    return remaining <= 0


def remaining_budget(budget: Mapping[str, Any]) -> JsonObject:
    explicit = budget.get("remaining")
    if isinstance(explicit, Mapping):
        return dict(explicit)
    if budget.get("status") == "unapproved":
        return {}
    limits = _require_mapping(budget.get("limits"), "budget limits")
    reserved = _require_mapping(budget.get("reserved"), "reserved counters")
    consumed = _require_mapping(budget.get("consumed"), "consumed counters")
    return {
        field: budget_cap_remaining(
            limits[field],
            reserved.get(field, 0),
            consumed.get(field, 0),
        )
        for field in ADDITIVE_BUDGET_FIELDS
        if field in limits
    }


def _normalize_counters(
    counters: Mapping[str, Any], name: str
) -> JsonObject:
    missing = set(ADDITIVE_BUDGET_FIELDS) - counters.keys()
    unknown = counters.keys() - set(ADDITIVE_BUDGET_FIELDS)
    if missing:
        raise ValueError(f"{name} counters missing field: {sorted(missing)[0]}")
    if unknown:
        raise ValueError(f"{name} counters has unknown field: {sorted(unknown)[0]}")
    normalized: JsonObject = {}
    for field in ADDITIVE_BUDGET_FIELDS:
        try:
            normalized[field] = _normalize_additive_amount(
                counters[field], field
            )
        except ValueError as error:
            raise ValueError(f"{name} {error}") from error
    return normalized


def _nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _subtract_exact(total: Any, amount: Any) -> int | float:
    difference = total - amount
    if isinstance(difference, float) and abs(difference) < 1e-12:
        return 0.0
    return difference


def _hash_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_copy(value: Mapping[str, Any], name: str) -> JsonObject:
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        copied = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain JSON values") from error
    return _require_mapping(copied, name)


def _require_mapping(value: Any, name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{name} must be a list of non-empty strings")
    return list(value)


def _require_bool(value: Any, name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _gate_payload_path(layout: ProjectLayout, gate_id: str) -> Path:
    return layout.root / ".research" / "gate-payloads" / f"{gate_id}.json"


def _modification_envelope_path(layout: ProjectLayout) -> Path:
    return layout.root / ".research" / "modification-envelope.json"


def _gate_lock_path(layout: ProjectLayout) -> Path:
    return layout.root / ".research" / "gates.lock"


def _budget_lock_path(layout: ProjectLayout) -> Path:
    return layout.root / ".research" / "budget.lock"


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
