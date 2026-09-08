# Synthesis and Comparison

## Build Axes Before Conclusions

Choose axes that match the question. Common technical axes include:

- optimized object: weights, activations, tokens, operators, steps, candidates, memory movement;
- intervention time: training, calibration, compile, offline search, online inference;
- mechanism: quantization, sparsity, caching, distillation, routing, asynchronous execution;
- deployment reality: fake quantization versus real kernel, dynamic shape cost, communication, memory bandwidth;
- quality consequence: reconstruction, prediction, ranking, action, success, safety;
- evidence status: reported, reproduced, code-inspected, inferred.

## Metric Discipline

Never flatten distinct metrics into a single “speed” column. Separate:

- theoretical FLOPs;
- measured kernel latency;
- single-query latency;
- throughput;
- actions per second;
- environment-sample efficiency;
- task completion time;
- deadline misses and tail latency;
- energy per successful outcome.

Carry hardware, precision, batch size, sequence or action horizon, sampling steps, control mode, warm-up state, and quality constraint with every performance value.

## Cross-Paper Judgment

For each synthesized conclusion:

1. list the supporting items and their evidence levels;
2. identify disagreements or incompatible settings;
3. state the shared mechanism;
4. state the boundary where the conclusion may fail;
5. distinguish absence of evidence from evidence of absence.

## Full Distribution and Error Propagation

When studying compression or acceleration, do not report only outliers or averages. Inspect:

- full distribution shape;
- module, layer, timestep, token, state, or action dependence;
- calibration drift;
- downstream Jacobian or sensitivity;
- accumulated rollout or closed-loop error;
- rank or decision preservation;
- contact, OOD, and safety failures.

## Recommended Deliverable Structure

1. Scope and corpus audit
2. Terminology and metric definitions
3. Per-item evidence cards
4. Mechanism taxonomy
5. Context-qualified comparison
6. Agreements and contradictions
7. Evidence gaps and unresolved questions
8. Implications and bounded recommendations
9. Validation and limitations
