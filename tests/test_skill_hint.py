import tempfile
import unittest
from pathlib import Path

from devstudio.orchestration.render import SKILLS, _reference_rows, skill_hint, skill_ref

ROOT = Path(__file__).resolve().parents[1]


def _skill_files() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for top in ("celestia", "sdk"):
        for item in sorted((ROOT / top).rglob("SKILL.md")):
            found.setdefault(item.parent.name, item)
    return found


class SkillHintTests(unittest.TestCase):
    def test_every_listed_skill_has_a_short_hint(self):
        files = _skill_files()
        names = {name for group in SKILLS.values() for name in group}
        for name in sorted(names):
            with self.subTest(skill=name):
                self.assertIn(name, files)
                hint = skill_hint(files[name])
                self.assertTrue(hint)
                self.assertLessEqual(len(hint), 100)

    def test_skills_declare_when_to_use(self):
        for path in sorted(_skill_files().values()):
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("何时用：", text)
                self.assertIn("When:", text)

    def test_hint_prefers_when_marker_and_joins_folded_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(
                "---\nname: demo\ndescription: >-\n  做一件事。\n  何时用：用户说\n  加应用时。\n---\n\n# demo\n",
                encoding="utf-8",
            )
            self.assertEqual(skill_hint(path), "用户说加应用时。")

    def test_hint_falls_back_to_first_paragraph_without_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text("# demo\n\n比较两次读数。没有读数不写结论。\n", encoding="utf-8")
            self.assertEqual(skill_hint(path), "比较两次读数。")
            self.assertEqual(skill_hint(Path(tmp) / "missing.md"), "")

    def test_hint_emits_bilingual_when_both_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(
                "---\nname: demo\ndescription: >-\n  Does a thing.\n"
                "  何时用：中文触发。\n  When: English trigger keywords.\n---\n\n# demo\n",
                encoding="utf-8",
            )
            self.assertEqual(skill_hint(path), "English trigger keywords. | 中文触发。")

    def test_skill_ref_strips_prefix_and_suffix(self):
        self.assertEqual(
            skill_ref("devstudio/celestia/project/project-bootstrap/SKILL.md"),
            "celestia/project/project-bootstrap",
        )
        self.assertEqual(skill_ref("sdk/build/build-triage/SKILL.md"), "sdk/build/build-triage")
        self.assertEqual(
            skill_ref("/home/x/devstudio/celestia/overview/celestia/SKILL.md"),
            "celestia/overview/celestia",
        )

    def test_reference_rows_render_hint(self):
        rows = _reference_rows(
            [("devstudio/a/SKILL.md", "加应用时"), "devstudio/b/SKILL.md"],
            ["devstudio/ide/cursor.mcp.json"],
        )
        self.assertIn("  - a | 加应用时", rows)
        self.assertIn("  - b", rows)
        self.assertIn("- MCP (IDE loads from `devstudio/ide/`; else run `devstudio` CLI):", rows)
        self.assertIn("  - devstudio/ide/cursor.mcp.json", rows)


if __name__ == "__main__":
    unittest.main()
