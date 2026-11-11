from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import multiprocessing
from pathlib import Path

import pytest
import yaml

from autoresearch.artifacts import (
    register_artifact,
    resolve_artifact,
    verify_artifact,
)
from autoresearch.governance import (
    record_gate_receipt,
    reserve_budget,
    resolve_budget_settlement,
    request_gate,
    settle_budget,
)
from autoresearch.io import atomic_write_json, read_json, read_jsonl, sha256_file
from autoresearch.project import ProjectLayout
from autoresearch.records import append_model_call
from autoresearch.workflow import (
    apply_graph_revision,
    complete_task,
    create_checkpoint,
    open_task,
    request_graph_revision,
    transition_stage,
    validate_project_integrity,
    inspect_pending_intents,
    quarantine_pending_intent,
    validate_report,
)


def test_intent_inspect_and_quarantine_preserve_pending_intent(project_layout):
    transaction_id = "stage-transition-test"
    intent_path = (
        project_layout.root
        / ".research"
        / "workflow-transactions"
        / f"{transaction_id}.json"
    )
    atomic_write_json(
        intent_path,
        {
            "schema_version": 1,
            "kind": "stage-transition",
            "transaction_id": transaction_id,
            "phase": "prepared",
        },
    )

    assert inspect_pending_intents(project_layout) == [
        {
            "intent_id": transaction_id,
            "category": "workflow",
            "kind": "stage-transition",
            "phase": "prepared",
            "path": str(intent_path),
        }
    ]
    receipt = quarantine_pending_intent(
        project_layout, transaction_id, "operator triage"
    )

    assert not intent_path.exists()
    assert Path(receipt["quarantine_path"]).is_file()
    assert receipt["reason"] == "operator triage"


def test_inspect_pending_intents_isolates_malformed_intent(project_layout):
    intent_path = (
        project_layout.root
        / ".research"
        / "workflow-transactions"
        / "malformed.json"
    )
    atomic_write_json(intent_path, {"schema_version": 1, "unexpected": True})

    entries = inspect_pending_intents(project_layout)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["intent_id"] == "malformed"
    assert entry["kind"] == "malformed"
    assert entry["phase"] == "malformed"
    assert entry["path"] == str(intent_path)


def test_validate_allows_run_reservation_without_run_state(approved_project):
    reserve_budget(approved_project, {"id": "run:no-state", "wall_seconds": 5})
    assert validate_project_integrity(approved_project) == []


def test_validate_reservation_task_consistency_rejects_malformed_task_id(
    project_layout,
):
    from autoresearch.workflow.recovery import (
        _validate_reservation_task_consistency,
    )
    budget = {"reservations": {"task:onlyone": {"wall_seconds": 1}}}
    with pytest.raises(ValueError, match="task reservation id must be"):
        _validate_reservation_task_consistency(project_layout, budget)


from autoresearch.workflow.tasks import finalize_task_report
from researchctl import main


REPORT_HEADINGS = (
    "Summary",
    "Evidence",
    "Ideas Considered",
    "Rejected Ideas",
    "Risks and Confounds",
    "Recommended Next Action",
    "Artifact Links",
)


def canonical_hash(value: dict) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def task_spec(task_id: str = "task-1", **overrides) -> dict:
    spec = {
        "cycle_id": "cycle-1",
        "task_id": task_id,
        "candidate_id": "cand-1",
        "stage_id": "intake",
        "primary_skill": "autoresearch-loop",
        "capability_skills": [],
        "input_artifact_ids": [],
        "budget_slice": {"wall_seconds": 300},
        "editable_paths": [],
        "output_path": f"cycles/cycle-1/tasks/{task_id}/report.md",
        "stop_conditions": ["missing input artifact", "budget exhausted"],
        "work_type": "simple",
    }
    spec.update(overrides)
    return spec


def open_coordinator_task_with_test_stage(
    layout,
    monkeypatch: pytest.MonkeyPatch,
    spec: dict,
    *,
    control_plane: bool,
    retain_outputs: bool = False,
) -> dict:
    from autoresearch.workflow import tasks as workflow

    original_capture_graph = workflow._capture_graph

    def capture_budgeted_graph(current_layout):
        graph, graph_hash = original_capture_graph(current_layout)
        staged_graph = deepcopy(graph)
        stages = staged_graph["stages"]
        stage = next(
            item for item in stages if item["id"] == spec["stage_id"]
        )
        stage["control_plane"] = control_plane
        if not retain_outputs:
            stage["outputs"] = []
        return staged_graph, graph_hash

    monkeypatch.setattr(workflow, "_capture_graph", capture_budgeted_graph)
    return open_task(layout, spec)


def open_budgeted_coordinator_task(
    layout, monkeypatch: pytest.MonkeyPatch, spec: dict, *, retain_outputs: bool = False
) -> dict:
    return open_coordinator_task_with_test_stage(
        layout,
        monkeypatch,
        spec,
        control_plane=False,
        retain_outputs=retain_outputs,
    )


def open_control_plane_task_without_outputs(
    layout, monkeypatch: pytest.MonkeyPatch, spec: dict
) -> dict:
    return open_coordinator_task_with_test_stage(
        layout, monkeypatch, spec, control_plane=True
    )


def input_artifact(layout, tmp_path: Path, name: str = "input.md") -> dict:
    source = tmp_path / name
    source.write_text(f"evidence:{name}", encoding="utf-8")
    return register_artifact(
        layout,
        source,
        {
            "type": "research-request",
            "producer": "test",
            "storage": "snapshot",
        },
    )


def write_report(
    task: dict,
    *,
    artifact_ids: list[str] | None = None,
    status: str = "completed",
    frontmatter_overrides: dict | None = None,
    headings: tuple[str, ...] = REPORT_HEADINGS,
) -> Path:
    frontmatter = {
        "task_id": task["task_id"],
        "candidate_id": task["candidate_id"],
        "status": status,
        "artifact_ids": artifact_ids or [],
        "decision_recommendation": "advance",
    }
    if frontmatter_overrides:
        frontmatter.update(frontmatter_overrides)
    body = "\n".join(f"## {heading}\n\ncontent" for heading in headings)
    path = Path(task["report_path"])
    path.write_text(
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def model_call(
    task: dict,
    call_id: str,
    *,
    duration_ms: int,
    tokens: dict | None,
    cost_usd: float | None,
) -> dict:
    return {
        "id": call_id,
        "at": "2026-07-28T00:00:00Z",
        "usage": "task_execution",
        "cycle": task["cycle_id"],
        "task": task["task_id"],
        "candidate": task["candidate_id"],
        "skills": [task["primary_skill"]],
        "model": "provider/model",
        "duration_ms": duration_ms,
        "tokens": tokens,
        "cost_usd": cost_usd,
        "report": task["output_path"],
        "status": "ok",
    }


def mark_subagent_report_ready(layout, task: dict) -> None:
    from autoresearch.worker_events import record_worker_event

    for event_id, kind in (
        ("worker-dispatched", "dispatched"),
        ("worker-report-ready", "report-ready"),
    ):
        record_worker_event(
            layout,
            {
                "event_id": f"{event_id}-{task['task_id']}",
                "kind": kind,
                "cycle_id": task["cycle_id"],
                "task_id": task["task_id"],
                "worker_id": "worker-1",
                "attempt": 1,
                "terminal_status": None,
                "reason": None,
            },
        )


def approve_stage_gate(layout, gate_type: str = "charter") -> None:
    gate = request_gate(
        layout,
        gate_type,
        {"stage_id": read_json(layout.state)["current_stage"]},
    )
    record_gate_receipt(
        layout,
        gate["gate_id"],
        {"source": "test", "evidence": "approved-stage-boundary"},
    )


def require_charter_gate_for_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoresearch.workflow import stages as workflow

    original_capture_graph = workflow._capture_graph

    def capture_graph_with_charter_gate(layout):
        graph, graph_hash = original_capture_graph(layout)
        staged_graph = deepcopy(graph)
        charter = next(
            stage
            for stage in staged_graph["stages"]
            if stage["id"] == "research-charter"
        )
        charter["gate_after"] = True
        charter["gate_type"] = "charter"
        charter["gate_authority"] = "user"
        return staged_graph, graph_hash

    monkeypatch.setattr(workflow, "_capture_graph", capture_graph_with_charter_gate)


def complete_task_in_process(root, task_id, report_path, completed, results):
    try:
        result = complete_task(
            ProjectLayout.from_root(root), task_id, Path(report_path)
        )
    except Exception as error:
        results.put(("completion", "error", str(error)))
    else:
        results.put(("completion", "accepted", result))
    finally:
        completed.set()


def append_task_call_in_process(root, record, completed, results):
    try:
        result = append_model_call(ProjectLayout.from_root(root), record)
    except Exception as error:
        results.put(("late-call", "rejected", str(error)))
    else:
        results.put(("late-call", "accepted", result))
    finally:
        completed.set()


def test_report_requires_frontmatter_and_all_sections(tmp_path: Path):
    report = tmp_path / "report.md"
    report.write_text(
        "---\ntask_id: t1\nstatus: completed\n---\n## Summary\nx\n",
        encoding="utf-8",
    )

    errors = validate_report(report)

    assert "missing section: Evidence" in errors
    assert "missing section: Artifact Links" in errors


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("no frontmatter", "missing YAML frontmatter"),
        ("---\n[]\n---\n", "frontmatter must be a mapping"),
        ("---\n: invalid\n---\n", "invalid YAML frontmatter"),
    ],
)
def test_report_requires_safe_mapping_frontmatter(
    tmp_path: Path, content: str, expected: str
):
    report = tmp_path / "report.md"
    report.write_text(content, encoding="utf-8")

    assert expected in validate_report(report)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"remove": "artifact_ids"}, "missing frontmatter field: artifact_ids"),
        ({"add": ("unknown", "x")}, "unknown frontmatter field: unknown"),
        ({"set": ("status", "done")}, "unknown status: done"),
        ({"set": ("artifact_ids", "artifact-x")}, "artifact_ids must be a list"),
    ],
)
def test_report_frontmatter_is_exact_and_typed(
    tmp_path: Path, mutation: dict, expected: str
):
    frontmatter = {
        "task_id": "task-1",
        "candidate_id": "cand-1",
        "status": "completed",
        "artifact_ids": [],
        "decision_recommendation": "advance",
    }
    if "remove" in mutation:
        del frontmatter[mutation["remove"]]
    if "add" in mutation:
        field, value = mutation["add"]
        frontmatter[field] = value
    if "set" in mutation:
        field, value = mutation["set"]
        frontmatter[field] = value
    report = tmp_path / "report.md"
    body = "\n".join(f"## {heading}\ntext" for heading in REPORT_HEADINGS)
    report.write_text(
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n{body}\n",
        encoding="utf-8",
    )

    assert expected in validate_report(report)


def test_report_rejects_duplicate_yaml_fields_and_headings(tmp_path: Path):
    report = tmp_path / "report.md"
    body = "\n".join(f"## {heading}\ntext" for heading in REPORT_HEADINGS)
    report.write_text(
        "---\n"
        "task_id: task-1\n"
        "task_id: task-2\n"
        "candidate_id: cand-1\n"
        "status: completed\n"
        "artifact_ids: []\n"
        "decision_recommendation: advance\n"
        "---\n"
        f"{body}\n## Evidence\nagain\n",
        encoding="utf-8",
    )

    errors = validate_report(report)

    assert "duplicate frontmatter field: task_id" in errors
    assert "duplicate section: Evidence" in errors


