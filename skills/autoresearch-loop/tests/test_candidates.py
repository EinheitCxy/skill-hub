from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest
import yaml

from autoresearch import candidates
from autoresearch.candidates import (
    create_candidate,
    evaluate_stop_conditions,
    record_verdict,
    select_parallel_candidates,
    update_candidate,
)
from autoresearch.artifacts import register_artifact, resolve_artifact
from autoresearch.governance import record_gate_receipt, request_gate
from autoresearch.io import atomic_write_json, read_json, read_jsonl
from autoresearch.project import ProjectLayout, initialize_project, load_graph
from researchctl import main


CANDIDATE_FIXTURES = [
    {
        "candidate_id": "exploit-1",
        "search_role": "exploit",
        "hypothesis_id": "h1",
        "kind": "refine",
        "primary_change": "increase encoder depth",
        "data_intervention": "none",
        "objective": "accuracy",
        "priority": 0.90,
    },
    {
        "candidate_id": "explore-1",
        "search_role": "explore",
        "hypothesis_id": "h2",
        "kind": "objective-loss",
        "primary_change": "add supervised contrastive loss",
        "data_intervention": "balanced sampler",
        "objective": "macro_f1",
        "priority": 0.80,
        "mechanism": "contrastive supervision",
    },
    {
        "candidate_id": "falsify-1",
        "search_role": "falsify",
        "hypothesis_id": "h1",
        "kind": "ablate",
        "primary_change": "remove depth increase",
        "data_intervention": "none",
        "objective": "accuracy",
        "priority": 0.70,
    },
    {
        "candidate_id": "exploit-duplicate",
        "search_role": "exploit",
        "hypothesis_id": "h1",
        "kind": "refine",
        "primary_change": " increase   encoder depth ",
        "data_intervention": "none",
        "objective": "accuracy",
        "priority": 0.60,
    },
]


def proposal(candidate_id: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "candidate_id": candidate_id,
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
    value.update(overrides)
    return value


def child_proposal(
    candidate_id: str, parent_id: str = "baseline-1", **overrides: object
) -> dict[str, object]:
    value = proposal(
        candidate_id,
        kind="architecture-model",
        parent_id=parent_id,
        hypothesis_id="h1",
        search_role="explore",
        mechanism="deeper encoder improves representation capacity",
        primary_change="increase encoder depth",
        expected_observation="accuracy improves above locked noise",
        falsification_condition="gain is at or below locked noise",
        evaluation_spec_id=None,
        budget_slice={"wall_seconds": 30},
        scientific_depth=1,
    )
    value.update(overrides)
    return value


def create_baseline(layout: ProjectLayout) -> dict[str, object]:
    return create_candidate(layout, proposal("baseline-1"))


def candidate_events(layout: ProjectLayout, record_type: str) -> list[dict]:
    return [
        row
        for row in read_jsonl(layout.journal)
        if row.get("record_type") == record_type
    ]


def workflow_intent(layout: ProjectLayout, pattern: str) -> tuple[Path, dict]:
    paths = list(
        (layout.root / ".research" / "workflow-transactions").glob(pattern)
    )
    assert len(paths) == 1
    return paths[0], read_json(paths[0])


def registered_artifact(
    layout: ProjectLayout, tmp_path: Path, name: str, artifact_type: str = "evidence"
) -> str:
    source = tmp_path / name
    source.write_text(name, encoding="utf-8")
    return register_artifact(
        layout,
        source,
        {"type": artifact_type, "producer": "test", "storage": "snapshot"},
    )["artifact_id"]


def evaluation_spec(spec_id: str = "eval-spec-1") -> dict[str, object]:
    return {
        "evaluation_spec_id": spec_id,
        "dataset_id": "dataset-v1",
        "split_id": "split-v1",
        "primary_metric": {
            "name": "accuracy",
            "direction": "maximize",
            "noise": 0.01,
        },
        "hard_gates": [
            "execution validity",
            "contract comparability",
            "evidence completeness",
        ],
    }


def registered_evaluation_spec(
    layout: ProjectLayout,
    tmp_path: Path,
    spec: dict[str, object],
    *,
    artifact_type: str = "evaluation-spec",
) -> str:
    source = tmp_path / f"{spec['evaluation_spec_id']}.json"
    source.write_text(
        json.dumps(spec, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return register_artifact(
        layout,
        source,
        {"type": artifact_type, "producer": "test", "storage": "snapshot"},
    )["artifact_id"]


def evaluation_anchor(
    layout: ProjectLayout,
    artifact_id: str,
    gate: dict[str, object],
    receipt: dict[str, object],
    *,
    spec_id: str = "eval-spec-1",
) -> dict[str, object]:
    artifact = resolve_artifact(layout, artifact_id)
    return {
        "evaluation_spec_id": spec_id,
        "artifact_id": artifact_id,
        "artifact_sha256": artifact["sha256"],
        "gate_id": gate["gate_id"],
        "gate_payload_hash": receipt["payload_hash"],
    }


def activate_evaluation_spec(layout: ProjectLayout, tmp_path: Path) -> str:
    from autoresearch.workflow import set_active_evaluation_spec

    spec = evaluation_spec()
    artifact_id = registered_evaluation_spec(layout, tmp_path, spec)
    gate = request_gate(layout, "evaluation", spec)
    receipt = record_gate_receipt(
        layout, gate["gate_id"], {"source": "test", "evidence": "approved"}
    )
    set_active_evaluation_spec(
        layout,
        evaluation_anchor(layout, artifact_id, gate, receipt),
        read_json(layout.state)["revision"],
    )
    return str(spec["evaluation_spec_id"])


def test_create_requires_exact_candidate_schema(project_layout):
    with pytest.raises(ValueError, match="unknown candidate field"):
        create_candidate(project_layout, proposal("baseline-1", extra=True))
    with pytest.raises(ValueError, match="missing candidate field"):
        value = proposal("baseline-1")
        del value["objective"]
        create_candidate(project_layout, value)


def test_create_replays_append_only_events_and_sets_first_baseline(project_layout):
    baseline = create_baseline(project_layout)

    assert baseline == proposal("baseline-1")
    state = read_json(project_layout.state)
    assert state["initial_baseline"] == "baseline-1"
    assert set(state["current_best"]) == {
        "candidate_id",
        "report_artifact_id",
        "evaluation_cycle_checkpoint_id",
        "promoted_at",
    }
    assert state["current_best"]["candidate_id"] == "baseline-1"
    assert state["current_best"]["report_artifact_id"] is None
    assert state["current_best"]["evaluation_cycle_checkpoint_id"] is None
    assert isinstance(state["current_best"]["promoted_at"], str)
    assert state["current_best"]["promoted_at"]
    assert state["provisional_best"] is None
    assert [
        row for row in read_jsonl(project_layout.journal)
        if row["record_type"] == "candidate-create"
    ] == [
        {"schema_version": 1, "record_type": "candidate-create", "candidate": baseline}
    ]
    assert candidates.load_candidates(project_layout) == {"baseline-1": baseline}


def test_duplicate_candidate_and_second_baseline_are_rejected(project_layout):
    create_baseline(project_layout)
    with pytest.raises(ValueError, match="candidate id already exists"):
        create_candidate(project_layout, proposal("baseline-1", objective="macro_f1"))
    with pytest.raises(ValueError, match="second baseline"):
        create_candidate(project_layout, proposal("baseline-2"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"parent_id": None}, "parent_id"),
        ({"parent_id": "missing"}, "unknown parent"),
        ({"parent_id": "candidate-1"}, "parent"),
        ({"mechanism": ""}, "mechanism"),
        ({"evaluation_spec_id": None, "status": "official"}, "evaluation_spec_id"),
    ],
)
def test_nonbaseline_parent_and_official_requirements(project_layout, overrides, message):
    create_baseline(project_layout)
    with pytest.raises(ValueError, match=message):
        create_candidate(project_layout, child_proposal("candidate-1", **overrides))


def test_official_candidate_requires_registered_evaluation_spec_and_budget(
    approved_project, tmp_path: Path
):
    create_baseline(approved_project)
    evaluation_spec_id = activate_evaluation_spec(approved_project, tmp_path)
    candidate = create_candidate(
        approved_project,
        child_proposal(
            "candidate-1",
            evaluation_spec_id=evaluation_spec_id,
        ),
    )

    assert candidate["candidate_id"] == "candidate-1"
    budget = read_json(approved_project.budget)
    assert budget["reservations"] == {}
    with pytest.raises(ValueError, match="remaining budget"):
        create_candidate(
            approved_project,
            child_proposal(
                "candidate-2",
                evaluation_spec_id=evaluation_spec_id,
                budget_slice={"wall_seconds": 100_000},
            ),
        )


def test_active_evaluation_spec_rejects_wrong_artifact_type(
    approved_project, tmp_path: Path
):
    from autoresearch.workflow import set_active_evaluation_spec

    spec = evaluation_spec()
    artifact_id = registered_evaluation_spec(
        approved_project, tmp_path, spec, artifact_type="evidence"
    )
    gate = request_gate(approved_project, "evaluation", spec)
    receipt = record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "approved"},
    )

    with pytest.raises(ValueError, match="artifact"):
        set_active_evaluation_spec(
            approved_project,
            evaluation_anchor(approved_project, artifact_id, gate, receipt),
            read_json(approved_project.state)["revision"],
        )


