import importlib
from pathlib import Path

from researchctl import build_parser, main


SCRIPT = Path(__file__).parents[1] / "scripts" / "researchctl.py"


def test_controller_declares_only_approved_runtime_dependency():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '# dependencies = ["pyyaml>=6,<7"]' in text
    assert "def main(" in text


def test_contract_module_exposes_six_interfaces():
    contracts = Path(__file__).parents[1] / "scripts" / "autoresearch" / "contracts.py"
    text = contracts.read_text(encoding="utf-8")
    for name in (
        "ResearchState",
        "ArtifactStore",
        "TaskStageProtocol",
        "EnvironmentExecutor",
        "Evaluator",
        "SearchPolicy",
    ):
        assert f"class {name}(Protocol)" in text


def executor_module():
    return importlib.import_module("autoresearch.executors")


def test_task9_cli_contract_is_lazy_and_explicit():
    commands = (
        (
            "environment-inspect",
            ["--root", "/tmp/project", "--spec", "/tmp/environment.json"],
        ),
        (
            "environment-prepare",
            ["--root", "/tmp/project", "--spec", "/tmp/environment.json"],
        ),
        (
            "run",
            ["--root", "/tmp/project", "--spec", "/tmp/run.json"],
        ),
        (
            "run-cancel",
            ["--root", "/tmp/project", "--run-id", "run-1"],
        ),
        (
            "run-collect",
            ["--root", "/tmp/project", "--run-id", "run-1"],
        ),
        (
            "run-cleanup",
            ["--root", "/tmp/project", "--run-id", "run-1"],
        ),
    )
    executor_module()
    for command, arguments in commands:
        parsed = build_parser().parse_args([command, *arguments])
        assert parsed.command == command


def test_worker_journal_cli_contract_is_explicit():
    commands = (
        ("worker-event", ["--root", "/tmp/project", "--event", "/tmp/event.json"]),
        ("worker-status", ["--root", "/tmp/project"]),
        ("worker-reconcile", ["--root", "/tmp/project"]),
    )

    for command, arguments in commands:
        parsed = build_parser().parse_args([command, *arguments])
        assert parsed.command == command


def test_root_accepts_research_directory(project_layout):
    assert main(["validate", "--root", str(project_layout.root / ".research")]) == 0
    assert ".research directory" in (build_parser().description or "")


def test_reservation_detach_and_lightweight_commands_skip_preflight():
    # Recovery and lightweight append commands must not run the full
    # validate_project_integrity preflight, which would block recovery of the
    # exact state the preflight rejects.
    import researchctl

    assert "reservation-detach" not in researchctl._MUTATING_COMMANDS
    for command in ("record-call", "worker-event", "artifact-alias-bind"):
        assert command in researchctl._MUTATING_COMMANDS
        assert command in researchctl._LIGHTWEIGHT_MUTATING_COMMANDS


def test_reservation_detach_runs_on_invalid_project_state(approved_project):
    # reservation-detach is a recovery command and skips the full preflight,
    # so it must proceed even when state.json is tampered (which would make
    # validate_project_integrity fail).
    from autoresearch.governance import reserve_budget

    reserve_budget(
        approved_project, {"id": "run:detach-test", "wall_seconds": 5}
    )
    (approved_project.root / ".research" / "state.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    assert (
        main(
            [
                "reservation-detach",
                "--root",
                str(approved_project.root),
                "--reservation-id",
                "run:detach-test",
                "--reason",
                "telemetry unavailable",
            ]
        )
        == 0
    )