def test_quoted_duplicate_yaml_key_is_rejected(tmp_path: Path):
    report = tmp_path / "report.md"
    body = "\n".join(f"## {heading}\ntext" for heading in REPORT_HEADINGS)
    report.write_text(
        "---\n"
        "task_id: task-1\n"
        '"task_id": task-2\n'
        "candidate_id: cand-1\n"
        "status: completed\n"
        "artifact_ids: []\n"
        "decision_recommendation: advance\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )

    errors = validate_report(report)

    assert "duplicate frontmatter field: task_id" in errors


@pytest.mark.parametrize("status", [[], {}, 1, True])
def test_report_non_string_status_returns_validation_error(
    tmp_path: Path, status
):
    task = {
        "task_id": "task-1",
        "candidate_id": "cand-1",
        "report_path": str(tmp_path / "report.md"),
    }
    report = write_report(
        task, frontmatter_overrides={"status": status}
    )

    errors = validate_report(report)

    assert errors == ["status must be a string"]


def test_report_rejects_unknown_or_out_of_order_sections(tmp_path: Path):
    task = {
        "task_id": "task-1",
        "candidate_id": "cand-1",
        "report_path": str(tmp_path / "report.md"),
    }
    headings = ("Evidence", "Summary", *REPORT_HEADINGS[2:], "Appendix")
    path = write_report(task, headings=headings)

    errors = validate_report(path)

    assert "unknown section: Appendix" in errors
    assert "report sections are out of order" in errors


def test_report_allows_dated_decision_addendum_after_required_sections(
    tmp_path: Path,
):
    task = {
        "task_id": "task-1",
        "candidate_id": "cand-1",
        "report_path": str(tmp_path / "report.md"),
    }
    path = write_report(
        task,
        headings=(*REPORT_HEADINGS, "Decision Addendum — 2026-08-03"),
    )

    assert validate_report(path) == []


def test_report_rejects_decision_addendum_before_required_sections(
    tmp_path: Path,
):
    task = {
        "task_id": "task-1",
        "candidate_id": "cand-1",
        "report_path": str(tmp_path / "report.md"),
    }
    path = write_report(
        task,
        headings=(
            *REPORT_HEADINGS[:-1],
            "Decision Addendum — 2026-08-03",
            "Artifact Links",
        ),
    )

    assert "report sections are out of order" in validate_report(path)


def test_open_task_requires_exact_spec_shape_and_safe_ids(approved_project):
    missing = task_spec()
    del missing["stage_id"]
    with pytest.raises(ValueError, match="missing required field: stage_id"):
        open_task(approved_project, missing)

    unknown = task_spec()
    unknown["priority"] = 1
    with pytest.raises(ValueError, match="unknown field: priority"):
        open_task(approved_project, unknown)

    with pytest.raises(ValueError, match="task_id"):
        open_task(approved_project, task_spec(task_id="../escape"))

    assert read_json(approved_project.budget)["reservations"] == {}


@pytest.mark.parametrize(
    ("work_type", "expected_model"),
    [
        ("simple", "luna"),
        ("planning", "sol"),
        ("thinking", "terra"),
        ("execution", "terra"),
    ],
)
def test_open_task_binds_work_type_to_direct_subagent_model(
    approved_project, monkeypatch: pytest.MonkeyPatch, work_type: str, expected_model: str
):
    from autoresearch.workflow import tasks as workflow

    original = workflow._validate_task_stage_and_limits

    def subagent_stage(*args, **kwargs):
        stage = dict(original(*args, **kwargs))
        stage["task_owner"] = "subagent"
        stage["control_plane"] = False
        return stage

    monkeypatch.setattr(workflow, "_validate_task_stage_and_limits", subagent_stage)
    task = open_task(approved_project, task_spec(work_type=work_type))

    assert task["task_owner"] == "subagent"
    assert task["subagent_model"] == expected_model
    assert f"- Model: `{expected_model}`" in Path(task["brief_path"]).read_text(
        encoding="utf-8"
    )


def test_subagent_report_contract_allows_coordinator_output_backfill(
    approved_project, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from autoresearch.workflow import tasks as workflow

    original = workflow._validate_task_stage_and_limits

    def subagent_stage(*args, **kwargs):
        stage = dict(original(*args, **kwargs))
        stage["task_owner"] = "subagent"
        stage["control_plane"] = False
        return stage

    monkeypatch.setattr(workflow, "_validate_task_stage_and_limits", subagent_stage)
    evidence = input_artifact(approved_project, tmp_path, "worker-input.md")
    task = open_task(
        approved_project,
        task_spec(
            task_id="null",
            candidate_id="true",
            input_artifact_ids=[evidence["artifact_id"]],
            output_path="cycles/cycle-1/tasks/null/report.md",
        ),
    )

    brief = Path(task["brief_path"]).read_text(encoding="utf-8")
    brief_yaml = brief.split("```yaml\n", 1)[1].split("\n```", 1)[0]
    brief_frontmatter = yaml.safe_load("\n".join(brief_yaml.splitlines()[1:-1]))
    assert brief_frontmatter == {
        "task_id": task["task_id"],
        "candidate_id": task["candidate_id"],
        "status": "completed",
        "artifact_ids": [],
        "decision_recommendation": "blocked",
    }
    assert "input_artifact_ids" not in brief_frontmatter

    report = write_report(task)
    frontmatter = yaml.safe_load(report.read_text(encoding="utf-8").split("---")[1])
    assert frontmatter["candidate_id"] == task["candidate_id"]
    assert frontmatter["artifact_ids"] == []
    assert "input_artifact_ids" not in frontmatter
    assert validate_report(report) == []

    output_source = tmp_path / "worker-output.md"
    output_source.write_text("worker output", encoding="utf-8")
    output = register_artifact(
        approved_project,
        output_source,
        {
            "type": "research-request",
            "producer": "test",
            "storage": "snapshot",
            "research_id": task["research_id"],
            "cycle_id": task["cycle_id"],
            "task_id": task["task_id"],
            "candidate_id": task["candidate_id"],
        },
    )
    with pytest.raises(ValueError, match="latest lifecycle event is not report-ready"):
        finalize_task_report(
            approved_project, task["task_id"], [output["artifact_id"]]
        )
    mark_subagent_report_ready(approved_project, task)
    worker_text = report.read_text(encoding="utf-8")
    worker_body = worker_text.split("---", 2)[2]
    worker_frontmatter = yaml.safe_load(worker_text.split("---", 2)[1])
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-worker-return",
            duration_ms=1,
            tokens={"input": 1, "output": 1, "cached": 0, "total": 2},
            cost_usd=0.0,
        ),
    )

    before_hash = sha256_file(report)
    finalized = finalize_task_report(
        approved_project, task["task_id"], [output["artifact_id"]]
    )
    finalized_text = report.read_text(encoding="utf-8")
    finalized_frontmatter = yaml.safe_load(finalized_text.split("---", 2)[1])
    assert finalized_text.split("---", 2)[2] == worker_body
    assert finalized_frontmatter["status"] == worker_frontmatter["status"]
    assert finalized_frontmatter["decision_recommendation"] == (
        worker_frontmatter["decision_recommendation"]
    )
    assert finalized["report_sha256_before"] == before_hash
    assert finalized["report_sha256_after"] != finalized["report_sha256_before"]
    assert validate_report(report) == []

    repeated = finalize_task_report(
        approved_project, task["task_id"], [output["artifact_id"]]
    )
    assert repeated["report_sha256_before"] == finalized["report_sha256_after"]
    assert repeated["report_sha256_after"] == finalized["report_sha256_after"]
    result = complete_task(approved_project, task["task_id"], report)

    assert result["status"] == "completed"
    assert read_json(Path(task["task_record_path"]))["artifact_ids"] == [
        output["artifact_id"]
    ]


def test_finalize_task_report_rejects_report_toctou_change(
    approved_project, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from autoresearch.workflow import tasks as workflow

    original = workflow._validate_task_stage_and_limits

    def subagent_stage(*args, **kwargs):
        stage = dict(original(*args, **kwargs))
        stage["task_owner"] = "subagent"
        stage["control_plane"] = False
        return stage

    monkeypatch.setattr(workflow, "_validate_task_stage_and_limits", subagent_stage)
    task = open_task(approved_project, task_spec())
    report = write_report(task)
    mark_subagent_report_ready(approved_project, task)
    output_source = tmp_path / "toctou-output.md"
    output_source.write_text("toctou output", encoding="utf-8")
    output = register_artifact(
        approved_project,
        output_source,
        {
            "type": "research-request",
            "producer": "test",
            "storage": "snapshot",
            "research_id": task["research_id"],
            "cycle_id": task["cycle_id"],
            "task_id": task["task_id"],
            "candidate_id": task["candidate_id"],
        },
    )
    original_text = report.read_text(encoding="utf-8")
    changed_text = original_text.replace("content", "changed", 1)
    original_sha256_file = workflow.sha256_file
    changed = False

    def mutate_before_hash(path: Path):
        nonlocal changed
        if path.resolve() == report.resolve() and not changed:
            report.write_text(changed_text, encoding="utf-8")
            changed = True
        return original_sha256_file(path)

    monkeypatch.setattr(workflow, "sha256_file", mutate_before_hash)
    with pytest.raises(ValueError, match="report changed during finalization"):
        finalize_task_report(approved_project, task["task_id"], [output["artifact_id"]])
    assert changed is True
    assert report.read_text(encoding="utf-8") == changed_text


def test_open_task_rejects_unknown_work_type_before_budget_reservation(
    approved_project,
):
    with pytest.raises(ValueError, match="work_type"):
        open_task(approved_project, task_spec(work_type="research"))

    assert read_json(approved_project.budget)["reservations"] == {}


def test_subagent_task_requires_both_active_envelopes(
    project_layout, monkeypatch: pytest.MonkeyPatch
):
    from autoresearch.workflow import tasks as workflow

    original = workflow._validate_task_stage_and_limits

    def subagent_stage(*args, **kwargs):
        stage = dict(original(*args, **kwargs))
        stage["task_owner"] = "subagent"
        stage["control_plane"] = False
        return stage

    monkeypatch.setattr(workflow, "_validate_task_stage_and_limits", subagent_stage)
    with pytest.raises(PermissionError, match="modification.*budget"):
        open_task(project_layout, task_spec())

    assert not (
        project_layout.cycles / "cycle-1" / "tasks" / "task-1"
    ).exists()
    assert read_json(project_layout.budget)["reservations"] == {}


def test_non_control_plane_coordinator_requires_execution_approval(
    project_layout, monkeypatch: pytest.MonkeyPatch
):
    from autoresearch.workflow import tasks as workflow

    original = workflow._validate_task_stage_and_limits

    def budgeted_coordinator_stage(*args, **kwargs):
        stage = dict(original(*args, **kwargs))
        stage["control_plane"] = False
        return stage

    monkeypatch.setattr(
        workflow, "_validate_task_stage_and_limits", budgeted_coordinator_stage
    )
    with pytest.raises(PermissionError, match="modification.*budget"):
        open_task(project_layout, task_spec())


def test_control_plane_coordinator_task_opens_before_execution_approval(
    project_layout, tmp_path: Path
):
    task = open_task(project_layout, task_spec())
    output = input_artifact(project_layout, tmp_path)
    report = write_report(task, artifact_ids=[output["artifact_id"]])

    result = complete_task(project_layout, task["task_id"], report)

    assert task["task_owner"] == "coordinator"
    assert task["reservation_id"] is None
    assert read_json(project_layout.budget)["reservations"] == {}
    assert result["status"] == "completed"
    assert result["usage"] == {
        "cost_usd": None,
        "tokens": None,
        "duration_ms": 0,
    }


def test_approved_control_plane_coordinator_never_reserves_or_needs_usage(
    approved_project, tmp_path: Path, capsys
):
    assert read_json(approved_project.state)["current_stage"] == "intake"
    task = open_task(approved_project, task_spec())
    output = input_artifact(approved_project, tmp_path)
    report = write_report(task, artifact_ids=[output["artifact_id"]])

    assert main(
        [
            "task-complete",
            "--root",
            str(approved_project.root),
            "--task-id",
            task["task_id"],
            "--report",
            str(report),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert task["stage_id"] == "intake"
    assert task["task_owner"] == "coordinator"
    assert task["subagent_model"] is None
    assert task["reservation_id"] is None
    assert read_json(approved_project.budget)["reservations"] == {}
    assert read_jsonl(approved_project.model_calls) == []
    assert read_jsonl(approved_project.worker_events) == []
    assert read_jsonl(approved_project.budget_settlements) == []
    assert result["status"] == "completed"
    assert result["receipt"]["record_type"] == "task-completion"
    assert result["usage"] == {
        "cost_usd": None,
        "tokens": None,
        "duration_ms": 0,
    }
    assert read_json(Path(task["task_record_path"]))["status"] == "completed"


def test_budgeted_coordinator_completion_settles_wall_without_provider_usage(
    approved_project, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from autoresearch.workflow import tasks as workflow

    original = workflow._validate_task_stage_and_limits

    def budgeted_coordinator_stage(*args, **kwargs):
        stage = dict(original(*args, **kwargs))
        stage["control_plane"] = False
        return stage

    monkeypatch.setattr(
        workflow, "_validate_task_stage_and_limits", budgeted_coordinator_stage
    )
    task = open_task(approved_project, task_spec())
    output = input_artifact(approved_project, tmp_path)
    report = write_report(task, artifact_ids=[output["artifact_id"]])
    result = complete_task(approved_project, task["task_id"], report)

    assert result["status"] == "completed"
    assert result["usage"] == {
        "cost_usd": None,
        "tokens": None,
        "duration_ms": 0,
    }
    budget = read_json(approved_project.budget)
    assert task["reservation_id"] not in budget["reservations"]
    assert budget["consumed"]["wall_seconds"] == 0
    assert read_json(Path(task["task_record_path"]))["status"] == "completed"


def test_validate_preflights_the_active_graph_skills(approved_project):
    graph = yaml.safe_load(approved_project.graph.read_text(encoding="utf-8"))
    graph["stages"][0]["primary_skill"] = "missing-test-skill"
    approved_project.graph.write_text(
        yaml.safe_dump(graph, sort_keys=False), encoding="utf-8"
    )

    assert validate_project_integrity(approved_project) == [
        "SKILL.md not found for skill missing-test-skill"
    ]


def test_graph_revision_request_preflights_the_target_graph(
    approved_project, tmp_path: Path
):
    graph = yaml.safe_load(approved_project.graph.read_text(encoding="utf-8"))
    graph["stages"][0]["primary_skill"] = "missing-test-skill"
    replacement = tmp_path / "missing-skill.yaml"
    replacement.write_text(
        yaml.safe_dump(graph, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(FileNotFoundError, match="SKILL.md not found"):
        request_graph_revision(approved_project, replacement, "test preflight")


def test_open_task_rejects_unsafe_output_and_editable_symlink(
    approved_project, tmp_path: Path
):
    with pytest.raises(ValueError, match="output_path"):
        open_task(
            approved_project,
            task_spec(output_path="../../outside/report.md"),
        )
    with pytest.raises(ValueError, match="output_path"):
        open_task(
            approved_project,
            task_spec(
                output_path="cycles//cycle-1/tasks/task-1/report.md"
            ),
        )

    outside = tmp_path / "outside"
    outside.mkdir()
    symlink = approved_project.root / "linked"
    symlink.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="editable_paths"):
        open_task(
            approved_project,
            task_spec(task_id="task-2", editable_paths=["linked/file.py"]),
        )

    assert read_json(approved_project.budget)["reservations"] == {}


def test_editable_path_envelope_escape_is_rejected(approved_project):
    active = read_json(
        approved_project.root
        / ".research"
        / "modification-envelope.json"
    )
    payload = deepcopy(active["payload"])
    allowed = approved_project.root / "approved-editable"
    allowed.mkdir()
    append_only = approved_project.root / "append-only"
    append_only.mkdir()
    read_only = approved_project.root / "read-only"
    read_only.mkdir()
    payload["editable_paths"] = [str(allowed)]
    payload["append_only_paths"] = [str(append_only)]
    payload["read_only_paths"] = [str(read_only)]
    gate = request_gate(approved_project, "modification", payload)
    record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "narrow-editable-envelope"},
    )

    accepted = open_task(
        approved_project,
        task_spec(
            task_id="accepted",
            candidate_id="cand-accepted",
            editable_paths=["approved-editable/model.py"],
            output_path=(
                "cycles/cycle-1/tasks/accepted/report.md"
            ),
        ),
    )
    assert accepted["editable_paths"] == [
        str((allowed / "model.py").resolve())
    ]

    for task_id, editable_path in (
        ("append-only-task", "append-only/results.jsonl"),
        ("read-only-task", "read-only/dataset.csv"),
        ("outside-task", "outside/model.py"),
    ):
        with pytest.raises(PermissionError, match="editable.*Envelope"):
            open_task(
                approved_project,
                task_spec(
                    task_id=task_id,
                    candidate_id=f"cand-{task_id}",
                    editable_paths=[editable_path],
                    output_path=(
                        f"cycles/cycle-1/tasks/{task_id}/report.md"
                    ),
                ),
            )
        assert not (
            approved_project.cycles
            / "cycle-1"
            / "tasks"
            / task_id
        ).exists()


def test_open_task_rejects_task_directory_symlink_escape(
    approved_project, tmp_path: Path
):
    cycle = approved_project.cycles / "cycle-1"
    cycle.mkdir()
    outside = tmp_path / "outside-tasks"
    outside.mkdir()
    (cycle / "tasks").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="output_path|task directory"):
        open_task(approved_project, task_spec())

    assert list(outside.iterdir()) == []
    assert read_json(approved_project.budget)["reservations"] == {}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "capability_skills": [
                    "statistical-analysis",
                    "scientific-writing",
                    "citation-management",
                ]
            },
            "more than two",
        ),
        (
            {
                "primary_skill": "statistical-analysis",
                "capability_skills": ["statistical-analysis"],
            },
            "primary skill",
        ),
        (
            {
                "capability_skills": [
                    "statistical-analysis",
                    "statistical-analysis",
                ]
            },
            "duplicate capability",
        ),
    ],
)
def test_open_task_enforces_capability_limits(
    approved_project, overrides: dict, message: str
):
    with pytest.raises(ValueError, match=message):
        open_task(approved_project, task_spec(**overrides))


