import json
from pathlib import Path
import re

import pytest

from autoresearch import artifacts
from autoresearch.artifacts import (
    bind_artifact_alias,
    list_artifacts,
    mark_artifact_unavailable,
    register_artifact,
    register_artifact_version,
    resolve_artifact,
    resolve_artifact_alias,
    verify_run_snapshot_artifacts,
    verify_artifact,
)
from autoresearch.io import read_jsonl, sha256_file
from researchctl import main


def artifact_metadata(**overrides) -> dict:
    metadata = {
        "type": "evidence",
        "task_id": "t1",
        "producer": "worker-a",
        "storage": "snapshot",
        "provenance_tags": ["fixture"],
        "comparability_tags": ["dataset-v1"],
    }
    metadata.update(overrides)
    return metadata


def test_registered_artifact_detects_mutation(project_layout, tmp_path: Path):
    source = tmp_path / "evidence.md"
    source.write_text("first", encoding="utf-8")
    record = register_artifact(
        project_layout,
        source,
        artifact_metadata(),
    )

    assert verify_artifact(project_layout, record["artifact_id"])["valid"] is True
    source.write_text("mutated", encoding="utf-8")
    assert verify_artifact(project_layout, record["artifact_id"])["valid"] is True
    Path(record["path"]).write_text("tampered", encoding="utf-8")
    assert verify_artifact(project_layout, record["artifact_id"])["valid"] is False


def test_logical_output_alias_reuses_one_registered_artifact(
    project_layout, tmp_path: Path
):
    source = tmp_path / "outputs.json"
    source.write_text("{}", encoding="utf-8")
    artifact = register_artifact(project_layout, source, artifact_metadata())

    first = bind_artifact_alias(
        project_layout, "metrics-report", artifact["artifact_id"]
    )
    second = bind_artifact_alias(
        project_layout, "evaluation-input", artifact["artifact_id"]
    )
    repeated = bind_artifact_alias(
        project_layout, "metrics-report", artifact["artifact_id"]
    )

    assert first["artifact_id"] == second["artifact_id"] == artifact["artifact_id"]
    assert repeated == first
    assert [row["artifact_id"] for row in resolve_artifact_alias(project_layout, "metrics-report")] == [artifact["artifact_id"]]
    assert len(read_jsonl(project_layout.artifact_index)) == 1


def test_logical_output_alias_dedups_per_type_and_artifact(
    project_layout, tmp_path: Path
):
    source_a = tmp_path / "a.json"
    source_a.write_text('{"v": 1}', encoding="utf-8")
    source_b = tmp_path / "b.json"
    source_b.write_text('{"v": 2}', encoding="utf-8")
    artifact_a = register_artifact(project_layout, source_a, artifact_metadata())
    artifact_b = register_artifact(project_layout, source_b, artifact_metadata())

    bind_artifact_alias(project_layout, "metrics-report", artifact_a["artifact_id"])
    bind_artifact_alias(project_layout, "metrics-report", artifact_a["artifact_id"])
    bind_artifact_alias(project_layout, "metrics-report", artifact_b["artifact_id"])

    aliases = read_jsonl(project_layout.artifact_aliases)
    metrics = [row for row in aliases if row["logical_output_type"] == "metrics-report"]
    assert {row["artifact_id"] for row in metrics} == {
        artifact_a["artifact_id"],
        artifact_b["artifact_id"],
    }
    # The repeated bind of artifact_a dedups to one entry, not a duplicate.
    assert (
        len([row for row in metrics if row["artifact_id"] == artifact_a["artifact_id"]])
        == 1
    )


def test_artifact_registration_reuses_canonical_id_for_same_content_identity(
    project_layout, tmp_path: Path
):
    source = tmp_path / "same.bin"
    source.write_bytes(b"identical")

    first = register_artifact(project_layout, source, artifact_metadata())
    second = register_artifact(project_layout, source, artifact_metadata())

    expected_prefix = f"artifact-{sha256_file(source)[:12]}-"
    pattern = re.compile(r"artifact-[0-9a-f]{12}-[0-9a-f]{8}")
    assert pattern.fullmatch(first["artifact_id"])
    assert pattern.fullmatch(second["artifact_id"])
    assert first["artifact_id"].startswith(expected_prefix)
    assert second == first
    assert len(read_jsonl(project_layout.artifact_index)) == 1
    assert len(list(project_layout.artifact_index.parent.iterdir())) == 2


