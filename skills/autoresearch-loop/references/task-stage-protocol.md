# Task & Stage Workflow (internal: Task/Stage Protocol)

## Purpose and owner

The Task/Stage Protocol keeps the research workflow declarative. Stage IDs and
edges are data in `.research/research.yaml`; they are not Python enums and are
not hard-coded in a runner. The Coordinator owns task creation, completion,
gate checks, checkpoints, and transitions. A worker owns only its task-local
`report.md` and declared output files.

Internal machine operations are `task-open`, `task-complete`, `checkpoint`, and
`transition`. Direct edits to task records, journal receipts, or live state are
forbidden.

## Runtime division

The local CLI and controller files own durable facts, permissions, budget
accounting, and recovery. Codex is the first research Coordinator and decides
which approved task to open; it does not turn its complete chat history into
implicit state.

Each worker receives a task-local Task Context Pack (internal: `Context
Manifest`) generated from `brief.md` and registered input artifacts. A
worker-start hook may load
that bounded pack before the worker begins. The hook may not inject unrelated
conversation history or bypass artifact verification.

The Experiment Runner (internal: `EnvironmentExecutor`) prepares dependencies
and owns experiment processes. It
must not launch an LLM, choose a worker, or become a second Coordinator.

## Research graph

`research.yaml` requires `schema_version`, `profile`, `start_stage`, `limits`,
and `stages`. Each stage requires:

- `id`: unique data-defined stage identity;
- `purpose`: concise research objective;
- `task_owner`: `coordinator` for controller work or `subagent` for a launched
  worker;
- `control_plane`: optional boolean, default `false`; only `true` Coordinator
  stages may open before execution approval, never receive a Budget
  reservation, and complete with no provider-model usage;
- `primary_skill`: one Coordinator or worker skill;
- `capability_skills`: zero to two bounded helper skills;
- `inputs`: artifact-type preconditions;
- `outputs`: artifact-type acceptance criteria;
- `parallelizable`: whether independent tasks may fan out;
- `gate_after`: whether transition requires an approved gate;
- `gate_type`: `charter`, `evaluation`, or `publication` when
  `gate_after: true` (otherwise `null`); and
- `gate_authority`: `coordinator` or `user` when `gate_after: true`
  (otherwise `null`); and
- `next`: declared outbound stage IDs.

`gate_after` requires a durable receipt; it is not itself a user prompt. A
Coordinator gate is mechanically derived from the verified stage outputs and
is bound to its stage ID, source revision, state hash, graph hash, and output
artifact IDs plus hashes. A user gate requires a recorded user decision.
Coordinator receipts can never satisfy a `user` gate, and an unbound or
cross-stage receipt never satisfies either authority.

Every `next` target must exist. Skill composition must be acyclic, the
Coordinator cannot appear as a capability, and composition depth is at most
two. The Parallel Worker Limit (internal: `loop-subagent-bound`) and the other
graph limits remain data.

## Task contract

One task specification contains exactly `cycle_id`, `task_id`, `candidate_id`,
`stage_id`, `primary_skill`, `capability_skills`, `input_artifact_ids`,
`budget_slice`, `editable_paths`, `output_path`, `stop_conditions`, and
`work_type`. For a `subagent`-owned stage, the controller assigns exactly one
subagent model:

| Work type | Model |
| --- | --- |
| `simple` | `luna` |
| `planning` | `sol` |
| `thinking` | `terra` |
| `execution` | `terra` |

Opening a task:

1. requires approved Change Scope and Resource Budget unless it is a
   `control_plane: true` Coordinator task;
2. requires `stage_id` to equal `ResearchState.current_stage`;
3. requires the primary skill to equal the active stage declaration and every
   selected capability to belong to its declared capability pool;
4. verifies every input artifact, requires every declared stage input type,
   and builds a bounded Task Context Pack (`Context Manifest`);
5. rejects a second open task when the active stage is not parallelizable;
6. reserves the task Budget Allocation (`Budget Slice`) for every
   non-control-plane task and, only for a subagent, one Parallel Worker Limit
   (`loop-subagent-bound`) slot; a control-plane Coordinator
   task never reserves budget, even after both approvals are recorded;
7. computes and persists the owner and direct model (or `null` for a
   Coordinator), then hashes the task, owner, model, skills,
   reservation, and context into `prompt_hash`;
8. writes task-local `brief.md` and `task.json`; and
9. designates one task-local `report.md`.

The worker must not receive complete chat history. It reads only the brief,
listed artifacts, one primary skill, and at most two capability skills.

## Durable worker events

Before launch, the Coordinator appends `dispatched` to the locked
`.research/worker-events.jsonl`. On return it appends either `report-ready` or
`terminal`; workers never write this shared journal. The journal retains only
identity, attempt, prompt hash, assigned model, report path, and short terminal
reason. It does not copy report content, model-call usage, budget data, or
ResearchState.