def test_open_task_requires_active_stage_contract(approved_project):
    rejected = (
        (
            task_spec(primary_skill="test-driven-development"),
            "primary_skill does not match active stage",
        ),
        (
            task_spec(
                task_id="future-stage",
                stage_id="research-charter",
                primary_skill="research-hypothesis-generation",
                output_path=(
                    "cycles/cycle-1/tasks/future-stage/report.md"
                ),
            ),
            "stage_id does not match current stage",
        ),
        (
            task_spec(
                task_id="undeclared-capability",
                primary_skill="autoresearch-loop",
                capability_skills=["statistical-analysis"],
                output_path=(
                    "cycles/cycle-1/tasks/undeclared-capability/report.md"
                ),
            ),
            "capability_skills are not declared by active stage",
        ),
    )
    for spec, message in rejected:
        with pytest.raises(ValueError, match=message):
            open_task(approved_project, spec)
        reservation_id = (
            f"task:{spec['cycle_id']}:{spec['task_id']}"
        )
        assert reservation_id not in read_json(approved_project.budget)[
            "reservations"
        ]

    first = open_task(
        approved_project,
        task_spec(primary_skill="autoresearch-loop"),
    )
    with pytest.raises(ValueError, match="stage is not parallelizable"):
        open_task(
            approved_project,
            task_spec(
                task_id="parallel-intake",
                primary_skill="autoresearch-loop",
                candidate_id="cand-2",
                output_path=(
                    "cycles/cycle-1/tasks/parallel-intake/report.md"
                ),
            ),
        )
    assert first["status"] == "open"


def test_open_task_requires_active_stage_input_artifact_types(
    approved_project,
):
    transition = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )
    assert transition.status == "applied"

    with pytest.raises(
        ValueError,
        match="active stage input artifact is missing: research-request",
    ):
        open_task(
            approved_project,
            task_spec(
                stage_id="research-charter",
                primary_skill="autoresearch-loop",
            ),
        )
    assert read_json(approved_project.budget)["reservations"] == {}


def test_open_task_requires_available_valid_input_artifacts(
    approved_project, tmp_path: Path
):
    with pytest.raises(ValueError, match="unknown artifact"):
        open_task(
            approved_project,
            task_spec(input_artifact_ids=["artifact-missing"]),
        )

    artifact = input_artifact(approved_project, tmp_path)
    Path(artifact["path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid input artifact"):
        open_task(
            approved_project,
            task_spec(input_artifact_ids=[artifact["artifact_id"]]),
        )

    assert read_json(approved_project.budget)["reservations"] == {}


def test_open_task_writes_exact_record_brief_manifest_for_control_plane(
    approved_project, tmp_path: Path
):
    selected = input_artifact(approved_project, tmp_path, "selected.md")
    excluded = input_artifact(approved_project, tmp_path, "excluded.md")
    supplied = task_spec(
        input_artifact_ids=[selected["artifact_id"]],
        editable_paths=["src/model.py"],
    )

    record = open_task(approved_project, supplied)

    normalized = deepcopy(supplied)
    normalized["editable_paths"] = [
        str((approved_project.root / "src" / "model.py").resolve())
    ]
    assert record["task_spec"] == normalized
    assert record["brief_path"] == str(
        (
            approved_project.cycles
            / "cycle-1"
            / "tasks"
            / "task-1"
            / "brief.md"
        ).resolve()
    )
    assert record["report_path"] == str(
        (
            approved_project.cycles
            / "cycle-1"
            / "tasks"
            / "task-1"
            / "report.md"
        ).resolve()
    )
    assert record["reservation_id"] is None
    assert record["status"] == "open"
    assert record["context_manifest"] == {
        "input_artifacts": [
            {
                field: selected[field]
                for field in (
                    "artifact_id",
                    "path",
                    "type",
                    "sha256",
                    "size_bytes",
                    "availability",
                )
            }
        ]
    }
    brief = Path(record["brief_path"]).read_text(encoding="utf-8")
    assert json.dumps(normalized, ensure_ascii=False, indent=2) in brief
    assert "control-plane Coordinator tasks never reserve budget" in brief
    assert selected["artifact_id"] in brief
    assert excluded["artifact_id"] not in brief
    budget = read_json(approved_project.budget)
    assert budget["reservations"] == {}
    assert record["task_owner"] == "coordinator"
    assert record["subagent_model"] is None


def test_coordinator_task_has_no_worker_model_or_worker_slot(approved_project):
    from autoresearch.worker_events import record_worker_event

    task = open_task(approved_project, task_spec())

    assert task["task_owner"] == "coordinator"
    assert task["subagent_model"] is None
    assert task["reservation_id"] is None
    assert read_json(approved_project.budget)["reservations"] == {}
    assert "no subagent is dispatched" in Path(task["brief_path"]).read_text(
        encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Coordinator-owned"):
        record_worker_event(
            approved_project,
            {
                "event_id": "coordinator-dispatch",
                "kind": "dispatched",
                "cycle_id": task["cycle_id"],
                "task_id": task["task_id"],
                "worker_id": "worker-1",
                "attempt": 1,
                "terminal_status": None,
                "reason": None,
            },
        )


def test_open_task_rolls_back_reservation_and_directory_on_write_failure(
    approved_project, monkeypatch
):
    from autoresearch.workflow import tasks as workflow

    def fail_write(path: Path, content: str) -> None:
        raise OSError("simulated brief write failure")

    monkeypatch.setattr(workflow, "_atomic_write_text", fail_write)

    with pytest.raises(OSError, match="simulated"):
        open_task(approved_project, task_spec())

    assert read_json(approved_project.budget)["reservations"] == {}
    assert not (
        approved_project.cycles / "cycle-1" / "tasks" / "task-1"
    ).exists()


def test_complete_task_rejects_identity_and_invalid_artifacts_before_mutation(
    approved_project
):
    task = open_task(approved_project, task_spec())
    report = write_report(
        task,
        artifact_ids=["artifact-missing"],
        frontmatter_overrides={"task_id": "different"},
    )
    before_index = approved_project.artifact_index.read_text(encoding="utf-8")
    before_journal = approved_project.journal.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="task_id"):
        complete_task(approved_project, "task-1", report)

    assert approved_project.artifact_index.read_text(encoding="utf-8") == before_index
    assert approved_project.journal.read_text(encoding="utf-8") == before_journal
    assert read_json(approved_project.budget)["reservations"] == {}


def test_complete_task_rejects_unknown_report_artifact_before_snapshot(
    approved_project
):
    task = open_task(approved_project, task_spec())
    report = write_report(task, artifact_ids=["artifact-missing"])
    before_index = approved_project.artifact_index.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="unknown artifact"):
        complete_task(approved_project, task["task_id"], report)

    assert approved_project.artifact_index.read_text(encoding="utf-8") == before_index
    assert read_json(approved_project.budget)["reservations"] == {}


def test_complete_task_snapshots_report_appends_receipt_and_settles_usage(
    approved_project, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from autoresearch.workflow import tasks as workflow

    original = workflow._validate_task_stage_and_limits

    def budgeted_coordinator_stage(*args, **kwargs):
        stage = dict(original(*args, **kwargs))
        stage["control_plane"] = False
        return stage

    monkeypatch.setattr(
        workflow, "_validate_task_stage_and_limits", budgeted_coordinator_stage
    )
    evidence = input_artifact(approved_project, tmp_path)
    task = open_task(
        approved_project,
        task_spec(input_artifact_ids=[evidence["artifact_id"]]),
    )
    report = write_report(task, artifact_ids=[evidence["artifact_id"]])
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-1",
            duration_ms=1500,
            tokens={"input": 10, "output": 5, "cached": 2, "total": 15},
            cost_usd=0.1,
        ),
    )
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-2",
            duration_ms=700,
            tokens={"input": 20, "output": 10, "cached": 3, "total": 30},
            cost_usd=0.2,
        ),
    )

    result = complete_task(approved_project, task["task_id"], report)

    assert result["status"] == "completed"
    assert result["usage"] == {
        "cost_usd": pytest.approx(0.3),
        "tokens": {"input": 30, "output": 15, "cached": 5, "total": 45},
        "duration_ms": 2200,
    }
    report_artifact = resolve_artifact(
        approved_project, result["report_artifact_id"]
    )
    assert report_artifact["type"] == "task-report"
    assert report_artifact["storage"] == "snapshot"
    assert report_artifact["task_id"] == task["task_id"]
    assert Path(report_artifact["path"]).read_bytes() == report.read_bytes()
    report.write_text("mutated after completion", encoding="utf-8")
    assert verify_artifact(
        approved_project, result["report_artifact_id"]
    )["valid"] is True
    receipt = read_jsonl(approved_project.journal)[-1]
    assert receipt == result["receipt"]
    assert receipt["record_type"] == "task-completion"
    assert receipt["report_artifact_id"] == result["report_artifact_id"]
    assert read_json(Path(task["task_record_path"]))["status"] == "completed"
    budget = read_json(approved_project.budget)
    assert task["reservation_id"] not in budget["reservations"]
    assert budget["consumed"]["wall_seconds"] == pytest.approx(2.2)


