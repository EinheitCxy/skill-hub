from __future__ import annotations

from dataclasses import fields, replace
import importlib
import json
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from autoresearch.artifacts import (
    list_artifacts,
    resolve_artifact,
    verify_artifact,
)
from autoresearch.contracts import EnvironmentSpec, RunSpec
from autoresearch.governance import reserve_budget
from autoresearch.io import (
    atomic_write_json,
    read_json,
    read_jsonl,
    sha256_file,
)


EXPECTED_DEFAULT_LIMITS = {
    "wall_seconds": 86_400,
    "cpu_seconds": 86_400,
    "gpu_type": None,
    "gpu_seconds": 86_400,
    "storage_bytes": 2_147_483_648,
    "official_candidates": 3,
    "loop-subagent-bound": 4,
    "retries": 3,
    "debug_candidates": 3,
}
TERMINAL_RESULT_FIELDS = {
    "run_id",
    "status",
    "returncode",
    "started_at",
    "finished_at",
    "duration_ms",
    "receipt_artifact_id",
    "stdout_artifact_id",
    "stderr_artifact_id",
}


def executor_module():
    return importlib.import_module("autoresearch.executors")


def test_gpu_readiness_rejects_sandbox_without_device(monkeypatch):
    readiness = executor_module()._require_gpu_host_readiness
    monkeypatch.setattr(Path, "glob", lambda self, pattern: [])

    with pytest.raises(RuntimeError, match="sandbox-no-device"):
        readiness()


def run_spec(layout, **overrides) -> RunSpec:
    values = {
        "run_id": "run-contract",
        "environment_id": "fixture-env",
        "dependency_lock_hash": "a" * 64,
        "backend": "command-local",
        "cwd": layout.root,
        "argv": (sys.executable, "-c", "print('ok')"),
        "timeout_seconds": 5,
        "resource_limits": {
            "wall_seconds": 5,
            "cpu_seconds": 5,
            "gpu_seconds": 0,
            "storage_bytes": 1_000_000,
        },
        "input_paths": (),
        "output_paths": (),
        "expected_artifact_types": (),
        "candidate_id": "cand-1",
        "evaluation_spec_id": "eval-spec-1",
        "evaluation_cycle_checkpoint_id": "evaluation-cycle-contract",
    }
    values.update(overrides)
    return RunSpec(**values)


def environment_spec(layout, lock_path: Path, **overrides) -> EnvironmentSpec:
    values = {
        "environment_id": "fixture-env",
        "backend": "command-local",
        "cwd": layout.root,
        "prepare_argv": (),
        "dependencies": (),
        "dependency_lock_path": lock_path,
        "dependency_lock_hash": sha256_file(lock_path),
        "allowed_write_paths": (layout.root,),
        "temporary_paths": (),
        "timeout_seconds": 5,
    }
    values.update(overrides)
    return EnvironmentSpec(**values)


def file_snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_fixture_uses_the_exact_approved_defaults(approved_execution_project):
    budget = read_json(approved_execution_project.budget)
    assert budget["limits"] == EXPECTED_DEFAULT_LIMITS
    assert budget["storage_accounting"] == {
        "count_snapshot_copy_once": True,
        "excluded_artifact_types": ["model-weight"],
    }


def test_run_spec_contract_requires_one_cycle_checkpoint():
    assert [field.name for field in fields(RunSpec)][-4:] == [
        "candidate_id",
        "evaluation_spec_id",
        "evaluation_cycle_checkpoint_id",
        "sensitive_action",
    ]


def test_run_is_blocked_without_approved_envelopes(project_layout):
    with pytest.raises(
        PermissionError,
        match="modification and budget approval required",
    ):
        executor_module().execute_run(
            project_layout, run_spec(project_layout)
        )


def test_prepare_rejects_dependency_lock_hash_mismatch(
    approved_execution_project, tmp_path: Path
):
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text("fixture-lock", encoding="utf-8")
    spec = environment_spec(
        approved_execution_project,
        lock_path,
        dependency_lock_hash="not-the-file-hash",
    )

    with pytest.raises(ValueError, match="dependency lock hash"):
        executor_module().prepare_environment(approved_execution_project, spec)


