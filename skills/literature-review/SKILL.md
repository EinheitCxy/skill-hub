---
name: literature-review
description: "Compare or review a bounded research corpus with traceable evidence and context-qualified conclusions. Use for multi-paper comparison, literature review, benchmark synthesis, or evidence refresh; not a one-paper explanation or final research-direction choice."
---

# Literature Review

## Choose the Depth First

State the question, corpus boundary, requested deliverable, and whether files may be edited. Then choose one depth:

- **Focused comparison (default):** for a small named set or a narrow question. Verify the material claims, compare only decision-relevant conditions, give a concise conclusion, and identify the next missing evidence.
- **Formal review:** for a literature review, canonical report, corpus refresh, or deliverable intended for reuse. Freeze and audit the corpus, create evidence cards, maintain a ledger and bibliography, synthesize, and validate the resulting artifacts.

Do not edit files for an analysis-only request. Do not add external sources to a bounded corpus without labeling them.

## Focused Comparison

1. Identify each source and its available evidence level.
2. Extract only claims needed for the question, with a source anchor and the conditions that determine whether they compare.
3. Compare the mechanism, task, baselines, metric, and relevant runtime or quality constraints.
4. State verified findings, scoped inferences, unknowns, and the cheapest next evidence check.

Read [evidence-contract.md](references/evidence-contract.md) before extracting claims and [synthesis-and-comparison.md](references/synthesis-and-comparison.md) before making a cross-source judgment.

## Formal Review

Use the focused-comparison rules, then read [corpus-workflow.md](references/corpus-workflow.md) to freeze identities, coverage, inclusion decisions, and deduplication. Create per-item evidence cards and a thematic synthesis; retain negative results and conditions with every reported metric. For a canonical document, read [deliverable-validation.md](references/deliverable-validation.md) and run the applicable artifact checks:

```bash
uv run --with bibtexparser python <skill-dir>/scripts/validate_research_artifacts.py \
  --markdown <report.md> \
  --bibtex <references.bib> \
  --require-mermaid-accessibility
```

Validate source structure and visible rendering separately when formulas, diagrams, wide tables, or standalone HTML are present. Report structural validation, visual inspection, and unverified limits as separate states.

## Evidence Boundaries

- Keep every material claim tied to an identity, source anchor, evidence level, and experimental context.
- Do not turn abstract-level evidence, repository presence, or search absence into stronger proof.
- Separate verified facts, inferences, recommendations, and unknowns.
- Do not call a search exhaustive or a result novel unless the method supports that scope.

Read [subskills-and-tools.md](references/subskills-and-tools.md) only when a step lacks a specific capability such as PDF inspection, citation management, scholarly retrieval, or rendering. Select the smallest applicable set; do not load or invoke tools merely because they appear in the table.
