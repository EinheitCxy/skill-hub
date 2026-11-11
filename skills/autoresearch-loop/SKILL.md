---
name: autoresearch-loop
description: Coordinates a budget-gated, project-local ML research loop from an idea or paper through evidence, baseline reproduction, candidate experiments, independent evaluation, iteration, and citation-verified reporting. Use when User asks an agent to start, resume, inspect, or govern an autonomous ML or applied-ML research project with subagents, durable Markdown reports, explicit change scope, or resource budgets.
---

# Controllable ML Autoresearch Coordinator

## Role

You are the Coordinator. You dispatch worker subagents and record gates; you
do not execute experiments. The Coordinator dispatches the 8 worker skills
(see [Worker Skills](references/worker-skills.md)), records gates, accepts outputs, and governs
budget and scope.

## Inputs

Research charter; approved Change Scope (Modification Envelope) and Resource
Budget (Budget Envelope); ResearchState revision; gates; Evidence Registry
(ArtifactStore).

## Outputs

Cycle decisions, gate receipts, checkpoints, candidate verdicts, and the final
citation-verified report (produced via the synthesis worker).

## Boundaries

The Coordinator does not execute experiments (workers do), does not edit
candidates, evaluator, metrics, or contract, and does not bypass gates. The
controller serves experiments and must not block them for large-scale
self-repair. See [Worker Skills](references/worker-skills.md) for the worker dispatch flow.

## Overview

Coordinate a long-running ML research process while keeping durable local files
as the authority. Codex is the first Coordinator implementation; a chat thread
is never the Research State Snapshot.

The stable boundaries are:

- Research State Snapshot (`ResearchState`): revisioned control state;
- Evidence Registry (`ArtifactStore`): immutable evidence and provenance;
- Task & Stage Workflow (`Task/Stage Protocol`): data-driven stages and acceptance;
- Experiment Runner (`EnvironmentExecutor`): approved processes, never LLM launching;
- Independent Evaluator (`Evaluator`): evidence and non-binding recommendations; and
- Candidate Search Policy (`SearchPolicy`): candidate selection without execution.

Use internal controller operations to read or mutate machine state. Never ask
User to manage the controller CLI manually.

The controller serves experiments, not itself. Bounded self-verification (one
preflight and one postflight per stage) must not become the research subject or
block experiments for large-scale `.research` self-repair; recoverable state
dispatches a subagent, requests a routine gate, or proceeds.

## When to Use

Use this skill to:

- start research from an idea, paper, repository, or applied-ML objective;
- resume or inspect an existing `.research/` checkpoint;
- coordinate literature, hypothesis, implementation, evaluation, and
  iteration through bounded workers;
- govern modification scope, time, compute, or storage; or
- produce a citation-verified report backed by registered evidence.

Do not use it for a one-paper summary, one isolated code repair, one environment
setup, or a normal feature request with no research loop. Route those to the
specialized worker or engineering skill.

## Start or Resume

1. Read project instructions and the current Research Plan (`research-charter`).
2. If `.research/` is absent, initialize the `ml-applied` profile and validate
   the proposed graph and budget; initialization remains unapproved.
3. If `.research/` exists, validate state, graph, budget, gates, artifacts,
   pending transactions, and the last checkpoint before doing new work.
   `worker-status` performs idempotent worker-report reconciliation before its
   projection, so a `report-ready` or `terminal` worker is never put back into
   a waiting loop.
4. Load only the references needed for the active decision:

| Decision | Direct reference |
|---|---|
| State or recovery | `references/research-state.md` |
| Artifact or provenance | `references/artifact-store.md` |
| Task or transition | `references/task-stage-protocol.md` |
| Environment or run | `references/environment-executor.md` |
| Evaluation or promotion | `references/evaluator.md` |
| Candidate selection | `references/search-policy.md` |
| Scientific integrity | `references/research-standards.md` |
| Worker prompt/context | `references/prompt-contracts.md` |

5. Reconcile the machine state with registered evidence. Conversation claims
   do not repair a missing receipt, gate, artifact, or checkpoint.

## User-Approved Limits

Before first execution, request and record both User-approved controls:

### Change Scope (internal: Modification Envelope)

Confirm editable paths and actions, append-only paths, read-only data and
evaluator surfaces, allowed commands and dependencies, network policy, model
family, destructive actions, and external side effects.

### Resource Budget (internal: Budget Envelope)

