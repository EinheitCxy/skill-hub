from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from autoresearch.contracts import JsonObject
from autoresearch.io import append_jsonl_locked, read_json, read_jsonl, sha256_file
from autoresearch.project import ProjectLayout, load_research_state


REQUIRED_METADATA = frozenset({"type", "producer", "storage"})
OPTIONAL_METADATA = frozenset(
    {
        "subagent",
        "research_id",
        "cycle_id",
        "task_id",
        "candidate_id",
        "run_id",
        "evaluation_cycle_checkpoint_id",
        "report_id",
        "provenance_tags",
        "comparability_tags",
        "sha256",
        "size_bytes",
    }
)
FILTER_FIELDS = frozenset({"task_id", "candidate_id", "run_id", "type"})
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
CANONICAL_IDENTITY_FIELDS = (
    "type",
    "storage",
    "producer",
    "subagent",
    "research_id",
    "cycle_id",
    "task_id",
    "candidate_id",
    "run_id",
    "evaluation_cycle_checkpoint_id",
    "report_id",
    "provenance_tags",
    "comparability_tags",
    "parent_artifact_id",
)


def register_artifact(
    layout: ProjectLayout,
    path: Path | str,
    metadata: Mapping[str, Any],
) -> JsonObject:
    return _register(layout, path, metadata, parent_artifact_id=None)


def register_artifact_version(
    layout: ProjectLayout,
    parent_id: str,
    path: Path | str,
    metadata: Mapping[str, Any],
) -> JsonObject:
    resolve_artifact(layout, parent_id)
    return _register(layout, path, metadata, parent_artifact_id=parent_id)


def resolve_artifact(
    layout: ProjectLayout, artifact_id: str
) -> JsonObject:
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("artifact_id must be a non-empty string")
    matching = [
        row
        for row in read_jsonl(layout.artifact_index)
        if row.get("artifact_id") == artifact_id
    ]
    if not matching:
        raise ValueError(f"unknown artifact: {artifact_id}")
    return dict(matching[-1])


def list_artifacts(
    layout: ProjectLayout, filters: Mapping[str, str]
) -> list[JsonObject]:
    if not isinstance(filters, Mapping):
        raise ValueError("artifact filters must be an object")
    unknown = filters.keys() - FILTER_FIELDS
    if unknown:
        raise ValueError(f"unknown artifact filter: {sorted(unknown)[0]}")
    for field, value in filters.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"artifact filter {field} must be a non-empty string")

    latest: dict[str, JsonObject] = {}
    for row in read_jsonl(layout.artifact_index):
        artifact_id = row.get("artifact_id")
        if isinstance(artifact_id, str):
            latest[artifact_id] = dict(row)
    return [
        row
        for row in latest.values()
        if all(row.get(field) == value for field, value in filters.items())
    ]


def bind_artifact_alias(
    layout: ProjectLayout, logical_output_type: str, artifact_id: str
) -> JsonObject:
    """Bind an additional logical output without duplicating artifact bytes."""
    output_type = _nonempty_string(logical_output_type, "logical_output_type")
    artifact = resolve_artifact(layout, artifact_id)
    # Dedup and append under one alias lock so two concurrent binds of the
    # same logical_output_type cannot both pass the existence check and then
    # both append, bypassing dedup.
    with _alias_lock(layout):
        existing = [
            row
            for row in _read_artifact_aliases(layout)
            if row.get("logical_output_type") == output_type
            and row.get("artifact_id") == artifact["artifact_id"]
        ]
        if existing:
            if existing[-1].get("artifact_sha256") != artifact["sha256"]:
                raise ValueError("artifact logical output alias hash conflicts")
            return existing[-1]
        receipt = {
            "schema_version": 1,
            "record_type": "artifact-logical-output-alias",
            "logical_output_type": output_type,
            "artifact_id": artifact["artifact_id"],
            "artifact_sha256": artifact["sha256"],
            "bound_at": _timestamp(),
        }
        append_jsonl_locked(layout.artifact_aliases, receipt)
    return receipt


