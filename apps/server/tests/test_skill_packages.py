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
from agent_factory_server.runtime.skill_package import PackageError, unpack_skill_zip
from agent_factory_server.runtime.skill_runtime import execute_skill_tool
from agent_factory_server.runtime.skills import SKILL_LOAD_TOOL_NAME, SKILL_READ_TOOL_NAME


def _make_zip(files: dict[str, str | bytes], *, nested_dir: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            full = f"{nested_dir}/{path}" if nested_dir else path
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(full, data)
    return buf.getvalue()


class UnpackSkillZipTest(unittest.TestCase):
    def test_unpack_nested_hub_layout(self):
        raw = _make_zip(
            {
                "SKILL.md": (
                    "---\n"
                    "name: nginx-triage\n"
                    "description: nginx 502 排查\n"
                    "---\n"
                    "# Nginx\n\nstep 1\n"
                ),
                "reference.md": "extra\n",
                "scripts/check.sh": "#!/bin/sh\necho ok\n",
            },
            nested_dir="nginx-triage",
        )
        unpacked = unpack_skill_zip(raw)
        self.assertEqual(unpacked["name"], "nginx-triage")
        self.assertEqual(unpacked["description"], "nginx 502 排查")
        paths = {f["path"] for f in unpacked["files"]}
        self.assertEqual(paths, {"SKILL.md", "reference.md", "scripts/check.sh"})
        script = next(f for f in unpacked["files"] if f["path"] == "scripts/check.sh")
        self.assertEqual(script["kind"], "script")
        self.assertFalse(script["is_binary"])

    def test_rejects_path_escape(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", "---\nname: x\ndescription: d\n---\n")
            zf.writestr("../evil.txt", "nope")
        with self.assertRaises(PackageError) as ctx:
            unpack_skill_zip(buf.getvalue())
        self.assertEqual(ctx.exception.code, "unsafe_path")

    def test_rejects_missing_skill_md(self):
        raw = _make_zip({"readme.md": "hi"})
        with self.assertRaises(PackageError) as ctx:
            unpack_skill_zip(raw)
        self.assertEqual(ctx.exception.code, "missing_skill_md")


class SkillPackageRepoTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    async def test_install_and_load_via_builtin(self):
        raw = _make_zip(
            {
                "SKILL.md": (
                    "---\n"
                    "name: pack-demo\n"
                    "description: demo package skill\n"
                    "---\n"
                    "# Pack Demo\n\nPROCEDURE_MARKER\n"
                ),
                "reference.md": "REF_MARKER\n",
            }
        )
        unpacked = unpack_skill_zip(raw)
        async with self.Session() as session:
            agent = await repo.create_agent(
                session,
                AgentCreate(name="a1", system_prompt="hi", tools=[], skills=[]),
            )
            pkg = await repo.install_skill_package(
                session, agent_id=agent.id, unpacked=unpacked, force=False
            )
            self.assertEqual(pkg.name, "pack-demo")
            self.assertEqual(pkg.file_count, 2)

            agent2 = await repo.get_agent(session, agent.id)
            assert agent2 is not None
            skills = agent2.skills or []
            self.assertEqual(len(skills), 1)
            self.assertEqual(skills[0].name, "pack-demo")
            self.assertEqual(skills[0].origin, "package")
            self.assertIn("demo package", skills[0].description)

            loaded = await execute_skill_tool(
                session,
                name=SKILL_LOAD_TOOL_NAME,
                arguments={"name": "pack-demo"},
                agent_id=agent.id,
                skills=skills,
                session_id="s1",
                client_device_id=None,
            )
            self.assertTrue(loaded["ok"])
            self.assertIn("PROCEDURE_MARKER", loaded["data"]["skill_md"])
            self.assertEqual(loaded["data"]["origin"], "package")
            self.assertIn("reference.md", loaded["data"]["files"])

            read = await execute_skill_tool(
                session,
                name=SKILL_READ_TOOL_NAME,
                arguments={"name": "pack-demo", "path": "reference.md"},
                agent_id=agent.id,
                skills=skills,
                session_id="s1",
                client_device_id=None,
            )
            self.assertTrue(read["ok"])
            self.assertIn("REF_MARKER", read["data"]["content"])
            self.assertIsNone(read["data"]["next_offset"])

            # No Bridge needed for package skills
            ok = await repo.uninstall_skill_package(
                session, agent_id=agent.id, name="pack-demo"
            )
            self.assertTrue(ok)
            agent3 = await repo.get_agent(session, agent.id)
            assert agent3 is not None
            self.assertEqual(agent3.skills, [])

    async def test_force_replace_and_client_conflict(self):
        raw = _make_zip(
            {
                "SKILL.md": "---\nname: c1\ndescription: v1\n---\n# v1\n",
            }
        )
        async with self.Session() as session:
            agent = await repo.create_agent(
                session,
                AgentCreate(
                    name="a2",
                    system_prompt="hi",
                    tools=[],
                    skills=[{"name": "c1", "description": "client one", "origin": "client"}],
                ),
            )
            with self.assertRaises(PackageError) as ctx:
                await repo.install_skill_package(
                    session,
                    agent_id=agent.id,
                    unpacked=unpack_skill_zip(raw),
                    force=False,
                )
            self.assertEqual(ctx.exception.code, "client_skill_conflict")

    async def test_update_description_and_force_keep(self):
        raw_v1 = _make_zip(
            {
                "SKILL.md": (
                    "---\n"
                    "name: pack-meta\n"
                    "description: from-zip-v1\n"
                    "version: 1.0.0\n"
                    "---\n"
                    "# v1\n"
                ),
            }
        )
        raw_v2 = _make_zip(
            {
                "SKILL.md": (
                    "---\n"
                    "name: pack-meta\n"
                    "description: from-zip-v2\n"
                    "version: 2.0.0\n"
                    "---\n"
                    "# v2 BODY\n"
                ),
                "extra.md": "more\n",
            }
        )
        async with self.Session() as session:
            agent = await repo.create_agent(
                session,
                AgentCreate(name="a3", system_prompt="hi", tools=[], skills=[]),
            )
            pkg = await repo.install_skill_package(
                session,
                agent_id=agent.id,
                unpacked=unpack_skill_zip(raw_v1),
            )
            self.assertEqual(pkg.version, "1.0.0")
            self.assertEqual(pkg.description, "from-zip-v1")

            updated = await repo.update_skill_package_meta(
                session,
                agent_id=agent.id,
                name="pack-meta",
                description="admin-edited",
            )
            assert updated is not None
            self.assertEqual(updated.description, "admin-edited")
            agent2 = await repo.get_agent(session, agent.id)
            assert agent2 is not None
            self.assertEqual(agent2.skills[0].description, "admin-edited")

            with self.assertRaises(PackageError) as ctx:
                await repo.install_skill_package(
                    session,
                    agent_id=agent.id,
                    unpacked=unpack_skill_zip(raw_v2),
                    force=True,
                    expected_name="other-name",
                )
            self.assertEqual(ctx.exception.code, "name_mismatch")

            replaced = await repo.install_skill_package(
                session,
                agent_id=agent.id,
                unpacked=unpack_skill_zip(raw_v2),
                force=True,
                expected_name="pack-meta",
                keep_description=True,
            )
            self.assertEqual(replaced.version, "2.0.0")
            self.assertEqual(replaced.description, "admin-edited")
            self.assertEqual(replaced.file_count, 2)
            agent3 = await repo.get_agent(session, agent.id)
            assert agent3 is not None
            self.assertEqual(agent3.skills[0].description, "admin-edited")

            replaced2 = await repo.install_skill_package(
                session,
                agent_id=agent.id,
                unpacked=unpack_skill_zip(raw_v2),
                force=True,
                keep_description=False,
            )
            self.assertEqual(replaced2.description, "from-zip-v2")


if __name__ == "__main__":
    unittest.main()
