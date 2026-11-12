from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from demo_bridge.skills_fs import skill_load, skill_read


class SkillsFsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        skill = self.root / "workspace-scout"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "# Workspace Scout\n\n### Workspace Report\n",
            encoding="utf-8",
        )
        (skill / "reference.md").write_text("extra notes\n", encoding="utf-8")
        nested = skill / "docs"
        nested.mkdir()
        (nested / "tips.md").write_text("nested tip\n", encoding="utf-8")

        outside = self.root / "secret.txt"
        outside.write_text("should-not-read\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_skill_load_returns_body_and_files(self):
        result = skill_load({"name": "workspace-scout"}, root=self.root)
        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["name"], "workspace-scout")
        self.assertIn("### Workspace Report", data["skill_md"])
        self.assertIn("SKILL.md", data["files"])
        self.assertIn("reference.md", data["files"])
        self.assertIn("docs/tips.md", data["files"])

    def test_skill_load_missing_skill(self):
        result = skill_load({"name": "not-installed"}, root=self.root)
        self.assertFalse(result["ok"])
        self.assertIn("skill not found", result["error"])

    def test_skill_load_rejects_invalid_name(self):
        for name in ["", "Bad Name", "../etc", "UPPER", "a/b"]:
            with self.subTest(name=name):
                result = skill_load({"name": name}, root=self.root)
                self.assertFalse(result["ok"])

    def test_skill_load_missing_skill_md(self):
        empty = self.root / "empty-skill"
        empty.mkdir()
        result = skill_load({"name": "empty-skill"}, root=self.root)
        self.assertFalse(result["ok"])
        self.assertIn("SKILL.md missing", result["error"])

    def test_skill_read_relative_file(self):
        result = skill_read(
            {"name": "workspace-scout", "path": "reference.md"},
            root=self.root,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["content"], "extra notes\n")

    def test_skill_read_nested_relative(self):
        result = skill_read(
            {"name": "workspace-scout", "path": "docs/tips.md"},
            root=self.root,
        )
        self.assertTrue(result["ok"])
        self.assertIn("nested tip", result["data"]["content"])

    def test_skill_read_rejects_escape_and_absolute(self):
        cases = [
            {"name": "workspace-scout", "path": "../secret.txt"},
            {"name": "workspace-scout", "path": "/etc/passwd"},
            {"name": "workspace-scout", "path": "docs/../../secret.txt"},
            {"name": "workspace-scout", "path": ""},
        ]
        for args in cases:
            with self.subTest(path=args["path"]):
                result = skill_read(args, root=self.root)
                self.assertFalse(result["ok"])
                err = result.get("error") or ""
                self.assertTrue(
                    "invalid path" in err or "path escapes" in err,
                    msg=err,
                )


if __name__ == "__main__":
    unittest.main()