def test_artifact_registration_does_not_merge_distinct_provenance_identities(
    project_layout, tmp_path: Path
):
    source = tmp_path / "same.bin"
    source.write_bytes(b"identical")

    first = register_artifact(
        project_layout,
        source,
        artifact_metadata(provenance_tags=["fixture-a"]),
    )
    second = register_artifact(
        project_layout,
        source,
        artifact_metadata(provenance_tags=["fixture-b"]),
    )

    assert first["artifact_id"] != second["artifact_id"]
    assert len(read_jsonl(project_layout.artifact_index)) == 2


def test_run_snapshot_verification_preserves_positional_type_classification(
    project_layout, tmp_path: Path
):
    output = tmp_path / "metrics.json"
    output.write_text('{"accuracy":0.9}', encoding="utf-8")
    weight = tmp_path / "model.pt"
    weight.write_bytes(b"weights")
    ordinary = register_artifact(
        project_layout,
        output,
        artifact_metadata(type="metrics", run_id="run-1"),
    )
    model_weight = register_artifact(
        project_layout,
        weight,
        artifact_metadata(type="model-weight", run_id="run-1"),
    )

    verified = verify_run_snapshot_artifacts(
        project_layout,
        [ordinary["artifact_id"], model_weight["artifact_id"]],
        run_id="run-1",
        expected_types=["metrics", "model-weight"],
    )

    assert [record["artifact_id"] for record in verified] == [
        ordinary["artifact_id"],
        model_weight["artifact_id"],
    ]
    assert [record["type"] for record in verified] == [
        "metrics",
        "model-weight",
    ]


def test_run_snapshot_verification_rejects_duplicate_registration_evidence(
    project_layout, tmp_path: Path
):
    output = tmp_path / "metrics.json"
    output.write_text("{}", encoding="utf-8")
    record = register_artifact(
        project_layout,
        output,
        artifact_metadata(type="metrics", run_id="run-1"),
    )

    with pytest.raises(ValueError, match="duplicate"):
        verify_run_snapshot_artifacts(
            project_layout,
            [record["artifact_id"], record["artifact_id"]],
            run_id="run-1",
            expected_types=["metrics", "metrics"],
        )


@pytest.mark.parametrize(
    ("metadata_overrides", "run_id", "expected_type", "message"),
    [
        ({"storage": "external", "run_id": "run-1"}, "run-1", "metrics", "snapshot"),
        ({"run_id": "run-other"}, "run-1", "metrics", "run_id"),
        ({"run_id": "run-1", "type": "predictions"}, "run-1", "metrics", "type"),
    ],
)
def test_run_snapshot_verification_rejects_wrong_classification(
    project_layout,
    tmp_path: Path,
    metadata_overrides: dict,
    run_id: str,
    expected_type: str,
    message: str,
):
    output = tmp_path / "output.bin"
    output.write_bytes(b"output")
    record = register_artifact(
        project_layout,
        output,
        artifact_metadata(**metadata_overrides),
    )

    with pytest.raises(ValueError, match=message):
        verify_run_snapshot_artifacts(
            project_layout,
            [record["artifact_id"]],
            run_id=run_id,
            expected_types=[expected_type],
        )


def test_run_snapshot_verification_rejects_tampered_size_or_hash(
    project_layout, tmp_path: Path
):
    output = tmp_path / "metrics.json"
    output.write_text("{}", encoding="utf-8")
    record = register_artifact(
        project_layout,
        output,
        artifact_metadata(type="metrics", run_id="run-1"),
    )
    Path(record["path"]).write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(ValueError, match="integrity"):
        verify_run_snapshot_artifacts(
            project_layout,
            [record["artifact_id"]],
            run_id="run-1",
            expected_types=["metrics"],
        )


