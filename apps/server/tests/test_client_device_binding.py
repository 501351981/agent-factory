from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import AgentCreate, SessionCreate
from agent_factory_server.tools.client_hub import ClientHub


class ClientDeviceBindingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_session_stores_client_device(self):
        async with self.Session() as session:
            agent = await repo.create_agent(
                session, AgentCreate(name="a", system_prompt="hi", tools=[])
            )
            out = await repo.create_session(
                session,
                SessionCreate(
                    agent_id=agent.id,
                    user_key="u1",
                    client_device_id="demo-mac",
                ),
            )
            self.assertEqual(out.client_device_id, "demo-mac")
            row = await repo.get_session_row(session, out.id)
            assert row is not None
            self.assertEqual(row.client_device_id, "demo-mac")

    async def test_ensure_binding_sticky_and_auto(self):
        async with self.Session() as session:
            agent = await repo.create_agent(
                session, AgentCreate(name="a", system_prompt="hi", tools=[])
            )
            created = await repo.create_session(
                session,
                SessionCreate(agent_id=agent.id, user_key="u1"),
            )
            self.assertIsNone(created.client_device_id)

            class FakeHub:
                def online_ids(self):
                    return ["only-one"]

            with patch(
                "agent_factory_server.tools.client_hub.client_hub",
                FakeHub(),
            ):
                bound = await repo.ensure_session_client_device(session, created.id)
                self.assertEqual(bound, "only-one")

                again = await repo.ensure_session_client_device(
                    session, created.id, preferred_device_id="other"
                )
                self.assertEqual(again, "only-one")

    async def test_call_tool_requires_bound_device(self):
        hub = ClientHub()
        result = await hub.call_tool(
            device_id="",
            call_id="c1",
            name="echo",
            arguments={},
            session_id="s1",
            timeout=1.0,
        )
        self.assertFalse(result["ok"])
        self.assertIn("client_device_id", result["error"])

        result2 = await hub.call_tool(
            device_id="missing-device",
            call_id="c2",
            name="echo",
            arguments={},
            session_id="s1",
            timeout=1.0,
        )
        self.assertFalse(result2["ok"])
        self.assertIn("offline", result2["error"])

    async def test_send_tool_cancel_and_cancel_pending(self):
        hub = ClientHub()
        sent: list[dict] = []

        class FakeWs:
            async def send_json(self, message):
                sent.append(message)

            async def close(self):
                return None

        await hub.register("dev-1", FakeWs())  # type: ignore[arg-type]
        ok = await hub.send_tool_cancel(
            device_id="dev-1",
            call_id="call_abc",
            session_id="sess-1",
        )
        self.assertTrue(ok)
        self.assertEqual(sent[-1]["type"], "tool_cancel")
        self.assertEqual(sent[-1]["call_id"], "call_abc")
        self.assertEqual(sent[-1]["session_id"], "sess-1")

        offline = await hub.send_tool_cancel(device_id="missing", call_id="x")
        self.assertFalse(offline)

        import asyncio

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        hub._pending["call_abc"] = fut
        self.assertTrue(hub.cancel_pending("call_abc", error="cancelled by user"))
        self.assertTrue(fut.done())
        self.assertTrue(fut.result().get("cancelled"))
