---
name: research-evidence-critique
description: Adversarially critiques ML evidence, confounds, baselines, data leakage, causal claims, and falsification. Use only after an independent EvaluationReport exists and an autoresearch task contract asks what could invalidate the evaluated hypothesis or which cheapest decisive ablation should come next.
---

# Research Evidence Critique

## Role
The adversarial critique worker; tests the scientific interpretation after an independent EvaluationReport exists and designs the cheapest decisive falsification or ablation (seventh phase of the autoresearch loop).

## Inputs
- task-local `brief.md`, the independent EvaluationReport, registered evidence IDs, comparison target, locked Evaluation Contract (`EvaluationSpec`), and the hypothesis or claimed-mechanism artifact (read only after evaluation validates).

## Outputs
- Fatal, material, and minor concerns; alternative explanations and disconfirming evidence; one cheapest decisive falsification or ablation; and exactly one calibrated recommendation.
- task-local `report.md` and a bounded receipt.

## Boundaries
- Does not change the candidate, Evaluation Contract, Research State (`ResearchState`), evaluator, metrics, datasets, reports, artifact metadata, Resource Budget, Best pointer, or global journal; does not run an ablation or recompute official metrics.
- Does not select or promote the Verified Best (Coordinator does).
- Does not edit other skills' artifacts.
- See [Worker Skills](../autoresearch-loop/references/worker-skills.md) for this skill's place in the loop.

## Overview

Try to break the scientific interpretation after independent evaluation, not
the code during execution. The goal is calibrated confidence and decisive next
evidence, not automatic rejection.

This is one primary worker role invoked by an explicit autoresearch task
contract. Its optional allowed-capability pool is
`scientific-critical-thinking` and `peer-review`. The Coordinator may select at
most two for one call; never nest capability composition or create a nested
subagent.

## When to Use

Use this skill after an independent EvaluationReport exists to:

- test the claimed mechanism against alternative explanations;
- inspect data leakage, weak baseline, and unfair-comparison risks;
- identify confounds, metric gaming, cherry-picking, and seed sensitivity;
- locate disconfirming evidence and slice regressions; or
- propose the cheapest decisive falsification or ablation.

Do not use it to compute the official metrics, repair or rerun code, design the
original Evaluation Contract (internal: `EvaluationSpec`), search the initial
literature, or write the final report.

## Process

1. **Require independent evaluation first.** Read task-local `brief.md` and
   locate the independent EvaluationReport. Validate its identity, registered
   evidence IDs, comparison target, and locked Evaluation Contract
   (`EvaluationSpec`). Return `blocked` if the independent report is absent or
   invalid. Only after that validation
   may you read the hypothesis and claimed-mechanism artifact.
2. **Try to disprove the causal claim.** State the intervention, measured
   outcome, proposed mechanism, required assumptions, and strength of the
   claimed conclusion. Actively seek observations that overturn the mechanism;
   separate association, prediction, mechanism evidence, and causal evidence.
3. **Seek disconfirming evidence.** Start with adverse seeds, failed attempts,
   negative results, qualitative failures, uncertainty intervals, slice
   regressions, robustness gaps, and observations the favored story does not
   explain. Do not search only for confirming artifacts.
4. **Generate alternative explanations.** Ask whether gains could arise from
   data volume, leakage, preprocessing, compute, parameter count, tuning
   exposure, initialization, evaluator quirks, metric selection, randomness,
   implementation mismatch, or reporting choices rather than the proposed
   mechanism.
5. **Audit common validity threats.**
   - data leakage, contamination, duplicate or subject overlap;
   - weak baseline, stale baseline, unequal budget, or unfair tuning;
   - metric gaming, post-hoc subsets, multiple-comparison selection;
   - cherry-picked seeds, runs, checkpoints, plots, or qualitative samples;
   - budget mismatch, early stopping asymmetry, or censored failures;
   - missing ablations, robustness tests, or implementation fidelity.
