# Evidence Contract

## Evidence Levels

| Level | Minimum support | Allowed use |
| --- | --- | --- |
| A | Verified full text, primary source code, official filing, dataset, or directly inspected artifact | Core factual claim, method description, baseline design |
| B | Verified metadata plus abstract or official summary | Candidate, scoped overlap warning, retrieval lead |
| C | Secondary source, snippet, citation trail, or unverified mention | Tracking only |

Assign the level claim by claim. A paper can support an architecture statement at A while leaving a deployment claim at B.

## Required Claim Fields

For each material claim record:

- claim text;
- source identity;
- source type and version;
- exact page, section, table, figure, file, line, or stable URL anchor;
- evidence level;
- experimental or contextual conditions;
- interpretation boundary.

## Fact, Inference, and Recommendation

- **Verified fact:** directly supported by the inspected source.
- **Inference:** reasoned synthesis from named facts; label it.
- **Recommendation:** proposed action based on evidence and constraints.
- **Unknown:** evidence not found or not accessible; do not fill the gap.

## Evidence Integrity Rules

1. Never invent DOI, year, author, affiliation, metric, hardware, or implementation status.
2. Never treat an abstract as proof of appendix details or code behavior.
3. Never treat repository presence as proof that the reported experiment is reproducible.
4. Never translate “not found in this search” into “does not exist.”
5. Recheck drift-prone facts such as project status, model versions, benchmarks, prices, policies, and current personnel.
6. Preserve negative results and failed gates; they constrain the synthesis.
7. State when access, rate limits, missing attachments, or rendering failures restrict verification.

## Stable Identity

Prefer, in order:

1. collection-native immutable key;
2. verified DOI;
3. repository commit plus path;
4. arXiv identifier plus version;
5. canonical primary URL;
6. local content hash.

Use titles only as display names, not deduplication identities.
