import copy
from contextlib import contextmanager
import fcntl
import json
import multiprocessing
from pathlib import Path

import pytest
import yaml

from autoresearch import governance
from autoresearch.governance import (
    build_evaluation_gate_envelope,
    cancel_gate,
    detach_budget_reservation,
    reject_gate,
    resolve_budget_settlement,
    record_gate_receipt,
    request_gate,
    require_execution_approval,
    reserve_budget,
    settle_budget,
)
from autoresearch.io import atomic_write_json, read_json, read_jsonl
from autoresearch.project import ProjectLayout
from autoresearch.workflow import validate_project_integrity
from researchctl import main


ADDITIVE_LIMITS = {
    "wall_seconds": 3600,
    "cpu_seconds": 3600,
    "gpu_seconds": 0,
    "storage_bytes": 1_000_000_000,
    "official_candidates": 5,
    "retries": 2,
    "debug_candidates": 2,
}


def modification_payload(layout: ProjectLayout) -> dict:
    return {
        "editable_paths": [str(layout.root)],
        "append_only_paths": [str(layout.root / ".research")],
        "read_only_paths": [],
        "allowed_actions": ["create", "modify", "execute", "cleanup-temporary"],
        "allowed_commands": ["uv"],
        "dependency_policy": {
            "allowed": ["pyyaml>=6,<7"],
            "undeclared_requires_gate": True,
        },
        "model_policy": {
            "allowed_families": ["test-fixture"],
            "external_service_change_requires_gate": True,
        },
        "network": {"mode": "denied", "allowed_services": []},
        "approval_required_actions": [
            "delete",
            "publish",
            "sensitive-data-access",
        ],
    }


def budget_payload() -> dict:
    return {
        "schema_version": 1,
        "limits": {
            **ADDITIVE_LIMITS,
            "gpu_type": None,
            "loop-subagent-bound": 4,
        },
        "storage_accounting": {
            "count_snapshot_copy_once": True,
            "excluded_artifact_types": ["model-weight"],
        },
    }


def reserve_in_process(root, reservation_id, completed, results):
    layout = ProjectLayout.from_root(root)
    try:
        reserve_budget(
            layout,
            {"id": reservation_id, "wall_seconds": 2000},
        )
    except ValueError as error:
        results.put((reservation_id, "rejected", str(error)))
    except Exception as error:
        results.put(
            (
                reservation_id,
                "error",
                f"{type(error).__name__}: {error}",
            )
        )
    else:
        results.put((reservation_id, "accepted", None))
    finally:
        completed.set()


def test_execution_requires_both_approved_envelopes(project_layout):
    with pytest.raises(PermissionError, match="modification.*budget"):
        require_execution_approval(project_layout)


@pytest.mark.parametrize("legacy_version", [0, 1])
def test_validate_migrates_recognized_legacy_budget_once_with_receipt(
    approved_project,
    legacy_version,
):
    legacy = read_json(approved_project.budget)
    legacy["schema_version"] = legacy_version
    legacy["payload_hash"] = "0" * 64
    legacy["limits"].update({"cost_usd": 12.0, "model_tokens": 1000})
    for counter in (legacy["reserved"], legacy["consumed"]):
        counter.update({"cost_usd": 0, "model_tokens": 0})
    atomic_write_json(approved_project.budget, legacy)

    assert validate_project_integrity(approved_project) == []

    migrated = read_json(approved_project.budget)
    assert migrated["schema_version"] == 1
    assert "cost_usd" not in migrated["limits"]
    assert "model_tokens" not in migrated["consumed"]
    assert migrated["migration"]["from_schema_version"] == legacy_version
    assert migrated["migration"]["legacy_usage"]["consumed"] == {
        "cost_usd": 0,
        "model_tokens": 0,
    }
    receipts = read_jsonl(approved_project.budget_migrations)
    assert receipts == [migrated["migration"]]
    assert validate_project_integrity(approved_project) == []
    assert read_jsonl(approved_project.budget_migrations) == receipts


def test_detach_unknown_reservation_releases_budget_without_zero_usage(
    approved_execution_project,
):
    reserve_budget(
        approved_execution_project,
        {"id": "gpu-unmeasured", "gpu_seconds": 5},
    )

    receipt = detach_budget_reservation(
        approved_execution_project, "gpu-unmeasured", "provider telemetry unavailable"
    )

    budget = read_json(approved_execution_project.budget)
    assert receipt["usage_status"] == "unknown"
    assert "actual" not in receipt
    assert budget["reserved"]["gpu_seconds"] == 0
    assert budget["consumed"]["gpu_seconds"] == 0
    assert "gpu-unmeasured" not in budget["reservations"]
    assert read_jsonl(approved_execution_project.reservation_detachments) == [receipt]


def test_detach_rejects_reservation_with_pending_settlement(approved_project):
    # Detach must not resume and commit a pending settlement for the same
    # reservation (that would invent measured usage then raise "unknown
    # reservation"). A pending settlement blocks detach until it is settled or
    # quarantined.
    reserve_budget(
        approved_project, {"id": "pending-detach", "wall_seconds": 2.0}
    )
    governance.plan_budget_settlement(
        approved_project, "pending-detach", {"wall_seconds": 1.0}
    )

    with pytest.raises(ValueError, match="pending budget settlement"):
        detach_budget_reservation(
            approved_project, "pending-detach", "telemetry unavailable"
        )

    budget = read_json(approved_project.budget)
    assert "pending-detach" in budget["reservations"]
    assert budget["reserved"]["wall_seconds"] == 2.0


