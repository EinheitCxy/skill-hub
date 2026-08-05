---
name: build-evidence-review
description: Build an auditable literature or technical review from a defined Zotero, folder, bibliography, code, or scholarly-search corpus, with evidence cards, citations, benchmark conditions, synthesis, and rendered-output validation. Use when the user asks for a literature review, paper-by-paper synthesis, benchmark comparison, evidence refresh, or canonical research report. Do not use for choosing a final research thesis, a simple one-paper explanation, or ordinary repository code review.
---

# Build an Evidence-Based Research Review

## Objective

Turn a declared corpus into an auditable research deliverable. Preserve the chain from source identity to extracted evidence, synthesis, citations, and validation. Separate verified facts from abstract-level leads and inference.

## Load References Selectively

- Read [evidence-contract.md](references/evidence-contract.md) before extracting claims or assigning evidence levels.
- Read [corpus-workflow.md](references/corpus-workflow.md) when building or refreshing a Zotero, folder, bibliography, or search-defined corpus.
- Read [synthesis-and-comparison.md](references/synthesis-and-comparison.md) before comparing papers, architectures, benchmarks, or acceleration results.
- Read [deliverable-validation.md](references/deliverable-validation.md) before editing a canonical report or claiming completion.
- Read [subskills-and-tools.md](references/subskills-and-tools.md) when selecting scholarly search, PDF, citation, writing, or rendering capabilities.

## Workflow

### 1. Establish the Contract

1. Restate the research question, requested depth, corpus boundary, deliverables, and read/write scope.
2. Read applicable project instructions and canonical entrypoints before searching.
3. Classify the workspace as a document collection, code repository, or mixed workspace.
4. Identify facts that may have changed and refresh them through current primary sources.
5. For substantial work, maintain a durable plan, findings log, and progress record when the workspace provides them.

Do not edit files during an analysis-only request. Do not broaden a collection-bounded review without labeling the external additions.

### 2. Freeze and Audit the Corpus

1. Select a stable item identity:
   - Zotero item key for Zotero collections;
   - DOI when verified;
   - repository commit plus path for code evidence;
   - canonical URL or local content hash otherwise.
2. Inventory collection memberships, local attachments, source packages, and missing full text.
3. Deduplicate by stable identity, not title similarity alone.
4. Record inclusion, exclusion, version, retrieval date, and evidence availability.
5. Preserve the frozen inventory so later corpus changes are detectable.

### 3. Extract Evidence Cards

For every included item, capture:

- identity and provenance;
- research problem and scope;
- architecture or method;
- training and inference path;
- claimed mechanism;
- datasets, baselines, hardware, precision, and runtime conditions;
- metrics and quality constraints;
- limitations, negative results, and implementation status;
- evidence level and exact source anchor.

Do not upgrade abstract or metadata evidence because a claim appears plausible.

### 4. Synthesize

1. Build comparison axes before writing narrative conclusions.
2. Separate metrics that measure different objects.
3. Keep every number attached to its experimental conditions.
4. Compare mechanisms, assumptions, error propagation, systems constraints, and downstream consequences.
5. Identify agreements, contradictions, missing comparisons, and evidence gaps.
6. Distinguish:
   - verified cross-paper finding;
   - scoped inference;
   - recommendation;
   - unverified lead.

For paper-by-paper requests, provide both evidence cards and thematic synthesis. Do not replace either with an abstract collage.

### 5. Build the Deliverable

1. Use the workspace language and heading hierarchy.
2. Keep one H1 in each Markdown document.
3. Use stable citation keys, DOI values, or primary URLs; never export session-local citation markers.
4. Update the bibliography and corpus ledger with the report.
5. Treat generated HTML as derived when a Markdown source exists.
6. Preserve canonical and historical boundaries.
7. Update progress and durable findings only with conclusions supported by the new evidence.

### 6. Validate Before Completion

Run checks proportional to the artifacts:

```bash
uv run --with bibtexparser python scripts/validate_research_artifacts.py \
  --markdown <report.md> \
  --bibtex <references.bib> \
  --require-mermaid-accessibility
```

Also validate actual rendering when formulas, diagrams, wide tables, or standalone HTML are present. A successful converter exit code is not visual proof.

Remove temporary exports, source archives, browser profiles, render intermediates, fixtures, and validation scripts that are not canonical deliverables.

## Completion Report

Lead with the verified outcome. State:

- frozen corpus size and deduplication rule;
- full-text versus abstract-only coverage;
- files created or changed;
- validation performed and actual results;
- unresolved evidence or rendering limits;
- recommended next evidence gate.

Never claim a search is exhaustive or novelty is guaranteed unless the method supports that claim.
