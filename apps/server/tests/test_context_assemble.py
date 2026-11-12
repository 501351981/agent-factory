from __future__ import annotations

import unittest
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import AgentCreate, ContextPolicy, SessionCreate
from agent_factory_server.runtime.context import assemble_llm_context


def _tool_payload(call_id: str, name: str = "list_dir") -> dict[str, Any]:
    return {
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ]
    }


class AssembleContextTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _session(self) -> tuple[AsyncSession, str]:
        db = self.Session()
        agent = await repo.create_agent(
            db, AgentCreate(name="ctx", system_prompt="sys", tools=[])
        )
        sess = await repo.create_session(
            db,
            SessionCreate(agent_id=agent.id, user_key="u1", title="t"),
            system_prompt="sys",
            prompt_vars={},
            locale="zh",
        )
        return db, sess.id

    async def _add_tool_run(
        self,
        db: AsyncSession,
        session_id: str,
        *,
        user_input: str,
        tool_body: str,
        call_id: str,
        finalize: bool = True,
    ) -> str:
        run = await repo.create_run(db, session_id=session_id, user_input=user_input)
        await repo.add_item(
            db,
            run_id=run.id,
            session_id=session_id,
            type="user_message",
            content=user_input,
        )
        await repo.add_item(
            db,
            run_id=run.id,
            session_id=session_id,
            type="assistant_message",
            content=None,
            payload=_tool_payload(call_id),
        )
        await repo.add_item(
            db,
            run_id=run.id,
            session_id=session_id,
            type="tool_result",
            call_id=call_id,
            name="list_dir",
            content=tool_body,
            payload={"ok": True},
        )
        await repo.add_item(
            db,
            run_id=run.id,
            session_id=session_id,
            type="assistant_message",
            content=f"done:{user_input}",
            payload={},
        )
        if finalize:
            await repo.finalize_run(db, run.id, status="completed", finish_reason="stop")
        return run.id

    def _tight_policy(self, **overrides: Any) -> ContextPolicy:
        # Keep tool_result_max_chars high so L1 alone cannot drop below soft/hard;
        # otherwise assemble returns early with layers=["L1"] and never exercises L2+.
        base = dict(
            mode="auto",
            soft_ratio=0.25,
            hard_ratio=0.40,
            keep_last_runs=1,
            tool_result_max_chars=5000,
            summarize_enabled=True,
            summarize_max_input_chars=8000,
            chars_per_token=4.0,
        )
        base.update(overrides)
        return ContextPolicy(**base)

    async def test_soft_applies_l2_folds_old_tools_keeps_current(self):
        db, session_id = await self._session()
        try:
            big = "Y" * 4000
            old_a = await self._add_tool_run(
                db, session_id, user_input="old-a", tool_body=big, call_id="c_old_a"
            )
            old_b = await self._add_tool_run(
                db, session_id, user_input="old-b", tool_body=big, call_id="c_old_b"
            )
            current = await self._add_tool_run(
                db,
                session_id,
                user_input="current",
                tool_body=big,
                call_id="c_cur",
                finalize=False,
            )

            # Soft but not hard: L2 folds old tools enough to stay under hard.
            policy = self._tight_policy(soft_ratio=0.15, hard_ratio=0.95)
            messages, report = await assemble_llm_context(
                db,
                session_id=session_id,
                system_prompt="sys",
                current_run_id=current,
                policy=policy,
                context_window_tokens=6000,
                reserve_output_tokens=200,
                llm=None,
                model_name=None,
            )

            self.assertIn("L1", report.layers_applied)
            self.assertIn("L2", report.layers_applied)
            self.assertNotIn("L3", report.layers_applied)
            self.assertNotIn("L4", report.layers_applied)
            self.assertGreaterEqual(report.estimated_tokens_before, report.soft)
            self.assertLess(report.estimated_tokens_after, report.hard)

            # Current run tool pair intact in projection.
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            self.assertTrue(tool_msgs)
            self.assertTrue(any(m.get("tool_call_id") == "c_cur" for m in tool_msgs))

            # Older runs folded: raw old tool ids should not appear.
            tool_ids = {m.get("tool_call_id") for m in tool_msgs}
            self.assertNotIn("c_old_a", tool_ids)
            self.assertNotIn("c_old_b", tool_ids)
            compacted = [
                m
                for m in messages
                if m.get("role") == "assistant" and "compacted" in str(m.get("content") or "")
            ]
            self.assertTrue(compacted)

            # Timeline / DB still hold full tool bodies (not truncated at write).
            timeline = await repo.get_session_timeline(db, session_id)
            full_results = []
            for run in timeline.runs:
                for step in run.steps:
                    if step.type == "tool_result":
                        full_results.append(step.content or "")
            self.assertTrue(any(big in (c or "") for c in full_results))
            self.assertTrue(any(len(c or "") >= 4000 for c in full_results))
            _ = old_a, old_b
        finally:
            await db.close()

    async def test_hard_triggers_l4_summary_reusable(self):
        db, session_id = await self._session()
        try:
            big = "Z" * 6000
            await self._add_tool_run(
                db, session_id, user_input="hist-1", tool_body=big, call_id="c1"
            )
            await self._add_tool_run(
                db, session_id, user_input="hist-2", tool_body=big, call_id="c2"
            )
            await self._add_tool_run(
                db, session_id, user_input="hist-3", tool_body=big, call_id="c3"
            )
            current = await self._add_tool_run(
                db,
                session_id,
                user_input="now",
                tool_body=big,
                call_id="c_now",
                finalize=False,
            )

            policy = self._tight_policy()
            messages1, report1 = await assemble_llm_context(
                db,
                session_id=session_id,
                system_prompt="sys",
                current_run_id=current,
                policy=policy,
                context_window_tokens=3500,
                reserve_output_tokens=200,
                llm=None,
                model_name=None,
            )

            self.assertIn("L2", report1.layers_applied)
            self.assertIn("L3", report1.layers_applied)
            self.assertIn("L4", report1.layers_applied)
            self.assertTrue(report1.covered_run_ids)
            self.assertIsNotNone(report1.covered_until_run_id)
            self.assertFalse(report1.summarize_failed)

            summary_msgs = [
                m
                for m in messages1
                if m.get("role") == "system"
                and str(m.get("content") or "").startswith("# Prior context summary")
            ]
            self.assertEqual(len(summary_msgs), 1)
            self.assertIn("hist-1", summary_msgs[0]["content"])

            stored = await repo.get_latest_context_summary(db, session_id)
            assert stored is not None
            first_summary_id = stored.id
            first_covered = list(report1.covered_run_ids)

            # Second assemble: reuse watermark; no duplicate summary item.
            messages2, report2 = await assemble_llm_context(
                db,
                session_id=session_id,
                system_prompt="sys",
                current_run_id=current,
                policy=policy,
                context_window_tokens=3500,
                reserve_output_tokens=200,
                llm=None,
                model_name=None,
            )
            latest = await repo.get_latest_context_summary(db, session_id)
            assert latest is not None
            self.assertEqual(latest.id, first_summary_id)
            self.assertEqual(sorted(report2.covered_run_ids), sorted(first_covered))
            self.assertNotIn("L4", report2.layers_applied)

            # Current run still projected with tool role.
            self.assertTrue(
                any(
                    m.get("role") == "tool" and m.get("tool_call_id") == "c_now"
                    for m in messages2
                )
            )
        finally:
            await db.close()

    async def test_l4_incremental_covers_new_outside_runs(self):
        db, session_id = await self._session()
        try:
            big = "W" * 5000
            await self._add_tool_run(
                db, session_id, user_input="a", tool_body=big, call_id="ca"
            )
            await self._add_tool_run(
                db, session_id, user_input="b", tool_body=big, call_id="cb"
            )
            current1 = await self._add_tool_run(
                db,
                session_id,
                user_input="c1",
                tool_body=big,
                call_id="cc1",
                finalize=False,
            )
            policy = self._tight_policy()
            _, report1 = await assemble_llm_context(
                db,
                session_id=session_id,
                system_prompt="sys",
                current_run_id=current1,
                policy=policy,
                context_window_tokens=3200,
                reserve_output_tokens=200,
                llm=None,
            )
            self.assertIn("L4", report1.layers_applied)
            covered1 = set(report1.covered_run_ids)

            await repo.finalize_run(
                db, current1, status="completed", finish_reason="stop"
            )
            current2 = await self._add_tool_run(
                db,
                session_id,
                user_input="c2",
                tool_body=big,
                call_id="cc2",
                finalize=False,
            )
            _, report2 = await assemble_llm_context(
                db,
                session_id=session_id,
                system_prompt="sys",
                current_run_id=current2,
                policy=policy,
                context_window_tokens=3200,
                reserve_output_tokens=200,
                llm=None,
            )
            # New outside runs should expand the watermark when L4 runs again.
            if "L4" in report2.layers_applied:
                self.assertTrue(set(report2.covered_run_ids) >= covered1)
                self.assertGreaterEqual(
                    len(report2.covered_run_ids), len(covered1)
                )
            else:
                # Already fully covered relative to new window — still ok.
                self.assertTrue(set(report2.covered_run_ids) >= covered1)
        finally:
            await db.close()

    async def test_current_run_open_tool_pair_not_dropped(self):
        db, session_id = await self._session()
        try:
            big = "Q" * 5000
            await self._add_tool_run(
                db, session_id, user_input="old", tool_body=big, call_id="c_old"
            )
            run = await repo.create_run(
                db, session_id=session_id, user_input="open-tool"
            )
            await repo.add_item(
                db,
                run_id=run.id,
                session_id=session_id,
                type="user_message",
                content="open-tool",
            )
            await repo.add_item(
                db,
                run_id=run.id,
                session_id=session_id,
                type="assistant_message",
                content=None,
                payload=_tool_payload("c_open"),
            )
            # Intentionally no tool_result yet (in-flight pair).

            policy = self._tight_policy(soft_ratio=0.05, hard_ratio=0.15)
            messages, report = await assemble_llm_context(
                db,
                session_id=session_id,
                system_prompt="sys",
                current_run_id=run.id,
                policy=policy,
                context_window_tokens=3000,
                reserve_output_tokens=100,
                llm=None,
            )
            self.assertGreaterEqual(len(report.layers_applied), 1)

            # Projection must keep assistant tool_calls for the open pair
            # (and synthesize a missing tool result rather than drop the call).
            assistant_with_tools = [
                m
                for m in messages
                if m.get("role") == "assistant" and m.get("tool_calls")
            ]
            self.assertTrue(
                any(
                    any(
                        (c or {}).get("id") == "c_open"
                        for c in (m.get("tool_calls") or [])
                    )
                    for m in assistant_with_tools
                )
            )
            tool_for_open = [
                m
                for m in messages
                if m.get("role") == "tool" and m.get("tool_call_id") == "c_open"
            ]
            self.assertEqual(len(tool_for_open), 1)
            self.assertIn("missing tool result", tool_for_open[0]["content"])
        finally:
            await db.close()

    async def test_overflow_when_budget_still_exceeded(self):
        db, session_id = await self._session()
        try:
            # One current run with many large tool results; L1 truncates each but
            # aggregate still exceeds a tiny budget.
            run = await repo.create_run(
                db, session_id=session_id, user_input="flood"
            )
            await repo.add_item(
                db,
                run_id=run.id,
                session_id=session_id,
                type="user_message",
                content="flood " + ("x" * 2000),
            )
            for i in range(8):
                cid = f"c{i}"
                await repo.add_item(
                    db,
                    run_id=run.id,
                    session_id=session_id,
                    type="assistant_message",
                    content=None,
                    payload=_tool_payload(cid, name=f"t{i}"),
                )
                await repo.add_item(
                    db,
                    run_id=run.id,
                    session_id=session_id,
                    type="tool_result",
                    call_id=cid,
                    name=f"t{i}",
                    content=("Z" * 3000),
                    payload={"ok": True},
                )

            policy = self._tight_policy(
                soft_ratio=0.2,
                hard_ratio=0.3,
                keep_last_runs=4,
                tool_result_max_chars=2500,
            )
            messages, report = await assemble_llm_context(
                db,
                session_id=session_id,
                system_prompt="sys",
                current_run_id=run.id,
                policy=policy,
                context_window_tokens=1200,
                reserve_output_tokens=200,
                llm=None,
            )
            self.assertTrue(report.overflow)
            self.assertGreater(report.estimated_tokens_after, report.budget)
            self.assertTrue(messages)  # still returns projection for debug
            event = report.to_event(session_id=session_id, run_id=run.id)
            self.assertEqual(event["type"], "context_compacted")
            self.assertTrue(event["overflow"])
        finally:
            await db.close()

    async def test_mode_off_skips_layers_but_flags_overflow(self):
        db, session_id = await self._session()
        try:
            big = "O" * 8000
            await self._add_tool_run(
                db, session_id, user_input="only", tool_body=big, call_id="c0"
            )
            runs = await repo.list_runs_for_session(db, session_id)
            current = runs[-1].id
            policy = self._tight_policy(mode="off", tool_result_max_chars=8000)
            _, report = await assemble_llm_context(
                db,
                session_id=session_id,
                system_prompt="sys",
                current_run_id=current,
                policy=policy,
                context_window_tokens=1500,
                reserve_output_tokens=200,
                llm=None,
            )
            self.assertEqual(report.layers_applied, ["L1"])
            self.assertTrue(report.overflow)
        finally:
            await db.close()


if __name__ == "__main__":
    unittest.main()
