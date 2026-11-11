from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from autoresearch.artifacts import (
    list_artifacts,
    register_artifact,
    resolve_artifact,
    verify_artifact,
)
from autoresearch.candidates import (
    create_candidate,
    load_candidates,
    update_candidate,
)
from autoresearch.evaluation import (
    bind_evaluation_cycle_checkpoint,
    check_evaluation_report,
    evaluate_hard_gates,
    lock_evaluation_spec,
    pareto_frontier,
    promote_current,
    promote_provisional,
    validate_evaluation_cycle_checkpoint,
)
from autoresearch.governance import (
    build_evaluation_gate_envelope,
    record_gate_receipt,
    request_gate,
)
from autoresearch.io import read_json, read_jsonl
from researchctl import main


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


def evaluation_envelope(spec: dict, **overrides) -> dict:
    """Build a split v1 evaluation gate envelope for the given EvaluationSpec."""
    return build_evaluation_gate_envelope(
        stage_id=overrides.get("stage_id", "baseline"),
        source_revision=overrides.get("source_revision", 0),
        source_state_hash=overrides.get("source_state_hash", "0" * 64),
        graph_hash=overrides.get("graph_hash", "1" * 64),
        output_artifacts=overrides.get("output_artifacts", []),
        evaluation_spec_id=spec["evaluation_spec_id"],
        payload_hash=canonical_payload_hash(spec),
    )


def test_evaluation_lock_accepts_split_stage_envelope(approved_project):
    spec = deepcopy(LOCKED_SPEC)
    envelope = evaluation_envelope(spec)
    request = request_gate(
        approved_project,
        "evaluation",
        envelope,
    )
    record_gate_receipt(
        approved_project,
        request["gate_id"],
        {"source": "test", "evidence": "separate stage gate and contract"},
    )

    locked = lock_evaluation_spec(approved_project, spec)

    assert locked["active_evaluation_spec"]["gate_id"] == request["gate_id"]


def test_evaluation_lock_reads_legacy_direct_gate_for_recovery(approved_project):
    """Legacy direct gates (payload IS the full EvaluationSpec) remain
    readable so in-flight projects can recover after the envelope migration.
    New callers must use the split envelope form.
    """
    spec = deepcopy(LOCKED_SPEC)
    legacy_request = request_gate(approved_project, "evaluation", spec)
    record_gate_receipt(
        approved_project,
        legacy_request["gate_id"],
        {"source": "test", "evidence": "legacy-direct-gate-for-recovery"},
    )

    locked = lock_evaluation_spec(approved_project, spec)

    assert locked["active_evaluation_spec"]["gate_id"] == legacy_request["gate_id"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda env: env.update(schema_version=2),
            "schema_version",
        ),
        (
            lambda env: env.update(stage_gate="not-a-dict"),
            "stage gate",
        ),
        (
            lambda env: env["stage_gate"].pop("graph_hash"),
            "stage gate payload schema is invalid",
        ),
        (
            lambda env: env["evaluation_spec"].update(payload_hash="bad"),
            "payload_hash must be a SHA-256",
        ),
        (
            lambda env: env["evaluation_spec"].update(evaluation_spec_id=""),
            "evaluation_spec_id must be a non-empty string",
        ),
        (
            lambda env: env["stage_gate"]["output_artifacts"].extend(
                [
                    {"artifact_id": "dup", "sha256": "a" * 64},
                    {"artifact_id": "dup", "sha256": "b" * 64},
                ]
            ),
            "artifact IDs must be unique",
        ),
    ],
)
def test_request_gate_rejects_malformed_evaluation_envelope(
    approved_project, mutate, message
):
    envelope = evaluation_envelope(deepcopy(LOCKED_SPEC))
    mutate(envelope)

    with pytest.raises(ValueError, match=message):
        request_gate(approved_project, "evaluation", envelope)


def test_record_coordinator_gate_receipt_rejects_malformed_evaluation_envelope(
    project_layout,
):
    from autoresearch import governance

    envelope = evaluation_envelope(deepcopy(LOCKED_SPEC))
    envelope["schema_version"] = 2

    with pytest.raises(ValueError, match="schema_version"):
        governance.record_coordinator_gate_receipt(
            project_layout, "evaluation", envelope
        )


CHECKPOINT_FIELDS = {
    "schema_version",
    "record_type",
    "checkpoint_id",
    "cycle_id",
    "evaluation_spec_id",
    "evaluation_spec_artifact_id",
    "evaluation_spec_artifact_sha256",
    "comparison_candidate_id",
    "comparison_report_artifact_id",
    "comparison_state_revision",
    "comparison_state_hash",
    "bound_at",
}
CHECKPOINT_IDENTITY_FIELDS = {
    "schema_version",
    "record_type",
    "cycle_id",
    "evaluation_spec_id",
    "evaluation_spec_artifact_id",
    "evaluation_spec_artifact_sha256",
    "comparison_candidate_id",
    "comparison_report_artifact_id",
    "comparison_state_revision",
    "comparison_state_hash",
}

