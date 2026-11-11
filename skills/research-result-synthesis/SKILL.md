---
name: research-result-synthesis
description: Synthesizes a final ML research report from registered, accepted experiments, evaluation reports, critique reports, negative or inconclusive evidence, and verified citations. Use when an autoresearch task contract asks for citation-verified claim-evidence integration without invented results or hidden adverse findings.
---

# Research Result Synthesis

## Role
The final-report synthesis worker; integrates registered, accepted experiments, evaluation reports, critique reports, and verified citations into a citation-verified research report (eighth phase of the autoresearch loop).

## Inputs
- Registered, accepted artifact IDs and their accepted experiment evidence, EvaluationReports, critique reports, negative or inconclusive result artifacts, and verified citations.

## Outputs
- A completed Claim-Evidence Contract, citation-verification results, and the final task-local Markdown `report.md` (held at the publication gate).
- A bounded receipt.

## Boundaries
- Does not change Research State (`ResearchState`), the Candidate Graph, source or experiment artifacts, candidate verdicts, EvaluationReports, Evaluation Contract (`EvaluationSpec`), metrics, candidate lineage, budgets, Best pointer, global journal, or gate receipts; does not rerun experiments, hide adverse evidence, invent results, or publish.
- Does not select or promote the Verified Best (Coordinator does).
- Does not edit other skills' artifacts.
- See [Worker Skills](../autoresearch-loop/references/worker-skills.md) for this skill's place in the loop.

## Overview

Build the claim-evidence structure before writing narrative prose. Every number
must resolve to a run artifact, every external claim to a verified citation,
and every limitation to its decision impact.

This is one primary worker role invoked by an explicit autoresearch task
contract. Its optional allowed-capability pool is `scientific-writing`,
`citation-management`, and `scientific-critical-thinking`. The Coordinator may
select at most two for one call; never load all three, nest capability
composition, or create a nested subagent.

## When to Use

Use this skill when the task is to:

- integrate accepted evaluation and robustness reports;
- preserve negative result, failed, contradictory, and inconclusive evidence;
- construct a traceable claim-evidence table;
- verify citations and numerical provenance; or
- draft the final Markdown research report before publication approval.

Do not use it to search the initial literature, generate candidates, implement
or evaluate experiments, change verdicts, promote Best pointers, or publish.

## Process

1. **Validate the synthesis boundary.** Load only registered, accepted artifact
   IDs and their accepted experiment evidence, EvaluationReports, critic
   reports, negative or inconclusive result artifacts, and verified citations.
   Accepted failed-experiment and contradictory artifacts remain admissible;
   exclude unregistered files, rejected evidence, and unsupported narrative.
2. **Inventory all admissible evidence.** Record artifact type, identity, hash,
   candidate and comparison target, status, provenance, and availability.
   Separate accepted scientific evidence from execution-only, exploratory,
   rejected, missing, or invalid artifacts.
3. **Build the claim-evidence table first.** Complete every field in the
   Claim-Evidence Contract before drafting narrative prose. Mark absent support
   explicitly; never infer a missing value.
4. **Trace every number.** Resolve every metric, uncertainty interval, sample
   count, runtime, cost, and resource statement to a registered run artifact
   and its accepted metric definition. Preserve units, aggregation,
   comparison, and seed/sample context. Remove orphan numbers.
5. **Preserve adverse evidence.** Include failed experiments, negative result
   artifacts, adverse seeds, slice regressions, contradictory evidence,
   discarded candidates, and inconclusive findings when they bear on a claim.
   Explain what each rules out or leaves unresolved.
6. **Calibrate claims.** Do not turn correlation into mechanism or causality.
   Narrow or remove claims when independent evaluation, evidence critique,
   robustness, or limitations do not support their strength. Distinguish
   project-specific evidence from external generalization.
7. **Perform citation verification.** Apply the Citation Verification contract
   to every external claim. Mark a failed claim for removal or downgrade; do
   not complete, guess, or fabricate a citation or bibliographic field.
