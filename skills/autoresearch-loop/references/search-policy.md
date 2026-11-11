# Candidate Search Policy Contract (internal: SearchPolicy)

## Purpose and owner

The Candidate Search Policy (`SearchPolicy`) chooses which ML candidate nodes
to investigate next. It reads
the Candidate Graph, structured evaluation reports, budget state, and
the Research State Snapshot (`ResearchState`); it does not execute commands,
edit code, judge raw outputs, or
mutate Best directly.

The Candidate Graph is deliberately ML-specific. Stage order remains data in
`research.yaml`, while candidate kinds and lineage rules are stable search
semantics.

## Candidate node

Every node records:

- `candidate_id`, `kind`, `status`, and `parent_id`;
- hypothesis, mechanism, one `primary_change`, and data intervention;
- objective, expected observation, and falsification condition;
- search role and active Evaluation Contract (`EvaluationSpec`) ID;
- a declared Task Budget Allocation (`Budget Slice`);
- append-only code, configuration, run, and evaluation artifact IDs; and
- separate `scientific_depth` and `debug_depth`.

Supported kinds are baseline, draft, architecture/model, objective/loss,
data/augmentation, optimization/hyperparameters, inference/post-processing,
efficiency/systems, refine, debug, ablate, replicate, and pivot.

A debug node repairs execution without asserting a new scientific claim.
Debug depth increases from its parent and is bounded by graph
`max_debug_depth`; `debug_candidates` and `retries` remain separate approved
budget quotas.

## Three-slot parallel policy

With three available slots, selection is deterministic:

1. `exploit`: improve Current Best using the strongest supported direction;
2. `explore`: test a materially different mechanism; and
3. `falsify`: choose the strongest node from the falsification and ablation
   union.

Slots below three truncate this order. Selection excludes terminal or blocked
nodes and rejects near duplicates by hypothesis, kind, primary-change hash,
data intervention, and objective. The Coordinator may additionally consider
mechanism, expected effect, cost, and risk when proposals are formed.

Parallel workers receive separate task-local context and editable paths. Their
reports and ideas are written to Markdown and registered before the next
selection; conversation history is not search state.

## Verdicts, branching, and debug obligations

An official node may receive `keep`, `discard`, `inconclusive`, `debuggable`,
`needs_user`, or `failed`. Reports and verdicts are append-only evidence.

`debuggable` closes the failed scientific attempt and creates a pending
obligation for a correctly parented debug child at the next debug depth.
Ordinary descendants of that branch remain blocked until the obligation is
satisfied. A debug child cannot receive `keep` as a scientific result. The
debug child is re-evaluated through the ordered hard gates; this loop is
bounded by the approved `retries`/`debug_candidates` quota, stays
self-recoverable while quota remains, and becomes `needs_user` or `failed`
only on exhaustion.
Before escalating a recoverable failure (`blocked`/`inconclusive`/`debuggable`)
to a debug child or `needs_user`, the Coordinator runs ONE bounded Reflexion
step (`.research/reflections.jsonl`; cap 20 stored per candidate, inject <=5,
`max_retry_attempts` from the `retries` quota) that re-dispatches with the
reflected alternative approach in the Task Context Pack; only on
`should_retry=False` or quota exhaustion does the debug child or escalation
proceed.

Refine, ablate, replicate, and pivot are explicit child nodes rather than
in-place edits, preserving the experiment journal and candidate lineage.

## Best selection

Initial Baseline is the immutable starting comparison. New official candidates
compare against the Current Best captured by their evaluation-cycle
checkpoint, never against an in-flight Provisional Best.

An eligible official candidate must pass hard gates, the locked minimum gain
and unacceptable regression rules, and exact-cycle Pareto membership before
the Coordinator may set Provisional Best. Provisional Best binds candidate,
evaluation-report artifact, cycle checkpoint, and promotion timestamp.

Current Best advances only by copying that exact Provisional Best after all
locked replication, multi-seed, or robustness artifacts pass. SearchPolicy may
recommend a promotion; it cannot write either pointer.

## Stop conditions

`should_stop` returns all applicable reasons in deterministic order:

- success criterion reached;
- pending user gate or requested boundary change;
- total cost or wall-time budget exhausted;
- official candidate budget exhausted;
- convergence without material gain;
- no valid independent candidate; or
- user stop requested.

Nullable aggregate cost/token limits are not exhaustion. Every concrete task
and run still declares a finite budget slice and timeout. The policy pauses
rather than widening scope, changing EvaluationSpec, or increasing budget.

## Invalid examples

```json
{"kind": "debug", "debug_depth": 0, "parent_id": "cand-4"}
```

Invalid: a debug child has positive depth and must be exactly one deeper than
its parent.

```json
{"search_role": "explore", "parent_id": null}
```

Invalid for a non-baseline node: candidate lineage is missing.

Also invalid: selecting three cosmetic variants, overwriting candidate
evidence, treating a retry as a scientific node, promoting a debug repair,
mixing evaluation checkpoints, comparing against Provisional Best, or
continuing after a mandatory stop.

## Verification checklist

- Validate every candidate field, parent, active EvaluationSpec, and budget.
- Keep scientific depth, debug depth, retries, and candidate quotas distinct.
- Select exploit, explore, and falsify/ablate deterministically and diversely.
- Preserve every branch, verdict, failure, and rejected idea in the journal.
- Resolve pending debug obligations before ordinary continuation.
- Bind comparison and Pareto membership to one evaluation-cycle checkpoint.
- Keep Provisional Best and Current Best mutation in the Coordinator.
- Stop on success, gates, budget, convergence, no candidate, or user request.
