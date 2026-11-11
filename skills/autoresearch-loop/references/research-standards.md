# ML Research Standards

## Purpose

These standards define what counts as valid progress in an applied-ML research
project. They apply to the Coordinator and every worker skill. A fluent report,
working script, lower training loss, or attractive figure is not evidence by
itself.

The Research Plan (`research-charter`), Change Scope (`Modification Envelope`),
Resource Budget (`Budget Envelope`), dataset contract, and Evaluation Contract
(`EvaluationSpec`) are durable project inputs. Conversation context
cannot amend them. A legacy Resource Budget schema with `cost_usd` or
`model_tokens` migrates to v1 exactly once via a recorded receipt and is not
re-triggered per new task.

## Literature and prior art

The first networked literature pass:

1. screens for direct relevance to the research question;
2. prefers accepted work verified through official proceedings, a publisher
   page, or a DOI record at a field-appropriate top
   conference or journal;
3. then includes other relevant peer-reviewed work; and
4. retains recent preprints for frontier coverage with an explicit
   `not peer-reviewed` label.

Venue status is evidence metadata, not an automatic quality score. Record
title, authors, year, venue, publication status, verification source, method,
data, metrics, limitations, and implementation relevance. A preprint must not
silently replace an available accepted source for the same claim.

Every cited claim requires citation verification against the source itself.
Search snippets, generated summaries, and remembered bibliographic details are
discovery aids, not verified evidence.

## Hypotheses and candidate scope

Every hypothesis is falsifiable and declares:

- the proposed mechanism;
- one primary scientific change;
- expected observation and effect direction;
- falsification condition;
- comparison target;
- required evidence and decisive ablation; and
- an approved budget slice.

One official candidate tests one primary change unless the Research Charter
explicitly approves a factorial experiment. Debug work repairs implementation;
it does not create a new scientific claim.

## Data integrity

Before official execution, lock dataset identity, versions, split IDs, sample
policy, labels, preprocessing, augmentation boundary, seeds, and evaluator.
Run a data leakage audit covering:

- train/validation/test overlap;
- duplicate or near-duplicate entities;
- temporal, subject, scene, and group leakage;
- preprocessing fitted on evaluation data;
- label or metadata shortcuts; and
- external pretraining or retrieval contamination relevant to the claim.

Changing locked data, split, evaluator, metric, seed policy, sample count, or
regression contract requires a newly approved EvaluationSpec. The agent cannot
reinterpret the split after seeing results. On User request, remove an unwanted
holdout, dataset, or candidate from the active contract, tasks, and reports,
and do not keep restoring state around it.

## Comparisons and run classes

Initial Baseline is the reproducible starting comparison and remains immutable.
Each evaluation cycle binds the concrete Current Best and its report through
one immutable checkpoint. New candidates compare against that captured Current
Best, never against an unverified Provisional Best.

Run classes remain separate:

- smoke: proves wiring and is excluded from official evidence;
- debug: diagnoses or repairs execution;
- official: follows the locked EvaluationSpec and enters comparison;
- robustness: performs required multi-seed, replication, slice, or stress
  checks.

Official comparisons keep data, evaluator, metric implementation, seed/sample
policy, and resource measurement comparable. Failed comparability is a hard
gate, not a caveat added after ranking.

## Evaluation and robustness

Evaluation proceeds through execution validity, contract comparability, and
evidence completeness before metrics. Passing reports include:

- primary and secondary metrics with direction and uncertainty;
- sufficient samples and the locked seed policy;
- applied-ML slices and an error taxonomy;
- representative qualitative successes and failures;
- latency, memory, compute, storage, and cost where relevant;
- confounds and alternative explanations;
- reproducibility evidence; and
- the required robustness contract.

No universal metric names or thresholds are embedded in the agent. Each
project's EvaluationSpec owns its metrics and unacceptable regression rules.
A leading result becomes Current Best only after its declared robustness
artifacts pass.

## Evidence retention and interpretation

Every official, debug, failed, negative result, and inconclusive result remains
in the candidate journal and ArtifactStore. Never delete an inconvenient run
or overwrite its report. Corrections create new artifact versions.

Use these interpretations:

- `keep`: evidence supports continued promotion checks;
- `discard`: tested direction is not competitive or violates the contract;
- `inconclusive`: evidence is confounded, incomplete, or too noisy;
- `debuggable`: scientific evaluation waits for a separate debug child;
- `needs_user`: the last resort for a genuine user-only decision; see
  `prompt-contracts.md` for the full escalation criteria and the
  recoverable-state rule; and
- `failed`: execution or evidence cannot support the claim.

Absence of improvement is a negative result, not permission to invent a
mechanism. Generated prose is never evidence. Producing a paper is not itself
a successful research outcome.
Grade source availability A (rich) / B (partial) / C (scarce); C degrades to
first-principles mode with confidence labels (high/medium/low) and
leave-blank-not-fabricate, marking missing data `data missing` never invented
(e.g., ScholarAPI `not_selected`, missing paper details).

## Claim and citation discipline

Every final claim links to at least one registered run artifact, evidence card,
evaluation report, or verified citation. A claim-evidence table precedes
narrative synthesis and distinguishes:

- directly measured evidence;
- supported inference;
- external prior art;
- negative or disconfirming evidence; and
- unresolved or inconclusive evidence.

Do not claim causality from correlation, generalization beyond evaluated
slices, novelty without a literature search, statistical significance without
the declared test, or publication status without an authoritative source.

## Stop and escalation

Pause for User when the requested work changes change scope, resource budget,
dependencies, model family, sensitive-data access, locked EvaluationSpec, or
publication authority. A routine Coordinator gate records its verified receipt
and continues without a question; one recorded approval is a durable gate (see
`autoresearch-loop/SKILL.md`). Sensitive, irreversible, or external actions
pause as `needs_review` (reviewer may edit the input then approve), not
`needs_user`. Autonomous search stops on success, exhausted
approved budget, convergence without material gain, no independent candidate, a
pending user gate, or explicit user stop (see `search-policy.md`).

## Verification checklist

- Confirm a falsifiable hypothesis and one primary change.
- Verify literature relevance, publication status, and every used citation.
- Lock data, split, Initial Baseline, evaluator, metrics, seeds, and samples.
- Complete the data leakage audit before official results.
- Keep smoke, debug, official, and robustness evidence separate.
- Compare against the cycle-captured Current Best.
- Report uncertainty, slices, errors, resources, and confounds.
- Preserve failed, negative, rejected, and inconclusive evidence.
- Link every claim to registered evidence and label unsupported inference.
