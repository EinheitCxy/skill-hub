# Corpus Workflow

## Boundary Types

### Collection bounded

Use the live collection as the source of truth. Record collection paths, membership counts, direct versus recursive scope, item keys, duplicate memberships, and attachment availability.

### Folder bounded

Inventory supported files, identify generated copies, select stable local identities, and distinguish canonical sources from exports or historical snapshots.

### Bibliography bounded

Parse the bibliography structurally. Resolve duplicate keys, duplicate DOI values, missing title/year, attachment-only paths, and entries not cited by the target deliverable.

### Search bounded

Record:

- databases and tools;
- exact queries or query families;
- date range and retrieval date;
- screening rules;
- citation chaining;
- full-text access;
- stopping rule.

Describe conclusions as scoped to the searched space.

## Inventory Schema

Use a table or structured file with:

| Field | Meaning |
| --- | --- |
| `identity` | Stable audit identity |
| `title` | Display title |
| `version` | Paper, source, or repository version |
| `memberships` | Collections or folders containing the item |
| `source_type` | PDF, TeX, code, metadata, abstract, dataset |
| `full_text` | Available, partial, unavailable |
| `included` | Inclusion decision |
| `reason` | Inclusion or exclusion reason |
| `evidence_level` | Highest currently verified level |

## Deduplication

1. Deduplicate exact stable identities first.
2. Flag DOI/title/version conflicts for manual review.
3. Preserve multiple collection memberships while counting one unique item.
4. Do not merge a survey, conference paper, extended journal version, and code repository without recording the relationship.

## Refresh Behavior

When the live corpus changes:

1. snapshot the new inventory;
2. compare stable identities;
3. identify additions, removals, and version changes;
4. update only affected evidence cards;
5. rerun citation and deliverable validation;
6. report the delta rather than silently replacing counts.
