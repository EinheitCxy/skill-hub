from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import math
from numbers import Real
from pathlib import Path
import re
from typing import Any

from autoresearch.contracts import JsonObject
from autoresearch.io import (
    append_jsonl_locked,
    ensure_within,
    read_json,
    read_jsonl,
)
from autoresearch.project import ProjectLayout


MODEL_CALL_FIELDS = frozenset(
    {
        "id",
        "at",
        "usage",
        "cycle",
        "task",
        "candidate",
        "skills",
        "model",
        "duration_ms",
        "tokens",
        "cost_usd",
        "report",
        "status",
    }
)
TOKEN_FIELDS = frozenset({"input", "output", "cached", "total"})
RFC3339_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def append_model_call(
    layout: ProjectLayout, record: Mapping[str, Any]
) -> JsonObject:
    normalized = _normalize_model_call(layout, record)
    with _exclusive_lock(_workflow_lock_path(layout)):
        task = _resolve_task_record(layout, normalized)
        if task is not None:
            _require_task_accepts_model_call(layout, task, normalized)
        existing_ids = {
            row.get("id") for row in read_jsonl(layout.model_calls)
        }
        if normalized["id"] in existing_ids:
            raise ValueError(
                f"model call id already exists: {normalized['id']}"
            )
        append_jsonl_locked(layout.model_calls, normalized)
    return normalized


