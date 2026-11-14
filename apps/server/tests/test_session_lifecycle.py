from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import AgentCreate, SessionCreate, SessionUpdate


class SessionLifecycleTest(unittest.IsolatedAsyncioTestCase):
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
            session, AgentCreate(name="life", system_prompt="hi", tools=[])
        )
        sess = await repo.create_session(
            session,
            SessionCreate(agent_id=agent.id, user_key="u1", title="原标题"),
            system_prompt="hi",
            prompt_vars={},
            locale="zh",
        )
        return agent.id, sess.id

    async def test_rename_session(self) -> None:
        async with self.Session() as session:
            _agent_id, session_id = await self._seed(session)
            updated = await repo.update_session(
                session, session_id, SessionUpdate(title="新标题")
            )
            assert updated is not None
            self.assertEqual(updated.title, "新标题")

    async def test_archive_hides_from_list(self) -> None:
        async with self.Session() as session:
            _agent_id, session_id = await self._seed(session)
            await repo.archive_session(session, session_id)
            page = await repo.list_sessions(session, page=1, page_size=20)
            self.assertEqual(page.total, 0)
            page_all = await repo.list_sessions(
                session, page=1, page_size=20, include_archived=True
            )
            self.assertEqual(page_all.total, 1)
            self.assertEqual(page_all.items[0].status, "archived")

    async def test_clear_history_hides_messages_and_items(self) -> None:
        async with self.Session() as session:
            _agent_id, session_id = await self._seed(session)
            run = await repo.create_run(
                session, session_id=session_id, user_input="旧问题"
            )
            await repo.add_item(
                session,
                run_id=run.id,
                session_id=session_id,
                type="user_message",
                content="旧问题",
            )
            await repo.add_item(
                session,
                run_id=run.id,
                session_id=session_id,
                type="assistant_message",
                content="旧回答",
            )
            before = await repo.list_messages(session, session_id)
            self.assertEqual(len(before), 2)

            cleared = await repo.clear_session_history(session, session_id)
            assert cleared is not None
            self.assertIsNotNone(cleared.history_cleared_at)

            after = await repo.list_messages(session, session_id)
            self.assertEqual(after, [])
            timeline = await repo.get_session_timeline(session, session_id)
            self.assertEqual(timeline.runs, [])

            # New messages after clear remain visible.
            await asyncio.sleep(0.01)
            run2 = await repo.create_run(
                session, session_id=session_id, user_input="新问题"
            )
            await repo.add_item(
                session,
                run_id=run2.id,
                session_id=session_id,
                type="user_message",
                content="新问题",
            )
            msgs = await repo.list_messages(session, session_id)
            self.assertEqual([m.content for m in msgs], ["新问题"])

    async def test_clear_cancels_queue(self) -> None:
        async with self.Session() as session:
            _agent_id, session_id = await self._seed(session)
            await repo.enqueue_session_message(
                session, session_id=session_id, message="pending"
            )
            await repo.clear_session_history(session, session_id)
            queued = await repo.list_queued_items(session, session_id)
            self.assertEqual(queued, [])

    async def test_hard_delete_removes_rows(self) -> None:
        async with self.Session() as session:
            _agent_id, session_id = await self._seed(session)
            run = await repo.create_run(
                session, session_id=session_id, user_input="bye"
            )
            await repo.add_item(
                session,
                run_id=run.id,
                session_id=session_id,
                type="user_message",
                content="bye",
            )
            ok = await repo.hard_delete_session(session, session_id)
            self.assertTrue(ok)
            self.assertIsNone(await repo.get_session_row(session, session_id))
            self.assertEqual(await repo.list_messages(session, session_id), [])
            self.assertEqual(await repo.list_runs_for_session(session, session_id), [])

    async def test_watermark_filters_old_created_at(self) -> None:
        """Items stamped before cleared_at stay hidden even if inserted after watermark set."""
        async with self.Session() as session:
            _agent_id, session_id = await self._seed(session)
            cleared = await repo.clear_session_history(session, session_id)
            assert cleared is not None
            assert cleared.history_cleared_at is not None

            # Manually plant an item with old timestamp (simulates pre-clear data).
            run = await repo.create_run(
                session, session_id=session_id, user_input="ghost"
            )
            item = await repo.add_item(
                session,
                run_id=run.id,
                session_id=session_id,
                type="user_message",
                content="ghost",
            )
            item.created_at = cleared.history_cleared_at - timedelta(seconds=5)
            run.started_at = cleared.history_cleared_at - timedelta(seconds=5)
            session.add(item)
            session.add(run)
            await session.commit()

            self.assertEqual(await repo.list_messages(session, session_id), [])
            self.assertEqual(await repo.list_runs_for_session(session, session_id), [])


if __name__ == "__main__":
    unittest.main()