def test_active_evaluation_spec_rejects_artifact_hash_failure(
    approved_project, tmp_path: Path
):
    from autoresearch.workflow import set_active_evaluation_spec

    spec = evaluation_spec()
    artifact_id = registered_evaluation_spec(approved_project, tmp_path, spec)
    gate = request_gate(approved_project, "evaluation", spec)
    receipt = record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "approved"},
    )
    anchor = evaluation_anchor(approved_project, artifact_id, gate, receipt)
    Path(resolve_artifact(approved_project, artifact_id)["path"]).write_text(
        "tampered", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="artifact"):
        set_active_evaluation_spec(
            approved_project,
            anchor,
            read_json(approved_project.state)["revision"],
        )


def test_active_evaluation_spec_rejects_unrelated_approved_payload(
    approved_project, tmp_path: Path
):
    from autoresearch.workflow import set_active_evaluation_spec

    spec = evaluation_spec()
    artifact_id = registered_evaluation_spec(approved_project, tmp_path, spec)
    unrelated = evaluation_spec("eval-spec-other")
    gate = request_gate(approved_project, "evaluation", unrelated)
    receipt = record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "approved"},
    )

    with pytest.raises(ValueError, match="payload"):
        set_active_evaluation_spec(
            approved_project,
            evaluation_anchor(approved_project, artifact_id, gate, receipt),
            read_json(approved_project.state)["revision"],
        )


def test_active_evaluation_spec_rejects_mismatched_spec_id(
    approved_project, tmp_path: Path
):
    from autoresearch.workflow import set_active_evaluation_spec

    spec = evaluation_spec()
    artifact_id = registered_evaluation_spec(approved_project, tmp_path, spec)
    gate = request_gate(approved_project, "evaluation", spec)
    receipt = record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "approved"},
    )

    with pytest.raises(ValueError, match="EvaluationSpec ID"):
        set_active_evaluation_spec(
            approved_project,
            evaluation_anchor(
                approved_project,
                artifact_id,
                gate,
                receipt,
                spec_id="eval-spec-other",
            ),
            read_json(approved_project.state)["revision"],
        )


def test_active_evaluation_spec_rejects_tampered_gate_payload_sidecar(
    approved_project, tmp_path: Path
):
    from autoresearch.workflow import set_active_evaluation_spec

    spec = evaluation_spec()
    artifact_id = registered_evaluation_spec(approved_project, tmp_path, spec)
    gate = request_gate(approved_project, "evaluation", spec)
    receipt = record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "approved"},
    )
    payload_path = (
        approved_project.root
        / ".research"
        / "gate-payloads"
        / f"{gate['gate_id']}.json"
    )
    payload_record = read_json(payload_path)
    payload_record["payload"]["dataset_id"] = "tampered"
    atomic_write_json(payload_path, payload_record)

    with pytest.raises(ValueError, match="gate payload"):
        set_active_evaluation_spec(
            approved_project,
            evaluation_anchor(approved_project, artifact_id, gate, receipt),
            read_json(approved_project.state)["revision"],
        )


