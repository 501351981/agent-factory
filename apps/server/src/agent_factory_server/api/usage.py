from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import require_api_key
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import LlmUsagePage, UsageSummaryOut

router = APIRouter(
    prefix="/api/usage", tags=["usage"], dependencies=[Depends(require_api_key)]
)


@router.get("/summary", response_model=UsageSummaryOut)
async def get_usage_summary(
    group_by: str = Query(default="day", pattern="^(day|model|agent)$"),
    from_time: Optional[datetime] = Query(default=None),
    to_time: Optional[datetime] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> UsageSummaryOut:
    return await repo.summarize_llm_usage(
        session,
        group_by=group_by,
        from_time=from_time,
        to_time=to_time,
        agent_id=agent_id,
    )


@router.get("", response_model=LlmUsagePage)
async def list_usage(
    session_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    model_profile_id: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> LlmUsagePage:
    return await repo.list_llm_usage(
        session,
        session_id=session_id,
        agent_id=agent_id,
        model_profile_id=model_profile_id,
        page=page,
        page_size=page_size,
    )
