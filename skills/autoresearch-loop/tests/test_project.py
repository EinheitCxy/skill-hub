import copy
import json
from pathlib import Path

import pytest
import yaml

from autoresearch import project
from autoresearch.io import atomic_write_json, read_json, sha256_file
from autoresearch.project import (
    ProjectLayout,
    initialize_project,
    load_graph,
    load_research_state,
    validate_graph,
)
from researchctl import main


ASSET = (
    Path(__file__).parents[1]
    / "assets"
    / "research-project"
    / "research.yaml"
)
REPORT_ASSET = ASSET.with_name("report.md")

GRAPH_FIELDS = {"schema_version", "profile", "start_stage", "limits", "stages"}
LIMIT_FIELDS = {
    "loop-subagent-bound",
    "max_debug_depth",
    "max_skill_depth",
    "max_capability_skills",
}
STAGE_FIELDS = {
    "id",
    "purpose",
    "task_owner",
    "primary_skill",
    "capability_skills",
    "inputs",
    "outputs",
    "parallelizable",
    "gate_after",
    "gate_type",
    "gate_authority",
    "next",
}
STATE_FIELDS = {
    "schema_version",
    "revision",
    "research_id",
    "current_stage",
    "previous_state_hash",
    "last_transition_id",
    "pending_gate",
    "initial_baseline",
    "provisional_best",
    "current_best",
    "active_evaluation_spec",
}
BEST_POINTER_FIELDS = {
    "candidate_id",
    "report_artifact_id",
    "evaluation_cycle_checkpoint_id",
    "promoted_at",
}
EVALUATION_ANCHOR_FIELDS = {
    "evaluation_spec_id",
    "artifact_id",
    "artifact_sha256",
    "gate_id",
    "gate_payload_hash",
}
SHA256 = "a" * 64
EXPECTED_STAGE_CONTRACTS = {
    "intake": (
        "autoresearch-loop",
        [],
        [],
        ["research-request"],
        False,
        False,
        ["paper-analysis", "research-charter"],
    ),
    "paper-analysis": (
        "research-paper-analysis",
        [],
        ["research-request"],
        ["paper-analysis-report"],
        True,
        False,
        ["research-charter"],
    ),
    "research-charter": (
        "autoresearch-loop",
        [],
        ["research-request"],
        ["research-charter"],
        False,
        True,
        ["literature-prior-art"],
    ),
    "literature-prior-art": (
        "research-literature-review",
        ["literature-review"],
        ["research-charter"],
        ["evidence-cards", "literature-report"],
        True,
        False,
        ["dataset-leakage-audit"],
    ),
    "dataset-leakage-audit": (
        "research-experiment-design",
        [],
        ["research-charter", "evidence-cards"],
        ["dataset-audit"],
        False,
        False,
        ["baseline-registration"],
    ),
    "baseline-registration": (
        "autoresearch-loop",
        [],
        ["dataset-audit", "evidence-cards"],
        ["baseline-candidate"],
        False,
        False,
        ["baseline-evaluation-contract"],
    ),
    "baseline-evaluation-contract": (
        "research-experiment-design",
        [],
        ["baseline-candidate", "dataset-audit"],
        ["evaluation-spec", "baseline-run-plan"],
        False,
        True,
        ["environment-readiness"],
    ),
    "environment-readiness": (
        "autoresearch-loop",
        [],
        ["evaluation-spec", "baseline-run-plan"],
        ["environment-receipt"],
        False,
        False,
        ["baseline-reproduction"],
    ),
    "baseline-reproduction": (
        "research-experiment-execution",
        [],
        [
            "baseline-candidate",
            "evaluation-spec",
            "baseline-run-plan",
            "environment-receipt",
        ],
        ["baseline-run", "initial-baseline"],
        False,
        False,
        ["hypothesis-pool"],
    ),
    "hypothesis-pool": (
        "research-hypothesis-generation",
        [],
        ["research-charter", "evidence-cards", "initial-baseline"],
        ["candidate-proposals"],
        True,
        False,
        ["evaluation-contract-lock"],
    ),
    "evaluation-contract-lock": (
        "research-experiment-design",
        [],
        ["dataset-audit", "initial-baseline", "candidate-proposals"],
        ["evaluation-spec", "candidate-run-plans"],
        False,
        True,
        ["candidate-implementation"],
    ),
    "candidate-implementation": (
        "research-experiment-execution",
        [],
        [
            "candidate-proposals",
            "evaluation-spec",
            "candidate-run-plans",
            "environment-receipt",
        ],
        ["candidate-runs"],
        True,
        False,
        ["candidate-evaluation"],
    ),
    "candidate-evaluation": (
        "research-experiment-evaluation",
        [],
        ["candidate-runs", "evaluation-spec"],
        ["evaluation-reports"],
        True,
        False,
        ["evidence-critique"],
    ),
    "evidence-critique": (
        "research-evidence-critique",
        [],
        ["evaluation-reports", "candidate-proposals"],
        ["critic-reports"],
        True,
        False,
        ["search-selection"],
    ),
    "search-selection": (
        "autoresearch-loop",
        [],
        ["evaluation-reports", "critic-reports"],
        ["search-decision"],
        False,
        False,
        [
            "candidate-implementation",
            "error-slice-analysis",
            "robustness-validation",
        ],
    ),
    "error-slice-analysis": (
        "research-experiment-evaluation",
        [],
        ["evaluation-reports", "evaluation-spec"],
        ["slice-report"],
        True,
        False,
        ["search-selection", "robustness-validation"],
    ),
    "robustness-validation": (
        "research-experiment-evaluation",
        [],
        ["evaluation-reports", "critic-reports"],
        ["robustness-report", "current-best"],
        True,
        False,
        ["candidate-implementation", "evidence-synthesis"],
    ),
    "evidence-synthesis": (
        "research-result-synthesis",
        [],
        [
            "current-best",
            "robustness-report",
            "evaluation-reports",
            "critic-reports",
        ],
        ["claim-evidence-report"],
        False,
        False,
        ["citation-verification"],
    ),
    "citation-verification": (
        "research-result-synthesis",
        [],
        ["claim-evidence-report", "evidence-cards"],
        ["citation-report"],
        False,
        False,
        ["final-report"],
    ),
    "final-report": (
        "research-result-synthesis",
        [],
        ["claim-evidence-report", "citation-report"],
        ["final-report"],
        False,
        True,
        ["archive-close"],
    ),
    "archive-close": (
        "autoresearch-loop",
        [],
        ["final-report"],
        ["archive-receipt"],
        False,
        False,
        [],
    ),
}


