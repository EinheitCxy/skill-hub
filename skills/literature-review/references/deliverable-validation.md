# Deliverable Validation

## Markdown

- exactly one H1;
- no heading-level jumps;
- local links resolve;
- no unresolved markers;
- project language and terminology remain consistent;
- generated and historical artifacts are labeled.

## Mathematics

- use the project-required source syntax;
- balance delimiters and environments;
- validate source structure and target-renderer behavior separately;
- inspect non-zero dimensions and clipping;
- constrain wide formulas to their own scroll container.

## Mermaid

- retain editable Mermaid as source;
- include `accTitle` and `accDescr`;
- render every canonical block;
- inspect labels, edges, contrast, and clipping.

## BibTeX and Citations

- parse structurally;
- require unique keys;
- require title and year unless the project explicitly allows otherwise;
- ensure every cited key exists;
- ensure collection-specific included items appear in the bibliography;
- identify uncited bibliography entries when full coverage is required.

## Standalone HTML

- citations resolved;
- all images have non-zero dimensions;
- formulas are visible;
- no duplicate SVG IDs;
- no horizontal page overflow at desktop or mobile widths;
- wide content scrolls locally.

## Completion Boundary

Report three states separately:

- **structurally validated**;
- **rendered and visually inspected**;
- **not verified**, with the exact blocker.

Do not promote a weaker state to a stronger one.
