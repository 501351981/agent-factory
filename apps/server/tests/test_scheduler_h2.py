from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

from agent_factory_server.api import schedules as schedules_api
from agent_factory_server.auth import AuthContext, require_caller
from agent_factory_server.db import models as _models  # noqa: F401
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import AgentCreate, SessionCreate
from agent_factory_server.runtime.scheduler import (
    SCHEDULE_PAUSE,
    SCHEDULE_RESUME,
    compute_next_run_at,
    fire_schedule_job,
    merge_agent_tools_with_scheduler,
    validate_cron_expr,
)
from agent_factory_server.db.models import AgentScheduleRecord


class SchedulerH2Test(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _session(self) -> AsyncSession:
        return self.Session()

    async def test_pause_resume_tools_injected(self) -> None:
        tools = merge_agent_tools_with_scheduler([], enabled=True)
        names = {t.name for t in tools}
        self.assertIn(SCHEDULE_PAUSE, names)
        self.assertIn(SCHEDULE_RESUME, names)

    async def test_every_and_cron_next_run(self) -> None:
        now = datetime.now(timezone.utc)
        every_job = AgentScheduleRecord(
            schedule_kind="every",
            every_ms=60_000,
            next_run_at=now - timedelta(seconds=1),
        )
        nxt = compute_next_run_at(every_job, after=now)
        assert nxt is not None
        self.assertGreaterEqual((nxt - now).total_seconds(), 59)

        validate_cron_expr("0 9 * * *", tz_name="Asia/Shanghai")
        cron_job = AgentScheduleRecord(
            schedule_kind="cron",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
        )
        cron_next = compute_next_run_at(cron_job, after=now)
        assert cron_next is not None
        self.assertGreater(cron_next, now)

        with self.assertRaises(ValueError):
            validate_cron_expr("not a cron")

    async def test_latest_fallback_and_isolated(self) -> None:
        async with await self._session() as session:
            agent = await repo.create_agent(
                session,
                AgentCreate(name="h2", system_prompt="x", tools=[], skills=[]),
            )
            origin = await repo.create_session(
                session,
                SessionCreate(agent_id=agent.id, user_key="u", title="origin"),
            )
            newer = await repo.create_session(
                session,
                SessionCreate(agent_id=agent.id, user_key="u", title="newer"),
            )
            # bump newer
            await repo.touch_session(session, newer.id)

            due = datetime.now(timezone.utc) - timedelta(seconds=1)
            latest_job = await repo.create_schedule(
                session,
                agent_id=agent.id,
                user_key="u",
                created_from_session_id=origin.id,
                schedule_kind="at",
                run_at=due,
                next_run_at=due,
                session_target="latest",
                run_mode="assistant_message",
                payload_text="latest hello",
            )
            result = await fire_schedule_job(latest_job, db=session)
            self.assertEqual(result["session_id"], newer.id)

            iso_job = await repo.create_schedule(
                session,
                agent_id=agent.id,
                user_key="u",
                created_from_session_id=origin.id,
                schedule_kind="at",
                run_at=due,
                next_run_at=due,
                run_mode="isolated_session",
                payload_text="请总结一下",
                name="日报",
            )
            # isolated uses enqueue_or_start → needs SessionLocal; may fail without LLM.
            # Create path only: verify resolve creates system session via fire's isolated branch
            # by mocking synthetic — instead call create_delegate_session pattern through fire
            # and catch WaitingHuman/LLM errors. For unit test, inspect create only:
            from agent_factory_server.runtime import scheduler as sched

            async def _fake_synth(*, agent, session_id, trigger):
                return {"ok": True, "session_id": session_id, "run_id": "r1", "enqueued": False}

            orig = sched.fire_synthetic_user
            sched.fire_synthetic_user = _fake_synth  # type: ignore
            try:
                iso_result = await fire_schedule_job(iso_job, db=session)
            finally:
                sched.fire_synthetic_user = orig  # type: ignore
            self.assertTrue(iso_result["ok"])
            iso_sid = iso_result["session_id"]
            self.assertNotEqual(iso_sid, origin.id)
            row = await repo.get_session_row(session, iso_sid)
            assert row is not None
            self.assertEqual(row.visibility, "system")
            self.assertEqual(row.schedule_job_id, iso_job.id)

            paused = await repo.pause_schedule(session, latest_job.id, agent_id=agent.id)
            assert paused is not None
            self.assertEqual(paused.status, "paused")
            resumed = await repo.resume_schedule(session, latest_job.id, agent_id=agent.id)
            assert resumed is not None
            self.assertEqual(resumed.status, "active")

    async def test_schedules_api(self) -> None:
        engine = self.engine
        Session = self.Session

        async def _override_session():
            async with Session() as session:
                yield session

        app = FastAPI()
        app.include_router(schedules_api.router)
        app.dependency_overrides[get_session] = _override_session
        app.dependency_overrides[require_caller] = lambda: AuthContext(kind="platform")

        async with Session() as session:
            agent = await repo.create_agent(
                session,
                AgentCreate(name="api-bot", system_prompt="x", tools=[], skills=[]),
            )
            sess = await repo.create_session(
                session,
                SessionCreate(agent_id=agent.id, user_key="u"),
            )
            due = datetime.now(timezone.utc) + timedelta(minutes=1)
            job = await repo.create_schedule(
                session,
                agent_id=agent.id,
                user_key="u",
                created_from_session_id=sess.id,
                schedule_kind="every",
                every_ms=60_000,
                next_run_at=due,
                run_mode="assistant_message",
                payload_text="tick",
                delete_after_run=False,
            )
            agent_id = agent.id
            job_id = job.id

        with TestClient(app) as client:
            resp = client.get(f"/api/agents/{agent_id}/schedules")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(any(j["id"] == job_id for j in data))

            resp = client.post(f"/api/schedules/{job_id}/pause")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "paused")

            resp = client.post(f"/api/schedules/{job_id}/resume")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "active")

            resp = client.post(f"/api/schedules/{job_id}/cancel")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
