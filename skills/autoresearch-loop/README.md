# Autoresearch Loop

> 中文文档：[README.zh.md](README.zh.md)

`autoresearch-loop` is a controllable ML research Coordinator. It takes an idea,
paper, or applied-ML objective through literature, baseline reproduction,
candidate branching, bounded execution, independent evaluation, iteration, and
citation-verified synthesis.

The durable project-local `.research/` directory is the authority for facts,
permissions, budgets, evidence, and recovery. Codex is the first Coordinator;
one long conversation is never system state.

## Use

Ask Codex to use `$autoresearch-loop` to start, resume, inspect, or govern a
research project. The Coordinator will inspect the repository, create or
validate `.research/`, and ask the User to approve:

- the **Change Scope** (internal: `Modification Envelope`): editable and read-only paths, commands,
  dependencies, models, network access, and external actions; and
- the **Resource Budget** (internal: `Budget Envelope`): wall/CPU/GPU time, storage, candidates, retries,
  debug work, and parallel subagents.

Provider token counts and dollar cost are not Resource Budget dimensions. When
the provider exposes them, the controller may retain them as optional audit
metadata; missing values never block `task-complete` and are never replaced by
invented zeros.

### Plain-language names

The controller keeps stable machine names in parentheses so existing projects
continue to load, while the workflow uses names that describe their purpose:

| User-facing name | Internal field or interface | Meaning |
|---|---|---|
| Change Scope | `Modification Envelope` | What files, commands, dependencies, models, and external actions are allowed |
| Resource Budget | `Budget Envelope` | Project-wide time, compute, storage, and candidate limits |
| Evaluation Contract | `EvaluationSpec` | Locked data, metrics, seeds, samples, regression rules, and evaluator |
| Research Plan | `research-charter` / Research Charter | Question, scope, success criteria, and constraints |
| Evidence Registry | `ArtifactStore` | Immutable artifacts, hashes, and provenance |
| Task Context Pack | `Context Manifest` | Bounded files and evidence a worker may read |
| Task Budget Allocation | `Budget Slice` | Finite share reserved for one task |
| Experiment Run Contract | `RunSpec` | Exact command, environment, inputs, and outputs for one run |
| Parallel Worker Limit | `loop-subagent-bound` | Maximum simultaneous subagent tasks |
| Candidate Under Review | `Provisional Best` | Candidate that passed gates but still needs replication or robustness evidence |
| Verified Best Candidate | `Current Best` | Candidate eligible as the cycle's comparison point |

Execution cannot start until both limits are recorded. Later scope, budget,
Evaluation Contract, model-family, sensitive-data, destructive-action, or
publication changes pause for another human gate.

Not every `gate_after` pauses the User. Each such stage declares
`gate_authority: coordinator | user`: a Coordinator gate verifies the locked
contract and stage artifacts, appends a receipt bound to stage/revision/graph/
artifact hashes, and continues. A user gate is reserved for a real boundary:
the first or expanded limits, locked Evaluation Contract or graph/scope changes,
dependencies/models/network, sensitive or irreversible actions, conflicting
evidence, or publication. Missing provider usage is not a completion or
accounting gate.

`needs_user` is the last resort, reserved for genuine user-only decisions
(publication, first or expanded approval, a sensitive or irreversible action,
or conflicting evidence needing human judgment). The Coordinator self-resolves
recoverable state: it dispatches a subagent to repair or debug a record,
requests a routine Coordinator-authority gate, detaches unknown usage, or
reasons and proceeds. The Coordinator only schedules, accepts, and records
gates; all execution goes to bounded workers, never a Coordinator-run
experiment or diagnostic. Self-verification is bounded to one preflight and
one postflight per stage; a fresh environment-readiness task supersedes a stale
`needs_user` one for the same target rather than blocking on it.

The default `research-charter` control-plane stage is a Coordinator gate and
continues directly to literature review after its Charter artifact verifies;
execution still requires both approved envelopes.

