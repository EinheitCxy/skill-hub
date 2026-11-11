from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
from typing import Any

from autoresearch.artifacts import (
    register_artifact,
    resolve_artifact,
    verify_artifact,
    verify_run_snapshot_artifacts,
)
from autoresearch.candidates import load_candidates
from autoresearch.contracts import EnvironmentSpec, JsonObject, RunSpec
from autoresearch.governance import (
    detach_budget_reservation,
    read_approved_gate,
    require_execution_approval,
    reserve_budget,
    settle_budget,
)
from autoresearch.io import (
    atomic_write_json,
    ensure_within,
    read_json,
    read_jsonl,
    sha256_file,
)
from autoresearch.project import ProjectLayout, load_research_state
from autoresearch.workflow import (
    resolve_evaluation_cycle_checkpoint,
    validate_active_evaluation_spec,
)


BACKEND_NAMES = frozenset({"uv-local", "command-local"})
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class CommandLocalExecutor:
    def __init__(self, layout: ProjectLayout):
        self.layout = layout

    def inspect(self, spec: EnvironmentSpec) -> JsonObject:
        return _inspect_known_environment(self.layout, spec)

    def prepare(self, spec: EnvironmentSpec) -> JsonObject:
        return _prepare_known_environment(self.layout, spec)

    def run(self, spec: RunSpec) -> JsonObject:
        return execute_run(self.layout, spec)

    def cancel(self, run_id: str) -> JsonObject:
        return cancel_run(self.layout, run_id)

    def collect(self, run_id: str) -> JsonObject:
        return collect_run(self.layout, run_id)

    def cleanup(self, run_id: str) -> JsonObject:
        return cleanup_run(self.layout, run_id)


class UvLocalExecutor(CommandLocalExecutor):
    pass


BACKEND_REGISTRY = {
    "command-local": CommandLocalExecutor,
    "uv-local": UvLocalExecutor,
}


def inspect_environment(
    layout: ProjectLayout, spec: EnvironmentSpec
) -> JsonObject:
    adapter = _adapter(layout, spec.backend)
    if adapter is None:
        return _unsupported_backend(spec.backend)
    return adapter.inspect(spec)


def prepare_environment(
    layout: ProjectLayout, spec: EnvironmentSpec
) -> JsonObject:
    adapter = _adapter(layout, spec.backend)
    if adapter is None:
        return _unsupported_backend(spec.backend)
    require_execution_approval(layout)
    return adapter.prepare(spec)


def execute_run(layout: ProjectLayout, spec: RunSpec) -> JsonObject:
    require_execution_approval(layout)
    validated = _validate_run_spec(layout, spec)
    run_root = _run_root(layout, spec.run_id)
    run_root.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(run_root / "run.lock"):
        state_path = run_root / "state.json"
        if state_path.is_file():
            state = read_json(state_path)
            _require_same_run_spec(state, validated)
        else:
            reservation_id = f"run:{spec.run_id}"
            reserve_budget(
                layout,
                {
                    "id": reservation_id,
                    **validated["resource_limits"],
                },
            )
            state = {
                "schema_version": 1,
                "record_type": "run-state",
                "run_id": spec.run_id,
                "spec": validated["spec"],
                "spec_hash": validated["spec_hash"],
                "reservation_id": reservation_id,
                "candidate_id": spec.candidate_id,
                "evaluation_spec_id": spec.evaluation_spec_id,
                "evaluation_cycle_checkpoint_id": (
                    spec.evaluation_cycle_checkpoint_id
                ),
                "started_at": _timestamp(),
                "status": "reserved",
                "phase": "reserved",
            }
            atomic_write_json(state_path, state)
        return _resume_execution_locked(
            layout, spec, validated, state, state_path
        )


def cancel_run(layout: ProjectLayout, run_id: str) -> JsonObject:
    require_execution_approval(layout)
    run_root = _run_root(layout, run_id)
    process_path = run_root / "process.json"
    if not process_path.is_file():
        raise ValueError(f"run process receipt is missing: {run_id}")
    process = read_json(process_path)
    if process.get("run_id") != run_id:
        raise ValueError("run process receipt identity conflicts")
    process_group_id = process.get("process_group_id")
    pid = process.get("pid")
    if (
        isinstance(process_group_id, bool)
        or not isinstance(process_group_id, int)
        or process_group_id <= 0
        or isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
    ):
        raise ValueError("run process identity is invalid")
    recorded_start_identity = process.get("process_start_identity")
    if (
        not isinstance(recorded_start_identity, str)
        or not recorded_start_identity
    ):
        raise ValueError("run process start identity is invalid")
    live_start_identity = _process_start_identity(pid)
    if (
        not live_start_identity.endswith(":unavailable")
        and live_start_identity != recorded_start_identity
    ):
        raise ValueError("run process start identity conflicts")
    request = {
        "schema_version": 1,
        "record_type": "run-cancellation",
        "run_id": run_id,
        "process_group_id": process_group_id,
        "process_start_identity": recorded_start_identity,
        "requested_at": _timestamp(),
        "status": "cancelled",
    }
    atomic_write_json(run_root / "cancel-request.json", request)
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    return {
        "run_id": run_id,
        "status": "cancelled",
        "process_group_id": process_group_id,
    }


def collect_run(layout: ProjectLayout, run_id: str) -> JsonObject:
    require_execution_approval(layout)
    run_root = _run_root(layout, run_id)
    state_path = run_root / "state.json"
    if not state_path.is_file():
        raise ValueError(f"unknown run: {run_id}")
    with _exclusive_lock(run_root / "run.lock"):
        state = read_json(state_path)
        if state.get("run_id") != run_id:
            raise ValueError("run state identity conflicts")
        phase = state.get("phase")
        if phase not in {
            "artifacts-registered",
            "settlement-prepared",
            "terminal",
        }:
            raise ValueError("run artifacts are not ready for collection")
        if phase == "terminal":
            return _verified_collection_result(layout, state)
        return _collect_and_settle_locked(layout, state, state_path)


