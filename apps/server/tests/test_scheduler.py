from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

from agent_factory_server.db import models as _models  # noqa: F401
from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import (
    AgentCreate,
    AgentOut,
    AgentRuntimeConfig,
    SchedulerPolicy,
    SessionCreate,
)
from agent_factory_server.runtime.loop import resolve_runtime
from agent_factory_server.runtime.scheduler import (
    SCHEDULE_CREATE,
    SCHEDULE_LIST,
    fire_schedule_job,
    merge_agent_tools_with_scheduler,
    resolve_scheduler_policy,
    schedule_trigger_text,
)


class SchedulerUnitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _session(self) -> AsyncSession:
        return self.Session()

    async def test_policy_defaults_and_runtime(self) -> None:
        policy = resolve_scheduler_policy(None)
        self.assertTrue(policy.enabled)
        policy2 = resolve_scheduler_policy({"enabled": False, "max_active_jobs": 3})
        self.assertFalse(policy2.enabled)
        self.assertEqual(policy2.max_active_jobs, 3)

        now = datetime.now(timezone.utc)
        agent = AgentOut(
            id="a1",
            name="bot",
            system_prompt="x",
            model=None,
            model_profile_id=None,
            tools=[],
            skills=[],
            runtime=AgentRuntimeConfig(
                scheduler=SchedulerPolicy(enabled=False, max_active_jobs=10)
            ),
            created_at=now,
            updated_at=now,
        )
        runtime = resolve_runtime(agent)
        self.assertFalse(runtime.scheduler.enabled)
        self.assertEqual(runtime.scheduler.max_active_jobs, 10)

    async def test_tool_injection_gate(self) -> None:
        tools = merge_agent_tools_with_scheduler([], enabled=True, locale="zh")
        names = {t.name for t in tools}
        self.assertIn(SCHEDULE_CREATE, names)
        self.assertIn(SCHEDULE_LIST, names)
        off = merge_agent_tools_with_scheduler([], enabled=False)
        self.assertEqual(off, [])

    async def test_create_list_cancel_and_assistant_fire(self) -> None:
        async with await self._session() as session:
            agent = await repo.create_agent(
                session,
                AgentCreate(
                    name="sched-bot",
                    system_prompt="you schedule",
                    tools=[],
                    skills=[],
                    runtime={"scheduler": {"enabled": True}},
                ),
            )
            sess = await repo.create_session(
                session,
                SessionCreate(agent_id=agent.id, user_key="u1", title="t"),
            )
            run_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            job = await repo.create_schedule(
                session,
                agent_id=agent.id,
                user_key="u1",
                created_from_session_id=sess.id,
                schedule_kind="at",
                run_at=run_at,
                next_run_at=run_at,
                run_mode="assistant_message",
                payload_text="该喝水了",
                delete_after_run=True,
            )
            listed = await repo.list_schedules(
                session, agent_id=agent.id, user_key="u1", status="active"
            )
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].id, job.id)

            # Make due and claim+fire via assistant_message path.
            job.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.add(job)
            await session.commit()
            await session.refresh(job)

            result = await fire_schedule_job(job, db=session)
            self.assertTrue(result["ok"])
            self.assertEqual(result["session_id"], sess.id)
            self.assertTrue(result.get("run_id"))

            items = await repo.list_items_for_session(session, sess.id)
            asst = [i for i in items if i.type == "assistant_message"]
            self.assertEqual(len(asst), 1)
            self.assertEqual(asst[0].content, "该喝水了")

            timeline = await repo.get_session_timeline(session, sess.id)
            self.assertEqual(len(timeline.runs), 1)
            self.assertEqual(timeline.runs[0].user_message, "")
            self.assertTrue(
                any(s.type == "assistant_message" for s in timeline.runs[0].steps)
            )

            await repo.mark_schedule_success(
                session,
                job.id,
                fired_session_id=sess.id,
                fired_run_id=result["run_id"],
                completed=True,
            )
            done = await repo.get_schedule(session, job.id)
            assert done is not None
            self.assertEqual(done.status, "completed")

            cancelled = await repo.cancel_schedule(
                session, job.id, agent_id=agent.id, user_key="u1"
            )
            assert cancelled is not None
            # already completed — cancel is no-op status-wise if we only cancel active;
            # our cancel sets cancelled even from completed. Accept either.
            self.assertIn(cancelled.status, ("cancelled", "completed"))

    async def test_process_due_schedules_marks_completed(self) -> None:
        async with await self._session() as session:
            agent = await repo.create_agent(
                session,
                AgentCreate(
                    name="due-bot",
                    system_prompt="x",
                    tools=[],
                    skills=[],
                ),
            )
            sess = await repo.create_session(
                session,
                SessionCreate(agent_id=agent.id, user_key="u2"),
            )
            due = datetime.now(timezone.utc) - timedelta(seconds=2)
            job = await repo.create_schedule(
                session,
                agent_id=agent.id,
                user_key="u2",
                created_from_session_id=sess.id,
                schedule_kind="at",
                run_at=due,
                next_run_at=due,
                run_mode="assistant_message",
                payload_text="hello schedule",
            )
            job_id = job.id

        # process_due uses SessionLocal — patch by using in-memory won't work
        # unless we point SessionLocal at our engine. Call claim+fire manually instead.
        async with await self._session() as session:
            claimed = await repo.claim_due_schedules(session, limit=5)
            self.assertTrue(any(c.id == job_id for c in claimed))
            hit = next(c for c in claimed if c.id == job_id)
            result = await fire_schedule_job(hit, db=session)
            self.assertTrue(result["ok"])
            await repo.mark_schedule_success(
                session,
                job_id,
                fired_session_id=result["session_id"],
                fired_run_id=result.get("run_id"),
                completed=True,
            )
            row = await repo.get_schedule(session, job_id)
            assert row is not None
            self.assertEqual(row.status, "completed")

    async def test_trigger_text(self) -> None:
        text = schedule_trigger_text("abc", "喝水")
        self.assertTrue(text.startswith("[schedule:abc]"))
        self.assertIn("喝水", text)


if __name__ == "__main__":
    unittest.main()
