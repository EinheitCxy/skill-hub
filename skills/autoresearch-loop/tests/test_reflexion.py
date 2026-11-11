from __future__ import annotations

from dataclasses import fields

import pytest

from autoresearch.io import read_jsonl
from autoresearch.project import ProjectLayout
from autoresearch.reflexion import (
    DEFAULT_MAX_REFLECTIONS_IN_PROMPT,
    STORED_REFLECTION_CAP,
    load_reflections,
    reflect,
)


def _reflections_path(layout: ProjectLayout):
    return layout.root / ".research" / "reflections.jsonl"


def test_reflect_writes_jsonl_row_and_returns_bounded_decision(project_layout):
    result = reflect(
        project_layout,
        candidate_id="cand-1",
        stage_id="stage-1",
        failure_reason="oom on gpu",
        alternative_approach="reduce batch size",
        should_retry=True,
    )

    assert result["should_retry"] is True
    assert result["alternative_approach"] == "reduce batch size"
    assert len(result["reflections_used"]) == 1
    assert result["reflections_used"][0]["candidate_id"] == "cand-1"

    rows = read_jsonl(_reflections_path(project_layout))
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "reflection_id",
        "candidate_id",
        "stage_id",
        "failure_reason",
        "alternative_approach",
        "should_retry",
        "created_at",
    }
    assert row["candidate_id"] == "cand-1"
    assert row["stage_id"] == "stage-1"
    assert row["failure_reason"] == "oom on gpu"
    assert row["alternative_approach"] == "reduce batch size"
    assert row["should_retry"] is True
    assert row["reflection_id"].startswith("reflection-")
    assert row["created_at"]


def test_load_reflections_returns_only_candidate_rows_in_recency_order(
    project_layout,
):
    for index in range(6):
        reflect(
            project_layout,
            candidate_id="cand-a",
            stage_id="stage-1",
            failure_reason=f"fail-{index}",
            alternative_approach=f"approach-{index}",
            should_retry=True,
        )
    reflect(
        project_layout,
        candidate_id="cand-b",
        stage_id="stage-1",
        failure_reason="other",
        alternative_approach="other",
        should_retry=True,
    )

    loaded = load_reflections(project_layout, "cand-a")
    assert len(loaded) == DEFAULT_MAX_REFLECTIONS_IN_PROMPT
    assert [row["failure_reason"] for row in loaded] == [
        "fail-1",
        "fail-2",
        "fail-3",
        "fail-4",
        "fail-5",
    ]
    assert load_reflections(project_layout, "cand-b") == load_reflections(
        project_layout, "cand-b", limit=5
    )
    assert load_reflections(project_layout, "cand-a", limit=2) == loaded[-2:]


def test_reflect_injects_at_most_five_and_considers_last_twenty(project_layout):
    for index in range(STORED_REFLECTION_CAP + 3):
        reflect(
            project_layout,
            candidate_id="cand-1",
            stage_id="stage-1",
            failure_reason=f"fail-{index}",
            alternative_approach=f"approach-{index}",
            should_retry=True,
            max_retry_attempts=STORED_REFLECTION_CAP + 10,
        )

    # inject cap: default 5
    result = reflect(
        project_layout,
        candidate_id="cand-1",
        stage_id="stage-1",
        failure_reason="fail-next",
        alternative_approach="approach-next",
        should_retry=True,
        max_retry_attempts=STORED_REFLECTION_CAP + 10,
    )
    assert len(result["reflections_used"]) == DEFAULT_MAX_REFLECTIONS_IN_PROMPT

    # stored cap: last 20 considered for quota; file is append-only (all kept)
    rows = read_jsonl(_reflections_path(project_layout))
    assert len(rows) == STORED_REFLECTION_CAP + 4
    capped = load_reflections(
        project_layout, "cand-1", limit=STORED_REFLECTION_CAP
    )
    assert len(capped) == STORED_REFLECTION_CAP


def test_reflect_quota_bounds_should_retry_to_false(project_layout):
    # max_retry_attempts=3: first three (prior 0,1,2 < 3) can retry.
    for index in range(3):
        result = reflect(
            project_layout,
            candidate_id="cand-2",
            stage_id="stage-1",
            failure_reason=f"fail-{index}",
            alternative_approach=f"approach-{index}",
            should_retry=True,
            max_retry_attempts=3,
        )
        assert result["should_retry"] is True

    # fourth: prior=3, 3 < 3 is False -> forced False even though asked True
    result = reflect(
        project_layout,
        candidate_id="cand-2",
        stage_id="stage-1",
        failure_reason="fail-3",
        alternative_approach="approach-3",
        should_retry=True,
        max_retry_attempts=3,
    )
    assert result["should_retry"] is False


def test_reflect_respects_coordinator_should_retry_false(project_layout):
    result = reflect(
        project_layout,
        candidate_id="cand-3",
        stage_id="stage-1",
        failure_reason="non-recoverable",
        alternative_approach="none",
        should_retry=False,
    )
    assert result["should_retry"] is False


def test_reflect_isolates_candidates(project_layout):
    reflect(
        project_layout,
        candidate_id="cand-a",
        stage_id="s",
        failure_reason="x",
        alternative_approach="a",
        should_retry=True,
    )
    reflect(
        project_layout,
        candidate_id="cand-b",
        stage_id="s",
        failure_reason="y",
        alternative_approach="b",
        should_retry=True,
    )
    assert len(load_reflections(project_layout, "cand-a")) == 1
    assert len(load_reflections(project_layout, "cand-b")) == 1
    assert load_reflections(project_layout, "cand-a")[0][
        "candidate_id"
    ] == "cand-a"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"candidate_id": "", "stage_id": "s", "failure_reason": "x", "alternative_approach": "a", "should_retry": True},
        {"candidate_id": "c", "stage_id": "", "failure_reason": "x", "alternative_approach": "a", "should_retry": True},
        {"candidate_id": "c", "stage_id": "s", "failure_reason": "", "alternative_approach": "a", "should_retry": True},
        {"candidate_id": "c", "stage_id": "s", "failure_reason": "x", "alternative_approach": "", "should_retry": True},
    ],
)
def test_reflect_rejects_invalid_inputs(project_layout, kwargs):
    with pytest.raises(ValueError):
        reflect(project_layout, **kwargs)


def test_reflexion_module_is_coordinator_side_only():
    # reflexion lives at the top level (not under workflow/) and imports no
    # worker/subagent machinery; it is a Coordinator-side decision mechanism.
    import autoresearch.reflexion as reflexion

    assert hasattr(reflexion, "reflect")
    assert hasattr(reflexion, "load_reflections")
    public = {name for name in dir(reflexion) if not name.startswith("__")}
    assert "subagent" not in " ".join(public).lower()
