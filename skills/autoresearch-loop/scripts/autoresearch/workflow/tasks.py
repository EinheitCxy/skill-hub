from __future__ import annotations
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from numbers import Real
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any
from uuid import uuid4
import yaml
from autoresearch.artifacts import (
    resolve_artifact,
    verify_artifact,
)
from autoresearch.contracts import JsonObject
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
    validate_research_state_document,
)
from autoresearch.worker_events import latest_task_event
from autoresearch.runtime_navigation import validate_runtime_context

from .recovery import (
    HEADING_PATTERN,
    _DuplicateYamlKeyError,
    _UniqueKeySafeLoader,
    _atomic_copy,
    _atomic_json_hash,
    _atomic_write_text,
    _capture_graph,
    _exclusive_lock,
    _hash_payload,
    _json_copy,
    _nonnegative_number,
    _reject_other_pending_intents,
    _require_exact_fields,
    _require_nonnegative_integer,
    _require_sha256,
    _required_string,
    _safe_id,
    _state_integrity_error,
    _string_list,
    _timestamp,
    _validate_intent_header,
    _workflow_lock_path,
    _workflow_transactions_root,
)

TASK_SPEC_FIELDS = frozenset(
    {
        "cycle_id",
        "task_id",
        "candidate_id",
        "stage_id",
        "primary_skill",
        "capability_skills",
        "input_artifact_ids",
        "budget_slice",
        "editable_paths",
        "output_path",
        "stop_conditions",
        "work_type",
    }
)


# Sub-agent model tiers keyed by ``work_type``. The values are tier aliases,
# not provider model names: luna=haiku, sol=sonnet, terra=opus. ``thinking``
# shares the opus tier (``terra``) with ``execution`` so deep-reasoning workers
# run on the same heavy model instead of the sonnet tier.
SUBAGENT_MODELS = {
    "simple": "luna",
    "planning": "sol",
    "thinking": "terra",
    "execution": "terra",
}


REPORT_FIELDS = frozenset(
    {
        "task_id",
        "candidate_id",
        "status",
        "artifact_ids",
        "decision_recommendation",
    }
)


REPORT_STATUSES = frozenset(
    {"completed", "blocked", "needs_user", "needs_review", "failed"}
)


REPORT_HEADINGS = (
    "Summary",
    "Evidence",
    "Ideas Considered",
    "Rejected Ideas",
    "Risks and Confounds",
    "Recommended Next Action",
    "Artifact Links",
)
DECISION_ADDENDUM_HEADING_PATTERN = re.compile(
    r"Decision Addendum — (?P<date>\d{4}-\d{2}-\d{2})"
)


CONTEXT_ARTIFACT_FIELDS = (
    "artifact_id",
    "path",
    "type",
    "sha256",
    "size_bytes",
    "availability",
)


COMPLETION_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "transaction_id",
        "phase",
        "cycle_id",
        "task_id",
        "candidate_id",
        "stage_id",
        "task_record_path",
        "task_record_hash_before",
        "task_before",
        "report_path",
        "report_sha256",
        "reported_status",
        "result_status",
        "referenced_artifact_ids",
        "artifact_ids",
        "decision_recommendation",
        "usage",
        "actual_usage",
        "reservation_id",
        "settlement",
        "settlement_id",
        "report_artifact_id",
        "report_artifact",
        "receipt_id",
        "completed_at",
        "receipt",
        "task_after",
        "result",
    }
)


COMPLETION_RECEIPT_FIELDS = frozenset(
    {
        "record_type",
        "transaction_id",
        "receipt_id",
        "completed_at",
        "cycle_id",
        "task_id",
        "candidate_id",
        "stage_id",
        "status",
        "reported_status",
        "artifact_ids",
        "report_artifact_id",
        "decision_recommendation",
        "reservation_id",
        "reservation_settled",
        "settlement_id",
        "usage",
    }
)


COMPLETION_RESULT_FIELDS = frozenset(
    {"status", "task_id", "report_artifact_id", "usage", "receipt"}
)


TASK_REPORT_ARTIFACT_FIELDS = frozenset(
    {
        "artifact_id",
        "path",
        "storage",
        "size_bytes",
        "sha256",
        "type",
        "producer",
        "subagent",
        "research_id",
        "cycle_id",
        "task_id",
        "candidate_id",
        "run_id",
        "report_id",
        "provenance_tags",
        "comparability_tags",
        "parent_artifact_id",
        "availability",
        "registered_at",
        "unavailable_reason",
    }
)


def open_task(
    layout: ProjectLayout, task_spec: Mapping[str, Any]
) -> JsonObject:
    normalized, task_dir, report_path = _normalize_task_spec(layout, task_spec)
    reserved_task_id = (
        f"task:{normalized['cycle_id']}:{normalized['task_id']}"
    )
    brief_path = task_dir / "brief.md"
    task_record_path = task_dir / "task.json"

    with _exclusive_lock(_workflow_lock_path(layout)):
        _reject_other_pending_intents(layout, None)
        _validate_editable_envelope(layout, normalized["editable_paths"])
        state = load_research_state(layout)
        state_hash = sha256_file(layout.state)
        graph, graph_hash = _capture_graph(layout)
        integrity_error = _state_integrity_error(
            layout, state, state_hash, graph_hash
        )
        if integrity_error is not None:
            raise ValueError(integrity_error)
        context_manifest = _context_manifest(
            layout, normalized["input_artifact_ids"]
        )
        stage = _validate_task_stage_and_limits(
            layout,
            graph,
            state,
            normalized,
            context_manifest,
        )
        task_owner = stage["task_owner"]
        control_plane = stage.get("control_plane", False)
        if task_owner == "coordinator" and control_plane:
            reservation_id: str | None = None
        else:
            require_execution_approval(layout)
            reservation_id = reserved_task_id
        subagent_model = (
            SUBAGENT_MODELS[normalized["work_type"]]
            if task_owner == "subagent"
            else None
        )
        reservation = (
            {
                "id": reservation_id,
                **normalized["budget_slice"],
            }
            if reservation_id is not None
            else None
        )
        if reservation is not None and task_owner == "subagent":
            reservation["loop-subagent-bound"] = 1
        record: JsonObject = {
            **normalized,
            "research_id": _required_string(state, "research_id"),
            "task_spec": normalized,
            "task_owner": task_owner,
            "subagent_model": subagent_model,
            "brief_path": str(brief_path.resolve()),
            "report_path": str(report_path.resolve()),
            "task_record_path": str(task_record_path.resolve()),
            "reservation_id": reservation_id,
            "context_manifest": context_manifest,
            "status": "open",
            "report_artifact_id": None,
            "artifact_ids": [],
            "opened_at": _timestamp(),
            "completed_at": None,
        }
        brief = _render_brief(record)
        _reject_existing_task(task_dir)
        if task_owner == "subagent":
            _validate_active_task_limit(layout, graph)
        if reservation is not None:
            reserve_budget(layout, reservation)
        try:
            task_dir.mkdir(parents=True)
            _atomic_write_text(brief_path, brief)
            atomic_write_json(task_record_path, record)
        except BaseException:
            _remove_partial_task(task_dir, layout.cycles)
            if reservation_id is not None:
                try:
                    settle_budget(layout, reservation_id, {})
                except Exception as rollback_error:
                    raise RuntimeError(
                        "task creation failed and reservation rollback failed"
                    ) from rollback_error
            raise
    return record


def validate_report(path: Path) -> list[str]:
    _, _, errors = _parse_report(path)
    return errors


