from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest

from autoresearch.artifacts import (
    mark_artifact_unavailable,
    register_artifact,
    resolve_artifact,
    verify_artifact,
)
from autoresearch.io import read_json, read_jsonl
from autoresearch.workflow import open_task
from researchctl import main


def navigation():
    return importlib.import_module("autoresearch.runtime_navigation")


@pytest.fixture
def runtime_spec(project_layout):
    root = project_layout.root
    (root / "src").mkdir()
    (root / "src/train.py").write_text("print('train')\n", encoding="utf-8")
    (root / "src/evaluate.py").write_text("print('evaluate')\n", encoding="utf-8")
    return {
        "schema_version": 1,
        "entries": [
            {
                "id": "train-smoke",
                "kind": "command",
                "summary": "Smoke training with a small sample before a full run.",
                "tags": ["train", "smoke", "训练"],
                "sources": ["src/train.py"],
                "argv": ["uv", "run", "python", "src/train.py", "--name", "a b"],
                "artifact_ids": [],
            },
            {
                "id": "evaluate",
                "kind": "entrypoint",
                "summary": "Evaluate a fixed checkpoint and write metrics.",
                "tags": ["evaluate", "metrics", "评估"],
                "sources": ["src/evaluate.py"],
                "artifact_ids": [],
            },
        ],
    }


def index_document(layout, record):
    return read_json(Path(record["path"]))


def task_spec(task_id, inputs):
    return {
        "cycle_id": "cycle-nav",
        "task_id": task_id,
        "candidate_id": "candidate-nav",
        "stage_id": "intake",
        "primary_skill": "autoresearch-loop",
        "capability_skills": [],
        "input_artifact_ids": inputs,
        "budget_slice": {"wall_seconds": 300},
        "editable_paths": [],
        "output_path": f"cycles/cycle-nav/tasks/{task_id}/report.md",
        "stop_conditions": ["budget exhausted"],
        "work_type": "simple",
    }


def test_build_reuses_identical_index_and_preserves_argv(project_layout, runtime_spec):
    first = navigation().build_runtime_index(project_layout, runtime_spec)
    second = navigation().build_runtime_index(project_layout, runtime_spec)
    assert first["artifact_id"] == second["artifact_id"]
    entry = index_document(project_layout, first)["entries"][0]
    assert entry["argv"] == ["uv", "run", "python", "src/train.py", "--name", "a b"]
    source = resolve_artifact(project_layout, entry["sources"][0]["artifact_id"])
    assert Path(source["path"]).read_text() == "print('train')\n"
    assert source["storage"] == "snapshot"
    assert not (project_layout.root / "a b").exists()


def test_select_supplies_only_relevant_registered_inputs(project_layout, runtime_spec):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    result = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "train"
    )
    assert result["status"] == "selected"
    assert result["matched_entry_ids"] == ["train-smoke"]
    context = read_json(
        Path(resolve_artifact(project_layout, result["context_artifact_id"])["path"])
    )
    assert [entry["id"] for entry in context["entries"]] == ["train-smoke"]
    source_id = context["entries"][0]["sources"][0]["artifact_id"]
    assert result["input_artifact_ids"] == [result["context_artifact_id"], source_id]
    assert index["artifact_id"] not in result["input_artifact_ids"]
    assert result["context_chars"] <= 6000


def test_select_links_fixed_run_artifacts(project_layout, runtime_spec):
    metric_path = project_layout.root / "metrics.json"
    metric_path.write_text('{"accuracy": 0.8}')
    metric = register_artifact(
        project_layout,
        metric_path,
        {
            "type": "metrics",
            "producer": "test",
            "storage": "snapshot",
        },
    )
    runtime_spec["entries"][1]["artifact_ids"] = [metric["artifact_id"]]
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    result = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "评估"
    )
    assert metric["artifact_id"] in result["input_artifact_ids"]


def test_query_filters_limits_and_does_not_return_zero_score_entries(
    project_layout, runtime_spec
):
    runtime_spec["entries"][0]["tags"].append("common")
    runtime_spec["entries"][1]["tags"].append("common")
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    selected = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "common", limit=1
    )
    assert selected["matched_entry_ids"] == ["evaluate"]
    filtered = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "common", kind="command"
    )
    assert filtered["matched_entry_ids"] == ["train-smoke"]
    before = project_layout.artifact_index.read_bytes()
    empty = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "nonexistent-keyword"
    )
    assert empty["status"] == "no_matches"
    assert empty["input_artifact_ids"] == []
    assert project_layout.artifact_index.read_bytes() == before