The User does not need to invoke `researchctl` manually. It is
Coordinator-internal transport for deterministic state changes.

## Installation from Skill Hub

`autoresearch-loop` and its eight worker directories are bundled with Skill Hub. Install the complete `skills/` collection, including `literature-review`. `$autoresearch-loop` is the user-facing
Coordinator; its eight sibling directories are worker skills it loads
internally.

### Codex

```bash
git clone https://github.com/EinheitCxy/skill-hub.git
cd skill-hub
mkdir -p ~/.codex/skills
cp -R skills/* ~/.codex/skills/
```

Restart Codex so it discovers the Coordinator and workers.

### Claude Code

Install and authenticate Claude Code first, then run:

```bash
git clone https://github.com/EinheitCxy/skill-hub.git
cd skill-hub
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/
```

Start a new Claude Code session so it discovers the Coordinator and workers.

Install real directories rather than symlinks so the Coordinator and worker
agents resolve skill and persona files consistently.

Then start from the ML project that will own the research state:

```text
Use $autoresearch-loop to turn this idea or paper into a controlled research
project. Inspect the repository first, propose the Change Scope and Resource
Budget, and wait for my approval before execution.
```

## Embedded Planner ScholarAPI

ScholarAPI is bundled inside the main Coordinator as
`autoresearch-loop/skills/planner/skills/scholarapi`; it is not a tenth
top-level `skills` capability and is not a user-local agent setup. The
Coordinator invokes the embedded Planner before every
`research-literature-review` task as a bounded ScholarAPI preflight. Planner
selects ScholarAPI only when the Change Scope permits network access,
`SCHOLARAPI_API_KEY` is present, and the plan declares finite query, page, and
result limits. Otherwise it records `not_selected` and the literature worker
uses only already-approved fallback sources; this is not a user gate.

The Planner registers a `planner-decision` artifact in every case. When it
selects ScholarAPI, it also registers an immutable `literature-metadata`
artifact before passing IDs to a worker Task Context Pack. This metadata is
untrusted candidate material; the literature worker still verifies acceptance,
publication status, DOI, and scientific claims from primary sources.

### Safely provide `SCHOLARAPI_API_KEY`

Use a session-only environment variable. In the shell that will start Codex or
Claude Code, enter the key without displaying it or saving it to shell history,
then start a **new** agent session from that same shell:

```bash
read -rs SCHOLARAPI_API_KEY
export SCHOLARAPI_API_KEY
codex
```

For Claude Code, replace the last line with `claude`. The child process
inherits the key; existing Codex/Claude Code sessions do not, so restart them
after setting it. When the parent shell no longer needs the key, remove it:

```bash
unset SCHOLARAPI_API_KEY
```

Never put the key in a command argument, `.env` file, shell startup file,
`research.yaml`, `.research/`, artifact, report, task brief, or Git commit.
The retained provenance contains query/filter/pagination metadata, retrieval
time, IDs, DOI/source URLs, and result count—never the key. If it is absent,
the Planner records `not_selected` and uses only other approved sources; it
does not make a network request or ask you to paste the key into chat.

### ScholarAPI request rules

The preflight uses the official ScholarAPI v1 read-only wrapper bundled at
`skills/planner/skills/scholarapi/scholarapi.py`. It follows these rules:

- `GET /search` is the default for relevance-ranked candidate discovery;
- `GET /list` is allowed only when the approved plan explicitly requests a
  finite index-order enumeration, exhaustive scan, or monitoring slice;
- every request declares at least one query and finite page/result limits;
  `--limit` is validated to `1..1000`;
- `/search` continues with `next_cursor` passed back as `--cursor`, while
  `/list` continues with `next_indexed_after` passed as `--indexed-after`;
- the wrapper sends the key only as the `X-API-Key` header, surfaces HTTP
  status and `X-Request-Id` when available, and never prints an unbounded error
  body or the key.

The wrapper is normally invoked by Planner rather than by a worker. For a
local smoke check, use the bundled script with a session-only key:

