---
name: narrow-research-direction
description: Turn a scattered research idea set into one evidence-backed primary thesis, at most one fallback, an overlap audit, and falsifiable go or no-go gates. Use when the user asks which direction is publishable or adviser-aligned, requests novelty or feasibility screening, or needs a defensible proposal decision. Do not use for an evidence-poor open brainstorm, a pure literature review, or implementation after the direction is already fixed.
---

# Narrow a Research Direction

## Objective

Convert an audited evidence base into one defensible research decision. Make the rejection logic, evidence limits, and failure responses as explicit as the winning thesis.

## Load References Selectively

- Read [claim-and-overlap-audit.md](references/claim-and-overlap-audit.md) before making novelty or differentiation claims.
- Read [narrowing-and-rejection.md](references/narrowing-and-rejection.md) when the idea set has multiple mechanisms, tasks, or contributions.
- Read [feasibility-gates.md](references/feasibility-gates.md) before proposing experiments, milestones, or a learned component.
- Use [decision-packet-template.md](references/decision-packet-template.md) for the durable output.
- Read [subskills-and-tools.md](references/subskills-and-tools.md) when selecting critical-thinking, literature, statistical, or planning capabilities.

## Workflow

### 1. Confirm Evidence Sufficiency

1. Read applicable project instructions and canonical research documents.
2. Locate the current corpus inventory, evidence cards, bibliography, findings, prior proposals, and novelty scan.
3. Separate full-text or code evidence from metadata, abstracts, secondary reports, and inference.
4. Identify drift-prone claims and refresh them with current primary sources.
5. If the evidence base is insufficient, stop direction selection and invoke or recommend an evidence-first review.

Never use brainstorming fluency as a substitute for overlap screening.

### 2. Anchor the Decision

Record:

- the user's actual objective;
- adviser, lab, dataset, deployment, hardware, time, or publication constraints;
- the intended unit of contribution;
- the baseline and experimental budget that can realistically be supported;
- explicit exclusions.

When adviser alignment matters, begin from the adviser's verified methods and open problems before inventing a standalone mechanism.

### 3. Decompose Atomic Claims

Rewrite every candidate as separately testable claims:

- mechanism claim;
- novelty claim;
- systems claim;
- quality or safety claim;
- generalization claim;
- theoretical claim.

Do not allow a bundle of known components to masquerade as one novel contribution.

### 4. Audit Overlap

Compare each candidate against the strongest related work across:

- optimized object;
- mechanism;
- objective;
- training or inference stage;
- experimental protocol;
- deployment assumptions;
- downstream decision or task consequence.

Classify the result as:

- direct overlap;
- partial overlap;
- orthogonal component;
- baseline;
- conditional branch;
- surviving thesis.

Treat “not found in the searched space” as a scoped result, not a novelty guarantee.

### 5. Reject and Narrow

1. Reject candidates whose main claim is already covered.
2. Demote infrastructure, benchmark work, or standard combinations when they do not supply the primary contribution.
3. Reject candidates whose first feasibility condition cannot be tested with the available resources.
4. Explain every rejection.
5. Select exactly one primary thesis.
6. Keep no more than one fallback, and make its activation condition explicit.

If the direction remains scattered, narrow the task, mechanism, metric, or deployment setting instead of adding more components.

### 6. Design Decision Gates

Define the cheapest experiment that can falsify the central premise before building complex machinery. Gates must include:

- pass condition;
- measurement and controls;
- matched compute, data, tuning, or wall-clock budget where relevant;
- failure response;
- claims permitted after pass;
- claims prohibited after failure.

Implement a rule-based or minimal baseline before a learned controller, scheduler, or search policy unless the evidence proves the simpler comparator invalid.

### 7. Produce the Decision Packet

Use the provided template. Include:

- one primary thesis;
- at most one fallback;
- claim ledger;
- overlap matrix;
- rejected directions;
- evidence boundary;
- decision gates;
- failure responses;
- next evidence or experiment step;
- proposed canonical document changes.

Validate:

```bash
uv run python scripts/validate_direction_packet.py <direction-packet.md>
```

### 8. Update Canonical Artifacts Carefully

Update the proposal, findings, protocol, and task order only after the decision packet passes review. Preserve superseded ideas as historical or rejected where audit value remains. Do not silently convert conditional branches into the primary direction.

## Completion Report

Lead with the selected thesis and why it survived. Then state:

- strongest overlap and how the thesis differs;
- rejected alternatives and their roles;
- first go or no-go gate;
- explicit failure response;
- evidence levels and unverified assumptions;
- files changed and validations performed.

Negative or conditional conclusions are valid outcomes.
