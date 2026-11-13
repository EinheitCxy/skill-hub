---
name: research-figure-creator
description: "Design or build evidence-preserving figures for experiment results. Use for ablation, benchmark, error-analysis, trajectory, or statistical comparison figures; not decorative images or figures with invented values."
---

# Research Figure Creator

## Start With the Claim

State the scientific question, the decision the figure should support, and the result it may not overclaim. Define the statistical unit, conditions, control, aggregation, uncertainty display, axis quantity and unit before choosing a chart.

Record two separate dimensions for every input (they are not mutually exclusive categories):

- **Origin:** measured data from a named source, or a synthetic illustration visibly labeled as such. A simulation result may be a real measurement in a simulated environment; it is not automatically a synthetic illustration or a physical-world experiment.
- **Evaluation mode:** offline analysis of saved data or online execution in a live/closed-loop system. Specify simulated versus physical environment, or mark this dimension not applicable. Offline statistics from saved online rollouts remain evidence of that online protocol, not a new online run.

Do not invent, interpolate, or cosmetically adjust measured values to make a figure look better.

## Build the Figure Contract

1. Map each panel to one claim, a source table or file, and a statistical unit.
2. Choose controls and aggregation that preserve the experimental comparison; retain sample counts and uncertainty where they affect interpretation.
3. Name axes with the quantity, direction, and unit. Keep task success, trajectory statistics, contact/grasp measures, and classifier labels distinct unless their relationship is measured and stated.
4. Write the caption and nearby body text from the same figure contract. They must agree on conditions, sample count, error representation, and what the result establishes.
5. Preserve a regeneration entrypoint: source-data location, transformation or script path, parameters, environment, and output location.

Read [figure-contract.md](references/figure-contract.md) when defining panels, statistics, captions, or the regeneration record. Read [review-and-rendering.md](references/review-and-rendering.md) before delivery when layout, accessibility, or export quality matters.

## Select Tools Only When Needed

Check which optional visualization skills and libraries are actually available before choosing one. Use one only when it fits the requested output: `scientific-figure-making` or `scientific-visualization` for publication figures, `matplotlib` for precise static control, and `plotly` for requested interactive inspection. Read the selected skill before use. If an optional skill is absent, use an appropriate available plotting library directly (for example Matplotlib for static figures or Plotly for requested interaction); do not fabricate a skill invocation or treat a missing optional skill as a blocker. If no suitable tool can run, provide the plotting specification and identify the rendering limit rather than claiming a completed figure. Do not load all skills or create a plotting tutorial.

Apply the same checks on every path: verify data provenance, statistical units and aggregation, axes and legends, caption consistency, exported files, and actual rendered output. A successful export alone is not visual verification. Respect authorization for installing dependencies or running new experiments.

## Evidence Boundary

- A chart cannot establish a claim beyond the data and protocol it shows.
- Do not call synthetic or illustrative panels experimental evidence.
- Do not merge incomparable metrics, hardware, data splits, or evaluation conditions into one visual ranking.
- Preserve raw-data and transformation provenance so another person can regenerate or audit the figure.

## Default Handoff

Return the scientific question, panel-to-claim mapping, data classification and source, aggregation and controls, figure/caption/body copy, regeneration entrypoint, and any unresolved evidence limit. Do not silently start new experiments to fill a figure; when the user requests them, run the authorized protocol before plotting and retain the actual results.
