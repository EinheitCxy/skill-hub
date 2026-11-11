import fcntl
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

from autoresearch.contracts import JsonObject


def read_json(path: Path) -> JsonObject:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as stream:
        json.dump(dict(value), stream, ensure_ascii=False, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
        temporary_path = Path(stream.name)
    os.replace(temporary_path, path)


def append_jsonl_locked(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            line = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")) + "\n"
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_jsonl(path: Path) -> list[JsonObject]:
    rows: list[JsonObject] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("JSONL row must be an object")
            rows.append(row)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_within(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"{path} escapes research root {root}")
    return resolved_path
