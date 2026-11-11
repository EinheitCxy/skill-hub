from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence, TypedDict

JsonObject = dict[str, Any]


class BestPointer(TypedDict):
    candidate_id: str
    report_artifact_id: str | None
    evaluation_cycle_checkpoint_id: str | None
    promoted_at: str


class ActiveEvaluationSpecAnchor(TypedDict):
    evaluation_spec_id: str
    artifact_id: str
    artifact_sha256: str
    gate_id: str
    gate_payload_hash: str


class StorageAccountingPolicy(TypedDict):
    count_snapshot_copy_once: Literal[True]
    excluded_artifact_types: list[str]


class EvaluationCycleCheckpoint(TypedDict):
    schema_version: Literal[1]
    record_type: Literal["evaluation-cycle-checkpoint"]
    checkpoint_id: str
    cycle_id: str
    evaluation_spec_id: str
    evaluation_spec_artifact_id: str
    evaluation_spec_artifact_sha256: str
    comparison_candidate_id: str
    comparison_report_artifact_id: str | None
    comparison_state_revision: int
    comparison_state_hash: str
    bound_at: str


class ResearchStateDocument(TypedDict):
    schema_version: Literal[1]
    revision: int
    research_id: str
    current_stage: str
    previous_state_hash: str | None
    last_transition_id: str | None
    pending_gate: str | None
    initial_baseline: str | None
    provisional_best: BestPointer | None
    current_best: BestPointer | None
    active_evaluation_spec: ActiveEvaluationSpecAnchor | None


@dataclass(frozen=True)
class StateSnapshot:
    schema_version: Literal[1]
    revision: int
    research_id: str
    current_stage: str
    previous_state_hash: str | None
    last_transition_id: str | None
    pending_gate: str | None
    initial_baseline: str | None
    provisional_best: BestPointer | None
    current_best: BestPointer | None
    active_evaluation_spec: ActiveEvaluationSpecAnchor | None


@dataclass(frozen=True)
class TransitionResult:
    status: Literal["applied", "blocked", "invalid"]
    state_revision: int
    receipt_path: Path | None
    reason: str


@dataclass(frozen=True)
class EnvironmentSpec:
    environment_id: str
    backend: Literal["uv-local", "command-local"]
    cwd: Path
    prepare_argv: tuple[str, ...]
    dependencies: tuple[str, ...]
    dependency_lock_path: Path
    dependency_lock_hash: str
    allowed_write_paths: tuple[Path, ...]
    temporary_paths: tuple[Path, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    environment_id: str
    dependency_lock_hash: str
    backend: Literal["uv-local", "command-local"]
    cwd: Path
    argv: tuple[str, ...]
    timeout_seconds: int
    resource_limits: Mapping[str, int | float | str | None]
    input_paths: tuple[Path, ...]
    output_paths: tuple[Path, ...]
    expected_artifact_types: tuple[str, ...]
    candidate_id: str
    evaluation_spec_id: str
    evaluation_cycle_checkpoint_id: str
    sensitive_action: bool = False


@dataclass(frozen=True)
class EvaluationReport:
    report_id: str
    candidate_id: str
    hard_gates_passed: bool
    verdict: Literal[
        "keep", "discard", "inconclusive", "debuggable", "needs_user", "failed"
    ]
    metrics: Mapping[str, float]
    confounds: tuple[str, ...]


class ResearchState(Protocol):
    def load(self) -> StateSnapshot: ...
    def compare_and_swap(
        self, expected_revision: int, next_state: StateSnapshot
    ) -> StateSnapshot: ...


class ArtifactStore(Protocol):
    def register(self, record: JsonObject) -> JsonObject: ...
    def resolve(self, artifact_id: str) -> JsonObject: ...
    def list(self, filters: Mapping[str, str]) -> Sequence[JsonObject]: ...
    def verify(self, artifact_id: str) -> JsonObject: ...
    def verify_run_snapshots(
        self,
        artifact_ids: Sequence[str],
        *,
        run_id: str,
        expected_types: Sequence[str],
    ) -> Sequence[JsonObject]: ...
    def mark_unavailable(self, artifact_id: str, reason: str) -> JsonObject: ...


class TaskStageProtocol(Protocol):
    def stage(self, stage_id: str) -> JsonObject: ...
    def can_transition(self, source: str, target: str) -> bool: ...


class EnvironmentExecutor(Protocol):
    def inspect(self, spec: EnvironmentSpec) -> JsonObject: ...
    def prepare(self, spec: EnvironmentSpec) -> JsonObject: ...
    def run(self, spec: RunSpec) -> JsonObject: ...
    def cancel(self, run_id: str) -> JsonObject: ...
    def collect(self, run_id: str) -> JsonObject: ...
    def cleanup(self, run_id: str) -> JsonObject: ...


class Evaluator(Protocol):
    def evaluate(
        self, evaluation_spec: JsonObject, run_artifacts: Sequence[JsonObject]
    ) -> EvaluationReport: ...


class SearchPolicy(Protocol):
    def select(
        self, candidates: Sequence[JsonObject], slots: int
    ) -> Sequence[JsonObject]: ...
    def should_stop(self, state: StateSnapshot, budget: JsonObject) -> bool: ...