def test_completion_registers_declared_stage_outputs(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    task = open_budgeted_coordinator_task(
        approved_project,
        monkeypatch,
        task_spec(),
        retain_outputs=True,
    )
    output_source = approved_project.root / "research-request.md"
    output_source.write_text("normalized request", encoding="utf-8")
    output = register_artifact(
        approved_project,
        output_source,
        {
            "type": "research-request",
            "producer": "test",
            "storage": "snapshot",
        },
    )
    report = write_report(task, artifact_ids=[output["artifact_id"]])
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-declared-output",
            duration_ms=1,
            tokens={"input": 1, "output": 1, "cached": 0, "total": 2},
            cost_usd=0.0,
        ),
    )

    result = complete_task(approved_project, task["task_id"], report)
    completed = read_json(Path(task["task_record_path"]))
    registered_output = resolve_artifact(
        approved_project, completed["artifact_ids"][0]
    )

    assert result["status"] == "completed"
    assert completed["artifact_ids"] == [output["artifact_id"]]
    assert registered_output["type"] == "research-request"
    assert Path(registered_output["path"]).read_text(encoding="utf-8") == (
        "normalized request"
    )
    assert transition_stage(
        approved_project, "intake", "paper-analysis", expected_revision=0
    ).status == "applied"


@pytest.mark.parametrize("artifact_type", [None, "paper-analysis-report"])
def test_completion_requires_every_declared_stage_output(
    approved_project, tmp_path: Path, artifact_type: str | None
):
    task = open_task(approved_project, task_spec())
    artifact_ids: list[str] = []
    if artifact_type is not None:
        source = tmp_path / "wrong-output.md"
        source.write_text("wrong output", encoding="utf-8")
        artifact = register_artifact(
            approved_project,
            source,
            {
                "type": artifact_type,
                "producer": "test",
                "storage": "snapshot",
            },
        )
        artifact_ids = [artifact["artifact_id"]]
    report = write_report(task, artifact_ids=artifact_ids)
    append_model_call(
        approved_project,
        model_call(
            task,
            f"call-missing-output-{artifact_type or 'none'}",
            duration_ms=1,
            tokens={"input": 1, "output": 1, "cached": 0, "total": 2},
            cost_usd=0.0,
        ),
    )

    with pytest.raises(
        ValueError, match="missing declared stage output artifact: research-request"
    ):
        complete_task(approved_project, task["task_id"], report)


def test_subagent_completion_does_not_require_provider_usage(
    approved_project, monkeypatch, tmp_path: Path
):
    from autoresearch.workflow import tasks as workflow

    original = workflow._validate_task_stage_and_limits

    def subagent_stage(*args, **kwargs):
        stage = dict(original(*args, **kwargs))
        stage["task_owner"] = "subagent"
        stage["control_plane"] = False
        return stage

    monkeypatch.setattr(workflow, "_validate_task_stage_and_limits", subagent_stage)
    task = open_task(approved_project, task_spec())
    output_source = tmp_path / "subagent-output.md"
    output_source.write_text("subagent output", encoding="utf-8")
    output = register_artifact(
        approved_project,
        output_source,
        {
            "type": "research-request",
            "producer": f"task:{task['task_id']}",
            "storage": "snapshot",
            "research_id": task["research_id"],
            "cycle_id": task["cycle_id"],
            "task_id": task["task_id"],
            "candidate_id": task["candidate_id"],
        },
    )
    report = write_report(task, artifact_ids=[output["artifact_id"]])

    result = complete_task(approved_project, task["task_id"], report)

    assert result["status"] == "completed"
    assert task["reservation_id"] not in read_json(approved_project.budget)[
        "reservations"
    ]


def test_task_completion_rejects_usage_above_its_budget_slice(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-over-slice",
            duration_ms=300_001,
            tokens={"input": 1, "output": 1, "cached": 0, "total": 2},
            cost_usd=1.01,
        ),
    )

    with pytest.raises(ValueError, match="exceeds Budget Slice"):
        complete_task(approved_project, task["task_id"], report)


def test_completion_orphaned_settlement_is_owner_bound_and_recoverable(
    approved_project,
    monkeypatch: pytest.MonkeyPatch,
):
    from autoresearch.workflow import tasks as workflow

    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec()
    )
    output_source = approved_project.root / "research-request.md"
    output_source.write_text("normalized request", encoding="utf-8")
    output = register_artifact(
        approved_project,
        output_source,
        {
            "type": "research-request",
            "producer": "test",
            "storage": "snapshot",
        },
    )
    report = write_report(task, artifact_ids=[output["artifact_id"]])
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-orphaned-settlement",
            duration_ms=250,
            tokens={"input": 8, "output": 2, "cached": 1, "total": 10},
            cost_usd=0.2,
        ),
    )
    workflow_transactions = (
        approved_project.root / ".research" / "workflow-transactions"
    )
    original_atomic_write_json = workflow.atomic_write_json

    def fail_before_completion_intent(path: Path, value: dict) -> None:
        if (
            path.parent == workflow_transactions
            and value.get("kind") == "task-completion"
            and value.get("phase") == "prepared"
        ):
            raise OSError("injected after settlement plan")
        original_atomic_write_json(path, value)

    monkeypatch.setattr(
        workflow, "atomic_write_json", fail_before_completion_intent
    )
    with pytest.raises(OSError, match="after settlement plan"):
        complete_task(approved_project, task["task_id"], report)
    monkeypatch.setattr(
        workflow, "atomic_write_json", original_atomic_write_json
    )

    assert list(workflow_transactions.glob("*.json")) == []
    settlement_path = next(
        approved_project.budget_transactions.glob("*.json")
    )
    settlement_before = read_json(settlement_path)
    assert settlement_before["phase"] == "prepared"
    assert settlement_before["owner_transaction_id"].startswith(
        "task-completion-"
    )
    budget_before = read_json(approved_project.budget)
    settlements_before = read_jsonl(approved_project.budget_settlements)
    task_before = read_json(Path(task["task_record_path"]))

    restarted = ProjectLayout.from_root(approved_project.root)
    with pytest.raises(ValueError, match="owning workflow"):
        reserve_budget(restarted, {"id": "unrelated", "wall_seconds": 1.0})
    assert read_json(restarted.budget) == budget_before
    assert read_json(settlement_path) == settlement_before
    assert read_jsonl(restarted.budget_settlements) == settlements_before
    assert read_json(Path(task["task_record_path"])) == task_before

    expanded = {
        "schema_version": 1,
        "limits": {
            **budget_before["limits"],
            "wall_seconds": 7_200,
        },
        "storage_accounting": budget_before["storage_accounting"],
    }
    gate = request_gate(restarted, "budget", expanded)
    with pytest.raises(ValueError, match="owning workflow"):
        record_gate_receipt(
            restarted,
            gate["gate_id"],
            {"source": "test", "evidence": "must-not-steal-owner"},
        )
    assert read_json(restarted.budget) == budget_before
    assert read_json(settlement_path) == settlement_before
    assert read_jsonl(restarted.budget_settlements) == settlements_before
    assert read_json(Path(task["task_record_path"])) == task_before

    recovered = complete_task(restarted, task["task_id"], report)
    repeated = complete_task(restarted, task["task_id"], report)

    assert repeated == recovered
    assert (
        recovered["receipt"]["transaction_id"]
        == settlement_before["owner_transaction_id"]
    )
    budget_after = read_json(restarted.budget)
    assert budget_after["consumed"]["wall_seconds"] == pytest.approx(0.25)
    assert len(read_jsonl(restarted.budget_settlements)) == 1
    assert read_json(settlement_path)["phase"] == "committed"
    assert read_json(Path(task["task_record_path"]))["status"] == "completed"


@pytest.mark.parametrize(
    "failure_phase",
    [
        "prepared",
        "artifact_registered",
        "settlement_confirmed",
        "receipt_appended",
        "task_state_written",
        "committed",
    ],
)
def test_task_bound_model_call_is_frozen_at_every_completion_phase(
    approved_project,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
):
    from autoresearch.workflow import tasks as workflow

    task = open_budgeted_coordinator_task(
        approved_project,
        monkeypatch,
        task_spec(task_id=f"freeze-{failure_phase}"),
    )
    report = write_report(task)
    accepted_call = model_call(
        task,
        f"call-before-{failure_phase}",
        duration_ms=250,
        tokens={"input": 8, "output": 2, "cached": 1, "total": 10},
        cost_usd=0.2,
    )
    append_model_call(approved_project, accepted_call)
    original_atomic_write_json = workflow.atomic_write_json
    workflow_transactions = (
        approved_project.root / ".research" / "workflow-transactions"
    )

    def fail_after_phase(path: Path, value: dict) -> None:
        original_atomic_write_json(path, value)
        if (
            path.parent == workflow_transactions
            and value.get("kind") == "task-completion"
            and value.get("phase") == failure_phase
        ):
            raise OSError(f"injected after {failure_phase}")

    monkeypatch.setattr(workflow, "atomic_write_json", fail_after_phase)
    with pytest.raises(OSError, match=failure_phase):
        complete_task(approved_project, task["task_id"], report)
    monkeypatch.setattr(
        workflow, "atomic_write_json", original_atomic_write_json
    )

    calls_before = read_jsonl(approved_project.model_calls)
    budget_before = read_json(approved_project.budget)
    with pytest.raises(ValueError, match="completion|open"):
        append_model_call(
            ProjectLayout.from_root(approved_project.root),
            model_call(
                task,
                f"call-late-{failure_phase}",
                duration_ms=500,
                tokens={
                    "input": 16,
                    "output": 4,
                    "cached": 2,
                    "total": 20,
                },
                cost_usd=0.4,
            ),
        )
    assert read_jsonl(approved_project.model_calls) == calls_before
    assert read_json(approved_project.budget) == budget_before

    restarted = ProjectLayout.from_root(approved_project.root)
    recovered = complete_task(restarted, task["task_id"], report)
    budget_after = read_json(restarted.budget)
    repeated = complete_task(restarted, task["task_id"], report)

    assert repeated == recovered
    assert recovered["usage"]["cost_usd"] == pytest.approx(0.2)
    assert recovered["usage"]["tokens"]["total"] == 10
    assert read_jsonl(restarted.model_calls) == [accepted_call]
    assert read_json(restarted.budget) == budget_after


