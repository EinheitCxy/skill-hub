import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("collect_repo_snapshot.py")


class RepoSnapshotCollectorTests(unittest.TestCase):
    def test_collects_git_and_operational_entrypoints_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            (root / "train.py").write_text("print('train')\n", encoding="utf-8")
            (root / "evaluate.py").write_text("print('eval')\n", encoding="utf-8")
            (root / "checkpoint_latest.pt").write_text("weights\n", encoding="utf-8")
            (root / "package_submission.sh").write_text("exit 0\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            (root / "api_token.txt").write_text("secret\n", encoding="utf-8")

            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            (root / "train.py").write_text("print('changed')\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(root), "--mode", "evaluate"],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            snapshot = json.loads(result.stdout)
            self.assertEqual(snapshot["mode"], "evaluate")
            self.assertEqual(Path(snapshot["repository"]["root"]), root.resolve())
            self.assertTrue(snapshot["repository"]["git"]["available"])
            self.assertTrue(snapshot["repository"]["git"]["dirty"])
            self.assertIn("AGENTS.md", snapshot["instructions"])
            self.assertIn("train.py", snapshot["entrypoints"]["train"])
            self.assertIn("evaluate.py", snapshot["entrypoints"]["evaluate"])
            self.assertIn("checkpoint_latest.pt", snapshot["entrypoints"]["checkpoint"])
            self.assertIn("package_submission.sh", snapshot["entrypoints"]["package"])
            serialized = json.dumps(snapshot)
            self.assertNotIn(".env", serialized)
            self.assertNotIn("api_token.txt", serialized)


if __name__ == "__main__":
    unittest.main()
