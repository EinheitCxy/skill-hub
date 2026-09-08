---
name: results-validator
description: "Diagnose surprising or conflicting experiment results from data, evaluation, and protocol evidence. Use for success-rate changes, regressions, ablations, quantization comparisons, or missing/failed trial analysis; not for choosing a new research direction."
---

# Results Validator

## Outcome

Explain what the available evidence supports, what it does not, and the lowest-cost check that separates the live explanations. Diagnosis alone does not authorize new experiments or code changes. Execute a targeted check when the user has requested or authorized it; otherwise provide its protocol. Route an explicit research-direction decision to `choose-research`.

## Establish the Comparison Contract

1. Identify the task, dataset or episodes, sample selection, random seeds, checkpoint, model variant, quantization scope, and evaluation protocol for every result being compared.
2. Name the statistical unit separately for episode, task instance, seed, trajectory, action step, and object. Verify pairing through stable trial keys and matched task instances, seeds and initial states; closed-loop policies can legitimately observe different later states, so identical full trajectories or later policy inputs are not required; do not assume records are paired or IID.
3. Recompute supplied raw results where possible. Check trial counts, duplicates, missing entries, invalid records, failure modes, and aggregation before interpreting a headline metric.
4. Keep task success labels, classification conditions, trajectory features, object contact, and verified object grasp as different evidence layers. Do not invent H2/H3 or other category definitions absent from the task and evaluator.

Read [comparison-and-statistics.md](references/comparison-and-statistics.md) when data need recomputation, pairing, missingness, or a statistical test. Read [evidence-and-explanations.md](references/evidence-and-explanations.md) before turning an observation into a mechanism claim.

## Diagnose in Three Layers

State each conclusion under the correct layer:

| Layer | Question | Allowed conclusion |
| --- | --- | --- |
| Code definition | What does the loader, metric, threshold, or evaluator count? | Verified from source or configuration. |
| Observation | What changed after recomputation under the stated protocol? | Measured difference with sample and uncertainty limits. |
| Mechanism hypothesis | Why might the difference occur? | A labeled hypothesis with competing explanations. |

For each mechanism hypothesis, name at least one credible alternative and the smallest discriminating check. Prefer replaying matched samples, inspecting recorded actions or trajectories, and targeted evaluator checks before a costly new training run.

## Evidence Boundary

- A changed aggregate is not proof of a causal mechanism.
- Quantization effects require the exact precision, calibrated modules, excluded modules, checkpoint, and runtime path.
- Separate a missing result from a failed result and from an excluded sample; report the handling rule and its impact.
- Keep negative or null findings when they rule out an explanation.
- State what would change the conclusion and what remains unknown.

## Default Report

Return the comparison contract, recomputed observation, evidence boundary, ranked explanations, one lowest-cost discriminating check, and the next safe action. Use a fuller incident record only when the user needs a durable experiment handoff.