def finalize_task_report(
    layout: ProjectLayout, task_id: str, artifact_ids: list[str]
) -> JsonObject:
    task_id = _safe_id(task_id, "task_id")
    normalized_artifact_ids = _string_list(artifact_ids, "artifact_ids")
    if len(set(normalized_artifact_ids)) != len(normalized_artifact_ids):
        raise ValueError("artifact_ids must not contain duplicates")
    with _exclusive_lock(_workflow_lock_path(layout)):
        task_record_path, task = _find_task(layout, task_id)
        if task.get("status") != "open":
            raise ValueError(f"task is not open: {task_id}")
        _reject_other_pending_intents(layout, None)
        _require_subagent_report_ready(layout, task)
        report_path = Path(_required_string(task, "report_path"))
        _reject_symlink_components(
            task_record_path.parent, report_path, "report path"
        )
        try:
            report_snapshot = report_path.read_bytes()
        except OSError as error:
            raise ValueError(f"could not read report: {error}") from error
        try:
            report_text = report_snapshot.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("could not read report: invalid UTF-8") from error
        report_sha256_before = hashlib.sha256(report_snapshot).hexdigest()
        frontmatter, _, errors = _parse_report_text(report_text)
        if errors:
            raise ValueError(f"invalid task report: {'; '.join(errors)}")
        if frontmatter is None:
            raise ValueError("invalid task report")
        for field in ("task_id", "candidate_id"):
            if frontmatter[field] != task[field]:
                raise ValueError(f"report {field} does not match task record")

        report_artifact_ids = list(frontmatter["artifact_ids"])
        combined_artifact_ids = report_artifact_ids + [
            artifact_id
            for artifact_id in normalized_artifact_ids
            if artifact_id not in report_artifact_ids
        ]
        artifacts = _validated_report_output_artifacts(
            layout, task, combined_artifact_ids
        )
        _validate_declared_stage_outputs(layout, task, artifacts)
        if report_artifact_ids != combined_artifact_ids:
            if sha256_file(report_path) != report_sha256_before:
                raise ValueError("report changed during finalization; retry")
            _atomic_write_text(
                report_path,
                _render_finalized_report(
                    report_text, frontmatter, combined_artifact_ids
                ),
            )
        report_sha256_after = sha256_file(report_path)
        result: JsonObject = {
            "status": "finalized",
            "task_id": task_id,
            "report_path": str(report_path.resolve()),
            "artifact_ids": combined_artifact_ids,
            "report_sha256_before": report_sha256_before,
            "report_sha256_after": report_sha256_after,
        }
        return result


def complete_task(
    layout: ProjectLayout, task_id: str, report_path: Path
) -> JsonObject:
    task_id = _safe_id(task_id, "task_id")
    with _exclusive_lock(_workflow_lock_path(layout)):
        task_record_path, task = _find_task(layout, task_id)
        expected_report = Path(_required_string(task, "report_path"))
        supplied_report = Path(report_path)
        if supplied_report.resolve() != expected_report.resolve():
            raise ValueError(
                "report path does not match the task output_path"
            )
        task_dir = task_record_path.parent
        _reject_symlink_components(task_dir, supplied_report, "report path")
        intent_path = _completion_intent_path(layout, task)
        if intent_path.is_file():
            intent = read_json(intent_path)
            _validate_completion_intent_identity(
                layout,
                intent,
                task,
                task_record_path,
                supplied_report,
            )
        else:
            if task.get("status") != "open":
                raise ValueError(
                    f"task is not open and has no completion intent: {task_id}"
                )
            _reject_other_pending_intents(layout, None)
            intent = _prepare_completion_intent(
                layout,
                task,
                task_record_path,
                supplied_report,
            )
            atomic_write_json(intent_path, intent)
            _validate_completion_intent_identity(
                layout,
                intent,
                task,
                task_record_path,
                supplied_report,
            )
        _reject_other_pending_intents(
            layout, intent["transaction_id"]
        )
        return _resume_completion(
            layout, intent_path, intent, supplied_report
        )


def _prepare_completion_intent(
    layout: ProjectLayout,
    task: Mapping[str, Any],
    task_record_path: Path,
    report_path: Path,
) -> JsonObject:
    frontmatter, _, errors = _parse_report(report_path)
    if errors:
        raise ValueError(f"invalid task report: {'; '.join(errors)}")
    if frontmatter is None:
        raise ValueError("invalid task report")
    for field, expected in (
        ("task_id", task["task_id"]),
        ("candidate_id", task["candidate_id"]),
    ):
        if frontmatter.get(field) != expected:
            raise ValueError(f"report {field} does not match task record")

    referenced_artifact_ids = list(frontmatter["artifact_ids"])
    if task["task_owner"] == "subagent":
        referenced_artifacts = _validated_report_output_artifacts(
            layout, task, referenced_artifact_ids
        )
    else:
        referenced_artifacts = []
        for artifact_id in referenced_artifact_ids:
            artifact = resolve_artifact(layout, artifact_id)
            verification = verify_artifact(layout, artifact_id)
            if (
                verification.get("available") is not True
                or verification.get("valid") is not True
            ):
                raise ValueError(
                    f"report references invalid artifact: {artifact_id}"
                )
            referenced_artifacts.append(artifact)
    _validate_declared_stage_outputs(layout, task, referenced_artifacts)
    usage = _aggregate_task_usage(layout, task)
    has_budget_reservation = task["reservation_id"] is not None
    actual_usage = (
        {
            "wall_seconds": usage["duration_ms"] / 1000,
        }
        if has_budget_reservation
        else None
    )
    if actual_usage is not None:
        _validate_task_usage_within_slice(task, actual_usage)
    result_status = frontmatter["status"]
    transaction_id = _completion_transaction_id(task)
    completed_at = _timestamp()
    settlement_plan = (
        plan_budget_settlement(
            layout,
            task["reservation_id"],
            actual_usage,
            settled_at=completed_at,
            owner_transaction_id=transaction_id,
        )
        if actual_usage is not None and has_budget_reservation
        else None
    )
    settlement = (
        _settlement_evidence_from_receipt(
            settlement_plan["receipt"]
        )
        if settlement_plan is not None
        else None
    )
    settlement_id = (
        settlement_plan["settlement_id"]
        if settlement_plan is not None
        else None
    )
    report_sha256 = sha256_file(report_path)
    artifact_id = (
        f"artifact-{report_sha256[:12]}-"
        f"{hashlib.sha256(transaction_id.encode()).hexdigest()[:8]}"
    )
    artifact_path = (
        layout.artifact_index.parent
        / artifact_id
        / report_path.name
    ).resolve()
    report_artifact: JsonObject = {
        "artifact_id": artifact_id,
        "path": str(artifact_path),
        "storage": "snapshot",
        "size_bytes": report_path.stat().st_size,
        "sha256": report_sha256,
        "type": "task-report",
        "producer": f"task:{task['task_id']}",
        "subagent": None,
        "research_id": task["research_id"],
        "cycle_id": task["cycle_id"],
        "task_id": task["task_id"],
        "candidate_id": task["candidate_id"],
        "run_id": None,
        "report_id": None,
        "provenance_tags": ["validated-task-report"],
        "comparability_tags": [],
        "parent_artifact_id": None,
        "availability": "available",
        "registered_at": completed_at,
        "unavailable_reason": None,
    }
    artifact_ids = referenced_artifact_ids
    if len(set(artifact_ids)) != len(artifact_ids):
        raise ValueError("completion artifact IDs must be unique")
    intent: JsonObject = {
        "schema_version": 1,
        "kind": "task-completion",
        "transaction_id": transaction_id,
        "phase": "prepared",
        "cycle_id": task["cycle_id"],
        "task_id": task["task_id"],
        "candidate_id": task["candidate_id"],
        "stage_id": task["stage_id"],
        "task_record_path": str(task_record_path.resolve()),
        "task_record_hash_before": sha256_file(task_record_path),
        "task_before": dict(task),
        "report_path": str(report_path.resolve()),
        "report_sha256": report_sha256,
        "reported_status": frontmatter["status"],
        "result_status": result_status,
        "referenced_artifact_ids": referenced_artifact_ids,
        "artifact_ids": artifact_ids,
        "decision_recommendation": frontmatter[
            "decision_recommendation"
        ],
        "usage": usage,
        "actual_usage": actual_usage,
        "reservation_id": task["reservation_id"],
        "settlement": settlement,
        "settlement_id": settlement_id,
        "report_artifact_id": artifact_id,
        "report_artifact": report_artifact,
        "receipt_id": f"task-completion-{uuid4().hex}",
        "completed_at": completed_at,
        "receipt": None,
        "task_after": None,
        "result": None,
    }
    _populate_completion_result(intent)
    return intent