def test_completion_first_blocks_then_rejects_concurrent_task_call(
    approved_project,
    monkeypatch: pytest.MonkeyPatch,
):
    from autoresearch import records
    from autoresearch.workflow import tasks as workflow

    task = open_budgeted_coordinator_task(
        approved_project,
        monkeypatch,
        task_spec(task_id="concurrent-freeze"),
    )
    report = write_report(task)
    accepted_call = model_call(
        task,
        "call-before-concurrent-freeze",
        duration_ms=250,
        tokens={"input": 8, "output": 2, "cached": 1, "total": 10},
        cost_usd=0.2,
    )
    append_model_call(approved_project, accepted_call)
    late_call = model_call(
        task,
        "call-late-concurrent-freeze",
        duration_ms=500,
        tokens={"input": 16, "output": 4, "cached": 2, "total": 20},
        cost_usd=0.4,
    )
    context = multiprocessing.get_context("fork")
    completion_holds_lock = context.Event()
    release_completion = context.Event()
    completion_done = context.Event()
    late_call_done = context.Event()
    results = context.Queue()
    lock_observations = context.Queue()
    original_ensure_artifact = workflow._ensure_completion_artifact
    original_records_lock = records._exclusive_lock
    workflow_lock = (
        approved_project.root / ".research" / "workflow.lock"
    )

    def pause_completion(*args, **kwargs):
        if multiprocessing.current_process().name == "task-completion":
            completion_holds_lock.set()
            if not release_completion.wait(timeout=5):
                raise TimeoutError("test did not release completion")
        return original_ensure_artifact(*args, **kwargs)

    @contextmanager
    def observe_late_call_lock(path):
        if multiprocessing.current_process().name == "late-model-call":
            with path.open("a+", encoding="utf-8") as probe:
                try:
                    fcntl.flock(
                        probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                except BlockingIOError:
                    lock_observations.put(("blocked", str(path)))
                else:
                    fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
                    lock_observations.put(("acquired", str(path)))
                    raise RuntimeError(
                        "stable workflow lock was unexpectedly available"
                    )
        with original_records_lock(path):
            yield

    monkeypatch.setattr(
        workflow, "_ensure_completion_artifact", pause_completion
    )
    monkeypatch.setattr(records, "_exclusive_lock", observe_late_call_lock)
    completion = context.Process(
        name="task-completion",
        target=complete_task_in_process,
        args=(
            approved_project.root,
            task["task_id"],
            report,
            completion_done,
            results,
        ),
    )
    late = context.Process(
        name="late-model-call",
        target=append_task_call_in_process,
        args=(
            approved_project.root,
            late_call,
            late_call_done,
            results,
        ),
    )
    started_processes = []
    try:
        completion.start()
        started_processes.append(completion)
        assert completion_holds_lock.wait(timeout=5)
        late.start()
        started_processes.append(late)
        assert lock_observations.get(timeout=5) == (
            "blocked",
            str(workflow_lock),
        )
        assert not late_call_done.is_set()
        release_completion.set()
    finally:
        release_completion.set()
        for process in started_processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert completion.exitcode == 0
    assert late.exitcode == 0
    assert completion_done.is_set()
    assert late_call_done.is_set()
    outcomes = {
        actor: (status, detail)
        for actor, status, detail in (
            results.get(timeout=1),
            results.get(timeout=1),
        )
    }
    assert outcomes["completion"][0] == "accepted"
    assert outcomes["late-call"][0] == "rejected"
    assert "completion" in outcomes["late-call"][1] or "open" in outcomes[
        "late-call"
    ][1]
    assert read_jsonl(approved_project.model_calls) == [accepted_call]
    assert read_json(approved_project.budget)["consumed"][
        "wall_seconds"
    ] == pytest.approx(0.25)


def test_committed_completion_retry_uses_historical_settlement_after_later_usage(
    approved_project,
    monkeypatch: pytest.MonkeyPatch,
):
    first = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec(task_id="first")
    )
    first_report = write_report(first)
    append_model_call(
        approved_project,
        model_call(
            first,
            "call-first",
            duration_ms=100,
            tokens={"input": 4, "output": 1, "cached": 0, "total": 5},
            cost_usd=0.1,
        ),
    )
    first_result = complete_task(
        approved_project, first["task_id"], first_report
    )

    second = open_budgeted_coordinator_task(
        approved_project,
        monkeypatch,
        task_spec(
            task_id="second",
            candidate_id="cand-2",
            output_path="cycles/cycle-1/tasks/second/report.md",
        ),
    )
    second_report = write_report(second)
    append_model_call(
        approved_project,
        model_call(
            second,
            "call-second",
            duration_ms=200,
            tokens={"input": 8, "output": 2, "cached": 0, "total": 10},
            cost_usd=0.2,
        ),
    )
    complete_task(approved_project, second["task_id"], second_report)
    expanded = {
        "schema_version": 1,
        "limits": {
            "wall_seconds": 7200,
            "cpu_seconds": 7200,
            "gpu_type": None,
            "gpu_seconds": 0,
            "storage_bytes": 2_000_000_000,
            "official_candidates": 10,
            "loop-subagent-bound": 5,
            "retries": 4,
            "debug_candidates": 4,
        },
        "storage_accounting": read_json(approved_project.budget)[
            "storage_accounting"
        ],
    }
    gate = request_gate(approved_project, "budget", expanded)
    record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "later-budget-approval"},
    )
    first_report.unlink()
    observed_paths = [
        approved_project.budget,
        approved_project.budget_settlements,
        approved_project.gates,
        approved_project.journal,
        approved_project.artifact_index,
        *sorted(approved_project.budget_transactions.glob("*.json")),
        *sorted(
            (
                approved_project.root
                / ".research"
                / "workflow-transactions"
            ).glob("*.json")
        ),
    ]
    before = {path: path.read_bytes() for path in observed_paths}

    retried = complete_task(
        ProjectLayout.from_root(approved_project.root),
        first["task_id"],
        first_report,
    )

    assert retried == first_result
    assert {path: path.read_bytes() for path in observed_paths} == before


def test_durable_workflow_recovery_after_budget_settlement(
    approved_project, monkeypatch
):
    from autoresearch.workflow import tasks as workflow

    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-settlement-recovery",
            duration_ms=250,
            tokens={"input": 8, "output": 2, "cached": 1, "total": 10},
            cost_usd=0.2,
        ),
    )
    original_append = workflow.append_jsonl_locked

    def fail_completion_receipt(path: Path, value: dict) -> None:
        if path == approved_project.journal:
            raise OSError("injected failure after settlement")
        original_append(path, value)

    monkeypatch.setattr(
        workflow, "append_jsonl_locked", fail_completion_receipt
    )
    with pytest.raises(OSError, match="after settlement"):
        complete_task(approved_project, task["task_id"], report)

    budget_after_failure = read_json(approved_project.budget)
    artifact_rows_after_failure = read_jsonl(
        approved_project.artifact_index
    )
    assert task["reservation_id"] not in budget_after_failure["reservations"]
    assert len(artifact_rows_after_failure) == 1
    assert read_jsonl(approved_project.journal) == []

    monkeypatch.setattr(
        workflow, "append_jsonl_locked", original_append
    )
    restarted = ProjectLayout.from_root(approved_project.root)
    recovered = complete_task(restarted, task["task_id"], report)
    recovered_again = complete_task(restarted, task["task_id"], report)

    assert recovered_again == recovered
    assert read_json(restarted.budget) == budget_after_failure
    assert read_jsonl(restarted.artifact_index) == artifact_rows_after_failure
    assert read_jsonl(restarted.journal) == [recovered["receipt"]]
    assert read_json(Path(task["task_record_path"]))["status"] == "completed"


def test_completion_recovery_reuses_snapshot_before_artifact_index(
    approved_project, monkeypatch
):
    from autoresearch.workflow import tasks as workflow

    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec(), retain_outputs=True
    )
    output_source = approved_project.root / "research-request.md"
    output_source.write_text("normalized request", encoding="utf-8")
    output = register_artifact(
        approved_project,
        output_source,
        {
            "type": "research-request",
            "producer": "test",
            "storage": "snapshot",
        },
    )
    report = write_report(task, artifact_ids=[output["artifact_id"]])
    initial_artifact_rows = read_jsonl(approved_project.artifact_index)
    initial_snapshot_directories = sorted(
        path
        for path in approved_project.artifact_index.parent.iterdir()
        if path.is_dir()
    )
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-recovery-snapshot",
            duration_ms=1,
            tokens={"input": 1, "output": 1, "cached": 0, "total": 2},
            cost_usd=0.0,
        ),
    )
    original_append = workflow.append_jsonl_locked

    def fail_artifact_index(path: Path, value: dict) -> None:
        if path == approved_project.artifact_index:
            raise OSError("injected failure after task-report snapshot")
        original_append(path, value)

    monkeypatch.setattr(
        workflow, "append_jsonl_locked", fail_artifact_index
    )
    with pytest.raises(OSError, match="after task-report snapshot"):
        complete_task(approved_project, task["task_id"], report)

    snapshot_directories = sorted(
        path
        for path in approved_project.artifact_index.parent.iterdir()
        if path.is_dir()
    )
    assert len(snapshot_directories) == len(initial_snapshot_directories) + 1
    assert read_jsonl(approved_project.artifact_index) == initial_artifact_rows

    monkeypatch.setattr(workflow, "append_jsonl_locked", original_append)
    report.unlink()
    restarted = ProjectLayout.from_root(approved_project.root)
    recovered = complete_task(restarted, task["task_id"], report)

    assert len(read_jsonl(restarted.artifact_index)) == len(initial_artifact_rows) + 1
    assert sorted(
        path
        for path in restarted.artifact_index.parent.iterdir()
        if path.is_dir()
    ) == snapshot_directories
    assert verify_artifact(
        restarted, recovered["report_artifact_id"]
    )["valid"] is True


def test_completion_before_snapshot_requires_source_report(
    approved_project, monkeypatch
):
    from autoresearch.workflow import tasks as workflow

    task = open_control_plane_task_without_outputs(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    original_ensure = workflow._ensure_completion_artifact

    def stop_before_snapshot(*args, **kwargs):
        raise OSError("injected before task-report snapshot")

    monkeypatch.setattr(
        workflow, "_ensure_completion_artifact", stop_before_snapshot
    )
    with pytest.raises(OSError, match="before task-report snapshot"):
        complete_task(approved_project, task["task_id"], report)
    monkeypatch.setattr(
        workflow, "_ensure_completion_artifact", original_ensure
    )
    report.unlink()
    budget_before = read_json(approved_project.budget)

    with pytest.raises(ValueError, match="report.*missing"):
        complete_task(
            ProjectLayout.from_root(approved_project.root),
            task["task_id"],
            report,
        )

    assert read_json(approved_project.budget) == budget_before
    assert read_jsonl(approved_project.artifact_index) == []
    assert read_jsonl(approved_project.journal) == []
    assert read_jsonl(approved_project.budget_settlements) == []


@pytest.mark.parametrize(
    "field", ["settlement_id"]
)
def test_pending_completion_rejects_hostile_settlement_binding_before_effects(
    approved_project, monkeypatch, field
):
    from autoresearch.workflow import tasks as workflow

    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-hostile-binding",
            duration_ms=100,
            tokens={"input": 4, "output": 1, "cached": 0, "total": 5},
            cost_usd=0.1,
        ),
    )
    original_ensure = workflow._ensure_completion_artifact

    def stop_before_first_effect(*args, **kwargs):
        raise OSError("injected after completion intent")

    monkeypatch.setattr(
        workflow, "_ensure_completion_artifact", stop_before_first_effect
    )
    with pytest.raises(OSError, match="after completion intent"):
        complete_task(approved_project, task["task_id"], report)
    monkeypatch.setattr(
        workflow, "_ensure_completion_artifact", original_ensure
    )
    intent_path = next(
        (
            approved_project.root
            / ".research"
            / "workflow-transactions"
        ).glob("task-completion-*.json")
    )
    intent = read_json(intent_path)
    intent[field] = "0" * 64
    (
        intent["receipt"],
        intent["task_after"],
        intent["result"],
    ) = workflow._expected_completion_evidence(intent)
    atomic_write_json(intent_path, intent)
    budget_before = read_json(approved_project.budget)

    with pytest.raises(ValueError, match="settlement|receipt"):
        complete_task(
            ProjectLayout.from_root(approved_project.root),
            task["task_id"],
            report,
        )

    assert read_json(approved_project.budget) == budget_before
    assert read_jsonl(approved_project.artifact_index) == []
    assert read_jsonl(approved_project.journal) == []
    assert read_jsonl(approved_project.budget_settlements) == []