```bash
uv run --no-project python \
  skills/planner/skills/scholarapi/scholarapi.py search \
  --q "contrastive unpaired image translation" --limit 10
```

The command must run in a process that already has `SCHOLARAPI_API_KEY`; do
not place the key in the command line. The wrapper returns bounded JSON with
request metadata, retrieval time, records, source IDs/DOIs/URLs, availability
flags, and pagination state. Planner stores that unchanged response as a
`literature-metadata` artifact. The literature worker may use it only as a
candidate lead list and must reopen primary sources before creating evidence
cards or citation-backed claims. Validation, transport, HTTP, malformed JSON,
and limit errors stop ScholarAPI selection; they do not trigger unbounded
retries or silently widen the approved source policy.

## Architecture

The system retains six stable interfaces:

| Interface | Ownership |
|---|---|
| Research State Snapshot (`ResearchState`) | Revisioned control snapshot; Coordinator writes |
| Evidence Registry (`ArtifactStore`) | Immutable evidence, hashes, lineage, and availability |
| Task & Stage Workflow (`Task/Stage Protocol`) | Data-defined stages, bounded tasks, reports, and transitions |
| Experiment Runner (`EnvironmentExecutor`) | Approved dependency preparation and process execution |
| Independent Evaluator (`Evaluator`) | Independent locked evaluation and advisory verdict |
| Candidate Search Policy (`SearchPolicy`) | Candidate diversity, branching, Best rules, and stop decisions |

Stage IDs and transitions live in `research.yaml`, not a Python enum or runner.
Candidate Search Policy does not execute experiments. The Experiment Runner does not
launch LLMs. The Evaluator cannot repair candidates or mutate Best.
Task opening fails before budget reservation unless its stage is active, its
primary and capability skills are graph-declared, all required input artifact
types are verified, and the stage permits another parallel task.
Initialization, `validate`, and graph-revision request/application preflight
every declared primary and capability `SKILL.md`; a missing or conflicting
skill aborts before `.research/` is created or a later stage becomes
unreachable.

## Durable Project Layout

```text
.research/
├── research.yaml
├── state.json
├── budget.json
├── budget-migrations.jsonl
├── reservation-detachments.jsonl
├── gates.jsonl
├── journal.jsonl
├── worker-events.jsonl
├── autoresearch-record.jsonl
├── artifacts/
│   ├── index.jsonl
│   └── aliases.jsonl
├── cycles/
│   └── <cycle-id>/
│       ├── evaluation-comparison.json
│       └── tasks/
│           └── <task-id>/
│               ├── brief.md
│               ├── task.json
│               └── report.md
├── runs/
└── checkpoints/
```

State and transaction files are controller-managed. JSONL ledgers are
append-only. Registered snapshots and completed task reports are immutable;
corrections create a new artifact version.

## Coordinator Lifecycle

1. Validate the repository instructions and the current `.research/` state.
2. Record the Research Plan and both User-approved limits.
3. Search and screen literature; prioritize directly relevant accepted papers
   whose status is verified through official proceedings, publisher pages, or
   DOI records.
4. Optionally analyze a supplied paper, then audit dataset identity, splits,
   and leakage.
5. Register the baseline candidate, lock its evaluation and run plan, verify
   the environment, and reproduce the Initial Baseline.
6. Generate falsifiable exploit, explore, and falsify candidates.
7. Lock project-specific metrics, data, split, seeds, samples, regressions,
   evaluator hashes, and robustness in the Evaluation Contract.
8. Bind the cycle's concrete Verified Best Candidate in an immutable comparison
   checkpoint.
9. Implement candidates, evaluate registered evidence independently, critique
   confounds, and let the Coordinator select the next graph branch.
10. Debug, refine, ablate, replicate, pivot, promote, or stop.
11. Checkpoint every transition and archive a citation-verified claim-evidence
    report behind a Publication Gate.

The default lifecycle is data in the `ml-applied` graph. Projects may change
stages through an approved graph revision without changing Coordinator code.

### Safe graph revisions