6. **Grade each concern.** Use:
   - `fatal`: current evidence cannot support the claimed decision;
   - `material`: confidence or generalization changes substantially;
   - `minor`: reporting or follow-up issue that does not overturn the decision.
   Link evidence and state what observation would resolve each concern.
7. **Design the cheapest decisive challenge.** Propose one minimal ablation,
   negative control, leakage test, matched baseline, seed replication, slice
   check, or alternative-mechanism test that best separates explanations.
   Include prediction under each explanation, cost class, required artifacts,
   and boundary fit. Do not execute it.
8. **Issue a calibrated recommendation.** Use exactly one recommendation:
   `promotion`, `more evidence`, `inconclusive`, `discard`, or `needs_user`.
   Explain whether fatal and material concerns survive; do not mutate candidate
   state.
9. **Write task-local `report.md`.** Preserve required YAML frontmatter and the
   ordered sections: `Summary`, `Evidence`, `Ideas Considered`,
   `Rejected Ideas`, `Risks and Confounds`, `Recommended Next Action`, and
   `Artifact Links`. Link the critique and proposed challenge artifacts.
10. **Return a bounded receipt.** Return only the report path, bounded summary,
    `completed`, `blocked`, `needs_user`, or `failed`, and artifact IDs.

## Output Contract

Write the critique to the current task's task-local Markdown `report.md`. It
must contain:

- evaluated candidate and EvaluationReport identity;
- fatal, material, and minor concerns;
- alternative explanations and disconfirming evidence;
- the cheapest decisive falsification or ablation;
- exactly one allowed recommendation;
- unresolved evidence and cited artifact IDs; and
- an explicit statement that global state was not mutated.

## Ownership and Safety Boundary

You own adversarial interpretation review, validity-threat classification, and
minimal falsification design for the evaluated hypothesis.

You must not edit global state.

You must not edit the model-call ledger.

Do not change the candidate, Evaluation Contract (`EvaluationSpec`), Research
State (`ResearchState`), evaluator, metrics, datasets, reports, artifact
metadata, Resource Budget, Best pointer, or global journal.
Do not run an ablation, recompute official metrics, publish conclusions, or
create a nested subagent.

## Common Rationalizations

| Rationalization | Required response |
|---|---|
| "The primary metric passed, so mechanism is proven." | Test alternative explanations and distinguish predictive from causal evidence. |
| "The baseline is standard." | Check version, tuning, data, compute, and implementation fairness. |
| "One bad seed is just noise." | Compare it with the locked uncertainty and seed requirements. |
| "More experiments are always safer." | Propose the cheapest decisive challenge, not an unbounded wishlist. |
| "Criticism should reject the result." | Grade concerns and recommend proportionally to surviving evidence. |

## Red Flags

- Critique begins before an independent EvaluationReport exists.
- The claimed mechanism is never stated separately from metric improvement.
- No alternative explanation or disconfirming evidence is considered.
- Data leakage, weak baseline, metric gaming, seeds, slices, and budget fairness
  are treated as generic checkboxes without artifact evidence.
- Concerns lack severity or a resolution condition.
- Proposed follow-up changes multiple variables or exceeds the active boundary.
- The worker edits evidence, state, the ledger, or executes a challenge.

## Verification

Before reporting `completed`, verify:

- independent evaluation preceded access to the hypothesis interpretation;
- the causal claim and required assumptions are explicit;
- disconfirming evidence and at least one plausible alternative explanation
  were examined;
- data leakage, weak baseline, confound, metric, seed, slice, budget, and
  implementation threats were addressed;
- every concern is fatal, material, or minor with evidence and resolution;
- the cheapest decisive falsification or ablation separates competing
  explanations and fits the boundary;
- exactly one calibrated recommendation is present;
- task-local `report.md` links registered artifacts; and
- no candidate, evaluator, global state, or model-call ledger was modified.