def test_debug_depth_is_not_bounded_by_retry_attempt_quota(
    approved_project,
):
    active_budget = read_json(approved_project.budget)
    limits = active_budget["limits"]
    limits["retries"] = 0
    gate = request_gate(
        approved_project,
        "budget",
        {
            "schema_version": 1,
            "limits": limits,
            "storage_accounting": active_budget["storage_accounting"],
        },
    )
    record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "zero-rerun-attempts"},
    )
    create_baseline(approved_project)
    candidate = create_candidate(
        approved_project,
        child_proposal(
            "debug-1",
            kind="debug",
            status="draft",
            evaluation_spec_id=None,
            debug_depth=3,
        ),
    )

    assert candidate["debug_depth"] == 3
    with pytest.raises(ValueError, match="max_debug_depth"):
        create_candidate(
            approved_project,
            child_proposal(
                "debug-too-deep",
                kind="debug",
                status="draft",
                evaluation_spec_id=None,
                debug_depth=4,
            ),
        )


def test_debug_depth_uses_initialized_graph_limit(tmp_path: Path):
    profile = (
        Path(__file__).parents[1]
        / "assets"
        / "research-project"
        / "research.yaml"
    )
    graph = load_graph(profile)
    graph["limits"]["max_debug_depth"] = 1
    custom_profile = tmp_path / "custom-profile.yaml"
    custom_profile.write_text(
        yaml.safe_dump(graph, sort_keys=False),
        encoding="utf-8",
    )
    layout = initialize_project(tmp_path / "project", custom_profile)
    create_baseline(layout)

    with pytest.raises(ValueError, match="max_debug_depth"):
        create_candidate(
            layout,
            child_proposal(
                "debug-depth-two",
                kind="debug",
                status="draft",
                evaluation_spec_id=None,
                debug_depth=2,
            ),
        )


def test_debug_candidate_quota_is_derived_from_candidate_journal(
    approved_project,
):
    create_baseline(approved_project)
    for index in range(2):
        create_candidate(
            approved_project,
            child_proposal(
                f"debug-{index}",
                kind="debug",
                status="draft",
                evaluation_spec_id=None,
                debug_depth=1,
            ),
        )

    with pytest.raises(ValueError, match="debug candidate budget exhausted"):
        create_candidate(
            approved_project,
            child_proposal(
                "debug-exhausted",
                kind="debug",
                status="draft",
                evaluation_spec_id=None,
                debug_depth=1,
            ),
        )


def test_official_and_debug_candidate_quotas_have_separate_journal_owners(
    approved_project, tmp_path: Path
):
    create_baseline(approved_project)
    active_budget = read_json(approved_project.budget)
    limits = active_budget["limits"]
    limits["official_candidates"] = 1
    limits["debug_candidates"] = 2
    gate = request_gate(
        approved_project,
        "budget",
        {
            "schema_version": 1,
            "limits": limits,
            "storage_accounting": active_budget["storage_accounting"],
        },
    )
    record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "separate-candidate-quotas"},
    )
    spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(
        approved_project,
        child_proposal(
            "debug-official",
            kind="debug",
            evaluation_spec_id=spec_id,
            debug_depth=1,
        ),
    )

    scientific = create_candidate(
        approved_project,
        child_proposal(
            "scientific-official",
            evaluation_spec_id=spec_id,
        ),
    )

    assert scientific["status"] == "official"


def test_update_uses_exact_patch_and_preserves_scientific_identity(project_layout):
    create_baseline(project_layout)
    candidate = create_candidate(
        project_layout, child_proposal("candidate-1", status="draft")
    )
    updated = update_candidate(
        project_layout,
        "candidate-1",
        {"run_artifact_ids": []},
    )

    assert updated == candidate
    assert read_jsonl(project_layout.journal)[-1] == {
        "schema_version": 1,
        "record_type": "candidate-update",
        "candidate_id": "candidate-1",
        "patch": {"run_artifact_ids": []},
    }
    with pytest.raises(ValueError, match="immutable"):
        update_candidate(project_layout, "candidate-1", {"mechanism": "other"})
    with pytest.raises(ValueError, match="unknown candidate patch"):
        update_candidate(project_layout, "candidate-1", {"extra": 1})


def test_verdict_lifecycle_is_append_only_and_never_promotes(approved_project, tmp_path):
    create_baseline(approved_project)
    evaluation_spec_id = activate_evaluation_spec(approved_project, tmp_path)
    candidate = create_candidate(
        approved_project,
        child_proposal("candidate-1", evaluation_spec_id=evaluation_spec_id),
    )
    evidence_id = registered_artifact(approved_project, tmp_path, "evaluation.json")
    verdict = record_verdict(approved_project, "candidate-1", "keep", [evidence_id])

    assert verdict == {
        "schema_version": 1,
        "record_type": "candidate-verdict",
        "candidate_id": "candidate-1",
        "verdict": "keep",
        "evidence_ids": [evidence_id],
    }
    replayed = candidates.load_candidates(approved_project)["candidate-1"]
    assert replayed["status"] == "completed"
    state = read_json(approved_project.state)
    assert state["current_best"]["candidate_id"] == "baseline-1"
    assert state["provisional_best"] is None
    assert candidate["status"] == "official"


@pytest.mark.parametrize(
    ("verdict", "expected_status"),
    [
        ("discard", "discarded"),
        ("inconclusive", "inconclusive"),
        ("debuggable", "completed"),
        ("needs_user", "official"),
        ("failed", "discarded"),
    ],
)
def test_verdict_mapping_is_exact(approved_project, tmp_path, verdict, expected_status):
    create_baseline(approved_project)
    evaluation_spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(
        approved_project,
        child_proposal("candidate-1", evaluation_spec_id=evaluation_spec_id),
    )
    evidence_id = registered_artifact(approved_project, tmp_path, f"{verdict}.json")

    record_verdict(approved_project, "candidate-1", verdict, [evidence_id])

    assert candidates.load_candidates(approved_project)["candidate-1"]["status"] == expected_status


def test_debug_candidate_cannot_be_promoted_as_scientific_result(approved_project, tmp_path):
    create_baseline(approved_project)
    evaluation_spec_id = activate_evaluation_spec(approved_project, tmp_path)
    candidate = create_candidate(
        approved_project,
        child_proposal(
            "debug-1",
            kind="debug",
            status="official",
            evaluation_spec_id=evaluation_spec_id,
            mechanism="repair implementation without changing hypothesis",
            primary_change="repair tensor shape",
            debug_depth=1,
        ),
    )
    evidence_id = registered_artifact(approved_project, tmp_path, "eval-1.json")
    with pytest.raises(ValueError, match="debug candidate"):
        record_verdict(approved_project, candidate["candidate_id"], "keep", [evidence_id])


