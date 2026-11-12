from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional, Union

from agent_factory_server.db import repo
from agent_factory_server.db.session import SessionLocal
from agent_factory_server.domain.schemas import AgentOut, SessionQueueItemOut
from agent_factory_server.runtime.events import EventHub
from agent_factory_server.runtime.hitl import (
    HITL_KIND_APPROVAL,
    ApprovalDecision,
    hitl_kind_from_item,
    normalize_approval_decision,
)
from agent_factory_server.runtime.loop import (
    resume_after_approval,
    resume_after_ask_user,
    run_chat_loop,
)

logger = logging.getLogger(__name__)


class RunConflictError(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"session already has a running run: {run_id}")
        self.run_id = run_id


class WaitingHumanError(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"session is waiting_human: {run_id}")
        self.run_id = run_id


@dataclass
class ChatStarted:
    hub: EventHub
    session_id: str


@dataclass
class ChatEnqueued:
    session_id: str
    item: SessionQueueItemOut
    queue: list[SessionQueueItemOut]


ChatSubmitResult = Union[ChatStarted, ChatEnqueued]


class RunSupervisor:
    """Owns background chat runs so SSE disconnect does not stop work."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._hubs: dict[str, EventHub] = {}
        self._session_to_run: dict[str, str] = {}
        self._starting_sessions: set[str] = set()
        self._dispatching_sessions: set[str] = set()

    def is_active(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    def active_run_for_session(self, session_id: str) -> Optional[str]:
        run_id = self._session_to_run.get(session_id)
        if run_id and self.is_active(run_id):
            return run_id
        return None

    def get_hub(self, run_id: str) -> Optional[EventHub]:
        return self._hubs.get(run_id)

    async def heal_orphaned_runs(self, *, session_id: Optional[str] = None) -> int:
        """Mark DB 'running' rows with no live task as interrupted (e.g. process restart).

        waiting_human is intentional parking — never healed as failed.
        """
        async with SessionLocal() as session:
            await repo.expire_waiting_human_runs(session)
            if session_id:
                existing = await repo.get_running_run(session, session_id)
                rows = [existing] if existing else []
            else:
                rows = await repo.list_running_runs(session)

            count = 0
            for run in rows:
                if self.is_active(run.id) or run.session_id in self._starting_sessions:
                    continue
                await repo.finalize_run(
                    session,
                    run.id,
                    status="failed",
                    finish_reason="error",
                    error_message="服务重启或任务中断，未完成的 Run 已标记失败",
                )
                await repo.repair_orphan_tool_calls(session, run.id)
                count += 1
            return count

    def _track_worker(
        self,
        *,
        session_id: str,
        agent: AgentOut,
        hub: EventHub,
        worker_coro,
        name: str,
        queue_item_id: Optional[str] = None,
    ) -> EventHub:
        self._starting_sessions.add(session_id)
        terminal_status: Optional[str] = None

        async def _worker() -> None:
            nonlocal terminal_status
            run_id: Optional[str] = None
            try:
                async with SessionLocal() as session:
                    async for event in worker_coro(session):
                        if event.get("type") == "run_start" and isinstance(
                            event.get("run_id"), str
                        ):
                            run_id = event["run_id"]
                            self._tasks[run_id] = task
                            self._hubs[run_id] = hub
                            self._session_to_run[session_id] = run_id
                        if event.get("type") == "done":
                            terminal_status = str(event.get("status") or "")
                        hub.publish(event)
            except Exception as exc:  # noqa: BLE001
                logger.exception("background chat run crashed")
                hub.publish({"type": "error", "error": str(exc), "run_id": run_id})
                hub.publish(
                    {
                        "type": "done",
                        "session_id": session_id,
                        "run_id": run_id,
                        "status": "failed",
                    }
                )
                terminal_status = "failed"
            finally:
                hub.close()
                self._starting_sessions.discard(session_id)
                if run_id:
                    self._tasks.pop(run_id, None)
                    if self._session_to_run.get(session_id) == run_id:
                        self._session_to_run.pop(session_id, None)

                    async def _drop_hub(
                        kept_id: str = run_id, kept_hub: EventHub = hub
                    ) -> None:
                        await asyncio.sleep(120)
                        if self._hubs.get(kept_id) is kept_hub:
                            self._hubs.pop(kept_id, None)

                    asyncio.create_task(_drop_hub(), name=f"drop-hub:{run_id}")
                elif queue_item_id:
                    # Claimed but never reached run_start — put back on queue.
                    try:
                        async with SessionLocal() as session:
                            await repo.requeue_item(session, queue_item_id)
                    except Exception:  # noqa: BLE001
                        logger.exception("failed to requeue item %s", queue_item_id)

                # Auto-continue FIFO when this run truly finished (not parked for human).
                if terminal_status and terminal_status != "waiting_human":
                    asyncio.create_task(
                        self._dispatch_next(session_id=session_id, agent=agent),
                        name=f"dispatch-next:{session_id}",
                    )

        task = asyncio.create_task(_worker(), name=name)
        return hub

    def _start_worker_locked(
        self,
        *,
        agent: AgentOut,
        session_id: str,
        user_message: str,
        model_profile_id: Optional[str] = None,
        queue_item_id: Optional[str] = None,
    ) -> EventHub:
        hub = EventHub()
        qid = queue_item_id

        async def _coro(session):
            async for event in run_chat_loop(
                session,
                agent=agent,
                session_id=session_id,
                user_message=user_message,
                model_profile_id=model_profile_id,
                queue_item_id=qid,
            ):
                yield event

        return self._track_worker(
            session_id=session_id,
            agent=agent,
            hub=hub,
            worker_coro=_coro,
            name=f"chat-run:{session_id}",
            queue_item_id=qid,
        )

    async def enqueue_or_start(
        self,
        *,
        agent: AgentOut,
        session_id: str,
        user_message: str,
        model_profile_id: Optional[str] = None,
    ) -> ChatSubmitResult:
        """Start immediately if idle; otherwise append to session FIFO queue."""
        async with self._lock:
            await self.heal_orphaned_runs(session_id=session_id)

            async with SessionLocal() as session:
                waiting = await repo.get_waiting_human_run(session, session_id)
                if waiting:
                    raise WaitingHumanError(waiting.id)

                busy = (
                    session_id in self._starting_sessions
                    or session_id in self._dispatching_sessions
                    or self.active_run_for_session(session_id) is not None
                    or (await repo.get_running_run(session, session_id)) is not None
                )
                if busy:
                    item = await repo.enqueue_session_message(
                        session,
                        session_id=session_id,
                        message=user_message,
                        model_profile_id=model_profile_id,
                    )
                    queue = await repo.list_queued_items(session, session_id)
                    return ChatEnqueued(session_id=session_id, item=item, queue=queue)

            hub = self._start_worker_locked(
                agent=agent,
                session_id=session_id,
                user_message=user_message,
                model_profile_id=model_profile_id,
            )
            return ChatStarted(hub=hub, session_id=session_id)

    async def start_chat(
        self,
        *,
        agent: AgentOut,
        session_id: str,
        user_message: str,
        model_profile_id: Optional[str] = None,
    ) -> EventHub:
        """Legacy helper: start only; raises if busy. Prefer enqueue_or_start."""
        result = await self.enqueue_or_start(
            agent=agent,
            session_id=session_id,
            user_message=user_message,
            model_profile_id=model_profile_id,
        )
        if isinstance(result, ChatEnqueued):
            raise RunConflictError("queued")
        return result.hub

    async def kick_session_queue(self, *, session_id: str, agent: AgentOut) -> None:
        """Public hook after waiting_human cancel / external unblock."""
        await self._dispatch_next(session_id=session_id, agent=agent)

    async def _dispatch_next(self, *, session_id: str, agent: AgentOut) -> None:
        async with self._lock:
            if session_id in self._starting_sessions or session_id in self._dispatching_sessions:
                return
            if self.active_run_for_session(session_id):
                return

            self._dispatching_sessions.add(session_id)
            try:
                async with SessionLocal() as session:
                    await repo.expire_waiting_human_runs(session)
                    if await repo.get_waiting_human_run(session, session_id):
                        return
                    if await repo.get_running_run(session, session_id):
                        return
                    # Reload agent in case definition changed.
                    live = await repo.get_agent(session, agent.id)
                    if live is None:
                        return
                    item = await repo.claim_next_queue_item(session, session_id)
                    if not item:
                        return
                    agent = live
                    message = item.message
                    model_profile_id = item.model_profile_id
                    queue_item_id = item.id

                self._start_worker_locked(
                    agent=agent,
                    session_id=session_id,
                    user_message=message,
                    model_profile_id=model_profile_id,
                    queue_item_id=queue_item_id,
                )
            finally:
                self._dispatching_sessions.discard(session_id)

    async def resume_idle_queues(self) -> int:
        """After restart: heal running, then dispatch queued sessions that are idle."""
        await self.heal_orphaned_runs()
        started = 0
        async with SessionLocal() as session:
            session_ids = await repo.list_session_ids_with_queued(session)
        for session_id in session_ids:
            async with SessionLocal() as session:
                if await repo.get_running_run(session, session_id):
                    continue
                if await repo.get_waiting_human_run(session, session_id):
                    continue
                row = await repo.get_session_row(session, session_id)
                if not row:
                    continue
                agent = await repo.get_agent(session, row.agent_id)
                if not agent:
                    continue
            await self._dispatch_next(session_id=session_id, agent=agent)
            started += 1
        return started

    async def resume_chat(
        self,
        *,
        agent: AgentOut,
        run_id: str,
        call_id: str,
        answer: Optional[str] = None,
        selected_option: Optional[str] = None,
        decision: Optional[str] = None,
        edited_arguments: Optional[dict[str, Any]] = None,
        model_profile_id: Optional[str] = None,
    ) -> EventHub:
        async with self._lock:
            async with SessionLocal() as session:
                await repo.expire_waiting_human_runs(session)
                run = await repo.get_run(session, run_id)
                if not run:
                    raise ValueError("Run not found")
                if run.status != "waiting_human":
                    raise ValueError(f"run is not waiting_human (status={run.status})")
                session_id = run.session_id
                pending = await repo.find_pending_hitl_call(
                    session, run_id, call_id=call_id
                )
                if not pending:
                    raise ValueError("pending HITL call not found")
                kind = hitl_kind_from_item(
                    name=pending.name, payload_json=pending.payload_json
                )

            if self.is_active(run_id):
                raise RunConflictError(run_id)
            if session_id in self._starting_sessions:
                raise RunConflictError("starting")
            active = self.active_run_for_session(session_id)
            if active:
                raise RunConflictError(active)

            hub = EventHub()
            approval_decision: Optional[ApprovalDecision] = None
            if kind == HITL_KIND_APPROVAL:
                approval_decision = normalize_approval_decision(decision)
                if approval_decision is None:
                    raise ValueError(
                        "decision must be approve, edit, or reject for tool approval"
                    )

            async def _coro(session):
                if kind == HITL_KIND_APPROVAL:
                    assert approval_decision is not None
                    async for event in resume_after_approval(
                        session,
                        agent=agent,
                        run_id=run_id,
                        call_id=call_id,
                        decision=approval_decision,
                        edited_arguments=edited_arguments,
                        model_profile_id=model_profile_id,
                    ):
                        yield event
                else:
                    async for event in resume_after_ask_user(
                        session,
                        agent=agent,
                        run_id=run_id,
                        call_id=call_id,
                        answer=answer,
                        selected_option=selected_option,
                        model_profile_id=model_profile_id,
                    ):
                        yield event

            return self._track_worker(
                session_id=session_id,
                agent=agent,
                hub=hub,
                worker_coro=_coro,
                name=f"chat-resume:{run_id}",
            )


run_supervisor = RunSupervisor()