def test_snapshot_records_hash_size_and_full_metadata_defaults(
    project_layout, tmp_path: Path
):
    source = tmp_path / "result.json"
    source.write_bytes(b'{"score":1}')

    record = register_artifact(
        project_layout,
        source,
        artifact_metadata(
            research_id="research-a",
            cycle_id="c1",
            candidate_id="candidate-a",
            run_id="run-a",
            subagent="worker-1",
        ),
    )

    assert record == {
        "artifact_id": record["artifact_id"],
        "path": record["path"],
        "storage": "snapshot",
        "size_bytes": len(b'{"score":1}'),
        "sha256": sha256_file(Path(record["path"])),
        "type": "evidence",
        "producer": "worker-a",
        "subagent": "worker-1",
        "research_id": "research-a",
        "cycle_id": "c1",
        "task_id": "t1",
        "candidate_id": "candidate-a",
        "run_id": "run-a",
        "report_id": None,
        "provenance_tags": ["fixture"],
        "comparability_tags": ["dataset-v1"],
        "parent_artifact_id": None,
        "availability": "available",
        "registered_at": record["registered_at"],
        "unavailable_reason": None,
    }
    assert record["registered_at"].endswith("Z")
    assert Path(record["path"]).parent.name == record["artifact_id"]
    assert Path(record["path"]).read_bytes() == source.read_bytes()


def test_optional_artifact_metadata_has_explicit_defaults(
    project_layout, tmp_path: Path
):
    source = tmp_path / "minimal.txt"
    source.write_text("minimal", encoding="utf-8")

    record = register_artifact(
        project_layout,
        source,
        {"type": "note", "producer": "worker", "storage": "snapshot"},
    )

    assert record["subagent"] is None
    assert record["research_id"] == project_layout.root.resolve().name
    for field in (
        "cycle_id",
        "task_id",
        "candidate_id",
        "run_id",
        "report_id",
        "parent_artifact_id",
        "unavailable_reason",
    ):
        assert record[field] is None
    assert record["provenance_tags"] == []
    assert record["comparability_tags"] == []


def test_report_id_is_optional_metadata_and_is_persisted(
    project_layout, tmp_path: Path
):
    source = tmp_path / "evaluation.json"
    source.write_text('{"report_id":"eval-1"}', encoding="utf-8")

    record = register_artifact(
        project_layout,
        source,
        artifact_metadata(
            type="evaluation-report",
            report_id="eval-1",
        ),
    )

    assert record["report_id"] == "eval-1"
    assert resolve_artifact(project_layout, record["artifact_id"])[
        "report_id"
    ] == "eval-1"


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"type": "evidence", "producer": "worker"}, "storage"),
        (
            {"type": "evidence", "producer": "worker", "storage": "inferred"},
            "storage",
        ),
        ({"producer": "worker", "storage": "snapshot"}, "type"),
        ({"type": "evidence", "storage": "snapshot"}, "producer"),
        (
            {
                "type": "evidence",
                "producer": "worker",
                "storage": "snapshot",
                "unknown": "value",
            },
            "unknown",
        ),
        (
            {
                "type": "evidence",
                "producer": "worker",
                "storage": "snapshot",
                "provenance_tags": ["ok", ""],
            },
            "provenance_tags",
        ),
    ],
)
def test_artifact_metadata_is_validated(
    project_layout, tmp_path: Path, metadata: dict, message: str
):
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        register_artifact(project_layout, source, metadata)


def test_external_local_artifact_retains_canonical_path_and_is_verified(
    project_layout, tmp_path: Path
):
    (tmp_path / "nested").mkdir()
    source = tmp_path / "nested" / ".." / "external.txt"
    source.write_text("external", encoding="utf-8")

    record = register_artifact(
        project_layout,
        source,
        artifact_metadata(storage="external"),
    )

    assert record["path"] == str(source.resolve())
    assert verify_artifact(project_layout, record["artifact_id"])["valid"] is True
    source.write_text("changed", encoding="utf-8")
    assert verify_artifact(project_layout, record["artifact_id"])["valid"] is False


def test_external_local_artifact_becomes_unavailable_without_becoming_invalid(
    project_layout, tmp_path: Path
):
    source = tmp_path / "external.txt"
    source.write_text("external", encoding="utf-8")
    record = register_artifact(
        project_layout,
        source,
        artifact_metadata(storage="external"),
    )
    source.unlink()

    verification = verify_artifact(project_layout, record["artifact_id"])

    assert verification["available"] is False
    assert verification["valid"] is None
    assert len(read_jsonl(project_layout.artifact_index)) == 1


