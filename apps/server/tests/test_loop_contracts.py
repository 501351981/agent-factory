from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import (
    AgentCreate,
    AgentRuntimeConfig,
    SessionCreate,
    SkillDef,
    ToolDef,
)
from agent_factory_server.runtime.cancel import run_cancel_registry
from agent_factory_server.runtime.loop import run_chat_loop
from agent_factory_server.runtime.skills import SKILL_LOAD_TOOL_NAME


class ScriptedLLM:
    """Minimal OpenAICompatClient stand-in for loop contract tests."""

    def __init__(self, rounds: list[dict[str, Any]]) -> None:
        self._rounds = list(rounds)
        self.calls = 0

    async def chat_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        should_cancel=None,
    ):
        self.calls += 1
        if not self._rounds:
            yield {
                "type": "complete",
                "message": {"role": "assistant", "content": "fallback"},
            }
            return
        step = self._rounds.pop(0)
        kind = step.get("type")
        if kind == "text":
            content = str(step.get("content") or "")
            if content:
                yield {"type": "message_delta", "delta": content}
            yield {
                "type": "complete",
                "message": {"role": "assistant", "content": content},
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            }
            return
        if kind == "tool":
            name = str(step.get("name") or "echo")
            arguments = step.get("arguments") or {}
            yield {
                "type": "complete",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": str(step.get("call_id") or "call_test_1"),
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ],
                },
            }
            return
        if kind == "slow":
            yield {"type": "message_delta", "delta": "…"}
            while True:
                if should_cancel is not None and await should_cancel():
                    yield {"type": "cancelled"}
                    return
                await asyncio.sleep(0.02)
            return
        raise AssertionError(f"unknown script step: {step}")


def _mock_resolve(llm: ScriptedLLM):
    async def _resolve(*_args, **_kwargs):
        return (llm, "mock-model", 0.0, False, 128000, 8192, None, 0.0, 0.0)

    return _resolve


class LoopContractsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _create_agent_session(
        self,
        session: AsyncSession,
        *,
        runtime: Optional[AgentRuntimeConfig] = None,
        tools: Optional[list[ToolDef]] = None,
        skills: Optional[list[SkillDef]] = None,
        client_device_id: Optional[str] = None,
    ) -> tuple[Any, str]:
        agent = await repo.create_agent(
            session,
            AgentCreate(
                name="loop-agent",
                system_prompt="you are helpful",
                tools=tools or [],
                skills=skills or [],
                runtime=runtime or AgentRuntimeConfig(run_timeout_seconds=30),
            ),
        )
        sess = await repo.create_session(
            session,
            SessionCreate(
                agent_id=agent.id,
                user_key="u1",
                title="t",
                client_device_id=client_device_id,
            ),
            system_prompt="you are helpful",
            prompt_vars={},
            locale="zh",
        )
        return agent, sess.id

    async def test_cancel_interrupts_llm_stream(self):
        llm = ScriptedLLM([{"type": "slow"}])
        async with self.Session() as session:
            agent, session_id = await self._create_agent_session(session)
            events: list[dict[str, Any]] = []
            run_id: str | None = None
            with patch(
                "agent_factory_server.runtime.loop.resolve_llm_client",
                new=AsyncMock(side_effect=_mock_resolve(llm)),
            ):
                async for event in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message="hello",
                ):
                    events.append(event)
                    if event.get("type") == "run_start":
                        run_id = str(event.get("run_id"))
                    if event.get("type") == "message_delta" and run_id:
                        await run_cancel_registry.request_cancel(run_id)

            types = [e.get("type") for e in events]
            self.assertIn("run_start", types)
            self.assertIn("cancelled", types)
            self.assertIn("done", types)
            done = next(e for e in events if e.get("type") == "done")
            self.assertEqual(done.get("status"), "cancelled")
            assert run_id is not None
            row = await repo.get_run(session, run_id)
            assert row is not None
            self.assertEqual(row.status, "cancelled")

    async def test_run_timeout_fails(self):
        llm = ScriptedLLM([{"type": "slow"}])
        async with self.Session() as session:
            agent, session_id = await self._create_agent_session(
                session,
                runtime=AgentRuntimeConfig(run_timeout_seconds=0.05),
            )
            events: list[dict[str, Any]] = []
            with patch(
                "agent_factory_server.runtime.loop.resolve_llm_client",
                new=AsyncMock(side_effect=_mock_resolve(llm)),
            ):
                async for event in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message="hello",
                ):
                    events.append(event)

            done = next(e for e in events if e.get("type") == "done")
            self.assertEqual(done.get("status"), "failed")
            err = next(e for e in events if e.get("type") == "error")
            self.assertEqual(err.get("code"), "timeout")
            run_id = done.get("run_id")
            assert run_id
            row = await repo.get_run(session, str(run_id))
            assert row is not None
            self.assertEqual(row.status, "failed")
            self.assertEqual(row.finish_reason, "timeout")

    async def test_client_tool_errors_when_unbound_and_multiple_bridges(self):
        llm = ScriptedLLM(
            [
                {"type": "tool", "name": "echo", "arguments": {"text": "hi"}},
                {"type": "text", "content": "done"},
            ]
        )
        async with self.Session() as session:
            agent, session_id = await self._create_agent_session(
                session,
                tools=[
                    ToolDef(
                        name="echo",
                        description="echo",
                        execution="client",
                        parameters={
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    )
                ],
                client_device_id=None,
            )

            class FakeHub:
                def online_ids(self):
                    return ["dev-a", "dev-b"]

            events: list[dict[str, Any]] = []
            with (
                patch(
                    "agent_factory_server.runtime.loop.resolve_llm_client",
                    new=AsyncMock(side_effect=_mock_resolve(llm)),
                ),
                patch(
                    "agent_factory_server.tools.client_hub.client_hub",
                    FakeHub(),
                ),
            ):
                async for event in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message="call echo",
                ):
                    events.append(event)

            tool_results = [e for e in events if e.get("type") == "tool_result"]
            self.assertTrue(tool_results)
            payload = tool_results[0].get("result") or {}
            self.assertFalse(payload.get("ok", True))
            self.assertIn("client_device_id", str(payload.get("error") or ""))
            self.assertIn("bridges are online", str(payload.get("error") or ""))

    async def test_client_tool_errors_when_bound_device_offline(self):
        llm = ScriptedLLM(
            [
                {"type": "tool", "name": "echo", "arguments": {"text": "hi"}},
                {"type": "text", "content": "done"},
            ]
        )
        async with self.Session() as session:
            agent, session_id = await self._create_agent_session(
                session,
                tools=[
                    ToolDef(
                        name="echo",
                        description="echo",
                        execution="client",
                        parameters={
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    )
                ],
                client_device_id="bound-mac",
            )
            events: list[dict[str, Any]] = []
            with patch(
                "agent_factory_server.runtime.loop.resolve_llm_client",
                new=AsyncMock(side_effect=_mock_resolve(llm)),
            ):
                async for event in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message="call echo",
                ):
                    events.append(event)

            tool_results = [e for e in events if e.get("type") == "tool_result"]
            self.assertTrue(tool_results)
            payload = tool_results[0].get("result") or {}
            self.assertFalse(payload.get("ok", True))
            self.assertIn("offline", str(payload.get("error") or ""))
            self.assertIn("bound-mac", str(payload.get("error") or ""))

    async def test_skill_load_then_business_tool_on_timeline(self):
        """Positive path: skill_load → get_cwd; timeline keeps both tool steps."""
        llm = ScriptedLLM(
            [
                {
                    "type": "tool",
                    "name": SKILL_LOAD_TOOL_NAME,
                    "arguments": {"name": "workspace-scout"},
                    "call_id": "call_skill_1",
                },
                {
                    "type": "tool",
                    "name": "get_cwd",
                    "arguments": {},
                    "call_id": "call_cwd_1",
                },
                {"type": "text", "content": "### Workspace Report\n- cwd: /tmp/demo"},
            ]
        )
        skill_body = "# Workspace Scout\n\n### Workspace Report\n"

        class FakeHub:
            def online_ids(self):
                return ["demo-mac"]

            async def call_tool(
                self,
                *,
                device_id,
                call_id,
                name,
                arguments,
                session_id,
                timeout,
            ):
                if name == SKILL_LOAD_TOOL_NAME:
                    return {
                        "ok": True,
                        "data": {
                            "name": arguments.get("name"),
                            "skill_md": skill_body,
                            "files": ["SKILL.md", "reference.md"],
                        },
                    }
                if name == "get_cwd":
                    return {"ok": True, "data": {"cwd": "/tmp/demo"}}
                return {"ok": False, "error": f"unexpected tool: {name}"}

        async with self.Session() as session:
            agent, session_id = await self._create_agent_session(
                session,
                tools=[
                    ToolDef(
                        name="get_cwd",
                        description="cwd",
                        execution="client",
                        parameters={"type": "object", "properties": {}},
                    )
                ],
                skills=[
                    SkillDef(
                        name="workspace-scout",
                        description="local workspace scout",
                    )
                ],
                client_device_id="demo-mac",
            )
            events: list[dict[str, Any]] = []
            with (
                patch(
                    "agent_factory_server.runtime.loop.resolve_llm_client",
                    new=AsyncMock(side_effect=_mock_resolve(llm)),
                ),
                patch(
                    "agent_factory_server.tools.executors.client_hub",
                    FakeHub(),
                ),
            ):
                async for event in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message=(
                        '请先调用 skill_load(name="workspace-scout")，'
                        "再严格按加载到的 skill 流程处理下面的用户请求。\n\n"
                        "看看当前目录"
                    ),
                ):
                    events.append(event)

            tool_starts = [e for e in events if e.get("type") == "tool_call"]
            names = [e.get("name") for e in tool_starts]
            self.assertIn(SKILL_LOAD_TOOL_NAME, names)
            self.assertIn("get_cwd", names)

            tool_results = [e for e in events if e.get("type") == "tool_result"]
            by_name = {e.get("name"): e.get("result") or {} for e in tool_results}
            self.assertTrue(by_name[SKILL_LOAD_TOOL_NAME].get("ok"))
            self.assertIn(
                "### Workspace Report",
                str((by_name[SKILL_LOAD_TOOL_NAME].get("data") or {}).get("skill_md")),
            )
            self.assertTrue(by_name["get_cwd"].get("ok"))

            timeline = await repo.get_session_timeline(session, session_id)
            step_names = [
                step.name
                for run in timeline.runs
                for step in run.steps
                if step.type in {"tool_call", "tool_result"}
            ]
            self.assertIn(SKILL_LOAD_TOOL_NAME, step_names)

            done = next(e for e in events if e.get("type") == "done")
            self.assertEqual(done.get("status"), "completed")

    async def test_skill_load_errors_when_bridge_offline(self):
        llm = ScriptedLLM(
            [
                {
                    "type": "tool",
                    "name": SKILL_LOAD_TOOL_NAME,
                    "arguments": {"name": "workspace-scout"},
                },
                {"type": "text", "content": "bridge offline, cannot load skill"},
            ]
        )
        async with self.Session() as session:
            agent, session_id = await self._create_agent_session(
                session,
                skills=[
                    SkillDef(
                        name="workspace-scout",
                        description="local workspace scout",
                    )
                ],
                client_device_id="offline-mac",
            )
            events: list[dict[str, Any]] = []
            with patch(
                "agent_factory_server.runtime.loop.resolve_llm_client",
                new=AsyncMock(side_effect=_mock_resolve(llm)),
            ):
                async for event in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message='请先调用 skill_load(name="workspace-scout")',
                ):
                    events.append(event)

            tool_results = [e for e in events if e.get("type") == "tool_result"]
            self.assertTrue(tool_results)
            payload = tool_results[0].get("result") or {}
            self.assertEqual(tool_results[0].get("name"), SKILL_LOAD_TOOL_NAME)
            self.assertFalse(payload.get("ok", True))
            self.assertIn("offline", str(payload.get("error") or ""))
            # Failure is surfaced as tool_result — model must not receive a fake body.
            self.assertNotIn("skill_md", payload)

    async def test_context_overflow_fails_run_with_code(self):
        from agent_factory_server.runtime.context import CompactReport

        llm = ScriptedLLM([{"type": "text", "content": "should not run"}])
        overflow_report = CompactReport(
            estimated_tokens_before=9000,
            estimated_tokens_after=8800,
            budget=2000,
            soft=1400,
            hard=1700,
            layers_applied=["L1", "L2", "L3", "L4"],
            overflow=True,
        )

        async def _fake_assemble(*_args, **_kwargs):
            return ([{"role": "system", "content": "x"}], overflow_report)

        async with self.Session() as session:
            agent, session_id = await self._create_agent_session(session)
            events: list[dict[str, Any]] = []
            with (
                patch(
                    "agent_factory_server.runtime.loop.resolve_llm_client",
                    new=AsyncMock(side_effect=_mock_resolve(llm)),
                ),
                patch(
                    "agent_factory_server.runtime.loop.assemble_llm_context",
                    new=AsyncMock(side_effect=_fake_assemble),
                ),
            ):
                async for event in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message="too long",
                ):
                    events.append(event)

            err = next(e for e in events if e.get("type") == "error")
            self.assertEqual(err.get("code"), "context_overflow")
            done = next(e for e in events if e.get("type") == "done")
            self.assertEqual(done.get("status"), "failed")
            self.assertEqual(llm.calls, 0)
            compacted = [e for e in events if e.get("type") == "context_compacted"]
            self.assertTrue(compacted)
            self.assertTrue(compacted[0].get("overflow"))
