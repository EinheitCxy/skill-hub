from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
module_spec = importlib.util.spec_from_file_location(
    "package_openai_plugin", ROOT / "scripts/package_openai_plugin.py"
)
packaging = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(packaging)


class AutoresearchBundleTests(unittest.TestCase):
    def test_package_omits_nested_runtime_state_and_caches(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            skill = root / "skills/example"
            skill.mkdir(parents=True)
            (root / "plugin.json").write_text('{"name":"example"}')
            (root / "README.md").write_text("Example")
            (skill / "SKILL.md").write_text("---\nname: example\n---\n")
            (skill / "runtime.py").write_text("print('runtime')")
            for name in [
                ".pytest_cache/state",
                ".research/state.json",
                ".venv/config",
                "__pycache__/runtime.pyc",
                "diagnostic.log",
            ]:
                path = skill / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("temporary state")
            output = Path(directory) / "package.zip"
            self.assertEqual(packaging.package_plugin(root, output), 1)
            with ZipFile(output) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        "plugin.json",
                        "README.md",
                        "skills/example/SKILL.md",
                        "skills/example/runtime.py",
                    },
                )

    def test_extracted_bundle_initializes_without_user_installed_skills(self):
        with TemporaryDirectory() as directory:
            temporary = Path(directory)
            archive_path = temporary / "skill-hub.zip"
            packaging.package_plugin(ROOT, archive_path)
            extracted = temporary / "extracted"
            with ZipFile(archive_path) as archive:
                archive.extractall(extracted)
                self.assertIn(
                    "skills/autoresearch-loop/scripts/autoresearch/runtime_navigation.py",
                    archive.namelist(),
                )
                self.assertIn(
                    "skills/autoresearch-loop/references/worker-skills.md",
                    archive.namelist(),
                )
                self.assertIn(
                    "skills/research-experiment-execution/SKILL.md", archive.namelist()
                )
            self._initialize(
                extracted / "skills", temporary / "home", temporary / "project"
            )

    def test_native_codex_and_claude_layouts_initialize_without_external_skills(self):
        for product in [".codex", ".claude"]:
            with self.subTest(product=product), TemporaryDirectory() as directory:
                temporary = Path(directory)
                home = temporary / "home"
                skills = home / product / "skills"
                shutil.copytree(
                    ROOT / "skills",
                    skills,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
                )
                self._initialize(skills, home, temporary / "project")

    def _initialize(self, skills: Path, home: Path, project: Path):
        home.mkdir(parents=True, exist_ok=True)
        loop = skills / "autoresearch-loop"
        command = [sys.executable, str(loop / "scripts/researchctl.py")]
        environment = {**os.environ, "HOME": str(home)}
        environment.pop("PYTHONPATH", None)
        environment.pop("CODEX_HOME", None)
        initialized = subprocess.run(
            [
                *command,
                "init",
                "--root",
                str(project),
                "--profile",
                str(loop / "assets/research-project/research.yaml"),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(
            initialized.returncode, 0, initialized.stdout + initialized.stderr
        )
        self.assertEqual(json.loads(initialized.stdout)["status"], "initialized")
        validated = subprocess.run(
            [*command, "validate", "--root", str(project)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)


if __name__ == "__main__":
    unittest.main()