Never edit an active `.research/research.yaml` in place. The Coordinator first
requests a `graph` gate bound to the current graph hash, proposed graph hash,
State revision, and reason; after the User records approval, it applies the graph
as one recoverable transaction. The transaction rejects open tasks and a
proposal that removes the active stage, writes a source checkpoint, advances
ResearchState by one revision, and records both graph hashes in one receipt.

If it is interrupted, the Coordinator resumes `graph-revise` with the gate ID;
the durable transaction already contains the proposed graph text, so it does
not depend on the original YAML file remaining available. Both `validate` and
`status` check this same graph/state/checkpoint anchor before reporting health.

## Worker Skills

Each graph stage declares `task_owner: coordinator|subagent`. A
Coordinator-owned task records `subagent_model: null`, never receives worker
events, and never consumes a `loop-subagent-bound` slot. A subagent-owned task
receives one task-local `brief.md`, one primary skill, at most two capability
skills, verified Task Context Pack artifacts, a finite Task Budget Allocation, disjoint
editable paths, and one `report.md`.

Workers are single-level leaf subagents and cannot spawn subagents or mutate
global state.

Only a Coordinator stage marked `control_plane: true` may open before
Modification and Budget execution approval. The default is `false`; every
other Coordinator or subagent task requires the approved execution envelope
and receives a Budget reservation. The bundled graph marks only `intake` and
`research-charter` as control-plane stages. A control-plane Coordinator task
never receives a Budget reservation, even after execution approval, and
completes with no provider-model usage (`cost_usd` and `tokens` are `null`,
and `duration_ms` is `0`).

For subagent-owned tasks, immutable `work_type` assigns the model directly:
`simple → luna`, `planning → sol`, `thinking → terra`, and `execution → terra`.
The values are tier aliases (`luna`=haiku, `sol`=sonnet, `terra`=opus); `thinking`
shares the opus tier with `execution`. This selection appears in task-local
`brief.md`.

### Sub-agent personas

Eight worker personas ship as thin shells in
`skills/autoresearch-loop/agents/` (see the table below for tier and role).
Each persona preloads its worker `SKILL.md` (the single source of truth),
inherits all sub-agent tools, and only forbids `Agent` so workers stay
single-level leaf agents.

The Skill Hub Claude Code plugin declares all eight personas in its manifest.
For a manual installation, copy them so Claude Code discovers them:

```bash
mkdir -p ~/.claude/agents
cp skills/autoresearch-loop/agents/research-*.md ~/.claude/agents/
```

The Coordinator proactively dispatches each persona via the Agent tool right
after `researchctl task-open` (`subagent_type=research-<role>`, `model` from the
work_type alias, `prompt` = the task `brief.md`). Under the Claude Code plugin,
use the host-exposed `skill-hub:research-<role>` persona identity. No repository
local Claude settings are assumed. Workers remain single-level leaves via
`disallowedTools: Agent`; persona edits affect subsequently opened tasks.

| Persona | Model | Role |
|---|---|---|
| `research-literature-review` | haiku | Search, screen, reject, and verify prior art |
| `research-paper-analysis` | sonnet | Map one source into claims, methods, limits, and reproduction work |
| `research-hypothesis-generation` | opus | Propose falsifiable exploit, explore, and falsify nodes |
| `research-experiment-design` | sonnet | Lock data, controls, metrics, gates, and budget |
| `research-experiment-execution` | opus | Implement and run one approved candidate |
| `research-experiment-evaluation` | sonnet | Evaluate registered evidence independently |
| `research-evidence-critique` | opus | Challenge mechanisms, leakage, baselines, and confounds |
| `research-result-synthesis` | opus | Build a citation-verified claim-evidence report |

Parallel fan-out is bounded by available independent tasks, graph limits,
hardware, and the approved Parallel Worker Limit. The graph and approved
Resource Budget are independent ceilings; effective fan-out is their lower
value. The `ml-applied` graph ceiling is 8; the initial Budget proposal is 4.

