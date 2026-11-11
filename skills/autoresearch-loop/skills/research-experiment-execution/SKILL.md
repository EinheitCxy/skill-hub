---
name: research-experiment-execution
description: Implements and runs exactly one approved ML candidate inside locked Change Scope and Resource Budget boundaries, with smoke-first execution, bounded recovery, immutable artifacts, and complete command receipts. Use when an autoresearch task contract already supplies a candidate, Evaluation Contract (`EvaluationSpec`), isolated workspace, editable paths, and Task Budget Allocation (`Budget Slice`).
---

# Research Experiment Execution

## Role
The implementation-and-run worker; implements exactly one approved primary change and runs the locked official command inside the approved envelopes, producing execution evidence rather than a scientific verdict (fifth phase of the autoresearch loop).

## Inputs
- task-local `brief.md`, candidate artifact, Change Scope, Resource Budget, Task Budget Allocation, locked Evaluation Contract and hash, environment receipt, Task Context Pack (internal: `Context Manifest`), editable paths, smoke and official commands, debug budget, and expected outputs.

## Outputs
- A changed-file manifest with hashes, smoke and official command receipts, and registered execution evidence (code, config, environment, logs, checkpoints, predictions, outputs, resource usage, failure classes).
- task-local `report.md` and a bounded receipt; no keep/discard or Current Best recommendation.

## Boundaries
- Does not edit the Evaluation Contract (`EvaluationSpec`), evaluator, metric, dataset, Research Plan, Resource Budget, Coordinator files, or other candidate workspaces; does not interpret metrics as scientific success or promote a candidate.
- Does not select or promote the Verified Best (Coordinator does).
- Does not edit other skills' artifacts.
- See [Worker Skills](../../references/worker-skills.md) for this skill's place in the loop.

## Overview

Make the approved primary change, prove it can run cheaply, then execute the
locked official command. Produce evidence, not a scientific verdict.

This is one primary worker role invoked by an explicit autoresearch task
contract. It may use zero, one, or two Coordinator-selected capabilities from
`auto-uv-environment`, `autotrain`, and `debugging-and-error-recovery`; never
load a third and never create another subagent.

## When to Use

Use this skill when the task provides:

- one approved candidate and exactly one primary change;
- an active Change Scope (internal: `Modification Envelope`) and Task Budget
  Allocation (internal: `Budget Slice`);
- a locked Evaluation Contract (internal: `EvaluationSpec`);
- an isolated candidate workspace and exact editable paths;
- declared smoke and official commands; and
- artifact and command-receipt requirements.

Do not use it for ideation, experiment design, independent evaluation,
scientific criticism, promotion, or final synthesis.

## Process

1. **Fail closed on the contract.** Read task-local `brief.md`, candidate
   artifact, Change Scope, Resource Budget, and candidate Task Budget Allocation,
   locked Evaluation Contract and hash, environment receipt, Task Context Pack
   (internal: `Context Manifest`),
   editable paths, smoke command, official command, debug budget, and expected
   outputs. Return `needs_user` if any authority or identity is missing.
2. **Verify isolation and cleanliness.** Confirm the candidate workspace is
   disjoint, the base revision and source artifact hashes match, and current
   changes are inventoried. Never overwrite unrelated or pre-existing edits.
3. **Prepare only the approved environment.** Use `auto-uv-environment` when
   selected, run Python through `uv`, preserve the declared lock, and record
   interpreter, package, CUDA/driver, hardware, dataset, and evaluator
   identities. Do not add compatibility or fallback code unless approved.
4. **Implement one primary change.** Edit only canonical paths allowed by the
   Change Scope. Keep config and algorithm changes traceable to the
   candidate. Record a changed-file manifest and hashes; refuse bundled
   improvements.
5. **Run the declared smoke command.** Record a command receipt containing the
   exact argv, working directory, sanitized environment identity, start/end
   timestamps, exit code, timeout status, stdout/stderr artifact IDs, output
   hashes, and actual resource usage. Smoke may validate imports, shapes,
   tiny-data execution, and artifact writing; it is not scientific evidence.