@pytest.mark.parametrize(
    ("parent", "field"),
    [
        (None, "editable_paths"),
        (None, "append_only_paths"),
        (None, "read_only_paths"),
        (None, "allowed_actions"),
        (None, "allowed_commands"),
        ("dependency_policy", "allowed"),
        ("dependency_policy", "undeclared_requires_gate"),
        ("model_policy", "allowed_families"),
        ("model_policy", "external_service_change_requires_gate"),
        ("network", "mode"),
        ("network", "allowed_services"),
        (None, "approval_required_actions"),
    ],
)
def test_modification_envelope_requires_every_field(project_layout, parent, field):
    payload = modification_payload(project_layout)
    if parent is None:
        del payload[field]
    else:
        del payload[parent][field]

    with pytest.raises(ValueError, match=field):
        request_gate(project_layout, "modification", payload)


def test_modification_paths_are_canonicalized_before_hashing(project_layout):
    canonical = modification_payload(project_layout)
    equivalent = copy.deepcopy(canonical)
    equivalent["editable_paths"] = [
        str(project_layout.root / "not-created" / "..")
    ]

    first = request_gate(project_layout, "modification", canonical)
    second = request_gate(project_layout, "modification", equivalent)

    assert first["payload_hash"] == second["payload_hash"]


def test_gate_receipts_are_append_only_and_latest_approval_becomes_active(
    project_layout,
):
    first = request_gate(
        project_layout, "modification", modification_payload(project_layout)
    )
    first_receipt = record_gate_receipt(
        project_layout,
        first["gate_id"],
        {"source": "chat", "evidence": "approve-first"},
    )
    second_payload = modification_payload(project_layout)
    second_payload["allowed_commands"] = ["uv", "pytest"]
    second = request_gate(project_layout, "modification", second_payload)
    second_receipt = record_gate_receipt(
        project_layout,
        second["gate_id"],
        {"source": "chat", "evidence": "approve-second"},
    )

    records = read_jsonl(project_layout.gates)
    assert [record["status"] for record in records] == [
        "requested",
        "approved",
        "requested",
        "approved",
    ]
    assert records[0] == first
    assert records[2] == second
    assert first_receipt["payload_hash"] == first["payload_hash"]
    assert second_receipt["payload_hash"] == second["payload_hash"]
    active = read_json(
        project_layout.root / ".research" / "modification-envelope.json"
    )
    assert active["gate_id"] == second["gate_id"]
    assert active["payload"]["allowed_commands"] == ["uv", "pytest"]


def test_coordinator_gate_reuses_matching_pending_request(project_layout):
    payload = {
        "stage_id": "research-charter",
        "source_revision": 1,
        "source_state_hash": "a" * 64,
        "graph_hash": "b" * 64,
        "output_artifacts": [],
    }
    pending = request_gate(project_layout, "charter", payload)

    receipt = governance.record_coordinator_gate_receipt(
        project_layout, "charter", payload
    )

    assert receipt["gate_id"] == pending["gate_id"]
    records = [
        record
        for record in read_jsonl(project_layout.gates)
        if record["gate_id"] == pending["gate_id"]
    ]
    assert [record["status"] for record in records] == [
        "requested",
        "approved",
    ]
    assert records[-1]["approval_source"] == "Coordinator"


def test_coordinator_cannot_record_publication_gate(project_layout):
    with pytest.raises(PermissionError, match="charter or evaluation"):
        governance.record_coordinator_gate_receipt(
            project_layout,
            "publication",
            {
                "stage_id": "final-report",
                "source_revision": 1,
                "source_state_hash": "a" * 64,
                "graph_hash": "b" * 64,
                "output_artifacts": [],
            },
        )


def test_read_approved_gate_validates_latest_receipt_and_payload_sidecar(
    project_layout,
):
    from autoresearch.governance import read_approved_gate

    payload = build_evaluation_gate_envelope(
        stage_id="research-charter",
        source_revision=0,
        source_state_hash="a" * 64,
        graph_hash="b" * 64,
        output_artifacts=[],
        evaluation_spec_id="eval-spec-1",
        payload_hash="c" * 64,
    )
    gate = request_gate(project_layout, "evaluation", payload)
    receipt = record_gate_receipt(
        project_layout,
        gate["gate_id"],
        {"source": "chat", "evidence": "approve-evaluation"},
    )

    approved = read_approved_gate(
        project_layout, gate["gate_id"], "evaluation"
    )

    assert approved == {
        "gate_id": gate["gate_id"],
        "gate_type": "evaluation",
        "payload_hash": gate["payload_hash"],
        "payload": payload,
        "request": gate,
        "receipt": receipt,
    }


def test_read_approved_gate_rejects_nonlatest_approval(project_layout):
    from autoresearch.governance import read_approved_gate

    first = request_gate(
        project_layout,
        "evaluation",
        build_evaluation_gate_envelope(
            stage_id="research-charter",
            source_revision=0,
            source_state_hash="a" * 64,
            graph_hash="b" * 64,
            output_artifacts=[],
            evaluation_spec_id="eval-spec-1",
            payload_hash="c" * 64,
        ),
    )
    record_gate_receipt(
        project_layout,
        first["gate_id"],
        {"source": "chat", "evidence": "approve-first"},
    )
    second = request_gate(
        project_layout,
        "evaluation",
        build_evaluation_gate_envelope(
            stage_id="research-charter",
            source_revision=0,
            source_state_hash="a" * 64,
            graph_hash="b" * 64,
            output_artifacts=[],
            evaluation_spec_id="eval-spec-2",
            payload_hash="d" * 64,
        ),
    )
    record_gate_receipt(
        project_layout,
        second["gate_id"],
        {"source": "chat", "evidence": "approve-second"},
    )

    with pytest.raises(ValueError, match="latest approved"):
        read_approved_gate(project_layout, first["gate_id"], "evaluation")