def test_character_budget_never_truncates_commands(project_layout, runtime_spec):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    with pytest.raises(ValueError, match="character budget"):
        navigation().select_runtime_context(
            project_layout, index["artifact_id"], "train", max_chars=100
        )


@pytest.mark.parametrize(
    "path", ["../outside.py", "/tmp/outside.py", ".research/state.json"]
)
def test_build_rejects_sources_outside_repository_or_in_controller_state(
    project_layout, runtime_spec, path
):
    runtime_spec["entries"][0]["sources"] = [path]
    with pytest.raises(ValueError, match="source"):
        navigation().build_runtime_index(project_layout, runtime_spec)
    assert read_jsonl(project_layout.artifact_index) == []


def test_build_rejects_symlink_escape(project_layout, runtime_spec, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("outside")
    try:
        (project_layout.root / "escape.py").symlink_to(outside)
        runtime_spec["entries"][0]["sources"] = ["escape.py"]
        with pytest.raises(ValueError, match="source"):
            navigation().build_runtime_index(project_layout, runtime_spec)
    finally:
        outside.unlink()


@pytest.mark.parametrize(
    "mutation", ["duplicate", "missing-source", "shell-string", "unknown-field"]
)
def test_invalid_spec_is_rejected_before_registering_inputs(
    project_layout, runtime_spec, mutation
):
    if mutation == "duplicate":
        runtime_spec["entries"].append(deepcopy(runtime_spec["entries"][0]))
    elif mutation == "missing-source":
        runtime_spec["entries"][1]["sources"] = ["missing.py"]
    elif mutation == "shell-string":
        runtime_spec["entries"][0]["argv"] = "uv run python src/train.py"
    else:
        runtime_spec["entries"][1]["execute"] = True
    with pytest.raises((ValueError, FileNotFoundError)):
        navigation().build_runtime_index(project_layout, runtime_spec)
    assert read_jsonl(project_layout.artifact_index) == []


def test_changed_source_requires_new_index_but_unrelated_entry_remains_usable(
    project_layout, runtime_spec
):
    first = navigation().build_runtime_index(project_layout, runtime_spec)
    (project_layout.root / "src/train.py").write_text("print('updated')\n")
    with pytest.raises(ValueError, match="stale"):
        navigation().select_runtime_context(
            project_layout, first["artifact_id"], "train"
        )
    unaffected = navigation().select_runtime_context(
        project_layout, first["artifact_id"], "evaluate"
    )
    assert unaffected["matched_entry_ids"] == ["evaluate"]
    second = navigation().build_runtime_index(
        project_layout, runtime_spec, parent_id=first["artifact_id"]
    )
    assert second["artifact_id"] != first["artifact_id"]
    assert second["parent_artifact_id"] == first["artifact_id"]
    assert (
        navigation().select_runtime_context(
            project_layout, second["artifact_id"], "train"
        )["status"]
        == "selected"
    )


def test_task_open_checks_dependencies_and_staleness_before_writing(
    project_layout, runtime_spec
):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    result = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "train"
    )
    with pytest.raises(ValueError, match="input_artifact_ids"):
        open_task(
            project_layout, task_spec("missing-input", [result["context_artifact_id"]])
        )
    (project_layout.root / "src/train.py").write_text(
        "print('changed after selection')\n"
    )
    with pytest.raises(ValueError, match="stale"):
        open_task(
            project_layout, task_spec("stale-input", result["input_artifact_ids"])
        )
    assert not (project_layout.cycles / "cycle-nav").exists()


def test_open_task_keeps_immutable_context_when_index_is_refreshed(
    project_layout, runtime_spec
):
    first = navigation().build_runtime_index(project_layout, runtime_spec)
    result = navigation().select_runtime_context(
        project_layout, first["artifact_id"], "train"
    )
    task = open_task(project_layout, task_spec("bound", result["input_artifact_ids"]))
    brief_path = Path(task["brief_path"])
    before = brief_path.read_bytes()
    (project_layout.root / "src/train.py").write_text("print('next revision')\n")
    second = navigation().build_runtime_index(
        project_layout, runtime_spec, parent_id=first["artifact_id"]
    )
    assert first["artifact_id"] != second["artifact_id"]
    assert brief_path.read_bytes() == before
    manifest_ids = [
        row["artifact_id"] for row in task["context_manifest"]["input_artifacts"]
    ]
    assert manifest_ids == result["input_artifact_ids"]
    source = resolve_artifact(project_layout, manifest_ids[1])
    assert Path(source["path"]).read_text() == "print('train')\n"


