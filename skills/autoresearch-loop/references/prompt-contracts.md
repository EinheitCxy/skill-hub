# Prompt and Context Contract

## Purpose and owner

The Coordinator constructs one bounded prompt for one task-local worker. A
prompt is a reproducible artifact projection, not the system of record. Local
controller files remain authoritative for state, permission, budget, task
identity, and recovery.

A subagent-owned task gets exactly one primary skill and at most two Coordinator-selected
capability skills. Skills are loaded by explicit name. Workers are leaf agents.
Workers must not spawn subagents. Nested teams and implicit skill routing are
forbidden.

The only non-worker nesting is the Coordinator's explicit internal component
`skills/planner`, which may select its declared `skills/scholarapi` child under
the Planner contract. It is neither a task primary/capability skill nor a
worker: it launches no agent, uses no worker slot, and adds no model-call row.
Its selection record and any metadata result become immutable artifacts before
a later task receives their IDs through its Task Context Pack (`Context Manifest`).

A Coordinator-owned task is not a worker prompt: it has no assigned subagent
model, worker lifecycle event, or worker slot. Its task record and report still
remain durable evidence.

## Ten approved prompt blocks

Every worker prompt is assembled in this order:

1. **Role Boundary**: worker identity, owned responsibility, and non-authority;
2. **Research Plan** (internal: `research-charter`): question, scope, success criteria, and constraints;
3. **Current Task Contract**: cycle/task/candidate/stage identity and goal;
4. **Change Scope** (internal: `Modification Envelope`): editable, append-only, and read-only paths;
5. **Task Budget Allocation** (internal: `Budget Slice`): finite task allowance and stop threshold;
6. **Task Context Pack** (internal: `Context Manifest`): verified task-local artifacts and summaries;
7. **Evidence Inputs**: exact registered evidence required for the task;
8. **Output Contract**: `report.md`, declared outputs, and artifact types;
9. **Forbidden Actions**: state, gate, ledger, evaluator, and scope mutations;
10. **Stop and Escalation Rules**: terminal status and when User input is required.

The complete chat history is forbidden. The full journal,
unlisted files, unrelated task reports, hidden memories, and raw artifacts not
named by ID are also forbidden. A long conversation must never become state.

## Task-local context pack

When using [Runtime Navigation](runtime-navigation.md), include only the
selected `runtime-context` artifact and its returned source/linked artifact
IDs. The worker reads the excerpt before opening relevant registered source
snapshots. Source paths in an excerpt are navigation, not additional read or
execution permission. Do not eagerly load all linked files or inject the full
runtime index. `task-open` rejects stale selections or missing dependency IDs.

The durable task record supplies task spec, budget reservation, skill names,
and Task Context Pack. `brief.md` renders this bounded
context pack for the worker.

The Task Context Pack contains only verified registered input artifacts and
their identity, type, path/reference, hash, size, and relevant provenance.
The Coordinator may add a concise ResearchState or cycle summary as a
registered input artifact; it must not inject an unversioned reconstruction of
prior conversation.

A worker-start hook may load:

- the task-local `brief.md`;
- the named primary skill;
- up to two named capability skills; and
- only artifacts listed in the Task Context Pack.

The hook cannot approve gates, widen context, edit task records, launch another
worker, or bypass ArtifactStore verification.

## Task identity

Task opening records the normalized task spec, reservation identity, skill
names, and Task Context Pack into `task.json` and `brief.md`. The report
frontmatter carries `task_id` and `candidate_id` as Coordinator-provided task
identities that must not change.

Changing a skill, context artifact, budget slice, task field, or output
contract creates a new task identity. A worker cannot reuse an old report
under a relabeled task.

For independent evaluation, the EvaluationSpec locks the evaluator contract.
Partial blinding hides the planner's preferred direction and subjective
confidence; the critic receives the hypothesis only after the independent
evaluation report exists.

## Markdown output contract

Every worker writes its complete reasoning product and new ideas to its
task-local `report.md`. Parallel workers use disjoint task directories and do
not mutate shared files.

The exact YAML frontmatter is:

```yaml
---
task_id: task-id
candidate_id: candidate-id
status: completed
artifact_ids: []
decision_recommendation: keep
---
```