def test_budget_approval_copies_limits_and_initializes_counters(project_layout):
    gate = request_gate(project_layout, "budget", budget_payload())
    record_gate_receipt(
        project_layout,
        gate["gate_id"],
        {"source": "chat", "evidence": "approve-budget"},
    )

    budget = read_json(project_layout.budget)
    assert budget["status"] == "approved"
    assert budget["schema_version"] == 1
    assert budget["limits"] == budget_payload()["limits"]
    assert (
        budget["storage_accounting"]
        == budget_payload()["storage_accounting"]
    )
    aggregate_limits = {
        "wall_seconds",
        "cpu_seconds",
        "gpu_seconds",
        "storage_bytes",
    }
    assert budget["reserved"] == {
        key: 0 for key in aggregate_limits
    }
    assert budget["consumed"] == budget["reserved"]
    assert budget["reservations"] == {}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.pop("schema_version"), "schema_version"),
        (lambda payload: payload.update(schema_version=True), "schema_version"),
        (lambda payload: payload.update(schema_version=2), "schema_version"),
        (lambda payload: payload.pop("storage_accounting"), "storage_accounting"),
        (
            lambda payload: payload["storage_accounting"].update(
                count_snapshot_copy_once=False
            ),
            "count_snapshot_copy_once",
        ),
        (
            lambda payload: payload["storage_accounting"].update(
                excluded_artifact_types=[]
            ),
            "excluded_artifact_types",
        ),
        (
            lambda payload: payload["storage_accounting"].update(extra=True),
            "unknown field",
        ),
        (lambda payload: payload.update(extra=True), "budget payload"),
    ],
)
def test_budget_requires_exact_versioned_storage_accounting_policy(
    project_layout, mutate, message
):
    payload = budget_payload()
    mutate(payload)

    with pytest.raises(ValueError, match=message):
        request_gate(project_layout, "budget", payload)


def test_later_budget_approval_preserves_tracked_usage(approved_project):
    reserve_budget(approved_project, {"id": "settled", "wall_seconds": 2.0})
    settle_budget(approved_project, "settled", {"wall_seconds": 1.5})
    before = reserve_budget(
        approved_project, {"id": "active", "wall_seconds": 3.0}
    )
    updated_payload = budget_payload()
    updated_payload["limits"]["wall_seconds"] = 20.0

    gate = request_gate(approved_project, "budget", updated_payload)
    record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "chat", "evidence": "approve-expanded-budget"},
    )

    after = read_json(approved_project.budget)
    assert after["limits"]["wall_seconds"] == 20.0
    assert after["consumed"] == before["consumed"]
    assert after["reserved"] == before["reserved"]
    assert after["reservations"] == before["reservations"]


def test_later_budget_approval_rejects_limit_below_committed_usage(
    approved_project,
):
    reserve_budget(approved_project, {"id": "settled", "wall_seconds": 2.0})
    settle_budget(approved_project, "settled", {"wall_seconds": 1.5})
    reserve_budget(approved_project, {"id": "active", "wall_seconds": 2.0})
    reduced_payload = budget_payload()
    reduced_payload["limits"]["wall_seconds"] = 3.0
    gate = request_gate(approved_project, "budget", reduced_payload)

    with pytest.raises(ValueError, match="below tracked usage"):
        record_gate_receipt(
            approved_project,
            gate["gate_id"],
            {"source": "chat", "evidence": "reject-unsafe-reduction"},
        )

    budget = read_json(approved_project.budget)
    assert budget["limits"]["wall_seconds"] == 3600
    assert budget["consumed"]["wall_seconds"] == 1.5
    assert budget["reserved"]["wall_seconds"] == 2.0
    assert set(budget["reservations"]) == {"active"}


def test_budget_accepts_nullable_additive_limits_and_keeps_usage_finite(
    project_layout,
):
    payload = budget_payload()
    payload["limits"]["wall_seconds"] = None
    payload["limits"]["cpu_seconds"] = None
    gate = request_gate(project_layout, "budget", payload)
    record_gate_receipt(
        project_layout,
        gate["gate_id"],
        {"source": "test", "evidence": "approve-unlimited-additive-caps"},
    )

    reserved = reserve_budget(
        project_layout,
        {"id": "finite-under-null", "wall_seconds": 12.5, "cpu_seconds": 500},
    )
    settled = settle_budget(
        project_layout,
        "finite-under-null",
        {"wall_seconds": 11.25, "cpu_seconds": 450},
    )

    assert reserved["limits"]["wall_seconds"] is None
    assert reserved["limits"]["cpu_seconds"] is None
    assert settled["consumed"]["wall_seconds"] == 11.25
    assert settled["consumed"]["cpu_seconds"] == 450
    assert all(
        value is not None
        for counters in (settled["reserved"], settled["consumed"])
        for value in counters.values()
    )


def test_fractional_elapsed_limits_and_usage_are_valid(project_layout):
    payload = budget_payload()
    payload["limits"].update(
        {
            "wall_seconds": 3600.5,
            "cpu_seconds": 1800.25,
            "gpu_seconds": 900.125,
        }
    )
    gate = request_gate(project_layout, "budget", payload)
    record_gate_receipt(
        project_layout,
        gate["gate_id"],
        {"source": "test", "evidence": "fractional-elapsed-usage"},
    )
    reserve_budget(
        project_layout,
        {
            "id": "fractional-elapsed",
            "wall_seconds": 2.5,
            "cpu_seconds": 1.25,
            "gpu_seconds": 0.5,
        },
    )

    settled = settle_budget(
        project_layout,
        "fractional-elapsed",
        {
            "wall_seconds": 2.2,
            "cpu_seconds": 1.1,
            "gpu_seconds": 0.25,
        },
    )

    assert settled["consumed"]["wall_seconds"] == pytest.approx(2.2)
    assert settled["consumed"]["cpu_seconds"] == pytest.approx(1.1)
    assert settled["consumed"]["gpu_seconds"] == pytest.approx(0.25)


