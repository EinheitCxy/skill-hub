from __future__ import annotations

from dataclasses import replace
import importlib
import json
from pathlib import Path
import sys

import pytest

from autoresearch.artifacts import (
    register_artifact,
    resolve_artifact,
    verify_artifact,
)
from autoresearch.candidates import create_candidate
from autoresearch.contracts import EnvironmentSpec, RunSpec
from autoresearch.evaluation import (
    bind_evaluation_cycle_checkpoint,
    lock_evaluation_spec,
    promote_current,
    promote_provisional,
)
from autoresearch.governance import record_gate_receipt, request_gate
from autoresearch.io import read_json, read_jsonl, sha256_file
from autoresearch.project import initialize_project
from autoresearch.workflow import complete_task, open_task


PROFILE = (
    Path(__file__).parents[1]
    / "assets"
    / "research-project"
    / "research.yaml"
)
EXPECTED_BEST_POINTER_FIELDS = {
    "candidate_id",
    "report_artifact_id",
    "evaluation_cycle_checkpoint_id",
    "promoted_at",
}


def approve(layout, gate_type: str, payload: dict):
    request = request_gate(layout, gate_type, payload)
    return record_gate_receipt(
        layout,
        request["gate_id"],
        {
            "source": "test",
            "evidence": f"rick-approved-{gate_type}",
        },
    )


def executor_module():
    return importlib.import_module("autoresearch.executors")


