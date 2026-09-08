# Feasibility Gates

## Gate Anatomy

Every gate must specify:

| Field | Requirement |
| --- | --- |
| Premise | The assumption the research direction needs |
| Measurement | Observable statistic or comparison |
| Controls | Fixed components and causal controls |
| Budget | Matched wall-clock, compute, data, or search budget |
| Pass condition | Preregistered threshold or directional condition |
| Failure response | Stop, repair, narrow, or fall back |
| Allowed claim | What a pass permits |
| Forbidden claim | What remains unsupported |

## Gate Ordering

1. **Evidence gate:** full-text and implementation overlap screened.
2. **Mechanism gate:** intervention changes the intended variable.
3. **Feasibility gate:** cheap proxy preserves the outcome that matters.
4. **Runtime gate:** overhead is smaller than saved work.
5. **Causality gate:** matched controls exclude artifacts.
6. **Generalization gate:** result survives relevant task, contact, OOD, or population shift.
7. **Safety or calibration gate:** risk claims match observed coverage.

Use only applicable gates, but keep the order.

## Failure Responses

- Missing evidence: do not freeze novelty language.
- Mechanism does not move the target: stop the mechanism branch.
- Proxy fails decision preservation: repair representation or score consistency before scheduler work.
- Runtime overhead erases savings: simplify or stop dynamic allocation.
- Matched controls remove the effect: report artifact, not performance gain.
- Generalization fails: restrict the claim to the demonstrated setting.
- Calibration fails: remove safety or certification language.

Do not silently lower a threshold after observing failure.

## Acceleration or Scheduling Research

Apply this section only when the direction uses learned scheduling, dynamic allocation, compression, or inference acceleration. It is not a general prerequisite for other research domains.

Before a learned component:

1. implement a deterministic or rule-based comparator;
2. verify the input signal contains actionable information;
3. count training, tuning, inference, compilation, and scheduling costs;
4. compare under matched search and wall-clock budgets;
5. define the negative result that stops the branch.