def cleanup_run(layout: ProjectLayout, run_id: str) -> JsonObject:
    require_execution_approval(layout)
    modification = _active_modification_payload(layout)
    allowed_actions = modification.get("allowed_actions")
    if (
        not isinstance(allowed_actions, list)
        or "cleanup-temporary" not in allowed_actions
    ):
        raise PermissionError("cleanup-temporary action is not approved")

    run_root = _run_root(layout, run_id)
    environment_path = run_root / "environment.json"
    if not environment_path.is_file():
        raise ValueError(f"run environment receipt is missing: {run_id}")
    environment = read_json(environment_path)
    environment_id = environment.get("environment_id")
    if not isinstance(environment_id, str):
        raise ValueError("run environment identity is invalid")
    receipt_artifact_id = environment.get(
        "environment_receipt_artifact_id"
    )
    if not isinstance(receipt_artifact_id, str):
        raise ValueError("run environment artifact identity is invalid")
    receipt_artifact = resolve_artifact(layout, receipt_artifact_id)
    receipt_verification = verify_artifact(layout, receipt_artifact_id)
    if (
        receipt_artifact.get("type") != "environment-receipt"
        or receipt_verification.get("available") is not True
        or receipt_verification.get("valid") is not True
    ):
        raise ValueError("run-bound environment receipt is invalid")
    spec = read_json(Path(receipt_artifact["path"]))
    if (
        spec.get("environment_id") != environment_id
        or spec.get("dependency_lock_hash")
        != environment.get("dependency_lock_hash")
    ):
        raise ValueError("run-bound environment receipt conflicts")
    allowed_write_paths = _path_sequence(
        spec.get("allowed_write_paths"), "allowed_write_paths"
    )
    temporary_paths = _path_sequence(
        spec.get("temporary_paths"), "temporary_paths"
    )
    registered_paths = {
        Path(row["path"]).resolve()
        for row in read_jsonl(layout.artifact_index)
        if isinstance(row.get("path"), str)
        and "://" not in row["path"]
    }
    removed: list[str] = []
    for temporary in temporary_paths:
        safe_temporary = ensure_within(layout.root, temporary)
        if safe_temporary == layout.root.resolve():
            raise ValueError("research root cannot be a temporary path")
        if not _within_any(safe_temporary, allowed_write_paths):
            raise ValueError("temporary path is outside allowed write paths")
        if any(
            artifact == safe_temporary
            or artifact.is_relative_to(safe_temporary)
            for artifact in registered_paths
        ):
            raise ValueError("temporary path contains registered artifacts")
        if safe_temporary.is_dir():
            shutil.rmtree(safe_temporary)
            removed.append(str(safe_temporary))
        elif safe_temporary.exists():
            safe_temporary.unlink()
            removed.append(str(safe_temporary))
    result = {
        "run_id": run_id,
        "status": "cleaned",
        "removed_paths": removed,
    }
    atomic_write_json(run_root / "cleanup.json", result)
    return result


def _resume_execution_locked(
    layout: ProjectLayout,
    spec: RunSpec,
    validated: Mapping[str, Any],
    initial_state: Mapping[str, Any],
    state_path: Path,
) -> JsonObject:
    state = dict(initial_state)
    process: subprocess.Popen[str] | None = None
    if state.get("phase") == "terminal":
        return _terminal_result(state)
    if state.get("phase") == "reserved":
        state = {**state, "status": "starting", "phase": "spawned"}
        atomic_write_json(state_path, state)
    if state.get("phase") == "spawned":
        process, state = _spawn_owned_process(
            layout, spec, validated, state, state_path
        )
    if state.get("phase") == "pid-persisted":
        if process is None:
            state = _recover_interrupted_process(layout, state, state_path)
        else:
            state = _communicate_owned_process(
                layout, spec, state, state_path, process
            )
    if state.get("phase") == "communicated":
        result = _register_terminal_run_artifacts(
            layout,
            spec,
            state,
            _run_root(layout, spec.run_id) / "stdout.txt",
            _run_root(layout, spec.run_id) / "stderr.txt",
        )
        state = {
            **state,
            "phase": "artifacts-registered",
            "result": result,
            "artifact_ids": [
                result["receipt_artifact_id"],
                result["stdout_artifact_id"],
                result["stderr_artifact_id"],
            ],
            "artifact_types": [
                "command-receipt",
                "command-stdout",
                "command-stderr",
            ],
        }
        atomic_write_json(state_path, state)
    if state.get("phase") == "artifacts-registered":
        if spec.output_paths:
            return _terminal_result(state)
        _collect_and_settle_locked(layout, state, state_path)
        return _terminal_result(read_json(state_path))
    if state.get("phase") == "settlement-prepared":
        state = _settle_run_locked(layout, state, state_path)
    if state.get("phase") != "terminal":
        raise ValueError(f"unknown durable run phase: {state.get('phase')}")
    return _terminal_result(state)