8. **State limitations and scope.** Cover generalization limits, resource
   limits, dataset and domain limits, evaluation noise, sample/seed limits,
   unresolved confounds, implementation constraints, unsupported claims,
   negative evidence, and what was not tested.
9. **Write narrative from the table.** Structure conclusions around the
   calibrated claim-evidence rows, not the experiment chronology or desired
   story. Use exact numbers only where they materially support a claim and
   retain links to evidence.
10. **Write only the final task-local Markdown `report.md`.** Preserve required
    YAML frontmatter and ordered sections: `Summary`, `Evidence`,
    `Ideas Considered`, `Rejected Ideas`, `Risks and Confounds`,
    `Recommended Next Action`, and `Artifact Links`. Include the claim-evidence
    table and citation-verification results in this report.
11. **Stop at the publication gate.** Produce the approved final-report
    artifact but do not submit, upload, email, announce, or otherwise publish
    it without an explicit publication approval receipt.
12. **Return a bounded receipt.** Return only the report path, bounded summary,
    `completed`, `blocked`, `needs_user`, or `failed`, and artifact IDs.

## Claim-Evidence Contract

Build this table before narrative prose. Every row must contain:

- `claim_id`;
- claim text;
- supporting artifact IDs;
- contradicting artifact IDs;
- metric/run evidence, with each number bound to a run artifact ID;
- citation IDs;
- confidence/status;
- limitations; and
- unresolved confounds.

## Citation Verification

For every citation, reopen the primary source and verify source identity,
publication status, locator, and sentence-level support. If verification fails,
mark the dependent claim for removal or downgrade. Never replace missing
support with a search snippet, guessed citation, or fabricated metadata.

## Ownership and Safety Boundary

You own evidence inventory, claim calibration, numerical provenance, citation
verification, limitation reporting, and final-draft synthesis.

You must not edit global state.

You must not edit the model-call ledger.

Do not change Research State (`ResearchState`), the Candidate Graph, source or
experiment artifacts, candidate verdicts, EvaluationReports, Evaluation
Contract (`EvaluationSpec`), metrics,
candidate lineage, budgets, Best pointer, global journal, or gate receipts. Do
not rerun experiments, hide adverse evidence, invent results, claim unsupported
novelty, publish, or create a nested subagent.

## Common Rationalizations

| Rationalization | Required response |
|---|---|
| "The narrative reads better without failed runs." | Preserve relevant negative, failed, contradictory, and inconclusive evidence. |
| "This number is in the summary, so provenance is obvious." | Link it to the exact registered run artifact and locked metric. |
| "The critic concern is probably minor." | Preserve its recorded severity and resolution status. |
| "The citation looks correct from the title." | Reopen the primary source and verify support for the exact claim. |
| "The final report is done, so I can publish it." | Stop at the explicit publication gate. |

## Red Flags

- Narrative prose is drafted before the claim-evidence table.
- Numbers lack artifact IDs, units, uncertainty, or comparison context.
- Negative result, failed, contradictory, or inconclusive evidence disappears.
- The claim strength exceeds the evaluation and critique evidence.
- Citations are unverified, secondary-only, or do not support the sentence.
- Limitations are generic and omit decision impact.
- The worker changes research state, the ledger, verdicts, or publishes output.

## Verification

Before reporting `completed`, verify:

- only registered, accepted artifacts were treated as supporting evidence;
- the claim-evidence table precedes and controls the narrative;
- every number traces to an immutable artifact and locked definition;
- every material negative result, failure, contradiction, and inconclusive
  finding is represented;
- claim strength matches independent evaluation, robustness, critique, and
  limitations;
- citation verification covers identity, publication status, locator, and
  sentence-level support;
- generalization, resource, dataset, statistical, and confound limits are
  explicit;
- task-local `report.md` contains the table, verification, and final narrative;
- the publication gate remains untouched; and
- no global state or model-call ledger was modified.
