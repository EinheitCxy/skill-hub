# Evidence and Explanations

## Claim Types

| Claim | Minimum evidence |
| --- | --- |
| Evaluator or metric definition | Source, configuration, or versioned evaluator evidence |
| Observed difference | Recomputed data with conditions and uncertainty |
| Mechanism | A measurement that changes as the proposed mechanism predicts, plus a competing explanation check |
| Causal claim | Matched intervention and controls that exclude plausible alternatives |

## Competing Explanations

Consider protocol drift, sample composition, checkpoint mismatch, incomplete quantization, stochasticity, evaluator thresholds, data corruption, and runtime-path differences before attributing a result to model behavior.

For every proposed explanation, specify the expected observation if it is true and the smallest existing-data or targeted check that would differ under the strongest alternative. Mark explanations as hypotheses until that check is completed.