def _spawn_owned_process(
    layout: ProjectLayout,
    spec: RunSpec,
    validated: Mapping[str, Any],
    state: Mapping[str, Any],
    state_path: Path,
) -> tuple[subprocess.Popen[str], JsonObject]:
    process = subprocess.Popen(
        list(spec.argv),
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(spec.cwd.resolve()),
        start_new_session=True,
    )
    process_group_id = os.getpgid(process.pid)
    run_root = _run_root(layout, spec.run_id)
    atomic_write_json(
        run_root / "process.json",
        {
            "schema_version": 1,
            "record_type": "run-process",
            "run_id": spec.run_id,
            "pid": process.pid,
            "process_group_id": process_group_id,
            "process_start_identity": _process_start_identity(process.pid),
            "persisted_at": _timestamp(),
        },
    )
    atomic_write_json(
        run_root / "command.json",
        {
            "schema_version": 1,
            "record_type": "run-command",
            "run_id": spec.run_id,
            "argv": list(spec.argv),
            "cwd": str(spec.cwd.resolve()),
            "backend": spec.backend,
            "candidate_id": spec.candidate_id,
            "evaluation_spec_id": spec.evaluation_spec_id,
            "evaluation_cycle_checkpoint_id": (
                spec.evaluation_cycle_checkpoint_id
            ),
            "reservation_id": state["reservation_id"],
        },
    )
    atomic_write_json(
        run_root / "environment.json",
        {
            "schema_version": 1,
            "record_type": "run-environment",
            "run_id": spec.run_id,
            "environment_id": spec.environment_id,
            "backend": spec.backend,
            "dependency_lock_hash": spec.dependency_lock_hash,
            "environment_receipt_artifact_id": validated[
                "environment_receipt_artifact_id"
            ],
            "evaluation_cycle_checkpoint_id": (
                spec.evaluation_cycle_checkpoint_id
            ),
        },
    )
    running_state = {
        **state,
        "status": "running",
        "phase": "pid-persisted",
        "pid": process.pid,
        "process_group_id": process_group_id,
    }
    atomic_write_json(state_path, running_state)
    return process, running_state


def _communicate_owned_process(
    layout: ProjectLayout,
    spec: RunSpec,
    state: Mapping[str, Any],
    state_path: Path,
    process: subprocess.Popen[str],
) -> JsonObject:
    terminal_status = "completed"
    try:
        stdout, stderr = process.communicate(timeout=spec.timeout_seconds)
        returncode = process.returncode
        if returncode != 0:
            terminal_status = "failed"
    except subprocess.TimeoutExpired as error:
        terminal_status = "timeout"
        os.killpg(state["process_group_id"], signal.SIGTERM)
        try:
            drained_stdout, drained_stderr = process.communicate(
                timeout=spec.timeout_seconds
            )
        except subprocess.TimeoutExpired:
            os.killpg(state["process_group_id"], signal.SIGKILL)
            drained_stdout, drained_stderr = process.communicate(
                timeout=spec.timeout_seconds
            )
        stdout = _partial_text(error.output) + drained_stdout
        stderr = _partial_text(error.stderr) + drained_stderr
        returncode = process.returncode
    if (_run_root(layout, spec.run_id) / "cancel-request.json").is_file():
        terminal_status = "cancelled"
    return _persist_communicated_state(
        layout,
        state,
        state_path,
        stdout=stdout,
        stderr=stderr,
        status=terminal_status,
        returncode=returncode,
    )