def test_idea_to_current_best_requires_every_gate_and_artifact(
    tmp_path: Path,
):
    layout = initialize_project(tmp_path, PROFILE)
    lock_path = layout.root / "fixture.lock"
    lock_path.write_text("fixture-lock", encoding="utf-8")
    lock_hash = sha256_file(lock_path)
    unapproved_run = RunSpec(
        run_id="run-1",
        environment_id="fixture-env",
        dependency_lock_hash=lock_hash,
        backend="command-local",
        cwd=layout.root,
        argv=(sys.executable, "-c", "print('official-result')"),
        timeout_seconds=5,
        resource_limits={
            "wall_seconds": 5,
            "cpu_seconds": 5,
            "gpu_seconds": 0,
            "storage_bytes": 1_000_000,
        },
        input_paths=(lock_path,),
        output_paths=(),
        expected_artifact_types=(),
        candidate_id="cand-1",
        evaluation_spec_id="eval-spec-1",
        evaluation_cycle_checkpoint_id="evaluation-cycle-unapproved",
    )
    with pytest.raises(
        PermissionError,
        match="modification and budget approval required",
    ):
        executor_module().execute_run(layout, unapproved_run)
    assert not (layout.runs / "run-1").exists()

    approve(
        layout,
        "modification",
        {
            "editable_paths": [str(layout.root)],
            "append_only_paths": [str(layout.root / ".research")],
            "read_only_paths": [],
            "allowed_actions": [
                "create",
                "modify",
                "execute",
                "cleanup-temporary",
            ],
            "allowed_commands": [sys.executable],
            "dependency_policy": {
                "allowed": ["pyyaml>=6,<7"],
                "undeclared_requires_gate": True,
            },
            "model_policy": {
                "allowed_families": ["test-fixture"],
                "external_service_change_requires_gate": True,
            },
            "network": {"mode": "denied", "allowed_services": []},
            "approval_required_actions": [
                "delete",
                "publish",
                "sensitive-data-access",
            ],
        },
    )
    approve(
        layout,
        "budget",
        {
            "schema_version": 1,
            "limits": {
                "wall_seconds": 86_400,
                "cpu_seconds": 86_400,
                "gpu_type": None,
                "gpu_seconds": 86_400,
                "storage_bytes": 2_147_483_648,
                "official_candidates": 3,
                "loop-subagent-bound": 4,
                "retries": 3,
                "debug_candidates": 3,
            },
            "storage_accounting": {
                "count_snapshot_copy_once": True,
                "excluded_artifact_types": ["model-weight"],
            },
        },
    )

    baseline = create_candidate(
        layout,
        {
            "candidate_id": "baseline-1",
            "kind": "baseline",
            "status": "official",
            "parent_id": None,
            "hypothesis_id": "baseline",
            "search_role": None,
            "mechanism": "",
            "primary_change": "none",
            "data_intervention": "none",
            "objective": "accuracy",
            "expected_observation": "reference score",
            "falsification_condition": "reference run invalid",
            "evaluation_spec_id": None,
            "budget_slice": {},
            "code_artifact_ids": [],
            "configuration_artifact_ids": [],
            "run_artifact_ids": [],
            "evaluation_report_ids": [],
            "debug_depth": 0,
            "scientific_depth": 0,
        },
    )
    evaluation_spec = {
        "evaluation_spec_id": "eval-spec-1",
        "dataset_id": "dataset-v1",
        "split_id": "split-v1",
        "baseline_candidate_id": baseline["candidate_id"],
        "primary_metric": {
            "name": "accuracy",
            "direction": "maximize",
            "noise": 0.01,
        },
        "secondary_metrics": [],
        "unacceptable_regressions": {},
        "slice_requirements": ["per-class"],
        "qualitative_requirements": ["representative-errors"],
        "resource_metrics": [
            "latency_ms",
            "peak_memory_mb",
            "cost_usd",
        ],
        "promotion_rule": {
            "minimum_primary_gain": 0.01,
            "allow_unacceptable_regression": False,
        },
        "seed_policy": {"seeds": [1, 2, 3]},
        "sample_count_policy": {"minimum": 3},
        "hard_gates": [
            "execution validity",
            "contract comparability",
            "evidence completeness",
        ],
        "robustness_requirements": ["replication"],
    }
    approve(layout, "evaluation", evaluation_spec)
    lock_evaluation_spec(layout, evaluation_spec)
    comparison = bind_evaluation_cycle_checkpoint(layout, "cycle-1")
    candidate = create_candidate(
        layout,
        {
            "candidate_id": "cand-1",
            "kind": "architecture-model",
            "status": "official",
            "parent_id": baseline["candidate_id"],
            "hypothesis_id": "h1",
            "search_role": "explore",
            "mechanism": "deeper encoder improves representation capacity",
            "primary_change": "increase encoder depth",
            "data_intervention": "none",
            "objective": "accuracy",
            "expected_observation": "accuracy improves",
            "falsification_condition": "gain is below noise",
            "evaluation_spec_id": "eval-spec-1",
            "budget_slice": {"wall_seconds": 300},
            "code_artifact_ids": [],
            "configuration_artifact_ids": [],
            "run_artifact_ids": [],
            "evaluation_report_ids": [],
            "debug_depth": 0,
            "scientific_depth": 1,
        },
    )
    executor_module().prepare_environment(
        layout,
        EnvironmentSpec(
            environment_id="fixture-env",
            backend="command-local",
            cwd=layout.root,
            prepare_argv=(),
            dependencies=(),
            dependency_lock_path=lock_path,
            dependency_lock_hash=lock_hash,
            allowed_write_paths=(layout.root,),
            temporary_paths=(),
            timeout_seconds=5,
        ),
    )
    run_spec = replace(
        unapproved_run,
        evaluation_cycle_checkpoint_id=comparison["checkpoint_id"],
    )
    run = executor_module().execute_run(layout, run_spec)
    assert run["status"] == "completed"
    assert read_jsonl(layout.model_calls) == []

    evaluation_path = layout.runs / "run-1" / "evaluation.json"
    evaluation_path.write_text(
        json.dumps(
            {
                "report_id": "eval-1",
                "candidate_id": candidate["candidate_id"],
                "evaluation_spec_id": "eval-spec-1",
                "evaluation_cycle_checkpoint_id": comparison[
                    "checkpoint_id"
                ],
                "official": True,
                "execution_valid": True,
                "comparable": True,
                "evidence_complete": True,
                "primary_gain": 0.02,
                "metrics": {"accuracy": 0.91},
                "uncertainty": {"accuracy_std": 0.005},
                "slices": {"per-class": {"macro_accuracy": 0.90}},
                "qualitative": {
                    "representative-errors": ["fixture-error-1"]
                },
                "resources": {
                    "latency_ms": 10.0,
                    "peak_memory_mb": 128.0,
                    "cost_usd": 0.0,
                },
                "objective": 0.91,
                "robustness": 0.90,
                "evidence_strength": 0.90,
                "information_gain": 0.80,
                "cost": 0.0,
                "complexity": 1.0,
                "confound_penalty": 0.0,
            }
        ),
        encoding="utf-8",
    )
    evaluation = register_artifact(
        layout,
        evaluation_path,
        {
            "type": "evaluation-report",
            "task_id": "evaluate-cand-1",
            "candidate_id": candidate["candidate_id"],
            "run_id": run_spec.run_id,
            "producer": "research-experiment-evaluation",
            "report_id": "eval-1",
            "storage": "snapshot",
            "provenance_tags": ["official-evaluation"],
            "comparability_tags": [
                "dataset-v1",
                "split-v1",
                "eval-spec-1",
                comparison["checkpoint_id"],
            ],
        },
    )
    provisional_state = promote_provisional(
        layout, candidate["candidate_id"], evaluation["artifact_id"]
    )
    provisional = provisional_state["provisional_best"]
    assert set(provisional) == EXPECTED_BEST_POINTER_FIELDS
    assert provisional["candidate_id"] == candidate["candidate_id"]
    assert provisional["report_artifact_id"] == evaluation["artifact_id"]
    assert provisional["evaluation_cycle_checkpoint_id"] == (
        comparison["checkpoint_id"]
    )
    baseline_pointer = provisional_state["current_best"]
    assert set(baseline_pointer) == EXPECTED_BEST_POINTER_FIELDS
    assert baseline_pointer["candidate_id"] == baseline["candidate_id"]
    assert baseline_pointer["report_artifact_id"] is None
    assert baseline_pointer["evaluation_cycle_checkpoint_id"] is None

    robustness_path = layout.runs / "run-1" / "replication.json"
    robustness_path.write_text(
        json.dumps(
            {
                "report_id": "replication-1",
                "candidate_id": candidate["candidate_id"],
                "evaluation_spec_id": "eval-spec-1",
                "evaluation_cycle_checkpoint_id": comparison[
                    "checkpoint_id"
                ],
                "type": "replication",
                "passed": True,
                "seeds": [1, 2, 3],
            }
        ),
        encoding="utf-8",
    )
    robustness = register_artifact(
        layout,
        robustness_path,
        {
            "type": "replication",
            "task_id": "replicate-cand-1",
            "candidate_id": candidate["candidate_id"],
            "run_id": run_spec.run_id,
            "producer": "research-experiment-evaluation",
            "report_id": "replication-1",
            "storage": "snapshot",
            "provenance_tags": ["robustness-validation"],
            "comparability_tags": [
                "dataset-v1",
                "split-v1",
                "eval-spec-1",
                comparison["checkpoint_id"],
            ],
        },
    )
    current_state = promote_current(
        layout, candidate["candidate_id"], [robustness["artifact_id"]]
    )
    assert current_state["current_best"] == provisional
    assert current_state["provisional_best"] is None

    task = open_task(
        layout,
        {
            "cycle_id": "cycle-1",
            "task_id": "evaluate-cand-1",
            "candidate_id": candidate["candidate_id"],
            "stage_id": "intake",
            "primary_skill": "autoresearch-loop",
            "capability_skills": [],
            "input_artifact_ids": [
                evaluation["artifact_id"],
                robustness["artifact_id"],
            ],
            "budget_slice": {"wall_seconds": 30},
            "editable_paths": [],
            "output_path": (
                "cycles/cycle-1/tasks/evaluate-cand-1/report.md"
            ),
            "stop_conditions": [
                "missing artifact",
                "budget exhausted",
            ],
            "work_type": "simple",
        },
    )
    # The intake stage declares `research-request` as its output; the
    # completion must reference a matching artifact type.
    research_request_path = layout.root / "research-request.json"
    research_request_path.write_text(
        json.dumps({"request": "reproduce and evaluate candidate cand-1"}),
        encoding="utf-8",
    )
    research_request = register_artifact(
        layout,
        research_request_path,
        {
            "type": "research-request",
            "producer": "research-coordinator",
            "storage": "snapshot",
            "provenance_tags": ["intake-output"],
            "comparability_tags": [],
        },
    )
    report_path = Path(task["report_path"])
    report_path.write_text(
        "\n".join(
            [
                "---",
                "task_id: evaluate-cand-1",
                "candidate_id: cand-1",
                "status: completed",
                "artifact_ids:",
                f"  - {research_request['artifact_id']}",
                f"  - {evaluation['artifact_id']}",
                f"  - {robustness['artifact_id']}",
                "decision_recommendation: keep",
                "---",
                "## Summary",
                "Candidate passed the locked contract.",
                "## Evidence",
                "Registered evaluation and replication artifacts.",
                "## Ideas Considered",
                "Promotion after robustness.",
                "## Rejected Ideas",
                "Promotion before robustness.",
                "## Risks and Confounds",
                "Fixture-only evidence.",
                "## Recommended Next Action",
                "Archive the verified fixture.",
                "## Artifact Links",
                f"- {research_request['artifact_id']}",
                f"- {evaluation['artifact_id']}",
                f"- {robustness['artifact_id']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    complete_task(layout, "evaluate-cand-1", report_path)

    final_state = read_json(layout.state)
    assert set(final_state["current_best"]) == EXPECTED_BEST_POINTER_FIELDS
    assert final_state["current_best"] == provisional
    assert all(
        verify_artifact(layout, row["artifact_id"])["valid"]
        for row in read_jsonl(layout.artifact_index)
    )

    evaluation_anchor = final_state["active_evaluation_spec"]
    evaluation_spec_artifact = resolve_artifact(
        layout,
        evaluation_anchor["artifact_id"],
    )
    evaluation_spec_path = Path(evaluation_spec_artifact["path"])
    changed_evaluation_spec = read_json(evaluation_spec_path)
    changed_evaluation_spec["primary_metric"] = {"name": "accuracy", "direction": "maximize", "noise": 0.99}
    evaluation_spec_path.write_text(
        json.dumps(changed_evaluation_spec, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError,
        match="active EvaluationSpec artifact is invalid",
    ):
        bind_evaluation_cycle_checkpoint(layout, "cycle-after-evaluator-change")
