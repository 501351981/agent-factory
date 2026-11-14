from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import AgentCreate
from agent_factory_server.runtime.platform_skill_seed import seed_platform_skills
from agent_factory_server.runtime.skill_package import PackageError, unpack_skill_zip
from agent_factory_server.runtime.skill_runtime import execute_skill_tool
from agent_factory_server.runtime.skills import SKILL_LOAD_TOOL_NAME


def _make_zip(files: dict[str, str | bytes], *, nested_dir: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            full = f"{nested_dir}/{path}" if nested_dir else path
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(full, data)
    return buf.getvalue()


class PlatformSkillsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    async def test_seed_mount_and_load(self):
        async with self.Session() as session:
            await seed_platform_skills(session)
            listed = await repo.list_platform_skill_packages(session)
            names = {p.name for p in listed}
            self.assertIn("skill-creator", names)
            creator = next(p for p in listed if p.name == "skill-creator")
            self.assertEqual(creator.source, "builtin")

            agent = await repo.create_agent(
                session,
                AgentCreate(name="a1", system_prompt="hi", tools=[], skills=[]),
            )
            mount = await repo.mount_platform_skill(
                session, agent_id=agent.id, name="skill-creator"
            )
            self.assertEqual(mount.name, "skill-creator")

            agent2 = await repo.get_agent(session, agent.id)
            assert agent2 is not None
            skills = agent2.skills or []
            self.assertEqual(len(skills), 1)
            self.assertEqual(skills[0].origin, "platform")

            loaded = await execute_skill_tool(
                session,
                name=SKILL_LOAD_TOOL_NAME,
                arguments={"name": "skill-creator"},
                agent_id=agent.id,
                skills=skills,
                session_id="s1",
                client_device_id=None,
            )
            self.assertTrue(loaded["ok"])
            self.assertEqual(loaded["data"]["origin"], "platform")
            self.assertIn("Skill Creator", loaded["data"]["skill_md"])

            await repo.unmount_platform_skill(
                session, agent_id=agent.id, name="skill-creator"
            )
            agent3 = await repo.get_agent(session, agent.id)
            assert agent3 is not None
            self.assertEqual(agent3.skills, [])

    async def test_mount_conflicts_with_owned(self):
        raw = _make_zip(
            {
                "SKILL.md": (
                    "---\n"
                    "name: skill-creator\n"
                    "description: owned copy\n"
                    "---\n"
                    "# owned\n"
                ),
            }
        )
        async with self.Session() as session:
            await seed_platform_skills(session)
            agent = await repo.create_agent(
                session,
                AgentCreate(name="a2", system_prompt="hi", tools=[], skills=[]),
            )
            await repo.install_skill_package(
                session,
                agent_id=agent.id,
                unpacked=unpack_skill_zip(raw),
            )
            with self.assertRaises(PackageError) as ctx:
                await repo.mount_platform_skill(
                    session, agent_id=agent.id, name="skill-creator"
                )
            self.assertEqual(ctx.exception.code, "name_conflict")


if __name__ == "__main__":
    unittest.main()
