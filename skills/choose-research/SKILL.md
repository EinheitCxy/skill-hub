---
name: choose-research
description: "Choose one evidence-bounded research direction, at most one fallback, and an early falsification gate. Use when several research ideas must be narrowed into a defensible thesis; not for a general literature review or proposal writing alone."
---

# Choose Research

## Default Decision

For a question such as “which direction should I pursue?”, answer with:

1. one recommended thesis and, only if useful, one fallback;
2. the strongest checked overlap and its implication;
3. the first cheap gate, including what it measures and the matched comparison;
4. the failure response: stop, narrow, repair, or activate the fallback.

Read applicable project instructions and reuse existing evidence before searching. Anchor the decision in the user's objective, adviser alignment when relevant, available data, hardware, and experimental budget. Verify adviser-related claims from sources.

Anchor the recommendation in available evidence. Mark unverified novelty or feasibility as unknown rather than filling the gap. Do not grow a thesis by stacking techniques.

Read [claim-and-overlap-audit.md](references/claim-and-overlap-audit.md) when the proposal's claims or overlap need scrutiny, and [narrowing-and-rejection.md](references/narrowing-and-rejection.md) when choosing or rejecting candidates. Read [feasibility-gates.md](references/feasibility-gates.md) when designing the first gate; use its acceleration-specific guidance only for acceleration or scheduling research.

## Formal Decision Packet

Use a full packet only for a proposal, a durable project decision, or an explicit request for the complete audit. Read [decision-packet-template.md](references/decision-packet-template.md), replace every instructional placeholder with evidence-backed content, and validate the result:

```bash
uv run python <skill-dir>/scripts/validate_direction_packet.py <direction-packet.md>
```

The full packet records the claim ledger, overlap matrix, rejected directions, evidence boundary, decision gates, and stop condition. It may retain one primary thesis and at most one fallback.

## Decision Discipline

- Compare object, mechanism, stage, objective, conditions, evaluation, and contribution before calling work overlapping.
- Keep an early gate cheaper than a full implementation and state the controls, budget, pass condition, and failure response.
- A negative result is a result: preserve it, restrict claims to the supported setting, and do not lower thresholds after observing failure.
- Do not claim “first,” “no prior work,” or guaranteed novelty from a partial search.

Read [subskills-and-tools.md](references/subskills-and-tools.md) only when the current step lacks a needed capability. Evidence review precedes brainstorming when novelty matters; do not invoke every listed skill as a fixed sequence.