def stage(
    stage_id: str,
    *,
    primary_skill: str | None = None,
    capability_skills: list[str] | None = None,
    next_stages: list[str] | None = None,
) -> dict:
    return {
        "id": stage_id,
        "purpose": f"Run {stage_id}.",
        "task_owner": "subagent",
        "primary_skill": primary_skill or f"{stage_id}-primary",
        "capability_skills": capability_skills or [],
        "inputs": [],
        "outputs": [f"{stage_id}-output"],
        "parallelizable": False,
        "gate_after": False,
        "gate_type": None,
        "gate_authority": None,
        "next": next_stages or [],
    }


def valid_graph() -> dict:
    return {
        "schema_version": 1,
        "profile": "test",
        "start_stage": "one",
        "limits": {
            "loop-subagent-bound": 3,
            "max_debug_depth": 3,
            "max_skill_depth": 2,
            "max_capability_skills": 2,
        },
        "stages": [stage("one")],
    }


def test_ml_profile_is_declarative_and_matches_stage_contracts():
    graph = load_graph(ASSET)
    assert graph["profile"] == "ml-applied"
    assert graph["start_stage"] == "intake"
    assert set(graph) == GRAPH_FIELDS
    assert set(graph["limits"]) == LIMIT_FIELDS
    assert graph["limits"]["loop-subagent-bound"] == 8
    assert graph["limits"]["max_debug_depth"] == 3
    assert [item["id"] for item in graph["stages"]] == list(
        EXPECTED_STAGE_CONTRACTS
    )
    assert all(
        set(item) == STAGE_FIELDS
        or set(item) == STAGE_FIELDS | {"control_plane"}
        for item in graph["stages"]
    )
    assert {
        item["id"]
        for item in graph["stages"]
        if item.get("control_plane") is True
    } == {"intake", "research-charter"}
    assert all(item["purpose"] for item in graph["stages"])
    for item in graph["stages"]:
        actual = (
            item["primary_skill"],
            item["capability_skills"],
            item["inputs"],
            item["outputs"],
            item["parallelizable"],
            item["gate_after"],
            item["next"],
        )
        assert actual == EXPECTED_STAGE_CONTRACTS[item["id"]]
    assert {
        item["id"]: item["gate_type"]
        for item in graph["stages"]
        if item["gate_after"]
    } == {
        "research-charter": "charter",
        "baseline-evaluation-contract": "evaluation",
        "evaluation-contract-lock": "evaluation",
        "final-report": "publication",
    }
    assert {
        item["id"]: item["gate_authority"]
        for item in graph["stages"]
        if item["gate_after"]
    } == {
        "research-charter": "coordinator",
        "baseline-evaluation-contract": "user",
        "evaluation-contract-lock": "user",
        "final-report": "user",
    }
    assert {
        item["id"]: item["task_owner"]
        for item in graph["stages"]
        if item["primary_skill"] == "autoresearch-loop"
    } == {
        "intake": "coordinator",
        "research-charter": "coordinator",
        "baseline-registration": "coordinator",
        "environment-readiness": "coordinator",
        "search-selection": "coordinator",
        "archive-close": "coordinator",
    }


