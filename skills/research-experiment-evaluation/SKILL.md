---
name: research-experiment-evaluation
description: Independently evaluates registered ML run artifacts against a locked Evaluation Contract (`EvaluationSpec`) using partial blinding, ordered hard gates, uncertainty and slice analysis, and a non-mutating verdict recommendation. Use when an autoresearch task contract provides completed run receipts and asks for evidence-based scoring without repairing the candidate.
---

# Research Experiment Evaluation

## Role
The Independent Evaluator (advisory); judges registered run artifacts against the locked Evaluation Contract under partial blinding and ordered hard gates, and reports a non-binding verdict recommendation (sixth phase of the autoresearch loop).

## Inputs
- task-local `brief.md`, locked Evaluation Contract artifact and hash, approved gate receipt, comparison target, registered run receipt and artifact hashes, and Task Budget Allocation.

## Outputs
- An EvaluationReport with ordered hard-gate decisions, metrics, uncertainty, slices, confounds, resources, and exactly one non-binding verdict recommendation.
- task-local `report.md` and a bounded receipt.

## Boundaries
- Cannot edit candidates, code, data, metrics, the evaluator, run outputs, the Evaluation Contract (`EvaluationSpec`), comparison target, Resource Budget, or execution receipts.
- Does not select or promote the Verified Best (Coordinator does).
- Does not edit other skills' artifacts.
- See [Worker Skills](../autoresearch-loop/references/worker-skills.md) for this skill's place in the loop.

## Overview

Judge the evidence under the rules fixed before the run. Preserve independence
from candidate generation and execution, and report a recommendation without
changing research state.

This is one primary worker role invoked by an explicit autoresearch task
contract. It may use zero, one, or two Coordinator-selected capabilities from
`statistical-analysis`, `scientific-critical-thinking`, and
`scientific-visualization`; never load a third and never create another
subagent.

## When to Use

Use this skill to:

- validate a completed run and its registered artifacts;
- apply a locked Evaluation Contract (`EvaluationSpec`) and evaluator hash;
- compute or verify metrics, uncertainty, slices, regressions, and cost;
- check robustness and confounds under partial blinding; or
- issue a keep/discard/inconclusive-style verdict recommendation.

Do not use it to design the contract, implement or repair the candidate, change
data or metrics, promote Current Best, or write the final narrative.

## Process

1. **Verify evaluation authority and identity.** Read task-local `brief.md`,
   locked Evaluation Contract artifact and hash, approved gate receipt, comparison
   target, registered run receipt, code/config/environment/data/evaluator
   hashes, expected artifacts, and Task Budget Allocation (`Budget Slice`). Stop
   if identities disagree.
2. **Establish partial blinding.** Do not read the hypothesis generator's
   expected observation, candidate rationale, subjective execution assessment,
   desired verdict, or unregistered narrative. Use only the candidate ID,
   mutation-before comparison target, locked rules, and evidence required to
   evaluate validity and outcomes.
3. **Apply hard gates in order.**
   - `execution_validity`: official command, exit/timeout, required completion,
     artifact integrity, and budget compliance;
   - `contract_comparability`: dataset, split, preprocessing, seeds, code base,
     evaluator hash, metrics, and comparison target;
   - `evidence_completeness`: raw inputs/outputs, required samples, logs,
     uncertainty inputs, robustness evidence, and receipts.
   Stop metric interpretation at the first failed gate and use the locked
   terminal status.
4. **Compute or independently verify outcomes.** Using the locked evaluator,
   calculate the primary metric, secondary metrics, uncertainty, seed
   dispersion, sample counts, qualitative slices, unacceptable regressions,
   runtime, cost, memory, and robustness. Preserve raw-to-derived artifact
   lineage and numerical units.
5. **Compare without moving the goalposts.** Compare only with the locked
   mutation-before Current Best or approved baseline under equivalent
   conditions. Apply minimum meaningful gain and noise thresholds exactly;
   do not choose favorable subsets or post-hoc metrics.
6. **Identify confounds and missing evidence.** Record data leakage indicators,
   unfair compute/data differences, seed sensitivity, slice regressions,
   evaluator or implementation mismatch, cherry-picking, censored failures,
   and evidence that remains unavailable. Distinguish observations from causal
   interpretation.
7. **Produce an EvaluationReport.** Record all hard-gate decisions, metrics and
   uncertainty, comparisons, resources, robustness, confounds, artifact IDs,
   and one verdict recommendation:
   `keep`, `discard`, `inconclusive`, `debuggable`, `needs_user`, or `failed`.
   A recommendation does not authorize state mutation or promotion.
8. **Write task-local `report.md`.** Preserve required YAML frontmatter and the
   ordered sections: `Summary`, `Evidence`, `Ideas Considered`,
   `Rejected Ideas`, `Risks and Confounds`, `Recommended Next Action`, and
   `Artifact Links`. Link the immutable EvaluationReport.
9. **Return a bounded receipt.** Return only the report path, bounded summary,
   `completed`, `blocked`, `needs_user`, or `failed`, and artifact IDs.

## Ownership and Safety Boundary

You own independent validity checks, locked metric evaluation, uncertainty and
slice analysis, confound identification, and the verdict recommendation.

You must not edit global state.

You must not edit the model-call ledger.

You cannot modify the candidate, its code or config, the evaluator, metrics,
dataset, split, run outputs, Evaluation Contract (`EvaluationSpec`), comparison
target, Resource Budget, or
execution receipts. Do not repair failures, rerun the candidate, promote a Best
pointer, publish results, or create subagents.

## Common Rationalizations

| Rationalization | Required response |
|---|---|
| "I need the expected result to interpret this." | Preserve partial blinding; evaluate against the locked contract. |
| "The metric is good, so the missing artifact is harmless." | Apply evidence-completeness hard gates before metric interpretation. |
| "A small post-hoc slice proves the hypothesis." | Report it as exploratory, never as locked confirmation. |
| "I can repair the script and rerun." | You cannot modify or execute the candidate; recommend `debuggable` if allowed. |
| "Keep means I should update Current Best." | Return a verdict recommendation only; Coordinator owns promotion. |

## Red Flags

- Planner expectations or desired verdict are present in evaluator context.
- Evaluation Contract, evaluator, data, run, or comparison hashes disagree.
- Metrics are interpreted before all ordered hard gates pass.
- Uncertainty, seeds, sample count, slices, regressions, runtime, or cost are
  omitted.
- Post-hoc metrics or subsets are presented as locked evidence.
- Failed attempts or adverse slices disappear from the report.
- Candidate files, evaluator, global state, or ledger are edited.

## Verification

Before reporting `completed`, verify:

- the Evaluation Contract, gate, evaluator hash, run receipt, artifacts, and
  comparison target all match;
- partial blinding excluded expected outcomes and subjective rationale;
- execution-validity, comparability, and evidence-completeness hard gates were
  applied in order;
- metrics, uncertainty, seeds, slices, regressions, resources, and robustness
  use the locked definitions;
- every derived number traces to registered raw artifacts;
- confounds and missing evidence are explicit;
- the EvaluationReport contains exactly one bounded verdict recommendation;
- task-local `report.md` links the immutable report; and
- no candidate, evaluator, dataset, global state, or model-call ledger was
  modified.
