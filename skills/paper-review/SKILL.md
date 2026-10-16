---
name: paper-review
description: "Use when an already-provided academic paper needs a Chinese critical explanation of its problem, method, evidence, and limitations. Use the complete-report and HTML-rendering workflow only when the user explicitly requests a formal document deliverable."
---

# Paper Review

## Purpose and scope

Explain the supplied paper in Chinese for technical readers through its problem, method, evidence, and limitations. Connect the bottleneck, mechanism, experimental conditions, and limits of the conclusions; avoid paragraph-by-paragraph summaries.

Do not search for, download, or complete missing paper materials. State when the source does not specify a mechanism, value, implementation detail, or causal link.

## Default response

Unless a full report or document is requested, cover four parts at the appropriate depth:

1. **Problem:** task, constraints, and weaknesses of prior methods.
2. **Method:** assumptions, component roles, and data flow. Explain core algorithms through motivation, inputs/outputs, training, inference, and cost.
3. **Evidence:** findings with datasets, baselines, metrics, budgets, and protocols. Identify which hypothesis each ablation supports.
4. **Limitations:** documented failures, comparison boundaries, missing controls, and unproven claims.

Label each key judgment as direct evidence, reasonable inference, or unproven, using Chinese labels. Cite pages, sections, figures, tables, algorithms, or equations for direct evidence; state assumptions for inferences and missing evidence for unknowns.

## Method and comparison rules

- Explain only supported details: inputs, outputs, notation, known shapes, module roles, training/inference paths, objectives, and costs. Do not infer control flow, caching, optimizers, or sampling order from a diagram.
- Provide pseudocode or quantitative complexity only when the source supports it; otherwise state the uncertainty.
- Align problem settings, data, protocols, controls, compute/memory/annotation costs, scope, and failure modes. When conditions differ, report insufficient evidence rather than rank methods.

## Optional extensions

- For an explicitly requested full report, review, seven-chapter structure, or formal document, read `references/complete-report.md`.
- For document deliverables with substantial math or uncertain renderer support, read that reference and generate HTML to verify equations. Follow it for explicit HTML requests too. Ordinary explanations do not create files automatically.
- Suggest future directions only when requested. Each needs a gap, testable hypothesis, and validation path; no minimum count.

## Equations

Use inline math for short expressions and fenced `math` blocks for display equations, subject to the user's presentation preferences. Include only source-supported formulas. HTML formatting and checks are in `references/complete-report.md`.

## Before delivery

- Connect the problem, method, evidence, and limitations.
- Label evidence status and preserve comparison boundaries.
- Keep algorithms, objectives, pseudocode, and ablation claims within the source.
- When HTML is required, verify visible, unclipped equations and ensure long equations do not widen the page.
