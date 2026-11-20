from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

from agent_factory_server.api import board as board_api
from agent_factory_server.api import teams as teams_api
from agent_factory_server.auth import AuthContext, require_api_key, require_caller
from agent_factory_server.db import models as _models  # noqa: F401
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import (
    AgentCreate,
    BoardArtifactWrite,
    MultiAgentPolicy,
    TeamCreate,
    TeamMember,
)
from agent_factory_server.runtime.cancel import RunCancelRegistry
from agent_factory_server.runtime.loop import resolve_runtime
from agent_factory_server.runtime.multi_agent import (
    DELEGATE_TOOL_NAME,
    merge_agent_tools_with_multi_agent,
    resolve_multi_agent_policy,
)


class MultiAgentUnitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _session(self) -> AsyncSession:
        return self.Session()

    async def test_policy_defaults(self) -> None:
        policy = resolve_multi_agent_policy(None)
        self.assertFalse(policy.enabled)
        self.assertIsNone(policy.team_id)
        policy2 = resolve_multi_agent_policy({"enabled": True, "team_id": "t1"})
        self.assertTrue(policy2.enabled)
        self.assertEqual(policy2.team_id, "t1")

    async def test_resolve_runtime_keeps_multi_agent(self) -> None:
        from agent_factory_server.domain.schemas import AgentOut, AgentRuntimeConfig
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        agent = AgentOut(
            id="lead",
            name="lead",
            system_prompt="x",
            model=None,
            model_profile_id=None,
            tools=[],
            skills=[],
            runtime=AgentRuntimeConfig(
                multi_agent=MultiAgentPolicy(enabled=True, team_id="team-1")
            ),
            created_at=now,
            updated_at=now,
        )
        runtime = resolve_runtime(agent)
        self.assertTrue(runtime.multi_agent.enabled)
        self.assertEqual(runtime.multi_agent.team_id, "team-1")

    async def test_team_crud_and_tool_injection(self) -> None:
        async with await self._session() as session:
            lead = await repo.create_agent(
                session,
                AgentCreate(
                    name="lead",
                    system_prompt="you are lead",
                    tools=[],
                    skills=[],
                    runtime={"multi_agent": {"enabled": True}},
                ),
            )
            pm = await repo.create_agent(
                session,
                AgentCreate(name="pm", system_prompt="you are pm", tools=[], skills=[]),
            )
            team = await repo.create_team(
                session,
                TeamCreate(
                    name="squad",
                    lead_agent_id=lead.id,
                    members=[TeamMember(agent_id=pm.id, role="pm")],
                    allow_async=True,
                ),
            )
            from agent_factory_server.domain.schemas import AgentRuntimeConfig, AgentUpdate

            lead = await repo.update_agent(
                session,
                lead.id,
                AgentUpdate(
                    runtime=AgentRuntimeConfig(
                        multi_agent=MultiAgentPolicy(enabled=True, team_id=team.id)
                    )
                ),
            )
            assert lead is not None

            tools = await merge_agent_tools_with_multi_agent(
                [],
                agent=lead,
                runtime=lead.runtime,
                delegation_depth=0,
                locale="zh",
                session=session,
            )
            names = {t.name for t in tools}
            self.assertIn(DELEGATE_TOOL_NAME, names)
            self.assertIn("board_list", names)
            self.assertIn("board_write", names)

            # depth gate: child should not get delegate
            child_tools = await merge_agent_tools_with_multi_agent(
                [],
                agent=pm,
                runtime=pm.runtime,
                delegation_depth=1,
                locale="zh",
                session=session,
            )
            child_names = {t.name for t in child_tools}
            self.assertNotIn(DELEGATE_TOOL_NAME, child_names)
            self.assertIn("board_list", child_names)

    async def test_board_write_read(self) -> None:
        async with await self._session() as session:
            lead = await repo.create_agent(
                session,
                AgentCreate(name="lead2", system_prompt="x", tools=[], skills=[]),
            )
            sess = await repo.create_session(
                session,
                __import__(
                    "agent_factory_server.domain.schemas", fromlist=["SessionCreate"]
                ).SessionCreate(
                    agent_id=lead.id,
                    user_key="u1",
                    title="chat",
                ),
            )
            project = await repo.get_or_create_board_project(
                session, lead_session_id=sess.id, user_key="u1"
            )
            art = await repo.write_board_artifact(
                session,
                project_id=project.id,
                payload=BoardArtifactWrite(
                    path="prd/login.md", title="Login PRD", content="# PRD"
                ),
                author_agent_id=lead.id,
            )
            self.assertEqual(art.version, 1)
            art2 = await repo.write_board_artifact(
                session,
                project_id=project.id,
                payload=BoardArtifactWrite(path="prd/login.md", content="# PRD v2"),
                author_agent_id=lead.id,
            )
            self.assertEqual(art2.version, 2)
            got = await repo.get_board_artifact(
                session, project_id=project.id, path="prd/login.md"
            )
            assert got is not None
            self.assertEqual(got.content, "# PRD v2")

    async def test_cancel_cascade(self) -> None:
        reg = RunCancelRegistry()
        await reg.register_child("parent", "child-a")
        await reg.register_child("child-a", "child-b")
        await reg.request_cancel("parent")
        self.assertTrue(await reg.is_cancelled("parent"))
        self.assertTrue(await reg.is_cancelled("child-a"))
        self.assertTrue(await reg.is_cancelled("child-b"))


class TeamsApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        self.Session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        app = FastAPI()

        async def _override_session():
            async with self.Session() as session:
                yield session

        async def _platform_auth():
            return AuthContext(kind="platform")

        app.dependency_overrides[get_session] = _override_session
        app.dependency_overrides[require_api_key] = _platform_auth
        app.dependency_overrides[require_caller] = _platform_auth
        app.include_router(teams_api.router)
        app.include_router(board_api.router)
        self.client = TestClient(app)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_team_api(self) -> None:
        async with self.Session() as session:
            lead = await repo.create_agent(
                session,
                AgentCreate(name="api-lead", system_prompt="x", tools=[], skills=[]),
            )
            member = await repo.create_agent(
                session,
                AgentCreate(name="api-pm", system_prompt="y", tools=[], skills=[]),
            )
            lead_id, member_id = lead.id, member.id

        resp = self.client.post(
            "/api/teams",
            json={
                "name": "api-squad",
                "lead_agent_id": lead_id,
                "members": [{"agent_id": member_id, "role": "pm"}],
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["name"], "api-squad")
        self.assertEqual(len(body["members"]), 1)

        listed = self.client.get("/api/teams")
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(any(t["id"] == body["id"] for t in listed.json()))


if __name__ == "__main__":
    unittest.main()