def _validated_report_output_artifacts(
    layout: ProjectLayout,
    task: Mapping[str, Any],
    artifact_ids: list[str],
) -> list[JsonObject]:
    artifacts: list[JsonObject] = []
    for artifact_id in artifact_ids:
        artifact = resolve_artifact(layout, artifact_id)
        verification = verify_artifact(layout, artifact_id)
        if (
            verification.get("available") is not True
            or verification.get("valid") is not True
        ):
            raise ValueError(
                f"report references invalid artifact: {artifact_id}"
            )
        for field in (
            "research_id",
            "cycle_id",
            "task_id",
            "candidate_id",
        ):
            if artifact.get(field) != task.get(field):
                raise ValueError(
                    f"output artifact {field} does not match task record"
                )
        artifacts.append(artifact)
    return artifacts


def _require_subagent_report_ready(
    layout: ProjectLayout, task: Mapping[str, Any]
) -> None:
    if task.get("task_owner") != "subagent":
        return
    event = latest_task_event(layout, task["cycle_id"], task["task_id"])
    if event is None or event.get("kind") != "report-ready":
        raise ValueError("subagent task latest lifecycle event is not report-ready")


def _render_finalized_report(
    report_text: str,
    frontmatter: Mapping[str, Any],
    artifact_ids: list[str],
) -> str:
    lines = report_text.splitlines(keepends=True)
    closing = next(
        index
        for index, line in enumerate(lines[1:], start=1)
        if line.rstrip("\r\n") == "---"
    )
    updated_frontmatter = dict(frontmatter)
    updated_frontmatter["artifact_ids"] = artifact_ids
    return (
        "---\n"
        + yaml.safe_dump(
            updated_frontmatter,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
        + "---\n"
        + "".join(lines[closing + 1 :])
    )


def _resume_completion(
    layout: ProjectLayout,
    intent_path: Path,
    intent: JsonObject,
    report_path: Path,
) -> JsonObject:
    intent = _ensure_completion_artifact(
        layout, intent_path, intent, report_path
    )
    intent = _ensure_completion_settlement(
        layout, intent_path, intent
    )
    intent = _ensure_completion_receipt(
        layout, intent_path, intent
    )
    intent = _ensure_completion_task_state(
        intent_path, intent
    )
    if intent.get("phase") != "committed":
        intent["phase"] = "committed"
        atomic_write_json(intent_path, intent)
    _, _, result = _expected_completion_evidence(intent)
    return result


def _ensure_completion_artifact(
    layout: ProjectLayout,
    intent_path: Path,
    intent: JsonObject,
    report_path: Path,
) -> JsonObject:
    matching = _matching_task_report_artifacts(layout, intent)
    recorded_id = intent.get("report_artifact_id")
    record = intent.get("report_artifact")
    if not isinstance(recorded_id, str) or not isinstance(record, Mapping):
        raise ValueError("completion intent task-report artifact is invalid")
    if record.get("artifact_id") != recorded_id:
        raise ValueError(
            "completion intent task-report artifact identity conflicts"
        )
    if any(row.get("artifact_id") != recorded_id for row in matching):
        raise ValueError(
            "completion intent has multiple matching task-report artifacts"
        )
    snapshot_path = Path(_required_string(record, "path"))
    ensure_within(layout.artifact_index.parent, snapshot_path)
    expected_snapshot_path = (
        layout.artifact_index.parent
        / recorded_id
        / Path(intent["report_path"]).name
    ).resolve()
    if snapshot_path.resolve() != expected_snapshot_path:
        raise ValueError(
            "completion task-report snapshot path evidence conflicts"
        )
    if snapshot_path.is_file():
        if (
            sha256_file(snapshot_path) != intent["report_sha256"]
            or snapshot_path.stat().st_size != record.get("size_bytes")
        ):
            raise ValueError(
                "completion task-report snapshot evidence conflicts"
            )
    else:
        _atomic_copy(report_path, snapshot_path)
        if (
            sha256_file(snapshot_path) != intent["report_sha256"]
            or snapshot_path.stat().st_size != record.get("size_bytes")
        ):
            raise OSError(
                "completion task-report snapshot changed while copied"
            )
    indexed = [
        row
        for row in read_jsonl(layout.artifact_index)
        if row.get("artifact_id") == recorded_id
    ]
    if not indexed:
        append_jsonl_locked(layout.artifact_index, record)
    elif len(indexed) != 1 or indexed[0] != record:
        raise ValueError(
            "completion task-report artifact evidence conflicts"
        )
    if intent.get("phase") == "prepared":
        intent["phase"] = "artifact_registered"
        atomic_write_json(intent_path, intent)
    verification = verify_artifact(layout, intent["report_artifact_id"])
    if (
        verification.get("available") is not True
        or verification.get("valid") is not True
    ):
        raise ValueError("completion task-report artifact is invalid")
    return intent


def _populate_completion_result(intent: JsonObject) -> None:
    receipt, task_after, result = _expected_completion_evidence(intent)
    intent["receipt"] = receipt
    intent["task_after"] = task_after
    intent["result"] = result


def _expected_completion_evidence(
    intent: Mapping[str, Any],
) -> tuple[JsonObject, JsonObject, JsonObject]:
    receipt: JsonObject = {
        "record_type": "task-completion",
        "transaction_id": intent["transaction_id"],
        "receipt_id": intent["receipt_id"],
        "completed_at": intent["completed_at"],
        "cycle_id": intent["cycle_id"],
        "task_id": intent["task_id"],
        "candidate_id": intent["candidate_id"],
        "stage_id": intent["stage_id"],
        "status": intent["result_status"],
        "reported_status": intent["reported_status"],
        "artifact_ids": intent["artifact_ids"],
        "report_artifact_id": intent["report_artifact_id"],
        "decision_recommendation": intent["decision_recommendation"],
        "reservation_id": intent["reservation_id"],
        "reservation_settled": intent["settlement"] is not None,
        "settlement_id": intent["settlement_id"],
        "usage": intent["usage"],
    }
    task_after = dict(intent["task_before"])
    task_after.update(
        {
            "status": intent["result_status"],
            "reported_status": intent["reported_status"],
            "report_artifact_id": intent["report_artifact_id"],
            "artifact_ids": intent["artifact_ids"],
            "decision_recommendation": intent[
                "decision_recommendation"
            ],
            "usage": intent["usage"],
            "completion_receipt_id": intent["receipt_id"],
            "completed_at": intent["completed_at"],
        }
    )
    result: JsonObject = {
        "status": intent["result_status"],
        "task_id": intent["task_id"],
        "report_artifact_id": intent["report_artifact_id"],
        "usage": intent["usage"],
        "receipt": receipt,
    }
    return receipt, task_after, result


def _matching_task_report_artifacts(
    layout: ProjectLayout, intent: Mapping[str, Any]
) -> list[JsonObject]:
    latest: dict[str, JsonObject] = {}
    for row in read_jsonl(layout.artifact_index):
        artifact_id = row.get("artifact_id")
        if isinstance(artifact_id, str):
            latest[artifact_id] = row
    return [
        row
        for row in latest.values()
        if row.get("type") == "task-report"
        and row.get("storage") == "snapshot"
        and row.get("producer") == f"task:{intent['task_id']}"
        and row.get("cycle_id") == intent["cycle_id"]
        and row.get("task_id") == intent["task_id"]
        and row.get("candidate_id") == intent["candidate_id"]
        and row.get("sha256") == intent["report_sha256"]
    ]


def _ensure_completion_settlement(
    layout: ProjectLayout,
    intent_path: Path,
    intent: JsonObject,
) -> JsonObject:
    settlement = intent.get("settlement")
    if settlement is not None:
        if not isinstance(settlement, Mapping):
            raise ValueError("completion settlement evidence is invalid")
        settle_budget_for_completion(
            layout,
            intent["reservation_id"],
            intent["actual_usage"],
            settled_at=intent["completed_at"],
            expected_settlement_id=intent["settlement_id"],
            owner_transaction_id=intent["transaction_id"],
        )
        resolved = resolve_budget_settlement(
            layout, intent["settlement_id"]
        )
        _validate_completion_settlement_binding(intent, resolved)
    if intent.get("phase") in {
        "prepared",
        "artifact_registered",
    }:
        intent["phase"] = "settlement_confirmed"
        atomic_write_json(intent_path, intent)
    return intent


def _ensure_completion_receipt(
    layout: ProjectLayout,
    intent_path: Path,
    intent: JsonObject,
) -> JsonObject:
    receipt = intent.get("receipt")
    if not isinstance(receipt, Mapping):
        raise ValueError("completion intent receipt is invalid")
    matching = [
        row
        for row in read_jsonl(layout.journal)
        if row.get("record_type") == "task-completion"
        and row.get("transaction_id") == intent["transaction_id"]
    ]
    if not matching:
        append_jsonl_locked(layout.journal, receipt)
    elif len(matching) != 1 or matching[0] != receipt:
        raise ValueError("completion receipt evidence conflicts")
    if intent.get("phase") == "settlement_confirmed":
        intent["phase"] = "receipt_appended"
        atomic_write_json(intent_path, intent)
    return intent


def _ensure_completion_task_state(
    intent_path: Path, intent: JsonObject
) -> JsonObject:
    task_record_path = Path(intent["task_record_path"])
    current = read_json(task_record_path)
    expected = intent.get("task_after")
    if not isinstance(expected, Mapping):
        raise ValueError("completion intent task state is invalid")
    if current == expected:
        pass
    elif (
        sha256_file(task_record_path)
        == intent["task_record_hash_before"]
    ):
        atomic_write_json(task_record_path, expected)
    else:
        raise ValueError("completion task state evidence conflicts")
    if intent.get("phase") == "receipt_appended":
        intent["phase"] = "task_state_written"
        atomic_write_json(intent_path, intent)
    return intent


def _prepare_settlement_evidence(
    layout: ProjectLayout,
    reservation_id: str,
    actual: Mapping[str, Any],
) -> JsonObject:
    budget = read_json(layout.budget)
    return _settlement_evidence_from_budget(
        budget, reservation_id, actual
    )


def _settlement_evidence_from_receipt(
    receipt: Mapping[str, Any],
) -> JsonObject:
    reservation = receipt.get("reservation")
    actual = receipt.get("actual")
    if not isinstance(reservation, Mapping) or not isinstance(actual, Mapping):
        raise ValueError("budget settlement receipt evidence is invalid")
    return {
        "reservation": dict(reservation),
        "actual_usage": dict(actual),
    }


def _validate_completion_settlement_binding(
    intent: Mapping[str, Any], resolved: Mapping[str, Any]
) -> None:
    receipt = resolved.get("receipt")
    if not isinstance(receipt, Mapping):
        raise ValueError("completion settlement receipt evidence is invalid")
    if (
        resolved.get("settlement_id") != intent.get("settlement_id")
        or _settlement_evidence_from_receipt(receipt)
        != intent.get("settlement")
    ):
        raise ValueError("completion settlement receipt binding conflicts")


def _settlement_evidence_from_budget(
    budget: Mapping[str, Any],
    reservation_id: str,
    actual: Mapping[str, Any],
) -> JsonObject:
    reservations = budget.get("reservations")
    reserved = budget.get("reserved")
    consumed = budget.get("consumed")
    if (
        not isinstance(reservations, Mapping)
        or not isinstance(reserved, Mapping)
        or not isinstance(consumed, Mapping)
    ):
        raise ValueError("budget settlement state is invalid")
    reservation = reservations.get(reservation_id)
    if not isinstance(reservation, Mapping):
        raise ValueError(f"unknown reservation: {reservation_id}")
    expected = _json_copy(budget, "budget")
    expected_reservations = dict(expected["reservations"])
    del expected_reservations[reservation_id]
    expected["reservations"] = expected_reservations
    expected["reserved"] = {
        field: _subtract_exact(
            reserved.get(field, 0), reservation.get(field, 0)
        )
        for field in ADDITIVE_BUDGET_FIELDS
    }
    expected["consumed"] = {
        field: consumed.get(field, 0) + actual.get(field, 0)
        for field in ADDITIVE_BUDGET_FIELDS
    }
    return {
        "reservation": dict(reservation),
        "actual_usage": dict(actual),
    }


def _completion_transaction_id(task: Mapping[str, Any]) -> str:
    digest = _hash_payload(
        {
            "kind": "task-completion",
            "cycle_id": task["cycle_id"],
            "task_id": task["task_id"],
        }
    )
    return f"task-completion-{digest}"


def _completion_intent_path(
    layout: ProjectLayout, task: Mapping[str, Any]
) -> Path:
    return (
        _workflow_transactions_root(layout)
        / f"{_completion_transaction_id(task)}.json"
    )


def _validate_completion_intent_identity(
    layout: ProjectLayout,
    intent: Mapping[str, Any],
    task: Mapping[str, Any],
    task_record_path: Path,
    report_path: Path,
) -> None:
    _validate_intent_header(intent)
    _require_exact_fields(
        intent, COMPLETION_INTENT_FIELDS, "completion intent"
    )
    expected = {
        "kind": "task-completion",
        "transaction_id": _completion_transaction_id(task),
        "cycle_id": task["cycle_id"],
        "task_id": task["task_id"],
        "candidate_id": task["candidate_id"],
        "stage_id": task["stage_id"],
        "task_record_path": str(task_record_path.resolve()),
        "report_path": str(report_path.resolve()),
        "reservation_id": task["reservation_id"],
    }
    for field, value in expected.items():
        if intent.get(field) != value:
            raise ValueError(
                f"completion intent identity conflicts at {field}"
            )
    for field in (
        "cycle_id",
        "task_id",
        "candidate_id",
        "stage_id",
        "task_record_path",
        "task_record_hash_before",
        "report_path",
        "report_sha256",
        "reported_status",
        "result_status",
        "decision_recommendation",
        "report_artifact_id",
        "receipt_id",
        "completed_at",
    ):
        _required_string(intent, field)
    if intent["reservation_id"] is not None:
        _required_string(intent, "reservation_id")
    _require_sha256(
        intent["task_record_hash_before"],
        "completion task_record_hash_before",
    )
    _require_sha256(intent["report_sha256"], "completion report_sha256")
    if (
        intent["reported_status"] not in REPORT_STATUSES
        or intent["result_status"] not in REPORT_STATUSES
    ):
        raise ValueError("completion status evidence is invalid")
    referenced_artifact_ids = _string_list(
        intent.get("referenced_artifact_ids"),
        "completion referenced_artifact_ids",
    )
    if len(set(referenced_artifact_ids)) != len(referenced_artifact_ids):
        raise ValueError("completion referenced_artifact_ids contain duplicates")
    artifact_ids = _string_list(
        intent.get("artifact_ids"), "completion artifact_ids"
    )
    if len(set(artifact_ids)) != len(artifact_ids):
        raise ValueError("completion artifact_ids contain duplicates")
    task_before = intent.get("task_before")
    report_artifact = intent.get("report_artifact")
    receipt = intent.get("receipt")
    task_after = intent.get("task_after")
    result = intent.get("result")
    if not all(
        isinstance(value, Mapping)
        for value in (
            task_before,
            report_artifact,
            receipt,
            task_after,
            result,
        )
    ):
        raise ValueError("completion durable evidence is invalid")
    if _atomic_json_hash(task_before) != intent["task_record_hash_before"]:
        raise ValueError("completion task_before hash evidence conflicts")
    if (
        task_before.get("status") != "open"
        or task_before.get("reservation_id") != intent["reservation_id"]
    ):
        raise ValueError("completion task_before identity conflicts")
    task_research_id = _required_string(task_before, "research_id")
    _require_exact_fields(
        report_artifact,
        TASK_REPORT_ARTIFACT_FIELDS,
        "completion task-report artifact",
    )
    if (
        report_artifact.get("artifact_id")
        != intent["report_artifact_id"]
        or report_artifact.get("sha256") != intent["report_sha256"]
        or report_artifact.get("type") != "task-report"
        or report_artifact.get("storage") != "snapshot"
        or report_artifact.get("availability") != "available"
        or report_artifact.get("producer")
        != f"task:{intent['task_id']}"
        or report_artifact.get("cycle_id") != intent["cycle_id"]
        or report_artifact.get("task_id") != intent["task_id"]
        or report_artifact.get("candidate_id") != intent["candidate_id"]
        or report_artifact.get("research_id") != task_research_id
    ):
        raise ValueError(
            "completion task-report artifact identity conflicts"
        )
    _require_nonnegative_integer(
        report_artifact.get("size_bytes"),
        "completion task-report size_bytes",
    )
    if artifact_ids != referenced_artifact_ids:
        raise ValueError("completion artifact IDs evidence conflicts")
    _validate_completion_usage(intent)
    expected_status = (
        intent["reported_status"]
        if intent["reservation_id"] is None
        or intent.get("actual_usage") is not None
        # A reserved task with unmeasured usage is recoverable: the
        # Coordinator detaches the reservation (unknown usage) rather than
        # escalating to the User. ``blocked`` is the self-recoverable state;
        # only genuine user-only decisions escalate to ``needs_user``.
        else "blocked"
    )
    if intent["result_status"] != expected_status:
        raise ValueError("completion reconciliation status conflicts")
    expected_receipt, expected_task, expected_result = (
        _expected_completion_evidence(intent)
    )
    _require_exact_fields(
        receipt, COMPLETION_RECEIPT_FIELDS, "completion receipt"
    )
    _require_exact_fields(
        result, COMPLETION_RESULT_FIELDS, "completion result"
    )
    if dict(receipt) != expected_receipt:
        raise ValueError("completion receipt evidence conflicts")
    if dict(task_after) != expected_task:
        raise ValueError("completion final task state evidence conflicts")
    if dict(result) != expected_result:
        raise ValueError("completion durable result evidence conflicts")
    _validate_completion_against_sources(
        layout,
        intent,
        task,
        report_path,
    )


def _validate_completion_against_sources(
    layout: ProjectLayout,
    intent: Mapping[str, Any],
    current_task: Mapping[str, Any],
    report_path: Path,
) -> None:
    task_before = intent["task_before"]
    trusted_task_before = _trusted_task_before(
        current_task, intent
    )
    if dict(task_before) != trusted_task_before:
        raise ValueError(
            "completion task_before source evidence conflicts"
        )

    trusted_report_path = Path(
        _required_string(task_before, "report_path")
    )
    if trusted_report_path.resolve() != report_path.resolve():
        raise ValueError("completion report path source evidence conflicts")
    expected_artifact_id = (
        f"artifact-{intent['report_sha256'][:12]}-"
        f"{hashlib.sha256(intent['transaction_id'].encode()).hexdigest()[:8]}"
    )
    expected_snapshot_path = (
        layout.artifact_index.parent
        / expected_artifact_id
        / trusted_report_path.name
    ).resolve()
    snapshot_exists = expected_snapshot_path.is_file()
    trusted_report = (
        expected_snapshot_path if snapshot_exists else trusted_report_path
    )
    if not trusted_report.is_file():
        raise ValueError("completion trusted report evidence is missing")
    trusted_report_hash = sha256_file(trusted_report)
    trusted_report_size = trusted_report.stat().st_size
    if trusted_report_hash != intent["report_sha256"]:
        raise ValueError("completion report hash source evidence conflicts")
    frontmatter, _, errors = _parse_report(trusted_report)
    if errors or frontmatter is None:
        raise ValueError(
            "completion trusted report content is invalid"
        )
    expected_report_fields = {
        "task_id": intent["task_id"],
        "candidate_id": intent["candidate_id"],
        "status": intent["reported_status"],
        "artifact_ids": intent["referenced_artifact_ids"],
        "decision_recommendation": intent[
            "decision_recommendation"
        ],
    }
    if frontmatter != expected_report_fields:
        raise ValueError(
            "completion report content source evidence conflicts"
        )
    if intent["report_artifact_id"] != expected_artifact_id:
        raise ValueError(
            "completion task-report artifact id source evidence conflicts"
        )
    expected_artifact: JsonObject = {
        "artifact_id": expected_artifact_id,
        "path": str(expected_snapshot_path),
        "storage": "snapshot",
        "size_bytes": trusted_report_size,
        "sha256": trusted_report_hash,
        "type": "task-report",
        "producer": f"task:{intent['task_id']}",
        "subagent": None,
        "research_id": task_before["research_id"],
        "cycle_id": intent["cycle_id"],
        "task_id": intent["task_id"],
        "candidate_id": intent["candidate_id"],
        "run_id": None,
        "report_id": None,
        "provenance_tags": ["validated-task-report"],
        "comparability_tags": [],
        "parent_artifact_id": None,
        "availability": "available",
        "registered_at": intent["completed_at"],
        "unavailable_reason": None,
    }
    if intent["report_artifact"].get("path") != str(
        expected_snapshot_path
    ):
        raise ValueError(
            "completion task-report artifact path source evidence conflicts"
        )
    if dict(intent["report_artifact"]) != expected_artifact:
        raise ValueError(
            "completion task-report artifact source evidence conflicts"
        )

    expected_usage = _aggregate_task_usage(layout, task_before)
    if dict(intent["usage"]) != expected_usage:
        raise ValueError("completion usage source evidence conflicts")
    expected_actual = (
        {
            "wall_seconds": expected_usage["duration_ms"] / 1000,
        }
        if task_before["reservation_id"] is not None
        else None
    )
    if intent.get("actual_usage") != expected_actual:
        raise ValueError(
            "completion actual_usage source evidence conflicts"
        )
    _validate_completion_settlement_source(
        layout, intent, task_before, expected_actual
    )


def _trusted_task_before(
    current_task: Mapping[str, Any],
    intent: Mapping[str, Any],
) -> JsonObject:
    if current_task.get("status") == "open":
        return dict(current_task)
    expected_receipt, expected_task_after, _ = (
        _expected_completion_evidence(intent)
    )
    if (
        dict(current_task) != expected_task_after
        or current_task.get("completion_receipt_id")
        != expected_receipt["receipt_id"]
    ):
        raise ValueError(
            "completion current task source evidence conflicts"
        )
    reconstructed = dict(current_task)
    for field in (
        "reported_status",
        "decision_recommendation",
        "usage",
        "completion_receipt_id",
    ):
        reconstructed.pop(field, None)
    reconstructed.update(
        {
            "status": "open",
            "report_artifact_id": None,
            "artifact_ids": [],
            "completed_at": None,
        }
    )
    return reconstructed


def _validate_completion_settlement_source(
    layout: ProjectLayout,
    intent: Mapping[str, Any],
    task_before: Mapping[str, Any],
    actual: Mapping[str, Any] | None,
) -> None:
    if task_before.get("reservation_id") is None:
        if (
            actual is not None
            or intent.get("settlement") is not None
            or intent.get("settlement_id") is not None
        ):
            raise ValueError(
                "completion control-plane settlement evidence conflicts"
            )
        return
    budget_slice = task_before.get("budget_slice")
    if not isinstance(budget_slice, Mapping):
        raise ValueError("completion task budget_slice is invalid")
    expected_reservation: JsonObject = {
        "id": intent["reservation_id"],
        "wall_seconds": budget_slice.get("wall_seconds"),
    }
    if task_before.get("task_owner") == "subagent":
        expected_reservation["loop-subagent-bound"] = 1
    reservation_id = intent["reservation_id"]
    if actual is None:
        if (
            intent.get("settlement") is not None
            or intent.get("settlement_id") is not None
        ):
            raise ValueError(
                "completion unsettled reservation source evidence conflicts"
            )
        if intent.get("phase") != "committed":
            budget = read_json(layout.budget)
            reservations = budget.get("reservations")
            if (
                not isinstance(reservations, Mapping)
                or reservations.get(reservation_id)
                != expected_reservation
            ):
                raise ValueError(
                    "completion unsettled reservation source evidence conflicts"
                )
        return

    resolved = inspect_budget_settlement(
        layout,
        reservation_id,
        actual,
        owner_transaction_id=intent["transaction_id"],
    )
    _validate_completion_settlement_binding(intent, resolved)
    receipt = resolved.get("receipt")
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("reservation") != expected_reservation
    ):
        raise ValueError(
            "completion reservation source evidence conflicts"
        )