def test_external_file_uri_is_locally_verified(project_layout, tmp_path: Path):
    source = tmp_path / "file-uri.txt"
    source.write_text("file URI", encoding="utf-8")
    uri = source.as_uri()

    record = register_artifact(
        project_layout,
        uri,
        artifact_metadata(storage="external"),
    )

    assert record["path"] == uri
    assert record["sha256"] == sha256_file(source)
    assert record["size_bytes"] == source.stat().st_size
    assert verify_artifact(project_layout, record["artifact_id"])["valid"] is True


def test_external_file_uri_accepts_localhost_authority(
    project_layout, tmp_path: Path
):
    source = tmp_path / "localhost.txt"
    source.write_text("localhost", encoding="utf-8")
    uri = source.as_uri().replace("file:///", "file://localhost/", 1)

    record = register_artifact(
        project_layout,
        uri,
        artifact_metadata(storage="external"),
    )

    assert record["path"] == uri
    assert verify_artifact(project_layout, record["artifact_id"])["valid"] is True


def test_external_file_uri_percent_decodes_and_detects_mutation_and_missing(
    project_layout, tmp_path: Path
):
    source = tmp_path / "evidence #1.txt"
    source.write_text("first", encoding="utf-8")
    uri = source.as_uri()
    assert "%20" in uri and "%23" in uri
    record = register_artifact(
        project_layout,
        uri,
        artifact_metadata(storage="external"),
    )

    source.write_text("mutated", encoding="utf-8")
    assert verify_artifact(project_layout, record["artifact_id"])["valid"] is False

    source.unlink()
    verification = verify_artifact(project_layout, record["artifact_id"])
    assert verification["available"] is False
    assert verification["valid"] is None


def test_external_file_uri_rejects_non_local_authority_without_reading(
    project_layout, monkeypatch: pytest.MonkeyPatch
):
    hash_calls: list[Path] = []

    def observe_hash(path: Path) -> str:
        hash_calls.append(path)
        raise AssertionError("non-local file URI must not be read")

    monkeypatch.setattr(artifacts, "sha256_file", observe_hash)

    with pytest.raises(ValueError, match="file URI authority"):
        register_artifact(
            project_layout,
            "file://research-host/etc/passwd",
            artifact_metadata(storage="external"),
        )

    assert hash_calls == []


def test_external_uri_retains_uri_and_reports_local_verification_unavailable(
    project_layout,
):
    expected_hash = "a" * 64
    record = register_artifact(
        project_layout,
        "s3://research-bucket/evidence.bin",
        artifact_metadata(
            storage="external",
            sha256=expected_hash,
            size_bytes=42,
        ),
    )

    verification = verify_artifact(project_layout, record["artifact_id"])

    assert record["path"] == "s3://research-bucket/evidence.bin"
    assert record["sha256"] == expected_hash
    assert record["size_bytes"] == 42
    assert verification["available"] is False
    assert verification["valid"] is None


def test_external_unavailable_reference_requires_declared_integrity(
    project_layout, tmp_path: Path
):
    with pytest.raises(ValueError, match="sha256.*size_bytes"):
        register_artifact(
            project_layout,
            tmp_path / "missing.bin",
            artifact_metadata(storage="external"),
        )


def test_artifact_version_requires_existing_parent_and_links_it(
    project_layout, tmp_path: Path
):
    first_source = tmp_path / "first.txt"
    first_source.write_text("first", encoding="utf-8")
    first = register_artifact(project_layout, first_source, artifact_metadata())
    second_source = tmp_path / "second.txt"
    second_source.write_text("second", encoding="utf-8")

    second = register_artifact_version(
        project_layout,
        first["artifact_id"],
        second_source,
        artifact_metadata(),
    )

    assert second["parent_artifact_id"] == first["artifact_id"]
    assert len(read_jsonl(project_layout.artifact_index)) == 2
    with pytest.raises(ValueError, match="unknown artifact"):
        register_artifact_version(
            project_layout,
            "artifact-000000000000-00000000",
            second_source,
            artifact_metadata(),
        )


def test_artifact_parent_is_controlled_by_version_interface(
    project_layout, tmp_path: Path
):
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")

    with pytest.raises(ValueError, match="parent_artifact_id"):
        register_artifact(
            project_layout,
            source,
            artifact_metadata(parent_artifact_id="forged"),
        )


