---
name: research-experiment-design
description: Designs comparable, budgeted ML experiments and produces a lock-ready Evaluation Contract (`EvaluationSpec`) before candidate results are observed. Use when an autoresearch task contract requires dataset-split and leakage checks, baseline and metric definitions, uncertainty rules, hard gates, smoke and official commands, or resource and stop conditions.
---

# Research Experiment Design

## Role
The experiment-design worker; freezes how a candidate will be judged and produces a lock-ready Evaluation Contract before candidate results are observed (fourth phase of the autoresearch loop).

## Inputs
- task-local `brief.md`, Research Plan (internal: `research-charter`), registered data artifacts, Change Scope (internal: `Modification Envelope`), Resource Budget (internal: `Budget Envelope`), and allowed infrastructure; stage-specific baseline or candidate artifacts.

## Outputs
- A lock-ready Evaluation Contract (`EvaluationSpec`) with dataset-split and leakage checks, locked metrics and hard gates, smoke and official commands, artifact requirements, and a bounded Task Budget Allocation.
- task-local `report.md` and a bounded receipt.

## Boundaries
- Does not edit candidate code, preprocess data, run smoke or official commands, change the budget, approve the evaluation gate, inspect hidden official results, evaluate a run, or promote Current Best.
- Does not select or promote the Verified Best (Coordinator does).
- Does not edit other skills' artifacts.
- See [Worker Skills](../../references/worker-skills.md) for this skill's place in the loop.

## Overview

Freeze how a candidate will be judged before seeing its official result. The
output is a lock-ready Evaluation Contract (`EvaluationSpec`) and run plan, not
an experiment result.

This is one primary worker role invoked by an explicit autoresearch task
contract. It may use zero, one, or two Coordinator-selected capabilities from
`statistical-analysis` and `autotrain`; it never creates another subagent.

## When to Use

Use this skill to:

- audit dataset split integrity and data leakage;
- design a baseline reproduction contract before the baseline run;
- define Initial Baseline and Current Best comparisons;
- lock primary and secondary metrics, uncertainty, and noise thresholds;
- separate smoke validation from official evaluation;
- define hard gates, robustness tests, artifacts, and stop conditions; or
- estimate a bounded candidate Task Budget Allocation (`Budget Slice`).

Do not use it to invent hypotheses, implement a candidate, run commands,
inspect candidate results before locking, or decide promotion.

## Process

1. **Validate the stage-specific design inputs.** Read task-local `brief.md`,
   the Research Plan (internal: `research-charter`), registered data artifacts,
   Change Scope (internal: `Modification Envelope`), Resource Budget (internal:
   `Budget Envelope`), and allowed infrastructure. A dataset-audit task does not
   require a candidate or baseline. A baseline-contract task requires the
   registered baseline candidate and reproduction target. A candidate-contract
   task additionally requires the candidate proposal, Initial Baseline, and
   mutation-before Current Best. Stop with `needs_user` when the task mode,
   scientific question, or comparison target is ambiguous.
2. **Audit the data contract.** Record dataset identity and version, train,
   validation, and test dataset split rules, sample counts, group or temporal
   boundaries, preprocessing ownership, label provenance, exclusion rules, and
   contamination checks. Identify data leakage paths through duplicates,
   subjects, time, augmentations, normalization, feature engineering, tuning,
   or pretrained data.
3. **Lock comparison targets.** Name the Initial Baseline, mutation-before
   Current Best, any external baselines, their artifact hashes, environments,
   configs, and fairness requirements. Reject a weak baseline or unequal
   compute/data comparison.
4. **Lock metrics before results.** Define one primary metric, direction,
   calculation, aggregation, and unit; secondary metrics; qualitative slices;
   resource measures; uncertainty method; seed count; sample count; noise
   threshold; minimum meaningful gain; unacceptable regressions; and missing
   value behavior.
5. **Define ordered hard gates.** Check execution validity first, contract
   comparability second, and evidence completeness third. Specify exactly what
   produces `failed`, `inconclusive`, `needs_user`, or a metric-bearing
   evaluation.
6. **Separate commands.** Define a cheap smoke command that checks load,
   shape/interface, tiny-data execution, and artifact writing. Define the
   official command separately with exact config, seed policy, data, evaluator,
   time limit, and output locations. A smoke result is never official evidence.
7. **Budget the candidate.** Estimate wall time, training time, model calls,
   monetary cost, GPU type/count/memory, CPU/RAM, storage, and retry/debug
   allowance. Check the candidate Task Budget Allocation against the approved
   remaining Resource Budget and parallelism cap.
8. **Specify evidence artifacts.** Require immutable code/config hashes,
   environment receipt, dataset/evaluator hashes, command receipts, logs,
   checkpoints, raw predictions, metric inputs/outputs, runtime/cost records,
   and qualitative samples. State retention requirements.
9. **Define robustness and stopping.** Lock required seeds, datasets, stress
   tests, ablations, slices, resource limits, early termination, timeout,
   divergence, and evidence-insufficiency conditions.
10. **Produce the lock-ready Evaluation Contract (`EvaluationSpec`).** Include all decisions in a
    canonical machine-readable artifact and summarize them in task-local
    `report.md`. Do this before any official candidate result is observed. If
    result leakage already occurred, declare the contract contaminated and
    return `needs_user`.
11. **Return a bounded receipt.** Return only the report path, bounded summary,
    `completed`, `blocked`, `needs_user`, or `failed`, and artifact IDs.

## Ownership and Safety Boundary

You own experimental comparability, data-integrity checks, evaluation-rule
design, and resource estimation for this task only.

You must not edit global state.

You must not edit the model-call ledger.

Do not edit candidate code, preprocess data, run smoke or official commands,
change the budget, approve the evaluation gate, inspect hidden official
results, evaluate a run, or promote Current Best.

## Common Rationalizations

| Rationalization | Required response |
|---|---|
| "We can choose the metric after seeing what improves." | Lock the primary metric and direction before official results. |
| "The standard split is probably clean." | Verify identities, groups, time, duplicates, preprocessing, and tuning access. |
| "One run is enough for this quick idea." | Set seed/sample requirements from noise and decision risk. |
| "Smoke accuracy looks promising." | Smoke validates execution only; exclude it from scientific evidence. |
| "The GPU estimate can be adjusted later." | Fit the Task Budget Allocation now or return `needs_user`. |

## Red Flags

- Candidate results are visible before Evaluation Contract locking.
- The dataset split lacks identity, version, or leakage controls.
- Primary metric, direction, noise threshold, or comparison target is missing.
- Baseline and candidate use unequal data, compute, preprocessing, or evaluator.
- Smoke and official commands are identical or their evidence is mixed.
- Budget, artifact, robustness, or stop requirements are prose-only.
- The worker runs code or edits global state or the ledger.

## Verification

Before reporting `completed`, verify:

- dataset identity, dataset split, and data leakage checks are explicit;
- the stage-appropriate baseline, candidate, and comparison targets are
  hash-addressed;
- primary metric, direction, uncertainty, noise threshold, seeds, and minimum
  meaningful gain are locked;
- unacceptable regressions and ordered hard gates are machine-checkable;
- smoke and official commands and outputs are distinct;
- the Task Budget Allocation fits the approved Resource Budget;
- required artifacts, robustness checks, and stop conditions are complete;
- no official candidate result influenced the Evaluation Contract;
- task-local `report.md` links the lock-ready artifact; and
- no experiment, global state, or model-call ledger was modified.