@pytest.mark.parametrize("field", ["storage_bytes"])
def test_discrete_usage_rejects_fractional_amounts(approved_project, field):
    with pytest.raises(ValueError, match=f"{field} must be an integer"):
        reserve_budget(
            approved_project,
            {"id": f"fractional-{field}", field: 1.5},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("official_candidates", None),
        ("retries", None),
        ("debug_candidates", None),
        ("loop-subagent-bound", None),
        ("gpu_type", 0),
    ],
)
def test_null_is_rejected_outside_additive_limits(
    project_layout, field, value
):
    payload = budget_payload()
    payload["limits"][field] = value

    with pytest.raises(ValueError, match=field):
        request_gate(project_layout, "budget", payload)


@pytest.mark.parametrize("field", ["wall_seconds", "cpu_seconds"])
@pytest.mark.parametrize("value", [None, float("inf"), float("nan")])
def test_reservations_and_actual_usage_reject_nonfinite_values(
    approved_project, field, value
):
    with pytest.raises(ValueError, match="finite|number"):
        reserve_budget(
            approved_project,
            {"id": f"invalid-{field}-{value!r}", field: value},
        )

    reserve_budget(
        approved_project,
        {"id": f"actual-{field}-{value!r}", field: 1},
    )
    with pytest.raises(ValueError, match="finite|number"):
        settle_budget(
            approved_project,
            f"actual-{field}-{value!r}",
            {field: value},
        )


@pytest.mark.parametrize(
    "field", ["official_candidates", "retries", "debug_candidates"]
)
def test_journal_and_retry_quotas_are_not_reservable(
    approved_project, field
):
    with pytest.raises(ValueError, match=field):
        reserve_budget(
            approved_project,
            {"id": f"quota-{field}", field: 1},
        )


def test_budget_parallelism_and_graph_bound_are_independent_caps(
    project_layout,
):
    graph = yaml.safe_load(project_layout.graph.read_text(encoding="utf-8"))
    graph["limits"]["loop-subagent-bound"] = 7
    project_layout.graph.write_text(
        yaml.safe_dump(graph, sort_keys=False),
        encoding="utf-8",
    )
    lower_payload = budget_payload()
    lower_payload["limits"]["loop-subagent-bound"] = 2

    gate = request_gate(project_layout, "budget", lower_payload)
    record_gate_receipt(
        project_layout,
        gate["gate_id"],
        {"source": "test", "evidence": "approve-lower-two"},
    )
    assert read_json(project_layout.budget)["limits"][
        "loop-subagent-bound"
    ] == 2

    higher_payload = budget_payload()
    higher_payload["limits"]["loop-subagent-bound"] = 8
    higher_gate = request_gate(project_layout, "budget", higher_payload)
    record_gate_receipt(
        project_layout,
        higher_gate["gate_id"],
        {"source": "test", "evidence": "approve-eight-budget-slots"},
    )
    assert read_json(project_layout.budget)["limits"]["loop-subagent-bound"] == 8


