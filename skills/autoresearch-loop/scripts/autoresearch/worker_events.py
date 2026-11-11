from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import re
from typing import Any

from autoresearch.contracts import JsonObject
from autoresearch.io import append_jsonl_locked, ensure_within, read_json, read_jsonl
from autoresearch.project import ProjectLayout


EVENT_FIELDS = frozenset(
    {
        "event_id",
        "kind",
        "cycle_id",
        "task_id",
        "worker_id",
        "attempt",
        "terminal_status",
        "reason",
    }
)
PERSISTED_EVENT_FIELDS = EVENT_FIELDS | frozenset(
    {"schema_version", "at", "model", "report_path"}
)
EVENT_KINDS = frozenset({"dispatched", "report-ready", "terminal"})
TERMINAL_STATUSES = frozenset(
    {"blocked", "needs_user", "needs_review", "failed", "cancelled"}
)
SAFE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RFC3339_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)


def record_worker_event(
    layout: ProjectLayout, event: Mapping[str, Any]
) -> JsonObject:
    normalized = _normalize_event(event)
    with _exclusive_lock(_worker_events_lock_path(layout)):
        return _record_worker_event_locked(layout, normalized)


def latest_task_event(
    layout: ProjectLayout, cycle_id: str, task_id: str
) -> JsonObject | None:
    cycle_id = _safe_id(cycle_id, "cycle_id")
    task_id = _safe_id(task_id, "task_id")
    with _exclusive_lock(_worker_events_lock_path(layout)):
        latest: JsonObject | None = None
        for event in _read_worker_events(layout):
            if (
                event["cycle_id"] == cycle_id
                and event["task_id"] == task_id
            ):
                latest = event
        return latest


def worker_status(layout: ProjectLayout) -> JsonObject:
    with _exclusive_lock(_worker_events_lock_path(layout)):
        reconciled_task_ids = _reconcile_worker_reports_locked(layout)
        open_tasks = _open_tasks(layout)
        latest = _latest_open_task_events(layout, open_tasks)
        workers: list[JsonObject] = []
        for key in sorted(latest):
            event = latest[key]
            status = {
                "dispatched": "dispatched",
                "report-ready": "ready_to_reconcile",
                "terminal": "terminal",
            }[event["kind"]]
            worker: JsonObject = {
                "cycle_id": event["cycle_id"],
                "task_id": event["task_id"],
                "worker_id": event["worker_id"],
                "attempt": event["attempt"],
                "model": event["model"],
                "status": status,
            }
            if event["kind"] == "terminal":
                worker["terminal_status"] = event["terminal_status"]
                worker["reason"] = event["reason"]
            workers.append(worker)
        return {
            "reconciled_task_ids": reconciled_task_ids,
            "workers": workers,
        }


def reconcile_worker_reports(layout: ProjectLayout) -> JsonObject:
    with _exclusive_lock(_worker_events_lock_path(layout)):
        return {"reconciled_task_ids": _reconcile_worker_reports_locked(layout)}


def _record_worker_event_locked(
    layout: ProjectLayout, event: JsonObject
) -> JsonObject:
    task = _open_task(layout, event["cycle_id"], event["task_id"])
    if task.get("task_owner") == "coordinator":
        raise ValueError("Coordinator-owned task cannot receive worker events")
    model = _nonempty_string(task.get("subagent_model"), "task subagent_model")
    report_path = _nonempty_string(task.get("output_path"), "task output_path")
    existing = _read_worker_events(layout)
    if any(row["event_id"] == event["event_id"] for row in existing):
        raise ValueError(f"worker event id already exists: {event['event_id']}")
    _validate_transition(existing, event)
    record: JsonObject = {
        "schema_version": 1,
        "at": _timestamp(),
        **event,
        "model": model,
        "report_path": report_path,
    }
    append_jsonl_locked(layout.worker_events, record)
    return record


def _normalize_event(event: Mapping[str, Any]) -> JsonObject:
    if not isinstance(event, Mapping):
        raise ValueError("worker event must be an object")
    missing = EVENT_FIELDS - event.keys()
    unknown = event.keys() - EVENT_FIELDS
    if missing:
        raise ValueError(f"worker event missing required field: {sorted(missing)[0]}")
    if unknown:
        raise ValueError(f"worker event has unknown field: {sorted(unknown)[0]}")

    kind = event["kind"]
    if not isinstance(kind, str) or kind not in EVENT_KINDS:
        raise ValueError("worker event kind is invalid")
    attempt = event["attempt"]
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt <= 0:
        raise ValueError("worker event attempt must be a positive integer")
    terminal_status = event["terminal_status"]
    reason = event["reason"]
    if kind in {"dispatched", "report-ready"}:
        if terminal_status is not None or reason is not None:
            raise ValueError(f"{kind} event terminal_status and reason must be null")
    else:
        if (
            not isinstance(terminal_status, str)
            or terminal_status not in TERMINAL_STATUSES
        ):
            raise ValueError("terminal event terminal_status is invalid")
        _nonempty_string(reason, "terminal event reason")

    return {
        "event_id": _safe_id(event["event_id"], "event_id"),
        "kind": kind,
        "cycle_id": _safe_id(event["cycle_id"], "cycle_id"),
        "task_id": _safe_id(event["task_id"], "task_id"),
        "worker_id": _safe_id(event["worker_id"], "worker_id"),
        "attempt": attempt,
        "terminal_status": terminal_status,
        "reason": reason,
    }


