from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import (
    AuthContext,
    enforce_agent_scope,
    enforce_user_key_binding,
    require_caller,
)
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import ChatRequest, RunResumeRequest, SessionCreate
from agent_factory_server.runtime.cancel import run_cancel_registry
from agent_factory_server.runtime.hitl import parse_ask_user_arguments
from agent_factory_server.runtime.locale import (
    normalize_locale,
    pick_system_prompt_template,
)
from agent_factory_server.runtime.prompt_template import (
    normalize_prompt_vars,
    render_prompt_template,
)
from agent_factory_server.runtime.runner import (
    ChatEnqueued,
    ChatStarted,
    RunConflictError,
    WaitingHumanError,
    run_supervisor,
)

router = APIRouter(prefix="/api", tags=["chat"])


def _sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> StreamingResponse:
    enforce_agent_scope(auth, payload.agent_id)
    agent = await repo.get_agent(session, payload.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    session_id = payload.session_id
    if session_id:
        row = await repo.get_session_row(session, session_id)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        if row.agent_id != payload.agent_id:
            raise HTTPException(status_code=400, detail="session/agent mismatch")
        enforce_agent_scope(auth, row.agent_id)
        if auth.kind == "embed" and (auth.user_key or "").strip():
            bound = auth.user_key.strip()
            if (row.user_key or "").strip() and (row.user_key or "").strip() != bound:
                raise HTTPException(
                    status_code=403, detail="session user_key does not match embed token"
                )
    else:
        user_key = enforce_user_key_binding(auth, payload.user_key)
        if not user_key:
            raise HTTPException(
                status_code=400, detail="user_key is required when creating a session"
            )
        locale = normalize_locale(payload.locale)
        prompt_vars = normalize_prompt_vars(payload.prompt_vars)
        template = pick_system_prompt_template(
            system_prompt=agent.system_prompt,
            system_prompts=agent.system_prompts,
            locale=locale,
        )
        resolved_system_prompt = render_prompt_template(template, prompt_vars)
        try:
            created = await repo.create_session(
                session,
                SessionCreate(
                    agent_id=payload.agent_id,
                    user_key=user_key,
                    title=payload.message[:40],
                    locale=locale,
                    prompt_vars=prompt_vars,
                    client_device_id=(payload.client_device_id or "").strip() or None,
                ),
                system_prompt=resolved_system_prompt,
                prompt_vars=prompt_vars,
                locale=locale,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session_id = created.id

    if payload.model_profile_id:
        profile = await repo.get_model_profile(session, payload.model_profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Model profile not found")
        if not profile.enabled:
            raise HTTPException(status_code=400, detail="Model profile disabled")

    try:
        result = await run_supervisor.enqueue_or_start(
            agent=agent,
            session_id=session_id,
            user_message=payload.message,
            model_profile_id=payload.model_profile_id,
        )
    except WaitingHumanError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"session is waiting_human: {exc.run_id}; answer or cancel first",
        ) from exc

    if isinstance(result, ChatEnqueued):
        item = result.item.model_dump(mode="json")
        queue = [q.model_dump(mode="json") for q in result.queue]

        async def queued_stream():
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'queue_enqueued', 'session_id': session_id, 'item': item, 'queue': queue}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'status': 'queued'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            queued_stream(),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    assert isinstance(result, ChatStarted)
    hub = result.hub

    async def event_stream():
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        try:
            async for event in hub.subscribe():
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = 0,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> StreamingResponse:
    """Replay buffered events then continue live. Refresh/reconnect without losing stream."""
    row = await repo.get_run(session, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    sess = await repo.get_session_row(session, row.session_id)
    if sess:
        enforce_agent_scope(auth, sess.agent_id)

    hub = run_supervisor.get_hub(run_id)
    if hub is None:
        status = row.status
        session_id = row.session_id

        async def terminal_stream():
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            if status == "running":
                yield f"data: {json.dumps({'type': 'error', 'error': 'run hub unavailable; use timeline', 'code': 'hub_unavailable', 'run_id': run_id}, ensure_ascii=False)}\n\n"
            elif status == "waiting_human":
                from agent_factory_server.runtime.hitl import (
                    HITL_KIND_APPROVAL,
                    hitl_kind_from_item,
                    parse_tool_call_payload,
                )

                pending = await repo.find_pending_hitl_call(session, run_id)
                yield f"data: {json.dumps({'type': 'run_start', 'run_id': run_id, 'session_id': session_id, 'status': status}, ensure_ascii=False)}\n\n"
                if pending and pending.call_id:
                    expires = getattr(row, "waiting_expires_at", None)
                    kind = hitl_kind_from_item(
                        name=pending.name, payload_json=pending.payload_json
                    )
                    call_payload = parse_tool_call_payload(pending.payload_json)
                    args = call_payload.get("arguments") or {}
                    if kind == HITL_KIND_APPROVAL:
                        yield f"data: {json.dumps({'type': 'tool_approval_required', 'run_id': run_id, 'session_id': session_id, 'call_id': pending.call_id, 'name': pending.name or 'tool', 'arguments': args if isinstance(args, dict) else {}, 'expires_at': expires.isoformat() if expires else None}, ensure_ascii=False)}\n\n"
                    else:
                        ask = parse_ask_user_arguments(
                            args if isinstance(args, dict) else {}
                        )
                        yield f"data: {json.dumps({'type': 'human_input_required', 'run_id': run_id, 'session_id': session_id, 'call_id': pending.call_id, 'question': ask['question'], 'options': ask['options'], 'allow_free_text': ask['allow_free_text'], 'expires_at': expires.isoformat() if expires else None}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'run_id': run_id, 'status': 'waiting_human'}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'run_start', 'run_id': run_id, 'session_id': session_id, 'status': status}, ensure_ascii=False)}\n\n"
                if status == "cancelled":
                    yield f"data: {json.dumps({'type': 'cancelled', 'run_id': run_id, 'session_id': session_id}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'run_id': run_id, 'status': status}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            terminal_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    async def event_stream():
        try:
            async for event in hub.subscribe(after=max(0, after)):
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> dict[str, str]:
    row = await repo.get_run(session, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    sess = await repo.get_session_row(session, row.session_id)
    if sess:
        enforce_agent_scope(auth, sess.agent_id)
    if row.status == "waiting_human":
        await repo.cancel_waiting_human_run(
            session,
            run_id,
            reason="cancelled",
            error_message="用户取消等待",
        )
        # Parked runs have no live worker — kick the session queue manually.
        if sess:
            agent = await repo.get_agent(session, sess.agent_id)
            if agent:
                asyncio.create_task(
                    run_supervisor.kick_session_queue(
                        session_id=row.session_id, agent=agent
                    )
                )
        return {"status": "cancelled", "run_id": run_id}
    if row.status != "running":
        return {"status": row.status, "run_id": run_id}
    await run_cancel_registry.request_cancel(run_id)
    return {"status": "cancelling", "run_id": run_id}


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    payload: RunResumeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> StreamingResponse:
    row = await repo.get_run(session, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    sess = await repo.get_session_row(session, row.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    enforce_agent_scope(auth, sess.agent_id)
    agent = await repo.get_agent(session, sess.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if row.status != "waiting_human":
        raise HTTPException(
            status_code=409,
            detail=f"run is not waiting_human (status={row.status})",
        )

    try:
        hub = await run_supervisor.resume_chat(
            agent=agent,
            run_id=run_id,
            call_id=payload.call_id,
            answer=payload.answer,
            selected_option=payload.selected_option,
            decision=payload.decision,
            edited_arguments=payload.edited_arguments,
        )
    except RunConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"session already has a running run: {exc.run_id}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = row.session_id

    async def event_stream():
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        try:
            async for event in hub.subscribe():
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
