#!/usr/bin/env python3

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import bibtexparser


HEADING_RE = re.compile(r"^(#{1,6})\s+\S", re.MULTILINE)
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
LINK_TARGET_RE = re.compile(r"(!?\[[^\]]*\])\([^)]+\)")
UNRESOLVED_RE = re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE)
FENCE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
MERMAID_RE = re.compile(r"```mermaid[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
CITATION_BLOCK_RE = re.compile(r"\[([^\]]*@[^\]]+)\]")
CITATION_KEY_RE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9_.:/+-]*)")


def issue(code: str, message: str, path: Path | None = None) -> dict[str, str]:
    item = {"code": code, "message": message}
    if path is not None:
        item["path"] = str(path)
    return item


def prose_without_fences(text: str) -> str:
    return FENCE_RE.sub("", text)


def validate_markdown(
    path: Path,
    require_mermaid_accessibility: bool,
) -> tuple[list[dict[str, str]], list[dict[str, str]], set[str], dict[str, int]]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    text = path.read_text(encoding="utf-8")
    prose = prose_without_fences(text)
    headings = [(len(match.group(1)), match.group(0)) for match in HEADING_RE.finditer(prose)]
    h1_count = sum(level == 1 for level, _ in headings)
    if h1_count != 1:
        errors.append(issue("markdown.h1_count", f"Expected exactly one H1, found {h1_count}.", path))

    previous_level: int | None = None
    for level, heading in headings:
        if previous_level is not None and level > previous_level + 1:
            errors.append(
                issue(
                    "markdown.heading_jump",
                    f"Heading level jumps from H{previous_level} to H{level}: {heading}",
                    path,
                )
            )
        previous_level = level

    for raw_target in LINK_RE.findall(text):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
        if (
            not target
            or target.startswith(("#", "http://", "https://", "mailto:", "data:"))
        ):
            continue
        decoded = unquote(target.split("#", 1)[0])
        resolved = (path.parent / decoded).resolve()
        if not resolved.exists():
            errors.append(
                issue(
                    "markdown.local_link",
                    f"Local link does not resolve: {target}",
                    path,
                )
            )

    marker_text = LINK_TARGET_RE.sub(r"\1", prose)
    if UNRESOLVED_RE.search(marker_text):
        errors.append(issue("markdown.unresolved_marker", "Found TODO, TBD, or FIXME.", path))

    if "$$" in prose or r"\[" in prose or r"\]" in prose:
        errors.append(
            issue(
                "markdown.display_math_delimiter",
                "Found forbidden display-math delimiter outside a fenced block.",
                path,
            )
        )

    mermaid_blocks = MERMAID_RE.findall(text)
    if require_mermaid_accessibility:
        for index, block in enumerate(mermaid_blocks, start=1):
            if "accTitle:" not in block or "accDescr:" not in block:
                errors.append(
                    issue(
                        "markdown.mermaid_accessibility",
                        f"Mermaid block {index} lacks accTitle or accDescr.",
                        path,
                    )
                )

    citation_keys: set[str] = set()
    for block in CITATION_BLOCK_RE.findall(prose):
        citation_keys.update(CITATION_KEY_RE.findall(block))

    stats = {
        "h1_count": h1_count,
        "heading_count": len(headings),
        "mermaid_blocks": len(mermaid_blocks),
        "citation_keys": len(citation_keys),
    }
    return errors, warnings, citation_keys, stats


def validate_bibtex(path: Path) -> tuple[list[dict[str, str]], set[str], dict[str, int]]:
    errors: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as handle:
        database = bibtexparser.load(handle)
    entries = database.entries
    keys = [entry.get("ID", "").strip() for entry in entries]
    for key, count in Counter(keys).items():
        if not key:
            errors.append(issue("bibtex.missing_key", "BibTeX entry lacks an ID.", path))
        elif count > 1:
            errors.append(
                issue("bibtex.duplicate_key", f"BibTeX key appears {count} times: {key}", path)
            )
    for entry in entries:
        key = entry.get("ID", "<missing>")
        for field in ("title", "year"):
            if not entry.get(field, "").strip():
                errors.append(
                    issue(
                        "bibtex.required_field",
                        f"BibTeX entry {key} lacks required field {field}.",
                        path,
                    )
                )
    return errors, {key for key in keys if key}, {"entries": len(entries), "unique_keys": len(set(keys))}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate evidence-first research artifacts.")
    parser.add_argument("--markdown", action="append", type=Path, required=True)
    parser.add_argument("--bibtex", type=Path)
    parser.add_argument("--require-mermaid-accessibility", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    citations: set[str] = set()
    markdown_stats: dict[str, dict[str, int]] = {}

    for markdown in args.markdown:
        if not markdown.is_file():
            errors.append(issue("input.markdown", "Markdown file does not exist.", markdown))
            continue
        found_errors, found_warnings, found_citations, stats = validate_markdown(
            markdown,
            args.require_mermaid_accessibility,
        )
        errors.extend(found_errors)
        warnings.extend(found_warnings)
        citations.update(found_citations)
        markdown_stats[str(markdown)] = stats

    bibtex_keys: set[str] = set()
    bibtex_stats: dict[str, int] | None = None
    if args.bibtex:
        if not args.bibtex.is_file():
            errors.append(issue("input.bibtex", "BibTeX file does not exist.", args.bibtex))
        else:
            bib_errors, bibtex_keys, bibtex_stats = validate_bibtex(args.bibtex)
            errors.extend(bib_errors)

    if citations and not args.bibtex:
        errors.append(
            issue(
                "citation.bibliography_missing",
                "Markdown citations were found but no BibTeX file was provided.",
            )
        )
    for key in sorted(citations - bibtex_keys):
        errors.append(issue("citation.missing_key", f"Citation key is absent from BibTeX: {key}"))

    report = {
        "status": "fail" if errors else "pass",
        "checks": {
            "markdown": markdown_stats,
            "bibtex": bibtex_stats,
            "citation_count": len(citations),
        },
        "errors": errors,
        "warnings": warnings,
    }
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