def _canonical_budget_counter(
    field: str, value: int | float
) -> int | float:
    if value == 0:
        return 0
    return value


def _subtract_exact(total: Any, amount: Any) -> int | float:
    difference = total - amount
    if isinstance(difference, float) and abs(difference) < 1e-12:
        return 0.0
    return difference


def _validate_completion_usage(intent: Mapping[str, Any]) -> None:
    usage = intent.get("usage")
    if not isinstance(usage, Mapping) or set(usage) != {
        "cost_usd",
        "tokens",
        "duration_ms",
    }:
        raise ValueError("completion usage evidence is invalid")
    duration_ms = _require_nonnegative_integer(
        usage.get("duration_ms"), "completion duration_ms"
    )
    cost = usage.get("cost_usd")
    if cost is not None:
        _nonnegative_number(cost, "completion cost_usd")
    tokens = usage.get("tokens")
    if tokens is not None:
        if not isinstance(tokens, Mapping) or set(tokens) != {
            "input",
            "output",
            "cached",
            "total",
        }:
            raise ValueError("completion token evidence is invalid")
        for field, value in tokens.items():
            _require_nonnegative_integer(
                value, f"completion tokens.{field}"
            )
        if tokens["total"] != tokens["input"] + tokens["output"]:
            raise ValueError("completion token total evidence conflicts")
    actual = intent.get("actual_usage")
    settlement = intent.get("settlement")
    if intent.get("reservation_id") is None:
        if (
            actual is not None
            or settlement is not None
            or intent.get("settlement_id") is not None
        ):
            raise ValueError("completion reconciliation evidence conflicts")
        return
    if not isinstance(actual, Mapping) or set(actual) != {"wall_seconds"}:
        raise ValueError("completion actual_usage evidence is invalid")
    expected_actual = {"wall_seconds": duration_ms / 1000}
    if dict(actual) != expected_actual:
        raise ValueError("completion actual_usage evidence conflicts")
    if not isinstance(settlement, Mapping) or set(settlement) != {
        "reservation",
        "actual_usage",
    }:
        raise ValueError("completion settlement evidence is invalid")
    if not isinstance(settlement.get("reservation"), Mapping):
        raise ValueError("completion reservation evidence is invalid")
    if settlement.get("actual_usage") != actual:
        raise ValueError("completion settlement usage evidence conflicts")
    _required_string(intent, "settlement_id")