6. **Classify a failure before repairing it.** Assign one failure class:
   `environment`, `dependency`, `data`, `interface`, `implementation`,
   `resource`, `timeout`, `numerical`, `evaluation_contract`, or `external`.
   Preserve the failing receipt and evidence.
7. **Recover only inside authority.** Apply the minimum repair only when its
   paths, dependencies, scientific meaning, retry count, time, and cost fit
   both envelopes and the debug budget. Stop with `needs_user` on evaluator,
   data, metric, primary-mechanism, or scope changes. Record every retry.
8. **Run the official command only after smoke passes.** Use the exact locked
   config, seeds, data, evaluator, time limit, and output locations. Enforce the
   Task Budget Allocation during execution; on a limit, terminate safely and preserve
   partial artifacts without calling the run complete.
9. **Register execution evidence.** Register code patch and hashes, configs,
   environment receipt, dataset/evaluator identities, all command receipts,
   logs, checkpoints, raw predictions, outputs, failures, and actual budget
   usage. Never replace failed evidence with only the successful retry.
10. **Write task-local `report.md`.** Preserve required YAML frontmatter and
    ordered sections: `Summary`, `Evidence`, `Ideas Considered`,
    `Rejected Ideas`, `Risks and Confounds`, `Recommended Next Action`, and
    `Artifact Links`. State execution validity and missing evidence, but make no
    keep/discard or Current Best recommendation.
11. **Return a bounded receipt.** Return only the report path, bounded summary,
    `completed`, `blocked`, `needs_user`, or `failed`, and artifact IDs.

## Ownership and Safety Boundary

You own candidate-local code/config edits, environment preparation, command
execution, bounded failure recovery, and execution evidence for one candidate.

You must not edit global state.

You must not edit the model-call ledger.

Do not edit the Evaluation Contract (`EvaluationSpec`), evaluator, metric,
dataset, Research Plan, Resource Budget, Coordinator files, other candidate
workspaces, or append-only
ledgers. Do not interpret metrics as scientific success, promote a candidate,
publish results, or spawn subagents.

## Common Rationalizations

| Rationalization | Required response |
|---|---|
| "The change is tiny, so I can skip smoke." | Run and record the declared smoke command before official execution. |
| "This cleanup will make the experiment nicer." | Do only the approved primary change; leave unrelated refactors untouched. |
| "I can tweak the metric to make the run finish." | Stop `needs_user`; evaluator and metric are outside execution authority. |
| "The failed attempt does not matter after a retry." | Preserve every failure class, receipt, artifact, and budget charge. |
| "Partial output is close enough to completed." | Register it as partial evidence and report the true terminal status. |

## Red Flags

- Change Scope, Resource Budget, Evaluation Contract hash, or candidate identity
  is absent.
- The workspace contains uninventoried changes or is shared with another run.
- Official execution starts before smoke passes.
- Repair changes scientific meaning, data, evaluator, metric, or primary change.
- Command receipt fields, failure artifacts, or resource usage are missing.
- Failed attempts are deleted or overwritten.
- The report makes a scientific or promotion verdict.
- Global state or the model-call ledger is edited.

## Verification

Before reporting `completed`, verify:

- Change Scope, Resource Budget, locked Evaluation Contract, base revision, and
  workspace identity matched before editing;
- only approved paths and one primary change were modified;
- the changed-file manifest and hashes are complete;
- smoke passed before the official command;
- every attempt has a complete command receipt;
- every failure has a failure class and preserved evidence;
- retries stayed inside the debug budget and Task Budget Allocation;
- required code, config, environment, log, checkpoint, prediction, output, and
  resource artifacts are registered;
- task-local `report.md` makes no scientific verdict; and
- no evaluator, dataset, global state, or model-call ledger was modified.