def test_inspection_is_read_only_and_reports_undeclared_dependencies(
    approved_execution_project,
):
    lock_path = approved_execution_project.root / "inspect.lock"
    lock_path.write_text("fixture-lock", encoding="utf-8")
    spec = environment_spec(
        approved_execution_project,
        lock_path,
        dependencies=("undeclared-package>=1",),
    )
    before = file_snapshot(approved_execution_project.root)

    result = executor_module().inspect_environment(
        approved_execution_project, spec
    )

    assert result["status"] == "modification_gate_required"
    assert result["undeclared_dependencies"] == ["undeclared-package>=1"]
    assert file_snapshot(approved_execution_project.root) == before


def test_unknown_backend_is_a_typed_failure_without_fallback(
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    lock_path = approved_execution_project.root / "unknown.lock"
    lock_path.write_text("fixture-lock", encoding="utf-8")
    spec = environment_spec(
        approved_execution_project,
        lock_path,
        backend="unknown-local",
    )
    monkeypatch.setattr(
        executors.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("unknown backend spawned"),
    )

    result = executors.prepare_environment(
        approved_execution_project, spec
    )

    assert result["status"] == "failed"
    assert result["failure_type"] == "unsupported_backend"


def test_nonempty_preparation_uses_owned_argv_process(
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    lock_path = approved_execution_project.root / "prepare.lock"
    lock_path.write_text("fixture-lock", encoding="utf-8")

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout):
            return "prepared\n", ""

    def fake_popen(argv, **kwargs):
        assert argv == [sys.executable, "-c", "print('prepare')"]
        assert kwargs["shell"] is False
        assert kwargs["start_new_session"] is True
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["text"] is True
        return FakeProcess()

    monkeypatch.setattr(executors.subprocess, "Popen", fake_popen)
    result = executors.prepare_environment(
        approved_execution_project,
        environment_spec(
            approved_execution_project,
            lock_path,
            prepare_argv=(
                sys.executable,
                "-c",
                "print('prepare')",
            ),
        ),
    )

    assert result["status"] == "prepared"
    assert verify_artifact(
        approved_execution_project, result["receipt_artifact_id"]
    )["valid"]


def test_uv_backend_rejects_non_uv_argv_before_spawn(
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    lock_path = approved_execution_project.root / "uv.lock"
    lock_path.write_text("fixture-lock", encoding="utf-8")
    spec = environment_spec(
        approved_execution_project,
        lock_path,
        backend="uv-local",
        prepare_argv=(sys.executable, "-c", "print('not uv')"),
    )
    monkeypatch.setattr(
        executors.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("invalid uv argv spawned"),
    )

    with pytest.raises(ValueError, match="uv"):
        executors.prepare_environment(approved_execution_project, spec)


def test_empty_preparation_writes_success_receipt_and_records_network_state(
    approved_execution_project, tmp_path: Path
):
    lock_path = tmp_path / "empty.lock"
    lock_path.write_text("fixture-lock", encoding="utf-8")

    result = executor_module().prepare_environment(
        approved_execution_project,
        environment_spec(approved_execution_project, lock_path),
    )

    assert result["status"] == "prepared"
    assert result["command"] == []
    receipt = read_json(Path(result["receipt_path"]))
    assert receipt["environment_id"] == "fixture-env"
    assert receipt["dependency_lock_hash"] == sha256_file(lock_path)
    assert receipt["network"]["declared_mode"] == "denied"
    assert receipt["network"]["os_isolation_enforced"] is False
    assert "network_isolated" not in receipt


def test_executor_never_uses_shell_and_reserves_before_spawn(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    events: list[str] = []
    original_reserve = executors.reserve_budget

    def observed_reserve(*args, **kwargs):
        events.append("reserve")
        reservation = args[1]
        assert "loop-subagent-bound" not in reservation
        return original_reserve(*args, **kwargs)

    class FakeProcess:
        pid = 123
        returncode = 0

        def communicate(self, timeout):
            events.append("communicate")
            return "ok\n", ""

    def fake_popen(argv, **kwargs):
        events.append("spawn")
        assert kwargs["shell"] is False
        assert kwargs["start_new_session"] is True
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["text"] is True
        return FakeProcess()

    monkeypatch.setattr(executors, "reserve_budget", observed_reserve)
    monkeypatch.setattr(executors.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(executors.os, "getpgid", lambda pid: pid)

    result = executors.execute_run(approved_execution_project, approved_run_spec)

    assert events == ["reserve", "spawn", "communicate"]
    assert set(result) == TERMINAL_RESULT_FIELDS


def test_pid_process_group_command_and_environment_are_durable_before_wait(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    run_root = approved_execution_project.runs / approved_run_spec.run_id

    class FakeProcess:
        pid = 321
        returncode = 0

        def communicate(self, timeout):
            assert (run_root / "run.lock").is_file()
            state = read_json(run_root / "state.json")
            process = read_json(run_root / "process.json")
            command = read_json(run_root / "command.json")
            environment = read_json(run_root / "environment.json")
            assert state["status"] == "running"
            assert process["pid"] == process["process_group_id"] == 321
            assert command["argv"] == list(approved_run_spec.argv)
            assert (
                environment["dependency_lock_hash"]
                == approved_run_spec.dependency_lock_hash
            )
            return "ok\n", ""

    monkeypatch.setattr(
        executors.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(executors.os, "getpgid", lambda pid: pid)

    executors.execute_run(approved_execution_project, approved_run_spec)


@pytest.mark.parametrize(
    ("returncode", "status"),
    [(0, "completed"), (7, "failed")],
)
def test_success_and_nonzero_exit_are_typed_terminal_results(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    status: str,
):
    executors = executor_module()

    class FakeProcess:
        pid = 456

        def __init__(self):
            self.returncode = returncode

        def communicate(self, timeout):
            return "partial-or-complete\n", "diagnostic\n"

    monkeypatch.setattr(
        executors.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(executors.os, "getpgid", lambda pid: pid)

    result = executors.execute_run(approved_execution_project, approved_run_spec)

    assert result["status"] == status
    assert result["returncode"] == returncode
    assert verify_artifact(
        approved_execution_project, result["stdout_artifact_id"]
    )["valid"]
    assert verify_artifact(
        approved_execution_project, result["stderr_artifact_id"]
    )["valid"]


def test_timeout_preserves_partial_output_and_terminates_only_owned_group(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    signals: list[tuple[int, signal.Signals]] = []

    class FakeProcess:
        pid = 654
        returncode = None

        def __init__(self):
            self.communicate_calls = 0

        def communicate(self, timeout):
            self.communicate_calls += 1
            if self.communicate_calls > 1:
                self.returncode = -signal.SIGTERM
                return "", ""
            raise subprocess.TimeoutExpired(
                approved_run_spec.argv,
                timeout,
                output="partial stdout",
                stderr="partial stderr",
            )

    monkeypatch.setattr(
        executors.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(executors.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        executors.os,
        "killpg",
        lambda group, sent: signals.append((group, sent)),
    )

    result = executors.execute_run(approved_execution_project, approved_run_spec)

    assert result["status"] == "timeout"
    assert signals[0] == (654, signal.SIGTERM)
    assert Path(
        resolve_artifact(
            approved_execution_project, result["stdout_artifact_id"]
        )["path"]
    ).read_text(encoding="utf-8") == "partial stdout"


def test_cancel_uses_only_the_project_owned_process_group(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    run_root = approved_execution_project.runs / approved_run_spec.run_id
    run_root.mkdir(parents=True)
    atomic_write_json(
        run_root / "process.json",
        {
            "run_id": approved_run_spec.run_id,
            "pid": 700,
            "process_group_id": 700,
            "process_start_identity": "owned-start-identity",
        },
    )
    observed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        executors.os,
        "killpg",
        lambda group, sent: observed.append((group, sent)),
    )

    result = executors.cancel_run(
        approved_execution_project, approved_run_spec.run_id
    )

    assert result["status"] == "cancelled"
    assert observed[0] == (700, signal.SIGTERM)
    assert all(group == 700 for group, _ in observed)


def test_cancel_rejects_live_pid_identity_reuse(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    run_root = approved_execution_project.runs / approved_run_spec.run_id
    run_root.mkdir(parents=True)
    atomic_write_json(
        run_root / "process.json",
        {
            "run_id": approved_run_spec.run_id,
            "pid": 701,
            "process_group_id": 701,
            "process_start_identity": "pid:701:start:old",
        },
    )
    monkeypatch.setattr(
        executors,
        "_process_start_identity",
        lambda pid: "pid:701:start:reused",
    )
    monkeypatch.setattr(
        executors.os,
        "killpg",
        lambda *args: pytest.fail("reused process group was signalled"),
    )

    with pytest.raises(ValueError, match="identity"):
        executors.cancel_run(
            approved_execution_project, approved_run_spec.run_id
        )


def test_run_command_must_be_approved_before_reservation_or_spawn(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    observed: list[str] = []
    monkeypatch.setattr(
        executors,
        "reserve_budget",
        lambda *args, **kwargs: observed.append("reserve"),
    )
    monkeypatch.setattr(
        executors.subprocess,
        "Popen",
        lambda *args, **kwargs: observed.append("spawn"),
    )

    with pytest.raises(PermissionError, match="command"):
        executors.execute_run(
            approved_execution_project,
            replace(
                approved_run_spec,
                argv=("unapproved-command", "--version"),
            ),
        )

    assert observed == []


def test_collect_requires_strict_positional_output_type_cardinality(
    approved_run_spec, approved_execution_project
):
    output = approved_execution_project.root / "metric.json"
    output.write_text("{}", encoding="utf-8")
    mismatched = replace(
        approved_run_spec,
        output_paths=(output,),
        expected_artifact_types=(),
    )

    executors = executor_module()
    executors.execute_run(approved_execution_project, mismatched)
    with pytest.raises(ValueError, match="cardinality"):
        executors.collect_run(approved_execution_project, mismatched.run_id)


def test_collect_registers_only_declared_outputs_with_exact_checkpoint(
    approved_run_spec, approved_execution_project
):
    output = approved_execution_project.root / "metric.json"
    ignored = approved_execution_project.root / "ignored.json"
    ignored.write_text("{}", encoding="utf-8")
    declared = replace(
        approved_run_spec,
        argv=(
            sys.executable,
            "-c",
            (
                "from pathlib import Path;"
                f"Path({str(output)!r}).write_text("
                f"{json.dumps({'accuracy': 0.9})!r}, encoding='utf-8')"
            ),
        ),
        output_paths=(output,),
        expected_artifact_types=("metrics",),
    )

    executors = executor_module()
    executors.execute_run(approved_execution_project, declared)
    result = executors.collect_run(approved_execution_project, declared.run_id)

    records = [
        record
        for record in list_artifacts(approved_execution_project, {"run_id": declared.run_id})
        if record["type"] == "metrics"
    ]
    assert [record["path"] for record in records] == [
        result["output_artifacts"][0]["path"]
    ]
    assert records[0]["evaluation_cycle_checkpoint_id"] == (
        declared.evaluation_cycle_checkpoint_id
    )
    assert all(Path(record["path"]).name != ignored.name for record in records)


def test_collect_detaches_gpu_run_without_registering_outputs(
    approved_execution_project,
):
    executors = executor_module()
    layout = approved_execution_project
    run_id = "gpu-detach"
    reservation_id = f"run:{run_id}"
    reserve_budget(layout, {"id": reservation_id, "gpu_seconds": 5})
    run_root = layout.runs / run_id
    run_root.mkdir(parents=True)
    atomic_write_json(
        run_root / "state.json",
        {
            "schema_version": 1,
            "record_type": "run-state",
            "run_id": run_id,
            "reservation_id": reservation_id,
            "spec": {
                "run_id": run_id,
                "resource_limits": {"gpu_seconds": 5},
                "output_paths": [],
                "expected_artifact_types": [],
            },
            "started_at": "2026-01-01T00:00:00Z",
            "duration_ms": 100,
            "phase": "artifacts-registered",
            "artifact_ids": [],
            "artifact_types": [],
        },
    )

    result = executors.collect_run(layout, run_id)

    assert result["status"] == "detached"
    budget = read_json(layout.budget)
    assert reservation_id not in budget["reservations"]
    assert budget["reserved"]["gpu_seconds"] == 0
    detachments = read_jsonl(layout.reservation_detachments)
    assert detachments[-1]["usage_status"] == "unknown"
    state = read_json(run_root / "state.json")
    assert state["phase"] == "terminal"
    assert state["usage_status"] == "unknown"
    # No output artifacts were registered for the non-settleable run.
    assert result["output_artifacts"] == []


@pytest.mark.parametrize(
    "checkpoint_id",
    [
        "evaluation-cycle-missing",
        "evaluation-cycle-wrong-cycle",
        "evaluation-cycle-wrong-spec",
        "evaluation-cycle-runspec-mismatch",
    ],
)
def test_checkpoint_mismatch_rejects_before_reservation_or_spawn(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint_id: str,
):
    executors = executor_module()
    observed: list[str] = []
    monkeypatch.setattr(
        executors,
        "reserve_budget",
        lambda *args, **kwargs: observed.append("reserve"),
    )
    monkeypatch.setattr(
        executors.subprocess,
        "Popen",
        lambda *args, **kwargs: observed.append("spawn"),
    )

    with pytest.raises(ValueError, match="checkpoint"):
        executors.execute_run(
            approved_execution_project,
            replace(
                approved_run_spec,
                evaluation_cycle_checkpoint_id=checkpoint_id,
            ),
        )

    assert observed == []


def test_tampered_checkpoint_rejects_before_reservation_or_spawn(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
):
    executors = executor_module()
    checkpoint_path = (
        approved_execution_project.cycles
        / "cycle-fixture"
        / "evaluation-comparison.json"
    )
    checkpoint = read_json(checkpoint_path)
    checkpoint["comparison_state_hash"] = "0" * 64
    atomic_write_json(checkpoint_path, checkpoint)
    observed: list[str] = []
    monkeypatch.setattr(
        executors,
        "reserve_budget",
        lambda *args, **kwargs: observed.append("reserve"),
    )
    monkeypatch.setattr(
        executors.subprocess,
        "Popen",
        lambda *args, **kwargs: observed.append("spawn"),
    )

    with pytest.raises(ValueError, match="checkpoint|artifact"):
        executors.execute_run(approved_execution_project, approved_run_spec)

    assert observed == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cycle_id", "cycle-other"),
        ("evaluation_spec_id", "eval-spec-other"),
        ("checkpoint_id", "evaluation-cycle-other"),
    ],
)
def test_resolved_checkpoint_identity_conflict_rejects_before_spawn(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
):
    executors = executor_module()
    checkpoint = read_json(
        approved_execution_project.cycles
        / "cycle-fixture"
        / "evaluation-comparison.json"
    )
    checkpoint[field] = value
    observed: list[str] = []
    monkeypatch.setattr(
        executors,
        "resolve_evaluation_cycle_checkpoint",
        lambda *args, **kwargs: checkpoint,
    )
    monkeypatch.setattr(
        executors,
        "reserve_budget",
        lambda *args, **kwargs: observed.append("reserve"),
    )
    monkeypatch.setattr(
        executors.subprocess,
        "Popen",
        lambda *args, **kwargs: observed.append("spawn"),
    )

    with pytest.raises(ValueError, match="checkpoint|cycle|EvaluationSpec"):
        executors.execute_run(approved_execution_project, approved_run_spec)

    assert observed == []


def test_checkpoint_id_is_immutable_across_all_run_evidence(
    approved_run_spec, approved_execution_project
):
    executors = executor_module()
    result = executors.execute_run(approved_execution_project, approved_run_spec)
    collected = executors.collect_run(
        approved_execution_project, approved_run_spec.run_id
    )
    checkpoint_id = approved_run_spec.evaluation_cycle_checkpoint_id
    run_root = approved_execution_project.runs / approved_run_spec.run_id
    evidence = [
        read_json(run_root / "state.json"),
        read_json(run_root / "command.json"),
        read_json(run_root / "environment.json"),
        read_json(
            Path(
                resolve_artifact(
                    approved_execution_project, result["receipt_artifact_id"]
                )["path"]
            )
        ),
        *collected["output_artifacts"],
    ]
    assert all(
        row["evaluation_cycle_checkpoint_id"] == checkpoint_id
        for row in evidence
    )
    for artifact_id in (
        result["stdout_artifact_id"],
        result["stderr_artifact_id"],
    ):
        assert resolve_artifact(
            approved_execution_project, artifact_id
        )["evaluation_cycle_checkpoint_id"] == checkpoint_id


def test_relabel_and_stale_cycle_evidence_are_rejected(
    approved_run_spec, approved_execution_project
):
    executors = executor_module()
    executors.execute_run(approved_execution_project, approved_run_spec)
    relabelled = replace(
        approved_run_spec,
        evaluation_cycle_checkpoint_id="evaluation-cycle-live-other",
    )

    with pytest.raises(ValueError, match="checkpoint|relabel"):
        executors.execute_run(approved_execution_project, relabelled)


def test_storage_is_classified_verified_and_settled_exactly_once(
    approved_run_spec, approved_execution_project
):
    executors = executor_module()
    ordinary = approved_execution_project.root / "ordinary.bin"
    weight = approved_execution_project.root / "weights.bin"
    preexisting = approved_execution_project.root / "preexisting.bin"
    preexisting.write_bytes(b"preexisting")
    spec = replace(
        approved_run_spec,
        argv=(
            sys.executable,
            "-c",
            (
                "from pathlib import Path;"
                f"Path({str(ordinary)!r}).write_bytes(b'ordinary');"
                f"Path({str(weight)!r}).write_bytes(b'model-weight')"
            ),
        ),
        input_paths=(*approved_run_spec.input_paths, preexisting),
        output_paths=(ordinary, weight),
        expected_artifact_types=("metrics", "model-weight"),
    )
    executors.execute_run(approved_execution_project, spec)

    first = executors.collect_run(approved_execution_project, spec.run_id)
    budget_after_first = read_json(approved_execution_project.budget)
    settlements_after_first = read_jsonl(
        approved_execution_project.budget_settlements
    )
    second = executors.collect_run(approved_execution_project, spec.run_id)

    assert second == first
    assert read_json(approved_execution_project.budget) == budget_after_first
    assert (
        read_jsonl(approved_execution_project.budget_settlements)
        == settlements_after_first
    )
    accounting = first["storage_accounting"]
    assert accounting["excluded_artifact_types"] == ["model-weight"]
    assert preexisting not in [
        Path(row["source_path"]) for row in accounting["charged_artifacts"]
    ]
    assert all(
        verify_artifact(approved_execution_project, row["artifact_id"])["valid"]
        for row in accounting["charged_artifacts"]
    )
    assert accounting["charged_bytes"] == sum(
        row["size_bytes"] for row in accounting["charged_artifacts"]
    )


@pytest.mark.parametrize(
    "corruption",
    ["hash", "size", "duplicate-artifact", "mislabeled-exclusion"],
)
def test_storage_corruption_rejects_without_double_charge(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
):
    executors = executor_module()
    executors.execute_run(approved_execution_project, approved_run_spec)
    before = read_json(approved_execution_project.budget)

    def reject_corrupt_evidence(*args, **kwargs):
        raise ValueError(f"storage artifact {corruption}")

    monkeypatch.setattr(
        executors,
        "verify_run_snapshot_artifacts",
        reject_corrupt_evidence,
    )
    with pytest.raises(ValueError, match="storage|artifact|duplicate"):
        executors.collect_run(approved_execution_project, approved_run_spec.run_id)

    assert read_json(approved_execution_project.budget) == before


@pytest.mark.parametrize(
    "phase",
    [
        "reserved",
        "spawned",
        "pid-persisted",
        "communicated",
        "artifacts-registered",
        "settlement-prepared",
    ],
)
def test_restart_recovery_is_idempotent_at_every_durable_phase(
    approved_run_spec,
    approved_execution_project,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
):
    executors = executor_module()
    original_write = executors.atomic_write_json

    def fail_at_boundary(path, payload):
        original_write(path, payload)
        if (
            path.name == "state.json"
            and payload.get("phase") == phase
        ):
            raise OSError(f"injected after {phase}")

    monkeypatch.setattr(
        executors, "atomic_write_json", fail_at_boundary
    )
    with pytest.raises(OSError, match=phase):
        executors.execute_run(approved_execution_project, approved_run_spec)
    monkeypatch.setattr(
        executors, "atomic_write_json", original_write
    )

    recovered = executors.execute_run(
        approved_execution_project, approved_run_spec
    )
    repeated = executors.execute_run(
        approved_execution_project, approved_run_spec
    )

    assert repeated == recovered
    assert len(
        [
            row
            for row in read_jsonl(
                approved_execution_project.budget_settlements
            )
            if row.get("run_id") == approved_run_spec.run_id
        ]
    ) == 1


def test_cleanup_requires_declared_temporary_path_and_explicit_action(
    approved_run_spec, approved_execution_project, tmp_path: Path
):
    executors = executor_module()
    temporary = approved_execution_project.root / "temporary-run"
    user_owned = approved_execution_project.root / "user-owned"
    temporary.mkdir()
    user_owned.mkdir()
    lock_path = Path(approved_run_spec.input_paths[0])
    spec = environment_spec(
        approved_execution_project,
        lock_path,
        allowed_write_paths=(temporary, user_owned),
        temporary_paths=(temporary,),
    )
    executors.prepare_environment(approved_execution_project, spec)

    run = replace(
        approved_run_spec,
        dependency_lock_hash=spec.dependency_lock_hash,
    )
    executors.execute_run(approved_execution_project, run)
    result = executors.cleanup_run(
        approved_execution_project, approved_run_spec.run_id
    )

    assert result["removed_paths"] == [str(temporary)]
    assert not temporary.exists()
    assert user_owned.is_dir()
    assert all(
        verify_artifact(approved_execution_project, row["artifact_id"])["valid"]
        for row in list_artifacts(approved_execution_project, {})
    )


def test_cleanup_uses_the_environment_receipt_bound_to_the_run(
    approved_run_spec, approved_execution_project
):
    executors = executor_module()
    original_temporary = (
        approved_execution_project.root / "original-temporary"
    )
    later_temporary = approved_execution_project.root / "later-temporary"
    original_temporary.mkdir()
    later_temporary.mkdir()
    lock_path = Path(approved_run_spec.input_paths[0])
    original_environment = environment_spec(
        approved_execution_project,
        lock_path,
        allowed_write_paths=(original_temporary,),
        temporary_paths=(original_temporary,),
    )
    executors.prepare_environment(
        approved_execution_project, original_environment
    )
    executors.execute_run(
        approved_execution_project,
        replace(
            approved_run_spec,
            dependency_lock_hash=(
                original_environment.dependency_lock_hash
            ),
        ),
    )
    executors.prepare_environment(
        approved_execution_project,
        environment_spec(
            approved_execution_project,
            lock_path,
            allowed_write_paths=(later_temporary,),
            temporary_paths=(later_temporary,),
        ),
    )

    result = executors.cleanup_run(
        approved_execution_project, approved_run_spec.run_id
    )

    assert result["removed_paths"] == [str(original_temporary)]
    assert not original_temporary.exists()
    assert later_temporary.is_dir()