def _normalize_task_spec(
    layout: ProjectLayout, task_spec: Mapping[str, Any]
) -> tuple[JsonObject, Path, Path]:
    if not isinstance(task_spec, Mapping):
        raise ValueError("task spec must be an object")
    missing = TASK_SPEC_FIELDS - task_spec.keys()
    unknown = task_spec.keys() - TASK_SPEC_FIELDS
    if missing:
        raise ValueError(
            f"task spec missing required field: {sorted(missing)[0]}"
        )
    if unknown:
        raise ValueError(f"task spec has unknown field: {sorted(unknown)[0]}")
    normalized = _json_copy(task_spec, "task spec")

    for field in ("cycle_id", "task_id", "candidate_id", "stage_id"):
        normalized[field] = _safe_id(normalized[field], field)
    work_type = normalized["work_type"]
    if not isinstance(work_type, str) or work_type not in SUBAGENT_MODELS:
        raise ValueError(
            "work_type must be one of: "
            + ", ".join(sorted(SUBAGENT_MODELS))
        )
    normalized["primary_skill"] = _safe_id(
        normalized["primary_skill"], "primary_skill"
    )
    capabilities = _string_list(
        normalized["capability_skills"], "capability_skills"
    )
    for capability in capabilities:
        _safe_id(capability, "capability_skills")
    if len(capabilities) > 2:
        raise ValueError("task has more than two capability skills")
    if len(set(capabilities)) != len(capabilities):
        raise ValueError("task has a duplicate capability skill")
    if normalized["primary_skill"] in capabilities:
        raise ValueError("primary skill cannot also be a capability skill")
    normalized["capability_skills"] = capabilities

    artifact_ids = _string_list(
        normalized["input_artifact_ids"], "input_artifact_ids"
    )
    if len(set(artifact_ids)) != len(artifact_ids):
        raise ValueError("input_artifact_ids must not contain duplicates")
    normalized["input_artifact_ids"] = artifact_ids

    budget_slice = normalized["budget_slice"]
    if (
        not isinstance(budget_slice, Mapping)
        or set(budget_slice) != {"wall_seconds"}
    ):
        raise ValueError("budget_slice must contain exactly wall_seconds")
    wall = budget_slice["wall_seconds"]
    if isinstance(wall, bool) or not isinstance(wall, int) or wall < 0:
        raise ValueError(
            "budget_slice.wall_seconds must be a non-negative integer"
        )
    normalized["budget_slice"] = {
        "wall_seconds": wall,
    }

    editable_paths = _string_list(
        normalized["editable_paths"], "editable_paths"
    )
    normalized["editable_paths"] = [
        _normalize_project_relative_path(layout, value, "editable_paths")
        for value in editable_paths
    ]
    normalized["stop_conditions"] = _string_list(
        normalized["stop_conditions"], "stop_conditions"
    )

    output = normalized["output_path"]
    if not isinstance(output, str) or not output:
        raise ValueError("output_path must be a non-empty string")
    output_relative = _strict_relative_path(output, "output_path")
    expected = (
        PurePosixPath("cycles")
        / normalized["cycle_id"]
        / "tasks"
        / normalized["task_id"]
        / "report.md"
    )
    if output_relative != expected:
        raise ValueError(
            "output_path must be the task directory report.md"
        )
    normalized["output_path"] = output_relative.as_posix()

    research_root = (layout.root / ".research").resolve()
    task_dir = (
        layout.cycles
        / normalized["cycle_id"]
        / "tasks"
        / normalized["task_id"]
    )
    report_path = layout.root / ".research" / output_relative
    try:
        ensure_within(research_root, task_dir)
        ensure_within(task_dir, report_path)
    except ValueError as error:
        raise ValueError("output_path escapes the task directory") from error
    _reject_symlink_components(research_root, task_dir, "task directory")
    _reject_symlink_components(research_root, report_path, "output_path")
    return normalized, task_dir, report_path