def test_completion_recovery_after_receipt_does_not_duplicate_evidence(
    approved_project, monkeypatch
):
    from autoresearch.workflow import tasks as workflow

    task = open_control_plane_task_without_outputs(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    task_record_path = Path(task["task_record_path"])
    original_atomic = workflow.atomic_write_json

    def fail_task_state(path: Path, value: dict) -> None:
        if path == task_record_path:
            raise OSError("injected failure after completion receipt")
        original_atomic(path, value)

    monkeypatch.setattr(workflow, "atomic_write_json", fail_task_state)
    with pytest.raises(OSError, match="after completion receipt"):
        complete_task(approved_project, task["task_id"], report)

    artifact_rows = read_jsonl(approved_project.artifact_index)
    journal_rows = read_jsonl(approved_project.journal)
    budget = read_json(approved_project.budget)
    assert len(artifact_rows) == 1
    assert len(journal_rows) == 1
    assert task["reservation_id"] not in budget["reservations"]
    assert read_json(task_record_path)["status"] == "open"

    monkeypatch.setattr(workflow, "atomic_write_json", original_atomic)
    restarted = ProjectLayout.from_root(approved_project.root)
    recovered = complete_task(restarted, task["task_id"], report)

    assert read_jsonl(restarted.artifact_index) == artifact_rows
    assert read_jsonl(restarted.journal) == journal_rows
    assert read_json(restarted.budget) == budget
    assert recovered["receipt"] == journal_rows[0]


def test_committed_completion_rejects_tampered_public_result(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    task = open_control_plane_task_without_outputs(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    complete_task(approved_project, task["task_id"], report)
    intent_path = next(
        (
            approved_project.root
            / ".research"
            / "workflow-transactions"
        ).glob("task-completion-*.json")
    )
    intent = read_json(intent_path)
    intent["result"]["status"] = "failed"
    atomic_write_json(intent_path, intent)

    with pytest.raises(ValueError, match="completion.*result"):
        complete_task(approved_project, task["task_id"], report)


def test_pending_completion_rejects_changed_actual_usage_before_side_effect(
    approved_project, monkeypatch
):
    from autoresearch.workflow import tasks as workflow

    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-pending-usage",
            duration_ms=1000,
            tokens={"input": 8, "output": 2, "cached": 0, "total": 10},
            cost_usd=0.2,
        ),
    )
    original_ensure = workflow._ensure_completion_artifact

    def stop_before_first_side_effect(*args, **kwargs):
        raise OSError("injected after completion intent")

    monkeypatch.setattr(
        workflow,
        "_ensure_completion_artifact",
        stop_before_first_side_effect,
    )
    with pytest.raises(OSError, match="after completion intent"):
        complete_task(approved_project, task["task_id"], report)
    monkeypatch.setattr(
        workflow, "_ensure_completion_artifact", original_ensure
    )

    intent_path = next(
        (
            approved_project.root
            / ".research"
            / "workflow-transactions"
        ).glob("task-completion-*.json")
    )
    intent = read_json(intent_path)
    intent["usage"]["cost_usd"] = 0.8
    intent["actual_usage"]["cost_usd"] = 0.8
    intent["settlement"] = workflow._prepare_settlement_evidence(
        approved_project,
        task["reservation_id"],
        intent["actual_usage"],
    )
    (
        intent["receipt"],
        intent["task_after"],
        intent["result"],
    ) = workflow._expected_completion_evidence(intent)
    atomic_write_json(intent_path, intent)
    budget_before = read_json(approved_project.budget)

    with pytest.raises(ValueError, match="usage"):
        complete_task(
            ProjectLayout.from_root(approved_project.root),
            task["task_id"],
            report,
        )

    assert read_json(approved_project.budget) == budget_before
    assert read_jsonl(approved_project.artifact_index) == []
    assert read_jsonl(approved_project.journal) == []


def test_pending_completion_rejects_changed_settlement_before_side_effect(
    approved_project, monkeypatch
):
    from autoresearch.workflow import tasks as workflow

    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-pending-settlement",
            duration_ms=100,
            tokens={"input": 4, "output": 1, "cached": 0, "total": 5},
            cost_usd=0.1,
        ),
    )
    original_ensure = workflow._ensure_completion_artifact

    def stop_before_first_side_effect(*args, **kwargs):
        raise OSError("injected after completion intent")

    monkeypatch.setattr(
        workflow,
        "_ensure_completion_artifact",
        stop_before_first_side_effect,
    )
    with pytest.raises(OSError, match="after completion intent"):
        complete_task(approved_project, task["task_id"], report)
    monkeypatch.setattr(
        workflow, "_ensure_completion_artifact", original_ensure
    )

    intent_path = next(
        (
            approved_project.root
            / ".research"
            / "workflow-transactions"
        ).glob("task-completion-*.json")
    )
    intent = read_json(intent_path)
    intent["settlement"]["reservation"]["cost_usd"] = 9.0
    atomic_write_json(intent_path, intent)
    budget_before = read_json(approved_project.budget)

    with pytest.raises(ValueError, match="settlement|reservation"):
        complete_task(
            ProjectLayout.from_root(approved_project.root),
            task["task_id"],
            report,
        )

    assert read_json(approved_project.budget) == budget_before
    assert read_jsonl(approved_project.artifact_index) == []
    assert read_jsonl(approved_project.journal) == []


def test_pending_completion_rejects_changed_artifact_path_before_side_effect(
    approved_project, monkeypatch
):
    from autoresearch.workflow import tasks as workflow

    task = open_control_plane_task_without_outputs(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    original_ensure = workflow._ensure_completion_artifact

    def stop_before_first_side_effect(*args, **kwargs):
        raise OSError("injected after completion intent")

    monkeypatch.setattr(
        workflow,
        "_ensure_completion_artifact",
        stop_before_first_side_effect,
    )
    with pytest.raises(OSError, match="after completion intent"):
        complete_task(approved_project, task["task_id"], report)
    monkeypatch.setattr(
        workflow, "_ensure_completion_artifact", original_ensure
    )

    intent_path = next(
        (
            approved_project.root
            / ".research"
            / "workflow-transactions"
        ).glob("task-completion-*.json")
    )
    intent = read_json(intent_path)
    intent["report_artifact"]["path"] = str(
        (
            approved_project.artifact_index.parent
            / intent["report_artifact_id"]
            / "changed.md"
        ).resolve()
    )
    atomic_write_json(intent_path, intent)
    budget_before = read_json(approved_project.budget)

    with pytest.raises(ValueError, match="artifact.*path|snapshot path"):
        complete_task(
            ProjectLayout.from_root(approved_project.root),
            task["task_id"],
            report,
        )

    assert read_json(approved_project.budget) == budget_before
    assert read_jsonl(approved_project.artifact_index) == []
    assert read_jsonl(approved_project.journal) == []


def test_completion_recovers_after_final_task_state_boundary(
    approved_project, monkeypatch
):
    from autoresearch.workflow import tasks as workflow

    task = open_control_plane_task_without_outputs(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    task_record_path = Path(task["task_record_path"])
    original_atomic = workflow.atomic_write_json

    def fail_phase_after_task_state(path: Path, value: dict) -> None:
        if (
            path.parent.name == "workflow-transactions"
            and value.get("phase") == "task_state_written"
        ):
            raise OSError("injected failure after final task state")
        original_atomic(path, value)

    monkeypatch.setattr(
        workflow, "atomic_write_json", fail_phase_after_task_state
    )
    with pytest.raises(OSError, match="after final task state"):
        complete_task(approved_project, task["task_id"], report)

    artifacts = read_jsonl(approved_project.artifact_index)
    receipts = read_jsonl(approved_project.journal)
    budget = read_json(approved_project.budget)
    assert read_json(task_record_path)["status"] == "completed"

    monkeypatch.setattr(workflow, "atomic_write_json", original_atomic)
    recovered = complete_task(
        ProjectLayout.from_root(approved_project.root),
        task["task_id"],
        report,
    )

    assert len(artifacts) == 1
    assert len(receipts) == 1
    assert read_jsonl(approved_project.artifact_index) == artifacts
    assert read_jsonl(approved_project.journal) == receipts
    assert read_json(approved_project.budget) == budget
    assert recovered["receipt"] == receipts[0]


def test_complete_task_settles_wall_for_null_provider_usage(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    append_model_call(
        approved_project,
        model_call(
            task,
            "call-null",
            duration_ms=100,
            tokens=None,
            cost_usd=None,
        ),
    )

    result = complete_task(approved_project, task["task_id"], report)

    assert result["status"] == "completed"
    assert result["usage"] == {
        "cost_usd": None,
        "tokens": None,
        "duration_ms": 100,
    }
    budget = read_json(approved_project.budget)
    assert task["reservation_id"] not in budget["reservations"]
    assert budget["consumed"]["wall_seconds"] == pytest.approx(0.1)


def test_completion_without_provider_usage_unblocks_later_task(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec()
    )
    report = write_report(task)
    assert complete_task(approved_project, task["task_id"], report)["status"] == "completed"

    later = open_budgeted_coordinator_task(
        approved_project,
        monkeypatch,
        task_spec(
            task_id="paid-task-2",
            candidate_id="cand-2",
            output_path="cycles/cycle-1/tasks/paid-task-2/report.md",
        ),
    )

    assert later["status"] == "open"


def test_transition_checkpoints_before_advancing(
    project_layout, approved_project
):
    initial = read_json(
        project_layout.checkpoints / "000000-initial.json"
    )

    result = transition_stage(
        project_layout, "intake", "research-charter", expected_revision=0
    )

    assert result.status == "applied"
    assert result.receipt_path == project_layout.journal.resolve()
    checkpoints = sorted(project_layout.checkpoints.iterdir())
    assert len(checkpoints) == 2
    receipt = read_jsonl(project_layout.journal)[-1]
    checkpoint = read_json(Path(receipt["checkpoint_path"]))
    state = read_json(project_layout.state)
    assert checkpoint["state_hash"] == initial["state_hash"]
    assert receipt["source_state_hash"] == initial["state_hash"]
    assert receipt["target_state_hash"] == sha256_file(project_layout.state)
    assert state["revision"] == 1
    assert state["previous_state_hash"] == initial["state_hash"]
    assert state["last_transition_id"] == receipt["receipt_id"]


def test_create_checkpoint_captures_state_hashes_and_ledger_counts(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    task = open_budgeted_coordinator_task(
        approved_project, monkeypatch, task_spec()
    )
    settle_budget(approved_project, task["reservation_id"], {})
    checkpoint = create_checkpoint(approved_project, "before fixture")

    assert checkpoint["reason"] == "before fixture"
    assert checkpoint["state"] == read_json(approved_project.state)
    assert checkpoint["state_hash"] == sha256_file(approved_project.state)
    assert checkpoint["graph_hash"] == sha256_file(approved_project.graph)
    assert checkpoint["budget_hash"] == sha256_file(approved_project.budget)
    assert checkpoint["ledger_line_counts"] == {
        "gates": 4,
        "journal": 0,
        "model_calls": 0,
        "artifact_index": 0,
        "budget_settlements": 1,
    }
    assert Path(checkpoint["checkpoint_path"]).is_file()


def test_create_checkpoint_reuses_existing_identical_control_snapshot(
    approved_project
):
    first = create_checkpoint(approved_project, "before retry")
    second = create_checkpoint(approved_project, "retry uses same control state")

    assert second == first
    assert len(list(approved_project.checkpoints.iterdir())) == 2


def test_transition_rejects_optimistic_revision_conflict_and_tamper(
    approved_project
):
    conflict = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=1,
    )
    assert conflict.status == "invalid"
    assert conflict.reason == "state revision conflict: expected 1, found 0"
    assert len(list(approved_project.checkpoints.iterdir())) == 1

    state = read_json(approved_project.state)
    state["pending_gate"] = "tampered"
    atomic_write_json(approved_project.state, state)
    tampered = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )
    assert tampered.status == "invalid"
    assert "state hash mismatch" in tampered.reason
    assert len(list(approved_project.checkpoints.iterdir())) == 1


def test_unauthorized_graph_mutation_is_rejected(approved_project):
    graph = yaml.safe_load(
        approved_project.graph.read_text(encoding="utf-8")
    )
    graph["stages"][0]["gate_after"] = True
    graph["stages"][0]["gate_type"] = "evaluation"
    graph["stages"][0]["gate_authority"] = "user"
    approved_project.graph.write_text(
        yaml.safe_dump(graph, sort_keys=False),
        encoding="utf-8",
    )

    result = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )

    assert result.status == "invalid"
    assert "graph hash mismatch" in result.reason
    assert len(list(approved_project.checkpoints.iterdir())) == 1
    assert read_jsonl(approved_project.journal) == []


def test_open_task_rejects_uncommitted_graph_mutation(approved_project):
    graph = yaml.safe_load(
        approved_project.graph.read_text(encoding="utf-8")
    )
    graph["limits"]["loop-subagent-bound"] += 1
    approved_project.graph.write_text(
        yaml.safe_dump(graph, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="graph hash mismatch"):
        open_task(approved_project, task_spec())

    assert not any(approved_project.cycles.iterdir())
    assert read_json(approved_project.budget)["reservations"] == {}


def test_graph_revision_is_gate_bound_atomic_and_restores_validation(
    approved_project, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    source_graph_hash = sha256_file(approved_project.graph)
    source_state = read_json(approved_project.state)
    replacement = yaml.safe_load(
        approved_project.graph.read_text(encoding="utf-8")
    )
    replacement["limits"]["loop-subagent-bound"] = 7
    replacement_path = tmp_path / "approved-research.yaml"
    replacement_path.write_text(
        yaml.safe_dump(replacement, sort_keys=False), encoding="utf-8"
    )

    gate = request_graph_revision(
        approved_project, replacement_path, "raise graph fan-out ceiling"
    )
    record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "rick-approved-graph-revision"},
    )

    result = apply_graph_revision(
        approved_project, replacement_path, gate["gate_id"]
    )

    assert result["status"] == "applied"
    assert result["source_graph_hash"] == source_graph_hash
    assert result["target_graph_hash"] == sha256_file(approved_project.graph)
    assert read_json(approved_project.state)["revision"] == source_state["revision"] + 1
    receipt = read_jsonl(approved_project.journal)[-1]
    assert receipt["record_type"] == "graph-revision"
    assert receipt["gate_id"] == gate["gate_id"]
    checkpoint = read_json(Path(receipt["checkpoint_path"]))
    assert checkpoint["graph_hash"] == source_graph_hash
    assert main(["validate", "--root", str(approved_project.root)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "valid",
        "errors": [],
    }

    assert apply_graph_revision(
        approved_project, replacement_path, gate["gate_id"]
    ) == result

    intent_path = (
        approved_project.root
        / ".research"
        / "workflow-transactions"
        / f"{result['transaction_id']}.json"
    )
    intent = read_json(intent_path)
    intent["phase"] = "prepared"
    atomic_write_json(intent_path, intent)
    assert main(["validate", "--root", str(approved_project.root)]) == 1
    assert "graph revision evidence is not committed" in json.loads(
        capsys.readouterr().out
    )["errors"][0]
    assert main(
        [
            "graph-revise",
            "--root",
            str(approved_project.root),
            "--gate-id",
            gate["gate_id"],
        ]
    ) == 0
    assert main(["validate", "--root", str(approved_project.root)]) == 0
    responses = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line
    ]
    assert responses[0]["status"] == "applied"
    assert responses[1] == {
        "status": "valid",
        "errors": [],
    }


@pytest.mark.parametrize(
    "failure_point", ("checkpoint", "receipt", "graph", "state", "commit")
)
def test_graph_revision_recovers_once_after_each_durable_write(
    approved_project,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
):
    from autoresearch.workflow import revisions as revision_workflow

    source_revision = read_json(approved_project.state)["revision"]
    initial_checkpoints = list(approved_project.checkpoints.glob("*.json"))
    replacement = yaml.safe_load(
        approved_project.graph.read_text(encoding="utf-8")
    )
    replacement["limits"]["loop-subagent-bound"] = 7
    replacement_path = tmp_path / "approved-research.yaml"
    replacement_path.write_text(
        yaml.safe_dump(replacement, sort_keys=False), encoding="utf-8"
    )
    gate = request_graph_revision(
        approved_project, replacement_path, f"recover after {failure_point}"
    )
    record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "approve recovery test"},
    )

    with monkeypatch.context() as patch:
        if failure_point == "checkpoint":
            original = revision_workflow.atomic_write_json

            def fail_after_write(path, value):
                original(path, value)
                if path.parent == approved_project.checkpoints:
                    raise RuntimeError("interrupted after checkpoint write")

            patch.setattr(revision_workflow, "atomic_write_json", fail_after_write)
        elif failure_point == "receipt":
            original = revision_workflow.append_jsonl_locked

            def fail_after_write(path, value):
                original(path, value)
                if path == approved_project.journal:
                    raise RuntimeError("interrupted after receipt append")

            patch.setattr(
                revision_workflow, "append_jsonl_locked", fail_after_write
            )
        elif failure_point == "graph":
            original = revision_workflow._atomic_write_text

            def fail_after_write(path, value):
                original(path, value)
                if path == approved_project.graph:
                    raise RuntimeError("interrupted after graph write")

            patch.setattr(
                revision_workflow, "_atomic_write_text", fail_after_write
            )
        elif failure_point == "state":
            original = revision_workflow._atomic_write_research_state

            def fail_after_write(layout, value):
                original(layout, value)
                raise RuntimeError("interrupted after state write")

            patch.setattr(
                revision_workflow, "_atomic_write_research_state", fail_after_write
            )
        else:
            original = revision_workflow._set_phase

            def fail_after_write(path, value, phase):
                result = original(path, value, phase)
                if phase == "committed":
                    raise RuntimeError("interrupted after commit write")
                return result

            patch.setattr(revision_workflow, "_set_phase", fail_after_write)

        with pytest.raises(RuntimeError, match="interrupted after"):
            apply_graph_revision(
                approved_project, replacement_path, gate["gate_id"]
            )

    result = apply_graph_revision(
        approved_project, replacement_path, gate["gate_id"]
    )
    receipts = [
        row
        for row in read_jsonl(approved_project.journal)
        if row.get("transaction_id") == result["transaction_id"]
    ]
    assert len(receipts) == 1
    assert len(list(approved_project.checkpoints.glob("*.json"))) == (
        len(initial_checkpoints) + 1
    )
    assert read_json(approved_project.state)["revision"] == source_revision + 1
    assert main(["validate", "--root", str(approved_project.root)]) == 0