def _validate_transition(existing: list[JsonObject], event: Mapping[str, Any]) -> None:
    task_key = (event["cycle_id"], event["task_id"])
    task_events = [
        row
        for row in existing
        if (row["cycle_id"], row["task_id"]) == task_key
    ]
    key = (
        event["cycle_id"],
        event["task_id"],
        event["worker_id"],
        event["attempt"],
    )
    prior = [
        row
        for row in existing
        if (
            row["cycle_id"],
            row["task_id"],
            row["worker_id"],
            row["attempt"],
        )
        == key
    ]
    kind = event["kind"]
    if kind == "dispatched":
        if task_events:
            raise ValueError(
                "illegal worker event transition: task already has a worker lifecycle"
            )
        return
    if not prior or prior[0]["kind"] != "dispatched":
        raise ValueError("illegal worker event transition: dispatched is required")
    if any(row["kind"] == "terminal" for row in prior):
        raise ValueError("illegal worker event transition: terminal is final")
    if kind == "report-ready":
        if any(row["kind"] == "report-ready" for row in prior):
            raise ValueError(
                "illegal worker event transition: report-ready already exists"
            )
        return
    if any(row["kind"] == "report-ready" for row in prior):
        raise ValueError("illegal worker event transition: report-ready is final")


def _reconcile_worker_reports_locked(layout: ProjectLayout) -> list[str]:
    open_tasks = _open_tasks(layout)
    latest = _latest_open_task_events(layout, open_tasks)
    reconciled_task_ids: list[str] = []
    for key in sorted(latest):
        event = latest[key]
        if event["kind"] != "dispatched":
            continue
        task = open_tasks[key]
        report_path = _task_report_path(layout, task)
        if not report_path.is_file():
            continue
        generated = _normalize_event(
            {
                "event_id": (
                    f"reconcile-{event['cycle_id']}-{event['task_id']}-"
                    f"{event['worker_id']}-{event['attempt']}"
                ),
                "kind": "report-ready",
                "cycle_id": event["cycle_id"],
                "task_id": event["task_id"],
                "worker_id": event["worker_id"],
                "attempt": event["attempt"],
                "terminal_status": None,
                "reason": None,
            }
        )
        _record_worker_event_locked(layout, generated)
        reconciled_task_ids.append(event["task_id"])
    return reconciled_task_ids


def _latest_open_task_events(
    layout: ProjectLayout, open_tasks: Mapping[tuple[str, str], JsonObject]
) -> dict[tuple[str, str], JsonObject]:
    latest: dict[tuple[str, str], JsonObject] = {}
    for event in _read_worker_events(layout):
        key = (event["cycle_id"], event["task_id"])
        if key in open_tasks:
            latest[key] = event
    return latest


def _open_tasks(layout: ProjectLayout) -> dict[tuple[str, str], JsonObject]:
    tasks: dict[tuple[str, str], JsonObject] = {}
    if not layout.cycles.exists():
        return tasks
    for path in sorted(layout.cycles.glob("*/tasks/*/task.json")):
        ensure_within(layout.cycles, path)
        if not path.is_file():
            continue
        task = read_json(path)
        if not isinstance(task, Mapping):
            raise ValueError("task record must be an object")
        cycle_id = _safe_id(task.get("cycle_id"), "task cycle_id")
        task_id = _safe_id(task.get("task_id"), "task task_id")
        if path.parent.name != task_id or path.parent.parent.parent.name != cycle_id:
            raise ValueError("task record identity conflicts")
        if task.get("status") == "open":
            tasks[(cycle_id, task_id)] = dict(task)
    return tasks


def _open_task(layout: ProjectLayout, cycle_id: str, task_id: str) -> JsonObject:
    path = layout.cycles / cycle_id / "tasks" / task_id / "task.json"
    ensure_within(layout.cycles, path)
    if not path.is_file():
        raise ValueError("worker event task does not exist")
    task = read_json(path)
    if not isinstance(task, Mapping):
        raise ValueError("task record must be an object")
    if task.get("cycle_id") != cycle_id or task.get("task_id") != task_id:
        raise ValueError("task record identity conflicts")
    if task.get("status") != "open":
        raise ValueError("worker event task is not open")
    return dict(task)


def _task_report_path(layout: ProjectLayout, task: Mapping[str, Any]) -> Path:
    relative = _nonempty_string(task.get("output_path"), "task output_path")
    try:
        return ensure_within(layout.root / ".research", layout.root / ".research" / relative)
    except ValueError as error:
        raise ValueError("task output_path escapes .research") from error


def _read_worker_events(layout: ProjectLayout) -> list[JsonObject]:
    if not layout.worker_events.exists():
        return []
    rows = read_jsonl(layout.worker_events)
    for row in rows:
        _validate_persisted_event(row)
    return rows


def _validate_persisted_event(event: Mapping[str, Any]) -> None:
    if set(event) != PERSISTED_EVENT_FIELDS:
        raise ValueError("worker event ledger record has invalid fields")
    if event.get("schema_version") != 1:
        raise ValueError("worker event ledger schema_version is invalid")
    timestamp = event.get("at")
    if not isinstance(timestamp, str) or RFC3339_PATTERN.fullmatch(timestamp) is None:
        raise ValueError("worker event ledger timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(
            timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
        )
    except ValueError as error:
        raise ValueError("worker event ledger timestamp is invalid") from error
    if parsed.utcoffset() is None:
        raise ValueError("worker event ledger timestamp is invalid")
    _normalize_event({field: event[field] for field in EVENT_FIELDS})
    _nonempty_string(event.get("model"), "worker event ledger model")
    _nonempty_string(event.get("report_path"), "worker event ledger report_path")


def _safe_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or SAFE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a safe id")
    return value


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _worker_events_lock_path(layout: ProjectLayout) -> Path:
    return layout.root / ".research" / "worker-events.lock"


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