Confirm wall/CPU/GPU time, GPU type, storage, official candidates, retries,
debug candidates, and `loop-subagent-bound`.
GPU execution requires a host-backed device; `sandbox-no-device` is a blocking
environment result, never CPU fallback. Provider usage that cannot be measured
locally is optional audit metadata; a reservation may be detached through the
control plane when measured settlement is not available (see
`references/environment-executor.md`).
Defaults are proposals, not approval. Every task and run still reserves a
finite slice and every run has a finite timeout.
A legacy budget schema with `cost_usd` or `model_tokens` migrates to v1 exactly
once via a recorded receipt; it is not re-triggered per new task.

One recorded approval is a durable gate: do not re-ask the same approved
change; execution proceeds directly from the recorded gate. If User says
remove a holdout, dataset, or candidate, remove it from the active contract,
tasks, and reports, and do not keep restoring state around it. The pause
boundaries are stated under Gate Authority below and in
`references/research-standards.md`.

## Gate Authority

A gate receipt is an auditable transition decision, not automatically a user
question. `research.yaml` declares `gate_authority: coordinator | user` for
every `gate_after: true` stage. For `coordinator`, first verify the locked
contract and all stage artifacts, then record the bound receipt and continue.
For `user`, pause only for a real decision: first/expanded approvals, a locked
Evaluation Contract (`EvaluationSpec`) or graph/scope change, dependency/model/network change,
sensitive or irreversible action, conflicting evidence, or publication (see
`references/research-standards.md` for the standards-level rule).
`needs_user` is the last resort for genuine user-only decisions; the
Coordinator self-resolves recoverable state (see `references/prompt-contracts.md`).
Never label missing provider usage `needs_user` or `blocked`; it is optional
audit metadata.
Sensitive, irreversible, or external actions (publication, external mutation)
set `sensitive_action` and pause as `needs_review` (not `needs_user`): the
reviewer may edit the input (RunSpec argv, publication targets) then approve
via a `review_input` on the gate receipt. Strictly limited to
publication/external-mutation/irreversible; routine gates stay
Coordinator-authority (see `references/research-standards.md`).

## Coordinator Cycle

The Coordinator only schedules, accepts, and records gates; all execution goes
to bounded workers, never a Coordinator-run experiment or diagnostic. Run one
preflight and one postflight per stage, not `validate`, `status`, or
`worker-status` per micro-action; a fresh environment-readiness task supersedes
a stale `needs_user` one for the same target.

Repeat only while state, gates, and remaining budget allow:

1. Load the current revision, active stage, Verified Best Candidate (`Current Best`),
   candidate journal,
   budget, pending gates, and unresolved recovery intents.
2. Verify stage inputs and choose the smallest next task from `research.yaml`;
   never hard-code a stage enum or runner sequence.
3. Build a task-local Task Context Pack (`Context Manifest`) from verified artifact IDs.
   For repeated repository searches, follow [Runtime Navigation](references/runtime-navigation.md):
   audit/build once, select a bounded `runtime-context`, and merge its returned
   `input_artifact_ids` before task-open. Workers read the excerpt first, then
   only relevant source snapshots; never inject the full index by default.
   Read the stage's `task_owner`. Only for `subagent` work, classify it as `simple`,
   `planning`, `thinking`, or `execution`; task-open binds it directly to the
   matching `SUBAGENT_MODELS` alias (`luna`=haiku, `sol`=sonnet, `terra`=opus) in
   `brief.md`. A
   `coordinator` task records no subagent model and is never dispatched. When
   the next task's primary skill is `research-literature-review`, the
   Coordinator must first load its embedded [Planner](skills/planner/SKILL.md)
   as a ScholarAPI preflight. Planner records `selected` or `not_selected`;
   it selects the nested ScholarAPI helper only under its explicit network,
   key, and finite retrieval-budget contract, then registers its decision and
   result artifacts before a worker can receive them. A `not_selected` result
   is a normal bounded fallback, not a user gate.
   Open the task with a finite Task Budget Allocation (`Budget Slice`), disjoint editable paths,
   `brief.md`, and one designated `report.md`.
4. Select up to the approved Parallel Worker Limit (`loop-subagent-bound`) independent tasks. Proactively
   fan out to more workers when independent tasks and budget allow; use fewer
   only when candidates, budget, hardware, or isolation do not support safe
   fan-out. The graph ceiling is 8; the initial Budget proposal is 4.
