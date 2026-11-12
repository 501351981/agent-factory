from __future__ import annotations

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import AgentCreate, SessionCreate
from agent_factory_server.runtime.hitl import ASK_USER_TOOL_NAME, waiting_expires_at


class SessionQueueRepoTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed(self, session: AsyncSession) -> str:
        agent = await repo.create_agent(
            session,
            AgentCreate(name="q-agent", system_prompt="hi", tools=[]),
        )
        sess = await repo.create_session(
            session,
            SessionCreate(agent_id=agent.id, user_key="u1", title="t"),
            system_prompt="hi",
            prompt_vars={},
            locale="zh",
        )
        return sess.id

    async def test_fifo_enqueue_claim_bind(self):
        async with self.Session() as session:
            session_id = await self._seed(session)
            a = await repo.enqueue_session_message(
                session, session_id=session_id, message="first"
            )
            b = await repo.enqueue_session_message(
                session, session_id=session_id, message="second"
            )
            queued = await repo.list_queued_items(session, session_id)
            self.assertEqual([x.message for x in queued], ["first", "second"])
            self.assertEqual(a.seq + 1, b.seq)

            claimed = await repo.claim_next_queue_item(session, session_id)
            assert claimed is not None
            self.assertEqual(claimed.message, "first")
            self.assertEqual(claimed.status, "dispatched")

            await repo.bind_queue_item_run(session, claimed.id, "run-1")
            row = await repo.get_queue_item(session, claimed.id)
            assert row is not None
            self.assertEqual(row.run_id, "run-1")

            remaining = await repo.list_queued_items(session, session_id)
            self.assertEqual([x.message for x in remaining], ["second"])

    async def test_cancel_queued_only(self):
        async with self.Session() as session:
            session_id = await self._seed(session)
            item = await repo.enqueue_session_message(
                session, session_id=session_id, message="x"
            )
            cancelled = await repo.cancel_queue_item(session, session_id, item.id)
            assert cancelled is not None
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(await repo.list_queued_items(session, session_id), [])

    async def test_waiting_human_blocks_idle_dispatch_precondition(self):
        """dispatch must see waiting_human via get_waiting_human_run."""
        async with self.Session() as session:
            session_id = await self._seed(session)
            await repo.enqueue_session_message(
                session, session_id=session_id, message="next"
            )
            run = await repo.create_run(session, session_id=session_id, user_input="hi")
            await repo.add_item(
                session,
                run_id=run.id,
                session_id=session_id,
                type="tool_call",
                call_id="c1",
                name=ASK_USER_TOOL_NAME,
                payload={"arguments": {"question": "?"}},
            )
            await repo.pause_run_waiting_human(
                session, run.id, waiting_expires_at=waiting_expires_at()
            )
            waiting = await repo.get_waiting_human_run(session, session_id)
            self.assertIsNotNone(waiting)
            # Claim would still work at DB layer; supervisor must gate on waiting.
            # Ensure queued items remain until waiting clears.
            queued = await repo.list_queued_items(session, session_id)
            self.assertEqual(len(queued), 1)


if __name__ == "__main__":
    unittest.main()