`status` is `completed`, `blocked`, `needs_user`, `needs_review`, or `failed`.
`needs_review` pauses for a reviewer who may edit the input then approve a
sensitive/irreversible/external action; `needs_user` remains the last resort
for genuine user-only decisions.
`decision_recommendation` is advisory and cannot mutate ResearchState.

The body contains every heading:

```markdown
## Summary
## Evidence
## Ideas Considered
## Rejected Ideas
## Risks and Confounds
## Recommended Next Action
## Artifact Links
```

Before completion, the Coordinator or worker may append zero or more sections
with the exact heading `## Decision Addendum — YYYY-MM-DD` after `Artifact
Links`. Use them to retain later user decisions and cite their registered
evidence. The date must be valid; arbitrary headings and addenda inserted among
the required sections are rejected. A Decision Addendum does not approve a
gate, mutate ResearchState, or substitute for an ArtifactStore record.

The worker returns to the Coordinator only the report path, a bounded summary,
and terminal status. Before completion, it must cite the IDs of separately
registered declared outputs; completion snapshots only the report. Full output
remains in Markdown so the Coordinator can compact its context without losing
evidence.

## Model-call ledger

Each individual model call appends one compact JSON line to
`.research/autoresearch-record.jsonl` through the locked internal controller
operation. Required fields are:

- `id`, RFC-3339 `at`, `usage`, `cycle`, `task`, and nullable `candidate`;
- one primary plus zero to two capability `skills`;
- `model` and non-negative `duration_ms`;
- provider-reported `tokens` or null;
- provider-reported `cost_usd` or null;
- task-relative `report` and `status`.

Tokens contain `input`, `output`, `cached`, and `total`; total equals input plus
output. Provider tokens and cost are optional audit metadata; absent or
estimated values are accepted. The ledger does not contain full
prompts, full responses, chain-of-thought, or duplicated report content.
Existing rows are append-only, and no row is accepted after task completion
begins.

## Forbidden actions

A worker cannot:

- edit ResearchState, gates, budgets, candidate journal, artifact index, or
  model-call ledger directly;
- read unlisted context or write outside its Change Scope;
- change locked data, evaluator, metrics, thresholds, or sample policy;
- execute commands unless assigned to the execution worker and approved;
- repair a candidate while acting as Evaluator;
- invent an artifact ID, citation, metric, token count, or cost;
- suppress negative, failed, rejected, or inconclusive evidence; or
- continue after a stop condition or required human gate.

## Stop and escalation

The worker writes the best partial `report.md` before returning `blocked`,
`needs_user`, or `failed`. `needs_user` is the last resort, used only when a
genuine user-only decision is required: publication, first or expanded
approval, a sensitive or irreversible action, or conflicting evidence needing
human judgment. Recoverable conditions return `blocked` with a recovery
pointer (the Coordinator dispatches a debug subagent, requests a routine gate,
or detaches unknown usage) rather than escalating. A worker escalates to
`needs_user` only when scope, budget, dependency, model family, sensitive data,
locked evaluation, destructive action, external publication, or missing
authoritative evidence requires a user decision.

Retries use a new model-call row but the same task identity unless the task
contract itself changes. The Coordinator decides whether to retry, create a
debug candidate, change the approved contract, or stop.

## Design provenance

The explicit evidence cards, measurable hypotheses, real-metric requirements,
and result-grounding constraints are adapted from
[AutoResearchClaw prompts](https://github.com/aiming-lab/AutoResearchClaw/blob/main/prompts.default.yaml).
The journaled nodes, parallel branches, reports, and bounded debug depth are
adapted from
[AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2).
Neither project's monolithic pipeline or prompt authority is copied.

## Verification checklist

- Confirm all ten blocks exist in order.
- Verify task, skill, budget, context, and output identities.
- Load only the task-local context pack through the worker-start hook.
- Confirm one primary skill, at most two capabilities, and no nested subagent.
- Validate frontmatter and all Markdown sections before completion.
- Append one concise model-call row per invocation with reported usage only.
- Preserve full outputs and ideas in registered Markdown artifacts.
- Escalate before any scope, budget, evaluation, or publication boundary.