PARETO_FIXTURES = [
    {
        "candidate_id": "fast",
        "objective": 0.90,
        "robustness": 0.70,
        "evidence_strength": 0.80,
        "information_gain": 0.70,
        "cost": 1.0,
        "complexity": 1.0,
        "confound_penalty": 0.10,
    },
    {
        "candidate_id": "robust",
        "objective": 0.88,
        "robustness": 0.95,
        "evidence_strength": 0.90,
        "information_gain": 0.80,
        "cost": 3.0,
        "complexity": 2.0,
        "confound_penalty": 0.05,
    },
    {
        "candidate_id": "dominated",
        "objective": 0.85,
        "robustness": 0.60,
        "evidence_strength": 0.60,
        "information_gain": 0.50,
        "cost": 4.0,
        "complexity": 3.0,
        "confound_penalty": 0.20,
    },
]


def candidate(candidate_id: str, *, baseline: bool = False) -> dict:
    return {
        "candidate_id": candidate_id,
        "kind": "baseline" if baseline else "objective-loss",
        "status": "official",
        "parent_id": None if baseline else "baseline-1",
        "hypothesis_id": "baseline" if baseline else "h2",
        "search_role": None if baseline else "explore",
        "mechanism": "" if baseline else "contrastive separation improves boundaries",
        "primary_change": "none" if baseline else "add contrastive loss",
        "data_intervention": "none",
        "objective": "accuracy",
        "expected_observation": (
            "reference score" if baseline else "accuracy improves"
        ),
        "falsification_condition": (
            "reference run invalid" if baseline else "gain is below noise"
        ),
        "evaluation_spec_id": None if baseline else "eval-spec-1",
        "budget_slice": (
            {} if baseline else {"wall_seconds": 300}
        ),
        "code_artifact_ids": [],
        "configuration_artifact_ids": [],
        "run_artifact_ids": [],
        "evaluation_report_ids": [],
        "debug_depth": 0,
        "scientific_depth": 0 if baseline else 1,
    }


def approve_and_lock(layout, spec: dict | None = None) -> dict:
    value = deepcopy(spec or LOCKED_SPEC)
    request = request_gate(layout, "evaluation", evaluation_envelope(value))
    record_gate_receipt(
        layout,
        request["gate_id"],
        {"source": "test", "evidence": "rick-approved-evaluation-contract"},
    )
    return lock_evaluation_spec(layout, value)


def prepare_candidates(layout) -> dict:
    create_candidate(layout, candidate("baseline-1", baseline=True))
    checkpoint = bind_evaluation_cycle_checkpoint(layout, "cycle-1")
    create_candidate(layout, candidate("cand-2"))
    return checkpoint


def evaluation_payload(
    evaluation_cycle_checkpoint_id: str = "evaluation-cycle-unbound",
    **overrides,
) -> dict:
    payload = {
        "report_id": "eval-2",
        "candidate_id": "cand-2",
        "evaluation_spec_id": "eval-spec-1",
        "official": True,
        "execution_valid": True,
        "comparable": True,
        "evidence_complete": True,
        "evaluation_cycle_checkpoint_id": evaluation_cycle_checkpoint_id,
        "primary_gain": 0.02,
        "unacceptable_regressions": [],
        "pareto_eligible": True,
        "objective": 0.91,
        "robustness": 0.90,
        "evidence_strength": 0.90,
        "information_gain": 0.80,
        "cost": 1.0,
        "complexity": 1.0,
        "confound_penalty": 0.05,
        "metrics": {"accuracy": 0.91},
        "uncertainty": {"accuracy_std": 0.005},
        "slices": {"per-class": {"macro_accuracy": 0.90}},
        "qualitative": {"representative-errors": ["fixture-error-1"]},
        "resources": {
            "latency_ms": 10.0,
            "peak_memory_mb": 128.0,
            "cost_usd": 0.0,
        },
    }
    payload.update(overrides)
    return payload


def spec_with_regression_contract() -> dict:
    spec = deepcopy(LOCKED_SPEC)
    spec["resource_metrics"] = [
        "latency_ms",
        "throughput_qps",
        "peak_memory_mb",
        "cost_usd",
    ]
    spec["unacceptable_regressions"] = {
        "accuracy": {
            "source": "metrics",
            "direction": "maximize",
            "threshold": 0.90,
        },
        "error_rate": {
            "source": "metrics",
            "direction": "minimize",
            "threshold": 0.10,
        },
        "throughput_qps": {
            "source": "resources",
            "direction": "maximize",
            "threshold": 100.0,
        },
        "latency_ms": {
            "source": "resources",
            "direction": "minimize",
            "threshold": 10.0,
        },
    }
    return spec


def register_json_report(
    layout,
    tmp_path: Path,
    payload: dict,
    *,
    artifact_type: str,
    metadata_report_id: str | None = None,
) -> dict:
    path = tmp_path / f"{payload['report_id']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return register_artifact(
        layout,
        path,
        {
            "type": artifact_type,
            "task_id": f"evaluation-{payload['candidate_id']}",
            "candidate_id": payload["candidate_id"],
            "producer": "research-experiment-evaluation",
            "report_id": metadata_report_id or payload["report_id"],
            "storage": "snapshot",
            "provenance_tags": ["official-evaluation"],
            "comparability_tags": ["dataset-v1", "split-v1", "eval-spec-1"],
        },
    )