def test_default_loop_subagent_bounds_are_visible_and_distinct(tmp_path: Path):
    graph = load_graph(ASSET)
    layout = initialize_project(tmp_path, ASSET)
    budget = read_json(layout.budget)

    assert graph["limits"]["loop-subagent-bound"] == 8
    assert budget["limits"]["loop-subagent-bound"] == 4


def test_research_charter_default_is_coordinator_auto_continuation():
    charter = next(
        item for item in load_graph(ASSET)["stages"] if item["id"] == "research-charter"
    )

    assert {
        key: charter.get(key)
        for key in (
            "task_owner",
            "control_plane",
            "gate_after",
            "gate_type",
            "gate_authority",
        )
    } == {
        "task_owner": "coordinator",
        "control_plane": True,
        "gate_after": True,
        "gate_type": "charter",
        "gate_authority": "coordinator",
    }


def test_publication_gate_cannot_use_coordinator_authority():
    graph = valid_graph()
    graph["stages"][0].update(
        {
            "gate_after": True,
            "gate_type": "publication",
            "gate_authority": "coordinator",
        }
    )

    assert validate_graph(graph) == [
        "stage one publication gate_authority must be user"
    ]


def test_initialize_creates_required_research_files(tmp_path: Path):
    layout = initialize_project(tmp_path, ASSET)

    assert layout == ProjectLayout.from_root(tmp_path)
    assert layout.graph == tmp_path / ".research" / "research.yaml"
    assert layout.state == tmp_path / ".research" / "state.json"
    assert layout.budget == tmp_path / ".research" / "budget.json"
    assert layout.budget_settlements == (
        tmp_path / ".research" / "budget-settlements.jsonl"
    )
    assert layout.budget_transactions == (
        tmp_path / ".research" / "budget-transactions"
    )
    assert layout.gates == tmp_path / ".research" / "gates.jsonl"
    assert layout.journal == tmp_path / ".research" / "journal.jsonl"
    assert layout.worker_events == tmp_path / ".research" / "worker-events.jsonl"
    assert layout.model_calls == tmp_path / ".research" / "autoresearch-record.jsonl"
    assert layout.artifact_index == tmp_path / ".research" / "artifacts" / "index.jsonl"
    assert layout.cycles == tmp_path / ".research" / "cycles"
    assert layout.runs == tmp_path / ".research" / "runs"
    assert layout.checkpoints == tmp_path / ".research" / "checkpoints"
    assert validate_graph(load_graph(layout.graph)) == []

    state = load_research_state(layout)
    assert set(state) == STATE_FIELDS
    assert state["schema_version"] == 1
    assert state["revision"] == 0
    assert state["current_stage"] == "intake"
    assert state["previous_state_hash"] is None
    assert state["last_transition_id"] is None
    budget = read_json(layout.budget)
    assert budget["status"] == "unapproved"
    assert budget["limits"] == {
        "wall_seconds": 86400,
        "cpu_seconds": 86400,
        "gpu_seconds": 86400,
        "storage_bytes": 2147483648,
        "official_candidates": 3,
        "retries": 3,
        "debug_candidates": 3,
        "loop-subagent-bound": 4,
        "gpu_type": None,
    }
    assert budget["reserved"] == {
        "wall_seconds": 0,
        "cpu_seconds": 0,
        "gpu_seconds": 0,
        "storage_bytes": 0,
    }
    assert budget["consumed"] == budget["reserved"]
    assert budget["reservations"] == {}
    for ledger in (
        layout.gates,
        layout.journal,
        layout.worker_events,
        layout.model_calls,
        layout.artifact_index,
        layout.budget_settlements,
    ):
        assert ledger.read_text(encoding="utf-8") == ""
    assert (tmp_path / ".research" / "report.md").read_text(
        encoding="utf-8"
    ) == REPORT_ASSET.read_text(encoding="utf-8")
    assert layout.cycles.is_dir()
    assert layout.runs.is_dir()
    assert layout.artifact_index.parent.is_dir()
    assert layout.checkpoints.is_dir()
    assert layout.budget_transactions.is_dir()

    checkpoints = list(layout.checkpoints.iterdir())
    assert len(checkpoints) == 1
    checkpoint = json.loads(checkpoints[0].read_text(encoding="utf-8"))
    assert checkpoint["state_hash"] == sha256_file(layout.state)
    assert checkpoint["graph_hash"] == sha256_file(layout.graph)
    assert checkpoint["budget_hash"] == sha256_file(layout.budget)
    assert checkpoint["ledger_line_counts"] == {
        "gates": 0,
        "journal": 0,
        "model_calls": 0,
        "artifact_index": 0,
        "budget_settlements": 0,
    }


