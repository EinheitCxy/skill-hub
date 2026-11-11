from __future__ import annotations

import ast
from pathlib import Path

from autoresearch import evaluation, workflow


WORKFLOW_ROOT = (
    Path(__file__).parents[1]
    / "scripts"
    / "autoresearch"
    / "workflow"
)
WORKFLOW_MODULES = frozenset(
    {
        "checkpoints",
        "mutations",
        "promotions",
        "recovery",
        "revisions",
        "stages",
        "tasks",
    }
)
EXPECTED_WORKFLOW_EXPORTS = frozenset(
    {
        "bind_evaluation_cycle_checkpoint",
        "apply_graph_revision",
        "complete_task",
        "create_checkpoint",
        "initialize_baseline_state",
        "inspect_pending_intents",
        "open_task",
        "promote_current_best",
        "promote_provisional_best",
        "request_graph_revision",
        "quarantine_pending_intent",
        "resolve_evaluation_cycle_checkpoint",
        "set_active_evaluation_spec",
        "state_mutation_transaction_id",
        "retry_pending_intent",
        "transition_stage",
        "validate_active_evaluation_spec",
        "validate_baseline_state_commit",
        "validate_evaluation_cycle_checkpoint",
        "validate_project_integrity",
        "validate_report",
    }
)
EXPECTED_WORKFLOW_SYMBOLS = {
    "checkpoints": frozenset(
        """
        EVALUATION_CYCLE_BIND_INTENT_FIELDS
        EVALUATION_CYCLE_CHECKPOINT_FIELDS
        EVALUATION_CYCLE_IDENTITY_FIELDS
        CONTROL_SNAPSHOT_IDENTITY_FIELDS
        _build_checkpoint
        _create_checkpoint_unlocked
        _find_evaluation_cycle_artifact
        _find_matching_control_checkpoint
        _require_evaluation_cycle_artifact
        _resume_evaluation_cycle_bind
        _validate_current_best_comparison_evidence
        _validate_evaluation_cycle_bind_intent
        _validate_evaluation_spec_anchor
        bind_evaluation_cycle_checkpoint
        create_checkpoint
        resolve_evaluation_cycle_checkpoint
        validate_evaluation_cycle_checkpoint
        """.split()
    ),
    "mutations": frozenset(
        """
        _apply_state_mutation
        _resume_state_mutation
        initialize_baseline_state
        set_active_evaluation_spec
        validate_active_evaluation_spec
        validate_baseline_state_commit
        """.split()
    ),
    "promotions": frozenset(
        """
        promote_current_best
        promote_provisional_best
        """.split()
    ),
    "recovery": frozenset(
        """
        CHECKPOINT_FIELDS
        GRAPH_REVISION_CHECKPOINT_FIELDS
        GRAPH_REVISION_INTENT_FIELDS
        GRAPH_REVISION_PHASES
        GRAPH_REVISION_RECEIPT_FIELDS
        HEADING_PATTERN
        LEDGER_COUNT_FIELDS
        SAFE_ID_PATTERN
        STATE_MUTATION_INTENT_FIELDS
        STATE_MUTATION_RECEIPT_FIELDS
        TRANSITION_INTENT_FIELDS
        TRANSITION_RECEIPT_FIELDS
        _DuplicateYamlKeyError
        _UniqueKeySafeLoader
        _atomic_copy
        _atomic_json_hash
        _atomic_write_research_state
        _atomic_write_text
        _capture_graph
        _committed_graph_revision_intent_for_receipt
        _committed_state_mutation_intent_for_receipt
        _committed_transition_intent_for_receipt
        _construct_unique_mapping
        _exclusive_lock
        _graph_stage
        _graph_revision_transaction_identity
        _hash_payload
        _historical_state_mutation_graph_anchor
        _historical_transition_graph_anchor
        _is_rfc3339
        _json_copy
        _nonnegative_number
        _reject_other_pending_intents
        _read_graph_revision_text
        _require_exact_fields
        _require_nonnegative_integer
        _require_sha256
        _required_string
        _safe_id
        _same_state_mutation_patch
        _source_revision_graph_anchor
        _state_integrity_error
        _state_mutation_identity_patch
        _string_list
        _timestamp
        _transition_intent_path
        _transition_transaction_id
        _validate_intent_header
        _validate_graph_revision_evidence
        _validate_state_mutation_evidence
        _validate_state_mutation_ledger_counts
        _validate_transition_evidence
        _validate_transition_intent_identity
        _validate_transition_ledger_counts
        _validate_reservation_task_consistency
        inspect_pending_intents
        quarantine_pending_intent
        retry_pending_intent
        _workflow_lock_path
        _workflow_transactions_root
        state_mutation_transaction_id
        validate_project_integrity
        """.split()
    ),
    "revisions": frozenset(
        """
        _existing_revision_intent
        _graph_revision_transaction_id
        _read_graph_document
        _read_graph_text
        _reject_open_tasks
        _resume_graph_revision
        _set_phase
        _validate_graph_revision_intent
        apply_graph_revision
        request_graph_revision
        """.split()
    ),
    "stages": frozenset(
        """
        _applicable_gate_error
        _coordinator_gate_conflict_error
        _coordinator_gate_payload
        _current_revision_or_invalid
        _required_task_artifact_error
        _resume_transition
        transition_stage
        """.split()
    ),
    "tasks": frozenset(
        """
        COMPLETION_INTENT_FIELDS
        COMPLETION_RECEIPT_FIELDS
        COMPLETION_RESULT_FIELDS
        CONTEXT_ARTIFACT_FIELDS
        DECISION_ADDENDUM_HEADING_PATTERN
        REPORT_FIELDS
        REPORT_HEADINGS
        REPORT_STATUSES
        SUBAGENT_MODELS
        TASK_REPORT_ARTIFACT_FIELDS
        TASK_SPEC_FIELDS
        _aggregate_task_usage
        _canonical_budget_counter
        _completion_intent_path
        _completion_transaction_id
        _context_manifest
        _ensure_completion_artifact
        _ensure_completion_receipt
        _ensure_completion_settlement
        _ensure_completion_task_state
        _expected_completion_evidence
        _find_task
        _is_decision_addendum_heading
        _is_within
        _matching_task_report_artifacts
        _normalize_project_relative_path
        _normalize_task_spec
        _parse_report
        _parse_report_text
        _populate_completion_result
        _prepare_completion_intent
        _prepare_settlement_evidence
        _reject_existing_task
        _reject_symlink_components
        _remove_partial_task
        _render_brief
        _render_finalized_report
        _report_body_errors
        _require_subagent_report_ready
        _resume_completion
        _settlement_evidence_from_budget
        _settlement_evidence_from_receipt
        _strict_relative_path
        _subtract_exact
        _task_records
        _trusted_task_before
        _validate_active_task_limit
        _validate_completion_against_sources
        _validate_completion_intent_identity
        _validate_completion_settlement_binding
        _validate_completion_settlement_source
        _validate_completion_usage
        _validate_declared_stage_outputs
        _validate_editable_envelope
        _validate_task_stage_and_limits
        _validate_task_usage_within_slice
        _validated_report_output_artifacts
        complete_task
        finalize_task_report
        open_task
        validate_report
        """.split()
    ),
}
EXPECTED_WORKFLOW_EDGES = {
    "checkpoints": frozenset({"recovery"}),
    "mutations": frozenset({"checkpoints", "recovery"}),
    "promotions": frozenset({"mutations", "recovery"}),
    "recovery": frozenset(),
    "revisions": frozenset({"checkpoints", "recovery"}),
    "stages": frozenset({"checkpoints", "recovery", "tasks"}),
    "tasks": frozenset({"recovery"}),
}
EVALUATION_ROOT = (
    Path(__file__).parents[1]
    / "scripts"
    / "autoresearch"
    / "evaluation"
)
EVALUATION_MODULES = frozenset(
    {"hard_gates", "pareto", "promotion", "reports", "spec"}
)
EXPECTED_EVALUATION_EXPORTS = frozenset(
    {
        "bind_evaluation_cycle_checkpoint",
        "check_evaluation_report",
        "evaluate_hard_gates",
        "lock_evaluation_spec",
        "pareto_frontier",
        "promote_current",
        "promote_provisional",
        "validate_evaluation_cycle_checkpoint",
    }
)
EXPECTED_EVALUATION_SYMBOLS = {
    "hard_gates": frozenset(
        {
            "_first_hard_gate_failure",
            "evaluate_hard_gates",
        }
    ),
    "pareto": frozenset(
        {
            "PARETO_HIGHER_IS_BETTER",
            "PARETO_LOWER_IS_BETTER",
            "_dominates",
            "_normalize_pareto_report",
            "pareto_frontier",
        }
    ),
    "promotion": frozenset(
        {
            "_artifact_ids",
            "_official_candidate",
            "_require_pareto_membership",
            "_unacceptable_regression_violations",
            "_validate_official_evaluation",
            "promote_current",
            "promote_provisional",
        }
    ),
    "reports": frozenset(
        {
            "_validate_evaluation_evidence",
            "_validate_report_checkpoint",
            "_verified_json_artifact",
            "_verified_json_payload",
            "check_evaluation_report",
        }
    ),
    "spec": frozenset(
        {
            "EVALUATION_SPEC_FIELDS",
            "EVALUATION_SPEC_OPTIONAL_FIELDS",
            "HARD_GATES",
            "METRIC_FIELDS",
            "PROMOTION_RULE_FIELDS",
            "UNACCEPTABLE_REGRESSION_RULE_FIELDS",
            "_approved_spec_hash",
            "_canonical_payload_hash",
            "_finite_number",
            "_json_object",
            "_latest_approved_evaluation",
            "_nonempty_string",
            "_normalize_evaluation_spec",
            "_normalize_metric",
            "_normalize_unacceptable_regressions",
            "_required_string",
            "_revision",
            "_string_list",
            "lock_evaluation_spec",
        }
    ),
}
EXPECTED_EVALUATION_EDGES = {
    "hard_gates": frozenset({"spec"}),
    "pareto": frozenset({"spec"}),
    "promotion": frozenset(
        {"hard_gates", "pareto", "reports", "spec"}
    ),
    "reports": frozenset({"hard_gates", "spec"}),
    "spec": frozenset(),
}


