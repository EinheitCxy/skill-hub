from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path

import pytest

from autoresearch import io
from autoresearch.io import (
    append_jsonl_locked,
    atomic_write_json,
    ensure_within,
    read_json,
    read_jsonl,
    sha256_file,
)


def test_atomic_write_replaces_complete_document(tmp_path: Path):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"revision": 1})
    atomic_write_json(target, {"revision": 2})
    assert read_json(target) == {"revision": 2}
    assert not list(tmp_path.glob("*.tmp"))


def test_append_jsonl_keeps_one_object_per_line(tmp_path: Path):
    target = tmp_path / "journal.jsonl"
    append_jsonl_locked(target, {"id": "a"})
    append_jsonl_locked(target, {"id": "b"})
    assert read_jsonl(target) == [{"id": "a"}, {"id": "b"}]


def test_append_jsonl_serializes_while_exclusive_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    lock_is_held = False
    serialization_lock_states: list[bool] = []
    original_dumps = json.dumps

    def record_flock(_file_descriptor: int, operation: int) -> None:
        nonlocal lock_is_held
        if operation == io.fcntl.LOCK_EX:
            lock_is_held = True
        elif operation == io.fcntl.LOCK_UN:
            lock_is_held = False

    def record_dumps(*args, **kwargs) -> str:
        serialization_lock_states.append(lock_is_held)
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(io.fcntl, "flock", record_flock)
    monkeypatch.setattr(io.json, "dumps", record_dumps)

    append_jsonl_locked(tmp_path / "journal.jsonl", {"id": "a"})

    assert serialization_lock_states == [True]


def append_row(args):
    path, row_id = args
    append_jsonl_locked(path, {"id": row_id})


def test_parallel_processes_keep_jsonl_complete(tmp_path: Path):
    target = tmp_path / "parallel.jsonl"
    with ProcessPoolExecutor(max_workers=4) as pool:
        list(pool.map(append_row, [(target, str(index)) for index in range(20)]))
    rows = read_jsonl(target)
    assert len(rows) == 20
    assert {row["id"] for row in rows} == {str(index) for index in range(20)}


def test_ensure_within_rejects_escape(tmp_path: Path):
    with pytest.raises(ValueError, match="escapes research root"):
        ensure_within(tmp_path / "root", tmp_path / "outside")


def test_sha256_file_is_stable(tmp_path: Path):
    target = tmp_path / "artifact.txt"
    target.write_text("evidence", encoding="utf-8")
    assert sha256_file(target) == sha256_file(target)
