---
name: research-hypothesis-generation
description: Converts verified evidence and the Current Best into diverse, bounded, falsifiable ML candidate hypotheses. Use when an autoresearch task contract asks for exploit, explore, or falsify directions with measurable predictions, explicit failure conditions, information-value ranking, and one primary change per candidate.
---

# Research Hypothesis Generation

## Role
The candidate-generation worker; converts verified evidence and the Current Best into diverse, bounded, falsifiable ML candidate hypotheses (third phase of the autoresearch loop).

## Inputs
- task-local `brief.md`, Research Plan, evidence summaries and artifact IDs, Initial Baseline, Verified Best Candidate (internal: `Current Best`), active Change Scope (internal: `Modification Envelope`), and Task Budget Allocation (internal: `Budget Slice`).

## Outputs
- A bounded pool of falsifiable candidates (exploit, explore, falsify), each with one primary change, expected observation, falsification condition, and an information-value ranking.
- task-local `report.md` and a bounded receipt.

## Boundaries
- Does not edit code or configuration, execute commands, modify datasets, lock the Evaluation Contract (`EvaluationSpec`), reserve budget, open tasks, judge run results, or change the Verified Best Candidate.
- Does not select or promote the Verified Best (Coordinator does).
- Does not edit other skills' artifacts.
- See [Worker Skills](../../references/worker-skills.md) for this skill's place in the loop.

## Overview

Generate candidates that can teach the project something, including candidates
designed to disconfirm the current explanation. Novel wording is not novelty;
each proposal must name a mechanism and a decisive observation.

This is one primary worker role invoked through an explicit autoresearch task
contract. It may use zero, one, or two Coordinator-selected capabilities from
`scientific-brainstorming`, `hypothesis-generation`, and
`scientific-critical-thinking`; never load a third and never create another
subagent.

## When to Use

Use this skill when verified evidence must become:

- a pool of falsifiable ML hypotheses;
- exploit, explore, and falsify candidate branches;
- mechanism-focused alternatives to the Current Best;
- measurable predictions and falsification conditions; or
- ranked candidates for later experiment design.

Do not use it to search for sources, lock evaluation rules, edit code, execute
experiments, evaluate runs, or promote a candidate.

## Process

1. **Validate bounded context.** Read only task-local `brief.md`, the Research
   Research Plan, evidence summaries and artifact IDs, Initial Baseline,
   Verified Best Candidate (internal: `Current Best`), active Change Scope
   (internal: `Modification Envelope`), and Task Budget Allocation (internal:
   `Budget Slice`). Record missing or
   contradictory inputs; do not widen the charter.
2. **Extract the open uncertainty.** State what is observed, what mechanism is
   currently believed, which evidence supports it, what remains unexplained,
   and which confounds could generate the same observation.
3. **Generate structurally different branches.** When evidence supports them,
   produce at least one:
   - `exploit`: improve a verified promising mechanism;
   - `explore`: test a materially different mechanism; and
   - `falsify`: try to disprove the current explanation or expose a confound.
   If a branch lacks evidence, state why rather than fabricating one.
4. **Specify every candidate.** Record a stable candidate label, kind,
   falsifiable hypothesis, mechanism, exactly one primary change, expected
   observation, falsification condition, supporting evidence IDs, alternative
   explanations, confounds, cost class, allowed files, data intervention,
   dependency impact, and prerequisite.
5. **Reflect against evidence.** Challenge quality, novelty, feasibility, and
   unnecessary complexity. Incorporate new evidence from allowed artifacts,
   but preserve the candidate's core mechanism unless a glaring contradiction
   requires rejection.
6. **Reject false diversity.** Remove cosmetic renames, hyperparameter-only
   variations without a distinct question, bundled primary changes, duplicate
   normalized mechanisms, and unsupported novelty claims. Record them under
   `Rejected Ideas` with the reason.
7. **Rank by information value.** Prefer candidates that decisively distinguish
   mechanisms, resolve a high-impact uncertainty, or cheaply falsify a claim.
   Use expected metric gain only after information value, feasibility,
   boundary fit, and cost.
8. **Recommend a bounded pool.** Mark candidates `ready_for_design`,
   `needs_evidence`, `out_of_boundary`, or `rejected`. Do not preselect winners
   based on hoped-for results.
9. **Write task-local `report.md`.** Preserve required YAML frontmatter and the
   ordered sections: `Summary`, `Evidence`, `Ideas Considered`,
   `Rejected Ideas`, `Risks and Confounds`, `Recommended Next Action`, and
   `Artifact Links`. The recommendation points to candidate artifacts, not
   prose-only ideas.
10. **Return a bounded receipt.** Return only the report path, short summary,
    status (`completed`, `blocked`, `needs_user`, or `failed`), and new artifact
    IDs.

## Ownership and Safety Boundary

You own hypothesis construction, diversity checks, reflection, falsifiers, and
candidate ranking for this task only.

You must not edit global state.

You must not edit the model-call ledger.

Do not edit code or configuration, execute commands, modify datasets, lock an
Evaluation Contract (`EvaluationSpec`), reserve budget, open new tasks, create
subagents, judge run results, or change the Verified Best Candidate. A
boundary-breaking candidate is
`out_of_boundary` or `needs_user`, never silently normalized into scope.

## Common Rationalizations

| Rationalization | Required response |
|---|---|
| "A higher score is enough of a hypothesis." | State a mechanism, expected observation, and falsification condition. |
| "Changing three components gives the best chance." | Keep exactly one primary change so evidence remains interpretable. |
| "Explore means another hyperparameter." | Require a materially different mechanism or scientific question. |
| "Falsification wastes budget." | Prefer the cheapest decisive test when it can invalidate a costly direction. |
| "This looks novel to me." | Tie novelty claims to verified evidence or label them unsupported. |

## Red Flags

- A candidate cannot be falsified by any declared observation.
- Expected observation restates the hypothesis without a measurable outcome.
- Exploit, explore, and falsify entries are cosmetic variants.
- Multiple primary changes make attribution impossible.
- Ranking optimizes expected gain while ignoring information value or cost.
- Evidence IDs, confounds, allowed files, or budget class are missing.
- The worker edits code, global state, the ledger, or experimental results.

## Verification

Before reporting `completed`, verify:

- each retained candidate is falsifiable and mechanism-specific;
- each has one primary change, expected observation, and falsification
  condition;
- exploit, explore, and falsify coverage exists or an evidence-based omission
  is recorded;
- duplicate and cosmetic ideas are rejected;
- supporting evidence and alternative explanations are traceable;
- ranking considers information value before expected metric gain;
- all files, data interventions, dependencies, and costs fit the active
  boundary or are marked `needs_user`;
- task-local `report.md` and candidate artifacts follow the output contract;
  and
- no code, experiment, global state, or model-call ledger was modified.