def canonical_payload_hash(value: dict) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_evaluation_spec_rejects_dynamic_current_best_field(
    approved_project,
):
    dynamic = deepcopy(LOCKED_SPEC)
    dynamic["current_best_candidate_id"] = "baseline-1"
    request = request_gate(
        approved_project, "evaluation", evaluation_envelope(dynamic)
    )
    record_gate_receipt(
        approved_project,
        request["gate_id"],
        {"source": "test", "evidence": "reject-dynamic-comparator"},
    )

    with pytest.raises(ValueError, match="current_best_candidate_id"):
        lock_evaluation_spec(approved_project, dynamic)


def test_evaluation_cycle_checkpoint_is_exact_immutable_artifact(
    approved_project,
):
    approve_and_lock(approved_project)
    create_candidate(
        approved_project, candidate("baseline-1", baseline=True)
    )

    first = bind_evaluation_cycle_checkpoint(approved_project, "cycle-1")
    checkpoint_path = (
        approved_project.cycles
        / "cycle-1"
        / "evaluation-comparison.json"
    )
    before_bytes = checkpoint_path.read_bytes()
    before_artifacts = list_artifacts(
        approved_project,
        {"type": "evaluation-cycle-checkpoint"},
    )
    second = bind_evaluation_cycle_checkpoint(
        approved_project, "cycle-1"
    )
    after_artifacts = list_artifacts(
        approved_project,
        {"type": "evaluation-cycle-checkpoint"},
    )

    assert set(first) == CHECKPOINT_FIELDS
    assert first == second == read_json(checkpoint_path)
    assert checkpoint_path.read_bytes() == before_bytes
    assert len(before_artifacts) == 1
    assert after_artifacts == before_artifacts
    assert (
        verify_artifact(
            approved_project, before_artifacts[0]["artifact_id"]
        )["valid"]
        is True
    )
    assert read_json(Path(before_artifacts[0]["path"])) == first


@pytest.mark.parametrize("missing", sorted(CHECKPOINT_FIELDS))
def test_evaluation_cycle_checkpoint_rejects_every_missing_key(
    approved_project, missing: str
):
    approve_and_lock(approved_project)
    create_candidate(
        approved_project, candidate("baseline-1", baseline=True)
    )
    checkpoint = bind_evaluation_cycle_checkpoint(
        approved_project, "cycle-schema"
    )
    del checkpoint[missing]

    with pytest.raises(ValueError, match="checkpoint"):
        validate_evaluation_cycle_checkpoint(
            checkpoint,
            initial_baseline_candidate_id="baseline-1",
        )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("schema_version", True),
        ("record_type", "other"),
        ("checkpoint_id", "evaluation-cycle-short"),
        ("cycle_id", ""),
        ("evaluation_spec_id", 7),
        ("evaluation_spec_artifact_id", ""),
        ("evaluation_spec_artifact_sha256", "bad"),
        ("comparison_candidate_id", ""),
        ("comparison_report_artifact_id", 7),
        ("comparison_state_revision", True),
        ("comparison_state_hash", "bad"),
        ("bound_at", "not-a-time"),
    ],
)
def test_evaluation_cycle_checkpoint_rejects_mistyped_keys(
    approved_project, field: str, invalid
):
    approve_and_lock(approved_project)
    create_candidate(
        approved_project, candidate("baseline-1", baseline=True)
    )
    checkpoint = bind_evaluation_cycle_checkpoint(
        approved_project, f"cycle-{field}"
    )
    checkpoint[field] = invalid

    with pytest.raises(ValueError, match="checkpoint"):
        validate_evaluation_cycle_checkpoint(
            checkpoint,
            initial_baseline_candidate_id="baseline-1",
        )


def test_evaluation_cycle_checkpoint_rejects_unknown_key(
    approved_project,
):
    approve_and_lock(approved_project)
    create_candidate(
        approved_project, candidate("baseline-1", baseline=True)
    )
    checkpoint = bind_evaluation_cycle_checkpoint(
        approved_project, "cycle-unknown"
    )
    checkpoint["legacy_comparison_target"] = "baseline-1"

    with pytest.raises(ValueError, match="checkpoint"):
        validate_evaluation_cycle_checkpoint(
            checkpoint,
            initial_baseline_candidate_id="baseline-1",
        )


def test_evaluation_cycle_checkpoint_enforces_bootstrap_only_null_report(
    approved_project,
):
    approve_and_lock(approved_project)
    create_candidate(
        approved_project, candidate("baseline-1", baseline=True)
    )
    checkpoint = bind_evaluation_cycle_checkpoint(
        approved_project, "cycle-bootstrap-null"
    )
    checkpoint["comparison_candidate_id"] = "not-initial-baseline"
    identity = {
        field: checkpoint[field]
        for field in CHECKPOINT_IDENTITY_FIELDS
    }
    checkpoint["checkpoint_id"] = (
        "evaluation-cycle-" + canonical_payload_hash(identity)
    )

    with pytest.raises(ValueError, match="baseline bootstrap"):
        validate_evaluation_cycle_checkpoint(
            checkpoint,
            initial_baseline_candidate_id="baseline-1",
        )