One open direct worker task reserves one `loop-subagent-bound` slot. Candidate
budget slices and local experiment runs never reserve that slot; they consume
only their own candidate and execution resources.

Before completion, declared stage outputs are separately registered and
verified ArtifactStore records. Register only inputs that affect conclusions,
run logs, outputs, and reports; do not register every plan, metadata stub, or
receipt as evidence. `report.md` cites those IDs; completion then
snapshots only the immutable `task-report`. The next stage consumes the real
ArtifactStore IDs such as `research-request` or `evaluation-reports`, never a
typed alias for the same Markdown report.

Artifact registration is idempotent for the same content hash and complete
provenance identity (including type, producer, task/candidate, prompt, tags,
and parent). A replay returns the existing canonical artifact ID; distinct
provenance still creates a new record. Historical index rows are never deleted.
Identical public control snapshots are likewise reused by checkpoint creation;
transition and graph-revision checkpoints remain separately bound to their
recovery reason.

Each generated task brief includes the exact report frontmatter contract. A
worker's initial report uses its bound `task_id` and `candidate_id`,
but leaves `artifact_ids: []`: input artifact IDs belong only to
the Task Spec and must never appear in report frontmatter. After the worker
returns, the Coordinator registers and verifies the declared outputs, then
invokes `researchctl task-report-finalize` with those IDs before
`task-complete`. This locked atomic finalization operation verifies task-bound
output IDs, covers declared outputs, semantically modifies only that
frontmatter field, and returns before/after report hashes; the Coordinator must
not alter the worker's report body during this handoff. Finalize before
requesting any accounting gate that
binds a report hash.

Completion rejects a report whose cited artifact types do not cover every
declared `stage.outputs` type.

The seven required report sections stay ordered and complete. Before task
completion, a report may append `## Decision Addendum — YYYY-MM-DD` after
`## Artifact Links` to record a later user decision or evidence correction.
The date must be valid, arbitrary headings remain rejected, and addenda must
cite registered evidence rather than acting as a gate or an artifact alias.

## Context and Model-Call Records

Optional [Runtime Navigation](references/runtime-navigation.md) supplies an
audited index of entrypoints, command templates and fixed artifact relationships.
`runtime-index-build` snapshots sources; `runtime-context` selects a bounded
excerpt and returns the exact artifact IDs to include before task-open. Default
limits are 5 entries/6000 serialized characters. Selected source freshness is
checked again at task-open; rebuilding an index never changes an open task.
It runs no commands, adds no model dependency, and makes no measured savings claim.

A worker-start hook may load only the task's `brief.md`, named skills, and
Task Context Pack artifacts. Complete chat history, the full journal, unrelated
reports, and unregistered raw files are forbidden prompt inputs.

Every model invocation appends one compact row to:

```text
.research/autoresearch-record.jsonl
```

The row records call ID, time, usage, cycle/task/candidate, skills, model,
duration, provider-reported tokens and cost (optional), report path, and
status. It never stores full prompts, full responses, or chain-of-thought.
Complete outputs and ideas remain in task-local Markdown and registered
artifacts.

Provider tokens and cost are optional audit metadata only. A task completes
without them; a reserved task settles its measured model-call duration against
its wall-time allocation. The controller never invents missing provider usage.

Worker lifecycle is intentionally separate from model usage. Before a worker
launches, the Coordinator appends `dispatched` to
`.research/worker-events.jsonl`; on return it appends `report-ready` or
`terminal`. On resume, `worker-status` performs reconciliation before its
projection and the Coordinator continues directly to completion/escalation for
`report-ready`/`terminal` workers instead of repeatedly waiting. Terminal
projections retain only their concise status and reason. The journal contains
no full report, token, cost, budget, or state copy.

## Candidate and Promotion Rules

The Candidate Graph retains candidate kind, parent, mechanism, primary change,
expected observation, falsification condition, Evaluation Contract, budget,
artifacts, scientific depth, and debug depth.