def test_graph_hash_toctou_never_blesses_replacement(
    approved_project, monkeypatch
):
    from autoresearch.workflow import stages as workflow

    committed_hash = read_json(
        approved_project.checkpoints / "000000-initial.json"
    )["graph_hash"]
    replacement = yaml.safe_load(
        approved_project.graph.read_text(encoding="utf-8")
    )
    replacement["limits"]["loop-subagent-bound"] += 1
    original_integrity = workflow._state_integrity_error

    def replace_after_integrity(*args, **kwargs):
        error = original_integrity(*args, **kwargs)
        if error is None:
            approved_project.graph.write_text(
                yaml.safe_dump(replacement, sort_keys=False),
                encoding="utf-8",
            )
        return error

    monkeypatch.setattr(
        workflow, "_state_integrity_error", replace_after_integrity
    )
    result = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )

    assert result.status == "applied"
    receipt = read_jsonl(approved_project.journal)[-1]
    checkpoint = read_json(Path(receipt["checkpoint_path"]))
    assert receipt["graph_hash"] == committed_hash
    assert checkpoint["graph_hash"] == committed_hash
    later = transition_stage(
        approved_project,
        "research-charter",
        "literature-prior-art",
        expected_revision=1,
    )
    assert later.status == "invalid"
    assert "graph hash mismatch" in later.reason


def test_transition_checks_source_graph_edge_and_required_task_artifact(
    approved_project
):
    wrong_source = transition_stage(
        approved_project,
        "research-charter",
        "literature-prior-art",
        expected_revision=0,
    )
    assert wrong_source.status == "invalid"
    assert "current stage" in wrong_source.reason

    missing_edge = transition_stage(
        approved_project,
        "intake",
        "final-report",
        expected_revision=0,
    )
    assert missing_edge.status == "invalid"
    assert "graph edge" in missing_edge.reason
    unknown_target = transition_stage(
        approved_project,
        "intake",
        "unknown-stage",
        expected_revision=0,
    )
    assert unknown_target.status == "invalid"
    assert "invalid research graph" in unknown_target.reason

    open_task(approved_project, task_spec())
    blocked = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )
    assert blocked.status == "blocked"
    assert "task-1" in blocked.reason
    assert len(list(approved_project.checkpoints.iterdir())) == 1


def test_transition_enforces_gate_and_appends_receipts(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    require_charter_gate_for_test(monkeypatch)
    first = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )
    assert first.status == "applied"
    first_line = approved_project.journal.read_text(encoding="utf-8")

    blocked = transition_stage(
        approved_project,
        "research-charter",
        "literature-prior-art",
        expected_revision=1,
    )
    assert blocked.status == "blocked"
    assert "charter gate" in blocked.reason

    approve_stage_gate(approved_project)
    second = transition_stage(
        approved_project,
        "research-charter",
        "literature-prior-art",
        expected_revision=1,
    )
    assert second.status == "applied"
    lines = approved_project.journal.read_text(encoding="utf-8").splitlines()
    assert lines[0] == first_line.strip()
    receipts = [
        row
        for row in read_jsonl(approved_project.journal)
        if row["record_type"] == "stage-transition"
    ]
    assert len(receipts) == 2
    assert receipts[0]["receipt_id"] != receipts[1]["receipt_id"]
    assert receipts[1]["source_state_hash"] == receipts[0]["target_state_hash"]


def test_coordinator_gate_binds_evidence_and_recovers_without_duplicate_receipts(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    from autoresearch.workflow import stages as workflow

    first = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )
    assert first.status == "applied"
    original_checkpoint = workflow._build_checkpoint

    def fail_after_coordinator_gate(*args, **kwargs):
        raise OSError("injected after Coordinator gate")

    monkeypatch.setattr(
        workflow, "_build_checkpoint", fail_after_coordinator_gate
    )
    with pytest.raises(OSError, match="after Coordinator gate"):
        transition_stage(
            approved_project,
            "research-charter",
            "literature-prior-art",
            expected_revision=1,
        )
    receipts = [
        row
        for row in read_jsonl(approved_project.gates)
        if row.get("gate_type") == "charter"
    ]
    assert len(receipts) == 2
    assert receipts[-1]["approval_source"] == "Coordinator"
    payload = read_json(
        approved_project.root
        / ".research"
        / "gate-payloads"
        / f"{receipts[-1]['gate_id']}.json"
    )["payload"]
    assert set(payload) == {
        "stage_id",
        "source_revision",
        "source_state_hash",
        "graph_hash",
        "output_artifacts",
    }
    assert payload["stage_id"] == "research-charter"
    assert payload["source_revision"] == 1

    monkeypatch.setattr(workflow, "_build_checkpoint", original_checkpoint)
    restarted = transition_stage(
        approved_project,
        "research-charter",
        "literature-prior-art",
        expected_revision=1,
    )
    assert restarted.status == "applied"
    assert len(
        [
            row
            for row in read_jsonl(approved_project.gates)
            if row.get("gate_type") == "charter"
        ]
    ) == 2


def test_tampered_coordinator_gate_stays_blocked(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    from autoresearch.workflow import stages as workflow

    assert transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    ).status == "applied"
    original_checkpoint = workflow._build_checkpoint
    monkeypatch.setattr(
        workflow,
        "_build_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("injected after Coordinator gate")
        ),
    )
    with pytest.raises(OSError, match="after Coordinator gate"):
        transition_stage(
            approved_project,
            "research-charter",
            "literature-prior-art",
            expected_revision=1,
        )
    receipt = read_jsonl(approved_project.gates)[-1]
    payload_path = (
        approved_project.root
        / ".research"
        / "gate-payloads"
        / f"{receipt['gate_id']}.json"
    )
    payload = read_json(payload_path)
    payload["payload"]["graph_hash"] = "0" * 64
    atomic_write_json(payload_path, payload)

    monkeypatch.setattr(workflow, "_build_checkpoint", original_checkpoint)
    result = transition_stage(
        approved_project,
        "research-charter",
        "literature-prior-art",
        expected_revision=1,
    )
    assert result.status == "blocked"
    assert "Coordinator gate evidence conflicts" in result.reason


def test_transition_rejects_tampered_applicable_gate(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    require_charter_gate_for_test(monkeypatch)
    first = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )
    assert first.status == "applied"
    approve_stage_gate(approved_project)
    approved = read_jsonl(approved_project.gates)[-1]
    payload_path = (
        approved_project.root
        / ".research"
        / "gate-payloads"
        / f"{approved['gate_id']}.json"
    )
    payload = read_json(payload_path)
    payload["payload"]["stage_id"] = "different-stage"
    atomic_write_json(payload_path, payload)

    result = transition_stage(
        approved_project,
        "research-charter",
        "literature-prior-art",
        expected_revision=1,
    )

    assert result.status == "blocked"
    assert "charter gate" in result.reason


def test_durable_workflow_recovery_after_transition_receipt(
    approved_project, monkeypatch
):
    from autoresearch.workflow import recovery as workflow

    original_atomic = workflow.atomic_write_json

    def fail_state_write(path: Path, value: dict) -> None:
        if path == approved_project.state:
            raise OSError("injected failure after transition receipt")
        original_atomic(path, value)

    monkeypatch.setattr(workflow, "atomic_write_json", fail_state_write)
    with pytest.raises(OSError, match="after transition receipt"):
        transition_stage(
            approved_project,
            "intake",
            "research-charter",
            expected_revision=0,
        )

    receipts_after_failure = read_jsonl(approved_project.journal)
    checkpoints_after_failure = sorted(
        approved_project.checkpoints.iterdir()
    )
    assert len(receipts_after_failure) == 1
    assert len(checkpoints_after_failure) == 2
    assert read_json(approved_project.state)["revision"] == 0

    monkeypatch.setattr(workflow, "atomic_write_json", original_atomic)
    restarted = ProjectLayout.from_root(approved_project.root)
    conflicting = transition_stage(
        restarted,
        "intake",
        "final-report",
        expected_revision=0,
    )
    assert conflicting.status == "invalid"
    assert "conflicting pending transition" in conflicting.reason

    recovered = transition_stage(
        restarted,
        "intake",
        "research-charter",
        expected_revision=0,
    )
    recovered_again = transition_stage(
        restarted,
        "intake",
        "research-charter",
        expected_revision=0,
    )

    assert recovered.status == "applied"
    assert recovered_again == recovered
    assert read_jsonl(restarted.journal) == receipts_after_failure
    assert sorted(restarted.checkpoints.iterdir()) == checkpoints_after_failure
    assert read_json(restarted.state)["revision"] == 1