def test_cli_build_select_and_task_open(project_layout, runtime_spec, capsys):
    spec_path = project_layout.root / "runtime-spec.json"
    spec_path.write_text(json.dumps(runtime_spec))
    assert (
        main(
            [
                "runtime-index-build",
                "--root",
                str(project_layout.root),
                "--spec",
                str(spec_path),
            ]
        )
        == 0
    )
    index = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                "runtime-context",
                "--root",
                str(project_layout.root / ".research"),
                "--index-id",
                index["artifact_id"],
                "--query",
                "训练",
            ]
        )
        == 0
    )
    selected = json.loads(capsys.readouterr().out)
    task_path = project_layout.root / "task-spec.json"
    task_path.write_text(
        json.dumps(task_spec("cli-task", selected["input_artifact_ids"]))
    )
    assert (
        main(
            ["task-open", "--root", str(project_layout.root), "--spec", str(task_path)]
        )
        == 0
    )
    task = json.loads(capsys.readouterr().out)
    assert task["task_spec"]["input_artifact_ids"] == selected["input_artifact_ids"]


def test_unchanged_refresh_reuses_parent_version(project_layout, runtime_spec):
    first = navigation().build_runtime_index(project_layout, runtime_spec)
    refreshed = navigation().build_runtime_index(
        project_layout, runtime_spec, parent_id=first["artifact_id"]
    )
    assert refreshed["artifact_id"] == first["artifact_id"]


def test_selected_artifacts_must_remain_available_at_task_open(
    project_layout, runtime_spec
):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    selected = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "train"
    )
    mark_artifact_unavailable(
        project_layout, selected["input_artifact_ids"][1], "removed"
    )
    with pytest.raises(ValueError, match="invalid input artifact"):
        open_task(
            project_layout, task_spec("unavailable", selected["input_artifact_ids"])
        )
    assert not (project_layout.cycles / "cycle-nav").exists()


def test_context_cannot_omit_sources_declared_by_index(project_layout, runtime_spec):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    selected = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "train"
    )
    record = resolve_artifact(project_layout, selected["context_artifact_id"])
    forged = read_json(Path(record["path"]))
    forged["entries"][0]["sources"] = []
    forged["entries"][0]["kind"] = "artifact"
    forged["entries"][0].pop("argv")
    forged["entries"][0]["artifact_ids"] = [index["artifact_id"]]
    forged_path = project_layout.root / "forged-context.json"
    forged_path.write_text(json.dumps(forged))
    forged_record = register_artifact(
        project_layout,
        forged_path,
        {
            "type": "runtime-context",
            "producer": "test",
            "storage": "snapshot",
        },
    )
    with pytest.raises(ValueError, match="match their registered index"):
        open_task(
            project_layout,
            task_spec("forged", [forged_record["artifact_id"], index["artifact_id"]]),
        )


def test_character_budget_returns_complete_ranked_prefix(project_layout, runtime_spec):
    for entry in runtime_spec["entries"]:
        entry["tags"].append("common")
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    first = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "common", limit=1
    )
    budget = first["context_chars"] + 1
    selected = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "common", max_chars=budget
    )
    record = resolve_artifact(project_layout, selected["context_artifact_id"])
    assert selected["matched_entry_ids"] == ["evaluate"]
    assert selected["context_chars"] == len(Path(record["path"]).read_text())
    assert selected["context_chars"] <= budget


@pytest.mark.parametrize(
    "options",
    [
        {"limit": 0},
        {"limit": 11},
        {"limit": True},
        {"max_chars": 12001},
        {"kind": "unknown"},
    ],
)
def test_invalid_selection_bounds_rejected(project_layout, runtime_spec, options):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    with pytest.raises(ValueError):
        navigation().select_runtime_context(
            project_layout, index["artifact_id"], "train", **options
        )