An approved evaluation gate is recorded as a split v1 envelope binding the
stage evidence (`stage_gate`) to the static Evaluation Contract payload hash
(`evaluation_spec`); stage evidence and the contract hash cannot drift
independently. Legacy direct evaluation gates remain readable for recovery
only.

With three slots, Candidate Search Policy selects:

1. exploit the Verified Best Candidate;
2. explore a materially different mechanism; and
3. falsify the strongest claim or execute its decisive ablation.

Initial Baseline is the immutable starting point. Each cycle checkpoints the
concrete Verified Best Candidate. New candidates compare against that checkpoint, not
the Candidate Under Review.

An official candidate reaches the Candidate Under Review state only after ordered hard gates,
the project's minimum-gain and unacceptable-regression rules, and exact-cycle
Pareto membership pass. The Verified Best Candidate receives that exact report/checkpoint
pointer only after required replication, multi-seed, or robustness evidence.

No generic metric or threshold is hard-coded in this skill.

## Proposed Default Budget

Initialization proposes but does not approve:

```yaml
wall_seconds: 86400
cpu_seconds: 86400
gpu_seconds: 86400
storage_bytes: 2147483648
official_candidates: 3
retries: 3
debug_candidates: 3
loop-subagent-bound: 4
gpu_type: null
```

Concrete task/run reservations and timeouts remain finite. Model weights are
excluded from charged storage; ordinary run snapshots are hash-verified and
charged once.

## Coordinator-Internal Commands

These commands document the deterministic transport used by Codex. They are
not the user-facing workflow.

```bash
uv run skills/autoresearch-loop/scripts/researchctl.py init --root /path/to/project --profile skills/autoresearch-loop/assets/research-project/research.yaml
uv run skills/autoresearch-loop/scripts/researchctl.py validate --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py status --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py gate-reject --root /path/to/project --gate-id gate-<id> --source User --reason "reason"
uv run skills/autoresearch-loop/scripts/researchctl.py gate-cancel --root /path/to/project --gate-id gate-<id> --source User --reason "reason"
uv run skills/autoresearch-loop/scripts/researchctl.py graph-revision-request --root /path/to/project --graph /path/to/proposed-research.yaml --reason "explain the approved change"
uv run skills/autoresearch-loop/scripts/researchctl.py graph-revise --root /path/to/project --graph /path/to/proposed-research.yaml --gate-id gate-<approved-id>
uv run skills/autoresearch-loop/scripts/researchctl.py worker-reconcile --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py worker-status --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py reservation-detach --root /path/to/project --reservation-id <id> --reason "provider usage unknown"
uv run skills/autoresearch-loop/scripts/researchctl.py intent-inspect --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py intent-retry --root /path/to/project --intent-id <id>
uv run skills/autoresearch-loop/scripts/researchctl.py intent-quarantine --root /path/to/project --intent-id <id> --reason "operator triage"
uv run skills/autoresearch-loop/scripts/researchctl.py task-report-finalize --root /path/to/project --task-id <task-id> --artifact-id artifact-<registered-output-id>
uv run skills/autoresearch-loop/scripts/researchctl.py artifact-alias-bind --root /path/to/project --type <logical-output-type> --artifact-id artifact-<id>
uv run skills/autoresearch-loop/scripts/researchctl.py artifact-alias-resolve --root /path/to/project --type <logical-output-type>
```

The script uses PEP 723 and `pyyaml>=6,<7` as its only third-party runtime
dependency. The controller also exposes internal gate, budget, task, artifact,
candidate, evaluation, promotion, environment, run, worker-event, and recovery
operations. Codex invokes these commands; they are not a required user workflow.
After an interrupted `graph-revise`, the same command may omit `--graph`: the
durable graph-revision intent is then the recovery authority.

