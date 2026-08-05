import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("validate_direction_packet.py")


class DirectionPacketValidatorTests(unittest.TestCase):
    def run_validator(self, packet: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(packet)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_accepts_one_thesis_one_fallback_and_explicit_stop_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp) / "direction.md"
            packet.write_text(
                """# Direction Decision Packet

## Primary thesis

- Decision-preserving low-fidelity screening under hard deadlines.

## Fallback

- Static-only compression under matched deployment constraints.

## Claim ledger

| Claim | Evidence | Status |
| --- | --- | --- |
| Screening preserves top candidates | A-level full text | Testable |

## Overlap matrix

| Candidate | Mechanism overlap | Objective overlap | Judgment |
| --- | --- | --- | --- |
| Candidate A | Partial | None | Retain |

## Rejected directions

| Direction | Reason | Role |
| --- | --- | --- |
| Generic pruning | Direct overlap | Baseline |

## Evidence boundary

- Evidence level: A for the core mechanism; B for frontier overlap.

## Decision gates

| Gate | Pass condition | Failure response |
| --- | --- | --- |
| G1 | Top-k recall clears the preregistered threshold | Stop the adaptive branch |

## Failure responses

- Stop condition: terminate the adaptive branch when G1 fails.
""",
                encoding="utf-8",
            )

            result = self.run_validator(packet)

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "pass")

    def test_rejects_multiple_theses_fallbacks_and_missing_decision_logic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp) / "broken.md"
            packet.write_text(
                """# Direction Decision Packet

## Primary thesis

- Thesis one.
- Thesis two.

## Fallback

- Fallback one.
- Fallback two.

## Claim ledger

No structured claims.

## Overlap matrix

No comparison.

## Rejected directions

No rejection.

## Evidence boundary

No level is stated.

## Decision gates

No gate table.

## Failure responses

Continue regardless of evidence.
""",
                encoding="utf-8",
            )

            result = self.run_validator(packet)

            self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            codes = {error["code"] for error in report["errors"]}
            self.assertTrue(
                {
                    "direction.primary_count",
                    "direction.fallback_count",
                    "direction.claim_ledger",
                    "direction.overlap_matrix",
                    "direction.rejection_table",
                    "direction.evidence_level",
                    "direction.gate_table",
                    "direction.stop_condition",
                }.issubset(codes),
                codes,
            )

    def test_rejects_unreplaced_template_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp) / "template-copy.md"
            packet.write_text(
                "# Direction Decision Packet\n\n"
                "## Primary thesis\n\n"
                "- One sentence naming intervention, setting, decision objective, and distinguishing constraint.\n",
                encoding="utf-8",
            )

            result = self.run_validator(packet)

            self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertIn(
                "direction.template_placeholder",
                {error["code"] for error in report["errors"]},
            )


if __name__ == "__main__":
    unittest.main()