def _bootstrap_pointer(candidate_id: str = "baseline-1") -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "report_artifact_id": None,
        "evaluation_cycle_checkpoint_id": None,
        "promoted_at": "2026-07-29T12:00:00+00:00",
    }


def _verified_pointer(candidate_id: str = "candidate-1") -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "report_artifact_id": "artifact-report",
        "evaluation_cycle_checkpoint_id": "evaluation-cycle-1",
        "promoted_at": "2026-07-29T12:00:00Z",
    }


def _evaluation_anchor() -> dict[str, object]:
    return {
        "evaluation_spec_id": "eval-spec-1",
        "artifact_id": "artifact-spec",
        "artifact_sha256": SHA256,
        "gate_id": "gate-evaluation",
        "gate_payload_hash": "b" * 64,
    }


def _write_state(layout: ProjectLayout, state: dict[str, object]) -> None:
    atomic_write_json(layout.state, state)


def test_research_state_loader_rejects_every_missing_top_level_key(
    tmp_path: Path,
):
    for missing in sorted(STATE_FIELDS):
        layout = initialize_project(tmp_path / missing, ASSET)
        state = read_json(layout.state)
        del state[missing]
        _write_state(layout, state)

        with pytest.raises(ValueError, match="reinitializ"):
            load_research_state(layout)


