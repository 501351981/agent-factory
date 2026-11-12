from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import require_api_key
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import (
    MemoryCreate,
    MemoryOut,
    MemoryPage,
    MemoryUpdate,
    MemoryUsageOut,
)
from agent_factory_server.runtime.memory import resolve_memory_policy

router = APIRouter(
    prefix="/api/memories", tags=["memories"], dependencies=[Depends(require_api_key)]
)


@router.get("/usage", response_model=MemoryUsageOut)
async def get_memory_usage(
    agent_id: str = Query(...),
    user_key: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> MemoryUsageOut:
    agent = await repo.get_agent(session, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    policy = resolve_memory_policy(agent.runtime.memory if agent.runtime else None)
    return await repo.memory_usage(
        session,
        agent_id=agent_id,
        user_key=user_key.strip(),
        policy=policy,
    )


@router.get("", response_model=MemoryPage)
async def list_memories(
    agent_id: Optional[str] = Query(default=None),
    user_key: Optional[str] = Query(default=None),
    kind: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> MemoryPage:
    if kind and kind not in ("profile", "playbook", "detail"):
        raise HTTPException(status_code=400, detail="kind must be profile, playbook, or detail")
    return await repo.list_memories(
        session,
        agent_id=agent_id,
        user_key=user_key,
        kind=kind,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreate,
    session: AsyncSession = Depends(get_session),
) -> MemoryOut:
    agent = await repo.get_agent(session, payload.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not (payload.user_key or "").strip():
        raise HTTPException(status_code=400, detail="user_key is required")
    policy = resolve_memory_policy(agent.runtime.memory if agent.runtime else None)
    try:
        return await repo.create_memory(session, payload, policy=policy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: str,
    session: AsyncSession = Depends(get_session),
) -> MemoryOut:
    row = await repo.get_memory(session, memory_id)
    if not row:
        raise HTTPException(status_code=404, detail="Memory not found")
    return row


@router.patch("/{memory_id}", response_model=MemoryOut)
async def patch_memory(
    memory_id: str,
    payload: MemoryUpdate,
    session: AsyncSession = Depends(get_session),
) -> MemoryOut:
    try:
        updated = await repo.update_memory(session, memory_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Memory not found")
    return updated


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    ok = await repo.soft_delete_memory(session, memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
