from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import (
    AuthContext,
    enforce_agent_scope,
    enforce_user_key_binding,
    require_api_key,
    require_caller,
)
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session as get_db_session
from agent_factory_server.domain.schemas import (
    CompactReportOut,
    LlmMessagesDebugOut,
    MessageOut,
    PromptSnapshotOut,
    SessionCreate,
    SessionOut,
    SessionPage,
    SessionQueueItemOut,
    SessionQueueOut,
    SessionTimeline,
    SessionUpdate,
)
from agent_factory_server.runtime.context import assemble_llm_context, resolve_context_policy
from agent_factory_server.runtime.loop import build_system_prompt, resolve_llm_client, resolve_runtime
from agent_factory_server.runtime.memory import load_memory_block
from agent_factory_server.runtime.locale import (
    normalize_locale,
    pick_system_prompt_template,
)
from agent_factory_server.runtime.prompt_template import (
    normalize_prompt_vars,
    render_prompt_template,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


async def _session_for_caller(
    session: AsyncSession, session_id: str, auth: AuthContext
):
    row = await repo.get_session_row(session, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    enforce_agent_scope(auth, row.agent_id)
    return row


@router.get("", response_model=SessionPage)
async def list_sessions(
    agent_id: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_caller),
) -> SessionPage:
    if auth.kind in ("agent_key", "embed"):
        agent_id = auth.agent_id
    return await repo.list_sessions(
        session, agent_id=agent_id, page=page, page_size=page_size
    )


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreate,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_caller),
) -> SessionOut:
    enforce_agent_scope(auth, payload.agent_id)
    agent = await repo.get_agent(session, payload.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    user_key = enforce_user_key_binding(auth, payload.user_key)
    if not user_key:
        raise HTTPException(status_code=400, detail="user_key is required")
    locale = normalize_locale(payload.locale)
    prompt_vars = normalize_prompt_vars(payload.prompt_vars)
    template = pick_system_prompt_template(
        system_prompt=agent.system_prompt,
        system_prompts=agent.system_prompts,
        locale=locale,
    )
    resolved_system_prompt = render_prompt_template(template, prompt_vars)
    create_payload = SessionCreate(
        agent_id=payload.agent_id,
        user_key=user_key,
        title=payload.title,
        locale=locale,
        prompt_vars=prompt_vars,
        client_device_id=(payload.client_device_id or "").strip() or None,
    )
    try:
        return await repo.create_session(
            session,
            create_payload,
            system_prompt=resolved_system_prompt,
            prompt_vars=prompt_vars,
            locale=locale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{session_id}", response_model=SessionOut)
async def get_session_detail(
    session_id: str,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_caller),
) -> SessionOut:
    row = await _session_for_caller(session, session_id, auth)
    return repo.session_to_out(row)


@router.patch("/{session_id}", response_model=SessionOut)
async def patch_session(
    session_id: str,
    payload: SessionUpdate,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_api_key),
) -> SessionOut:
    updated = await repo.update_session(session, session_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return updated


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def get_messages(
    session_id: str,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_caller),
) -> list[MessageOut]:
    await _session_for_caller(session, session_id, auth)
    return await repo.list_messages(session, session_id)


@router.get("/{session_id}/timeline", response_model=SessionTimeline)
async def get_timeline(
    session_id: str,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_caller),
) -> SessionTimeline:
    await _session_for_caller(session, session_id, auth)
    return await repo.get_session_timeline(session, session_id)


@router.get("/{session_id}/queue", response_model=SessionQueueOut)
async def get_queue(
    session_id: str,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_caller),
) -> SessionQueueOut:
    await _session_for_caller(session, session_id, auth)
    return await repo.get_session_queue(session, session_id)


@router.delete("/{session_id}/queue/{item_id}", response_model=SessionQueueItemOut)
async def cancel_queue_item(
    session_id: str,
    item_id: str,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_caller),
) -> SessionQueueItemOut:
    await _session_for_caller(session, session_id, auth)
    cancelled = await repo.cancel_queue_item(session, session_id, item_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Queue item not found")
    return cancelled


@router.get("/{session_id}/debug/llm-messages", response_model=LlmMessagesDebugOut)
async def debug_llm_messages(
    session_id: str,
    session: AsyncSession = Depends(get_db_session),
    _auth: AuthContext = Depends(require_api_key),
) -> LlmMessagesDebugOut:
    """Export the messages that would be sent to the LLM after compaction."""
    row = await repo.get_session_row(session, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    agent = await repo.get_agent(session, row.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    runtime = resolve_runtime(agent)
    (
        llm,
        model_name,
        temperature,
        include_reasoning,
        context_window_tokens,
        reserve_output_tokens,
        _profile_id,
        _in_price,
        _out_price,
    ) = await resolve_llm_client(session, agent)
    running = await repo.get_running_run(session, session_id)
    memory_block = await load_memory_block(
        session,
        agent_id=agent.id,
        session_id=session_id,
        user_key=row.user_key,
        policy=runtime.memory,
        locale=normalize_locale(getattr(row, "locale", None)),
    )
    agent_system = build_system_prompt(
        agent,
        resolved_system_prompt=getattr(row, "system_prompt", None),
        locale=normalize_locale(getattr(row, "locale", None)),
    )
    messages, report = await assemble_llm_context(
        session,
        session_id=session_id,
        system_prompt=agent_system,
        current_run_id=running.id if running else None,
        policy=resolve_context_policy(
            runtime.tool_result_max_chars, runtime.context_policy
        ),
        context_window_tokens=context_window_tokens,
        reserve_output_tokens=reserve_output_tokens,
        include_reasoning=include_reasoning,
        llm=llm,
        model_name=model_name,
        temperature=temperature,
        memory_block=memory_block,
    )
    summary_text = ""
    for msg in messages[1:4]:
        content = str((msg or {}).get("content") or "")
        if content.startswith("# Prior context summary"):
            summary_text = content
            break
    snapshots = await repo.list_prompt_snapshots(session, session_id, limit=20)
    return LlmMessagesDebugOut(
        session_id=session_id,
        agent_system_prompt=agent_system,
        memory_block=memory_block or "",
        context_summary=summary_text,
        messages=messages,
        report=CompactReportOut(**report.to_out_dict()),
        prompt_snapshots=[PromptSnapshotOut(**s) for s in snapshots],
    )
