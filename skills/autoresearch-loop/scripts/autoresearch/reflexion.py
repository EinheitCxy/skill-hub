from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from autoresearch.contracts import JsonObject
from autoresearch.io import append_jsonl_locked, read_jsonl
from autoresearch.project import ProjectLayout


REFLECTION_FIELDS = frozenset(
    {
        "reflection_id",
        "candidate_id",
        "stage_id",
        "failure_reason",
        "alternative_approach",
        "should_retry",
        "created_at",
    }
)
# Coordinator-side bounded Reflexion self-repair. On a recoverable failure
# (worker/evaluator returns blocked/inconclusive/debuggable) the Coordinator
# runs ONE reflection step before escalating to a debug subagent or needs_user.
# Stored reflections are append-only JSONL; only the last STORED_REFLECTION_CAP
# per candidate are considered for quota and injection.
STORED_REFLECTION_CAP = 20
DEFAULT_MAX_REFLECTIONS_IN_PROMPT = 5
DEFAULT_MAX_RETRY_ATTEMPTS = 3


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reflections_path(layout: ProjectLayout):
    return layout.root / ".research" / "reflections.jsonl"


def load_reflections(
    layout: ProjectLayout,
    candidate_id: str,
    limit: int = DEFAULT_MAX_REFLECTIONS_IN_PROMPT,
) -> list[JsonObject]:
    """Return up to ``limit`` most recent reflections for a candidate."""
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate_id must be a non-empty string")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    path = _reflections_path(layout)
    if not path.is_file():
        return []
    rows = [
        row
        for row in read_jsonl(path)
        if row.get("candidate_id") == candidate_id
    ]
    if limit:
        return rows[-limit:]
    return []


def reflect(
    layout: ProjectLayout,
    *,
    candidate_id: str,
    stage_id: str,
    failure_reason: str,
    alternative_approach: str,
    should_retry: bool,
    max_reflections_in_prompt: int = DEFAULT_MAX_REFLECTIONS_IN_PROMPT,
    max_retry_attempts: int = DEFAULT_MAX_RETRY_ATTEMPTS,
) -> JsonObject:
    """Record one bounded reflection and return the bounded retry decision.

    The Coordinator performs the reflection reasoning and supplies the
    ``alternative_approach`` and its initial ``should_retry``. This function
    enforces the quota bound: when the candidate already has
    ``max_retry_attempts`` prior reflections, ``should_retry`` is forced to
    ``False`` so escalation proceeds to a debug subagent or ``needs_user``.
    Up to ``max_reflections_in_prompt`` prior reflections are returned in
    ``reflections_used`` for injection into the next Task Context Pack.
    """
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate_id must be a non-empty string")
    if not isinstance(stage_id, str) or not stage_id:
        raise ValueError("stage_id must be a non-empty string")
    if not isinstance(failure_reason, str) or not failure_reason:
        raise ValueError("failure_reason must be a non-empty string")
    if not isinstance(alternative_approach, str) or not alternative_approach:
        raise ValueError("alternative_approach must be a non-empty string")
    if not isinstance(should_retry, bool):
        raise ValueError("should_retry must be a boolean")
    if (
        isinstance(max_reflections_in_prompt, bool)
        or not isinstance(max_reflections_in_prompt, int)
        or max_reflections_in_prompt < 0
    ):
        raise ValueError("max_reflections_in_prompt must be a non-negative integer")
    if (
        isinstance(max_retry_attempts, bool)
        or not isinstance(max_retry_attempts, int)
        or max_retry_attempts < 0
    ):
        raise ValueError("max_retry_attempts must be a non-negative integer")

    prior = load_reflections(
        layout, candidate_id, limit=STORED_REFLECTION_CAP
    )
    bounded_should_retry = bool(should_retry) and len(prior) < max_retry_attempts
    row: JsonObject = {
        "reflection_id": f"reflection-{uuid4().hex}",
        "candidate_id": candidate_id,
        "stage_id": stage_id,
        "failure_reason": failure_reason,
        "alternative_approach": alternative_approach,
        "should_retry": bounded_should_retry,
        "created_at": _timestamp(),
    }
    append_jsonl_locked(_reflections_path(layout), row)
    reflections_used = load_reflections(
        layout, candidate_id, limit=max_reflections_in_prompt
    )
    return {
        "should_retry": bounded_should_retry,
        "alternative_approach": alternative_approach,
        "reflections_used": reflections_used,
    }