On resume, `worker-status` first reconciles an existing task-local `report.md`
into `report-ready`, then projects worker status. `report-ready` is completed
by the existing `task-complete` authority; `terminal` exposes its concise
status and reason for a decision. Neither status may be awaited again. A Task
has one worker lifecycle; a retry opens a new Task rather than overwriting an
old terminal fact.

## Completion and acceptance criteria

`report.md` frontmatter binds `task_id`, `candidate_id`, terminal `status`,
`prompt_hash`, registered `artifact_ids`, and `decision_recommendation`. The
body retains Summary, Evidence, Ideas Considered, Rejected Ideas, Risks and
Confounds, Recommended Next Action, and Artifact Links.

A worker submits the bound identity fields with `artifact_ids: []`; it never
copies `input_artifact_ids` into report frontmatter. After registering and
verifying task-bound outputs, the Coordinator runs
`researchctl task-report-finalize --root <project> --task-id <task> --artifact-id <id-1> --artifact-id <id-2>`
once, with one repeated `--artifact-id` for each output. That locked atomic
finalization operation validates provenance and the full declared output set,
then semantically changes only `artifact_ids` before `task-complete` while
preserving the report body byte-for-byte.

Before completion, the report may append zero or more dated
`Decision Addendum — YYYY-MM-DD` sections after `Artifact Links`. They retain
later user decisions or evidence corrections while the report is still mutable;
the date must be valid, arbitrary headings remain invalid, and an addendum
cannot approve a gate or replace a registered artifact.

Completion verifies the exact output path and prompt hash, resolves and hashes
all referenced evidence, and snapshots the report as `task-report`. Each
declared stage output must have been separately registered as a real,
verified Evidence Registry (`ArtifactStore`) record and be cited by ID in the
report; completion
rejects any report whose cited artifact types do not cover every declared
`outputs` type, and does not create a typed alias of `report.md`. It validates
the measured wall time against the Task Budget Allocation (`Budget Slice`),
settles the reservation, and appends one completion receipt. Provider tokens
and cost are optional audit metadata: their absence never blocks completion or
causes fabricated usage. A control-plane Coordinator task has no reservation
and completes without a settlement. A recommendation is evidence, not
authority to mutate Current Best.

A transition from stage A to B is allowed only when:

- A is the current state at the expected revision;
- B appears in A's `next`;
- every task in A is completed;
- A's declared output artifact types are present and valid;
- the source stage's declared `gate_type` is approved by its declared
  `gate_authority` when `gate_after: true`;
  and
- the source state, graph, and budget match the checkpoint evidence.

Thus `inputs` are the stage preconditions and verified `outputs` are its
acceptance criteria.

## Allowed mutations

- The Coordinator may create a new task-local directory and reserve its budget.
- A worker may write only paths declared in that task's editable envelope and
  its assigned `report.md`.
- The Coordinator may complete the same task intent idempotently.
- The Coordinator may transition only across a declared `next` edge, after
  writing a checkpoint and receipt, using compare-and-swap revision control.
- A graph revision is an explicitly reviewed configuration change; it cannot be
  smuggled into an in-flight transition or performed by editing the live YAML.
  The Coordinator requests a hash-bound `graph` gate, records approval, then
  applies `graph-revise`. The transaction requires no open tasks, retains the
  active stage, checkpoints the source graph/state, advances State revision,
  and receipts source/target graph hashes. Its gate, checkpoint, receipt, and
  target State are mutually bound; `validate` rejects an uncommitted or
  incomplete revision. A pending revision resumes from its durable transaction
  by gate ID, without requiring the original YAML file.

## Invalid examples

```yaml
- id: evaluate
  primary_skill: evaluator
  next: [publish]
```

Invalid: required stage fields are missing and `publish` may be unknown.

```yaml
capability_skills:
  - autoresearch-loop
```

Invalid: the Coordinator cannot be nested as a capability.

Invalid runtime actions include opening a task for a different stage, loading
an unlisted artifact, completing with another prompt hash, transitioning with
an unfinished task, skipping a required gate, or jumping to a stage absent
from `next`.

## Verification checklist

- Validate the exact `research.yaml` graph and every `next` target.
- Confirm stage IDs are read from data, not duplicated in an enum or runner.
- Confirm one primary skill, at most two capability skills, and no composition
  cycle.
- Confirm every task input resolves to a valid registered artifact.
- Confirm task budget, editable paths, Task Context Pack (`Context Manifest`),
  and prompt hash match
  the durable task record.
- Confirm every report section and frontmatter identity before completion.
- Confirm stage output acceptance criteria and the applicable gate before
  transition.
- Confirm checkpoint, receipt, source revision, source hash, target revision,
  and target hash agree after transition.