def _validate_task_stage_and_limits(
    layout: ProjectLayout,
    graph: Mapping[str, Any],
    state: Mapping[str, Any],
    task_spec: Mapping[str, Any],
    context_manifest: Mapping[str, Any],
) -> Mapping[str, Any]:
    stages = graph.get("stages")
    if not isinstance(stages, list):
        raise ValueError("research graph stages must be a list")
    stage = next(
        (
            stage
            for stage in stages
            if isinstance(stage, Mapping)
            and stage.get("id") == task_spec["stage_id"]
        ),
        None,
    )
    if stage is None:
        raise ValueError(f"unknown stage_id: {task_spec['stage_id']}")
    if stage.get("task_owner") not in {"coordinator", "subagent"}:
        raise ValueError("active stage task_owner is invalid")
    control_plane = stage.get("control_plane", False)
    if not isinstance(control_plane, bool):
        raise ValueError("active stage control_plane is invalid")
    if control_plane and stage["task_owner"] != "coordinator":
        raise ValueError("active stage control_plane requires Coordinator")
    if task_spec["stage_id"] != state.get("current_stage"):
        raise ValueError("task stage_id does not match current stage")
    if task_spec["primary_skill"] != stage.get("primary_skill"):
        raise ValueError(
            "task primary_skill does not match active stage"
        )
    declared_capabilities = stage.get("capability_skills")
    if not isinstance(declared_capabilities, list):
        raise ValueError(
            "active stage capability_skills must be a list"
        )
    if not set(task_spec["capability_skills"]).issubset(
        declared_capabilities
    ):
        raise ValueError(
            "task capability_skills are not declared by active stage"
        )
    limits = graph.get("limits")
    if not isinstance(limits, Mapping):
        raise ValueError("research graph limits must be a mapping")
    maximum = limits.get("max_capability_skills")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum < 0
    ):
        raise ValueError("max_capability_skills must be a non-negative integer")
    if len(task_spec["capability_skills"]) > maximum:
        raise ValueError(
            "task capability skills exceed graph max_capability_skills"
        )
    required_inputs = stage.get("inputs")
    if not isinstance(required_inputs, list):
        raise ValueError("active stage inputs must be a list")
    manifest_artifacts = context_manifest.get("input_artifacts")
    if not isinstance(manifest_artifacts, list):
        raise ValueError("Context Manifest input_artifacts must be a list")
    available_types = {
        artifact.get("type")
        for artifact in manifest_artifacts
        if isinstance(artifact, Mapping)
    }
    missing_inputs = [
        artifact_type
        for artifact_type in required_inputs
        if artifact_type not in available_types
    ]
    if missing_inputs:
        raise ValueError(
            f"active stage input artifact is missing: {missing_inputs[0]}"
        )
    parallelizable = stage.get("parallelizable")
    if not isinstance(parallelizable, bool):
        raise ValueError(
            "active stage parallelizable must be a boolean"
        )
    if not parallelizable and any(
        task.get("status") == "open"
        and task.get("stage_id") == task_spec["stage_id"]
        for task in _task_records(layout)
    ):
        raise ValueError("active stage is not parallelizable")
    return stage


