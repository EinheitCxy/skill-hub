from __future__ import annotations

from pathlib import Path

import pytest

from autoresearch.io import atomic_write_json, read_jsonl
from autoresearch.worker_events import (
    reconcile_worker_reports,
    record_worker_event,
    worker_status,
)
from autoresearch.workflow import open_task


def _task(
    layout, task_id: str = "task-1", *, cycle_id: str = "cycle-1", status: str = "open"
) -> dict:
    output_path = f"cycles/{cycle_id}/tasks/{task_id}/report.md"
    task = {
        "cycle_id": cycle_id,
        "task_id": task_id,
        "subagent_model": "terra",
        "output_path": output_path,
        "status": status,
    }
    path = layout.cycles / cycle_id / "tasks" / task_id / "task.json"
    path.parent.mkdir(parents=True)
    atomic_write_json(path, task)
    return task


def _event(task: dict, event_id: str, kind: str = "dispatched", **overrides) -> dict:
    event = {
        "event_id": event_id,
        "kind": kind,
        "cycle_id": task["cycle_id"],
        "task_id": task["task_id"],
        "worker_id": "worker-1",
        "attempt": 1,
        "terminal_status": None,
        "reason": None,
    }
    event.update(overrides)
    return event


def test_dispatched_event_is_enriched_and_appended_to_its_own_ledger(project_layout):
    task = _task(project_layout)

    record = record_worker_event(project_layout, _event(task, "dispatch-1"))

    assert record["schema_version"] == 1
    assert record["model"] == "terra"
    assert record["report_path"] == "cycles/cycle-1/tasks/task-1/report.md"
    assert record["at"].endswith("Z")
    assert read_jsonl(project_layout.worker_events) == [record]


@pytest.mark.parametrize(
    "event",
    [
        {"event_id": "missing-fields"},
        {
            "event_id": "unknown-field",
            "kind": "dispatched",
            "cycle_id": "cycle-1",
            "task_id": "task-1",
            "worker_id": "worker-1",
            "attempt": 1,
            "terminal_status": None,
            "reason": None,
            "model": "forbidden",
        },
    ],
)
def test_event_schema_rejects_missing_or_controller_owned_fields(project_layout, event):
    _task(project_layout)

    with pytest.raises(ValueError):
        record_worker_event(project_layout, event)


def test_event_transitions_prevent_terminal_after_report_ready(project_layout):
    task = _task(project_layout)
    record_worker_event(project_layout, _event(task, "dispatch-1"))
    record_worker_event(project_layout, _event(task, "ready-1", "report-ready"))

    with pytest.raises(ValueError, match="transition"):
        record_worker_event(
            project_layout,
            _event(
                task,
                "terminal-1",
                "terminal",
                terminal_status="failed",
                reason="worker stopped",
            ),
        )


def test_worker_status_is_sorted_and_uses_latest_event_for_each_open_task(project_layout):
    second = _task(project_layout, "task-1", cycle_id="cycle-2")
    first = _task(project_layout, "task-1", cycle_id="cycle-1")
    record_worker_event(project_layout, _event(second, "dispatch-2"))
    record_worker_event(project_layout, _event(first, "dispatch-1"))
    record_worker_event(project_layout, _event(first, "ready-1", "report-ready"))

    assert worker_status(project_layout) == {
        "reconciled_task_ids": [],
        "workers": [
            {
                "cycle_id": "cycle-1",
                "task_id": "task-1",
                "worker_id": "worker-1",
                "attempt": 1,
                "model": "terra",
                "status": "ready_to_reconcile",
            },
            {
                "cycle_id": "cycle-2",
                "task_id": "task-1",
                "worker_id": "worker-1",
                "attempt": 1,
                "model": "terra",
                "status": "dispatched",
            },
        ]
    }


def test_reconcile_appends_one_deterministic_report_ready_event(project_layout):
    task = _task(project_layout)
    record_worker_event(project_layout, _event(task, "dispatch-1"))
    report = project_layout.root / ".research" / task["output_path"]
    report.write_text("worker output", encoding="utf-8")

    assert reconcile_worker_reports(project_layout) == {"reconciled_task_ids": ["task-1"]}
    assert reconcile_worker_reports(project_layout) == {"reconciled_task_ids": []}
    rows = read_jsonl(project_layout.worker_events)
    assert rows[-1]["event_id"] == "reconcile-cycle-1-task-1-worker-1-1"
    assert rows[-1]["kind"] == "report-ready"


def test_terminal_task_cannot_be_dispatched_again_or_reenter_waiting(project_layout):
    task = _task(project_layout)
    record_worker_event(project_layout, _event(task, "dispatch-1"))
    record_worker_event(
        project_layout,
        _event(
            task,
            "terminal-1",
            "terminal",
            terminal_status="failed",
            reason="worker process ended",
        ),
    )

    with pytest.raises(ValueError, match="already has a worker lifecycle"):
        record_worker_event(
            project_layout,
            _event(
                task,
                "dispatch-2",
                worker_id="worker-2",
                attempt=2,
            ),
        )

    assert worker_status(project_layout) == {
        "reconciled_task_ids": [],
        "workers": [
            {
                "cycle_id": "cycle-1",
                "task_id": "task-1",
                "worker_id": "worker-1",
                "attempt": 1,
                "model": "terra",
                "status": "terminal",
                "terminal_status": "failed",
                "reason": "worker process ended",
            }
        ],
    }


def test_coordinator_open_task_cannot_enter_worker_waiting_state(
    approved_project,
):
    task = open_task(
        approved_project,
        {
            "cycle_id": "cycle-1",
            "task_id": "execute-1",
            "candidate_id": "cand-1",
            "stage_id": "intake",
            "primary_skill": "autoresearch-loop",
            "capability_skills": [],
            "input_artifact_ids": [],
            "budget_slice": {"wall_seconds": 300},
            "editable_paths": [],
            "output_path": "cycles/cycle-1/tasks/execute-1/report.md",
            "stop_conditions": ["budget exhausted"],
            "work_type": "execution",
        },
    )
    with pytest.raises(ValueError, match="Coordinator-owned"):
        record_worker_event(
            approved_project,
            _event(task, "dispatch-real", worker_id="worker-real"),
        )