def test_gate_rejection_and_cancellation_are_durable_terminal_decisions(
    project_layout,
):
    rejected_gate = request_gate(
        project_layout, "charter", {"stage_id": "intake"}
    )
    rejected = reject_gate(
        project_layout,
        rejected_gate["gate_id"],
        {"source": "Rick", "reason": "need a narrower question"},
    )
    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == "need a narrower question"
    with pytest.raises(ValueError, match="already decided"):
        record_gate_receipt(
            project_layout,
            rejected_gate["gate_id"],
            {"source": "test", "evidence": "late approval"},
        )

    cancelled_gate = request_gate(
        project_layout, "charter", {"stage_id": "intake"}
    )
    cancelled = cancel_gate(
        project_layout,
        cancelled_gate["gate_id"],
        {"source": "Rick", "reason": "project stopped"},
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancellation_reason"] == "project stopped"


def test_nullable_caps_survive_reapproval_and_settlement_recovery(
    project_layout, monkeypatch: pytest.MonkeyPatch
):
    payload = budget_payload()
    payload["limits"]["cpu_seconds"] = None
    gate = request_gate(project_layout, "budget", payload)
    record_gate_receipt(
        project_layout,
        gate["gate_id"],
        {"source": "test", "evidence": "approve-null-cost"},
    )
    reserve_budget(
        project_layout, {"id": "recover-null", "cpu_seconds": 20.0}
    )
    original = governance.atomic_write_json

    def fail_after_intent(path, value):
        if path == project_layout.budget:
            raise OSError("injected nullable recovery")
        original(path, value)

    monkeypatch.setattr(governance, "atomic_write_json", fail_after_intent)
    with pytest.raises(OSError, match="nullable recovery"):
        settle_budget(
            project_layout, "recover-null", {"cpu_seconds": 15.0}
        )
    monkeypatch.setattr(governance, "atomic_write_json", original)

    expanded = budget_payload()
    expanded["limits"]["cpu_seconds"] = None
    expanded["limits"]["wall_seconds"] = None
    next_gate = request_gate(project_layout, "budget", expanded)
    record_gate_receipt(
        project_layout,
        next_gate["gate_id"],
        {"source": "test", "evidence": "recover-and-reapprove-null"},
    )

    recovered = read_json(project_layout.budget)
    assert recovered["limits"]["cpu_seconds"] is None
    assert recovered["limits"]["wall_seconds"] is None
    assert recovered["consumed"]["cpu_seconds"] == 15.0
    assert recovered["reserved"]["cpu_seconds"] == 0.0


def test_execution_rejects_tampered_active_modification_envelope(approved_project):
    path = approved_project.root / ".research" / "modification-envelope.json"
    active = read_json(path)
    active["payload"]["allowed_commands"].append("unapproved-command")
    atomic_write_json(path, active)

    with pytest.raises(PermissionError, match="modification.*hash"):
        require_execution_approval(approved_project)


def test_budget_cannot_be_oversubscribed(approved_project):
    reserve_budget(
        approved_project, {"id": "r1", "wall_seconds": 2000}
    )
    with pytest.raises(ValueError, match="exceeds remaining"):
        reserve_budget(
            approved_project,
            {"id": "r2", "wall_seconds": 2000},
        )


@pytest.mark.parametrize(
    "reservation",
    [
        {"id": "negative-cost", "wall_seconds": -0.01},
        {"id": "negative-slots", "loop-subagent-bound": -1},
        {"id": "unknown-field", "network_calls": 1},
    ],
)
def test_budget_rejects_negative_and_unknown_reservations(
    approved_project, reservation
):
    with pytest.raises(ValueError):
        reserve_budget(approved_project, reservation)


def test_budget_rejects_duplicate_reservation_id(approved_project):
    reserve_budget(approved_project, {"id": "duplicate", "wall_seconds": 1})

    with pytest.raises(ValueError, match="already exists"):
        reserve_budget(approved_project, {"id": "duplicate", "wall_seconds": 1})


def test_settlement_rejects_unknown_reservation(approved_project):
    with pytest.raises(ValueError, match="unknown reservation"):
        settle_budget(approved_project, "missing", {"wall_seconds": 1.0})


def test_settlement_accepts_actual_above_slice_within_approved_total(
    approved_project,
):
    reserve_budget(approved_project, {"id": "bounded", "wall_seconds": 2.0})

    result = settle_budget(
        approved_project, "bounded", {"wall_seconds": 2.5}
    )

    assert result["consumed"]["wall_seconds"] == 2.5


def test_settlement_rejects_actual_above_approved_total(approved_project):
    reserve_budget(approved_project, {"id": "bounded", "wall_seconds": 2.0})

    with pytest.raises(ValueError, match="exceeds approved budget"):
        settle_budget(approved_project, "bounded", {"wall_seconds": 4000.0})


def test_settlement_releases_unused_reservation(approved_project):
    reserve_budget(
        approved_project, {"id": "r1", "cpu_seconds": 4.0, "wall_seconds": 30}
    )
    result = settle_budget(
        approved_project, "r1", {"cpu_seconds": 1.5, "wall_seconds": 20}
    )
    assert result["consumed"]["cpu_seconds"] == 1.5
    assert result["reserved"]["cpu_seconds"] == 0.0
    assert "r1" not in result["reservations"]


def test_settlement_writes_exact_deterministic_intent_and_receipt(
    approved_project,
):
    reserve_budget(
        approved_project, {"id": "durable", "wall_seconds": 2.0}
    )

    first = settle_budget(
        approved_project, "durable", {"wall_seconds": 1.5}
    )
    second = settle_budget(
        approved_project, "durable", {"wall_seconds": 1.5}
    )

    assert second == first
    intent_paths = list(approved_project.budget_transactions.glob("*.json"))
    assert len(intent_paths) == 1
    intent = read_json(intent_paths[0])
    assert set(intent) == {
        "schema_version",
        "record_type",
        "settlement_id",
        "reservation_id",
        "owner_transaction_id",
        "phase",
        "reservation",
        "actual",
        "budget_before",
        "budget_after",
        "receipt",
    }
    assert intent["schema_version"] == 1
    assert intent["record_type"] == "budget-settlement-intent"
    assert intent["phase"] == "committed"
    assert intent["reservation_id"] == "durable"
    assert intent["owner_transaction_id"] is None
    assert intent["settlement_id"] in intent_paths[0].name
    receipt = read_jsonl(approved_project.budget_settlements)[0]
    assert set(receipt) == {
        "schema_version",
        "record_type",
        "settlement_id",
        "reservation_id",
        "reservation",
        "actual",
        "settled_at",
    }
    assert receipt == intent["receipt"]
    assert resolve_budget_settlement(
        approved_project, intent["settlement_id"]
    ) == {
        "settlement_id": intent["settlement_id"],
        "receipt": receipt,
    }


def test_owner_bound_settlement_requires_matching_owner_and_actual(
    approved_project,
):
    reservation_id = "owner-bound"
    owner_transaction_id = "task-completion-owner-bound"
    actual = {"wall_seconds": 1.5}
    reserve_budget(
        approved_project, {"id": reservation_id, "wall_seconds": 2.0}
    )

    plan = governance.plan_budget_settlement(
        approved_project,
        reservation_id,
        actual,
        owner_transaction_id=owner_transaction_id,
    )

    assert governance.plan_budget_settlement(
        approved_project,
        reservation_id,
        actual,
        owner_transaction_id=owner_transaction_id,
    ) == plan
    with pytest.raises(ValueError, match="owner"):
        governance.plan_budget_settlement(
            approved_project,
            reservation_id,
            actual,
            owner_transaction_id="task-completion-other",
        )
    with pytest.raises(ValueError, match="different actual"):
        governance.plan_budget_settlement(
            approved_project,
            reservation_id,
            {"wall_seconds": 1.0},
            owner_transaction_id=owner_transaction_id,
        )
    with pytest.raises(ValueError, match="owner"):
        settle_budget(approved_project, reservation_id, actual)

    settled = governance.settle_budget_for_completion(
        approved_project,
        reservation_id,
        actual,
        settled_at=plan["receipt"]["settled_at"],
        expected_settlement_id=plan["settlement_id"],
        owner_transaction_id=owner_transaction_id,
    )
    repeated = governance.settle_budget_for_completion(
        approved_project,
        reservation_id,
        actual,
        settled_at=plan["receipt"]["settled_at"],
        expected_settlement_id=plan["settlement_id"],
        owner_transaction_id=owner_transaction_id,
    )

    assert repeated == settled
    assert settled["consumed"]["wall_seconds"] == 1.5
    assert len(read_jsonl(approved_project.budget_settlements)) == 1


def test_owner_bound_pending_settlement_blocks_unrelated_budget_mutators(
    approved_project,
):
    reservation_id = "orphaned-owner"
    reserve_budget(
        approved_project, {"id": reservation_id, "wall_seconds": 2.0}
    )
    governance.plan_budget_settlement(
        approved_project,
        reservation_id,
        {"wall_seconds": 1.0},
        owner_transaction_id="task-completion-orphaned-owner",
    )
    intent_path = next(
        approved_project.budget_transactions.glob("*.json")
    )
    budget_before = read_json(approved_project.budget)
    intent_before = read_json(intent_path)
    settlements_before = read_jsonl(approved_project.budget_settlements)

    with pytest.raises(ValueError, match="owning workflow"):
        reserve_budget(
            approved_project, {"id": "unrelated", "wall_seconds": 1.0}
        )

    updated_payload = budget_payload()
    updated_payload["limits"]["wall_seconds"] = 20.0
    gate = request_gate(approved_project, "budget", updated_payload)
    with pytest.raises(ValueError, match="owning workflow"):
        record_gate_receipt(
            approved_project,
            gate["gate_id"],
            {"source": "test", "evidence": "must-not-steal-owner"},
        )

    assert read_json(approved_project.budget) == budget_before
    assert read_json(intent_path) == intent_before
    assert read_jsonl(approved_project.budget_settlements) == settlements_before


@pytest.mark.parametrize(
    "failure_phase",
    ["intent-prepared", "budget-replaced", "receipt-appended", "commit"],
)
def test_settlement_recovers_each_durable_phase_exactly_once(
    approved_project,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
):
    reservation_id = f"recover-{failure_phase}"
    reserve_budget(
        approved_project, {"id": reservation_id, "wall_seconds": 2.0}
    )
    original_atomic_write_json = governance.atomic_write_json
    original_append_jsonl_locked = governance.append_jsonl_locked

    def fail_atomic(path, value):
        if failure_phase == "intent-prepared" and path == approved_project.budget:
            raise OSError("injected after prepared intent")
        if failure_phase == "budget-replaced" and path == approved_project.budget:
            original_atomic_write_json(path, value)
            raise OSError("injected after budget replacement")
        if (
            failure_phase == "commit"
            and path.parent == approved_project.budget_transactions
            and value.get("phase") == "committed"
        ):
            raise OSError("injected before committed intent")
        original_atomic_write_json(path, value)

    def fail_append(path, value):
        original_append_jsonl_locked(path, value)
        if (
            failure_phase == "receipt-appended"
            and path == approved_project.budget_settlements
        ):
            raise OSError("injected after receipt append")

    monkeypatch.setattr(governance, "atomic_write_json", fail_atomic)
    monkeypatch.setattr(governance, "append_jsonl_locked", fail_append)
    with pytest.raises(OSError, match="injected"):
        settle_budget(
            approved_project, reservation_id, {"wall_seconds": 1.25}
        )

    monkeypatch.setattr(
        governance, "atomic_write_json", original_atomic_write_json
    )
    monkeypatch.setattr(
        governance, "append_jsonl_locked", original_append_jsonl_locked
    )
    reopened = ProjectLayout.from_root(approved_project.root)
    recovered = settle_budget(
        reopened, reservation_id, {"wall_seconds": 1.25}
    )
    repeated = settle_budget(
        reopened, reservation_id, {"wall_seconds": 1.25}
    )

    assert repeated == recovered
    assert recovered["consumed"]["wall_seconds"] == 1.25
    assert recovered["reserved"]["wall_seconds"] == 0.0
    assert recovered["reservations"] == {}
    assert len(read_jsonl(reopened.budget_settlements)) == 1
    intents = [
        read_json(path) for path in reopened.budget_transactions.glob("*.json")
    ]
    assert len(intents) == 1
    assert intents[0]["phase"] == "committed"


def test_settlement_rejects_changed_actual_and_historical_id_reuse(
    approved_project,
):
    reserve_budget(
        approved_project, {"id": "historical", "wall_seconds": 2.0}
    )
    settle_budget(approved_project, "historical", {"wall_seconds": 1.5})

    with pytest.raises(ValueError, match="different actual"):
        settle_budget(approved_project, "historical", {"wall_seconds": 1.0})
    with pytest.raises(ValueError, match="historically settled"):
        reserve_budget(
            approved_project, {"id": "historical", "wall_seconds": 1.0}
        )

    assert read_json(approved_project.budget)["consumed"]["wall_seconds"] == 1.5
    assert len(read_jsonl(approved_project.budget_settlements)) == 1


def test_reserve_resumes_pending_settlement_before_mutating_budget(
    approved_project,
    monkeypatch: pytest.MonkeyPatch,
):
    reserve_budget(
        approved_project, {"id": "pending", "wall_seconds": 2.0}
    )
    original_atomic_write_json = governance.atomic_write_json

    def fail_budget_write(path, value):
        if path == approved_project.budget:
            raise OSError("injected after settlement intent")
        original_atomic_write_json(path, value)

    monkeypatch.setattr(governance, "atomic_write_json", fail_budget_write)
    with pytest.raises(OSError, match="settlement intent"):
        settle_budget(approved_project, "pending", {"wall_seconds": 1.0})
    monkeypatch.setattr(
        governance, "atomic_write_json", original_atomic_write_json
    )

    result = reserve_budget(
        approved_project, {"id": "next", "wall_seconds": 2.0}
    )

    assert result["consumed"]["wall_seconds"] == 1.0
    assert result["reserved"]["wall_seconds"] == 2.0
    assert set(result["reservations"]) == {"next"}
    assert len(read_jsonl(approved_project.budget_settlements)) == 1


def test_budget_reapproval_resumes_pending_settlement_before_mutating_budget(
    approved_project,
    monkeypatch: pytest.MonkeyPatch,
):
    reserve_budget(
        approved_project, {"id": "pending", "wall_seconds": 2.0}
    )
    original_append_jsonl_locked = governance.append_jsonl_locked

    def fail_receipt(path, value):
        if path == approved_project.budget_settlements:
            raise OSError("injected before settlement receipt")
        original_append_jsonl_locked(path, value)

    monkeypatch.setattr(governance, "append_jsonl_locked", fail_receipt)
    with pytest.raises(OSError, match="settlement receipt"):
        settle_budget(approved_project, "pending", {"wall_seconds": 1.0})
    monkeypatch.setattr(
        governance, "append_jsonl_locked", original_append_jsonl_locked
    )
    updated_payload = budget_payload()
    updated_payload["limits"]["wall_seconds"] = 20.0
    gate = request_gate(approved_project, "budget", updated_payload)

    record_gate_receipt(
        approved_project,
        gate["gate_id"],
        {"source": "chat", "evidence": "approve-after-recovery"},
    )

    budget = read_json(approved_project.budget)
    assert budget["limits"]["wall_seconds"] == 20.0
    assert budget["consumed"]["wall_seconds"] == 1.0
    assert len(read_jsonl(approved_project.budget_settlements)) == 1


@pytest.mark.parametrize("tamper_target", ["intent", "receipt"])
def test_hostile_settlement_evidence_fails_before_new_side_effects(
    approved_project,
    monkeypatch: pytest.MonkeyPatch,
    tamper_target: str,
):
    reserve_budget(
        approved_project, {"id": "hostile", "wall_seconds": 2.0}
    )
    original_atomic_write_json = governance.atomic_write_json
    original_append_jsonl_locked = governance.append_jsonl_locked

    if tamper_target == "intent":
        def fail_write(path, value):
            if path == approved_project.budget:
                raise OSError("injected before budget replacement")
            original_atomic_write_json(path, value)

        monkeypatch.setattr(governance, "atomic_write_json", fail_write)
    else:
        def fail_append(path, value):
            original_append_jsonl_locked(path, value)
            if path == approved_project.budget_settlements:
                raise OSError("injected after settlement receipt")

        monkeypatch.setattr(governance, "append_jsonl_locked", fail_append)

    with pytest.raises(OSError, match="injected"):
        settle_budget(approved_project, "hostile", {"wall_seconds": 1.0})
    monkeypatch.setattr(
        governance, "atomic_write_json", original_atomic_write_json
    )
    monkeypatch.setattr(
        governance, "append_jsonl_locked", original_append_jsonl_locked
    )

    intent_path = next(approved_project.budget_transactions.glob("*.json"))
    if tamper_target == "intent":
        intent = read_json(intent_path)
        intent["actual"]["wall_seconds"] = 0.5
        atomic_write_json(intent_path, intent)
    else:
        receipts = read_jsonl(approved_project.budget_settlements)
        receipts[0]["actual"]["wall_seconds"] = 0.5
        approved_project.budget_settlements.write_text(
            json.dumps(receipts[0], separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    budget_before = read_json(approved_project.budget)
    receipt_count_before = len(
        read_jsonl(approved_project.budget_settlements)
    )
    with pytest.raises(ValueError, match="hash|evidence|intent|receipt"):
        settle_budget(approved_project, "hostile", {"wall_seconds": 1.0})

    assert read_json(approved_project.budget) == budget_before
    assert (
        len(read_jsonl(approved_project.budget_settlements))
        == receipt_count_before
    )


def test_budget_rejects_mismatched_gpu_type(approved_project):
    with pytest.raises(ValueError, match="gpu_type"):
        reserve_budget(
            approved_project,
            {"id": "gpu", "gpu_type": "nvidia-a100", "gpu_seconds": 0},
        )


def test_budget_limits_active_parallel_subagent_slots(approved_project):
    for index in range(4):
        reserve_budget(
            approved_project,
            {"id": f"worker-{index}", "loop-subagent-bound": 1},
        )

    with pytest.raises(ValueError, match="loop-subagent-bound"):
        reserve_budget(
            approved_project,
            {"id": "worker-4", "loop-subagent-bound": 1},
        )

    settle_budget(approved_project, "worker-0", {})
    reserve_budget(
        approved_project,
        {"id": "worker-replacement", "loop-subagent-bound": 1},
    )


def test_parallel_reservations_serialize_on_stable_budget_lock(
    approved_project, monkeypatch: pytest.MonkeyPatch
):
    context = multiprocessing.get_context("fork")
    first_holds_lock = context.Event()
    release_first = context.Event()
    first_completed = context.Event()
    second_completed = context.Event()
    results = context.Queue()
    lock_observations = context.Queue()
    original_atomic_write_json = governance.atomic_write_json
    original_exclusive_lock = governance._exclusive_lock
    budget_lock = approved_project.root / ".research" / "budget.lock"

    probed_lock_paths = set()

    def pause_first_before_replacement(path, value):
        if path == approved_project.budget and "r1" in value["reservations"]:
            first_holds_lock.set()
            if not release_first.wait(timeout=5):
                raise TimeoutError("test did not release first reservation")
        original_atomic_write_json(path, value)

    @contextmanager
    def observe_second_lock_attempt(path):
        if (
            multiprocessing.current_process().name == "reservation-r2"
            and path not in probed_lock_paths
        ):
            probed_lock_paths.add(path)
            with path.open("a+", encoding="utf-8") as probe:
                try:
                    fcntl.flock(
                        probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                except BlockingIOError:
                    lock_observations.put(
                        ("blocked", str(path), "BlockingIOError")
                    )
                else:
                    fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
                    lock_observations.put(("acquired", str(path), None))
                    raise RuntimeError(
                        "stable budget lock was unexpectedly available"
                    )
        with original_exclusive_lock(path):
            yield

    monkeypatch.setattr(
        governance, "atomic_write_json", pause_first_before_replacement
    )
    monkeypatch.setattr(
        governance, "_exclusive_lock", observe_second_lock_attempt
    )
    first = context.Process(
        name="reservation-r1",
        target=reserve_in_process,
        args=(approved_project.root, "r1", first_completed, results),
    )
    second = context.Process(
        name="reservation-r2",
        target=reserve_in_process,
        args=(approved_project.root, "r2", second_completed, results),
    )
    started_processes = []
    try:
        first.start()
        started_processes.append(first)
        assert first_holds_lock.wait(timeout=5)
        second.start()
        started_processes.append(second)
        observation = lock_observations.get(timeout=5)
        assert observation == (
            "blocked",
            str(budget_lock),
            "BlockingIOError",
        )
        release_first.set()
    finally:
        release_first.set()
        for process in started_processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert first.exitcode == 0
    assert second.exitcode == 0
    assert first_completed.is_set()
    assert second_completed.is_set()
    outcomes = {
        reservation_id: (status, detail)
        for reservation_id, status, detail in (
            results.get(timeout=1),
            results.get(timeout=1),
        )
    }
    assert outcomes["r1"] == ("accepted", None)
    assert outcomes["r2"][0] == "rejected"
    assert "exceeds remaining" in outcomes["r2"][1]
    assert budget_lock.is_file()


@pytest.mark.parametrize(
    "argv",
    [
        ["gate-request", "--type", "invalid"],
        ["budget-settle", "--reservation-id", "r1"],
    ],
)
def test_cli_argument_errors_emit_compact_json(
    argv, capsys: pytest.CaptureFixture[str]
):
    assert main(argv) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == captured.out.strip() + "\n"
    error = json.loads(captured.out)
    assert captured.out == json.dumps(
        error, ensure_ascii=False, separators=(",", ":")
    ) + "\n"
    assert error["status"] == "error"
    assert error["error"]


def test_cli_help_remains_standard_success(
    capsys: pytest.CaptureFixture[str],
):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("usage: researchctl ")


def test_cli_gate_and_budget_commands_emit_compact_json(
    project_layout, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    budget_file = tmp_path / "budget-payload.json"
    budget_file.write_text(json.dumps(budget_payload()), encoding="utf-8")

    assert main(
        [
            "gate-request",
            "--root",
            str(project_layout.root),
            "--type",
            "budget",
            "--payload",
            str(budget_file),
        ]
    ) == 0
    gate = json.loads(capsys.readouterr().out)
    assert gate["status"] == "requested"

    assert main(
        [
            "gate-record",
            "--root",
            str(project_layout.root),
            "--gate-id",
            gate["gate_id"],
            "--source",
            "chat",
            "--evidence",
            "rick-approved",
        ]
    ) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "approved"

    reservation_file = tmp_path / "reservation.json"
    reservation_file.write_text(
        json.dumps({"id": "cli-r1", "wall_seconds": 2.0}), encoding="utf-8"
    )
    assert main(
        [
            "budget-reserve",
            "--root",
            str(project_layout.root),
            "--reservation",
            str(reservation_file),
        ]
    ) == 0
    reserved = json.loads(capsys.readouterr().out)
    assert reserved["reserved"]["wall_seconds"] == 2.0

    actual_file = tmp_path / "actual.json"
    actual_file.write_text(json.dumps({"wall_seconds": 1.0}), encoding="utf-8")
    assert main(
        [
            "budget-settle",
            "--root",
            str(project_layout.root),
            "--reservation-id",
            "cli-r1",
            "--actual",
            str(actual_file),
        ]
    ) == 0
    settled = json.loads(capsys.readouterr().out)
    assert settled["consumed"]["wall_seconds"] == 1.0


def test_cli_governance_errors_are_nonzero(
    project_layout, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    invalid_payload = tmp_path / "invalid-payload.json"
    invalid_payload.write_text("[]", encoding="utf-8")

    assert main(
        [
            "gate-request",
            "--root",
            str(project_layout.root),
            "--type",
            "budget",
            "--payload",
            str(invalid_payload),
        ]
    ) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"

    assert main(
        [
            "gate-record",
            "--root",
            str(project_layout.root),
            "--gate-id",
            "missing",
            "--source",
            "chat",
            "--evidence",
            "none",
        ]
    ) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