@contextmanager
def _alias_lock(layout: ProjectLayout) -> Iterator[None]:
    lock_path = layout.root / ".research" / "artifact-aliases.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def resolve_artifact_alias(
    layout: ProjectLayout, logical_output_type: str
) -> list[JsonObject]:
    output_type = _nonempty_string(logical_output_type, "logical_output_type")
    artifacts: list[JsonObject] = []
    for row in _read_artifact_aliases(layout):
        if row.get("logical_output_type") != output_type:
            continue
        artifact_id = row.get("artifact_id")
        if not isinstance(artifact_id, str):
            raise ValueError("artifact logical output alias is invalid")
        artifact = resolve_artifact(layout, artifact_id)
        if artifact.get("sha256") != row.get("artifact_sha256"):
            raise ValueError("artifact logical output alias hash conflicts")
        artifacts.append(artifact)
    return artifacts


def _read_artifact_aliases(layout: ProjectLayout) -> list[JsonObject]:
    if not layout.artifact_aliases.is_file():
        return []
    return read_jsonl(layout.artifact_aliases)


def verify_artifact(
    layout: ProjectLayout, artifact_id: str
) -> JsonObject:
    record = resolve_artifact(layout, artifact_id)
    if record["availability"] == "unavailable":
        return _verification(
            record,
            available=False,
            valid=None,
            reason=record.get("unavailable_reason")
            or "artifact is not locally available",
        )

    local_path = _locally_verifiable_path(record["path"])
    if local_path is None:
        return _verification(
            record,
            available=False,
            valid=None,
            reason="external reference is not locally verifiable",
        )
    if not local_path.is_file():
        if record["storage"] == "external":
            return _verification(
                record,
                available=False,
                valid=None,
                reason="external artifact is not locally available",
            )
        return _verification(
            record,
            available=False,
            valid=False,
            reason="snapshot is missing",
        )

    actual_hash = sha256_file(local_path)
    actual_size = local_path.stat().st_size
    valid = (
        actual_hash == record["sha256"]
        and actual_size == record["size_bytes"]
    )
    return _verification(
        record,
        available=True,
        valid=valid,
        reason=None if valid else "artifact integrity mismatch",
        actual_hash=actual_hash,
        actual_size=actual_size,
    )


def verify_run_snapshot_artifacts(
    layout: ProjectLayout,
    artifact_ids: Sequence[str],
    *,
    run_id: str,
    expected_types: Sequence[str],
) -> list[JsonObject]:
    normalized_run_id = _nonempty_string(run_id, "run_id")
    normalized_ids = _nonempty_string_sequence(
        artifact_ids, "artifact_ids"
    )
    normalized_types = _nonempty_string_sequence(
        expected_types, "expected_types"
    )
    if len(normalized_ids) != len(normalized_types):
        raise ValueError(
            "artifact_ids and expected_types must have equal cardinality"
        )
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError("artifact_ids contain duplicate accounting evidence")

    verified: list[JsonObject] = []
    for artifact_id, expected_type in zip(
        normalized_ids, normalized_types, strict=True
    ):
        record = resolve_artifact(layout, artifact_id)
        if record.get("storage") != "snapshot":
            raise ValueError(
                f"run artifact must be a snapshot: {artifact_id}"
            )
        if record.get("run_id") != normalized_run_id:
            raise ValueError(
                f"run artifact run_id does not match: {artifact_id}"
            )
        if record.get("type") != expected_type:
            raise ValueError(
                f"run artifact type does not match: {artifact_id}"
            )
        integrity = verify_artifact(layout, artifact_id)
        if (
            integrity.get("available") is not True
            or integrity.get("valid") is not True
        ):
            raise ValueError(
                f"run artifact integrity verification failed: {artifact_id}"
            )
        verified.append(record)
    return verified


def mark_artifact_unavailable(
    layout: ProjectLayout, artifact_id: str, reason: str
) -> JsonObject:
    if not isinstance(reason, str) or not reason:
        raise ValueError("unavailable reason must be a non-empty string")
    record = resolve_artifact(layout, artifact_id)
    unavailable = dict(record)
    unavailable["availability"] = "unavailable"
    unavailable["unavailable_reason"] = reason
    append_jsonl_locked(layout.artifact_index, unavailable)
    return unavailable