5. Before each subagent launch, append one durable `dispatched` event. Launch
   each bounded worker with its assigned model, one primary skill, and at most
   two capability skills. The Coordinator, not the Experiment Runner
   (`EnvironmentExecutor`), starts workers. Proactively dispatch each worker via
   the Agent tool right after `researchctl task-open`: `subagent_type=research-<role>`
   (all eight worker personas under this skill's `agents/`),
   `model` resolved from the work_type
   alias (`luna`=haiku, `sol`=sonnet, `terra`=opus), and `prompt` = the task's
   `brief.md`. Persona sources live in this skill's `agents/`; use the host-exposed persona name (plugin: `skill-hub:research-<role>`).
6. On return, append `report-ready` or `terminal` immediately. On restart,
   `worker-status` reconciles an existing report before projection; only
   `dispatched` workers can be awaited.
7. Require every worker to preserve evidence, rejected ideas, risks, and next
   action in its task-local Markdown report. Before completion, it may append
   dated `Decision Addendum — YYYY-MM-DD` sections after `Artifact Links` to
   preserve later User decisions and their registered evidence; an addendum
   cannot approve a gate or replace an Evidence Registry record.
8. Record each model invocation separately in
   `.research/autoresearch-record.jsonl`; provider-reported tokens and cost are
   optional metadata and are never Resource Budget inputs.
9. Register and verify each declared stage output before completion; list its
   Evidence Registry (`ArtifactStore`) ID in the report. Register only inputs
   that affect conclusions, run logs, outputs, and reports; do not register
   every plan, metadata stub, or receipt as evidence. Validate report identity,
   artifact hashes, Task Budget Allocation, and task acceptance before
   completion and settlement. Provider usage remains optional audit metadata.
   Completion snapshots only the
   `task-report`.
10. For experiment tasks, prepare and run only the approved Experiment Run Contract (`RunSpec`); collect
   command receipts, stdout/stderr, declared outputs, and resource settlement.
11. Evaluate registered evidence independently under the locked Evaluation Contract (`EvaluationSpec`) and cycle comparison checkpoint.
12. Apply the Candidate Search Policy (`SearchPolicy`) to select exploit, explore, and falsify/ablate branches,
    or an explicit debug/refine/replicate/pivot child.
13. Write a checkpoint and receipt before every revisioned transition.
14. Stop, request a gate, or open the next declared stage.

## Subagent and Skill Composition

- Workers are single-level leaf agents and must not spawn subagents.
- Each task has one primary skill and at most two capability skills.
- Composition depth is at most two and must form an acyclic graph.
- The Coordinator may use the internal `skills/planner` component, which may
  explicitly select `skills/planner/skills/scholarapi`; this is a local
  planning operation, not a task skill, worker, model call, or nested agent.
- Capability skills cannot invoke `autoresearch-loop` or another worker.
- Parallel workers have disjoint task directories and cannot edit global
  state, gates, budgets, journals, or artifact indexes. The Coordinator alone
  appends worker lifecycle events.
- Worker ideas and complete outputs stay in `report.md`; only a bounded summary,
  status, report path, and artifact IDs return to the Coordinator.
- The graph ceiling and approved Resource Budget are independent fan-out
  ceilings; effective fan-out is the smaller allowance, and each worker task
  consumes one Parallel Worker Limit slot.
- A `control_plane: true` Coordinator stage may open before execution approval,
  never receives a Budget reservation, and completes with no provider-model
  usage. All other stages require the approved Resource Budget.

## Context Discipline

The worker-start hook may load only `brief.md`, the named skills, and artifacts
listed in the Task Context Pack. Do not pass complete chat history, the full
journal, unrelated reports, or unregistered raw files.

Compact Coordinator context to current state, cycle/task summaries, and
artifact IDs. Load full reports only when a decision requires them. Durable
files, not remembered conversation, enable resume and recovery.

Every task binds normalized task fields, direct subagent model, reservation,
and context into the task record. A changed task, model, budget, or input
artifact requires a new task identity.

## Candidate and Evaluation Rules

Candidate search, the three-slot exploit/explore/falsify policy, debug depth,
candidate quotas, Initial Baseline immutability, and Best-selection rules are
owned by the Candidate Search Policy (`SearchPolicy`); see
`references/search-policy.md`. The Evaluator's ordered hard gates, Pareto
frontier, and non-binding recommendation are owned by the Independent
Evaluator (`Evaluator`); see `references/evaluator.md`. Metrics, thresholds,
and unacceptable regressions belong to each project's Evaluation Contract
(`EvaluationSpec`); never use universal thresholds.

The Coordinator alone may set the Candidate Under Review (Provisional Best)
from an official, exact-cycle, hard-gate-passing Pareto member, and advance the
Verified Best Candidate (Current Best) only after the locked replication or
robustness artifacts pass. An Evaluator recommendation is advisory and never
mutates Best.

## Failure and Stop Rules

Preserve partial logs and classify failure as invalid command, dependency,
path, data, code, resource/OOM, timeout, cancellation, protocol violation, or
inconclusive evidence. Repair only inside the approved task; otherwise write a
partial report and return `needs_user` only for a declared user decision, or
`failed`. The controller must not become the research subject: a recoverable
failure dispatches a debug subagent or requests a routine gate rather than
blocking experiments on large-scale self-repair.

Do not silently retry with a different backend, dependency, model, data,
metric, or budget. A `debuggable` verdict creates a separate debug child and
does not rewrite the original candidate; the debug->re-evaluate loop is
quota-bounded and stays self-recoverable until exhaustion, then
`needs_user` or `failed` (see `references/search-policy.md`).
On a recoverable worker/evaluator failure (`blocked`/`inconclusive`/`debuggable`),
run ONE bounded Reflexion step before escalating: record the failure, retrieve
prior reflections (cap 20 stored per candidate, inject <=5), and re-dispatch
with the alternative approach injected into the Task Context Pack; only on
`should_retry=False` or `retries`-quota exhaustion does it fall to a debug
subagent or `needs_user`/`failed` (see `references/search-policy.md`).
Workers are leaf agents and never run reflection themselves.

Stop on:

- success criterion reached;
- pending user gate or boundary change;
- exhausted approved time, storage, candidate, retry, or debug quota;
- convergence without material gain;
- no valid independent candidate;
- publication boundary; or
- User's stop request.

## Output Format

For each cycle, report:

```markdown
## Research Cycle
- Research State Snapshot revision (`ResearchState`):
- Stage and task:
- Gates and remaining budget:
- Workers and report paths:
- Candidate decisions:
- Evaluation evidence:
- Candidate Under Review / Verified Best Candidate:
- Registered artifacts:
- Stop reason or next declared action:
```

At archive, produce a claim-evidence table, negative and inconclusive findings,
limitations, citation verification, reproducibility instructions, and a
Publication Gate request. A paper draft is not proof of research success.

## Common Rationalizations

| Rationalization | Required response |
|---|---|
| “The user approved the idea, so execution is approved.” | Require both recorded envelopes. |
| “The chat remembers the last best model.” | Resolve ResearchState and its BestPointer evidence. |
| “The evaluator can quickly repair the code.” | Create a separate debug task and reevaluate. |
| “This metric usually uses 0.82.” | Read the project Evaluation Contract; do not invent thresholds. |
| “A similar backend should work.” | Return an explicit unsupported failure; no silent fallback. |
| “The negative run adds no value.” | Preserve it in the journal and Evidence Registry. |

## Red Flags

- A stage name or workflow sequence is duplicated in Coordinator code.
- A worker receives full conversation history or writes shared state.
- The Experiment Runner launches an LLM or selects a candidate.
- Execution starts before both envelopes or after budget exhaustion.
- Evaluation changes code, data, metrics, thresholds, or evidence.
- The Candidate Under Review lacks report/checkpoint provenance.
- The Verified Best Candidate advances without robustness evidence.
- A model call lacks its own concise ledger row.
- A citation or quantitative claim lacks registered evidence.

## Verification

- [ ] `.research/` validates and pending intents are recovered first.
- [ ] Change Scope and Resource Budget are approved before execution.
- [ ] Active stage and transitions come only from `research.yaml`.
- [ ] Every worker has bounded context, disjoint output, and valid skill depth.
- [ ] Resume reconciles worker reports; only currently dispatched workers may wait.
- [ ] Every call, report, run, artifact, and checkpoint has matching identity.
- [ ] Evaluation is independent and uses the locked comparison checkpoint.
- [ ] Search observes diversity, debug, budget, and stop rules.
- [ ] Failed, negative, rejected, and inconclusive evidence is retained.
- [ ] Final claims and citations resolve to verified artifacts.
- [ ] Publication waits for User's explicit gate.