def test_evaluation_cycle_checkpoint_recovery_reuses_bound_at_and_artifact(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    from autoresearch.workflow import checkpoints as workflow

    approve_and_lock(approved_project)
    create_candidate(
        approved_project, candidate("baseline-1", baseline=True)
    )
    original_register = workflow.register_artifact

    def fail_registration(*args, **kwargs):
        raise OSError("injected checkpoint registration failure")

    monkeypatch.setattr(workflow, "register_artifact", fail_registration)
    with pytest.raises(OSError, match="registration failure"):
        bind_evaluation_cycle_checkpoint(
            approved_project, "cycle-recovery"
        )
    checkpoint_path = (
        approved_project.cycles
        / "cycle-recovery"
        / "evaluation-comparison.json"
    )
    prepared = read_json(checkpoint_path)
    monkeypatch.setattr(workflow, "register_artifact", original_register)

    recovered = bind_evaluation_cycle_checkpoint(
        approved_project, "cycle-recovery"
    )

    assert recovered == prepared
    assert recovered["bound_at"] == prepared["bound_at"]
    assert len(
        [
            artifact
            for artifact in list_artifacts(
                approved_project,
                {"type": "evaluation-cycle-checkpoint"},
            )
            if artifact["cycle_id"] == "cycle-recovery"
        ]
    ) == 1


def test_evaluation_cycle_checkpoint_conflicts_after_identity_changes(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="evaluation-report",
    )
    promote_provisional(
        approved_project, "cand-2", report["artifact_id"]
    )

    with pytest.raises(ValueError, match="identity conflicts"):
        bind_evaluation_cycle_checkpoint(approved_project, "cycle-1")


def test_incomparable_report_fails_before_metric_comparison():
    failures = evaluate_hard_gates(
        LOCKED_SPEC,
        {
            "execution_valid": True,
            "comparable": False,
            "evidence_complete": True,
            "metrics": {"accuracy": 0.99},
        },
    )
    assert failures == ["contract comparability failed"]


def test_hard_gate_failures_are_returned_in_declared_order():
    assert evaluate_hard_gates(
        LOCKED_SPEC,
        {
            "execution_valid": False,
            "comparable": False,
            "evidence_complete": False,
        },
    ) == [
        "execution validity failed",
        "contract comparability failed",
        "evidence completeness failed",
    ]


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (
            {
                "execution_valid": False,
                "comparable": True,
                "evidence_complete": True,
            },
            ["execution validity failed"],
        ),
        (
            {
                "execution_valid": True,
                "comparable": False,
                "evidence_complete": True,
            },
            ["contract comparability failed"],
        ),
    ],
)
def test_evaluation_check_stops_at_first_failed_gate_before_metrics(
    approved_project, flags, expected
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    report = evaluation_payload(**flags)
    del report["metrics"]

    assert check_evaluation_report(approved_project, report) == expected


def test_promotion_stops_at_execution_gate_before_metrics_or_pareto(
    approved_project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from autoresearch.evaluation import promotion as evaluation

    approve_and_lock(approved_project)
    prepare_candidates(approved_project)
    payload = evaluation_payload(execution_valid=False)
    del payload["metrics"]
    report = register_json_report(
        approved_project,
        tmp_path,
        payload,
        artifact_type="evaluation-report",
    )

    def fail_if_pareto_runs(*args, **kwargs):
        raise AssertionError("Pareto must not run after a failed gate")

    monkeypatch.setattr(evaluation, "pareto_frontier", fail_if_pareto_runs)
    with pytest.raises(
        ValueError, match="evaluation hard gates failed: execution validity"
    ):
        promote_provisional(
            approved_project, "cand-2", report["artifact_id"]
        )

    assert read_json(approved_project.state)["provisional_best"] is None
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == []


def test_pareto_frontier_does_not_collapse_dimensions():
    assert [row["candidate_id"] for row in pareto_frontier(PARETO_FIXTURES)] == [
        "fast",
        "robust",
    ]


def test_lock_requires_complete_spec_and_latest_approved_payload(
    approved_project,
):
    incomplete = deepcopy(LOCKED_SPEC)
    del incomplete["primary_metric"]
    request = request_gate(
        approved_project, "evaluation", evaluation_envelope(incomplete)
    )
    record_gate_receipt(
        approved_project,
        request["gate_id"],
        {"source": "test", "evidence": "approved-incomplete"},
    )
    with pytest.raises(ValueError, match="primary_metric"):
        lock_evaluation_spec(approved_project, incomplete)

    correct = request_gate(
        approved_project, "evaluation", evaluation_envelope(LOCKED_SPEC)
    )
    record_gate_receipt(
        approved_project,
        correct["gate_id"],
        {"source": "test", "evidence": "approved-correct"},
    )
    other = deepcopy(LOCKED_SPEC)
    other["evaluation_spec_id"] = "eval-spec-other"
    latest = request_gate(
        approved_project, "evaluation", evaluation_envelope(other)
    )
    record_gate_receipt(
        approved_project,
        latest["gate_id"],
        {"source": "test", "evidence": "approved-latest"},
    )
    with pytest.raises(ValueError, match="latest approved evaluation"):
        lock_evaluation_spec(approved_project, LOCKED_SPEC)


def test_lock_persists_hash_bound_active_anchor(approved_project):
    state = approve_and_lock(approved_project)

    anchor = state["active_evaluation_spec"]
    assert anchor["evaluation_spec_id"] == "eval-spec-1"
    assert anchor["artifact_sha256"]
    assert anchor["gate_payload_hash"]
    assert read_json(approved_project.state) == state


def test_lock_rejects_python_equal_numbers_with_different_canonical_hashes(
    approved_project,
):
    approved = deepcopy(LOCKED_SPEC)
    approved["promotion_rule"]["minimum_primary_gain"] = 1
    request = request_gate(
        approved_project, "evaluation", evaluation_envelope(approved)
    )
    record_gate_receipt(
        approved_project,
        request["gate_id"],
        {"source": "test", "evidence": "approved-integer-contract"},
    )
    supplied = deepcopy(approved)
    supplied["promotion_rule"]["minimum_primary_gain"] = 1.0

    with pytest.raises(ValueError, match="payload hash"):
        lock_evaluation_spec(approved_project, supplied)

    assert read_json(approved_project.state)["active_evaluation_spec"] is None


def test_lock_accepts_key_reordering_with_same_canonical_hash(
    approved_project,
):
    approved = deepcopy(LOCKED_SPEC)
    envelope = evaluation_envelope(approved)
    request = request_gate(
        approved_project, "evaluation", envelope
    )
    receipt = record_gate_receipt(
        approved_project,
        request["gate_id"],
        {"source": "test", "evidence": "approved-canonical-contract"},
    )
    supplied = dict(reversed(list(approved.items())))
    supplied["promotion_rule"] = dict(
        reversed(list(approved["promotion_rule"].items()))
    )

    state = lock_evaluation_spec(approved_project, supplied)

    anchor = state["active_evaluation_spec"]
    artifact = resolve_artifact(approved_project, anchor["artifact_id"])
    artifact_payload = read_json(Path(artifact["path"]))
    assert canonical_payload_hash(artifact_payload) == (
        envelope["evaluation_spec"]["payload_hash"]
    )
    assert anchor["gate_payload_hash"] == receipt["payload_hash"]


@pytest.mark.parametrize(
    "rules",
    [
        {"accuracy": {"source": "metrics", "direction": "maximize"}},
        {
            "accuracy": {
                "source": "metrics",
                "direction": "maximize",
                "threshold": 0.90,
                "extra": True,
            }
        },
        {
            "accuracy": {
                "source": "summary",
                "direction": "maximize",
                "threshold": 0.90,
            }
        },
        {
            "accuracy": {
                "source": "metrics",
                "direction": "sideways",
                "threshold": 0.90,
            }
        },
        {
            "accuracy": {
                "source": "metrics",
                "direction": "maximize",
                "threshold": True,
            }
        },
        {
            "accuracy": {
                "source": "metrics",
                "direction": "maximize",
                "threshold": "0.90",
            }
        },
        {"": {"source": "metrics", "direction": "maximize", "threshold": 0.90}},
        {"accuracy": []},
        {
            "accuracy": {
                "source": "metrics",
                "direction": "maximize",
                "threshold": float("inf"),
            }
        },
    ],
)
def test_lock_rejects_invalid_unacceptable_regression_schema(
    approved_project, rules
):
    spec = deepcopy(LOCKED_SPEC)
    spec["unacceptable_regressions"] = rules

    with pytest.raises(ValueError, match="unacceptable_regressions|JSON"):
        request = request_gate(
            approved_project, "evaluation", evaluation_envelope(spec)
        )
        record_gate_receipt(
            approved_project,
            request["gate_id"],
            {"source": "test", "evidence": "invalid-regression-contract"},
        )
        lock_evaluation_spec(approved_project, spec)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("metrics", "accuracy", 0.89),
        ("metrics", "error_rate", 0.11),
        ("resources", "throughput_qps", 99.0),
        ("resources", "latency_ms", 10.01),
    ],
)
def test_promotion_computes_locked_regression_violations_from_report_values(
    approved_project,
    tmp_path: Path,
    section: str,
    key: str,
    value: float,
):
    approve_and_lock(approved_project, spec_with_regression_contract())
    checkpoint = prepare_candidates(approved_project)
    metrics = {"accuracy": 0.90, "error_rate": 0.10}
    resources = {
        "latency_ms": 10.0,
        "throughput_qps": 100.0,
        "peak_memory_mb": 128.0,
        "cost_usd": 0.0,
    }
    (metrics if section == "metrics" else resources)[key] = value
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            metrics=metrics,
            resources=resources,
            unacceptable_regressions=[],
        ),
        artifact_type="evaluation-report",
    )

    with pytest.raises(ValueError, match="unacceptable regression"):
        promote_provisional(
            approved_project, "cand-2", report["artifact_id"]
        )

    assert read_json(approved_project.state)["provisional_best"] is None
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == []


def test_regression_threshold_boundaries_are_accepted(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project, spec_with_regression_contract())
    checkpoint = prepare_candidates(approved_project)
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            metrics={"accuracy": 0.90, "error_rate": 0.10},
            resources={
                "latency_ms": 10.0,
                "throughput_qps": 100.0,
                "peak_memory_mb": 128.0,
                "cost_usd": 0.0,
            },
            unacceptable_regressions=["advisory-summary-is-ignored"],
        ),
        artifact_type="evaluation-report",
    )

    assert promote_provisional(
        approved_project, "cand-2", report["artifact_id"]
    )["provisional_best"]["candidate_id"] == "cand-2"


@pytest.mark.parametrize(
    ("case", "value"),
    [
        ("missing", None),
        ("non-numeric", "slow"),
        ("non-finite", float("inf")),
    ],
)
def test_locked_regression_values_fail_closed(
    approved_project,
    tmp_path: Path,
    case: str,
    value: object,
):
    approve_and_lock(approved_project, spec_with_regression_contract())
    checkpoint = prepare_candidates(approved_project)
    metrics = {"accuracy": 0.90, "error_rate": 0.10}
    resources = {
        "latency_ms": 10.0,
        "throughput_qps": 100.0,
        "peak_memory_mb": 128.0,
        "cost_usd": 0.0,
    }
    if case == "missing":
        del resources["throughput_qps"]
    else:
        resources["throughput_qps"] = value
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            metrics=metrics,
            resources=resources,
        ),
        artifact_type="evaluation-report",
    )

    with pytest.raises(ValueError):
        promote_provisional(
            approved_project, "cand-2", report["artifact_id"]
        )

    assert read_json(approved_project.state)["provisional_best"] is None
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == []


def test_provisional_requires_resolvable_cycle_checkpoint(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    prepare_candidates(approved_project)
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload("evaluation-cycle-missing"),
        artifact_type="evaluation-report",
    )

    with pytest.raises(ValueError, match="not uniquely resolvable"):
        promote_provisional(
            approved_project, "cand-2", report["artifact_id"]
        )

    assert read_json(approved_project.state)["provisional_best"] is None
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == []


def test_provisional_rejects_dominated_report_despite_advisory_eligibility(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    target = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="evaluation-report",
    )
    register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            report_id="eval-dominator",
            candidate_id="cand-dominator",
            objective=0.95,
            robustness=0.95,
            evidence_strength=0.95,
            information_gain=0.90,
            cost=0.5,
            complexity=0.5,
            confound_penalty=0.01,
            pareto_eligible=True,
        ),
        artifact_type="evaluation-report",
    )

    with pytest.raises(ValueError, match="Pareto member"):
        promote_provisional(
            approved_project, "cand-2", target["artifact_id"]
        )

    assert read_json(approved_project.state)["provisional_best"] is None
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == []


def test_provisional_binds_pareto_membership_to_target_artifact(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            report_id="eval-same-candidate-frontier",
            objective=0.95,
            robustness=0.95,
            evidence_strength=0.95,
            information_gain=0.90,
            cost=0.5,
            complexity=0.5,
            confound_penalty=0.01,
        ),
        artifact_type="evaluation-report",
    )
    dominated_target = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            report_id="eval-same-candidate-dominated",
        ),
        artifact_type="evaluation-report",
    )

    with pytest.raises(ValueError, match="Pareto member"):
        promote_provisional(
            approved_project,
            "cand-2",
            dominated_target["artifact_id"],
        )

    assert read_json(approved_project.state)["provisional_best"] is None
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == []


def test_provisional_persists_evidence_before_pointer_mutation(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="evaluation-report",
    )

    state = promote_provisional(
        approved_project, "cand-2", report["artifact_id"]
    )

    assert state["current_best"]["candidate_id"] == "baseline-1"
    assert state["provisional_best"] == {
        "candidate_id": "cand-2",
        "report_artifact_id": report["artifact_id"],
        "evaluation_cycle_checkpoint_id": checkpoint["checkpoint_id"],
        "promoted_at": state["provisional_best"]["promoted_at"],
    }
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == [report["artifact_id"]]
    candidate_updates = [
        row
        for row in read_jsonl(approved_project.journal)
        if row.get("record_type") == "candidate-update"
    ]
    state_mutations = [
        row
        for row in read_jsonl(approved_project.journal)
        if row.get("record_type") == "state-mutation"
        and row.get("operation") == "promote-provisional-best"
    ]
    assert candidate_updates
    assert state_mutations
    journal = read_jsonl(approved_project.journal)
    assert journal.index(candidate_updates[-1]) < journal.index(state_mutations[-1])


def test_provisional_retry_reuses_promoted_at_from_durable_intent(
    approved_project,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from autoresearch.workflow import mutations as workflow

    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="evaluation-report",
    )
    original_resume = workflow._resume_state_mutation

    def fail_after_intent(*args, **kwargs):
        raise OSError("injected after promotion intent")

    monkeypatch.setattr(
        workflow, "_resume_state_mutation", fail_after_intent
    )
    with pytest.raises(OSError, match="after promotion intent"):
        promote_provisional(
            approved_project, "cand-2", report["artifact_id"]
        )
    intent = next(
        read_json(path)
        for path in (
            approved_project.root
            / ".research"
            / "workflow-transactions"
        ).glob("state-mutation-*.json")
        if read_json(path).get("operation") == "promote-provisional-best"
    )
    durable_promoted_at = intent["patch"]["provisional_best"][
        "promoted_at"
    ]
    monkeypatch.setattr(
        workflow, "_resume_state_mutation", original_resume
    )

    state = promote_provisional(
        approved_project, "cand-2", report["artifact_id"]
    )

    assert state["provisional_best"]["promoted_at"] == durable_promoted_at