def _context_manifest(
    layout: ProjectLayout, artifact_ids: list[str]
) -> JsonObject:
    artifacts: list[JsonObject] = []
    for artifact_id in artifact_ids:
        record = resolve_artifact(layout, artifact_id)
        verification = verify_artifact(layout, artifact_id)
        if (
            verification.get("available") is not True
            or verification.get("valid") is not True
        ):
            raise ValueError(f"invalid input artifact: {artifact_id}")
        artifacts.append(
            {
                field: record[field]
                for field in CONTEXT_ARTIFACT_FIELDS
            }
        )
    for artifact in artifacts:
        if artifact["type"] == "runtime-context":
            validate_runtime_context(layout, artifact["artifact_id"], artifact_ids)
    return {"input_artifacts": artifacts}


def _validate_declared_stage_outputs(
    layout: ProjectLayout,
    task: Mapping[str, Any],
    artifacts: list[Mapping[str, Any]],
) -> None:
    graph, _ = _capture_graph(layout)
    stages = graph.get("stages")
    if not isinstance(stages, list):
        raise ValueError("research graph stages must be a list")
    stage = next(
        (
            item
            for item in stages
            if isinstance(item, Mapping) and item.get("id") == task["stage_id"]
        ),
        None,
    )
    if not isinstance(stage, Mapping):
        raise ValueError("completion stage is not declared by active graph")
    outputs = stage.get("outputs")
    if not isinstance(outputs, list) or not all(
        isinstance(output, str) and output for output in outputs
    ):
        raise ValueError("completion stage outputs are invalid")
    artifact_types = {
        artifact.get("type")
        for artifact in artifacts
        if isinstance(artifact.get("type"), str)
    }
    for output in outputs:
        if output not in artifact_types:
            raise ValueError(
                f"completion missing declared stage output artifact: {output}"
            )


def _render_brief(record: Mapping[str, Any]) -> str:
    report_frontmatter = {
        "task_id": record["task_id"],
        "candidate_id": record["candidate_id"],
        "status": "completed",
        "artifact_ids": [],
        "decision_recommendation": "blocked",
    }
    assignment = (
        "- Owner: `Coordinator`\n"
        "- Model: `none` (no subagent is dispatched)\n"
        if record["task_owner"] == "coordinator"
        else (
            "- Owner: `subagent`\n"
            f"- Work Type: `{record['task_spec']['work_type']}`\n"
            f"- Model: `{record['subagent_model']}`\n"
        )
    )
    reservation = (
        f"- Reservation ID: `{record['reservation_id']}`\n"
        if record["reservation_id"] is not None
        else "- Reservation ID: `none` (control-plane Coordinator tasks never reserve budget)\n"
    )
    return (
        f"# Task Brief: {record['task_id']}\n\n"
        "## Task Spec\n\n"
        "```json\n"
        f"{json.dumps(record['task_spec'], ensure_ascii=False, indent=2)}\n"
        "```\n\n"
        "## Subagent Assignment\n\n"
        f"{assignment}\n"
        "## Reservation\n\n"
        f"{reservation}\n"
        "## Context Manifest\n\n"
        "```json\n"
        f"{json.dumps(record['context_manifest'], ensure_ascii=False, indent=2)}\n"
        "```\n\n"
        "## Task Completion Report Frontmatter\n\n"
        "Write `report.md` with this exact YAML frontmatter before its required "
        "Markdown sections. `task_id` and `candidate_id` are "
        "Coordinator-provided task identities and must not change.\n\n"
        "```yaml\n"
        "---\n"
        f"{yaml.safe_dump(report_frontmatter, sort_keys=False)}"
        "---\n"
        "```\n\n"
        "Permitted `status` values are `completed`, `blocked`, `needs_user`, "
        "`needs_review`, and `failed`.\n\n"
        "`artifact_ids` must contain only registered, verified output evidence "
        "artifact IDs; never copy `input_artifact_ids`. The worker must keep "
        "the list empty. The Coordinator replaces it only through "
        "`task-report-finalize` before `task-complete`. The worker sets `status` and "
        "`decision_recommendation` to its actual outcome.\n\n"
        "## Required Report Body\n\n"
        "```markdown\n"
        "## Summary\n\n"
        "## Evidence\n\n"
        "## Ideas Considered\n\n"
        "## Rejected Ideas\n\n"
        "## Risks and Confounds\n\n"
        "## Recommended Next Action\n\n"
        "## Artifact Links\n"
        "```\n"
    )