def test_append_only_status_resolution_filters_and_unavailable_preserve_snapshot(
    project_layout, tmp_path: Path
):
    first_source = tmp_path / "first.txt"
    first_source.write_text("first", encoding="utf-8")
    first = register_artifact(
        project_layout,
        first_source,
        artifact_metadata(task_id="t1", candidate_id="a", run_id="r1"),
    )
    second_source = tmp_path / "second.txt"
    second_source.write_text("second", encoding="utf-8")
    second = register_artifact(
        project_layout,
        second_source,
        artifact_metadata(
            type="metric", task_id="t2", candidate_id="b", run_id="r2"
        ),
    )
    snapshot = Path(first["path"])

    unavailable = mark_artifact_unavailable(
        project_layout, first["artifact_id"], "archived offline"
    )

    rows = read_jsonl(project_layout.artifact_index)
    assert len(rows) == 3
    assert rows[0] == first
    assert unavailable["availability"] == "unavailable"
    assert unavailable["unavailable_reason"] == "archived offline"
    assert resolve_artifact(project_layout, first["artifact_id"]) == unavailable
    assert snapshot.is_file()
    assert list_artifacts(project_layout, {"task_id": "t1"}) == [unavailable]
    assert list_artifacts(project_layout, {"candidate_id": "b"}) == [second]
    assert list_artifacts(project_layout, {"run_id": "r2"}) == [second]
    assert list_artifacts(project_layout, {"type": "metric"}) == [second]
    assert list_artifacts(project_layout, {}) == [unavailable, second]
    verification = verify_artifact(project_layout, first["artifact_id"])
    assert verification["available"] is False
    assert verification["valid"] is None


def test_artifact_lookup_and_status_inputs_are_validated(
    project_layout, tmp_path: Path
):
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    record = register_artifact(project_layout, source, artifact_metadata())

    with pytest.raises(ValueError, match="unknown artifact"):
        resolve_artifact(project_layout, "missing")
    with pytest.raises(ValueError, match="filter"):
        list_artifacts(project_layout, {"producer": "worker-a"})
    with pytest.raises(ValueError, match="reason"):
        mark_artifact_unavailable(project_layout, record["artifact_id"], "")


def test_artifact_index_is_one_compact_json_object_per_line(
    project_layout, tmp_path: Path
):
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    register_artifact(project_layout, source, artifact_metadata())

    lines = project_layout.artifact_index.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1
    assert ": " not in lines[0]
    assert isinstance(json.loads(lines[0]), dict)


def test_artifact_cli_register_version_resolve_list_verify_and_unavailable(
    project_layout, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(artifact_metadata()), encoding="utf-8"
    )
    first_source = tmp_path / "first.txt"
    first_source.write_text("first", encoding="utf-8")

    assert main(
        [
            "artifact-register",
            "--root",
            str(project_layout.root),
            "--path",
            str(first_source),
            "--metadata",
            str(metadata_path),
        ]
    ) == 0
    first = json.loads(capsys.readouterr().out)

    second_source = tmp_path / "second.txt"
    second_source.write_text("second", encoding="utf-8")
    assert main(
        [
            "artifact-register",
            "--root",
            str(project_layout.root),
            "--path",
            str(second_source),
            "--metadata",
            str(metadata_path),
            "--parent-id",
            first["artifact_id"],
        ]
    ) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["parent_artifact_id"] == first["artifact_id"]

    assert main(
        [
            "artifact-resolve",
            "--root",
            str(project_layout.root),
            "--artifact-id",
            first["artifact_id"],
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == first

    assert main(
        [
            "artifact-list",
            "--root",
            str(project_layout.root),
            "--task-id",
            "t1",
            "--type",
            "evidence",
        ]
    ) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed == {"artifacts": [first, second]}

    assert main(
        [
            "artifact-verify",
            "--root",
            str(project_layout.root),
            "--artifact-id",
            first["artifact_id"],
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    assert main(
        [
            "artifact-unavailable",
            "--root",
            str(project_layout.root),
            "--artifact-id",
            first["artifact_id"],
            "--reason",
            "offline",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["availability"] == "unavailable"


def test_artifact_cli_errors_remain_compact_json(
    capsys: pytest.CaptureFixture[str],
):
    assert main(["artifact-resolve", "--artifact-id", "missing"]) == 1
    captured = capsys.readouterr()
    error = json.loads(captured.out)
    assert captured.err == ""
    assert captured.out == json.dumps(
        error, ensure_ascii=False, separators=(",", ":")
    ) + "\n"
    assert error["status"] == "error"