def test_provisional_rejects_silent_pointer_overwrite(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    create_candidate(approved_project, candidate("cand-3"))
    first = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="evaluation-report",
    )
    second = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            report_id="eval-cand-3",
            candidate_id="cand-3",
        ),
        artifact_type="evaluation-report",
    )
    first_state = promote_provisional(
        approved_project, "cand-2", first["artifact_id"]
    )

    with pytest.raises(ValueError, match="explicitly cleared"):
        promote_provisional(
            approved_project, "cand-3", second["artifact_id"]
        )

    assert read_json(approved_project.state) == first_state


def test_pointer_failure_leaves_verified_evidence_without_promotion(
    approved_project,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from autoresearch.evaluation import promotion as evaluation

    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="evaluation-report",
    )

    def fail_pointer_mutation(*args, **kwargs):
        raise OSError("injected pointer failure")

    monkeypatch.setattr(
        evaluation, "promote_provisional_best", fail_pointer_mutation
    )
    with pytest.raises(OSError, match="pointer failure"):
        promote_provisional(
            approved_project, "cand-2", report["artifact_id"]
        )

    assert read_json(approved_project.state)["provisional_best"] is None
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == [report["artifact_id"]]


def test_current_best_requires_declared_robustness(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="evaluation-report",
    )
    promote_provisional(approved_project, "cand-2", report["artifact_id"])

    with pytest.raises(ValueError, match="robustness contract"):
        promote_current(approved_project, "cand-2", [])


def test_current_promotion_requires_provisional_and_clears_it(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    evaluation = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="evaluation-report",
    )
    robustness = register_json_report(
        approved_project,
        tmp_path,
        {
            "report_id": "replication-2",
            "candidate_id": "cand-2",
            "evaluation_spec_id": "eval-spec-1",
            "evaluation_cycle_checkpoint_id": checkpoint["checkpoint_id"],
        },
        artifact_type="replication",
    )

    with pytest.raises(ValueError, match="Provisional Best"):
        promote_current(
            approved_project, "cand-2", [robustness["artifact_id"]]
        )

    promote_provisional(
        approved_project, "cand-2", evaluation["artifact_id"]
    )
    state = promote_current(
        approved_project, "cand-2", [robustness["artifact_id"]]
    )

    assert state["current_best"] == {
        "candidate_id": "cand-2",
        "report_artifact_id": evaluation["artifact_id"],
        "evaluation_cycle_checkpoint_id": checkpoint["checkpoint_id"],
        "promoted_at": state["current_best"]["promoted_at"],
    }
    assert state["provisional_best"] is None
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == [evaluation["artifact_id"], robustness["artifact_id"]]


def test_current_copies_exact_provisional_report_without_rescanning_candidate(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    dominated_target = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            report_id="eval-current-dominated",
        ),
        artifact_type="evaluation-report",
    )
    frontier = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            report_id="eval-current-frontier",
            objective=0.95,
            robustness=0.95,
            evidence_strength=0.95,
            information_gain=0.90,
            cost=0.5,
            complexity=0.5,
            confound_penalty=0.01,
        ),
        artifact_type="evaluation-report",
    )
    robustness = register_json_report(
        approved_project,
        tmp_path,
        {
            "report_id": "replication-current-target",
            "candidate_id": "cand-2",
            "evaluation_spec_id": "eval-spec-1",
            "evaluation_cycle_checkpoint_id": checkpoint["checkpoint_id"],
        },
        artifact_type="replication",
    )
    update_candidate(
        approved_project,
        "cand-2",
        {"evaluation_report_ids": [dominated_target["artifact_id"]]},
    )
    promote_provisional(
        approved_project, "cand-2", frontier["artifact_id"]
    )

    state = promote_current(
        approved_project,
        "cand-2",
        [robustness["artifact_id"]],
    )

    assert state["current_best"]["report_artifact_id"] == frontier["artifact_id"]
    assert state["current_best"]["evaluation_cycle_checkpoint_id"] == (
        checkpoint["checkpoint_id"]
    )
    assert state["provisional_best"] is None
    assert load_candidates(approved_project)["cand-2"][
        "evaluation_report_ids"
    ] == [
        dominated_target["artifact_id"],
        frontier["artifact_id"],
        robustness["artifact_id"],
    ]


