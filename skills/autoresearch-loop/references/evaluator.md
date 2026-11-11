# Independent Evaluation Contract (internal: Evaluator)

## Purpose and owner

The Independent Evaluator (`Evaluator`) independently evaluates registered run
evidence against a human-locked Evaluation Contract (`EvaluationSpec`). It
produces an `EvaluationReport` and a
non-binding verdict recommendation. Only the Coordinator may record a verdict
or apply promotion.

The Evaluator cannot edit a candidate, code, data, metrics, run artifacts,
Evaluation Contract, Research State Snapshot, Candidate Under Review, or
Verified Best Candidate. An
implementation defect produces a `debuggable` recommendation and a new
candidate proposal; it is never repaired inside evaluation.

## Locked inputs

The active Evaluation Contract (`EvaluationSpec`) fixes:

- dataset and split identities plus the Initial Baseline;
- primary and secondary metrics, direction, and measurement noise;
- project-specific unacceptable regression rules;
- slice, qualitative, and resource requirements;
- minimum gain and promotion policy;
- seeds, minimum sample count, and robustness artifacts;
- ordered hard gates; and
- the locked evaluator contract.

Concrete Verified Best Candidate is not written into the Evaluation Contract.
At the start of each cycle, an immutable evaluation-cycle checkpoint binds the active spec artifact,
state revision/hash, and exact comparison candidate/report. Every RunSpec,
report, Pareto pool, and promotion in that cycle uses the same checkpoint.

Keep at most ONE locked diagnostic EvaluationSpec; diagnostic, contaminated,
test-A, and test-B variants must not re-lock it multiple times.

The Evaluator receives only the locked contract and registered run evidence:
metrics, logs, plots, outputs, resource receipts, provenance, and comparison
artifacts. Under partial blinding it must not receive the planner's expected
direction, subjective confidence, or preferred candidate. Scientific critique
begins only after this independent report exists.

## Ordered evaluation

Evaluation stops at the first failed hard gate:

1. `execution validity`;
2. `contract comparability`;
3. `evidence completeness`.

Only a report passing all hard gates proceeds to:

1. primary and secondary metrics;
2. uncertainty and seed/sample evidence;
3. required slices and qualitative failures;
4. robustness and reproducibility;
5. confounds and alternative explanations;
6. cost, latency, memory, and implementation complexity; and
7. Pareto dimensions and verdict recommendation.

Applied-ML evaluation should expose relevant per-class, domain, subgroup,
difficulty, and failure-mode slices rather than hiding regressions in an
aggregate. Missing required evidence is an invalid report, not a neutral score.

## EvaluationReport evidence

An official report binds:

- report, candidate, EvaluationSpec, and evaluation-cycle checkpoint IDs;
- `execution_valid`, `comparable`, `evidence_complete`, and `official`;
- finite metrics including the primary metric and primary gain;
- uncertainty, required slices, qualitative evidence, and resources;
- robustness and reproducibility observations;
- explicit confounds, alternative explanations, and contract violations;
- Pareto values for objective, robustness, evidence strength,
  information gain, cost, complexity, and confound penalty; and
- one recommendation: `keep`, `discard`, `inconclusive`, `debuggable`,
  `needs_user`, or `failed`. `needs_user` is the last resort, reserved for
  conflicting evidence requiring human judgment; recoverable findings use
  `debuggable` or `inconclusive` so the Coordinator can dispatch a debug
  subagent or reason and proceed.

The registered report artifact and payload identities must agree. Stale-cycle
reports remain scientific evidence but cannot mutate Best.

## Decision boundary

The Coordinator, not the Evaluator:

- records the recommendation as a candidate verdict;
- checks the locked minimum gain and regression thresholds;
- constructs the exact-cycle Pareto frontier;
- promotes an eligible official candidate to Provisional Best; and
- promotes that exact pointer to Current Best only after required robustness
  artifacts pass.

No LLM scalar replaces the ordered hard gates or Pareto comparison. Confounded
evidence is `inconclusive`; negative and failed evidence remains registered.

## Invalid examples

```json
{
  "execution_valid": true,
  "comparable": false,
  "evidence_complete": true,
  "verdict": "keep"
}
```

Invalid for promotion: contract comparability failed regardless of verdict.

```json
{"metrics": {"accuracy": 0.91}, "uncertainty": null}
```

Invalid when uncertainty is required by the locked contract.

Also invalid: recomputing an unregistered metric, changing a threshold after
seeing results, mixing checkpoints in a Pareto pool, suppressing a confound,
editing candidate code, or promoting directly from a recommendation.

## Verification checklist

- Resolve and hash-check the active EvaluationSpec and cycle checkpoint.
- Verify every run artifact before reading its payload.
- Confirm partial blinding and the locked evaluator contract.
- Apply hard gates in order and stop at the first failure.
- Require primary metric, uncertainty, slices, qualitative, and resources.
- Record confounds, alternative explanations, and reproducibility limits.
- Keep the verdict advisory and candidate/state mutation outside evaluation.
- Reject stale-cycle promotion while preserving its report as evidence.
