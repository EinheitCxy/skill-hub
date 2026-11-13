"""Structural regression checks; not a claim of model-level effectiveness."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]

class SkillRoutingTests(unittest.TestCase):
    def test_humanizer_language_guides_exist_and_are_split(self):
        root = ROOT / 'skills/humanizer'
        main = (root / 'SKILL.md').read_text()
        self.assertLess(len(main), 5000)
        for filename in ('chinese-rules.md', 'english-rules.md'):
            self.assertIn('references/' + filename, main)
            self.assertTrue((root / 'references' / filename).is_file())
        english = (root / 'references/english-rules.md').read_text()
        self.assertEqual(len(re.findall(r'^### \d+\.', english, re.M)), 25)
        self.assertIn('information preservation', main)
        self.assertIn('missing resource', main)

    def test_changed_skill_local_markdown_links_resolve(self):
        paths = [ROOT / 'skills' / name / 'SKILL.md' for name in
                 ('humanizer', 'paper-review', 'teacher', 'research-figure-creator')]
        paths += list((ROOT / 'skills/humanizer/references').glob('*.md'))
        for path in paths:
            for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
                if '://' in target or target.startswith('#'):
                    continue
                with self.subTest(file=path.name, target=target):
                    self.assertTrue((path.parent / target.split('#')[0]).exists())

    def test_paper_input_retrieval_has_scope_guard(self):
        text = (ROOT / 'skills/paper-review/SKILL.md').read_text()
        self.assertIn('title, link, or incomplete material', text)
        self.assertIn('abstract-only', text)
        self.assertIn('user-provided annotations', text)
        self.assertNotIn('Do not search for, download', text)

    def test_figure_fallback_retains_verification(self):
        text = (ROOT / 'skills/research-figure-creator/SKILL.md').read_text()
        self.assertIn('If an optional skill is absent', text)
        self.assertIn('actual rendered output', text)

    def test_teacher_both_routes_distinguish_concept_from_source(self):
        text = (ROOT / 'skills/teacher/SKILL.md').read_text()
        reference = (ROOT / 'skills/teacher/references/source-model-walkthrough.md').read_text()
        self.assertIn('general conceptual question', text)
        self.assertIn('source is unavailable', text)
        self.assertIn('一般概念问题', reference)
        self.assertIn('源码不可用', reference)

if __name__ == '__main__':
    unittest.main()