def test_stale_report_remains_valid_evidence_but_cannot_mutate_best(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    evaluation = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="evaluation-report",
    )
    robustness = register_json_report(
        approved_project,
        tmp_path,
        {
            "report_id": "replication-stale-setup",
            "candidate_id": "cand-2",
            "evaluation_spec_id": "eval-spec-1",
            "evaluation_cycle_checkpoint_id": checkpoint["checkpoint_id"],
        },
        artifact_type="replication",
    )
    promote_provisional(
        approved_project, "cand-2", evaluation["artifact_id"]
    )
    promote_current(
        approved_project, "cand-2", [robustness["artifact_id"]]
    )
    create_candidate(approved_project, candidate("cand-3"))
    stale_payload = evaluation_payload(
        checkpoint["checkpoint_id"],
        report_id="eval-stale-cand-3",
        candidate_id="cand-3",
    )
    stale = register_json_report(
        approved_project,
        tmp_path,
        stale_payload,
        artifact_type="evaluation-report",
    )

    assert check_evaluation_report(approved_project, stale_payload) == []
    with pytest.raises(ValueError, match="stale"):
        promote_provisional(
            approved_project, "cand-3", stale["artifact_id"]
        )


def test_pareto_pool_never_mixes_cycle_checkpoint_ids(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    first_cycle = prepare_candidates(approved_project)
    second_cycle = bind_evaluation_cycle_checkpoint(
        approved_project, "cycle-2"
    )
    target = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(first_cycle["checkpoint_id"]),
        artifact_type="evaluation-report",
    )
    register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            second_cycle["checkpoint_id"],
            report_id="eval-other-cycle-dominator",
            candidate_id="cand-other-cycle",
            objective=0.99,
            robustness=0.99,
            evidence_strength=0.99,
            information_gain=0.99,
            cost=0.1,
            complexity=0.1,
            confound_penalty=0.0,
        ),
        artifact_type="evaluation-report",
    )

    state = promote_provisional(
        approved_project, "cand-2", target["artifact_id"]
    )

    assert state["provisional_best"]["report_artifact_id"] == (
        target["artifact_id"]
    )


def test_cli_cycle_bind_command_returns_checkpoint(
    approved_project, capsys
):
    approve_and_lock(approved_project)
    create_candidate(
        approved_project, candidate("baseline-1", baseline=True)
    )
    assert main(
        [
            "evaluation-cycle-bind",
            "--root",
            str(approved_project.root),
            "--cycle-id",
            "cycle-cli",
        ]
    ) == 0
    checkpoint = json.loads(capsys.readouterr().out)
    assert checkpoint["cycle_id"] == "cycle-cli"
    assert checkpoint["record_type"] == "evaluation-cycle-checkpoint"


def test_promotion_verifies_artifact_type_hash_and_report_identity(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    wrong_type = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(checkpoint["checkpoint_id"]),
        artifact_type="note",
    )
    with pytest.raises(ValueError, match="evaluation-report"):
        promote_provisional(
            approved_project, "cand-2", wrong_type["artifact_id"]
        )

    mismatch = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"], report_id="payload-id"
        ),
        artifact_type="evaluation-report",
        metadata_report_id="metadata-id",
    )
    with pytest.raises(ValueError, match="report_id"):
        promote_provisional(
            approved_project, "cand-2", mismatch["artifact_id"]
        )

    valid = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"], report_id="valid-id"
        ),
        artifact_type="evaluation-report",
    )
    Path(valid["path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        promote_provisional(
            approved_project, "cand-2", valid["artifact_id"]
        )


def test_promotion_rule_is_coordinator_enforced(
    approved_project, tmp_path: Path
):
    approve_and_lock(approved_project)
    checkpoint = prepare_candidates(approved_project)
    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"],
            report_id="eval-advisory",
            primary_gain=0.001,
            recommendation="promote",
        ),
        artifact_type="evaluation-report",
    )

    with pytest.raises(ValueError, match="minimum primary gain"):
        promote_provisional(
            approved_project, "cand-2", report["artifact_id"]
        )
    assert read_json(approved_project.state)["provisional_best"] is None


def test_cli_exposes_evaluation_and_promotion_commands(
    approved_project, tmp_path: Path, capsys
):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(LOCKED_SPEC), encoding="utf-8")
    request = request_gate(
        approved_project, "evaluation", evaluation_envelope(LOCKED_SPEC)
    )
    record_gate_receipt(
        approved_project,
        request["gate_id"],
        {"source": "test", "evidence": "approved-cli-spec"},
    )
    assert main(
        [
            "evaluation-lock",
            "--root",
            str(approved_project.root),
            "--spec",
            str(spec_path),
        ]
    ) == 0
    lock_output = json.loads(capsys.readouterr().out)
    assert lock_output["active_evaluation_spec"]["evaluation_spec_id"] == (
        "eval-spec-1"
    )

    checkpoint = prepare_candidates(approved_project)
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(evaluation_payload(checkpoint["checkpoint_id"])),
        encoding="utf-8",
    )
    assert main(
        [
            "evaluation-check",
            "--root",
            str(approved_project.root),
            "--report",
            str(report_path),
        ]
    ) == 0
    check_output = json.loads(capsys.readouterr().out)
    assert check_output == {"failures": [], "status": "passing"}

    report = register_json_report(
        approved_project,
        tmp_path,
        evaluation_payload(
            checkpoint["checkpoint_id"], report_id="eval-cli"
        ),
        artifact_type="evaluation-report",
    )
    assert main(
        [
            "promote-provisional",
            "--root",
            str(approved_project.root),
            "--candidate-id",
            "cand-2",
            "--report-id",
            report["artifact_id"],
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["provisional_best"][
        "candidate_id"
    ] == "cand-2"
