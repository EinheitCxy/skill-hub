#!/usr/bin/env python3

import argparse
import json
import re
import sys
from pathlib import Path


H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
BULLET_RE = re.compile(r"^-\s+(.+?)\s*$", re.MULTILINE)
UNRESOLVED_RE = re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE)
TEMPLATE_PLACEHOLDER_RE = re.compile(
    "|".join(
        re.escape(phrase)
        for phrase in (
            "One sentence naming intervention, setting, decision objective, and distinguishing constraint.",
            "Atomic claim | Mechanism | A-level source anchor | Testable",
            "Name files and exact conclusions to update after approval.",
        )
    ),
    re.IGNORECASE,
)

REQUIRED_SECTIONS = [
    "Primary thesis",
    "Fallback",
    "Claim ledger",
    "Overlap matrix",
    "Rejected directions",
    "Evidence boundary",
    "Decision gates",
    "Failure responses",
]


def issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def sections(text: str) -> dict[str, str]:
    matches = list(H2_RE.finditer(text))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[match.group(1).strip().casefold()] = text[start:end]
    return result


def data_rows(section: str) -> int:
    rows = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]
    nonseparators = []
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        nonseparators.append(row)
    return max(0, len(nonseparators) - 1)


def non_none_bullets(section: str) -> list[str]:
    return [
        bullet
        for bullet in BULLET_RE.findall(section)
        if bullet.strip().casefold() not in {"none", "无", "不设"}
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a research direction decision packet.")
    parser.add_argument("packet", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    errors: list[dict[str, str]] = []
    if not args.packet.is_file():
        errors.append(issue("input.packet", f"Direction packet does not exist: {args.packet}"))
        report = {"status": "fail", "errors": errors, "checks": {}}
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 1

    text = args.packet.read_text(encoding="utf-8")
    section_map = sections(text)
    for required in REQUIRED_SECTIONS:
        if required.casefold() not in section_map:
            errors.append(issue("direction.required_section", f"Missing H2 section: {required}"))

    primary = non_none_bullets(section_map.get("primary thesis", ""))
    fallback = non_none_bullets(section_map.get("fallback", ""))
    if len(primary) != 1:
        errors.append(
            issue("direction.primary_count", f"Expected one primary thesis bullet, found {len(primary)}.")
        )
    if len(fallback) > 1:
        errors.append(
            issue("direction.fallback_count", f"Expected at most one fallback bullet, found {len(fallback)}.")
        )

    table_requirements = {
        "claim ledger": "direction.claim_ledger",
        "overlap matrix": "direction.overlap_matrix",
        "rejected directions": "direction.rejection_table",
        "decision gates": "direction.gate_table",
    }
    for heading, code in table_requirements.items():
        if data_rows(section_map.get(heading, "")) < 1:
            errors.append(issue(code, f"Section requires at least one Markdown table data row: {heading}"))

    boundary = section_map.get("evidence boundary", "")
    if not re.search(r"Evidence\s+level\s*:\s*[ABC]\b", boundary, re.IGNORECASE):
        errors.append(
            issue(
                "direction.evidence_level",
                "Evidence boundary must name at least one A, B, or C evidence level.",
            )
        )

    failure = section_map.get("failure responses", "")
    if not re.search(r"^-\s+Stop\s+condition\s*:", failure, re.MULTILINE | re.IGNORECASE):
        errors.append(
            issue(
                "direction.stop_condition",
                "Failure responses must contain a '- Stop condition:' bullet.",
            )
        )

    if UNRESOLVED_RE.search(text):
        errors.append(issue("direction.unresolved_marker", "Found TODO, TBD, or FIXME."))

    if TEMPLATE_PLACEHOLDER_RE.search(text):
        errors.append(
            issue(
                "direction.template_placeholder",
                "Found an instructional sentence from the decision-packet template; replace it with evidence-backed content.",
            )
        )

    h1_count = len(re.findall(r"^#\s+\S", text, re.MULTILINE))
    if h1_count != 1:
        errors.append(issue("direction.h1_count", f"Expected exactly one H1, found {h1_count}."))

    report = {
        "status": "fail" if errors else "pass",
        "checks": {
            "primary_count": len(primary),
            "fallback_count": len(fallback),
            "sections": sorted(section_map),
        },
        "errors": errors,
    }
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
