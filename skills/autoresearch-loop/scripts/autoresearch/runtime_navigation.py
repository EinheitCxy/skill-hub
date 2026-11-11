"""Audited runtime navigation; command templates are data and never executed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any

from autoresearch.artifacts import (
    register_artifact,
    register_artifact_version,
    resolve_artifact,
    verify_artifact,
)
from autoresearch.contracts import JsonObject
from autoresearch.io import read_json, sha256_file
from autoresearch.project import ProjectLayout


KINDS = frozenset({"entrypoint", "command", "artifact"})
MAX_DOCUMENT_BYTES = 262_144
MAX_SOURCE_BYTES = 262_144


def build_runtime_index(
    layout: ProjectLayout,
    spec: Mapping[str, Any],
    *,
    parent_id: str | None = None,
) -> JsonObject:
    """Snapshot audited source files and register a deterministic runtime index."""
    if not isinstance(spec, Mapping) or set(spec) != {"schema_version", "entries"}:
        raise ValueError(
            "runtime index spec requires exactly schema_version and entries"
        )
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ValueError("runtime index schema_version must be 1")
    entries = _validate_entries(spec["entries"], stored=False)
    # Validate every input before registration, including sources in later entries.
    for entry in entries:
        for source in entry["sources"]:
            _source_path(layout, source)
        for artifact_id in entry["artifact_ids"]:
            _verified_artifact(layout, artifact_id)
    parent = None
    if parent_id is not None:
        parent_record = resolve_artifact(layout, parent_id)
        if (
            parent_record["type"] != "runtime-index"
            or parent_record["storage"] != "snapshot"
        ):
            raise ValueError("parent must be a runtime-index snapshot")
        verification = verify_artifact(layout, parent_id)
        if verification.get("available") is True and verification.get("valid") is True:
            parent = _read_document(layout, parent_id, "runtime-index")
    for entry in entries:
        entry["sources"] = [
            {
                "path": source,
                "artifact_id": _snapshot_source(layout, source)["artifact_id"],
            }
            for source in entry["sources"]
        ]
    document = {
        "schema_version": 1,
        "project_root": str(layout.root.resolve()),
        "entries": entries,
    }
    if parent_id is not None and document == parent:
        return resolve_artifact(layout, parent_id)
    return _snapshot_document(layout, document, "runtime-index", parent_id)


def select_runtime_context(
    layout: ProjectLayout,
    index_id: str,
    query: str,
    *,
    kind: str | None = None,
    limit: int = 5,
    max_chars: int = 6000,
) -> JsonObject:
    """Select a ranked prefix within entry and serialized-character bounds."""
    query = _text(query, "query", 200)
    if kind is not None and kind not in KINDS:
        raise ValueError("kind must be entrypoint, command, or artifact")
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("limit must be an integer from 1 to 10")
    if type(max_chars) is not int or not 100 <= max_chars <= 12_000:
        raise ValueError("character budget must be an integer from 100 to 12000")
    index = _read_document(layout, index_id, "runtime-index")
    terms = list(dict.fromkeys(re.findall(r"\w+", query.casefold())))
    if not terms:
        raise ValueError("query must contain a searchable word")
    ranked = sorted(
        (
            (_score(entry, terms), entry["id"], entry)
            for entry in index["entries"]
            if kind is None or entry["kind"] == kind
        ),
        key=lambda row: (-row[0], row[1]),
    )
    document: JsonObject = {
        "schema_version": 1,
        "project_root": str(layout.root.resolve()),
        "index_artifact_id": index_id,
        "query": query,
        "entries": [],
    }
    for score, _, entry in ranked:
        if score == 0 or len(document["entries"]) == limit:
            break
        candidate = {**document, "entries": [*document["entries"], entry]}
        if len(_serialize(candidate)) > max_chars:
            if not document["entries"]:
                raise ValueError(
                    "first matching entry exceeds character budget; narrow the entry or raise max_chars"
                )
            break
        document = candidate
    if not document["entries"]:
        return {
            "status": "no_matches",
            "matched_entry_ids": [],
            "context_artifact_id": None,
            "input_artifact_ids": [],
            "context_chars": 0,
        }
    dependencies = _verify_entries(layout, document["entries"])
    context = _snapshot_document(layout, document, "runtime-context", index_id)
    return {
        "status": "selected",
        "matched_entry_ids": [entry["id"] for entry in document["entries"]],
        "context_artifact_id": context["artifact_id"],
        "input_artifact_ids": [context["artifact_id"], *dependencies],
        "context_chars": len(_serialize(document)),
    }


def validate_runtime_context(
    layout: ProjectLayout,
    artifact_id: str,
    input_artifact_ids: Sequence[str],
) -> None:
    """Recheck selection at task-open without rewriting existing task records."""
    context = _read_document(layout, artifact_id, "runtime-context")
    _text(context["query"], "query", 200)
    if len(_serialize(context)) > 12_000:
        raise ValueError("runtime context exceeds character budget of 12000")
    index_id = _text(context.get("index_artifact_id"), "index_artifact_id", 200)
    index = _read_document(layout, index_id, "runtime-index")
    indexed = {entry["id"]: entry for entry in index["entries"]}
    if len(context["entries"]) > 10:
        raise ValueError("runtime context must contain at most 10 entries")
    for entry in context["entries"]:
        if entry != indexed.get(entry["id"]):
            raise ValueError(
                "runtime context entries must match their registered index"
            )
    dependencies = _verify_entries(layout, context["entries"])
    if not set(dependencies).issubset(input_artifact_ids):
        raise ValueError(
            "runtime source and linked artifacts must be included in input_artifact_ids"
        )


def _validate_entries(raw_entries: Any, *, stored: bool) -> list[JsonObject]:
    if not isinstance(raw_entries, list) or not 1 <= len(raw_entries) <= 256:
        raise ValueError("runtime index requires 1 to 256 entries")
    entries: list[JsonObject] = []
    ids: set[str] = set()
    required = {"id", "kind", "summary", "sources"}
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ValueError("runtime entry must be an object")
        if not required.issubset(raw) or raw.keys() - (
            required | {"tags", "argv", "artifact_ids"}
        ):
            raise ValueError("runtime entry has missing or unknown fields")
        entry_id = _text(raw["id"], "entry id", 100)
        if entry_id in ids:
            raise ValueError(f"duplicate runtime entry id: {entry_id}")
        ids.add(entry_id)
        kind = raw["kind"]
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("entry kind must be entrypoint, command, or artifact")
        if stored:
            if not isinstance(raw["sources"], list) or len(raw["sources"]) > 32:
                raise ValueError("sources must be a list of at most 32 references")
            sources = []
            for source in raw["sources"]:
                if not isinstance(source, dict) or set(source) != {
                    "path",
                    "artifact_id",
                }:
                    raise ValueError("invalid runtime source reference")
                sources.append(
                    {
                        "path": _text(source["path"], "source path", 1000),
                        "artifact_id": _text(
                            source["artifact_id"], "source artifact_id", 200
                        ),
                    }
                )
            if len({source["path"] for source in sources}) != len(sources):
                raise ValueError("duplicate runtime source path")
        else:
            sources = _strings(raw["sources"], "sources")
        artifact_ids = _strings(raw.get("artifact_ids", []), "artifact_ids")
        if not sources and (kind != "artifact" or not artifact_ids):
            raise ValueError(
                "entry requires source files, or linked artifacts for artifact entries"
            )
        entry: JsonObject = {
            "id": entry_id,
            "kind": kind,
            "summary": _text(raw["summary"], "summary", 500),
            "tags": _strings(raw.get("tags", []), "tags"),
            "sources": sources,
            "artifact_ids": artifact_ids,
        }
        if kind == "command":
            argv = raw.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
                or not argv[0].strip()
            ):
                raise ValueError(
                    "command argv must be a nonempty list of argument strings"
                )
            entry["argv"] = list(argv)
        elif "argv" in raw:
            raise ValueError("only command entries accept argv")
        entries.append(entry)
    return entries


def _source_path(layout: ProjectLayout, source: str) -> Path:
    relative = Path(source)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("source must be a project-relative file path")
    root = layout.root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.is_relative_to(root / ".research"):
        raise ValueError("source must stay inside the project and outside .research")
    if not path.is_file():
        raise FileNotFoundError(f"runtime source file missing: {source}")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"runtime source exceeds {MAX_SOURCE_BYTES} bytes: {source}")
    return path


def _snapshot_source(layout: ProjectLayout, source: str) -> JsonObject:
    path = _source_path(layout, source)
    record = _register_snapshot(layout, path, "runtime-source", None)
    if sha256_file(path) != record["sha256"]:
        raise ValueError(
            f"runtime source changed during indexing: {source}; rebuild index"
        )
    return record


def _verify_entries(layout: ProjectLayout, entries: list[JsonObject]) -> list[str]:
    dependencies: list[str] = []
    for entry in entries:
        for source in entry["sources"]:
            artifact = _verified_artifact(layout, source["artifact_id"])
            if (
                artifact["type"] != "runtime-source"
                or artifact["storage"] != "snapshot"
            ):
                raise ValueError(
                    "runtime source must reference a runtime-source snapshot"
                )
            try:
                path = _source_path(layout, source["path"])
                matches = sha256_file(path) == artifact["sha256"]
            except (OSError, ValueError) as error:
                raise ValueError(
                    f"stale runtime source: {source['path']}; rebuild index"
                ) from error
            if not matches:
                raise ValueError(
                    f"stale runtime source: {source['path']}; rebuild index"
                )
            dependencies.append(artifact["artifact_id"])
        for artifact_id in entry["artifact_ids"]:
            _verified_artifact(layout, artifact_id)
            dependencies.append(artifact_id)
    return list(dict.fromkeys(dependencies))


def _verified_artifact(layout: ProjectLayout, artifact_id: str) -> JsonObject:
    record = resolve_artifact(layout, artifact_id)
    result = verify_artifact(layout, artifact_id)
    if result.get("available") is not True or result.get("valid") is not True:
        raise ValueError(f"runtime artifact is unavailable or invalid: {artifact_id}")
    return record


def _read_document(
    layout: ProjectLayout, artifact_id: str, expected_type: str
) -> JsonObject:
    record = _verified_artifact(layout, artifact_id)
    if (
        record["type"] != expected_type
        or record["storage"] != "snapshot"
        or record["size_bytes"] > MAX_DOCUMENT_BYTES
    ):
        raise ValueError(f"expected bounded {expected_type} snapshot")
    path = Path(record["path"])
    if (
        expected_type == "runtime-context"
        and len(path.read_text(encoding="utf-8")) > 12_000
    ):
        raise ValueError("runtime context file exceeds character budget of 12000")
    document = read_json(path)
    fields = {"schema_version", "project_root", "entries"}
    if expected_type == "runtime-context":
        fields |= {"index_artifact_id", "query"}
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("project_root") != str(layout.root.resolve())
    ):
        raise ValueError("runtime document schema or project root mismatch")
    document["entries"] = _validate_entries(document["entries"], stored=True)
    return document


def _snapshot_document(
    layout: ProjectLayout,
    document: JsonObject,
    artifact_type: str,
    parent_id: str | None,
) -> JsonObject:
    encoded = _serialize(document)
    if len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"{artifact_type} exceeds document size bound")
    with TemporaryDirectory(prefix="runtime-navigation-") as directory:
        path = Path(directory) / f"{artifact_type}.json"
        path.write_text(encoded, encoding="utf-8")
        return _register_snapshot(layout, path, artifact_type, parent_id)


def _register_snapshot(
    layout: ProjectLayout, path: Path, artifact_type: str, parent_id: str | None
) -> JsonObject:
    metadata = {
        "type": artifact_type,
        "producer": "runtime-navigation",
        "storage": "snapshot",
    }
    record = (
        register_artifact(layout, path, metadata)
        if parent_id is None
        else register_artifact_version(layout, parent_id, path, metadata)
    )
    # Canonical dedup can return a previously unavailable, missing or corrupt
    # snapshot. Explicit rebuild/selection creates a child instead of overwriting
    # history; subsequent calls walk/reuse that same verified child version.
    while True:
        verification = verify_artifact(layout, record["artifact_id"])
        if verification.get("available") is True and verification.get("valid") is True:
            return record
        record = register_artifact_version(
            layout, record["artifact_id"], path, metadata
        )


def _score(entry: JsonObject, terms: list[str]) -> int:
    names = " ".join([entry["id"], *entry["tags"]]).casefold()
    detail = " ".join(
        [
            entry["summary"],
            *(source["path"] for source in entry["sources"]),
            *entry.get("argv", []),
        ]
    ).casefold()
    return sum(3 * (term in names) + (term in detail) for term in terms)


def _serialize(document: JsonObject) -> str:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _text(value: Any, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ValueError(
            f"{field} must be nonempty text of at most {maximum} characters"
        )
    return value


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError(f"{field} must be a list of at most 32 strings")
    result = [_text(item, field, 1000) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicates")
    return result
