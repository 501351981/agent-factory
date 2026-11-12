from __future__ import annotations

import unittest
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import MemoryPolicy, ToolDef
from agent_factory_server.runtime.context import _build_messages
from agent_factory_server.runtime.memory import (
    WRITE_DISCIPLINE,
    builtin_memory_tools,
    execute_memory_tool,
    format_memory_block,
    merge_agent_tools_with_memory,
)


def _mem(**kwargs):
    defaults = {
        "id": "m1",
        "agent_id": "a1",
        "scope": "longterm",
        "kind": "profile",
        "key": "k",
        "content": "v",
        "user_key": "u1",
        "session_id": None,
        "active": True,
        "pinned": False,
        "metadata_json": None,
        "source_run_id": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class MemoryHelpersTest(unittest.TestCase):
    def test_tools_include_discipline(self):
        write = next(t for t in builtin_memory_tools() if t.name == "memory_write")
        self.assertIn("profile", write.description)
        self.assertIn("playbook", write.description)

    def test_format_includes_discipline(self):
        text = format_memory_block(
            [_mem(key="lang", content="zh")],
            [_mem(kind="playbook", key="stock", content="查股")],
            max_chars=4000,
        )
        self.assertIn(WRITE_DISCIPLINE[:20], text)
        self.assertIn("画像", text)
        self.assertIn("实践目录", text)

    def test_prompt_order(self):
        messages = _build_messages(
            system_prompt="sys",
            memory_block="# Memory\n- x",
            summary_text="sum",
            runs=[],
            items_by_run={},
            include_run_ids=[],
            fold_run_ids=set(),
            include_reasoning=False,
            tool_result_max_chars=100,
        )
        self.assertEqual(messages[0]["content"], "sys")
        self.assertIn("Memory", messages[1]["content"])


class MemoryQuotaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_evict_oldest_playbook_when_full(self) -> None:
        policy = MemoryPolicy(max_playbook_items=2, evict_when_full=True, max_playbook_chars=100)
        async with self.Session() as session:
            for i in range(2):
                await repo.upsert_memory(
                    session,
                    agent_id="a1",
                    kind="playbook",
                    key=f"pb{i}",
                    content=f"practice {i}",
                    user_key="u1",
                    policy=policy,
                )
            row, extras = await repo.upsert_memory(
                session,
                agent_id="a1",
                kind="playbook",
                key="pb2",
                content="practice 2",
                user_key="u1",
                policy=policy,
            )
            self.assertTrue(row.active)
            self.assertTrue(extras["evicted_keys"])
            profiles, playbooks = await repo.load_memories_for_prompt(
                session,
                agent_id="a1",
                user_key="u1",
                profile_top_k=10,
                playbook_top_k=10,
            )
            self.assertEqual(len(playbooks), 2)
            keys = {p.key for p in playbooks}
            self.assertIn("pb2", keys)
            # cold ones still searchable
            all_pb = await repo.search_memories(
                session, agent_id="a1", user_key="u1", kind="playbook", include_inactive=True
            )
            self.assertEqual(len(all_pb), 3)

    async def test_truncate_and_reject_without_evict(self) -> None:
        policy = MemoryPolicy(
            max_playbook_items=1,
            evict_when_full=False,
            max_playbook_chars=10,
        )
        async with self.Session() as session:
            row, extras = await repo.upsert_memory(
                session,
                agent_id="a1",
                kind="playbook",
                key="long",
                content="abcdefghijklmnop",
                user_key="u1",
                policy=policy,
            )
            self.assertTrue(extras["truncated"])
            self.assertLessEqual(len(row.content), 12)
            with self.assertRaises(ValueError):
                await repo.upsert_memory(
                    session,
                    agent_id="a1",
                    kind="playbook",
                    key="other",
                    content="x",
                    user_key="u1",
                    policy=policy,
                )

    async def test_tools_with_policy(self) -> None:
        policy = MemoryPolicy(max_playbook_items=5)
        async with self.Session() as session:
            out = await execute_memory_tool(
                session,
                name="memory_write",
                arguments={
                    "kind": "profile",
                    "key": "lang",
                    "content": "zh",
                },
                agent_id="a1",
                session_id="s",
                user_key="u1",
                policy=policy,
            )
            self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