def test_research_state_loader_rejects_unknown_top_level_key(tmp_path: Path):
    layout = initialize_project(tmp_path, ASSET)
    state = read_json(layout.state)
    state["legacy_best"] = "baseline-1"
    _write_state(layout, state)

    with pytest.raises(ValueError, match="unknown.*legacy_best"):
        load_research_state(layout)


def test_research_state_loader_rejects_mistyped_top_level_values(
    tmp_path: Path,
):
    cases = (
        ("schema_version", True),
        ("revision", True),
        ("research_id", 7),
        ("current_stage", 7),
        ("previous_state_hash", 7),
        ("last_transition_id", 7),
        ("pending_gate", 7),
        ("initial_baseline", 7),
        ("provisional_best", "legacy-provisional"),
        ("current_best", "legacy-current"),
        ("active_evaluation_spec", []),
    )
    for index, (field, invalid) in enumerate(cases):
        layout = initialize_project(tmp_path / f"{index}-{field}", ASSET)
        state = read_json(layout.state)
        state[field] = invalid
        _write_state(layout, state)

        with pytest.raises(ValueError, match=field):
            load_research_state(layout)


def test_research_state_loader_rejects_every_missing_best_pointer_key(
    tmp_path: Path,
):
    for missing in sorted(BEST_POINTER_FIELDS):
        layout = initialize_project(tmp_path / missing, ASSET)
        state = read_json(layout.state)
        state["initial_baseline"] = "baseline-1"
        state["current_best"] = _verified_pointer()
        del state["current_best"][missing]
        _write_state(layout, state)

        with pytest.raises(ValueError, match=f"current_best.*{missing}"):
            load_research_state(layout)


def test_research_state_loader_rejects_unknown_best_pointer_key(tmp_path: Path):
    layout = initialize_project(tmp_path, ASSET)
    state = read_json(layout.state)
    state["initial_baseline"] = "baseline-1"
    state["current_best"] = _verified_pointer()
    state["current_best"]["legacy_report"] = "artifact-old"
    _write_state(layout, state)

    with pytest.raises(ValueError, match="current_best.*legacy_report"):
        load_research_state(layout)


def test_research_state_loader_rejects_mistyped_best_pointer_fields(
    tmp_path: Path,
):
    cases = (
        ("candidate_id", 7),
        ("report_artifact_id", 7),
        ("evaluation_cycle_checkpoint_id", 7),
        ("promoted_at", 7),
    )
    for field, invalid in cases:
        layout = initialize_project(tmp_path / field, ASSET)
        state = read_json(layout.state)
        state["initial_baseline"] = "baseline-1"
        state["current_best"] = _verified_pointer()
        state["current_best"][field] = invalid
        _write_state(layout, state)

        with pytest.raises(ValueError, match=f"current_best.*{field}"):
            load_research_state(layout)


def test_research_state_loader_rejects_every_missing_evaluation_anchor_key(
    tmp_path: Path,
):
    for missing in sorted(EVALUATION_ANCHOR_FIELDS):
        layout = initialize_project(tmp_path / missing, ASSET)
        state = read_json(layout.state)
        state["active_evaluation_spec"] = _evaluation_anchor()
        del state["active_evaluation_spec"][missing]
        _write_state(layout, state)

        with pytest.raises(
            ValueError,
            match=f"active_evaluation_spec.*{missing}",
        ):
            load_research_state(layout)


def test_research_state_loader_rejects_unknown_evaluation_anchor_key(
    tmp_path: Path,
):
    layout = initialize_project(tmp_path, ASSET)
    state = read_json(layout.state)
    state["active_evaluation_spec"] = _evaluation_anchor()
    state["active_evaluation_spec"]["legacy_hash"] = SHA256
    _write_state(layout, state)

    with pytest.raises(ValueError, match="active_evaluation_spec.*legacy_hash"):
        load_research_state(layout)