def _module_tree(module: str) -> ast.Module:
    return ast.parse(
        (WORKFLOW_ROOT / f"{module}.py").read_text(encoding="utf-8")
    )


def _evaluation_module_tree(module: str) -> ast.Module:
    return ast.parse(
        (EVALUATION_ROOT / f"{module}.py").read_text(encoding="utf-8")
    )


def _top_level_symbols(tree: ast.Module) -> set[str]:
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(
            node,
            (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            symbols.update(
                target.id
                for target in targets
                if isinstance(target, ast.Name)
            )
    return symbols


def _workflow_dependencies(
    module: str, tree: ast.Module
) -> frozenset[str]:
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module in WORKFLOW_MODULES:
                dependencies.add(node.module)
            if node.level == 0 and (
                node.module == "autoresearch.workflow"
                or node.module.startswith("autoresearch.workflow.")
            ):
                raise AssertionError(
                    f"{module} imports through workflow facade"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "autoresearch.workflow" or (
                    alias.name.startswith("autoresearch.workflow.")
                ):
                    raise AssertionError(
                        f"{module} imports through workflow facade"
                    )
    return frozenset(dependencies)


def _evaluation_dependencies(
    module: str, tree: ast.Module
) -> frozenset[str]:
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module in EVALUATION_MODULES:
                dependencies.add(node.module)
            if node.level == 0 and (
                node.module == "autoresearch.evaluation"
                or node.module.startswith("autoresearch.evaluation.")
            ):
                raise AssertionError(
                    f"{module} imports through evaluation facade"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "autoresearch.evaluation" or (
                    alias.name.startswith("autoresearch.evaluation.")
                ):
                    raise AssertionError(
                        f"{module} imports through evaluation facade"
                    )
    return frozenset(dependencies)


def test_workflow_facade_preserves_only_evidenced_public_imports():
    assert frozenset(workflow.__all__) == EXPECTED_WORKFLOW_EXPORTS
    assert len(workflow.__all__) == len(EXPECTED_WORKFLOW_EXPORTS)
    assert callable(workflow._reject_other_pending_intents)
    assert "_reject_other_pending_intents" not in workflow.__all__
    for name in EXPECTED_WORKFLOW_EXPORTS:
        assert callable(getattr(workflow, name))


def test_workflow_top_level_symbols_have_exactly_one_owner():
    assert {
        path.stem
        for path in WORKFLOW_ROOT.glob("*.py")
        if path.name != "__init__.py"
    } == WORKFLOW_MODULES
    actual = {
        module: frozenset(_top_level_symbols(_module_tree(module)))
        for module in WORKFLOW_MODULES
    }
    assert actual == EXPECTED_WORKFLOW_SYMBOLS
    flattened = [
        symbol
        for symbols in actual.values()
        for symbol in symbols
    ]
    assert len(flattened) == 165
    assert len(set(flattened)) == 165


def test_workflow_duplicate_key_loader_and_registration_live_in_recovery():
    expressions = {
        module: [
            node
            for node in _module_tree(module).body
            if isinstance(node, ast.Expr)
        ]
        for module in WORKFLOW_MODULES
    }
    assert len(expressions["recovery"]) == 1
    assert all(
        not nodes
        for module, nodes in expressions.items()
        if module != "recovery"
    )
    registration = ast.unparse(expressions["recovery"][0])
    assert registration.startswith("_UniqueKeySafeLoader.add_constructor(")


def test_workflow_internal_imports_form_the_approved_acyclic_dag():
    dependencies = {
        module: _workflow_dependencies(module, _module_tree(module))
        for module in WORKFLOW_MODULES
    }
    assert dependencies == EXPECTED_WORKFLOW_EDGES

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        assert module not in visiting, f"workflow import cycle at {module}"
        if module in visited:
            return
        visiting.add(module)
        for dependency in dependencies[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in WORKFLOW_MODULES:
        visit(module)


def test_evaluation_facade_preserves_only_evidenced_public_imports():
    assert frozenset(evaluation.__all__) == EXPECTED_EVALUATION_EXPORTS
    assert len(evaluation.__all__) == len(EXPECTED_EVALUATION_EXPORTS)
    for name in EXPECTED_EVALUATION_EXPORTS:
        assert callable(getattr(evaluation, name))


def test_evaluation_top_level_symbols_have_exactly_one_owner():
    assert {
        path.stem
        for path in EVALUATION_ROOT.glob("*.py")
        if path.name != "__init__.py"
    } == EVALUATION_MODULES
    actual = {
        module: frozenset(
            _top_level_symbols(_evaluation_module_tree(module))
        )
        for module in EVALUATION_MODULES
    }
    assert actual == EXPECTED_EVALUATION_SYMBOLS
    flattened = [
        symbol
        for symbols in actual.values()
        for symbol in symbols
    ]
    assert len(flattened) == 38
    assert len(set(flattened)) == 38
    assert "HARD_GATES" in actual["spec"]
    assert all(
        "HARD_GATES" not in symbols
        for module, symbols in actual.items()
        if module != "spec"
    )


def test_evaluation_internal_imports_form_the_approved_acyclic_dag():
    dependencies = {
        module: _evaluation_dependencies(
            module, _evaluation_module_tree(module)
        )
        for module in EVALUATION_MODULES
    }
    assert dependencies == EXPECTED_EVALUATION_EDGES

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        assert module not in visiting, (
            f"evaluation import cycle at {module}"
        )
        if module in visited:
            return
        visiting.add(module)
        for dependency in dependencies[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in EVALUATION_MODULES:
        visit(module)