def _register(
    layout: ProjectLayout,
    path: Path | str,
    metadata: Mapping[str, Any],
    *,
    parent_artifact_id: str | None,
) -> JsonObject:
    normalized = _normalize_metadata(layout, metadata)
    reference, local_path = _normalize_reference(path)
    declared_hash = normalized.pop("sha256")
    declared_size = normalized.pop("size_bytes")
    storage = normalized["storage"]

    if storage == "snapshot":
        if local_path is None or not local_path.is_file():
            raise ValueError("snapshot source must be an existing local file")
        content_hash = sha256_file(local_path)
        size_bytes = local_path.stat().st_size
        _check_declared_integrity(
            declared_hash, declared_size, content_hash, size_bytes
        )
        canonical = _canonical_artifact(
            layout, content_hash, normalized, parent_artifact_id
        )
        if canonical is not None:
            return canonical
        artifact_id = _new_artifact_id(layout, content_hash)
        destination_dir = layout.artifact_index.parent / artifact_id
        destination_dir.mkdir()
        destination = destination_dir / local_path.name
        shutil.copyfile(local_path, destination)
        if (
            sha256_file(destination) != content_hash
            or destination.stat().st_size != size_bytes
        ):
            raise OSError("snapshot changed while it was copied")
        stored_path = str(destination.resolve())
        availability = "available"
    else:
        if local_path is not None and local_path.is_file():
            content_hash = sha256_file(local_path)
            size_bytes = local_path.stat().st_size
            _check_declared_integrity(
                declared_hash, declared_size, content_hash, size_bytes
            )
            availability = "available"
        else:
            if declared_hash is None or declared_size is None:
                raise ValueError(
                    "unavailable external reference requires sha256 and size_bytes"
                )
            content_hash = declared_hash
            size_bytes = declared_size
            availability = "unavailable"
        canonical = _canonical_artifact(
            layout, content_hash, normalized, parent_artifact_id
        )
        if canonical is not None:
            return canonical
        artifact_id = _new_artifact_id(layout, content_hash)
        stored_path = reference

    record: JsonObject = {
        "artifact_id": artifact_id,
        "path": stored_path,
        "storage": storage,
        "size_bytes": size_bytes,
        "sha256": content_hash,
        "type": normalized["type"],
        "producer": normalized["producer"],
        "subagent": normalized["subagent"],
        "research_id": normalized["research_id"],
        "cycle_id": normalized["cycle_id"],
        "task_id": normalized["task_id"],
        "candidate_id": normalized["candidate_id"],
        "run_id": normalized["run_id"],
        "report_id": normalized["report_id"],
        "provenance_tags": normalized["provenance_tags"],
        "comparability_tags": normalized["comparability_tags"],
        "parent_artifact_id": parent_artifact_id,
        "availability": availability,
        "registered_at": _timestamp(),
        "unavailable_reason": None,
    }
    if normalized["evaluation_cycle_checkpoint_id"] is not None:
        record["evaluation_cycle_checkpoint_id"] = normalized[
            "evaluation_cycle_checkpoint_id"
        ]
    append_jsonl_locked(layout.artifact_index, record)
    return record


def _canonical_artifact(
    layout: ProjectLayout,
    content_hash: str,
    metadata: Mapping[str, Any],
    parent_artifact_id: str | None,
) -> JsonObject | None:
    identity = dict(metadata)
    identity["sha256"] = content_hash
    identity["parent_artifact_id"] = parent_artifact_id
    for row in read_jsonl(layout.artifact_index):
        if all(
            row.get(field) == identity[field]
            for field in CANONICAL_IDENTITY_FIELDS
        ) and row.get("sha256") == content_hash:
            return resolve_artifact(layout, row["artifact_id"])
    return None