def test_verdict_requires_official_candidate_and_valid_evidence(approved_project, tmp_path):
    create_baseline(approved_project)
    create_candidate(approved_project, child_proposal("candidate-1", status="draft"))
    with pytest.raises(ValueError, match="official"):
        record_verdict(approved_project, "candidate-1", "discard", [])
    evaluation_spec_id = activate_evaluation_spec(approved_project, tmp_path)
    update_candidate(
        approved_project,
        "candidate-1",
        {"evaluation_spec_id": evaluation_spec_id, "status": "official"},
    )
    with pytest.raises(ValueError, match="unknown artifact"):
        record_verdict(approved_project, "candidate-1", "discard", ["unknown"])


def test_retry_after_pointer_write_failure_does_not_duplicate_baseline(
    project_layout, monkeypatch
):
    from autoresearch.workflow import recovery as workflow

    original = workflow.atomic_write_json

    def fail_state_write(path: Path, value: dict) -> None:
        if path == project_layout.state:
            raise OSError("injected state failure")
        original(path, value)

    monkeypatch.setattr(workflow, "atomic_write_json", fail_state_write)
    with pytest.raises(OSError, match="injected"):
        create_baseline(project_layout)
    assert len(candidate_events(project_layout, "candidate-create")) == 1
    _, candidate_intent = workflow_intent(project_layout, "candidate-*.json")
    assert candidate_intent["phase"] == "event_appended"
    monkeypatch.setattr(workflow, "atomic_write_json", original)

    restarted = ProjectLayout.from_root(project_layout.root)
    recovered = create_baseline(restarted)

    assert recovered["candidate_id"] == "baseline-1"
    assert len(candidate_events(restarted, "candidate-create")) == 1
    assert (
        read_json(restarted.state)["current_best"]["candidate_id"]
        == "baseline-1"
    )
    _, candidate_intent = workflow_intent(restarted, "candidate-*.json")
    _, state_intent = workflow_intent(restarted, "state-mutation-*.json")
    assert candidate_intent["phase"] == "committed"
    assert state_intent["phase"] == "committed"


def test_retry_after_prepared_candidate_intent_appends_create_once(
    project_layout, monkeypatch
):
    original = candidates.append_jsonl_locked

    def fail_before_append(path: Path, value: dict) -> None:
        if value.get("record_type") == "candidate-create":
            raise OSError("injected before candidate append")
        original(path, value)

    monkeypatch.setattr(candidates, "append_jsonl_locked", fail_before_append)
    with pytest.raises(OSError, match="before candidate append"):
        create_baseline(project_layout)
    assert candidate_events(project_layout, "candidate-create") == []
    _, intent = workflow_intent(project_layout, "candidate-*.json")
    assert intent["phase"] == "prepared"
    monkeypatch.setattr(candidates, "append_jsonl_locked", original)

    restarted = ProjectLayout.from_root(project_layout.root)
    recovered = create_baseline(restarted)

    assert recovered["candidate_id"] == "baseline-1"
    assert len(candidate_events(restarted, "candidate-create")) == 1
    _, intent = workflow_intent(restarted, "candidate-*.json")
    assert intent["phase"] == "committed"


def test_baseline_event_before_state_intent_is_not_candidate_committed(
    project_layout, monkeypatch
):
    original = candidates.initialize_baseline_state

    def fail_before_state_intent(*args, **kwargs):
        raise OSError("injected before baseline state intent")

    monkeypatch.setattr(
        candidates, "initialize_baseline_state", fail_before_state_intent
    )
    with pytest.raises(OSError, match="before baseline state intent"):
        create_baseline(project_layout)
    assert len(candidate_events(project_layout, "candidate-create")) == 1
    _, intent = workflow_intent(project_layout, "candidate-*.json")
    assert intent["phase"] == "event_appended"
    durable_promoted_at = intent["baseline_pointer"]["promoted_at"]
    assert list(
        (project_layout.root / ".research" / "workflow-transactions").glob(
            "state-mutation-*.json"
        )
    ) == []
    monkeypatch.setattr(candidates, "initialize_baseline_state", original)

    restarted = ProjectLayout.from_root(project_layout.root)
    create_baseline(restarted)

    assert len(candidate_events(restarted, "candidate-create")) == 1
    _, intent = workflow_intent(restarted, "candidate-*.json")
    assert intent["phase"] == "committed"
    current_best = read_json(restarted.state)["current_best"]
    assert current_best["candidate_id"] == "baseline-1"
    assert current_best["promoted_at"] == durable_promoted_at


