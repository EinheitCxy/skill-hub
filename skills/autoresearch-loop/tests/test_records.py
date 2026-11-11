from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from autoresearch.io import read_jsonl
from autoresearch.project import ProjectLayout
from autoresearch.records import append_model_call
from researchctl import main


EXPECTED_FIELDS = {
    "id",
    "at",
    "usage",
    "cycle",
    "task",
    "candidate",
    "skills",
    "model",
    "duration_ms",
    "tokens",
    "cost_usd",
    "report",
    "status",
}


def model_call(call_id: str = "call-1", **overrides) -> dict:
    record = {
        "id": call_id,
        "at": "2026-07-27T00:00:00Z",
        "usage": "hypothesis_planning",
        "cycle": "c1",
        "task": "t1",
        "candidate": None,
        "skills": ["research-hypothesis-generation"],
        "model": "provider/model",
        "duration_ms": 120,
        "tokens": {
            "input": 100,
            "output": 50,
            "cached": 0,
            "total": 150,
        },
        "cost_usd": 0.001,
        "report": "cycles/c1/tasks/t1/report.md",
        "status": "ok",
    }
    record.update(overrides)
    return record


def append_call_in_process(arguments):
    root, index = arguments
    append_model_call(
        ProjectLayout.from_root(root),
        model_call(f"call-{index}"),
    )


def test_standalone_model_call_mode_does_not_require_a_task_record(
    project_layout,
):
    task_record = (
        project_layout.cycles / "c1" / "tasks" / "t1" / "task.json"
    )
    assert not task_record.exists()

    row = append_model_call(project_layout, model_call())

    assert read_jsonl(project_layout.model_calls) == [row]


def test_unavailable_usage_is_null_and_never_estimated(project_layout):
    row = append_model_call(
        project_layout,
        model_call(tokens=None, cost_usd=None),
    )

    assert row["tokens"] is None
    assert row["cost_usd"] is None
    assert read_jsonl(project_layout.model_calls) == [row]


def test_model_call_requires_exact_schema(project_layout):
    for field in sorted(EXPECTED_FIELDS):
        missing = model_call()
        del missing[field]
        with pytest.raises(ValueError, match=field):
            append_model_call(project_layout, missing)

    for forbidden in ("prompt", "response", "transcript"):
        record = model_call()
        record[forbidden] = "raw model content"
        with pytest.raises(ValueError, match=forbidden):
            append_model_call(project_layout, record)

    unknown = model_call()
    unknown["latency_bucket"] = "fast"
    with pytest.raises(ValueError, match="latency_bucket"):
        append_model_call(project_layout, unknown)

    assert project_layout.model_calls.read_text(encoding="utf-8") == ""


def test_model_call_requires_rfc3339_timestamp(project_layout):
    invalid_timestamps = (
        "2026-07-27",
        "2026-07-27T00:00:00",
        "not-a-timestamp",
        "2026-02-30T00:00:00Z",
        123,
    )
    for timestamp in invalid_timestamps:
        with pytest.raises(ValueError, match="RFC-3339"):
            append_model_call(project_layout, model_call(at=timestamp))


def test_model_call_accepts_rfc3339_offset_and_preserves_value(project_layout):
    row = append_model_call(
        project_layout, model_call(at="2026-07-27T08:00:00.123456+08:00")
    )

    assert row["at"] == "2026-07-27T08:00:00.123456+08:00"
    assert datetime.fromisoformat(row["at"]).utcoffset() is not None


def test_duration_is_a_nonnegative_integer_and_rejects_bool(project_layout):
    for value in (-1, 1.5, True, "120", None):
        with pytest.raises(ValueError, match="duration_ms"):
            append_model_call(
                project_layout, model_call(duration_ms=value)
            )


def test_token_schema_and_total_are_strict(project_layout):
    invalid_tokens = (
        {"input": 1, "output": 2, "cached": 0},
        {"input": 1, "output": 2, "cached": 0, "total": 3, "extra": 0},
        {"input": -1, "output": 2, "cached": 0, "total": 1},
        {"input": 1.0, "output": 2, "cached": 0, "total": 3},
        {"input": True, "output": 2, "cached": 0, "total": 3},
        {"input": 1, "output": 2, "cached": 0, "total": 4},
        {"input": 1, "output": 2, "cached": 2, "total": 3},
        "unavailable",
    )
    for tokens in invalid_tokens:
        with pytest.raises(ValueError, match="tokens"):
            append_model_call(project_layout, model_call(tokens=tokens))


def test_cost_is_nonnegative_finite_number_or_null(project_layout):
    for value in (-0.01, True, "0.1", float("inf"), float("nan")):
        with pytest.raises(ValueError, match="cost_usd"):
            append_model_call(project_layout, model_call(cost_usd=value))


