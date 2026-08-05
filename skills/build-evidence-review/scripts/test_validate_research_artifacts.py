import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("validate_research_artifacts.py")


class ResearchArtifactValidatorTests(unittest.TestCase):
    def run_validator(self, markdown: Path, bibtex: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--markdown",
                str(markdown),
                "--bibtex",
                str(bibtex),
                "--require-mermaid-accessibility",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_accepts_auditable_research_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.md").write_text("# Source Note\n", encoding="utf-8")
            (root / "todo.md").write_text("# Execution Backlog\n", encoding="utf-8")
            markdown = root / "review.md"
            markdown.write_text(
                """# Review

## Evidence

The verified result is cited here [@smith2024]. See the [source note](note.md)
and the [execution backlog](todo.md).

```math
\\begin{aligned}
x &= 1
\\end{aligned}
```

```mermaid
flowchart LR
    accTitle: Evidence flow
    accDescr: Sources flow into an audited claim.
    A[Source] --> B[Claim]
```
""",
                encoding="utf-8",
            )
            bibtex = root / "references.bib"
            bibtex.write_text(
                """@article{smith2024,
  title = {Auditable Evidence},
  author = {Smith, Alex},
  year = {2024}
}
""",
                encoding="utf-8",
            )

            result = self.run_validator(markdown, bibtex)

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["errors"], [])

    def test_rejects_structural_citation_and_rendering_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            markdown = root / "broken.md"
            markdown.write_text(
                """# First
# Second

### Skipped heading

TODO: finish this claim [@missing2025].

[missing file](absent.md)

$$x = 1$$

```mermaid
flowchart LR
    A --> B
```
""",
                encoding="utf-8",
            )
            bibtex = root / "references.bib"
            bibtex.write_text(
                """@article{broken2024,
  title = {Missing Year}
}
""",
                encoding="utf-8",
            )

            result = self.run_validator(markdown, bibtex)

            self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            codes = {error["code"] for error in report["errors"]}
            self.assertTrue(
                {
                    "markdown.h1_count",
                    "markdown.heading_jump",
                    "markdown.local_link",
                    "markdown.unresolved_marker",
                    "markdown.display_math_delimiter",
                    "markdown.mermaid_accessibility",
                    "bibtex.required_field",
                    "citation.missing_key",
                }.issubset(codes),
                codes,
            )

    def test_rejects_duplicate_bibtex_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            markdown = root / "review.md"
            markdown.write_text("# Review\n", encoding="utf-8")
            bibtex = root / "references.bib"
            bibtex.write_text(
                """@article{duplicate2024,
  title = {First Entry},
  year = {2024}
}

@article{duplicate2024,
  title = {Second Entry},
  year = {2024}
}
""",
                encoding="utf-8",
            )

            result = self.run_validator(markdown, bibtex)

            self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            codes = {error["code"] for error in report["errors"]}
            self.assertIn("bibtex.duplicate_key", codes)


if __name__ == "__main__":
    unittest.main()