def test_pending_baseline_state_receipt_blocks_unrelated_workflow(
    approved_project, monkeypatch
):
    from autoresearch.workflow import recovery as workflow
    from autoresearch.workflow import create_checkpoint, open_task, transition_stage

    original = workflow.atomic_write_json

    def fail_state_write(path: Path, value: dict) -> None:
        if path == approved_project.state:
            raise OSError("injected after baseline state receipt")
        original(path, value)

    monkeypatch.setattr(workflow, "atomic_write_json", fail_state_write)
    with pytest.raises(OSError, match="after baseline state receipt"):
        create_baseline(approved_project)
    monkeypatch.setattr(workflow, "atomic_write_json", original)
    restarted = ProjectLayout.from_root(approved_project.root)

    with pytest.raises(ValueError, match="conflicting pending"):
        create_checkpoint(restarted, "unrelated")
    transition = transition_stage(
        restarted, "intake", "research-charter", expected_revision=0
    )
    assert transition.status == "invalid"
    assert "conflicting pending" in transition.reason
    with pytest.raises(ValueError, match="conflicting pending"):
        open_task(
            restarted,
            {
                "cycle_id": "cycle-1",
                "task_id": "unrelated",
                "candidate_id": "candidate-x",
                "stage_id": "intake",
                "primary_skill": "test-driven-development",
                "capability_skills": [],
                "input_artifact_ids": [],
                "budget_slice": {"wall_seconds": 1},
                "editable_paths": [],
                "output_path": "cycles/cycle-1/tasks/unrelated/report.md",
                "stop_conditions": ["pending transaction"],
                "work_type": "simple",
            },
        )

    create_baseline(restarted)

    assert create_checkpoint(restarted, "recovered")["revision"] == 1
    assert transition_stage(
        restarted, "intake", "research-charter", expected_revision=1
    ).status == "applied"


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("checkpoint", "checkpoint"),
        ("next_state", "target state"),
        ("receipt", "receipt"),
        ("phase", "workflow transaction intent"),
    ],
)
def test_pending_state_mutation_rejects_single_field_corruption_before_effects(
    project_layout, monkeypatch, target: str, message: str
):
    from autoresearch.workflow import recovery as workflow

    original = workflow.atomic_write_json

    def fail_state_write(path: Path, value: dict) -> None:
        if path == project_layout.state:
            raise OSError("injected pending state mutation")
        original(path, value)

    monkeypatch.setattr(workflow, "atomic_write_json", fail_state_write)
    with pytest.raises(OSError, match="pending state mutation"):
        create_baseline(project_layout)
    monkeypatch.setattr(workflow, "atomic_write_json", original)
    intent_path, intent = workflow_intent(
        project_layout, "state-mutation-*.json"
    )
    if target == "checkpoint":
        intent["checkpoint"]["reason"] = "tampered"
    elif target == "next_state":
        intent["next_state"]["current_best"] = "tampered"
    elif target == "receipt":
        intent["receipt"]["operation"] = "tampered"
    else:
        intent["phase"] = "state_committed"
    atomic_write_json(intent_path, intent)
    before_journal = read_jsonl(project_layout.journal)

    with pytest.raises(ValueError, match=message):
        create_baseline(ProjectLayout.from_root(project_layout.root))

    assert read_json(project_layout.state)["revision"] == 0
    assert read_jsonl(project_layout.journal) == before_journal
    _, candidate_intent = workflow_intent(project_layout, "candidate-*.json")
    assert candidate_intent["phase"] == "event_appended"


@pytest.mark.parametrize(
    "corruption",
    [
        "journal_receipt_operation",
        "intent_receipt_operation",
        "intent_checkpoint_reason",
        "intent_operation_active_spec",
        "checkpoint_file_reason",
    ],
)
@pytest.mark.parametrize(
    "entrypoint",
    ["checkpoint", "task-open", "transition"],
)
def test_task6_entrypoints_revalidate_latest_committed_state_mutation(
    approved_project,
    tmp_path: Path,
    corruption: str,
    entrypoint: str,
):
    from autoresearch.workflow import create_checkpoint, open_task, transition_stage

    create_baseline(approved_project)
    if corruption == "intent_operation_active_spec":
        activate_evaluation_spec(approved_project, tmp_path)
    state = read_json(approved_project.state)
    journal = read_jsonl(approved_project.journal)
    receipt = next(
        row
        for row in reversed(journal)
        if row.get("record_type") == "state-mutation"
    )
    intent_path = (
        approved_project.root
        / ".research"
        / "workflow-transactions"
        / f"{receipt['transaction_id']}.json"
    )
    intent = read_json(intent_path)

    if corruption == "journal_receipt_operation":
        receipt["operation"] = "tampered"
        approved_project.journal.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                + "\n"
                for row in journal
            ),
            encoding="utf-8",
        )
    elif corruption == "intent_receipt_operation":
        intent["receipt"]["operation"] = "tampered"
        atomic_write_json(intent_path, intent)
    elif corruption == "intent_checkpoint_reason":
        intent["checkpoint"]["reason"] = "tampered"
        atomic_write_json(intent_path, intent)
    elif corruption == "intent_operation_active_spec":
        assert state["active_evaluation_spec"] is not None
        intent["operation"] = "tampered"
        atomic_write_json(intent_path, intent)
    else:
        checkpoint_path = Path(intent["checkpoint"]["checkpoint_path"])
        checkpoint = read_json(checkpoint_path)
        checkpoint["reason"] = "tampered"
        atomic_write_json(checkpoint_path, checkpoint)

    error = "latest committed state mutation evidence is invalid"
    if entrypoint == "checkpoint":
        with pytest.raises(ValueError, match=error):
            create_checkpoint(approved_project, "must fail closed")
    elif entrypoint == "task-open":
        with pytest.raises(ValueError, match=error):
            open_task(
                approved_project,
                {
                    "cycle_id": "cycle-1",
                    "task_id": "must-fail-closed",
                    "candidate_id": "candidate-x",
                    "stage_id": "intake",
                    "primary_skill": "test-driven-development",
                    "capability_skills": [],
                    "input_artifact_ids": [],
                    "budget_slice": {"wall_seconds": 1},
                    "editable_paths": [],
                    "output_path": (
                        "cycles/cycle-1/tasks/must-fail-closed/report.md"
                    ),
                    "stop_conditions": ["invalid state evidence"],
                    "work_type": "simple",
                },
            )
    else:
        result = transition_stage(
            approved_project,
            "intake",
            "research-charter",
            expected_revision=state["revision"],
        )
        assert result.status == "invalid"
        assert error in result.reason


def create_in_process(root: str, proposal_value: dict, ready, result_queue) -> None:
    ready.wait()
    try:
        candidate = create_candidate(ProjectLayout.from_root(Path(root)), proposal_value)
    except Exception as error:
        result_queue.put(("error", str(error)))
    else:
        result_queue.put(("ok", candidate["candidate_id"]))