def test_transition_intent_rejects_tampered_checkpoint_state(
    approved_project, monkeypatch
):
    from autoresearch.workflow import recovery as workflow

    original_atomic = workflow.atomic_write_json

    def fail_state_write(path: Path, value: dict) -> None:
        if path == approved_project.state:
            raise OSError("injected pending transition")
        original_atomic(path, value)

    monkeypatch.setattr(workflow, "atomic_write_json", fail_state_write)
    with pytest.raises(OSError, match="pending transition"):
        transition_stage(
            approved_project,
            "intake",
            "research-charter",
            expected_revision=0,
        )
    monkeypatch.setattr(workflow, "atomic_write_json", original_atomic)

    intent_path = next(
        (
            approved_project.root
            / ".research"
            / "workflow-transactions"
        ).glob("stage-transition-*.json")
    )
    intent = read_json(intent_path)
    intent["checkpoint"]["state"]["pending_gate"] = "tampered"
    atomic_write_json(
        Path(intent["checkpoint"]["checkpoint_path"]),
        intent["checkpoint"],
    )
    atomic_write_json(intent_path, intent)

    result = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )

    assert result.status == "invalid"
    assert "checkpoint" in result.reason
    assert read_json(approved_project.state)["revision"] == 0
    assert len(read_jsonl(approved_project.journal)) == 1


def test_pending_transition_rejects_coordinated_graph_reanchor(
    approved_project, monkeypatch
):
    from autoresearch.workflow import recovery as workflow

    original_atomic = workflow.atomic_write_json

    def fail_state_write(path: Path, value: dict) -> None:
        if path == approved_project.state:
            raise OSError("injected pending transition")
        original_atomic(path, value)

    monkeypatch.setattr(workflow, "atomic_write_json", fail_state_write)
    with pytest.raises(OSError, match="pending transition"):
        transition_stage(
            approved_project,
            "intake",
            "research-charter",
            expected_revision=0,
        )
    monkeypatch.setattr(workflow, "atomic_write_json", original_atomic)

    graph = yaml.safe_load(
        approved_project.graph.read_text(encoding="utf-8")
    )
    graph["limits"]["loop-subagent-bound"] += 1
    approved_project.graph.write_text(
        yaml.safe_dump(graph, sort_keys=False),
        encoding="utf-8",
    )
    changed_graph_hash = sha256_file(approved_project.graph)
    intent_path = next(
        (
            approved_project.root
            / ".research"
            / "workflow-transactions"
        ).glob("stage-transition-*.json")
    )
    intent = read_json(intent_path)
    intent["graph_hash"] = changed_graph_hash
    intent["checkpoint"]["graph_hash"] = changed_graph_hash
    intent["checkpoint_hash"] = canonical_hash(intent["checkpoint"])
    intent["receipt"]["graph_hash"] = changed_graph_hash
    intent["receipt"]["checkpoint_hash"] = intent["checkpoint_hash"]
    intent["receipt_hash"] = canonical_hash(intent["receipt"])
    atomic_write_json(
        Path(intent["checkpoint"]["checkpoint_path"]),
        intent["checkpoint"],
    )
    atomic_write_json(intent_path, intent)
    approved_project.journal.write_text(
        json.dumps(
            intent["receipt"],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    restarted = ProjectLayout.from_root(approved_project.root)
    result = transition_stage(
        restarted,
        "intake",
        "research-charter",
        expected_revision=0,
    )

    assert result.status == "invalid"
    assert "graph anchor" in result.reason
    assert read_json(restarted.state)["revision"] == 0
    assert len(read_jsonl(restarted.journal)) == 1


def test_revision_one_pending_transition_rejects_prior_receipt_graph_reanchor(
    approved_project, monkeypatch
):
    from autoresearch.workflow import recovery as workflow

    first = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=0,
    )
    assert first.status == "applied"
    approve_stage_gate(approved_project)
    original_atomic = workflow.atomic_write_json

    def fail_second_state(path: Path, value: dict) -> None:
        if path == approved_project.state:
            raise OSError("injected revision-one pending transition")
        original_atomic(path, value)

    monkeypatch.setattr(workflow, "atomic_write_json", fail_second_state)
    with pytest.raises(OSError, match="revision-one"):
        transition_stage(
            approved_project,
            "research-charter",
            "literature-prior-art",
            expected_revision=1,
        )
    monkeypatch.setattr(workflow, "atomic_write_json", original_atomic)

    graph = yaml.safe_load(
        approved_project.graph.read_text(encoding="utf-8")
    )
    graph["limits"]["loop-subagent-bound"] += 1
    approved_project.graph.write_text(
        yaml.safe_dump(graph, sort_keys=False),
        encoding="utf-8",
    )
    changed_graph_hash = sha256_file(approved_project.graph)
    intent_path = (
        approved_project.root
        / ".research"
        / "workflow-transactions"
        / (
            workflow._transition_transaction_id(
                "research-charter", "literature-prior-art", 1
            )
            + ".json"
        )
    )
    intent = read_json(intent_path)
    intent["graph_hash"] = changed_graph_hash
    intent["checkpoint"]["graph_hash"] = changed_graph_hash
    intent["checkpoint_hash"] = canonical_hash(intent["checkpoint"])
    intent["receipt"]["graph_hash"] = changed_graph_hash
    intent["receipt"]["checkpoint_hash"] = intent["checkpoint_hash"]
    intent["receipt_hash"] = canonical_hash(intent["receipt"])
    atomic_write_json(
        Path(intent["checkpoint"]["checkpoint_path"]),
        intent["checkpoint"],
    )
    atomic_write_json(intent_path, intent)
    journal = read_jsonl(approved_project.journal)
    for row in journal:
        if row.get("target_revision") == 1:
            row["graph_hash"] = changed_graph_hash
        elif row.get("transaction_id") == intent["transaction_id"]:
            row.update(intent["receipt"])
    approved_project.journal.write_text(
        "".join(
            json.dumps(
                row, ensure_ascii=False, separators=(",", ":")
            )
            + "\n"
            for row in journal
        ),
        encoding="utf-8",
    )

    result = transition_stage(
        ProjectLayout.from_root(approved_project.root),
        "research-charter",
        "literature-prior-art",
        expected_revision=1,
    )

    assert result.status == "invalid"
    assert "graph anchor" in result.reason
    assert read_json(approved_project.state)["revision"] == 1


def test_pending_transition_rejects_inexact_ledger_counts(
    approved_project, monkeypatch
):
    from autoresearch.workflow import stages as workflow

    original_append = workflow.append_jsonl_locked

    def fail_receipt(path: Path, value: dict) -> None:
        if path == approved_project.journal:
            raise OSError("injected after checkpoint")
        original_append(path, value)

    monkeypatch.setattr(workflow, "append_jsonl_locked", fail_receipt)
    with pytest.raises(OSError, match="after checkpoint"):
        transition_stage(
            approved_project,
            "intake",
            "research-charter",
            expected_revision=0,
        )
    monkeypatch.setattr(workflow, "append_jsonl_locked", original_append)

    intent_path = next(
        (
            approved_project.root
            / ".research"
            / "workflow-transactions"
        ).glob("stage-transition-*.json")
    )
    intent = read_json(intent_path)
    intent["checkpoint"]["ledger_line_counts"]["journal"] += 1
    intent["checkpoint_hash"] = canonical_hash(intent["checkpoint"])
    intent["receipt"]["checkpoint_hash"] = intent["checkpoint_hash"]
    intent["receipt_hash"] = canonical_hash(intent["receipt"])
    atomic_write_json(
        Path(intent["checkpoint"]["checkpoint_path"]),
        intent["checkpoint"],
    )
    atomic_write_json(intent_path, intent)

    result = transition_stage(
        ProjectLayout.from_root(approved_project.root),
        "intake",
        "research-charter",
        expected_revision=0,
    )

    assert result.status == "invalid"
    assert "ledger" in result.reason
    assert read_json(approved_project.state)["revision"] == 0
    assert read_jsonl(approved_project.journal) == []


def test_transition_recovers_after_checkpoint_before_receipt(
    approved_project, monkeypatch
):
    from autoresearch.workflow import stages as workflow

    original_append = workflow.append_jsonl_locked

    def fail_receipt(path: Path, value: dict) -> None:
        if path == approved_project.journal:
            raise OSError("injected failure after checkpoint")
        original_append(path, value)

    monkeypatch.setattr(workflow, "append_jsonl_locked", fail_receipt)
    with pytest.raises(OSError, match="after checkpoint"):
        transition_stage(
            approved_project,
            "intake",
            "research-charter",
            expected_revision=0,
        )
    checkpoint_paths = sorted(approved_project.checkpoints.iterdir())
    assert len(checkpoint_paths) == 2
    assert read_jsonl(approved_project.journal) == []

    monkeypatch.setattr(workflow, "append_jsonl_locked", original_append)
    recovered = transition_stage(
        ProjectLayout.from_root(approved_project.root),
        "intake",
        "research-charter",
        expected_revision=0,
    )

    assert recovered.status == "applied"
    assert sorted(approved_project.checkpoints.iterdir()) == checkpoint_paths
    assert len(read_jsonl(approved_project.journal)) == 1
    assert read_json(approved_project.state)["revision"] == 1


def test_transition_recovers_after_state_before_intent_phase(
    approved_project, monkeypatch
):
    from autoresearch.workflow import stages as workflow

    original_atomic = workflow.atomic_write_json

    def fail_phase_after_state(path: Path, value: dict) -> None:
        if (
            path.parent.name == "workflow-transactions"
            and value.get("phase") == "state_written"
        ):
            raise OSError("injected failure after transition state")
        original_atomic(path, value)

    monkeypatch.setattr(
        workflow, "atomic_write_json", fail_phase_after_state
    )
    with pytest.raises(OSError, match="after transition state"):
        transition_stage(
            approved_project,
            "intake",
            "research-charter",
            expected_revision=0,
        )
    assert read_json(approved_project.state)["revision"] == 1
    receipts = read_jsonl(approved_project.journal)
    checkpoints = sorted(approved_project.checkpoints.iterdir())

    monkeypatch.setattr(workflow, "atomic_write_json", original_atomic)
    recovered = transition_stage(
        ProjectLayout.from_root(approved_project.root),
        "intake",
        "research-charter",
        expected_revision=0,
    )

    assert recovered.status == "applied"
    assert read_jsonl(approved_project.journal) == receipts
    assert sorted(approved_project.checkpoints.iterdir()) == checkpoints
    assert len(receipts) == 1
    assert len(checkpoints) == 2


def test_cli_exposes_task_checkpoint_and_transition_commands(
    approved_project, tmp_path: Path, capsys
):
    artifact = input_artifact(approved_project, tmp_path, "cli-input.md")
    spec_path = tmp_path / "task.json"
    spec_path.write_text(
        json.dumps(
            task_spec(input_artifact_ids=[artifact["artifact_id"]])
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "task-open",
                "--root",
                str(approved_project.root),
                "--spec",
                str(spec_path),
            ]
        )
        == 0
    )
    opened = json.loads(capsys.readouterr().out)
    report = write_report(opened, artifact_ids=[artifact["artifact_id"]])

    assert main(
        [
            "task-complete",
            "--root",
            str(approved_project.root),
            "--task-id",
            opened["task_id"],
            "--report",
            str(report),
        ]
    ) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "completed"

    assert main(
        [
            "checkpoint",
            "--root",
            str(approved_project.root),
            "--reason",
            "CLI smoke",
        ]
    ) == 0
    checkpoint = json.loads(capsys.readouterr().out)
    assert Path(checkpoint["checkpoint_path"]).is_file()

    assert main(
        [
            "transition",
            "--root",
            str(approved_project.root),
            "--from",
            "intake",
            "--to",
            "research-charter",
            "--expected-revision",
            "0",
        ]
    ) == 0
    transition = json.loads(capsys.readouterr().out)
    assert transition["status"] == "applied"
    assert transition["state_revision"] == 1
    assert transition["receipt_path"] == str(approved_project.journal.resolve())
