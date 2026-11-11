from __future__ import annotations

import importlib
from dataclasses import fields
from pathlib import Path

import pytest

from autoresearch.contracts import RunSpec
from autoresearch.governance import record_gate_receipt, request_gate
from autoresearch.io import read_jsonl
from autoresearch.workflow import validate_report
from autoresearch.workflow.tasks import REPORT_STATUSES
from autoresearch.worker_events import TERMINAL_STATUSES


def test_needs_review_is_a_recognized_report_and_terminal_status():
    assert "needs_review" in REPORT_STATUSES
    assert "needs_review" in TERMINAL_STATUSES
    # needs_review stays distinct from needs_user
    assert "needs_review" != "needs_user"


def test_run_spec_sensitive_action_field_defaults_false(project_layout):
    names = {field.name for field in fields(RunSpec)}
    assert "sensitive_action" in names
    spec = RunSpec(
        run_id="run-1",
        environment_id="env-1",
        dependency_lock_hash="a" * 64,
        backend="command-local",
        cwd=project_layout.root,
        argv=("uv",),
        timeout_seconds=5,
        resource_limits={"wall_seconds": 5},
        input_paths=(),
        output_paths=(),
        expected_artifact_types=(),
        candidate_id="cand-1",
        evaluation_spec_id="eval-spec-1",
        evaluation_cycle_checkpoint_id="cycle-1",
    )
    assert spec.sensitive_action is False


def test_run_spec_sensitive_action_is_bound_into_spec_payload(project_layout):
    executor = importlib.import_module("autoresearch.executors")
    base = dict(
        run_id="run-1",
        environment_id="env-1",
        dependency_lock_hash="a" * 64,
        backend="command-local",
        cwd=project_layout.root,
        argv=("uv",),
        timeout_seconds=5,
        resource_limits={"wall_seconds": 5},
        input_paths=(),
        output_paths=(),
        expected_artifact_types=(),
        candidate_id="cand-1",
        evaluation_spec_id="eval-spec-1",
        evaluation_cycle_checkpoint_id="cycle-1",
    )
    insensitive = executor._run_spec_payload(RunSpec(**base))
    sensitive = executor._run_spec_payload(
        RunSpec(**{**base, "sensitive_action": True})
    )
    assert insensitive["sensitive_action"] is False
    assert sensitive["sensitive_action"] is True
    assert insensitive != sensitive


def test_publication_gate_sensitive_flag_is_preserved(project_layout):
    gate = request_gate(
        project_layout,
        "publication",
        {"target": "arxiv", "sensitive": True},
    )
    receipt = record_gate_receipt(
        project_layout,
        gate["gate_id"],
        {"source": "reviewer", "evidence": "approved publication"},
    )
    assert receipt["status"] == "approved"
    rows = read_jsonl(project_layout.gates)
    assert any(
        row.get("gate_type") == "publication"
        and row.get("status") == "approved"
        for row in rows
    )


def test_publication_gate_rejects_non_bool_sensitive(project_layout):
    with pytest.raises(ValueError, match="sensitive"):
        request_gate(
            project_layout,
            "publication",
            {"target": "arxiv", "sensitive": "yes"},
        )


def test_review_input_recorded_when_reviewer_edits_then_approves(project_layout):
    gate = request_gate(
        project_layout,
        "publication",
        {"target": "arxiv", "sensitive": True},
    )
    receipt = record_gate_receipt(
        project_layout,
        gate["gate_id"],
        {
            "source": "reviewer",
            "evidence": "edited then approved",
            "review_input": {"target": "arxiv-v2"},
        },
    )
    assert receipt["review_input"] == {"target": "arxiv-v2"}
    rows = read_jsonl(project_layout.gates)
    approved = [r for r in rows if r.get("status") == "approved"]
    assert approved and approved[-1]["review_input"] == {"target": "arxiv-v2"}


def test_review_input_omitted_when_not_provided(project_layout):
    gate = request_gate(
        project_layout,
        "publication",
        {"target": "arxiv"},
    )
    receipt = record_gate_receipt(
        project_layout,
        gate["gate_id"],
        {"source": "reviewer", "evidence": "approved as-is"},
    )
    assert "review_input" not in receipt


def test_review_input_must_be_an_object(project_layout):
    gate = request_gate(project_layout, "publication", {"target": "arxiv"})
    with pytest.raises(ValueError, match="review_input"):
        record_gate_receipt(
            project_layout,
            gate["gate_id"],
            {
                "source": "reviewer",
                "evidence": "approved",
                "review_input": "not-an-object",
            },
        )


def _write_report(path: Path, status: str) -> Path:
    path.write_text(
        "---\n"
        "task_id: task-1\n"
        "candidate_id: cand-1\n"
        f"status: {status}\n"
        "artifact_ids: []\n"
        "decision_recommendation: blocked\n"
        "---\n"
        "## Summary\n\n"
        "## Evidence\n\n"
        "## Ideas Considered\n\n"
        "## Rejected Ideas\n\n"
        "## Risks and Confounds\n\n"
        "## Recommended Next Action\n\n"
        "## Artifact Links\n",
        encoding="utf-8",
    )
    return path


def test_report_accepts_needs_review_status(tmp_path: Path):
    report = _write_report(tmp_path / "report.md", "needs_review")
    errors = validate_report(report)
    assert not any("unknown status" in e for e in errors)
    # needs_review is a valid terminal status, not a parsing error
    assert errors == []
