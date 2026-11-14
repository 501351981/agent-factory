from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_factory_server.domain.schemas import AgentOut, SkillDef, ToolDef
from agent_factory_server.runtime.loop import build_system_prompt, merge_effective_tools
from agent_factory_server.runtime.skills import (
    SKILL_LOAD_TOOL_NAME,
    SKILL_READ_TOOL_NAME,
    format_skills_catalog,
)

# Real client skill body — must never appear in factory system/catalog.
_DEMO_SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "demo-bridge"
    / "skills"
    / "workspace-scout"
    / "SKILL.md"
)


class SkillsCatalogTest(unittest.TestCase):
    def test_skilldef_drops_legacy_content(self):
        body = _DEMO_SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("### Workspace Report", body)
        skill = SkillDef.model_validate(
            {
                "name": "workspace-scout",
                "content": body,
                "description": "scout local dirs",
            }
        )
        self.assertEqual(skill.name, "workspace-scout")
        self.assertEqual(skill.description, "scout local dirs")
        dumped = skill.model_dump()
        self.assertNotIn("content", dumped)

    def test_catalog_has_no_body(self):
        body = _DEMO_SKILL_MD.read_text(encoding="utf-8")
        text = format_skills_catalog(
            [
                SkillDef(
                    name="workspace-scout",
                    description="local workspace scout",
                )
            ],
            locale="zh",
        )
        self.assertIn("workspace-scout", text)
        self.assertIn("local workspace scout", text)
        self.assertIn(SKILL_LOAD_TOOL_NAME, text)
        self.assertNotIn("### Workspace Report", text)
        self.assertNotIn("Procedure", text)
        self.assertNotIn(body, text)

    def test_system_prompt_catalog_only(self):
        now = datetime.now(timezone.utc)
        agent = AgentOut(
            id="a1",
            name="demo",
            system_prompt="你是助手。",
            model=None,
            model_profile_id=None,
            tools=[],
            skills=[
                SkillDef(
                    name="workspace-scout",
                    description="问目录时使用",
                )
            ],
            created_at=now,
            updated_at=now,
        )
        prompt = build_system_prompt(agent, locale="zh")
        self.assertIn("问目录时使用", prompt)
        self.assertIn(SKILL_LOAD_TOOL_NAME, prompt)
        self.assertNotIn("### Workspace Report", prompt)
        self.assertNotIn("skill: workspace-scout", prompt)

    def test_skill_tools_merged_when_skills_present(self):
        tools = merge_effective_tools(
            [ToolDef(name="echo", description="d", execution="client")],
            memory_enabled=False,
            locale="zh",
            skills=[SkillDef(name="workspace-scout", description="d")],
        )
        names = [t.name for t in tools]
        self.assertIn(SKILL_LOAD_TOOL_NAME, names)
        self.assertIn(SKILL_READ_TOOL_NAME, names)
        load = next(t for t in tools if t.name == SKILL_LOAD_TOOL_NAME)
        self.assertEqual(load.execution, "builtin")

    def test_skill_tools_absent_without_skills(self):
        tools = merge_effective_tools(
            [ToolDef(name="echo", description="d", execution="client")],
            memory_enabled=False,
            locale="zh",
            skills=[],
        )
        names = [t.name for t in tools]
        self.assertNotIn(SKILL_LOAD_TOOL_NAME, names)
        self.assertNotIn(SKILL_READ_TOOL_NAME, names)


if __name__ == "__main__":
    unittest.main()
