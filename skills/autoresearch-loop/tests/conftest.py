import importlib
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from autoresearch.candidates import create_candidate
from autoresearch.contracts import EnvironmentSpec, RunSpec
from autoresearch.evaluation import (
    bind_evaluation_cycle_checkpoint,
    lock_evaluation_spec,
)
from autoresearch.governance import record_gate_receipt, request_gate
from autoresearch.io import sha256_file
from autoresearch.project import initialize_project


PROFILE = (
    Path(__file__).parents[1]
    / "assets"
    / "research-project"
    / "research.yaml"
)
LOCKED_SPEC = {
    "evaluation_spec_id": "eval-spec-1",
    "dataset_id": "dataset-v1",
    "split_id": "split-v1",
    "baseline_candidate_id": "baseline-1",
    "primary_metric": {
        "name": "accuracy",
        "direction": "maximize",
        "noise": 0.01,
    },
    "secondary_metrics": [],
    "unacceptable_regressions": {},
    "slice_requirements": ["per-class"],
    "qualitative_requirements": ["representative-errors"],
    "resource_metrics": ["latency_ms", "peak_memory_mb", "cost_usd"],
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
BASELINE_PROPOSAL = {
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
}
CANDIDATE_PROPOSAL = {
    "candidate_id": "cand-1",
    "kind": "architecture-model",
    "status": "official",
    "parent_id": "baseline-1",
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
}
PROPOSED_BUDGET_PAYLOAD = {
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
}
BOUNDED_TEST_BUDGET_PAYLOAD = {
    "schema_version": 1,
    "limits": {
        "wall_seconds": 3600,
        "cpu_seconds": 3600,
        "gpu_type": None,
        "gpu_seconds": 0,
        "storage_bytes": 1_000_000_000,
        "official_candidates": 5,
        "loop-subagent-bound": 4,
        "retries": 2,
        "debug_candidates": 2,
    },
    "storage_accounting": {
        "count_snapshot_copy_once": True,
        "excluded_artifact_types": ["model-weight"],
    },
}


@pytest.fixture
def project_layout(tmp_path: Path):
    return initialize_project(tmp_path, PROFILE)


@pytest.fixture
def approve_envelopes(project_layout):
    return _approve_envelopes(
        project_layout, BOUNDED_TEST_BUDGET_PAYLOAD
    )


@pytest.fixture
def approve_execution_envelopes(project_layout):
    return _approve_envelopes(
        project_layout, PROPOSED_BUDGET_PAYLOAD
    )


def _approve_envelopes(project_layout, budget_payload):
    modification = request_gate(
        project_layout,
        "modification",
        {
            "editable_paths": [str(project_layout.root)],
            "append_only_paths": [str(project_layout.root / ".research")],
            "read_only_paths": [],
            "allowed_actions": ["create", "modify", "execute", "cleanup-temporary"],
            "allowed_commands": ["uv", sys.executable],
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
    record_gate_receipt(
        project_layout,
        modification["gate_id"],
        {"source": "test", "evidence": "rick-approved-modification-envelope"},
    )
    budget = request_gate(
        project_layout,
        "budget",
        budget_payload,
    )
    record_gate_receipt(
        project_layout,
        budget["gate_id"],
        {"source": "test", "evidence": "rick-approved-budget-envelope"},
    )
    return project_layout


@pytest.fixture
def approved_project(approve_envelopes):
    return approve_envelopes


@pytest.fixture
def approved_execution_project(approve_execution_envelopes):
    return approve_execution_envelopes


@pytest.fixture
def approved_run_spec(approved_execution_project):
    evaluation = request_gate(
        approved_execution_project, "evaluation", LOCKED_SPEC
    )
    record_gate_receipt(
        approved_execution_project,
        evaluation["gate_id"],
        {
            "source": "test",
            "evidence": "rick-approved-evaluation-contract",
        },
    )
    lock_evaluation_spec(approved_execution_project, LOCKED_SPEC)
    create_candidate(approved_execution_project, BASELINE_PROPOSAL)
    comparison = bind_evaluation_cycle_checkpoint(
        approved_execution_project, "cycle-fixture"
    )
    create_candidate(approved_execution_project, CANDIDATE_PROPOSAL)

    lock_path = approved_execution_project.root / "fixture.lock"
    lock_path.write_text("fixture-lock", encoding="utf-8")
    lock_hash = sha256_file(lock_path)
    importlib.import_module(
        "autoresearch.executors"
    ).prepare_environment(
        approved_execution_project,
        EnvironmentSpec(
            environment_id="fixture-env",
            backend="command-local",
            cwd=approved_execution_project.root,
            prepare_argv=(),
            dependencies=(),
            dependency_lock_path=lock_path,
            dependency_lock_hash=lock_hash,
            allowed_write_paths=(approved_execution_project.root,),
            temporary_paths=(),
            timeout_seconds=5,
        ),
    )
    return RunSpec(
        run_id="run-approved",
        environment_id="fixture-env",
        dependency_lock_hash=lock_hash,
        backend="command-local",
        cwd=approved_execution_project.root,
        argv=(sys.executable, "-c", "print('ok')"),
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
        evaluation_cycle_checkpoint_id=comparison["checkpoint_id"],
    )