def _normalize_model_call(
    layout: ProjectLayout, record: Mapping[str, Any]
) -> JsonObject:
    if not isinstance(record, Mapping):
        raise ValueError("model call record must be an object")
    missing = MODEL_CALL_FIELDS - record.keys()
    unknown = record.keys() - MODEL_CALL_FIELDS
    if missing:
        raise ValueError(
            f"model call missing required field: {sorted(missing)[0]}"
        )
    if unknown:
        raise ValueError(
            f"model call has unknown field: {sorted(unknown)[0]}"
        )

    timestamp = record["at"]
    if (
        not isinstance(timestamp, str)
        or RFC3339_PATTERN.fullmatch(timestamp) is None
    ):
        raise ValueError("at must be an RFC-3339 timestamp")
    try:
        parsed_timestamp = datetime.fromisoformat(
            timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
        )
    except ValueError as error:
        raise ValueError("at must be an RFC-3339 timestamp") from error
    if parsed_timestamp.utcoffset() is None:
        raise ValueError("at must be an RFC-3339 timestamp")

    duration_ms = record["duration_ms"]
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, int)
        or duration_ms < 0
    ):
        raise ValueError("duration_ms must be a non-negative integer")

    skills = record["skills"]
    if (
        not isinstance(skills, list)
        or not 1 <= len(skills) <= 3
        or not all(isinstance(skill, str) and skill for skill in skills)
    ):
        raise ValueError("skills must contain one to three non-empty strings")

    candidate = record["candidate"]
    if candidate is not None and (
        not isinstance(candidate, str) or not candidate
    ):
        raise ValueError("candidate must be null or a non-empty string")

    normalized: JsonObject = {
        "id": _nonempty_string(record["id"], "id"),
        "at": timestamp,
        "usage": _nonempty_string(record["usage"], "usage"),
        "cycle": _nonempty_string(record["cycle"], "cycle"),
        "task": _nonempty_string(record["task"], "task"),
        "candidate": candidate,
        "skills": list(skills),
        "model": _nonempty_string(record["model"], "model"),
        "duration_ms": duration_ms,
        "tokens": _normalize_tokens(record["tokens"]),
        "cost_usd": _normalize_cost(record["cost_usd"]),
        "report": _normalize_report(layout, record["report"]),
        "status": _nonempty_string(record["status"], "status"),
    }
    try:
        encoded = json.dumps(
            normalized, ensure_ascii=False, allow_nan=False
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError("model call must contain JSON values") from error
    return copied


def _normalize_tokens(value: Any) -> JsonObject | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != TOKEN_FIELDS:
        raise ValueError(
            "tokens must be null or contain exactly input, output, cached, and total"
        )
    normalized: JsonObject = {}
    for field in TOKEN_FIELDS:
        amount = value[field]
        if (
            isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
        ):
            raise ValueError(
                f"tokens.{field} must be a non-negative integer"
            )
        normalized[field] = amount
    if normalized["total"] != normalized["input"] + normalized["output"]:
        raise ValueError("tokens.total must equal input plus output")
    if normalized["cached"] > normalized["input"]:
        raise ValueError("tokens.cached cannot exceed input")
    return {
        "input": normalized["input"],
        "output": normalized["output"],
        "cached": normalized["cached"],
        "total": normalized["total"],
    }


def _normalize_cost(value: Any) -> int | float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("cost_usd must be a non-negative finite number or null")
    return value


def _normalize_report(layout: ProjectLayout, value: Any) -> str:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError("report must be a non-empty path inside .research")
    research_root = layout.root / ".research"
    supplied = Path(value)
    candidate = supplied if supplied.is_absolute() else research_root / supplied
    try:
        resolved = ensure_within(research_root, candidate)
    except ValueError as error:
        raise ValueError("report path must remain inside .research") from error
    relative = resolved.relative_to(research_root.resolve())
    if not relative.parts:
        raise ValueError("report must name a file inside .research")
    return relative.as_posix()


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _resolve_task_record(
    layout: ProjectLayout, record: Mapping[str, Any]
) -> JsonObject | None:
    cycle_id = record["cycle"]
    task_id = record["task"]
    if (
        TASK_ID_PATTERN.fullmatch(cycle_id) is None
        or TASK_ID_PATTERN.fullmatch(task_id) is None
    ):
        return None
    path = layout.cycles / cycle_id / "tasks" / task_id / "task.json"
    ensure_within(layout.cycles, path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("task record is invalid")
    task = read_json(path)
    if not isinstance(task, Mapping):
        raise ValueError("task record must be an object")
    if (
        task.get("cycle_id") != cycle_id
        or task.get("task_id") != task_id
    ):
        raise ValueError("task record identity conflicts")
    return dict(task)


def _require_task_accepts_model_call(
    layout: ProjectLayout,
    task: Mapping[str, Any],
    record: Mapping[str, Any],
) -> None:
    if _completion_transaction_exists(layout, task):
        raise ValueError("task completion has already started")
    if _owner_bound_settlement_exists(layout, task):
        raise ValueError("task completion has already started")
    if task.get("status") != "open":
        raise ValueError("task is not open for model calls")
    for record_field, task_field in (
        ("candidate", "candidate_id"),
        ("report", "output_path"),
    ):
        if record.get(record_field) != task.get(task_field):
            raise ValueError(
                f"model call {record_field} does not match task record"
            )


def _completion_transaction_exists(
    layout: ProjectLayout, task: Mapping[str, Any]
) -> bool:
    root = layout.root / ".research" / "workflow-transactions"
    if not root.exists():
        return False
    if not root.is_dir():
        raise ValueError("workflow transaction store is invalid")
    for path in sorted(root.glob("*.json")):
        ensure_within(root, path)
        intent = read_json(path)
        if not isinstance(intent, Mapping):
            raise ValueError("workflow transaction must be an object")
        if (
            intent.get("kind") == "task-completion"
            and intent.get("cycle_id") == task["cycle_id"]
            and intent.get("task_id") == task["task_id"]
        ):
            return True
    return False


def _owner_bound_settlement_exists(
    layout: ProjectLayout, task: Mapping[str, Any]
) -> bool:
    if not layout.budget_transactions.exists():
        return False
    if not layout.budget_transactions.is_dir():
        raise ValueError("budget transaction store is invalid")
    for path in sorted(layout.budget_transactions.glob("*.json")):
        ensure_within(layout.budget_transactions, path)
        intent = read_json(path)
        if not isinstance(intent, Mapping):
            raise ValueError("budget settlement intent must be an object")
        owner_transaction_id = intent.get("owner_transaction_id")
        if (
            intent.get("reservation_id") == task.get("reservation_id")
            and owner_transaction_id is not None
        ):
            if (
                not isinstance(owner_transaction_id, str)
                or not owner_transaction_id
            ):
                raise ValueError(
                    "budget settlement owner transaction is invalid"
                )
            return True
    return False


def _workflow_lock_path(layout: ProjectLayout) -> Path:
    return layout.root / ".research" / "workflow.lock"


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