def test_model_call_requires_one_to_three_nonempty_skills(project_layout):
    invalid_skills = (
        [],
        ["one", "two", "three", "four"],
        ["one", ""],
        ["one", 2],
        "one",
    )
    for skills in invalid_skills:
        with pytest.raises(ValueError, match="skills"):
            append_model_call(project_layout, model_call(skills=skills))


def test_model_call_requires_nonempty_string_fields(project_layout):
    invalid_fields = (
        ("id", ""),
        ("usage", ""),
        ("cycle", None),
        ("task", 1),
        ("model", ""),
        ("status", ""),
    )
    for field, value in invalid_fields:
        with pytest.raises(ValueError, match=field):
            append_model_call(
                project_layout, model_call(**{field: value})
            )


def test_candidate_is_nullable_or_nonempty_string(project_layout):
    for candidate in (None, "candidate-a"):
        row = append_model_call(
            project_layout,
            model_call(call_id=f"call-{candidate}", candidate=candidate),
        )
        assert row["candidate"] == candidate


def test_candidate_rejects_other_values(project_layout):
    for candidate in ("", 1, False):
        with pytest.raises(ValueError, match="candidate"):
            append_model_call(
                project_layout, model_call(candidate=candidate)
            )


def test_report_path_is_normalized_inside_research_root(project_layout):
    row = append_model_call(
        project_layout,
        model_call(report="cycles/c1/../c1/tasks/t1/report.md"),
    )

    assert row["report"] == "cycles/c1/tasks/t1/report.md"


def test_absolute_report_inside_research_root_is_stored_relative(project_layout):
    report = project_layout.root / ".research" / "cycles" / "c1" / "report.md"
    row = append_model_call(project_layout, model_call(report=str(report)))

    assert row["report"] == "cycles/c1/report.md"


def test_report_path_cannot_escape_research_root(project_layout):
    for report in ("../outside.md", "/tmp/outside.md", "", None):
        with pytest.raises(ValueError, match="report"):
            append_model_call(project_layout, model_call(report=report))


def test_report_path_rejects_symlink_escape(project_layout, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = project_layout.root / ".research" / "cycles" / "escape"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="report"):
        append_model_call(
            project_layout,
            model_call(report="cycles/escape/report.md"),
        )


def test_duplicate_model_call_id_is_rejected_without_append(project_layout):
    first = append_model_call(project_layout, model_call())

    with pytest.raises(ValueError, match="already exists"):
        append_model_call(
            project_layout,
            model_call(usage="duplicate"),
        )

    assert read_jsonl(project_layout.model_calls) == [first]


def test_append_model_call_does_not_mutate_input(project_layout):
    record = model_call()
    original = deepcopy(record)

    append_model_call(project_layout, record)

    assert record == original


def test_model_calls_use_one_compact_json_object_per_line(project_layout):
    row = append_model_call(project_layout, model_call())
    content = project_layout.model_calls.read_text(encoding="utf-8")

    assert content == json.dumps(
        row, ensure_ascii=False, separators=(",", ":")
    ) + "\n"


def test_parallel_model_call_appends_remain_complete(project_layout):
    with ProcessPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                append_call_in_process,
                [(project_layout.root, index) for index in range(20)],
            )
        )

    rows = read_jsonl(project_layout.model_calls)
    assert len(rows) == 20
    assert {row["id"] for row in rows} == {
        f"call-{index}" for index in range(20)
    }


def test_record_call_cli_appends_and_emits_compact_json(
    project_layout, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    record_path = tmp_path / "call.json"
    record_path.write_text(json.dumps(model_call()), encoding="utf-8")

    assert main(
        [
            "record-call",
            "--root",
            str(project_layout.root),
            "--record",
            str(record_path),
        ]
    ) == 0

    captured = capsys.readouterr()
    row = json.loads(captured.out)
    assert captured.err == ""
    assert captured.out == json.dumps(
        row, ensure_ascii=False, separators=(",", ":")
    ) + "\n"
    assert read_jsonl(project_layout.model_calls) == [row]


def test_record_call_cli_validation_errors_are_compact(
    project_layout, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    record_path = tmp_path / "invalid-call.json"
    record_path.write_text(
        json.dumps(model_call(duration_ms=True)), encoding="utf-8"
    )

    assert main(
        [
            "record-call",
            "--root",
            str(project_layout.root),
            "--record",
            str(record_path),
        ]
    ) == 1

    captured = capsys.readouterr()
    error = json.loads(captured.out)
    assert captured.err == ""
    assert captured.out == json.dumps(
        error, ensure_ascii=False, separators=(",", ":")
    ) + "\n"
    assert error["status"] == "error"
    assert "duration_ms" in error["error"]
