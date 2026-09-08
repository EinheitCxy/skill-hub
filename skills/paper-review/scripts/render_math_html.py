#!/usr/bin/env python3
"""Render a paper-review Markdown report with fenced math blocks to MathML HTML."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


class RenderError(Exception):
    """Raised when a report cannot be converted into readable MathML HTML."""


def run_pandoc(arguments, source):
    result = subprocess.run(
        ["pandoc", *arguments],
        input=source,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RenderError(result.stderr.strip() or "Pandoc conversion failed.")
    return result.stdout


def extract_math_blocks(markdown):
    lines = markdown.splitlines(keepends=True)
    output = []
    formulas = {}
    block_lines = None

    for line in lines:
        marker = line.strip()
        if block_lines is None:
            if marker == "```math":
                block_lines = []
            else:
                output.append(line)
            continue

        if marker == "```":
            formula = "".join(block_lines).strip()
            if not formula:
                raise RenderError("Empty fenced math block.")
            formula_id = f"formula-{len(formulas) + 1:03d}"
            formulas[formula_id] = formula
            output.append(f"\nPAPER_REVIEW_{formula_id}\n\n")
            block_lines = None
        elif marker.startswith("```"):
            raise RenderError("A math block must close with a plain ``` fence.")
        else:
            block_lines.append(line)

    if block_lines is not None:
        raise RenderError("Unclosed fenced math block.")
    return "".join(output), formulas


def paragraph_text(inlines):
    text = []
    for inline in inlines:
        if inline.get("t") == "Str":
            text.append(inline["c"])
        elif inline.get("t") in {"Space", "SoftBreak", "LineBreak"}:
            text.append(" ")
    return "".join(text).strip()


def inject_math_nodes(node, formulas):
    if isinstance(node, list):
        return [inject_math_nodes(item, formulas) for item in node]
    if not isinstance(node, dict):
        return node

    if node.get("t") == "Para":
        token = paragraph_text(node["c"])
        matched = re.fullmatch(r"PAPER_REVIEW_(formula-\d{3})", token)
        if matched and matched.group(1) in formulas:
            formula_id = matched.group(1)
            return {
                "t": "Div",
                "c": [
                    [formula_id, ["math-block"], [["data-source-id", formula_id]]],
                    [
                        {
                            "t": "Para",
                            "c": [
                                {
                                    "t": "Math",
                                    "c": [{"t": "DisplayMath"}, formulas[formula_id]],
                                }
                            ],
                        }
                    ],
                ],
            }

    return {key: inject_math_nodes(value, formulas) for key, value in node.items()}


def add_styles(html):
    styles = """
<style>
body { max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1f2937; font: 16px/1.65 system-ui, sans-serif; }
table, pre { display: block; max-width: 100%; overflow-x: auto; }
.math-block { max-width: 100%; overflow-x: auto; margin: 1rem 0; padding: .25rem 0; }
.math-block math { display: block; min-width: max-content; }
</style>
"""
    return html.replace("</head>", f"{styles}</head>", 1)


def render(markdown_path, html_path):
    markdown = markdown_path.read_text(encoding="utf-8")
    prepared_markdown, formulas = extract_math_blocks(markdown)
    ast = json.loads(run_pandoc(["--from=markdown", "--to=json"], prepared_markdown))
    ast = inject_math_nodes(ast, formulas)
    html = run_pandoc(
        ["--from=json", "--to=html5", "--standalone", "--mathml"],
        json.dumps(ast),
    )
    if html.count("<math") < len(formulas):
        raise RenderError("Pandoc did not render every fenced math block as MathML.")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(add_styles(html), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path, help="Source Markdown report")
    parser.add_argument("html", type=Path, help="Output standalone HTML report")
    args = parser.parse_args()

    try:
        render(args.markdown, args.html)
    except (OSError, RenderError, json.JSONDecodeError) as error:
        print(f"render_math_html: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
