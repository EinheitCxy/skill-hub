# Claim and Overlap Audit

## Atomic Claim Ledger

Split a proposal into rows:

| Claim | Type | Required evidence | Current support | Status |
| --- | --- | --- | --- | --- |
| What is asserted | Mechanism, novelty, systems, quality, safety, theory | Full text, code, experiment, proof | A, B, C, or missing | Supported, testable, overclaimed |

Avoid conjunctions. “Faster and safer and more general” is at least three claims.

## Overlap Dimensions

Compare candidate and prior work by:

1. **Object:** weight, activation, token, operator, candidate, trajectory, memory, communication.
2. **Mechanism:** pruning, quantization, routing, caching, distillation, early exit, verification, asynchronous execution.
3. **Stage:** training, calibration, compile, offline search, online inference, closed loop.
4. **Objective:** reconstruction, likelihood, latency, throughput, ranking, action success, safety, energy.
5. **Conditioning:** input, state, timestep, layer, risk, hardware, deadline.
6. **Evaluation:** task, baseline, hardware, budget, metric, controls.
7. **Contribution form:** new method, new coupling, theory, benchmark, system, negative result.

## Overlap Judgments

- **Direct overlap:** same central mechanism and contribution target.
- **Partial overlap:** shared mechanism but materially different objective or setting.
- **Orthogonal component:** useful implementation element but not the thesis.
- **Baseline:** prior approach the proposal must beat.
- **Conditional branch:** worth testing only after a prerequisite gate.
- **Surviving thesis:** a scoped claim not eliminated by checked evidence.

## Novelty Language

Use:

- “No exact match was found in the screened corpus.”
- “The checked papers cover X separately but not the proposed coupling under Y.”
- “This remains a scoped search conclusion pending full-text and citation-chain review.”

Avoid:

- “This is the first.”
- “No prior work exists.”
- “Guaranteed novel.”

unless an appropriate systematic review supports the statement.