def test_research_state_loader_requires_nonempty_string_evaluation_anchor_fields(
    tmp_path: Path,
):
    for field in sorted(EVALUATION_ANCHOR_FIELDS):
        for index, invalid in enumerate((7, "")):
            layout = initialize_project(
                tmp_path / f"{field}-{index}", ASSET
            )
            state = read_json(layout.state)
            state["active_evaluation_spec"] = _evaluation_anchor()
            state["active_evaluation_spec"][field] = invalid
            _write_state(layout, state)

            with pytest.raises(
                ValueError,
                match=f"active_evaluation_spec.*{field}",
            ):
                load_research_state(layout)


def test_research_state_loader_requires_lowercase_sha256_anchor_hashes(
    tmp_path: Path,
):
    invalid_hashes = ("a" * 63, "A" * 64, "g" * 64)
    for field in ("artifact_sha256", "gate_payload_hash"):
        for index, invalid in enumerate(invalid_hashes):
            layout = initialize_project(
                tmp_path / f"{field}-{index}", ASSET
            )
            state = read_json(layout.state)
            state["active_evaluation_spec"] = _evaluation_anchor()
            state["active_evaluation_spec"][field] = invalid
            _write_state(layout, state)

            with pytest.raises(
                ValueError,
                match=f"active_evaluation_spec.*{field}",
            ):
                load_research_state(layout)


def test_research_state_loader_enforces_cross_field_invariants(
    tmp_path: Path,
):
    cases = (
        (lambda state: state.update(previous_state_hash=SHA256), "previous_state_hash"),
        (lambda state: state.update(last_transition_id="transition-1"), "last_transition_id"),
        (lambda state: state.update(revision=1), "previous_state_hash"),
        (
            lambda state: state.update(current_best=_bootstrap_pointer()),
            "initial_baseline",
        ),
        (
            lambda state: state.update(initial_baseline="baseline-1"),
            "current_best",
        ),
        (
            lambda state: state.update(
                initial_baseline="baseline-1",
                current_best=_bootstrap_pointer("other-candidate"),
            ),
            "current_best",
        ),
        (
            lambda state: state.update(
                initial_baseline="baseline-1",
                current_best={
                    **_verified_pointer(),
                    "report_artifact_id": None,
                },
            ),
            "current_best",
        ),
        (
            lambda state: state.update(
                initial_baseline="baseline-1",
                current_best=_bootstrap_pointer(),
                provisional_best={
                    **_verified_pointer(),
                    "evaluation_cycle_checkpoint_id": None,
                },
            ),
            "provisional_best",
        ),
    )
    for index, (mutate, field) in enumerate(cases):
        layout = initialize_project(tmp_path / str(index), ASSET)
        state = read_json(layout.state)
        mutate(state)
        _write_state(layout, state)

        with pytest.raises(ValueError, match=field):
            load_research_state(layout)


def test_research_state_loader_requires_rfc3339_promoted_at(
    tmp_path: Path,
):
    for index, timestamp in enumerate(
        ("", "2026-07-29", "not-a-timestamp")
    ):
        layout = initialize_project(tmp_path / str(index), ASSET)
        state = read_json(layout.state)
        state["initial_baseline"] = "baseline-1"
        state["current_best"] = {
            **_bootstrap_pointer(),
            "promoted_at": timestamp,
        }
        _write_state(layout, state)

        with pytest.raises(ValueError, match="current_best.*promoted_at"):
            load_research_state(layout)


def test_research_state_loader_rejects_stage_not_declared_by_graph(tmp_path: Path):
    layout = initialize_project(tmp_path, ASSET)
    state = read_json(layout.state)
    state["current_stage"] = "legacy-stage"
    _write_state(layout, state)

    with pytest.raises(ValueError, match="current_stage"):
        load_research_state(layout)


def test_project_exposes_no_public_general_research_state_writer():
    assert not hasattr(project, "write_research_state")


def test_initial_research_state_helper_is_create_only(tmp_path: Path):
    layout = initialize_project(tmp_path, ASSET)
    before = layout.state.read_bytes()

    with pytest.raises(FileExistsError, match="already exists"):
        project._create_initial_research_state(
            layout,
            research_id="replacement",
            current_stage="intake",
        )

    assert layout.state.read_bytes() == before


