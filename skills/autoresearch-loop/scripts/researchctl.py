# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6,<7"]
# ///

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from autoresearch.artifacts import (
    list_artifacts,
    bind_artifact_alias,
    mark_artifact_unavailable,
    register_artifact,
    register_artifact_version,
    resolve_artifact,
    resolve_artifact_alias,
    verify_artifact,
)
from autoresearch.candidates import (
    create_candidate,
    evaluate_stop_conditions,
    load_candidates,
    record_verdict,
    search_status as candidate_search_status,
    select_parallel_candidates,
    update_candidate,
)
from autoresearch.contracts import EnvironmentSpec, RunSpec
from autoresearch.executors import (
    cancel_run,
    cleanup_run,
    collect_run,
    execute_run,
    inspect_environment,
    prepare_environment,
)
from autoresearch.governance import (
    cancel_gate,
    reject_gate,
    record_gate_receipt,
    request_gate,
    reserve_budget,
    settle_budget,
    detach_budget_reservation,
)
from autoresearch.evaluation import (
    bind_evaluation_cycle_checkpoint,
    check_evaluation_report,
    lock_evaluation_spec,
    promote_current,
    promote_provisional,
)
from autoresearch.io import read_json
from autoresearch.project import (
    ProjectLayout,
    initialize_project,
    load_graph,
    load_research_state,
    validate_graph,
)
from autoresearch.records import append_model_call
from autoresearch.runtime_navigation import build_runtime_index, select_runtime_context
from autoresearch.worker_events import (
    reconcile_worker_reports,
    record_worker_event,
    worker_status,
)
from autoresearch.workflow import (
    apply_graph_revision,
    complete_task,
    create_checkpoint,
    open_task,
    request_graph_revision,
    transition_stage,
    inspect_pending_intents,
    quarantine_pending_intent,
    retry_pending_intent,
    validate_project_integrity,
)
from autoresearch.workflow.tasks import finalize_task_report


class ControllerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = ControllerArgumentParser(
        prog="researchctl",
        description="--root accepts either a project root or its .research directory.",
    )
    parser.add_argument("--version", action="version", version="researchctl 0.1.0")
    commands = parser.add_subparsers(dest="command")

    init_parser = commands.add_parser("init")
    init_parser.add_argument("--root", type=Path, required=True)
    init_parser.add_argument("--profile", type=Path, required=True)

    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--root", type=Path, required=True)

    status_parser = commands.add_parser("status")
    status_parser.add_argument("--root", type=Path, required=True)

    index_parser = commands.add_parser("runtime-index-build")
    index_parser.add_argument("--root", type=Path, required=True)
    index_parser.add_argument("--spec", type=Path, required=True)
    index_parser.add_argument("--parent-id")

    context_parser = commands.add_parser("runtime-context")
    context_parser.add_argument("--root", type=Path, required=True)
    context_parser.add_argument("--index-id", required=True)
    context_parser.add_argument("--query", required=True)
    context_parser.add_argument("--kind", choices=("entrypoint", "command", "artifact"))
    context_parser.add_argument("--limit", type=int, default=5)
    context_parser.add_argument("--max-chars", type=int, default=6000)

    gate_request_parser = commands.add_parser("gate-request")
    gate_request_parser.add_argument("--root", type=Path, required=True)
    gate_request_parser.add_argument(
        "--type",
        choices=(
            "modification",
            "budget",
            "graph",
            "charter",
            "evaluation",
            "publication",
        ),
        required=True,
    )
    gate_request_parser.add_argument("--payload", type=Path, required=True)

    gate_record_parser = commands.add_parser("gate-record")
    gate_record_parser.add_argument("--root", type=Path, required=True)
    gate_record_parser.add_argument("--gate-id", required=True)
    gate_record_parser.add_argument("--source", required=True)
    gate_record_parser.add_argument("--evidence", required=True)

    for command in ("gate-reject", "gate-cancel"):
        gate_decision_parser = commands.add_parser(command)
        gate_decision_parser.add_argument("--root", type=Path, required=True)
        gate_decision_parser.add_argument("--gate-id", required=True)
        gate_decision_parser.add_argument("--source", required=True)
        gate_decision_parser.add_argument("--reason", required=True)

    graph_revision_request_parser = commands.add_parser(
        "graph-revision-request"
    )
    graph_revision_request_parser.add_argument("--root", type=Path, required=True)
    graph_revision_request_parser.add_argument("--graph", type=Path, required=True)
    graph_revision_request_parser.add_argument("--reason", required=True)

    graph_revise_parser = commands.add_parser("graph-revise")
    graph_revise_parser.add_argument("--root", type=Path, required=True)
    graph_revise_parser.add_argument("--graph", type=Path)
    graph_revise_parser.add_argument("--gate-id", required=True)

    budget_reserve_parser = commands.add_parser("budget-reserve")
    budget_reserve_parser.add_argument("--root", type=Path, required=True)
    budget_reserve_parser.add_argument("--reservation", type=Path, required=True)

    budget_settle_parser = commands.add_parser("budget-settle")
    budget_settle_parser.add_argument("--root", type=Path, required=True)
    budget_settle_parser.add_argument("--reservation-id", required=True)
    budget_settle_parser.add_argument("--actual", type=Path, required=True)

    reservation_detach_parser = commands.add_parser("reservation-detach")
    reservation_detach_parser.add_argument("--root", type=Path, required=True)
    reservation_detach_parser.add_argument("--reservation-id", required=True)
    reservation_detach_parser.add_argument("--reason", required=True)

    intent_inspect_parser = commands.add_parser("intent-inspect")
    intent_inspect_parser.add_argument("--root", type=Path, required=True)
    intent_retry_parser = commands.add_parser("intent-retry")
    intent_retry_parser.add_argument("--root", type=Path, required=True)
    intent_retry_parser.add_argument("--intent-id", required=True)
    intent_quarantine_parser = commands.add_parser("intent-quarantine")
    intent_quarantine_parser.add_argument("--root", type=Path, required=True)
    intent_quarantine_parser.add_argument("--intent-id", required=True)
    intent_quarantine_parser.add_argument("--reason", required=True)

    environment_inspect_parser = commands.add_parser(
        "environment-inspect"
    )
    environment_inspect_parser.add_argument(
        "--root", type=Path, required=True
    )
    environment_inspect_parser.add_argument(
        "--spec", type=Path, required=True
    )

    environment_prepare_parser = commands.add_parser(
        "environment-prepare"
    )
    environment_prepare_parser.add_argument(
        "--root", type=Path, required=True
    )
    environment_prepare_parser.add_argument(
        "--spec", type=Path, required=True
    )

    run_parser = commands.add_parser("run")
    run_parser.add_argument("--root", type=Path, required=True)
    run_parser.add_argument("--spec", type=Path, required=True)

    for command in ("run-cancel", "run-collect", "run-cleanup"):
        run_action_parser = commands.add_parser(command)
        run_action_parser.add_argument("--root", type=Path, required=True)
        run_action_parser.add_argument("--run-id", required=True)

    artifact_register_parser = commands.add_parser("artifact-register")
    artifact_register_parser.add_argument("--root", type=Path, required=True)
    artifact_register_parser.add_argument("--path", required=True)
    artifact_register_parser.add_argument("--metadata", type=Path, required=True)
    artifact_register_parser.add_argument("--parent-id")

    artifact_resolve_parser = commands.add_parser("artifact-resolve")
    artifact_resolve_parser.add_argument("--root", type=Path, required=True)
    artifact_resolve_parser.add_argument("--artifact-id", required=True)

    artifact_list_parser = commands.add_parser("artifact-list")
    artifact_list_parser.add_argument("--root", type=Path, required=True)
    artifact_list_parser.add_argument("--task-id")
    artifact_list_parser.add_argument("--candidate-id")
    artifact_list_parser.add_argument("--run-id")
    artifact_list_parser.add_argument("--type")

    artifact_verify_parser = commands.add_parser("artifact-verify")
    artifact_verify_parser.add_argument("--root", type=Path, required=True)
    artifact_verify_parser.add_argument("--artifact-id", required=True)

    artifact_unavailable_parser = commands.add_parser("artifact-unavailable")
    artifact_unavailable_parser.add_argument("--root", type=Path, required=True)
    artifact_unavailable_parser.add_argument("--artifact-id", required=True)
    artifact_unavailable_parser.add_argument("--reason", required=True)

    artifact_alias_bind_parser = commands.add_parser("artifact-alias-bind")
    artifact_alias_bind_parser.add_argument("--root", type=Path, required=True)
    artifact_alias_bind_parser.add_argument("--type", required=True)
    artifact_alias_bind_parser.add_argument("--artifact-id", required=True)
    artifact_alias_resolve_parser = commands.add_parser("artifact-alias-resolve")
    artifact_alias_resolve_parser.add_argument("--root", type=Path, required=True)
    artifact_alias_resolve_parser.add_argument("--type", required=True)

    record_call_parser = commands.add_parser("record-call")
    record_call_parser.add_argument("--root", type=Path, required=True)
    record_call_parser.add_argument("--record", type=Path, required=True)

    task_open_parser = commands.add_parser("task-open")
    task_open_parser.add_argument("--root", type=Path, required=True)
    task_open_parser.add_argument("--spec", type=Path, required=True)

    task_complete_parser = commands.add_parser("task-complete")
    task_complete_parser.add_argument("--root", type=Path, required=True)
    task_complete_parser.add_argument("--task-id", required=True)
    task_complete_parser.add_argument("--report", type=Path, required=True)

    task_report_finalize_parser = commands.add_parser("task-report-finalize")
    task_report_finalize_parser.add_argument("--root", type=Path, required=True)
    task_report_finalize_parser.add_argument("--task-id", required=True)
    task_report_finalize_parser.add_argument(
        "--artifact-id", action="append", default=[]
    )

    worker_event_parser = commands.add_parser("worker-event")
    worker_event_parser.add_argument("--root", type=Path, required=True)
    worker_event_parser.add_argument("--event", type=Path, required=True)

    worker_status_parser = commands.add_parser("worker-status")
    worker_status_parser.add_argument("--root", type=Path, required=True)

    worker_reconcile_parser = commands.add_parser("worker-reconcile")
    worker_reconcile_parser.add_argument("--root", type=Path, required=True)

    checkpoint_parser = commands.add_parser("checkpoint")
    checkpoint_parser.add_argument("--root", type=Path, required=True)
    checkpoint_parser.add_argument("--reason", required=True)

    transition_parser = commands.add_parser("transition")
    transition_parser.add_argument("--root", type=Path, required=True)
    transition_parser.add_argument("--from", dest="source", required=True)
    transition_parser.add_argument("--to", dest="target", required=True)
    transition_parser.add_argument(
        "--expected-revision", type=int, required=True
    )

    candidate_create_parser = commands.add_parser("candidate-create")
    candidate_create_parser.add_argument("--root", type=Path, required=True)
    candidate_create_parser.add_argument("--proposal", type=Path, required=True)

    candidate_update_parser = commands.add_parser("candidate-update")
    candidate_update_parser.add_argument("--root", type=Path, required=True)
    candidate_update_parser.add_argument("--candidate-id", required=True)
    candidate_update_parser.add_argument("--patch", type=Path, required=True)

    candidate_verdict_parser = commands.add_parser("candidate-verdict")
    candidate_verdict_parser.add_argument("--root", type=Path, required=True)
    candidate_verdict_parser.add_argument("--candidate-id", required=True)
    candidate_verdict_parser.add_argument("--verdict", required=True)
    candidate_verdict_parser.add_argument("--evidence", nargs="+", default=[])

    candidate_select_parser = commands.add_parser("candidate-select")
    candidate_select_parser.add_argument("--root", type=Path, required=True)
    candidate_select_parser.add_argument("--slots", type=int, required=True)

    search_status_parser = commands.add_parser("search-status")
    search_status_parser.add_argument("--root", type=Path, required=True)

    evaluation_lock_parser = commands.add_parser("evaluation-lock")
    evaluation_lock_parser.add_argument("--root", type=Path, required=True)
    evaluation_lock_parser.add_argument("--spec", type=Path, required=True)

    evaluation_check_parser = commands.add_parser("evaluation-check")
    evaluation_check_parser.add_argument("--root", type=Path, required=True)
    evaluation_check_parser.add_argument("--report", type=Path, required=True)

    cycle_bind_parser = commands.add_parser("evaluation-cycle-bind")
    cycle_bind_parser.add_argument("--root", type=Path, required=True)
    cycle_bind_parser.add_argument("--cycle-id", required=True)

    provisional_parser = commands.add_parser("promote-provisional")
    provisional_parser.add_argument("--root", type=Path, required=True)
    provisional_parser.add_argument("--candidate-id", required=True)
    provisional_parser.add_argument("--report-id", required=True)

    current_parser = commands.add_parser("promote-current")
    current_parser.add_argument("--root", type=Path, required=True)
    current_parser.add_argument("--candidate-id", required=True)
    current_parser.add_argument(
        "--robustness-report", nargs="+", required=True
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
        if hasattr(arguments, "root"):
            arguments.root = _normalize_project_root(arguments.root)
        if arguments.command in _MUTATING_COMMANDS and (
            arguments.command not in _LIGHTWEIGHT_MUTATING_COMMANDS
        ):
            _require_mutation_integrity(arguments.root)
        if arguments.command == "init":
            return _init(arguments.root, arguments.profile)
        if arguments.command == "validate":
            return _validate(arguments.root)
        if arguments.command == "status":
            return _status(arguments.root)
        if arguments.command == "runtime-index-build":
            _emit(build_runtime_index(
                ProjectLayout.from_root(arguments.root),
                read_json(arguments.spec), parent_id=arguments.parent_id,
            ))
            return 0
        if arguments.command == "runtime-context":
            _emit(select_runtime_context(
                ProjectLayout.from_root(arguments.root), arguments.index_id,
                arguments.query, kind=arguments.kind, limit=arguments.limit,
                max_chars=arguments.max_chars,
            ))
            return 0
        if arguments.command == "gate-request":
            return _gate_request(
                arguments.root, arguments.type, arguments.payload
            )
        if arguments.command == "gate-record":
            return _gate_record(
                arguments.root,
                arguments.gate_id,
                arguments.source,
                arguments.evidence,
            )
        if arguments.command == "gate-reject":
            return _gate_reject(
                arguments.root,
                arguments.gate_id,
                arguments.source,
                arguments.reason,
            )
        if arguments.command == "gate-cancel":
            return _gate_cancel(
                arguments.root,
                arguments.gate_id,
                arguments.source,
                arguments.reason,
            )
        if arguments.command == "graph-revision-request":
            return _graph_revision_request(
                arguments.root, arguments.graph, arguments.reason
            )
        if arguments.command == "graph-revise":
            return _graph_revise(
                arguments.root, arguments.graph, arguments.gate_id
            )
        if arguments.command == "budget-reserve":
            return _budget_reserve(arguments.root, arguments.reservation)
        if arguments.command == "budget-settle":
            return _budget_settle(
                arguments.root, arguments.reservation_id, arguments.actual
            )
        if arguments.command == "reservation-detach":
            return _reservation_detach(
                arguments.root, arguments.reservation_id, arguments.reason
            )
        if arguments.command == "intent-inspect":
            return _intent_inspect(arguments.root)
        if arguments.command == "intent-retry":
            return _intent_retry(arguments.root, arguments.intent_id)
        if arguments.command == "intent-quarantine":
            return _intent_quarantine(
                arguments.root, arguments.intent_id, arguments.reason
            )
        if arguments.command == "environment-inspect":
            return _environment_inspect(arguments.root, arguments.spec)
        if arguments.command == "environment-prepare":
            return _environment_prepare(arguments.root, arguments.spec)
        if arguments.command == "run":
            return _run(arguments.root, arguments.spec)
        if arguments.command == "run-cancel":
            return _run_cancel(arguments.root, arguments.run_id)
        if arguments.command == "run-collect":
            return _run_collect(arguments.root, arguments.run_id)
        if arguments.command == "run-cleanup":
            return _run_cleanup(arguments.root, arguments.run_id)
        if arguments.command == "artifact-register":
            return _artifact_register(
                arguments.root,
                arguments.path,
                arguments.metadata,
                arguments.parent_id,
            )
        if arguments.command == "artifact-resolve":
            return _artifact_resolve(arguments.root, arguments.artifact_id)
        if arguments.command == "artifact-list":
            return _artifact_list(
                arguments.root,
                {
                    field: value
                    for field, value in (
                        ("task_id", arguments.task_id),
                        ("candidate_id", arguments.candidate_id),
                        ("run_id", arguments.run_id),
                        ("type", arguments.type),
                    )
                    if value is not None
                },
            )
        if arguments.command == "artifact-verify":
            return _artifact_verify(arguments.root, arguments.artifact_id)
        if arguments.command == "artifact-unavailable":
            return _artifact_unavailable(
                arguments.root, arguments.artifact_id, arguments.reason
            )
        if arguments.command == "artifact-alias-bind":
            return _artifact_alias_bind(
                arguments.root, arguments.type, arguments.artifact_id
            )
        if arguments.command == "artifact-alias-resolve":
            return _artifact_alias_resolve(arguments.root, arguments.type)
        if arguments.command == "record-call":
            return _record_call(arguments.root, arguments.record)
        if arguments.command == "task-open":
            return _task_open(arguments.root, arguments.spec)
        if arguments.command == "task-complete":
            return _task_complete(
                arguments.root, arguments.task_id, arguments.report
            )
        if arguments.command == "task-report-finalize":
            return _task_report_finalize(
                arguments.root, arguments.task_id, arguments.artifact_id
            )
        if arguments.command == "worker-event":
            return _worker_event(arguments.root, arguments.event)
        if arguments.command == "worker-status":
            return _worker_status(arguments.root)
        if arguments.command == "worker-reconcile":
            return _worker_reconcile(arguments.root)
        if arguments.command == "checkpoint":
            return _checkpoint(arguments.root, arguments.reason)
        if arguments.command == "transition":
            return _transition(
                arguments.root,
                arguments.source,
                arguments.target,
                arguments.expected_revision,
            )
        if arguments.command == "candidate-create":
            return _candidate_create(arguments.root, arguments.proposal)
        if arguments.command == "candidate-update":
            return _candidate_update(
                arguments.root, arguments.candidate_id, arguments.patch
            )
        if arguments.command == "candidate-verdict":
            return _candidate_verdict(
                arguments.root,
                arguments.candidate_id,
                arguments.verdict,
                arguments.evidence,
            )
        if arguments.command == "candidate-select":
            return _candidate_select(arguments.root, arguments.slots)
        if arguments.command == "search-status":
            return _search_status(arguments.root)
        if arguments.command == "evaluation-lock":
            return _evaluation_lock(arguments.root, arguments.spec)
        if arguments.command == "evaluation-check":
            return _evaluation_check(arguments.root, arguments.report)
        if arguments.command == "evaluation-cycle-bind":
            return _evaluation_cycle_bind(
                arguments.root, arguments.cycle_id
            )
        if arguments.command == "promote-provisional":
            return _promote_provisional(
                arguments.root,
                arguments.candidate_id,
                arguments.report_id,
            )
        if arguments.command == "promote-current":
            return _promote_current(
                arguments.root,
                arguments.candidate_id,
                arguments.robustness_report,
            )
        return 0
    except (OSError, PermissionError, ValueError) as error:
        _emit({"status": "error", "error": str(error)})
        return 1


def _init(root: Path, profile: Path) -> int:
    try:
        layout = initialize_project(root, profile)
    except ValueError as error:
        _emit({"status": "invalid", "errors": [str(error)]})
        return 1
    _emit({"status": "initialized", "root": str(layout.root)})
    return 0


_MUTATING_COMMANDS = frozenset(
    {
        "gate-request", "gate-record", "gate-reject", "gate-cancel",
        "graph-revision-request", "budget-reserve",
        "budget-settle", "environment-prepare", "run",
        "run-cancel", "run-collect", "run-cleanup", "artifact-register",
        "artifact-unavailable", "artifact-alias-bind", "record-call", "task-open", "task-complete",
        "task-report-finalize", "worker-event", "worker-reconcile", "checkpoint",
        "runtime-index-build", "runtime-context",
        "transition", "candidate-create", "candidate-update", "candidate-verdict", "candidate-select",
        "evaluation-lock", "evaluation-cycle-bind", "promote-provisional",
        "promote-current",
    }
)
# Recovery commands (reservation-detach, intent-*) bypass preflight because
# they exist to repair exactly the state a full preflight would reject.
# Append-only record/event binds are low-risk and skip the full preflight so
# lightweight appends do not block on unrelated pending recovery.
_LIGHTWEIGHT_MUTATING_COMMANDS = frozenset(
    {"record-call", "worker-event", "artifact-alias-bind"}
)


def _require_mutation_integrity(root: Path) -> None:
    errors = validate_project_integrity(ProjectLayout.from_root(root))
    if errors:
        raise ValueError("project integrity preflight failed: " + "; ".join(errors))


def _normalize_project_root(root: Path) -> Path:
    if root.name == ".research":
        return root.parent
    return root


def _validate(root: Path) -> int:
    layout = ProjectLayout.from_root(root)
    errors = validate_project_integrity(layout)
    status = "valid" if not errors else "invalid"
    _emit({"status": status, "errors": errors})
    return int(bool(errors))


def _status(root: Path) -> int:
    layout = ProjectLayout.from_root(root)
    errors = validate_project_integrity(layout)
    state = load_research_state(layout) if not errors else {}
    budget = read_json(layout.budget)
    status = "ok" if not errors else "invalid"
    _emit(
        {
            "status": status,
            "root": str(layout.root),
            "revision": state.get("revision"),
            "current_stage": state.get("current_stage"),
            "budget_status": budget.get("status"),
            "graph_errors": errors,
        }
    )
    return int(bool(errors))


def _gate_request(root: Path, gate_type: str, payload_path: Path) -> int:
    result = request_gate(
        ProjectLayout.from_root(root), gate_type, read_json(payload_path)
    )
    _emit(result)
    return 0


def _gate_record(
    root: Path, gate_id: str, source: str, evidence: str
) -> int:
    result = record_gate_receipt(
        ProjectLayout.from_root(root),
        gate_id,
        {"source": source, "evidence": evidence},
    )
    _emit(result)
    return 0


def _gate_reject(root: Path, gate_id: str, source: str, reason: str) -> int:
    result = reject_gate(
        ProjectLayout.from_root(root),
        gate_id,
        {"source": source, "reason": reason},
    )
    _emit(result)
    return 0


def _gate_cancel(root: Path, gate_id: str, source: str, reason: str) -> int:
    result = cancel_gate(
        ProjectLayout.from_root(root),
        gate_id,
        {"source": source, "reason": reason},
    )
    _emit(result)
    return 0


def _graph_revision_request(root: Path, graph: Path, reason: str) -> int:
    result = request_graph_revision(ProjectLayout.from_root(root), graph, reason)
    _emit(result)
    return 0


def _graph_revise(root: Path, graph: Path | None, gate_id: str) -> int:
    result = apply_graph_revision(ProjectLayout.from_root(root), graph, gate_id)
    _emit(result)
    return 0


def _budget_reserve(root: Path, reservation_path: Path) -> int:
    result = reserve_budget(
        ProjectLayout.from_root(root), read_json(reservation_path)
    )
    _emit(result)
    return 0


def _budget_settle(
    root: Path, reservation_id: str, actual_path: Path
) -> int:
    result = settle_budget(
        ProjectLayout.from_root(root),
        reservation_id,
        read_json(actual_path),
    )
    _emit(result)
    return 0


def _reservation_detach(root: Path, reservation_id: str, reason: str) -> int:
    _emit(detach_budget_reservation(ProjectLayout.from_root(root), reservation_id, reason))
    return 0


def _intent_inspect(root: Path) -> int:
    _emit({"pending_intents": inspect_pending_intents(ProjectLayout.from_root(root))})
    return 0


def _intent_retry(root: Path, intent_id: str) -> int:
    _emit(retry_pending_intent(ProjectLayout.from_root(root), intent_id))
    return 0


def _intent_quarantine(root: Path, intent_id: str, reason: str) -> int:
    _emit(quarantine_pending_intent(ProjectLayout.from_root(root), intent_id, reason))
    return 0


def _environment_inspect(root: Path, spec_path: Path) -> int:
    layout = ProjectLayout.from_root(root)
    result = inspect_environment(
        layout, _environment_spec(layout, spec_path)
    )
    _emit(result)
    return int(result.get("status") == "failed")


def _environment_prepare(root: Path, spec_path: Path) -> int:
    layout = ProjectLayout.from_root(root)
    result = prepare_environment(
        layout, _environment_spec(layout, spec_path)
    )
    _emit(result)
    return int(result.get("status") != "prepared")


def _run(root: Path, spec_path: Path) -> int:
    layout = ProjectLayout.from_root(root)
    result = execute_run(layout, _run_spec(layout, spec_path))
    _emit(result)
    return 0


def _run_cancel(root: Path, run_id: str) -> int:
    result = cancel_run(ProjectLayout.from_root(root), run_id)
    _emit(result)
    return 0


def _run_collect(root: Path, run_id: str) -> int:
    result = collect_run(ProjectLayout.from_root(root), run_id)
    _emit(result)
    return 0


def _run_cleanup(root: Path, run_id: str) -> int:
    result = cleanup_run(ProjectLayout.from_root(root), run_id)
    _emit(result)
    return 0


def _artifact_register(
    root: Path,
    path: str,
    metadata_path: Path,
    parent_id: str | None,
) -> int:
    layout = ProjectLayout.from_root(root)
    metadata = read_json(metadata_path)
    if parent_id is None:
        result = register_artifact(layout, path, metadata)
    else:
        result = register_artifact_version(
            layout, parent_id, path, metadata
        )
    _emit(result)
    return 0


def _artifact_resolve(root: Path, artifact_id: str) -> int:
    result = resolve_artifact(ProjectLayout.from_root(root), artifact_id)
    _emit(result)
    return 0


def _artifact_list(root: Path, filters: Mapping[str, str]) -> int:
    result = list_artifacts(ProjectLayout.from_root(root), filters)
    _emit({"artifacts": result})
    return 0


def _artifact_verify(root: Path, artifact_id: str) -> int:
    result = verify_artifact(ProjectLayout.from_root(root), artifact_id)
    _emit(result)
    return 0


def _artifact_unavailable(
    root: Path, artifact_id: str, reason: str
) -> int:
    result = mark_artifact_unavailable(
        ProjectLayout.from_root(root), artifact_id, reason
    )
    _emit(result)
    return 0


def _artifact_alias_bind(root: Path, output_type: str, artifact_id: str) -> int:
    _emit(bind_artifact_alias(ProjectLayout.from_root(root), output_type, artifact_id))
    return 0


def _artifact_alias_resolve(root: Path, output_type: str) -> int:
    _emit(
        {
            "logical_output_type": output_type,
            "artifacts": resolve_artifact_alias(
                ProjectLayout.from_root(root), output_type
            ),
        }
    )
    return 0


def _record_call(root: Path, record_path: Path) -> int:
    result = append_model_call(
        ProjectLayout.from_root(root), read_json(record_path)
    )
    _emit(result)
    return 0


def _task_open(root: Path, spec_path: Path) -> int:
    result = open_task(
        ProjectLayout.from_root(root), read_json(spec_path)
    )
    _emit(result)
    return 0


def _task_complete(
    root: Path, task_id: str, report_path: Path
) -> int:
    result = complete_task(
        ProjectLayout.from_root(root), task_id, report_path
    )
    _emit(result)
    return 0


def _task_report_finalize(
    root: Path, task_id: str, artifact_ids: list[str]
) -> int:
    result = finalize_task_report(
        ProjectLayout.from_root(root), task_id, artifact_ids
    )
    _emit(result)
    return 0


def _worker_event(root: Path, event_path: Path) -> int:
    result = record_worker_event(
        ProjectLayout.from_root(root), read_json(event_path)
    )
    _emit(result)
    return 0


def _worker_status(root: Path) -> int:
    _emit(worker_status(ProjectLayout.from_root(root)))
    return 0


def _worker_reconcile(root: Path) -> int:
    _emit(reconcile_worker_reports(ProjectLayout.from_root(root)))
    return 0


def _checkpoint(root: Path, reason: str) -> int:
    result = create_checkpoint(ProjectLayout.from_root(root), reason)
    _emit(result)
    return 0


def _transition(
    root: Path,
    source: str,
    target: str,
    expected_revision: int,
) -> int:
    result = transition_stage(
        ProjectLayout.from_root(root),
        source,
        target,
        expected_revision,
    )
    _emit(
        {
            "status": result.status,
            "state_revision": result.state_revision,
            "receipt_path": (
                str(result.receipt_path)
                if result.receipt_path is not None
                else None
            ),
            "reason": result.reason,
        }
    )
    return int(result.status != "applied")


def _candidate_create(root: Path, proposal_path: Path) -> int:
    result = create_candidate(
        ProjectLayout.from_root(root), read_json(proposal_path)
    )
    _emit(result)
    return 0


def _candidate_update(root: Path, candidate_id: str, patch_path: Path) -> int:
    result = update_candidate(
        ProjectLayout.from_root(root), candidate_id, read_json(patch_path)
    )
    _emit(result)
    return 0


def _candidate_verdict(
    root: Path, candidate_id: str, verdict: str, evidence_ids: Sequence[str]
) -> int:
    result = record_verdict(
        ProjectLayout.from_root(root), candidate_id, verdict, evidence_ids
    )
    _emit(result)
    return 0


def _candidate_select(root: Path, slots: int) -> int:
    layout = ProjectLayout.from_root(root)
    candidates = list(load_candidates(layout).values())
    debug_status = candidate_search_status(layout)
    blocked = set(debug_status["blocked_candidate_ids"])
    selectable = [
        candidate
        for candidate in candidates
        if candidate["candidate_id"] not in blocked
    ]
    reasons = evaluate_stop_conditions(
        load_research_state(layout), read_json(layout.budget), selectable
    )
    _emit(
        {
            "blocked_candidate_ids": debug_status["blocked_candidate_ids"],
            "candidates": []
            if reasons
            else select_parallel_candidates(selectable, slots),
            "pending_debug_obligations": debug_status[
                "pending_debug_obligations"
            ],
            "stop_reasons": reasons,
        }
    )
    return 0


def _search_status(root: Path) -> int:
    layout = ProjectLayout.from_root(root)
    candidates = list(load_candidates(layout).values())
    debug_status = candidate_search_status(layout)
    blocked = set(debug_status["blocked_candidate_ids"])
    selectable = [
        candidate
        for candidate in candidates
        if candidate["candidate_id"] not in blocked
    ]
    reasons = evaluate_stop_conditions(
        load_research_state(layout), read_json(layout.budget), selectable
    )
    _emit(
        {
            "blocked_candidate_ids": debug_status["blocked_candidate_ids"],
            "candidate_count": len(candidates),
            "pending_debug_obligations": debug_status[
                "pending_debug_obligations"
            ],
            "pending_debug_parent_ids": debug_status[
                "pending_debug_parent_ids"
            ],
            "stop_reasons": reasons,
            "status": "stopped" if reasons else "ready",
        }
    )
    return 0


def _evaluation_lock(root: Path, spec_path: Path) -> int:
    result = lock_evaluation_spec(
        ProjectLayout.from_root(root), read_json(spec_path)
    )
    _emit(result)
    return 0


def _evaluation_check(root: Path, report_path: Path) -> int:
    failures = check_evaluation_report(
        ProjectLayout.from_root(root), read_json(report_path)
    )
    _emit(
        {
            "failures": failures,
            "status": "passing" if not failures else "failing",
        }
    )
    return int(bool(failures))


def _evaluation_cycle_bind(root: Path, cycle_id: str) -> int:
    result = bind_evaluation_cycle_checkpoint(
        ProjectLayout.from_root(root), cycle_id
    )
    _emit(result)
    return 0


def _promote_provisional(
    root: Path, candidate_id: str, report_id: str
) -> int:
    result = promote_provisional(
        ProjectLayout.from_root(root), candidate_id, report_id
    )
    _emit(result)
    return 0


def _promote_current(
    root: Path,
    candidate_id: str,
    robustness_report_ids: Sequence[str],
) -> int:
    result = promote_current(
        ProjectLayout.from_root(root),
        candidate_id,
        robustness_report_ids,
    )
    _emit(result)
    return 0


def _emit(value: Mapping[str, Any]) -> None:
    print(json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")))


def _environment_spec(
    layout: ProjectLayout, spec_path: Path
) -> EnvironmentSpec:
    payload = _exact_spec(
        read_json(spec_path),
        {
            "environment_id",
            "backend",
            "cwd",
            "prepare_argv",
            "dependencies",
            "dependency_lock_path",
            "dependency_lock_hash",
            "allowed_write_paths",
            "temporary_paths",
            "timeout_seconds",
        },
        "EnvironmentSpec",
    )
    return EnvironmentSpec(
        environment_id=_required_string(
            payload, "environment_id", "EnvironmentSpec"
        ),
        backend=_required_string(payload, "backend", "EnvironmentSpec"),
        cwd=_project_path(layout, payload.get("cwd"), "cwd"),
        prepare_argv=tuple(
            _required_string_list(
                payload, "prepare_argv", "EnvironmentSpec"
            )
        ),
        dependencies=tuple(
            _required_string_list(
                payload, "dependencies", "EnvironmentSpec"
            )
        ),
        dependency_lock_path=_project_path(
            layout,
            payload.get("dependency_lock_path"),
            "dependency_lock_path",
        ),
        dependency_lock_hash=_required_string(
            payload, "dependency_lock_hash", "EnvironmentSpec"
        ),
        allowed_write_paths=tuple(
            _project_path(layout, value, "allowed_write_paths")
            for value in _required_string_list(
                payload, "allowed_write_paths", "EnvironmentSpec"
            )
        ),
        temporary_paths=tuple(
            _project_path(layout, value, "temporary_paths")
            for value in _required_string_list(
                payload, "temporary_paths", "EnvironmentSpec"
            )
        ),
        timeout_seconds=payload["timeout_seconds"],
    )


def _run_spec(layout: ProjectLayout, spec_path: Path) -> RunSpec:
    payload = _exact_spec(
        read_json(spec_path),
        {
            "run_id",
            "environment_id",
            "dependency_lock_hash",
            "backend",
            "cwd",
            "argv",
            "timeout_seconds",
            "resource_limits",
            "input_paths",
            "output_paths",
            "expected_artifact_types",
            "candidate_id",
            "evaluation_spec_id",
            "evaluation_cycle_checkpoint_id",
        },
        "RunSpec",
    )
    resource_limits = payload.get("resource_limits")
    if not isinstance(resource_limits, Mapping):
        raise ValueError("RunSpec.resource_limits must be an object")
    return RunSpec(
        run_id=_required_string(payload, "run_id", "RunSpec"),
        environment_id=_required_string(
            payload, "environment_id", "RunSpec"
        ),
        dependency_lock_hash=_required_string(
            payload, "dependency_lock_hash", "RunSpec"
        ),
        backend=_required_string(payload, "backend", "RunSpec"),
        cwd=_project_path(layout, payload.get("cwd"), "cwd"),
        argv=tuple(_required_string_list(payload, "argv", "RunSpec")),
        timeout_seconds=payload["timeout_seconds"],
        resource_limits=dict(resource_limits),
        input_paths=tuple(
            _project_path(layout, value, "input_paths")
            for value in _required_string_list(
                payload, "input_paths", "RunSpec"
            )
        ),
        output_paths=tuple(
            _project_path(layout, value, "output_paths")
            for value in _required_string_list(
                payload, "output_paths", "RunSpec"
            )
        ),
        expected_artifact_types=tuple(
            _required_string_list(
                payload, "expected_artifact_types", "RunSpec"
            )
        ),
        candidate_id=_required_string(
            payload, "candidate_id", "RunSpec"
        ),
        evaluation_spec_id=_required_string(
            payload, "evaluation_spec_id", "RunSpec"
        ),
        evaluation_cycle_checkpoint_id=_required_string(
            payload, "evaluation_cycle_checkpoint_id", "RunSpec"
        ),
    )


def _exact_spec(
    payload: Mapping[str, Any], expected: set[str], name: str
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{name} must be an object")
    missing = expected - payload.keys()
    unknown = payload.keys() - expected
    if missing:
        raise ValueError(f"{name} missing field: {sorted(missing)[0]}")
    if unknown:
        raise ValueError(f"{name} unknown field: {sorted(unknown)[0]}")
    return dict(payload)


def _required_string(
    payload: Mapping[str, Any], field: str, name: str
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name}.{field} must be a non-empty string")
    return value


def _required_string_list(
    payload: Mapping[str, Any], field: str, name: str
) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(
            f"{name}.{field} must contain non-empty strings"
        )
    return list(value)


def _project_path(layout: ProjectLayout, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty path")
    path = Path(value)
    return (path if path.is_absolute() else layout.root / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