def _recover_interrupted_process(
    layout: ProjectLayout,
    state: Mapping[str, Any],
    state_path: Path,
) -> JsonObject:
    run_root = _run_root(layout, state["run_id"])
    process = read_json(run_root / "process.json")
    if (
        process.get("run_id") != state["run_id"]
        or process.get("process_group_id") != state.get("process_group_id")
    ):
        raise ValueError("durable process ownership evidence conflicts")
    current_identity = _process_start_identity(process["pid"])
    if current_identity == process.get("process_start_identity"):
        try:
            os.killpg(process["process_group_id"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    status = (
        "cancelled"
        if (run_root / "cancel-request.json").is_file()
        else "failed"
    )
    return _persist_communicated_state(
        layout,
        state,
        state_path,
        stdout="",
        stderr="executor restarted after PID persistence",
        status=status,
        returncode=-signal.SIGTERM,
    )


def _persist_communicated_state(
    layout: ProjectLayout,
    state: Mapping[str, Any],
    state_path: Path,
    *,
    stdout: str,
    stderr: str,
    status: str,
    returncode: int | None,
) -> JsonObject:
    run_root = _run_root(layout, state["run_id"])
    (run_root / "stdout.txt").write_text(stdout, encoding="utf-8")
    (run_root / "stderr.txt").write_text(stderr, encoding="utf-8")
    finished_at = _timestamp()
    communicated = {
        **state,
        "status": status,
        "phase": "communicated",
        "returncode": returncode,
        "finished_at": finished_at,
        "duration_ms": _duration_ms(state["started_at"], finished_at),
    }
    atomic_write_json(state_path, communicated)
    return communicated


def _collect_and_settle_locked(
    layout: ProjectLayout,
    initial_state: Mapping[str, Any],
    state_path: Path,
) -> JsonObject:
    state = dict(initial_state)
    if state.get("phase") == "settlement-prepared":
        terminal = _settle_run_locked(layout, state, state_path)
        return _verified_collection_result(layout, terminal)
    spec = state.get("spec")
    if not isinstance(spec, Mapping):
        raise ValueError("durable RunSpec is missing")
    output_paths = _path_sequence(spec.get("output_paths"), "output_paths")
    output_types = _string_sequence(
        spec.get("expected_artifact_types"), "expected_artifact_types"
    )
    if len(output_paths) != len(output_types):
        raise ValueError(
            "output_paths and expected_artifact_types cardinality differs"
        )

    # A run with provider GPU usage can never be settled (usage is unknown);
    # detect that BEFORE registering any output artifact so a non-settleable
    # run detaches its reservation instead of leaving registered artifacts
    # pointing at a run whose state never advances.
    if _run_requires_detach(state):
        terminal = _detach_run_locked(layout, state, state_path)
        return dict(terminal["collection_result"])

    output_artifacts: list[JsonObject] = []
    for output_path, artifact_type in zip(
        output_paths, output_types, strict=True
    ):
        if not output_path.is_file():
            raise ValueError(f"declared output is missing: {output_path}")
        output_artifacts.append(
            register_artifact(
                layout,
                output_path,
                _run_artifact_metadata(state, artifact_type),
            )
        )

    artifact_ids = [
        *_string_sequence(state.get("artifact_ids"), "artifact_ids"),
        *[row["artifact_id"] for row in output_artifacts],
    ]
    artifact_types = [
        *_string_sequence(state.get("artifact_types"), "artifact_types"),
        *output_types,
    ]
    accounting = _storage_accounting(
        layout,
        state["run_id"],
        artifact_ids,
        artifact_types,
    )
    collection_result = {
        "run_id": state["run_id"],
        "status": "collected",
        "output_artifacts": output_artifacts,
        "storage_accounting": accounting,
    }
    prepared = {
        **state,
        "phase": "settlement-prepared",
        "output_artifacts": output_artifacts,
        "accounted_artifact_ids": artifact_ids,
        "accounted_artifact_types": artifact_types,
        "storage_accounting": accounting,
        "settlement_actual": _settlement_actual(state, accounting),
        "collection_result": collection_result,
    }
    atomic_write_json(state_path, prepared)
    terminal = _settle_run_locked(layout, prepared, state_path)
    return _verified_collection_result(layout, terminal)


def _storage_accounting(
    layout: ProjectLayout,
    run_id: str,
    artifact_ids: Sequence[str],
    artifact_types: Sequence[str],
) -> JsonObject:
    verified = verify_run_snapshot_artifacts(
        layout,
        artifact_ids,
        run_id=run_id,
        expected_types=artifact_types,
    )
    budget = read_json(layout.budget)
    policy = budget.get("storage_accounting")
    if not isinstance(policy, Mapping):
        raise ValueError("approved storage accounting policy is missing")
    excluded_types = _string_sequence(
        policy.get("excluded_artifact_types"),
        "excluded_artifact_types",
    )
    if policy.get("count_snapshot_copy_once") is not True:
        raise ValueError("unsupported storage accounting policy")
    charged: list[JsonObject] = []
    excluded: list[JsonObject] = []
    for record in verified:
        evidence = {
            "artifact_id": record["artifact_id"],
            "type": record["type"],
            "source_path": record["path"],
            "size_bytes": record["size_bytes"],
            "sha256": record["sha256"],
        }
        if record["type"] in excluded_types:
            excluded.append(evidence)
        else:
            charged.append(evidence)
    return {
        "count_snapshot_copy_once": True,
        "excluded_artifact_types": excluded_types,
        "charged_bytes": sum(row["size_bytes"] for row in charged),
        "charged_artifacts": charged,
        "excluded_artifacts": excluded,
    }


def _run_requires_detach(state: Mapping[str, Any]) -> bool:
    """A run with provider GPU usage has unknown usage and must detach, not settle."""
    spec = state.get("spec")
    if not isinstance(spec, Mapping):
        return False
    limits = spec.get("resource_limits")
    if not isinstance(limits, Mapping):
        return False
    return limits.get("gpu_seconds", 0) > 0 or limits.get("gpu_type") is not None


def _detach_run_locked(
    layout: ProjectLayout, state: Mapping[str, Any], state_path: Path
) -> JsonObject:
    # Release the unmeasured reservation without registering output artifacts
    # or running cleanup; the run terminates with unknown usage.
    detach_budget_reservation(
        layout, state["reservation_id"], "provider usage unknown; non-settleable run"
    )
    terminal = {
        **state,
        "phase": "terminal",
        "settlement_id": None,
        "usage_status": "unknown",
        "settlement_actual": None,
        "collection_result": {
            "run_id": state["run_id"],
            "status": "detached",
            "output_artifacts": [],
            "storage_accounting": None,
        },
    }
    atomic_write_json(state_path, terminal)
    return terminal


def _settlement_actual(
    state: Mapping[str, Any], accounting: Mapping[str, Any]
) -> JsonObject:
    spec = state.get("spec")
    if not isinstance(spec, Mapping):
        raise ValueError("durable RunSpec is missing")
    limits = spec.get("resource_limits")
    if not isinstance(limits, Mapping):
        raise ValueError("durable run resource limits are missing")
    actual: JsonObject = {}
    for field in ("cpu_seconds",):
        if field in limits:
            actual[field] = 0
    if limits.get("gpu_seconds", 0) > 0 or limits.get("gpu_type") is not None:
        raise ValueError(
            "provider GPU usage is unknown; reservation-detach is required "
            "instead of recording zero"
        )
    if "wall_seconds" in limits:
        actual["wall_seconds"] = min(
            limits["wall_seconds"], state.get("duration_ms", 0) / 1000
        )
    if "gpu_type" in limits:
        actual["gpu_type"] = limits["gpu_type"]
    if "storage_bytes" in limits:
        actual["storage_bytes"] = accounting["charged_bytes"]
    return actual


def _settle_run_locked(
    layout: ProjectLayout,
    state: Mapping[str, Any],
    state_path: Path,
) -> JsonObject:
    actual = state.get("settlement_actual")
    if not isinstance(actual, Mapping):
        raise ValueError("run settlement actual usage is missing")
    settle_budget(layout, state["reservation_id"], actual)
    matching = [
        row
        for row in read_jsonl(layout.budget_settlements)
        if row.get("reservation_id") == state["reservation_id"]
    ]
    if len(matching) != 1:
        raise ValueError("run budget settlement evidence is not unique")
    terminal = {
        **state,
        "phase": "terminal",
        "settlement_id": matching[0]["settlement_id"],
    }
    atomic_write_json(state_path, terminal)
    return terminal


def _verified_collection_result(
    layout: ProjectLayout, state: Mapping[str, Any]
) -> JsonObject:
    result = state.get("collection_result")
    if not isinstance(result, Mapping):
        raise ValueError("run collection result is missing")
    # A detached run has unknown usage and no registered output artifacts to
    # verify; its collection_result is authoritative on its own.
    if state.get("usage_status") == "unknown":
        return dict(result)
    artifact_ids = _string_sequence(
        state.get("accounted_artifact_ids"),
        "accounted_artifact_ids",
    )
    artifact_types = _string_sequence(
        state.get("accounted_artifact_types"),
        "accounted_artifact_types",
    )
    verify_run_snapshot_artifacts(
        layout,
        artifact_ids,
        run_id=state["run_id"],
        expected_types=artifact_types,
    )
    return dict(result)


def _run_artifact_metadata(
    state: Mapping[str, Any], artifact_type: str
) -> JsonObject:
    return {
        "type": artifact_type,
        "producer": "research-executor",
        "storage": "snapshot",
        "candidate_id": state["candidate_id"],
        "run_id": state["run_id"],
        "evaluation_cycle_checkpoint_id": state[
            "evaluation_cycle_checkpoint_id"
        ],
        "provenance_tags": [
            state["evaluation_spec_id"],
            state["evaluation_cycle_checkpoint_id"],
        ],
        "comparability_tags": [
            state["evaluation_spec_id"],
            state["evaluation_cycle_checkpoint_id"],
        ],
    }


def _path_sequence(value: Any, field: str) -> tuple[Path, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    paths: list[Path] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{field} must contain non-empty paths")
        paths.append(Path(item).resolve())
    return tuple(paths)


def _string_sequence(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must contain non-empty strings")
    return list(value)


def _validate_run_spec(
    layout: ProjectLayout, spec: RunSpec
) -> JsonObject:
    run_id = _safe_id(spec.run_id, "run_id")
    if not isinstance(spec.sensitive_action, bool):
        raise ValueError("run sensitive_action must be a boolean")
    if spec.backend not in BACKEND_NAMES:
        raise ValueError(f"unsupported run backend: {spec.backend}")
    if not spec.argv or not all(
        isinstance(part, str) and part for part in spec.argv
    ):
        raise ValueError("run argv must contain non-empty strings")
    if spec.backend == "uv-local" and spec.argv[0] != "uv":
        raise ValueError("uv-local run command must start with uv")
    _validate_run_command(layout, spec)
    if (
        isinstance(spec.timeout_seconds, bool)
        or not isinstance(spec.timeout_seconds, int)
        or spec.timeout_seconds <= 0
    ):
        raise ValueError("run timeout_seconds must be positive")
    if not isinstance(spec.resource_limits, Mapping):
        raise ValueError("run resource_limits must be an object")
    resource_limits = dict(spec.resource_limits)
    unknown_resource = resource_limits.keys() - {
        "wall_seconds",
        "cpu_seconds",
        "gpu_type",
        "gpu_seconds",
        "storage_bytes",
    }
    if unknown_resource:
        raise ValueError(
            "run resource_limits has unknown field: "
            f"{sorted(unknown_resource)[0]}"
        )
    if resource_limits.get("gpu_seconds", 0) > 0 or resource_limits.get("gpu_type") is not None:
        _require_gpu_host_readiness()
    if len(spec.output_paths) != len(spec.expected_artifact_types):
        # Collection owns the public rejection so execution may still capture
        # a terminal command receipt for a malformed output declaration.
        pass
    environment = _validated_environment_for_run(layout, spec)
    candidates = load_candidates(layout)
    candidate = candidates.get(spec.candidate_id)
    if candidate is None:
        raise ValueError(f"unknown candidate: {spec.candidate_id}")
    if candidate.get("evaluation_spec_id") != spec.evaluation_spec_id:
        raise ValueError(
            "candidate EvaluationSpec does not match RunSpec"
        )
    validate_active_evaluation_spec(layout, spec.evaluation_spec_id)
    checkpoint = resolve_evaluation_cycle_checkpoint(
        layout, spec.evaluation_cycle_checkpoint_id
    )
    _validate_run_checkpoint(layout, spec, checkpoint)
    payload = _run_spec_payload(spec)
    return {
        "run_id": run_id,
        "resource_limits": resource_limits,
        "environment_receipt_artifact_id": environment[
            "receipt_artifact_id"
        ],
        "checkpoint": checkpoint,
        "spec": payload,
        "spec_hash": _hash_payload(payload),
    }


def _validate_run_command(
    layout: ProjectLayout, spec: RunSpec
) -> None:
    modification = _active_modification_payload(layout)
    allowed_commands = modification.get("allowed_commands")
    if not isinstance(allowed_commands, list) or not all(
        isinstance(command, str) and command
        for command in allowed_commands
    ):
        raise ValueError("modification allowed_commands must be a string list")
    if spec.argv[0] not in allowed_commands:
        raise PermissionError(
            f"run command is not approved: {spec.argv[0]}"
        )
    editable_roots = _approved_path_roots(
        modification, "editable_paths", "append_only_paths"
    )
    if not _within_any(spec.cwd, editable_roots):
        raise PermissionError("run cwd is outside approved paths")


def _require_gpu_host_readiness() -> None:
    """Reject sandbox execution before a GPU reservation can become a CPU run."""
    device_nodes = list(Path("/dev").glob("nvidia*"))
    if not device_nodes:
        raise RuntimeError(
            "sandbox-no-device: GPU run requires host-backed /dev/nvidia* access"
        )
    driver = Path("/proc/driver/nvidia/version")
    telemetry = Path("/proc/driver/nvidia/gpus")
    if not driver.is_file() or not telemetry.exists():
        raise RuntimeError(
            "host-gpu-not-ready: NVIDIA driver or telemetry is unavailable"
        )


def _validated_environment_for_run(
    layout: ProjectLayout, spec: RunSpec
) -> JsonObject:
    index_path = _environment_index_path(layout, spec.environment_id)
    if not index_path.is_file():
        raise ValueError("matching prepared environment receipt is required")
    index = read_json(index_path)
    if (
        index.get("record_type") != "environment-index"
        or index.get("environment_id") != spec.environment_id
        or index.get("backend") != spec.backend
        or index.get("dependency_lock_hash")
        != spec.dependency_lock_hash
    ):
        raise ValueError("prepared environment does not match RunSpec")
    receipt_artifact_id = index.get("receipt_artifact_id")
    if not isinstance(receipt_artifact_id, str):
        raise ValueError("prepared environment artifact is invalid")
    artifact = resolve_artifact(layout, receipt_artifact_id)
    verification = verify_artifact(layout, receipt_artifact_id)
    if (
        artifact.get("type") != "environment-receipt"
        or verification.get("available") is not True
        or verification.get("valid") is not True
    ):
        raise ValueError("prepared environment receipt is invalid")
    receipt = read_json(Path(artifact["path"]))
    if (
        receipt.get("status") != "prepared"
        or receipt.get("environment_id") != spec.environment_id
        or receipt.get("backend") != spec.backend
        or receipt.get("dependency_lock_hash")
        != spec.dependency_lock_hash
    ):
        raise ValueError("prepared environment receipt conflicts")
    environment_spec = index.get("spec")
    if not isinstance(environment_spec, Mapping):
        raise ValueError("prepared environment specification is invalid")
    if environment_spec.get("cwd") != str(spec.cwd.resolve()):
        raise ValueError("run cwd does not match prepared environment")
    return {
        "receipt_artifact_id": receipt_artifact_id,
        "receipt": receipt,
        "spec": dict(environment_spec),
    }


def _validate_run_checkpoint(
    layout: ProjectLayout,
    spec: RunSpec,
    checkpoint: Mapping[str, Any],
) -> None:
    if (
        checkpoint.get("checkpoint_id")
        != spec.evaluation_cycle_checkpoint_id
    ):
        raise ValueError("evaluation checkpoint ID conflicts with RunSpec")
    if checkpoint.get("evaluation_spec_id") != spec.evaluation_spec_id:
        raise ValueError(
            "evaluation checkpoint EvaluationSpec conflicts with RunSpec"
        )
    cycle_id = checkpoint.get("cycle_id")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("evaluation checkpoint cycle identity is invalid")
    checkpoint_path = (
        layout.cycles / cycle_id / "evaluation-comparison.json"
    )
    if not checkpoint_path.is_file():
        raise ValueError("evaluation checkpoint cycle file is missing")
    if read_json(checkpoint_path) != dict(checkpoint):
        raise ValueError("evaluation checkpoint cycle evidence conflicts")
    state = load_research_state(layout)
    anchor = state.get("active_evaluation_spec")
    if not isinstance(anchor, Mapping):
        raise ValueError("active EvaluationSpec anchor is required")
    if (
        checkpoint.get("evaluation_spec_artifact_id")
        != anchor.get("artifact_id")
        or checkpoint.get("evaluation_spec_artifact_sha256")
        != anchor.get("artifact_sha256")
    ):
        raise ValueError(
            "evaluation checkpoint artifact conflicts with active EvaluationSpec"
        )


def _run_spec_payload(spec: RunSpec) -> JsonObject:
    return {
        "run_id": spec.run_id,
        "environment_id": spec.environment_id,
        "dependency_lock_hash": spec.dependency_lock_hash,
        "backend": spec.backend,
        "cwd": str(spec.cwd.resolve()),
        "argv": list(spec.argv),
        "timeout_seconds": spec.timeout_seconds,
        "resource_limits": dict(spec.resource_limits),
        "input_paths": [
            str(path.resolve()) for path in spec.input_paths
        ],
        "output_paths": [
            str(path.resolve()) for path in spec.output_paths
        ],
        "expected_artifact_types": list(
            spec.expected_artifact_types
        ),
        "candidate_id": spec.candidate_id,
        "evaluation_spec_id": spec.evaluation_spec_id,
        "evaluation_cycle_checkpoint_id": (
            spec.evaluation_cycle_checkpoint_id
        ),
        "sensitive_action": spec.sensitive_action,
    }


def _register_terminal_run_artifacts(
    layout: ProjectLayout,
    spec: RunSpec,
    state: Mapping[str, Any],
    stdout_path: Path,
    stderr_path: Path,
) -> JsonObject:
    run_root = _run_root(layout, spec.run_id)
    receipt_path = run_root / "command-receipt.json"
    receipt = {
        "schema_version": 1,
        "record_type": "command-receipt",
        "run_id": spec.run_id,
        "status": state["status"],
        "returncode": state["returncode"],
        "started_at": state["started_at"],
        "finished_at": state["finished_at"],
        "duration_ms": state["duration_ms"],
        "candidate_id": spec.candidate_id,
        "evaluation_spec_id": spec.evaluation_spec_id,
        "evaluation_cycle_checkpoint_id": (
            spec.evaluation_cycle_checkpoint_id
        ),
        "environment_id": spec.environment_id,
        "backend": spec.backend,
        "argv": list(spec.argv),
        "reservation_id": state["reservation_id"],
    }
    atomic_write_json(receipt_path, receipt)
    common_metadata = {
        "producer": "research-executor",
        "storage": "snapshot",
        "candidate_id": spec.candidate_id,
        "run_id": spec.run_id,
        "evaluation_cycle_checkpoint_id": (
            spec.evaluation_cycle_checkpoint_id
        ),
        "provenance_tags": [
            spec.evaluation_spec_id,
            spec.evaluation_cycle_checkpoint_id,
        ],
        "comparability_tags": [
            spec.evaluation_spec_id,
            spec.evaluation_cycle_checkpoint_id,
        ],
    }
    receipt_artifact = register_artifact(
        layout,
        receipt_path,
        {
            **common_metadata,
            "type": "command-receipt",
        },
    )
    stdout_artifact = register_artifact(
        layout,
        stdout_path,
        {
            **common_metadata,
            "type": "command-stdout",
        },
    )
    stderr_artifact = register_artifact(
        layout,
        stderr_path,
        {
            **common_metadata,
            "type": "command-stderr",
        },
    )
    return {
        "run_id": spec.run_id,
        "status": state["status"],
        "returncode": state["returncode"],
        "started_at": state["started_at"],
        "finished_at": state["finished_at"],
        "duration_ms": state["duration_ms"],
        "receipt_artifact_id": receipt_artifact["artifact_id"],
        "stdout_artifact_id": stdout_artifact["artifact_id"],
        "stderr_artifact_id": stderr_artifact["artifact_id"],
    }


def _require_same_run_spec(
    state: Mapping[str, Any], validated: Mapping[str, Any]
) -> None:
    if state.get("spec_hash") != validated["spec_hash"]:
        if (
            state.get("evaluation_cycle_checkpoint_id")
            != validated["spec"]["evaluation_cycle_checkpoint_id"]
        ):
            raise ValueError(
                "run checkpoint relabel is not permitted"
            )
        raise ValueError("run specification conflicts with durable state")


def _terminal_result(state: Mapping[str, Any]) -> JsonObject:
    result = state.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("terminal run result is invalid")
    return dict(result)


def _run_root(layout: ProjectLayout, run_id: str) -> Path:
    return layout.runs / _safe_id(run_id, "run_id")


def _process_start_identity(pid: int) -> str:
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        fields = stat_path.read_text(encoding="utf-8").split()
    except OSError:
        return f"pid:{pid}:unavailable"
    start_time = fields[21] if len(fields) > 21 else "unknown"
    return f"pid:{pid}:start:{start_time}"


def _hash_payload(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _adapter(
    layout: ProjectLayout, backend: str
) -> CommandLocalExecutor | UvLocalExecutor | None:
    executor_type = BACKEND_REGISTRY.get(backend)
    return executor_type(layout) if executor_type is not None else None


def _unsupported_backend(backend: Any) -> JsonObject:
    return {
        "status": "failed",
        "failure_type": "unsupported_backend",
        "backend": backend,
    }


def _inspect_known_environment(
    layout: ProjectLayout, spec: EnvironmentSpec
) -> JsonObject:
    environment_id = _safe_id(spec.environment_id, "environment_id")
    if spec.backend not in BACKEND_NAMES:
        return _unsupported_backend(spec.backend)
    if (
        isinstance(spec.timeout_seconds, bool)
        or not isinstance(spec.timeout_seconds, int)
        or spec.timeout_seconds <= 0
    ):
        raise ValueError("environment timeout_seconds must be positive")

    cwd = spec.cwd.resolve()
    lock_path = spec.dependency_lock_path.resolve()
    if not lock_path.is_file():
        raise ValueError("dependency lock path must be an existing file")
    actual_lock_hash = sha256_file(lock_path)
    if actual_lock_hash != spec.dependency_lock_hash:
        raise ValueError("dependency lock hash does not match lock file")

    modification = _active_modification_payload(layout)
    editable_roots = _approved_path_roots(
        modification, "editable_paths", "append_only_paths"
    )
    if not _within_any(cwd, editable_roots):
        raise PermissionError("environment cwd is outside editable paths")

    allowed_write_paths = tuple(
        path.resolve() for path in spec.allowed_write_paths
    )
    for path in allowed_write_paths:
        if not _within_any(path, editable_roots):
            raise PermissionError(
                f"environment write path is outside approved paths: {path}"
            )
    temporary_paths = tuple(path.resolve() for path in spec.temporary_paths)
    for path in temporary_paths:
        if not _within_any(path, allowed_write_paths):
            raise ValueError(
                f"temporary path is not an allowed write path: {path}"
            )

    command = list(spec.prepare_argv)
    _validate_prepare_command(spec.backend, command, modification)
    undeclared = _undeclared_dependencies(
        spec.dependencies, modification
    )
    network = _network_declaration(modification)
    dependency_policy = modification.get("dependency_policy")
    undeclared_gateable = (
        isinstance(dependency_policy, Mapping)
        and dependency_policy.get("undeclared_requires_gate") is True
    )
    if undeclared:
        # The coordinator can request a modification gate to approve the
        # dependency; this is a self-recoverable coordinator action, not a
        # passive user escalation.
        status = "modification_gate_required" if undeclared_gateable else "blocked"
    else:
        status = "ready"
    result: JsonObject = {
        "status": status,
        "environment_id": environment_id,
        "backend": spec.backend,
        "cwd": str(cwd),
        "dependency_lock_path": str(lock_path),
        "dependency_lock_hash": actual_lock_hash,
        "dependencies": list(spec.dependencies),
        "undeclared_dependencies": undeclared,
        "command": command,
        "allowed_write_paths": [str(path) for path in allowed_write_paths],
        "temporary_paths": [str(path) for path in temporary_paths],
        "timeout_seconds": spec.timeout_seconds,
        "network": network,
    }
    return result


def _prepare_known_environment(
    layout: ProjectLayout, spec: EnvironmentSpec
) -> JsonObject:
    inspection = _inspect_known_environment(layout, spec)
    if inspection["status"] != "ready":
        raise PermissionError(
            "environment preparation requires approved dependencies"
        )

    started_at = _timestamp()
    command = list(spec.prepare_argv)
    stdout = ""
    stderr = ""
    returncode = 0
    if command:
        process = subprocess.Popen(
            command,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(spec.cwd.resolve()),
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(
                timeout=spec.timeout_seconds
            )
        except subprocess.TimeoutExpired as error:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            drained_stdout, drained_stderr = process.communicate(
                timeout=spec.timeout_seconds
            )
            stdout = _partial_text(error.output) + drained_stdout
            stderr = _partial_text(error.stderr) + drained_stderr
            raise TimeoutError(
                "environment preparation exceeded timeout"
            ) from error
        returncode = process.returncode

    finished_at = _timestamp()
    receipt_path = _environment_receipt_path(
        layout, spec.environment_id
    )
    receipt: JsonObject = {
        "schema_version": 1,
        "record_type": "environment-receipt",
        "environment_id": inspection["environment_id"],
        "backend": inspection["backend"],
        "cwd": inspection["cwd"],
        "dependency_lock_path": inspection["dependency_lock_path"],
        "dependency_lock_hash": inspection["dependency_lock_hash"],
        "dependencies": inspection["dependencies"],
        "command": command,
        "allowed_write_paths": inspection["allowed_write_paths"],
        "temporary_paths": inspection["temporary_paths"],
        "tool_versions": {},
        "changed_paths": [],
        "network": inspection["network"],
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": _duration_ms(started_at, finished_at),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "status": "prepared" if returncode == 0 else "failed",
    }
    atomic_write_json(receipt_path, receipt)
    artifact = register_artifact(
        layout,
        receipt_path,
        {
            "type": "environment-receipt",
            "producer": "research-executor",
            "storage": "snapshot",
            "provenance_tags": [
                inspection["environment_id"],
                inspection["backend"],
                inspection["dependency_lock_hash"],
            ],
        },
    )
    result: JsonObject = {
        "status": receipt["status"],
        "environment_id": inspection["environment_id"],
        "backend": inspection["backend"],
        "command": command,
        "returncode": returncode,
        "receipt_path": str(receipt_path),
        "receipt_artifact_id": artifact["artifact_id"],
    }
    atomic_write_json(
        _environment_index_path(layout, spec.environment_id),
        {
            "schema_version": 1,
            "record_type": "environment-index",
            "environment_id": inspection["environment_id"],
            "backend": inspection["backend"],
            "dependency_lock_hash": inspection["dependency_lock_hash"],
            "spec": _environment_spec_payload(spec),
            "receipt_path": str(receipt_path),
            "receipt_artifact_id": artifact["artifact_id"],
            "result": result,
        },
    )
    return result


def _active_modification_payload(layout: ProjectLayout) -> JsonObject:
    approvals = [
        row
        for row in read_jsonl(layout.gates)
        if row.get("gate_type") == "modification"
        and row.get("status") == "approved"
    ]
    if not approvals:
        raise PermissionError("modification approval required")
    gate_id = approvals[-1].get("gate_id")
    if not isinstance(gate_id, str):
        raise PermissionError("modification approval is invalid")
    approved = read_approved_gate(layout, gate_id, "modification")
    payload = approved.get("payload")
    if not isinstance(payload, Mapping):
        raise PermissionError("modification approval payload is invalid")
    return dict(payload)


def _approved_path_roots(
    payload: Mapping[str, Any], *fields: str
) -> tuple[Path, ...]:
    roots: list[Path] = []
    for field in fields:
        value = payload.get(field)
        if not isinstance(value, list):
            raise ValueError(f"modification {field} must be a list")
        for path in value:
            if not isinstance(path, str) or not path:
                raise ValueError(
                    f"modification {field} contains an invalid path"
                )
            roots.append(Path(path).resolve())
    return tuple(roots)


def _within_any(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in roots)


def _validate_prepare_command(
    backend: str,
    command: list[str],
    modification: Mapping[str, Any],
) -> None:
    if not all(isinstance(part, str) and part for part in command):
        raise ValueError("prepare_argv must contain non-empty strings")
    if backend == "uv-local" and (not command or command[0] != "uv"):
        raise ValueError("uv-local prepare command must start with uv")
    if not command:
        return
    allowed_commands = modification.get("allowed_commands")
    if not isinstance(allowed_commands, list):
        raise ValueError("modification allowed_commands must be a list")
    if command[0] not in allowed_commands:
        raise PermissionError(
            f"prepare command is not approved: {command[0]}"
        )
    if shutil.which(command[0]) is None:
        raise ValueError(f"prepare command is unavailable: {command[0]}")


def _undeclared_dependencies(
    dependencies: Sequence[str],
    modification: Mapping[str, Any],
) -> list[str]:
    policy = modification.get("dependency_policy")
    if not isinstance(policy, Mapping):
        raise ValueError("modification dependency_policy must be an object")
    allowed = policy.get("allowed")
    if not isinstance(allowed, list) or not all(
        isinstance(item, str) and item for item in allowed
    ):
        raise ValueError(
            "modification dependency_policy.allowed must be a string list"
        )
    normalized: list[str] = []
    for dependency in dependencies:
        if not isinstance(dependency, str) or not dependency:
            raise ValueError("dependencies must contain non-empty strings")
        if dependency not in allowed:
            normalized.append(dependency)
    return normalized


def _network_declaration(
    modification: Mapping[str, Any],
) -> JsonObject:
    network = modification.get("network")
    if not isinstance(network, Mapping):
        raise ValueError("modification network must be an object")
    mode = network.get("mode")
    services = network.get("allowed_services")
    if not isinstance(mode, str) or not isinstance(services, list):
        raise ValueError("modification network declaration is invalid")
    return {
        "declared_mode": mode,
        "allowed_services": list(services),
        "os_isolation_enforced": False,
    }


def _environment_spec_payload(spec: EnvironmentSpec) -> JsonObject:
    return {
        "environment_id": spec.environment_id,
        "backend": spec.backend,
        "cwd": str(spec.cwd.resolve()),
        "prepare_argv": list(spec.prepare_argv),
        "dependencies": list(spec.dependencies),
        "dependency_lock_path": str(
            spec.dependency_lock_path.resolve()
        ),
        "dependency_lock_hash": spec.dependency_lock_hash,
        "allowed_write_paths": [
            str(path.resolve()) for path in spec.allowed_write_paths
        ],
        "temporary_paths": [
            str(path.resolve()) for path in spec.temporary_paths
        ],
        "timeout_seconds": spec.timeout_seconds,
    }


def _environment_root(
    layout: ProjectLayout, environment_id: str
) -> Path:
    return (
        layout.runs
        / "environments"
        / _safe_id(environment_id, "environment_id")
    )


def _environment_receipt_path(
    layout: ProjectLayout, environment_id: str
) -> Path:
    return _environment_root(
        layout, environment_id
    ) / "environment-receipt.json"


def _environment_index_path(
    layout: ProjectLayout, environment_id: str
) -> Path:
    return _environment_root(layout, environment_id) / "environment.json"


def _safe_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or SAFE_ID.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be a safe non-empty identifier")
    return value


def _partial_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(started_at: str, finished_at: str) -> int:
    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)
    return max(0, int((finished - started).total_seconds() * 1000))