def _parse_report(path: Path) -> tuple[JsonObject | None, str, list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return None, "", [f"could not read report: {error}"]
    return _parse_report_text(text)


def _parse_report_text(text: str) -> tuple[JsonObject | None, str, list[str]]:
    errors: list[str] = []
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None, text, ["missing YAML frontmatter"]
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return None, text, ["missing YAML frontmatter terminator"]

    yaml_text = "\n".join(lines[1:closing])
    try:
        loaded = yaml.load(yaml_text, Loader=_UniqueKeySafeLoader)
    except _DuplicateYamlKeyError as error:
        body_lines = lines[closing + 1 :]
        return None, "\n".join(body_lines), [
            f"duplicate frontmatter field: {error.key}",
            *_report_body_errors(body_lines),
        ]
    except yaml.YAMLError as error:
        return None, "\n".join(lines[closing + 1 :]), [
            "invalid YAML frontmatter"
        ]
    if not isinstance(loaded, Mapping):
        return None, "\n".join(lines[closing + 1 :]), [
            "frontmatter must be a mapping"
        ]
    frontmatter = dict(loaded)
    string_keys = {
        key for key in frontmatter if isinstance(key, str)
    }
    if len(string_keys) != len(frontmatter):
        errors.append("frontmatter field names must be strings")
    for field in sorted(REPORT_FIELDS - string_keys):
        errors.append(f"missing frontmatter field: {field}")
    for field in sorted(string_keys - REPORT_FIELDS):
        errors.append(f"unknown frontmatter field: {field}")
    for field in ("task_id", "candidate_id", "decision_recommendation"):
        if field in frontmatter and (
            not isinstance(frontmatter[field], str)
            or not frontmatter[field]
        ):
            errors.append(f"{field} must be a non-empty string")
    status = frontmatter.get("status")
    if "status" in frontmatter and not isinstance(status, str):
        errors.append("status must be a string")
    elif isinstance(status, str) and status not in REPORT_STATUSES:
        errors.append(f"unknown status: {status}")
    artifact_ids = frontmatter.get("artifact_ids")
    if artifact_ids is not None and (
        not isinstance(artifact_ids, list)
        or not all(
            isinstance(artifact_id, str) and artifact_id
            for artifact_id in artifact_ids
        )
    ):
        errors.append("artifact_ids must be a list")
    elif isinstance(artifact_ids, list) and len(set(artifact_ids)) != len(
        artifact_ids
    ):
        errors.append("artifact_ids must not contain duplicates")

    body_lines = lines[closing + 1 :]
    body = "\n".join(body_lines)
    errors.extend(_report_body_errors(body_lines))
    return frontmatter, body, errors


def _report_body_errors(lines: list[str]) -> list[str]:
    errors: list[str] = []
    headings = [
        match.group(1)
        for line in lines
        if (match := HEADING_PATTERN.match(line))
    ]
    seen_headings: set[str] = set()
    for heading in headings:
        if heading in seen_headings:
            errors.append(f"duplicate section: {heading}")
        seen_headings.add(heading)
        if heading not in REPORT_HEADINGS and not _is_decision_addendum_heading(
            heading
        ):
            errors.append(f"unknown section: {heading}")
    for heading in REPORT_HEADINGS:
        if heading not in headings:
            errors.append(f"missing section: {heading}")
    known_in_order = [
        heading
        for heading in headings
        if heading in REPORT_HEADINGS or _is_decision_addendum_heading(heading)
    ]
    expected_in_order = [
        heading for heading in REPORT_HEADINGS if heading in known_in_order
    ] + [
        heading
        for heading in known_in_order
        if _is_decision_addendum_heading(heading)
    ]
    if known_in_order != expected_in_order:
        errors.append("report sections are out of order")
    return errors


def _is_decision_addendum_heading(heading: str) -> bool:
    match = DECISION_ADDENDUM_HEADING_PATTERN.fullmatch(heading)
    if match is None:
        return False
    try:
        datetime.strptime(match["date"], "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _validate_active_task_limit(
    layout: ProjectLayout, graph: Mapping[str, Any]
) -> None:
    limits = graph.get("limits")
    if not isinstance(limits, Mapping):
        raise ValueError("research graph limits must be a mapping")
    maximum = limits.get("loop-subagent-bound")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum < 0
    ):
        raise ValueError(
            "loop-subagent-bound must be a non-negative integer"
        )
    active = [
        record
        for record in _task_records(layout)
        if record.get("status") == "open"
    ]
    if len(active) >= maximum:
        raise ValueError(
            "loop-subagent-bound active task limit reached"
        )


def _task_records(layout: ProjectLayout) -> list[JsonObject]:
    records: list[JsonObject] = []
    if not layout.cycles.exists():
        return records
    for path in sorted(layout.cycles.glob("*/tasks/*/task.json")):
        ensure_within(layout.cycles, path)
        records.append(read_json(path))
    return records


def _find_task(
    layout: ProjectLayout, task_id: str
) -> tuple[Path, JsonObject]:
    matches = [
        path
        for path in sorted(layout.cycles.glob(f"*/tasks/{task_id}/task.json"))
        if path.is_file()
    ]
    if not matches:
        raise ValueError(f"unknown task: {task_id}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous task id across cycles: {task_id}")
    path = matches[0]
    ensure_within(layout.cycles, path)
    _reject_symlink_components(layout.cycles, path, "task record")
    return path, read_json(path)


def _aggregate_task_usage(
    layout: ProjectLayout, task: Mapping[str, Any]
) -> JsonObject:
    calls = [
        row
        for row in read_jsonl(layout.model_calls)
        if row.get("cycle") == task["cycle_id"]
        and row.get("task") == task["task_id"]
    ]
    for row in calls:
        if row.get("candidate") != task["candidate_id"]:
            raise ValueError("model call candidate does not match task")
        if row.get("report") != task["output_path"]:
            raise ValueError("model call report does not match task output")

    if not calls:
        return {
            "cost_usd": None,
            "tokens": None,
            "duration_ms": 0,
        }
    duration_ms = sum(row["duration_ms"] for row in calls)
    cost_usd: int | float | None
    if any(row["cost_usd"] is None for row in calls):
        cost_usd = None
    else:
        cost_usd = sum(row["cost_usd"] for row in calls)
    tokens: JsonObject | None
    if any(row["tokens"] is None for row in calls):
        tokens = None
    else:
        tokens = {
            field: sum(row["tokens"][field] for row in calls)
            for field in ("input", "output", "cached", "total")
        }
    return {
        "cost_usd": cost_usd,
        "tokens": tokens,
        "duration_ms": duration_ms,
    }


def _validate_task_usage_within_slice(
    task: Mapping[str, Any], actual_usage: Mapping[str, Any]
) -> None:
    budget_slice = task.get("budget_slice")
    if not isinstance(budget_slice, Mapping):
        raise ValueError("task budget_slice is invalid")
    wall_seconds = actual_usage.get("wall_seconds")
    if (
        not isinstance(wall_seconds, (int, float))
        or isinstance(wall_seconds, bool)
    ):
        raise ValueError("task actual usage is invalid")
    if wall_seconds > budget_slice.get("wall_seconds"):
        raise ValueError("task actual wall time exceeds Budget Slice")


def _reject_existing_task(task_dir: Path) -> None:
    if task_dir.exists() or task_dir.is_symlink():
        raise FileExistsError(f"task directory already exists: {task_dir}")


def _normalize_project_relative_path(
    layout: ProjectLayout, value: str, field: str
) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must contain non-empty paths")
    if "\\" in value:
        raise ValueError(f"{field} path must use POSIX separators")
    supplied = Path(value)
    if supplied.is_absolute():
        candidate = supplied
    else:
        relative = _strict_relative_path(value, field)
        candidate = layout.root / relative
    try:
        ensure_within(layout.root, candidate)
    except ValueError as error:
        raise ValueError(f"{field} path escapes project root: {value}") from error
    _reject_symlink_components(layout.root.resolve(), candidate, field)
    return str(candidate.resolve())


def _validate_editable_envelope(
    layout: ProjectLayout, editable_paths: list[str]
) -> None:
    if not editable_paths:
        return
    envelope = read_json(
        layout.root / ".research" / "modification-envelope.json"
    )
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        raise PermissionError("active Modification Envelope is invalid")
    roots = payload.get("editable_paths")
    if not isinstance(roots, list) or not all(
        isinstance(root, str) and root for root in roots
    ):
        raise PermissionError(
            "active Modification Envelope editable paths are invalid"
        )
    canonical_roots = [Path(root).resolve() for root in roots]
    for editable_path in editable_paths:
        candidate = Path(editable_path)
        if not any(
            _is_within(approved_root, candidate)
            for approved_root in canonical_roots
        ):
            raise PermissionError(
                "editable path is outside active Modification Envelope: "
                f"{editable_path}"
            )


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        ensure_within(root, candidate)
    except ValueError:
        return False
    return True


def _strict_relative_path(value: str, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must contain non-empty paths")
    if "\\" in value:
        raise ValueError(f"{field} path must use POSIX separators")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{field} path must be normalized and relative")
    return path


def _reject_symlink_components(
    root: Path, candidate: Path, field: str
) -> None:
    resolved_root = root.resolve()
    try:
        relative = candidate.absolute().relative_to(root.absolute())
    except ValueError as error:
        raise ValueError(f"{field} escapes its allowed root") from error
    current = root.absolute()
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{field} must not traverse a symlink")
    try:
        ensure_within(resolved_root, candidate)
    except ValueError as error:
        raise ValueError(f"{field} escapes its allowed root") from error


def _remove_partial_task(task_dir: Path, cycles_root: Path) -> None:
    if task_dir.exists() and task_dir.is_dir() and not task_dir.is_symlink():
        shutil.rmtree(task_dir)
    for parent in (task_dir.parent, task_dir.parent.parent):
        if parent == cycles_root:
            break
        try:
            parent.rmdir()
        except OSError:
            break
