from __future__ import annotations

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
    ToolDef,
)
from agent_factory_server.runtime.hitl import (
    HITL_KIND_APPROVAL,
    tool_requires_approval,
)
from agent_factory_server.runtime.loop import (
    resume_after_approval,
    run_chat_loop,
)


class ScriptedLLM:
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
            yield {"type": "message_delta", "delta": content}
            yield {
                "type": "complete",
                "message": {"role": "assistant", "content": content},
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
                            "id": str(step.get("call_id") or "call_approval_1"),
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
        raise AssertionError(f"unknown script step: {step}")


def _mock_resolve(llm: ScriptedLLM):
    async def _resolve(*_args, **_kwargs):
        return (llm, "mock-model", 0.0, False, 128000, 8192, None, 0.0, 0.0)

    return _resolve


class ApprovalHelpersTest(unittest.TestCase):
    def test_require_approval_ignored_for_builtin(self):
        tool = ToolDef(
            name="memory_write",
            description="d",
            execution="builtin",
            require_approval=True,
        )
        self.assertFalse(tool_requires_approval(tool))

    def test_require_approval_for_client(self):
        tool = ToolDef(
            name="list_dir",
            description="d",
            execution="client",
            require_approval=True,
        )
        self.assertTrue(tool_requires_approval(tool))


class ApprovalLoopTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed(self, session: AsyncSession) -> tuple[Any, str]:
        agent = await repo.create_agent(
            session,
            AgentCreate(
                name="approval-agent",
                system_prompt="helpful",
                tools=[
                    ToolDef(
                        name="list_dir",
                        description="list",
                        execution="client",
                        require_approval=True,
                        parameters={
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    )
                ],
                runtime=AgentRuntimeConfig(run_timeout_seconds=30),
            ),
        )
        sess = await repo.create_session(
            session,
            SessionCreate(
                agent_id=agent.id,
                user_key="u1",
                title="t",
                client_device_id="demo-mac",
            ),
            system_prompt="helpful",
            prompt_vars={},
            locale="zh",
        )
        return agent, sess.id

    async def test_require_approval_pauses_before_execute(self):
        llm = ScriptedLLM(
            [
                {
                    "type": "tool",
                    "name": "list_dir",
                    "arguments": {"path": "."},
                    "call_id": "c_appr",
                }
            ]
        )
        async with self.Session() as session:
            agent, session_id = await self._seed(session)
            events: list[dict[str, Any]] = []
            with (
                patch(
                    "agent_factory_server.runtime.loop.resolve_llm_client",
                    new=AsyncMock(side_effect=_mock_resolve(llm)),
                ),
                patch(
                    "agent_factory_server.tools.executors.execute_tool",
                    new=AsyncMock(side_effect=AssertionError("must not execute yet")),
                ),
            ):
                async for event in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message="list files",
                ):
                    events.append(event)

            types = [e.get("type") for e in events]
            self.assertIn("tool_approval_required", types)
            self.assertIn("done", types)
            done = next(e for e in events if e.get("type") == "done")
            self.assertEqual(done.get("status"), "waiting_human")
            approval = next(e for e in events if e.get("type") == "tool_approval_required")
            self.assertEqual(approval.get("name"), "list_dir")
            self.assertEqual(approval.get("call_id"), "c_appr")

            run_id = str(done.get("run_id"))
            pending = await repo.find_pending_approval_call(session, run_id)
            assert pending is not None
            payload = json.loads(pending.payload_json or "{}")
            self.assertEqual(payload.get("hitl"), HITL_KIND_APPROVAL)

    async def test_approve_executes_tool_and_continues(self):
        llm = ScriptedLLM(
            [
                {
                    "type": "tool",
                    "name": "list_dir",
                    "arguments": {"path": "."},
                    "call_id": "c_ok",
                },
                {"type": "text", "content": "listed"},
            ]
        )

        class FakeHub:
            def online_ids(self):
                return ["demo-mac"]

            async def call_tool(self, **_kwargs):
                return {"ok": True, "data": {"entries": ["a"]}}

        async with self.Session() as session:
            agent, session_id = await self._seed(session)
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
                pause_events: list[dict[str, Any]] = []
                async for event in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message="list",
                ):
                    pause_events.append(event)
                run_id = next(
                    e.get("run_id") for e in pause_events if e.get("type") == "done"
                )
                assert run_id

                resume_events: list[dict[str, Any]] = []
                async for event in resume_after_approval(
                    session,
                    agent=agent,
                    run_id=str(run_id),
                    call_id="c_ok",
                    decision="approve",
                ):
                    resume_events.append(event)

            tool_results = [
                e for e in resume_events if e.get("type") == "tool_result"
            ]
            self.assertTrue(tool_results)
            self.assertTrue((tool_results[0].get("result") or {}).get("ok"))
            done = next(e for e in resume_events if e.get("type") == "done")
            self.assertEqual(done.get("status"), "completed")
            messages = [
                e for e in resume_events if e.get("type") == "message_done"
            ]
            self.assertTrue(any("listed" in str(e.get("content")) for e in messages))

    async def test_reject_skips_execute(self):
        llm = ScriptedLLM(
            [
                {
                    "type": "tool",
                    "name": "list_dir",
                    "arguments": {"path": "/tmp"},
                    "call_id": "c_rej",
                },
                {"type": "text", "content": "understood rejection"},
            ]
        )
        executed = {"n": 0}

        class FakeHub:
            def online_ids(self):
                return ["demo-mac"]

            async def call_tool(self, **_kwargs):
                executed["n"] += 1
                return {"ok": True, "data": {}}

        async with self.Session() as session:
            agent, session_id = await self._seed(session)
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
                async for _ in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message="list",
                ):
                    pass
                run = await repo.get_waiting_human_run(session, session_id)
                assert run is not None
                events: list[dict[str, Any]] = []
                async for event in resume_after_approval(
                    session,
                    agent=agent,
                    run_id=run.id,
                    call_id="c_rej",
                    decision="reject",
                ):
                    events.append(event)

            self.assertEqual(executed["n"], 0)
            result_ev = next(e for e in events if e.get("type") == "tool_result")
            payload = result_ev.get("result") or {}
            self.assertFalse(payload.get("ok", True))
            self.assertTrue(payload.get("rejected"))
            done = next(e for e in events if e.get("type") == "done")
            self.assertEqual(done.get("status"), "completed")

    async def test_edit_uses_edited_arguments(self):
        llm = ScriptedLLM(
            [
                {
                    "type": "tool",
                    "name": "list_dir",
                    "arguments": {"path": "."},
                    "call_id": "c_edit",
                },
                {"type": "text", "content": "edited ok"},
            ]
        )
        seen_args: dict[str, Any] = {}

        class FakeHub:
            def online_ids(self):
                return ["demo-mac"]

            async def call_tool(self, **kwargs):
                seen_args.update(kwargs.get("arguments") or {})
                return {"ok": True, "data": {"entries": ["x"]}}

        async with self.Session() as session:
            agent, session_id = await self._seed(session)
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
                async for _ in run_chat_loop(
                    session,
                    agent=agent,
                    session_id=session_id,
                    user_message="list",
                ):
                    pass
                run = await repo.get_waiting_human_run(session, session_id)
                assert run is not None
                events: list[dict[str, Any]] = []
                async for event in resume_after_approval(
                    session,
                    agent=agent,
                    run_id=run.id,
                    call_id="c_edit",
                    decision="edit",
                    edited_arguments={"path": "/edited"},
                ):
                    events.append(event)

            self.assertEqual(seen_args.get("path"), "/edited")
            result_ev = next(e for e in events if e.get("type") == "tool_result")
            self.assertTrue((result_ev.get("result") or {}).get("edited"))


if __name__ == "__main__":
    unittest.main()
