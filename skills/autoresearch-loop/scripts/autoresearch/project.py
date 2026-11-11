from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any

import yaml

from autoresearch.contracts import JsonObject, ResearchStateDocument
from autoresearch.io import atomic_write_json, read_json, sha256_file
from autoresearch.skills import preflight_graph_skills


GRAPH_FIELDS = frozenset(
    {"schema_version", "profile", "start_stage", "limits", "stages"}
)
LIMIT_FIELDS = frozenset(
    {
        "loop-subagent-bound",
        "max_debug_depth",
        "max_skill_depth",
        "max_capability_skills",
    }
)
DEFAULT_LOOP_SUBAGENT_BOUND = 4
STAGE_FIELDS = frozenset(
    {
        "id",
        "purpose",
        "task_owner",
        "primary_skill",
        "capability_skills",
        "inputs",
        "outputs",
        "parallelizable",
        "gate_after",
        "gate_type",
        "gate_authority",
        "next",
    }
)
TASK_OWNERS = frozenset({"coordinator", "subagent"})
REPORT_TEMPLATE = (
    Path(__file__).parents[2] / "assets" / "research-project" / "report.md"
)
RESEARCH_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "revision",
        "research_id",
        "current_stage",
        "previous_state_hash",
        "last_transition_id",
        "pending_gate",
        "initial_baseline",
        "provisional_best",
        "current_best",
        "active_evaluation_spec",
    }
)
BEST_POINTER_FIELDS = frozenset(
    {
        "candidate_id",
        "report_artifact_id",
        "evaluation_cycle_checkpoint_id",
        "promoted_at",
    }
)
ACTIVE_EVALUATION_SPEC_FIELDS = frozenset(
    {
        "evaluation_spec_id",
        "artifact_id",
        "artifact_sha256",
        "gate_id",
        "gate_payload_hash",
    }
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
RFC3339_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)


@dataclass(frozen=True)
class ProjectLayout:
    root: Path
    graph: Path
    state: Path
    budget: Path
    budget_settlements: Path
    budget_transactions: Path
    budget_migrations: Path
    reservation_detachments: Path
    gates: Path
    journal: Path
    worker_events: Path
    model_calls: Path
    artifact_index: Path
    artifact_aliases: Path
    cycles: Path
    runs: Path
    checkpoints: Path
    quarantine: Path

    @classmethod
    def from_root(cls, root: Path) -> ProjectLayout:
        research = root / ".research"
        return cls(
            root=root,
            graph=research / "research.yaml",
            state=research / "state.json",
            budget=research / "budget.json",
            budget_settlements=research / "budget-settlements.jsonl",
            budget_transactions=research / "budget-transactions",
            budget_migrations=research / "budget-migrations.jsonl",
            reservation_detachments=research / "reservation-detachments.jsonl",
            gates=research / "gates.jsonl",
            journal=research / "journal.jsonl",
            worker_events=research / "worker-events.jsonl",
            model_calls=research / "autoresearch-record.jsonl",
            artifact_index=research / "artifacts" / "index.jsonl",
            artifact_aliases=research / "artifacts" / "aliases.jsonl",
            cycles=research / "cycles",
            runs=research / "runs",
            checkpoints=research / "checkpoints",
            quarantine=research / "quarantine",
        )


def load_graph(path: Path) -> JsonObject:
    try:
        with path.open(encoding="utf-8") as stream:
            graph = yaml.safe_load(stream)
    except yaml.YAMLError as error:
        raise ValueError(f"could not load YAML: {error}") from error
    if not isinstance(graph, Mapping):
        raise ValueError("YAML root must be a mapping")
    return dict(graph)


def load_research_state(layout: ProjectLayout) -> ResearchStateDocument:
    state = read_json(layout.state)
    graph = load_graph(layout.graph)
    return validate_research_state_document(state, graph)


def _create_initial_research_state(
    layout: ProjectLayout,
    *,
    research_id: str,
    current_stage: str,
) -> ResearchStateDocument:
    if layout.state.is_file():
        raise FileExistsError(f"research state already exists: {layout.state}")
    state = {
        "schema_version": 1,
        "revision": 0,
        "research_id": research_id,
        "current_stage": current_stage,
        "previous_state_hash": None,
        "last_transition_id": None,
        "pending_gate": None,
        "initial_baseline": None,
        "provisional_best": None,
        "current_best": None,
        "active_evaluation_spec": None,
    }
    validated = validate_research_state_document(state, load_graph(layout.graph))
    atomic_write_json(layout.state, validated)
    return validated


def validate_research_state_document(
    state: Mapping[str, Any], graph: Mapping[str, Any]
) -> ResearchStateDocument:
    try:
        return _validate_research_state_document(state, graph)
    except ValueError as error:
        raise ValueError(
            "invalid ResearchState; reinitialize .research project: "
            f"{error}"
        ) from error