`validate` is also the explicit legacy budget migration boundary. A recognized
v0 or early-v1 `cost_usd`/`model_tokens` ledger is migrated once to wall/resource-only
v1, with original values retained in `budget-migrations.jsonl`; the migration is
idempotent and never re-triggers per new task, and unknown versions fail closed.
Provider usage that cannot be measured locally is optional audit metadata; a
reservation may be detached via `reservation-detach` when measured settlement is
not available, but settlement with absent or estimated usage is accepted. A
reservation with a pending or committed budget settlement cannot be detached
until that settlement is quarantined or settled. GPU requests require host-backed NVIDIA device, driver, and
telemetry readiness; sandbox execution reports `sandbox-no-device`.

`reservation-detach` and the `intent-*` recovery commands bypass the full
preflight (they exist to repair state a preflight would reject); lightweight
append commands (`record-call`, `worker-event`, `artifact-alias-bind`) also
skip it so routine appends do not block on unrelated pending recovery.

`--root` may name either the project root or its terminal `.research` directory.
Logical output aliases live in `artifacts/aliases.jsonl`; they point to existing
artifact IDs and never copy artifact content.

## Testing

Testing follows `references/testing-strategy.md`:

- **Task focused:** run only the owned contract node after a change;
- **phase checkpoint:** run the small integration spine once; and
- **release full:** run the complete suite only before final acceptance or
  release.

Focused Coordinator check:

```bash
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/autoresearch-uv-cache UV_PYTHON=3.11 uv run --offline --with pytest --with "pyyaml>=6,<7" pytest skills/autoresearch-loop/tests/test_coordinator_skill.py -q
```

Phase 3 integration spine:

```bash
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/autoresearch-uv-cache UV_PYTHON=3.11 uv run --offline --with pytest --with "pyyaml>=6,<7" pytest skills/autoresearch-loop/tests/test_coordinator_skill.py skills/autoresearch-loop/tests/test_skill_suite.py skills/autoresearch-loop/tests/test_module_architecture.py -q
```

Release validation executed on 2026-08-06:

```bash
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/autoresearch-uv-cache UV_PYTHON=3.11 uv run --with pytest --with "pyyaml>=6,<7" pytest -p no:cacheprovider skills/autoresearch-loop/tests -q
```

Result: `541 passed in 163.69s`.

## Direct References

`SKILL.md` links one level deep to:

- `references/research-state.md`
- `references/artifact-store.md`
- `references/task-stage-protocol.md`
- `references/environment-executor.md`
- `references/evaluator.md`
- `references/search-policy.md`
- `references/research-standards.md`
- `references/prompt-contracts.md`
- `references/testing-strategy.md`

## Current Limitations

- Execution supports local executors only: `uv-local` and `command-local`.
- Local adapters record network policy but do not claim OS-level isolation.
- Gate receipts record approve, reject, and cancel decisions with source and
  reason, and sensitive/irreversible actions may carry a reviewer-edited
  `review_input`, but provide no cryptographic user identity.
- A bounded Reflexion self-repair loop (`.research/reflections.jsonl`) runs one
  reflection step before escalating recoverable worker/evaluator failures;
  workers are leaf agents and never run reflection.
- The controller does not start LLMs; Codex performs Coordinator/subagent
  orchestration through task-local context packs.
- The idea-to-promotion fixture proves controller behavior with synthetic
  evaluation and replication artifacts; it does not establish scientific
  quality or real-model performance.
- Publication and external side effects always require the User's explicit gate.

## Design Provenance

The design selectively adapts artifact/checkpoint/HITL/citation mechanisms from
[AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw) and
candidate-node/journal/branch/debug/parallel-search mechanisms from
[AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2). The bounded
Reflexion self-repair loop and the `sensitive_action`/`needs_review`
reviewer-editable HITL are adapted from AutoGPT's recoverable-failure and
per-action review patterns.
Neither project is adopted as a fixed or monolithic research framework.

## Bundled Capability Pool

The default profile uses the nine primary skills and bundled `literature-review`.
Generic optional helpers from the former installation are not prerequisites.
Primary workers retain their method, evidence, statistical and citation contracts.
Add a capability only when its skill is actually installed and the project graph
change is approved; no missing helper is silently substituted.