def _normalize_metadata(
    layout: ProjectLayout, metadata: Mapping[str, Any]
) -> JsonObject:
    if not isinstance(metadata, Mapping):
        raise ValueError("artifact metadata must be an object")
    missing = REQUIRED_METADATA - metadata.keys()
    unknown = metadata.keys() - REQUIRED_METADATA - OPTIONAL_METADATA
    if missing:
        raise ValueError(
            f"artifact metadata missing required field: {sorted(missing)[0]}"
        )
    if unknown:
        raise ValueError(
            f"artifact metadata has unknown field: {sorted(unknown)[0]}"
        )

    artifact_type = _nonempty_string(metadata["type"], "type")
    producer = _nonempty_string(metadata["producer"], "producer")
    storage = metadata["storage"]
    if storage not in {"snapshot", "external"}:
        raise ValueError("storage must be snapshot or external")

    state = load_research_state(layout)
    research_id = metadata.get("research_id", state.get("research_id"))
    normalized: JsonObject = {
        "type": artifact_type,
        "producer": producer,
        "storage": storage,
        "subagent": _nullable_string(metadata.get("subagent"), "subagent"),
        "research_id": _nullable_string(research_id, "research_id"),
        "cycle_id": _nullable_string(metadata.get("cycle_id"), "cycle_id"),
        "task_id": _nullable_string(metadata.get("task_id"), "task_id"),
        "candidate_id": _nullable_string(
            metadata.get("candidate_id"), "candidate_id"
        ),
        "run_id": _nullable_string(metadata.get("run_id"), "run_id"),
        "evaluation_cycle_checkpoint_id": _nullable_string(
            metadata.get("evaluation_cycle_checkpoint_id"),
            "evaluation_cycle_checkpoint_id",
        ),
        "report_id": _nullable_string(
            metadata.get("report_id"), "report_id"
        ),
        "provenance_tags": _string_list(
            metadata.get("provenance_tags", []), "provenance_tags"
        ),
        "comparability_tags": _string_list(
            metadata.get("comparability_tags", []), "comparability_tags"
        ),
        "sha256": _declared_hash(metadata.get("sha256")),
        "size_bytes": _declared_size(metadata.get("size_bytes")),
    }
    return normalized


def _normalize_reference(path: Path | str) -> tuple[str, Path | None]:
    if isinstance(path, Path):
        local_path = path.resolve()
        return str(local_path), local_path
    if not isinstance(path, str) or not path:
        raise ValueError("artifact path must be a path or non-empty URI")
    if _is_uri(path):
        if urlsplit(path).scheme.lower() == "file":
            return path, _local_file_uri_path(path, reject_nonlocal=True)
        return path, None
    local_path = Path(path).resolve()
    return str(local_path), local_path


def _is_uri(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(parsed.scheme and "://" in value)


def _locally_verifiable_path(reference: str) -> Path | None:
    if _is_uri(reference):
        if urlsplit(reference).scheme.lower() == "file":
            return _local_file_uri_path(reference, reject_nonlocal=False)
        return None
    return Path(reference)


def _local_file_uri_path(
    reference: str, *, reject_nonlocal: bool
) -> Path | None:
    parsed = urlsplit(reference)
    if parsed.netloc.lower() not in {"", "localhost"}:
        if reject_nonlocal:
            raise ValueError(
                "file URI authority must be empty or localhost"
            )
        return None
    if parsed.query or parsed.fragment:
        raise ValueError("file URI must not contain a query or fragment")
    decoded_path = unquote(parsed.path, encoding="utf-8", errors="strict")
    return Path(decoded_path).resolve()


def _new_artifact_id(layout: ProjectLayout, content_hash: str) -> str:
    existing = {
        row.get("artifact_id") for row in read_jsonl(layout.artifact_index)
    }
    while True:
        artifact_id = f"artifact-{content_hash[:12]}-{uuid4().hex[:8]}"
        if artifact_id not in existing:
            return artifact_id


def _check_declared_integrity(
    declared_hash: str | None,
    declared_size: int | None,
    actual_hash: str,
    actual_size: int,
) -> None:
    if declared_hash is not None and declared_hash != actual_hash:
        raise ValueError("declared sha256 does not match artifact")
    if declared_size is not None and declared_size != actual_size:
        raise ValueError("declared size_bytes does not match artifact")


def _declared_hash(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("sha256 must be a lowercase 64-character digest")
    return value


def _declared_size(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("size_bytes must be a non-negative integer")
    return value


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _nullable_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field)


def _nonempty_string_sequence(
    value: Sequence[str], field: str
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a sequence")
    return [
        _nonempty_string(item, f"{field} item")
        for item in value
    ]


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return list(value)


def _verification(
    record: Mapping[str, Any],
    *,
    available: bool,
    valid: bool | None,
    reason: str | None,
    actual_hash: str | None = None,
    actual_size: int | None = None,
) -> JsonObject:
    return {
        "artifact_id": record["artifact_id"],
        "available": available,
        "valid": valid,
        "reason": reason,
        "expected_sha256": record["sha256"],
        "actual_sha256": actual_hash,
        "expected_size_bytes": record["size_bytes"],
        "actual_size_bytes": actual_size,
    }


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