def _validate_research_state_document(
    state: Mapping[str, Any], graph: Mapping[str, Any]
) -> ResearchStateDocument:
    if not isinstance(state, Mapping):
        raise ValueError("document must be an object")
    _require_exact_fields(state, RESEARCH_STATE_FIELDS, "ResearchState")

    schema_version = state["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise ValueError("schema_version must be integer 1")
    revision = state["revision"]
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
    ):
        raise ValueError("revision must be a non-negative integer")
    _nonempty_string(state["research_id"], "research_id")
    current_stage = _nonempty_string(state["current_stage"], "current_stage")
    stages = graph.get("stages")
    if not isinstance(stages, list):
        raise ValueError("active graph stages must be a list")
    declared_stages = {
        item.get("id")
        for item in stages
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    if current_stage not in declared_stages:
        raise ValueError("current_stage is not declared by the active graph")

    previous_state_hash = state["previous_state_hash"]
    if revision == 0:
        if previous_state_hash is not None:
            raise ValueError("previous_state_hash must be null at revision 0")
    elif not _is_sha256(previous_state_hash):
        raise ValueError(
            "previous_state_hash must be a lowercase 64-character SHA-256"
        )
    _nullable_string(state["last_transition_id"], "last_transition_id")
    if revision == 0 and state["last_transition_id"] is not None:
        raise ValueError("last_transition_id must be null at revision 0")
    _nullable_string(state["pending_gate"], "pending_gate")
    initial_baseline = _nullable_string(
        state["initial_baseline"], "initial_baseline"
    )
    provisional_best = _validate_best_pointer(
        state["provisional_best"], "provisional_best"
    )
    current_best = _validate_best_pointer(
        state["current_best"], "current_best"
    )
    _validate_evaluation_anchor(state["active_evaluation_spec"])

    if initial_baseline is None:
        if provisional_best is not None or current_best is not None:
            raise ValueError(
                "initial_baseline, provisional_best, and current_best "
                "must all be null before baseline bootstrap"
            )
    elif current_best is None:
        raise ValueError("current_best is required after baseline bootstrap")

    if provisional_best is not None:
        _require_verified_pointer(provisional_best, "provisional_best")
    if current_best is not None:
        report_id = current_best["report_artifact_id"]
        checkpoint_id = current_best["evaluation_cycle_checkpoint_id"]
        if report_id is None and checkpoint_id is None:
            if current_best["candidate_id"] != initial_baseline:
                raise ValueError(
                    "current_best bootstrap candidate must equal initial_baseline"
                )
        else:
            _require_verified_pointer(current_best, "current_best")

    return dict(state)  # type: ignore[return-value]


def _require_exact_fields(
    value: Mapping[str, Any], required: frozenset[str], name: str
) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required
    if missing:
        raise ValueError(f"{name} missing required field: {sorted(missing)[0]}")
    if unknown:
        raise ValueError(f"{name} has unknown field: {sorted(unknown)[0]}")


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _nullable_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _validate_best_pointer(value: Any, field: str) -> JsonObject | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a BestPointer object or null")
    _require_exact_fields(value, BEST_POINTER_FIELDS, field)
    _nonempty_string(value["candidate_id"], f"{field}.candidate_id")
    _nullable_string(
        value["report_artifact_id"], f"{field}.report_artifact_id"
    )
    _nullable_string(
        value["evaluation_cycle_checkpoint_id"],
        f"{field}.evaluation_cycle_checkpoint_id",
    )
    promoted_at = _nonempty_string(value["promoted_at"], f"{field}.promoted_at")
    if RFC3339_PATTERN.fullmatch(promoted_at) is None:
        raise ValueError(f"{field}.promoted_at must be an RFC-3339 timestamp")
    try:
        datetime.fromisoformat(promoted_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            f"{field}.promoted_at must be an RFC-3339 timestamp"
        ) from error
    return dict(value)


def _require_verified_pointer(pointer: Mapping[str, Any], field: str) -> None:
    if (
        pointer["report_artifact_id"] is None
        or pointer["evaluation_cycle_checkpoint_id"] is None
    ):
        raise ValueError(f"{field} requires both verified provenance IDs")


def _validate_evaluation_anchor(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise ValueError(
            "active_evaluation_spec must be an "
            "ActiveEvaluationSpecAnchor object or null"
        )
    field = "active_evaluation_spec"
    _require_exact_fields(value, ACTIVE_EVALUATION_SPEC_FIELDS, field)
    for name in ACTIVE_EVALUATION_SPEC_FIELDS:
        _nonempty_string(value[name], f"{field}.{name}")
    for name in ("artifact_sha256", "gate_payload_hash"):
        if not _is_sha256(value[name]):
            raise ValueError(
                f"{field}.{name} must be a lowercase 64-character SHA-256"
            )


def validate_graph(graph: Mapping[str, Any]) -> list[str]:
    if not isinstance(graph, Mapping):
        return ["graph root must be a mapping"]

    errors: list[str] = []
    for field in sorted(GRAPH_FIELDS - graph.keys()):
        errors.append(f"missing required field: {field}")

    limits = graph.get("limits")
    if isinstance(limits, Mapping):
        for field in sorted(LIMIT_FIELDS - limits.keys()):
            errors.append(f"limits missing required field: {field}")
        for field in sorted(limits.keys() - LIMIT_FIELDS):
            errors.append(f"limits unknown field: {field}")
        for field in ("loop-subagent-bound", "max_debug_depth"):
            value = limits.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                errors.append(
                    f"limits {field} must be a non-negative integer"
                )
    elif "limits" in graph:
        errors.append("limits must be a mapping")

    stages = graph.get("stages")
    if not isinstance(stages, list):
        if "stages" in graph:
            errors.append("stages must be a list")
        return errors

    stage_ids: list[str] = []
    next_targets: list[str] = []
    skill_edges: set[tuple[str, str]] = set()
    primary_skills: set[str] = set()

    for index, item in enumerate(stages):
        if not isinstance(item, Mapping):
            errors.append(f"stage {index} must be a mapping")
            continue

        for field in sorted(STAGE_FIELDS - item.keys()):
            errors.append(f"stage {index} missing required field: {field}")

        stage_id = item.get("id")
        if isinstance(stage_id, str):
            stage_ids.append(stage_id)
        elif "id" in item:
            errors.append(f"stage {index} id must be a string")

        task_owner = item.get("task_owner")
        if task_owner not in TASK_OWNERS:
            errors.append(
                f"stage {stage_id or index} task_owner must be coordinator or subagent"
            )
        control_plane = item.get("control_plane", False)
        if not isinstance(control_plane, bool):
            errors.append(
                f"stage {stage_id or index} control_plane must be a boolean"
            )
        elif control_plane and task_owner != "coordinator":
            errors.append(
                f"stage {stage_id or index} control_plane requires task_owner coordinator"
            )

        next_stages = item.get("next")
        if isinstance(next_stages, list) and all(
            isinstance(target, str) for target in next_stages
        ):
            next_targets.extend(next_stages)
        elif "next" in item:
            errors.append(f"stage {stage_id or index} next must be a list of strings")

        gate_after = item.get("gate_after")
        gate_type = item.get("gate_type")
        gate_authority = item.get("gate_authority")
        if not isinstance(gate_after, bool):
            errors.append(f"stage {stage_id or index} gate_after must be a boolean")
        elif gate_after:
            if gate_type not in {"charter", "evaluation", "publication"}:
                errors.append(
                    f"stage {stage_id or index} gate_type must name a declared gate"
                )
            if gate_authority not in {"coordinator", "user"}:
                errors.append(
                    f"stage {stage_id or index} gate_authority must be coordinator or user when gate_after is true"
                )
            elif gate_authority == "coordinator" and gate_type == "publication":
                errors.append(
                    f"stage {stage_id or index} publication gate_authority must be user"
                )
        else:
            if gate_type is not None:
                errors.append(
                    f"stage {stage_id or index} gate_type must be null when gate_after is false"
                )
            if gate_authority is not None:
                errors.append(
                    f"stage {stage_id or index} gate_authority must be null when gate_after is false"
                )

        primary_skill = item.get("primary_skill")
        capabilities = item.get("capability_skills")
        if isinstance(primary_skill, str):
            primary_skills.add(primary_skill)
        elif "primary_skill" in item:
            errors.append(f"stage {stage_id or index} primary_skill must be a string")

        outputs = item.get("outputs")
        if not isinstance(outputs, list) or not all(
            isinstance(output, str) and output for output in outputs
        ):
            errors.append(
                f"stage {stage_id or index} outputs must be a list of non-empty strings"
            )
        elif len(set(outputs)) != len(outputs):
            errors.append(f"stage {stage_id or index} outputs must not contain duplicates")

        if isinstance(capabilities, list) and all(
            isinstance(capability, str) for capability in capabilities
        ):
            if len(capabilities) > 2:
                errors.append(
                    f"stage {stage_id or index} has more than two capability skills"
                )
            if "autoresearch-loop" in capabilities:
                errors.append("autoresearch-loop cannot be a capability skill")
            if isinstance(primary_skill, str):
                skill_edges.update(
                    (primary_skill, capability) for capability in capabilities
                )
        elif "capability_skills" in item:
            errors.append(
                f"stage {stage_id or index} capability_skills must be a list of strings"
            )

    seen_stage_ids: set[str] = set()
    duplicate_stage_ids: set[str] = set()
    for stage_id in stage_ids:
        if stage_id in seen_stage_ids:
            duplicate_stage_ids.add(stage_id)
        seen_stage_ids.add(stage_id)
    errors.extend(
        f"duplicate stage id: {stage_id}"
        for stage_id in sorted(duplicate_stage_ids)
    )

    known_stage_ids = set(stage_ids)
    errors.extend(
        f"unknown next target: {target}"
        for target in sorted(set(next_targets) - known_stage_ids)
    )

    start_stage = graph.get("start_stage")
    if isinstance(start_stage, str) and start_stage not in known_stage_ids:
        errors.append(f"missing start stage: {start_stage}")
    elif "start_stage" in graph and not isinstance(start_stage, str):
        errors.append("start_stage must be a string")

    skill_graph = _adjacency(skill_edges)
    if _contains_cycle(skill_graph):
        errors.append("skill composition graph contains a cycle")
    if _skill_depth_exceeds_two(primary_skills, skill_graph):
        errors.append("skill composition depth exceeds two")

    return errors


def initialize_project(root: Path, profile: Path) -> ProjectLayout:
    graph = load_graph(profile)
    errors = validate_graph(graph)
    if errors:
        raise ValueError(f"invalid research graph: {'; '.join(errors)}")
    preflight_graph_skills(graph)
    if not REPORT_TEMPLATE.is_file():
        raise FileNotFoundError(f"report template does not exist: {REPORT_TEMPLATE}")

    layout = ProjectLayout.from_root(root)
    research = root / ".research"
    if research.exists():
        raise FileExistsError(f"refusing to overwrite existing {research}")

    research.mkdir(parents=True)
    layout.artifact_index.parent.mkdir()
    layout.cycles.mkdir()
    layout.runs.mkdir()
    layout.checkpoints.mkdir()
    layout.budget_transactions.mkdir()
    layout.quarantine.mkdir()

    layout.graph.write_text(
        yaml.safe_dump(graph, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    _create_initial_research_state(
        layout,
        research_id=root.resolve().name,
        current_stage=graph["start_stage"],
    )
    atomic_write_json(
        layout.budget,
        _unapproved_budget(graph["limits"]["loop-subagent-bound"]),
    )

    for ledger in (
        layout.gates,
        layout.journal,
        layout.worker_events,
        layout.model_calls,
        layout.artifact_index,
        layout.budget_settlements,
        layout.budget_migrations,
        layout.reservation_detachments,
    ):
        ledger.touch()

    (research / "report.md").write_text(
        REPORT_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    checkpoint = {
        "revision": 0,
        "reason": "initialization",
        "state_hash": sha256_file(layout.state),
        "graph_hash": sha256_file(layout.graph),
        "budget_hash": sha256_file(layout.budget),
        "ledger_line_counts": {
            "gates": 0,
            "journal": 0,
            "model_calls": 0,
            "artifact_index": 0,
            "budget_settlements": 0,
        },
    }
    atomic_write_json(layout.checkpoints / "000000-initial.json", checkpoint)
    return layout


def _adjacency(edges: set[tuple[str, str]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set())
    return adjacency


def _contains_cycle(adjacency: Mapping[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for target in adjacency.get(node, set()):
            if visit(target):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in adjacency)


def _skill_depth_exceeds_two(
    primary_skills: set[str], skill_graph: Mapping[str, set[str]]
) -> bool:
    def descend(node: str, depth: int, path: frozenset[str]) -> bool:
        if depth > 2:
            return True
        return any(
            descend(target, depth + 1, path | {target})
            for target in skill_graph.get(node, set())
            if target not in path
        )

    return any(
        descend(primary_skill, 1, frozenset({primary_skill}))
        for primary_skill in primary_skills
    )


def _unapproved_budget(graph_loop_subagent_bound: int) -> JsonObject:
    loop_subagent_bound = min(
        DEFAULT_LOOP_SUBAGENT_BOUND, graph_loop_subagent_bound
    )
    counters: JsonObject = {
        "wall_seconds": 0,
        "cpu_seconds": 0,
        "gpu_seconds": 0,
        "storage_bytes": 0,
    }
    return {
        "status": "unapproved",
        "limits": {
            "wall_seconds": 86400,
            "cpu_seconds": 86400,
            "gpu_seconds": 86400,
            "storage_bytes": 2147483648,
            "official_candidates": 3,
            "retries": 3,
            "debug_candidates": 3,
            "loop-subagent-bound": loop_subagent_bound,
            "gpu_type": None,
        },
        "reserved": dict(counters),
        "reservations": {},
        "consumed": dict(counters),
    }