def test_prechange_state_requires_project_reinitialization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    layout = initialize_project(tmp_path, ASSET)
    legacy = read_json(layout.state)
    del legacy["schema_version"]
    _write_state(layout, legacy)

    assert main(["validate", "--root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "invalid"
    assert "reinitializ" in " ".join(result["errors"])


def test_initialize_refuses_to_overwrite_existing_research_directory(
    tmp_path: Path,
):
    (tmp_path / ".research").mkdir()

    with pytest.raises(FileExistsError, match=r"\.research"):
        initialize_project(tmp_path, ASSET)


def test_initialize_rejects_invalid_graph_without_leaving_partial_project(
    tmp_path: Path,
):
    invalid_profile = tmp_path / "invalid.yaml"
    invalid_profile.write_text("schema_version: 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid research graph"):
        initialize_project(tmp_path, invalid_profile)

    assert not (tmp_path / ".research").exists()


def test_initialize_preflights_every_declared_skill(tmp_path: Path):
    graph = load_graph(ASSET)
    graph["stages"][0]["primary_skill"] = "missing-test-skill"
    profile = tmp_path / "missing-skill.yaml"
    profile.write_text(
        yaml.safe_dump(graph, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(FileNotFoundError, match="SKILL.md not found"):
        initialize_project(tmp_path / "project", profile)

    assert not (tmp_path / "project" / ".research").exists()


def test_validate_graph_rejects_missing_graph_fields():
    for field in sorted(GRAPH_FIELDS):
        graph = valid_graph()
        del graph[field]

        assert any(
            f"missing required field: {field}" in error
            for error in validate_graph(graph)
        )


def test_validate_graph_rejects_missing_limit_fields():
    for field in sorted(LIMIT_FIELDS):
        graph = valid_graph()
        del graph["limits"][field]

        assert any(
            f"limits missing required field: {field}" in error
            for error in validate_graph(graph)
        )


def test_validate_graph_rejects_retired_loop_bound_name():
    graph = valid_graph()
    graph["limits"]["max_parallel_subagents"] = 3

    assert "limits unknown field: max_parallel_subagents" in validate_graph(
        graph
    )


def test_validate_graph_rejects_invalid_search_limits():
    for field in ("loop-subagent-bound", "max_debug_depth"):
        for value in (True, -1, 1.5, float("inf")):
            graph = valid_graph()
            graph["limits"][field] = value

            assert any(field in error for error in validate_graph(graph))


def test_validate_graph_rejects_missing_stage_fields():
    for field in sorted(STAGE_FIELDS):
        graph = valid_graph()
        del graph["stages"][0][field]

        assert any(
            f"stage 0 missing required field: {field}" in error
            for error in validate_graph(graph)
        )


@pytest.mark.parametrize("owner", (None, "worker", 1))
def test_validate_graph_rejects_unknown_task_owner(owner):
    graph = valid_graph()
    graph["stages"][0]["task_owner"] = owner

    assert any("task_owner" in error for error in validate_graph(graph))


def test_validate_graph_rejects_duplicate_stage_ids():
    graph = valid_graph()
    graph["stages"].append(stage("one"))

    assert "duplicate stage id: one" in validate_graph(graph)


def test_validate_graph_rejects_unknown_next_target():
    graph = valid_graph()
    graph["stages"][0]["next"] = ["missing"]

    assert "unknown next target: missing" in validate_graph(graph)


def test_validate_graph_rejects_missing_start_stage():
    graph = valid_graph()
    graph["start_stage"] = "missing"

    assert "missing start stage: missing" in validate_graph(graph)


def test_validate_graph_rejects_more_than_two_capability_skills():
    graph = valid_graph()
    graph["stages"][0]["capability_skills"] = ["a", "b", "c"]

    assert "stage one has more than two capability skills" in validate_graph(graph)


def test_validate_graph_rejects_autoresearch_loop_as_capability_skill():
    graph = valid_graph()
    graph["stages"][0]["capability_skills"] = ["autoresearch-loop"]

    assert "autoresearch-loop cannot be a capability skill" in validate_graph(graph)


def test_validate_graph_rejects_skill_composition_depth_above_two():
    graph = valid_graph()
    graph["stages"] = [
        stage("one", primary_skill="alpha", capability_skills=["beta"]),
        stage("two", primary_skill="beta", capability_skills=["gamma"]),
    ]

    assert "skill composition depth exceeds two" in validate_graph(graph)


def test_validate_graph_rejects_accumulated_skill_composition_cycle():
    graph = valid_graph()
    graph["stages"] = [
        stage("one", primary_skill="alpha", capability_skills=["beta"]),
        stage("two", primary_skill="beta", capability_skills=["alpha"]),
    ]

    assert "skill composition graph contains a cycle" in validate_graph(graph)


def test_validate_graph_permits_stage_cycles():
    graph = valid_graph()
    graph["stages"] = [
        stage("one", next_stages=["two"]),
        stage("two", next_stages=["one"]),
    ]

    assert validate_graph(graph) == []


def test_load_graph_rejects_non_mapping_root(tmp_path: Path):
    graph_path = tmp_path / "list.yaml"
    graph_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="YAML root must be a mapping"):
        load_graph(graph_path)


def test_load_graph_rejects_unsafe_yaml_tag(tmp_path: Path):
    graph_path = tmp_path / "tagged.yaml"
    graph_path.write_text(
        "value: !!python/object/apply:builtins.str [unsafe]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="could not load YAML"):
        load_graph(graph_path)


def test_validation_does_not_mutate_graph():
    graph = valid_graph()
    original = copy.deepcopy(graph)

    validate_graph(graph)

    assert graph == original


def test_cli_init_validate_and_status_emit_compact_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(["init", "--root", str(tmp_path), "--profile", str(ASSET)]) == 0
    initialized = capsys.readouterr().out
    assert initialized == initialized.strip() + "\n"
    assert ": " not in initialized
    assert json.loads(initialized)["status"] == "initialized"

    assert main(["validate", "--root", str(tmp_path)]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated == {"status": "valid", "errors": []}

    assert main(["status", "--root", str(tmp_path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "ok"
    assert status["revision"] == 0
    assert status["current_stage"] == "intake"
    assert status["budget_status"] == "unapproved"


def test_cli_validate_returns_nonzero_for_graph_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    layout = initialize_project(tmp_path, ASSET)
    layout.graph.write_text("schema_version: 1\n", encoding="utf-8")

    assert main(["validate", "--root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "invalid"
    assert result["errors"]


def test_cli_validate_rejects_an_uncommitted_valid_graph_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    layout = initialize_project(tmp_path, ASSET)
    graph = load_graph(layout.graph)
    graph["limits"]["loop-subagent-bound"] += 1
    layout.graph.write_text(
        yaml.safe_dump(graph, sort_keys=False), encoding="utf-8"
    )

    assert main(["validate", "--root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "invalid"
    assert result["errors"] == ["graph hash mismatch with initial checkpoint"]


def test_cli_status_rejects_an_uncommitted_valid_graph_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    layout = initialize_project(tmp_path, ASSET)
    graph = load_graph(layout.graph)
    graph["limits"]["loop-subagent-bound"] += 1
    layout.graph.write_text(
        yaml.safe_dump(graph, sort_keys=False), encoding="utf-8"
    )

    assert main(["status", "--root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "invalid"
    assert result["graph_errors"] == [
        "graph hash mismatch with initial checkpoint"
    ]


def test_internal_worker_resolution_does_not_use_top_level_user_copy(tmp_path, monkeypatch):
    from autoresearch.skills import resolve_skill_path
    monkeypatch.setenv("HOME", str(tmp_path))
    resolved = resolve_skill_path("research-experiment-execution")
    assert resolved == ASSET.parents[2] / "skills" / "research-experiment-execution" / "SKILL.md"
    assert resolved.is_file()
