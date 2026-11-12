from __future__ import annotations

import unittest
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.db.models import utcnow
from agent_factory_server.domain.schemas import AgentCreate, SessionCreate, ToolDef
from agent_factory_server.runtime.hitl import (
    ASK_USER_TOOL_NAME,
    ask_user_tool,
    parse_ask_user_arguments,
    waiting_expires_at,
)
from agent_factory_server.runtime.loop import merge_effective_tools


class HitlHelpersTest(unittest.TestCase):
    def test_parse_ask_user_defaults(self):
        parsed = parse_ask_user_arguments({"question": "选一个？", "options": ["A", "B"]})
        self.assertEqual(parsed["question"], "选一个？")
        self.assertEqual(parsed["options"], ["A", "B"])
        self.assertTrue(parsed["allow_free_text"])

    def test_parse_forces_free_text_without_options(self):
        parsed = parse_ask_user_arguments(
            {"question": "补充说明", "allow_free_text": False}
        )
        self.assertTrue(parsed["allow_free_text"])

    def test_ask_user_always_merged(self):
        tools = merge_effective_tools(
            [ToolDef(name="echo", description="d", execution="client")],
            memory_enabled=False,
            locale="zh",
        )
        names = [t.name for t in tools]
        self.assertIn(ASK_USER_TOOL_NAME, names)
        self.assertIn("echo", names)

    def test_ask_user_tool_locale(self):
        zh = ask_user_tool(locale="zh")
        en = ask_user_tool(locale="en")
        self.assertIn("用户", zh.description)
        self.assertIn("user", en.description.lower())


class HitlRepoTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed(self, session: AsyncSession) -> tuple[str, str]:
        agent = await repo.create_agent(
            session,
            AgentCreate(
                name="hitl-agent",
                system_prompt="you are helpful",
                tools=[],
            ),
        )
        sess = await repo.create_session(
            session,
            SessionCreate(agent_id=agent.id, user_key="u1", title="t"),
            system_prompt="you are helpful",
            prompt_vars={},
            locale="zh",
        )
        return agent.id, sess.id

    async def test_pause_resume_and_pending_call(self):
        async with self.Session() as session:
            _, session_id = await self._seed(session)
            run = await repo.create_run(session, session_id=session_id, user_input="hi")
            await repo.add_item(
                session,
                run_id=run.id,
                session_id=session_id,
                type="tool_call",
                call_id="c1",
                name=ASK_USER_TOOL_NAME,
                payload={"arguments": {"question": "继续吗？", "options": ["是", "否"]}},
            )
            expires = waiting_expires_at(ttl=timedelta(hours=1))
            paused = await repo.pause_run_waiting_human(
                session, run.id, waiting_expires_at=expires
            )
            assert paused is not None
            self.assertEqual(paused.status, "waiting_human")
            self.assertIsNotNone(paused.waiting_expires_at)
            self.assertIsNone(paused.finished_at)

            pending = await repo.find_pending_ask_user_call(session, run.id)
            assert pending is not None
            self.assertEqual(pending.call_id, "c1")

            resumed = await repo.resume_run_to_running(session, run.id)
            assert resumed is not None
            self.assertEqual(resumed.status, "running")
            self.assertIsNone(resumed.waiting_expires_at)

    async def test_expire_waiting_human(self):
        async with self.Session() as session:
            _, session_id = await self._seed(session)
            run = await repo.create_run(session, session_id=session_id, user_input="hi")
            await repo.add_item(
                session,
                run_id=run.id,
                session_id=session_id,
                type="tool_call",
                call_id="c-expire",
                name=ASK_USER_TOOL_NAME,
                payload={"arguments": {"question": "超时？"}},
            )
            past = utcnow() - timedelta(minutes=1)
            await repo.pause_run_waiting_human(
                session, run.id, waiting_expires_at=past
            )
            count = await repo.expire_waiting_human_runs(session)
            self.assertEqual(count, 1)
            row = await repo.get_run(session, run.id)
            assert row is not None
            self.assertEqual(row.status, "cancelled")
            self.assertEqual(row.finish_reason, "timeout")
            pending = await repo.find_pending_ask_user_call(session, run.id)
            self.assertIsNone(pending)

    async def test_supersede_on_new_message(self):
        async with self.Session() as session:
            _, session_id = await self._seed(session)
            run = await repo.create_run(session, session_id=session_id, user_input="hi")
            await repo.add_item(
                session,
                run_id=run.id,
                session_id=session_id,
                type="tool_call",
                call_id="c-super",
                name=ASK_USER_TOOL_NAME,
                payload={"arguments": {"question": "抢占？"}},
            )
            await repo.pause_run_waiting_human(
                session, run.id, waiting_expires_at=waiting_expires_at()
            )
            cancelled = await repo.supersede_waiting_human_for_session(
                session, session_id
            )
            assert cancelled is not None
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(cancelled.finish_reason, "superseded")
            waiting = await repo.get_waiting_human_run(session, session_id)
            self.assertIsNone(waiting)


if __name__ == "__main__":
    unittest.main()