@pytest.mark.parametrize("target", ["source", "index", "context"])
@pytest.mark.parametrize("damage", ["unavailable", "missing", "corrupt"])
def test_explicit_rebuild_and_selection_replace_invalid_snapshots(
    project_layout, runtime_spec, target, damage
):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    selected = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "train"
    )
    old_id = {
        "source": selected["input_artifact_ids"][1],
        "index": index["artifact_id"],
        "context": selected["context_artifact_id"],
    }[target]
    old_path = Path(resolve_artifact(project_layout, old_id)["path"])
    if damage == "unavailable":
        mark_artifact_unavailable(project_layout, old_id, "fixture unavailable")
    elif damage == "missing":
        old_path.unlink()
    else:
        old_path.write_text("corrupt fixture")
    old_bytes = old_path.read_bytes() if old_path.exists() else None
    if target != "context":
        index = navigation().build_runtime_index(project_layout, runtime_spec)
    refreshed = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "train"
    )
    new_id = {
        "source": refreshed["input_artifact_ids"][1],
        "index": index["artifact_id"],
        "context": refreshed["context_artifact_id"],
    }[target]
    assert new_id != old_id
    assert resolve_artifact(project_layout, new_id)["parent_artifact_id"] == old_id
    for artifact_id in [index["artifact_id"], *refreshed["input_artifact_ids"]]:
        verification = verify_artifact(project_layout, artifact_id)
        assert verification["available"] is True
        assert verification["valid"] is True
    assert (old_path.read_bytes() if old_path.exists() else None) == old_bytes
    if damage == "unavailable":
        assert resolve_artifact(project_layout, old_id)["availability"] == "unavailable"


def test_task_open_rejects_external_context(project_layout, runtime_spec):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    selected = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "train"
    )
    context = resolve_artifact(project_layout, selected["context_artifact_id"])
    external_path = project_layout.root / "external-context.json"
    external_path.write_bytes(Path(context["path"]).read_bytes())
    external = register_artifact(
        project_layout,
        external_path,
        {
            "type": "runtime-context",
            "producer": "test",
            "storage": "external",
        },
    )
    with pytest.raises(ValueError, match="snapshot"):
        open_task(
            project_layout,
            task_spec(
                "external",
                [external["artifact_id"], *selected["input_artifact_ids"][1:]],
            ),
        )


def test_selection_rejects_external_source_reference(project_layout, runtime_spec):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    external = register_artifact(
        project_layout,
        project_layout.root / "src/train.py",
        {
            "type": "runtime-source",
            "producer": "test",
            "storage": "external",
        },
    )
    document = index_document(project_layout, index)
    document["entries"][0]["sources"][0]["artifact_id"] = external["artifact_id"]
    path = project_layout.root / "external-source-index.json"
    path.write_text(json.dumps(document))
    forged = register_artifact(
        project_layout,
        path,
        {
            "type": "runtime-index",
            "producer": "test",
            "storage": "snapshot",
        },
    )
    with pytest.raises(ValueError, match="snapshot"):
        navigation().select_runtime_context(
            project_layout, forged["artifact_id"], "train"
        )


def test_task_open_rejects_oversized_manually_registered_context(
    project_layout, runtime_spec
):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    selected = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "train"
    )
    context = resolve_artifact(project_layout, selected["context_artifact_id"])
    document = read_json(Path(context["path"]))
    document["query"] = "x" * 15000
    path = project_layout.root / "oversized-context.json"
    path.write_text(json.dumps(document))
    forged = register_artifact(
        project_layout,
        path,
        {
            "type": "runtime-context",
            "producer": "test",
            "storage": "snapshot",
        },
    )
    with pytest.raises(ValueError, match="query|character"):
        open_task(
            project_layout,
            task_spec(
                "oversized",
                [forged["artifact_id"], *selected["input_artifact_ids"][1:]],
            ),
        )


def test_task_open_caps_actual_context_file_including_whitespace(
    project_layout, runtime_spec
):
    index = navigation().build_runtime_index(project_layout, runtime_spec)
    selected = navigation().select_runtime_context(
        project_layout, index["artifact_id"], "train"
    )
    context = resolve_artifact(project_layout, selected["context_artifact_id"])
    path = project_layout.root / "padded-context.json"
    path.write_text(Path(context["path"]).read_text() + " " * 13000)
    padded = register_artifact(
        project_layout,
        path,
        {
            "type": "runtime-context",
            "producer": "test",
            "storage": "snapshot",
        },
    )
    with pytest.raises(ValueError, match="character"):
        open_task(
            project_layout,
            task_spec(
                "padded", [padded["artifact_id"], *selected["input_artifact_ids"][1:]]
            ),
        )


def test_explicit_parent_refresh_recovers_unavailable_index(
    project_layout, runtime_spec
):
    first = navigation().build_runtime_index(project_layout, runtime_spec)
    mark_artifact_unavailable(
        project_layout, first["artifact_id"], "fixture unavailable"
    )
    refreshed = navigation().build_runtime_index(
        project_layout, runtime_spec, parent_id=first["artifact_id"]
    )
    assert refreshed["parent_artifact_id"] == first["artifact_id"]
    assert (
        navigation().select_runtime_context(
            project_layout, refreshed["artifact_id"], "train"
        )["status"]
        == "selected"
    )