def test_concurrent_duplicate_create_is_serialized(project_layout):
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    results = context.Queue()
    proposal_value = proposal("baseline-1")
    workers = [
        context.Process(
            target=create_in_process,
            args=(str(project_layout.root), proposal_value, ready, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    ready.set()
    outcomes = [results.get(timeout=10) for _ in workers]
    for worker in workers:
        worker.join(timeout=10)

    assert sorted(kind for kind, _ in outcomes) == ["ok", "ok"]
    assert len(
        [row for row in read_jsonl(project_layout.journal) if row["record_type"] == "candidate-create"]
    ) == 1


def test_three_slots_prefer_exploit_explore_and_falsify():
    selected = select_parallel_candidates(CANDIDATE_FIXTURES, slots=3)
    assert {item["search_role"] for item in selected} == {
        "exploit",
        "explore",
        "falsify",
    }


def test_selection_normalizes_duplicates_ties_and_honors_slots():
    tied = [
        dict(CANDIDATE_FIXTURES[0], candidate_id="b", priority=1.0),
        dict(CANDIDATE_FIXTURES[0], candidate_id="a", priority=1.0),
        *CANDIDATE_FIXTURES[1:],
    ]
    selected = select_parallel_candidates(tied, slots=2)

    assert [item["candidate_id"] for item in selected] == ["a", "explore-1"]
    assert select_parallel_candidates(CANDIDATE_FIXTURES, slots=0) == []
    assert select_parallel_candidates(CANDIDATE_FIXTURES, slots=1)[0]["search_role"] == "exploit"


def test_search_stops_when_budget_or_boundary_requires_user():
    reasons = evaluate_stop_conditions(
        {"pending_gate": "modification-change", "success_reached": False},
        {"remaining": {"official_candidates": 0, "wall_seconds": 10}},
        CANDIDATE_FIXTURES,
    )
    assert reasons == ["pending human gate", "official candidate budget exhausted"]


def test_all_stop_conditions_keep_fixed_order_and_return_all_reasons():
    reasons = evaluate_stop_conditions(
        {
            "success_reached": True,
            "pending_gate": "evaluation-change",
            "converged_without_material_gain": True,
            "user_stop_requested": True,
        },
        {"remaining": {"wall_seconds": 0, "official_candidates": 0}},
        [],
    )
    assert reasons == [
        "success criterion reached",
        "pending human gate",
        "time budget exhausted",
        "official candidate budget exhausted",
        "convergence without material gain",
        "no valid independent candidate",
        "user stop requested",
    ]


def test_stop_conditions_use_nullable_caps_and_journal_official_quota():
    budget = {
        "status": "approved",
        "limits": {
            "wall_seconds": 10,
            "official_candidates": 1,
        },
        "reserved": {"wall_seconds": 0},
        "consumed": {"wall_seconds": 0},
    }
    official = dict(
        CANDIDATE_FIXTURES[0],
        kind="architecture-model",
        status="completed",
    )

    reasons = evaluate_stop_conditions({}, budget, [official])

    assert "official candidate budget exhausted" in reasons


def test_cli_candidate_commands_emit_json_and_stop_selection(project_layout, tmp_path, capsys):
    proposal_path = tmp_path / "baseline.json"
    proposal_path.write_text(json.dumps(proposal("baseline-1")), encoding="utf-8")
    assert main(["candidate-create", "--root", str(project_layout.root), "--proposal", str(proposal_path)]) == 0
    assert json.loads(capsys.readouterr().out)["candidate_id"] == "baseline-1"

    assert main(["candidate-select", "--root", str(project_layout.root), "--slots", "3"]) == 0
    selection = json.loads(capsys.readouterr().out)
    assert selection["candidates"] == []
    assert selection["stop_reasons"] == ["no valid independent candidate"]

    assert main(["search-status", "--root", str(project_layout.root)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["stop_reasons"] == ["no valid independent candidate"]


def test_cli_errors_are_compact_json(project_layout, capsys):
    assert main(["candidate-update", "--root", str(project_layout.root), "--candidate-id", "missing", "--patch", "none.json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"


def test_baseline_state_mutation_keeps_task6_checkpoint_chain(project_layout):
    from autoresearch.workflow import create_checkpoint, transition_stage

    create_baseline(project_layout)

    checkpoint = create_checkpoint(project_layout, "after baseline")

    assert checkpoint["revision"] == 1
    transition = transition_stage(
        project_layout, "intake", "research-charter", expected_revision=1
    )
    assert transition.status == "applied"


def test_baseline_state_anchor_supports_two_committed_transitions(
    approved_project,
):
    from autoresearch.workflow import transition_stage

    create_baseline(approved_project)
    first = transition_stage(
        approved_project,
        "intake",
        "research-charter",
        expected_revision=1,
    )
    assert first.status == "applied"
    gate = request_gate(
        approved_project,
        "evaluation",
        {"stage_id": "research-charter"},
    )
    record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "test", "evidence": "approved-stage-boundary"},
    )

    second = transition_stage(
        approved_project,
        "research-charter",
        "literature-prior-art",
        expected_revision=2,
    )

    assert second.status == "applied"
    assert read_json(approved_project.state)["revision"] == 3


def test_committed_baseline_retry_survives_later_state_mutation(
    approved_project, tmp_path: Path
):
    create_baseline(approved_project)
    activate_evaluation_spec(approved_project, tmp_path)

    recovered = create_baseline(
        ProjectLayout.from_root(approved_project.root)
    )

    assert recovered["candidate_id"] == "baseline-1"
    assert len(candidate_events(approved_project, "candidate-create")) == 1
    assert read_json(approved_project.state)["active_evaluation_spec"] is not None


def test_official_candidate_validates_envelope_without_reserving_budget(
    approved_project, tmp_path: Path
):
    create_baseline(approved_project)
    evaluation_spec_id = registered_artifact(approved_project, tmp_path, "generic")

    with pytest.raises(ValueError, match="active EvaluationSpec"):
        create_candidate(
            approved_project,
            child_proposal("candidate-1", evaluation_spec_id=evaluation_spec_id),
        )
    assert read_json(approved_project.budget)["reservations"] == {}


def test_candidate_artifact_lists_only_append(project_layout, tmp_path: Path):
    create_baseline(project_layout)
    artifact_id = registered_artifact(project_layout, tmp_path, "code.py")
    create_candidate(
        project_layout,
        child_proposal(
            "candidate-1", status="draft", code_artifact_ids=[artifact_id]
        ),
    )

    with pytest.raises(ValueError, match="append-only"):
        update_candidate(project_layout, "candidate-1", {"code_artifact_ids": []})


def test_debuggable_blocks_normal_child_until_next_depth_debug_child(
    approved_project, tmp_path: Path
):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(
        approved_project, child_proposal("candidate-1", evaluation_spec_id=spec_id)
    )
    evidence_id = registered_artifact(approved_project, tmp_path, "eval")
    record_verdict(approved_project, "candidate-1", "debuggable", [evidence_id])

    status = candidates.search_status(approved_project)
    assert status["pending_debug_parent_ids"] == ["candidate-1"]
    assert status["blocked_candidate_ids"] == ["candidate-1"]
    assert status["pending_debug_obligations"] == [
        {
            "parent_id": "candidate-1",
            "required_kind": "debug",
            "required_debug_depth": 1,
        }
    ]
    with pytest.raises(ValueError, match="pending debug"):
        create_candidate(
            approved_project,
            child_proposal(
                "ordinary-2", parent_id="candidate-1", status="draft"
            ),
        )
    debug = create_candidate(
        approved_project,
        child_proposal(
            "debug-2",
            parent_id="candidate-1",
            kind="debug",
            status="draft",
            debug_depth=1,
            evaluation_spec_id=None,
        ),
    )
    assert debug["kind"] == "debug"
    assert candidates.search_status(approved_project)["pending_debug_parent_ids"] == []


def test_pending_debug_blocks_descendants_but_not_independent_branch(
    approved_project, tmp_path: Path
):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(
        approved_project,
        child_proposal("branch-parent", evaluation_spec_id=spec_id),
    )
    create_candidate(
        approved_project,
        child_proposal(
            "branch-descendant",
            parent_id="branch-parent",
            status="draft",
        ),
    )
    create_candidate(
        approved_project,
        child_proposal(
            "independent",
            parent_id="baseline-1",
            status="draft",
            hypothesis_id="h-independent",
            mechanism="independent mechanism",
            primary_change="independent change",
        ),
    )
    evidence_id = registered_artifact(
        approved_project, tmp_path, "branch-debug-evidence"
    )
    record_verdict(
        approved_project, "branch-parent", "debuggable", [evidence_id]
    )

    status = candidates.search_status(approved_project)

    assert status["pending_debug_parent_ids"] == ["branch-parent"]
    assert status["blocked_candidate_ids"] == [
        "branch-descendant",
        "branch-parent",
    ]
    with pytest.raises(ValueError, match="pending debug"):
        create_candidate(
            approved_project,
            child_proposal(
                "blocked-grandchild",
                parent_id="branch-descendant",
                status="draft",
            ),
        )
    created = create_candidate(
        approved_project,
        child_proposal(
            "independent-child",
            parent_id="independent",
            status="draft",
            mechanism="another independent mechanism",
            primary_change="another independent change",
        ),
    )
    assert created["parent_id"] == "independent"


def test_pending_debug_requires_direct_parent_kind_and_next_depth(
    approved_project, tmp_path: Path
):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(
        approved_project,
        child_proposal("branch-parent", evaluation_spec_id=spec_id),
    )
    evidence_id = registered_artifact(
        approved_project, tmp_path, "debug-requirement-evidence"
    )
    record_verdict(
        approved_project, "branch-parent", "debuggable", [evidence_id]
    )

    wrong_parent = create_candidate(
        approved_project,
        child_proposal(
            "wrong-parent-debug",
            parent_id="baseline-1",
            kind="debug",
            status="draft",
            debug_depth=1,
        ),
    )
    assert wrong_parent["kind"] == "debug"
    assert candidates.search_status(approved_project)[
        "pending_debug_parent_ids"
    ] == ["branch-parent"]
    with pytest.raises(ValueError, match="pending debug"):
        create_candidate(
            approved_project,
            child_proposal(
                "wrong-kind",
                parent_id="branch-parent",
                kind="refine",
                status="draft",
            ),
        )
    with pytest.raises(ValueError, match="pending debug"):
        create_candidate(
            approved_project,
            child_proposal(
                "wrong-depth",
                parent_id="branch-parent",
                kind="debug",
                status="draft",
                debug_depth=2,
            ),
        )

    create_candidate(
        approved_project,
        child_proposal(
            "satisfying-debug",
            parent_id="branch-parent",
            kind="debug",
            status="draft",
            debug_depth=1,
        ),
    )

    assert candidates.search_status(approved_project)[
        "pending_debug_parent_ids"
    ] == []


def test_cli_selects_independent_branch_while_debug_branch_is_pending(
    approved_project, tmp_path: Path, capsys
):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(
        approved_project,
        child_proposal("blocked-parent", evaluation_spec_id=spec_id),
    )
    create_candidate(
        approved_project,
        child_proposal(
            "blocked-descendant",
            parent_id="blocked-parent",
            status="draft",
            search_role="exploit",
        ),
    )
    create_candidate(
        approved_project,
        child_proposal(
            "independent",
            status="draft",
            search_role="exploit",
            hypothesis_id="independent",
            mechanism="independent",
            primary_change="independent",
        ),
    )
    evidence_id = registered_artifact(
        approved_project, tmp_path, "cli-debug-evidence"
    )
    record_verdict(
        approved_project, "blocked-parent", "debuggable", [evidence_id]
    )

    assert main(
        [
            "candidate-select",
            "--root",
            str(approved_project.root),
            "--slots",
            "3",
        ]
    ) == 0
    selection = json.loads(capsys.readouterr().out)
    assert [row["candidate_id"] for row in selection["candidates"]] == [
        "independent"
    ]
    assert selection["stop_reasons"] == []
    assert selection["blocked_candidate_ids"] == [
        "blocked-descendant",
        "blocked-parent",
    ]

    assert main(
        ["search-status", "--root", str(approved_project.root)]
    ) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "ready"
    assert status["pending_debug_obligations"] == [
        {
            "parent_id": "blocked-parent",
            "required_kind": "debug",
            "required_debug_depth": 1,
        }
    ]


def test_third_slot_ranks_falsify_and_ablate_union():
    fixtures = [
        *CANDIDATE_FIXTURES[:2],
        dict(CANDIDATE_FIXTURES[2], candidate_id="low-falsify", priority=0.1),
        dict(
            CANDIDATE_FIXTURES[2],
            candidate_id="high-ablate",
            search_role="explore",
            kind="ablate",
            primary_change="remove contrastive loss",
            priority=0.9,
        ),
    ]
    selected = select_parallel_candidates(fixtures, slots=3)

    assert selected[-1]["candidate_id"] == "high-ablate"


def test_cli_accepts_one_evidence_flag_with_multiple_ids(project_layout, capsys):
    assert main(
        [
            "candidate-verdict",
            "--root",
            str(project_layout.root),
            "--candidate-id",
            "missing",
            "--verdict",
            "discard",
            "--evidence",
            "first",
            "second",
        ]
    ) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


@pytest.mark.parametrize(
    ("budget_slice", "message"),
    [
        ({"wall_seconds": 1.0, "gpu_type": "A100"}, "gpu_type"),
        (
            {"wall_seconds": 1.0, "loop-subagent-bound": 1},
            "budget_slice has unknown field",
        ),
    ],
)
def test_candidate_allocation_obeys_approved_policy_budget_fields(
    approved_project,
    tmp_path: Path,
    budget_slice: dict[str, object],
    message: str,
):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)

    with pytest.raises(ValueError, match=message):
        create_candidate(
            approved_project,
            child_proposal(
                "candidate-policy",
                evaluation_spec_id=spec_id,
                budget_slice=budget_slice,
            ),
        )

    assert read_json(approved_project.budget)["reservations"] == {}


def test_candidate_allocation_accepts_wall_time_allocation(
    approved_project, tmp_path: Path
):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)

    candidate = create_candidate(
        approved_project,
        child_proposal(
            "candidate-unlimited",
            evaluation_spec_id=spec_id,
            budget_slice={"wall_seconds": 1},
        ),
    )

    assert candidate["candidate_id"] == "candidate-unlimited"


@pytest.mark.parametrize(
    "field", ["official_candidates", "retries", "debug_candidates"]
)
def test_candidate_budget_slice_rejects_quota_fields(
    project_layout, field
):
    create_baseline(project_layout)

    with pytest.raises(ValueError, match=field):
        create_candidate(
            project_layout,
            child_proposal(
                "candidate-quota-slice",
                status="draft",
                budget_slice={field: 1},
            ),
        )


def test_update_and_verdict_retries_reuse_single_event(approved_project, tmp_path: Path, monkeypatch):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(approved_project, child_proposal("candidate-1", status="draft"))
    original = candidates.append_jsonl_locked

    def append_then_fail(path, value):
        original(path, value)
        if value.get("record_type") == "candidate-update":
            raise OSError("after append")

    monkeypatch.setattr(candidates, "append_jsonl_locked", append_then_fail)
    patch = {"evaluation_spec_id": spec_id, "status": "official"}
    with pytest.raises(OSError, match="after append"):
        update_candidate(approved_project, "candidate-1", patch)
    monkeypatch.setattr(candidates, "append_jsonl_locked", original)
    assert update_candidate(ProjectLayout.from_root(approved_project.root), "candidate-1", patch)["status"] == "official"
    evidence_id = registered_artifact(approved_project, tmp_path, "eval")
    record_verdict(approved_project, "candidate-1", "needs_user", [evidence_id])
    assert record_verdict(ProjectLayout.from_root(approved_project.root), "candidate-1", "needs_user", [evidence_id])["verdict"] == "needs_user"
    assert len([row for row in read_jsonl(approved_project.journal) if row.get("record_type") == "candidate-verdict"]) == 1


def test_draft_to_official_retry_after_append_uses_one_event(
    approved_project, tmp_path: Path, monkeypatch
):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(
        approved_project, child_proposal("candidate-1", status="draft")
    )
    original = candidates.append_jsonl_locked

    def append_then_fail(path: Path, value: dict) -> None:
        original(path, value)
        if value.get("record_type") == "candidate-update":
            raise OSError("injected after candidate update append")

    patch = {"evaluation_spec_id": spec_id, "status": "official"}
    monkeypatch.setattr(candidates, "append_jsonl_locked", append_then_fail)
    with pytest.raises(OSError, match="after candidate update append"):
        update_candidate(approved_project, "candidate-1", patch)
    monkeypatch.setattr(candidates, "append_jsonl_locked", original)

    restarted = ProjectLayout.from_root(approved_project.root)
    assert update_candidate(restarted, "candidate-1", patch)["status"] == "official"
    assert len(candidate_events(restarted, "candidate-update")) == 1
    matching = [
        read_json(path)
        for path in (
            restarted.root / ".research" / "workflow-transactions"
        ).glob("candidate-*.json")
        if read_json(path)["event"]["record_type"] == "candidate-update"
    ]
    assert len(matching) == 1
    assert matching[0]["phase"] == "committed"


def test_candidate_operation_identity_is_canonical_across_patch_key_order(
    approved_project, tmp_path: Path, monkeypatch
):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(
        approved_project, child_proposal("candidate-1", status="draft")
    )
    original = candidates.append_jsonl_locked

    def append_then_fail(path: Path, value: dict) -> None:
        original(path, value)
        if value.get("record_type") == "candidate-update":
            raise OSError("injected after canonical update append")

    monkeypatch.setattr(candidates, "append_jsonl_locked", append_then_fail)
    with pytest.raises(OSError, match="canonical update append"):
        update_candidate(
            approved_project,
            "candidate-1",
            {"evaluation_spec_id": spec_id, "status": "official"},
        )
    monkeypatch.setattr(candidates, "append_jsonl_locked", original)

    recovered = update_candidate(
        ProjectLayout.from_root(approved_project.root),
        "candidate-1",
        {"status": "official", "evaluation_spec_id": spec_id},
    )

    assert recovered["status"] == "official"
    assert len(candidate_events(approved_project, "candidate-update")) == 1


@pytest.mark.parametrize("verdict", ["discard", "needs_user"])
def test_verdict_retry_after_append_uses_one_event(
    approved_project, tmp_path: Path, monkeypatch, verdict: str
):
    create_baseline(approved_project)
    spec_id = activate_evaluation_spec(approved_project, tmp_path)
    create_candidate(
        approved_project,
        child_proposal("candidate-1", evaluation_spec_id=spec_id),
    )
    evidence_id = registered_artifact(
        approved_project, tmp_path, f"{verdict}-evidence"
    )
    original = candidates.append_jsonl_locked

    def append_then_fail(path: Path, value: dict) -> None:
        original(path, value)
        if value.get("record_type") == "candidate-verdict":
            raise OSError("injected after candidate verdict append")

    monkeypatch.setattr(candidates, "append_jsonl_locked", append_then_fail)
    with pytest.raises(OSError, match="after candidate verdict append"):
        record_verdict(
            approved_project, "candidate-1", verdict, [evidence_id]
        )
    monkeypatch.setattr(candidates, "append_jsonl_locked", original)

    restarted = ProjectLayout.from_root(approved_project.root)
    recovered = record_verdict(
        restarted, "candidate-1", verdict, [evidence_id]
    )

    assert recovered["verdict"] == verdict
    assert len(candidate_events(restarted, "candidate-verdict")) == 1
    expected_status = "discarded" if verdict == "discard" else "official"
    assert candidates.load_candidates(restarted)["candidate-1"]["status"] == expected_status
