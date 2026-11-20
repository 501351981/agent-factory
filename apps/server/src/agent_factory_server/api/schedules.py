from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import AuthContext, enforce_agent_scope, require_caller
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import ScheduleOut, ScheduleStatsOut
from agent_factory_server.runtime.scheduler import resolve_scheduler_policy

router = APIRouter(prefix="/api", tags=["schedules"])


def _to_out(row) -> ScheduleOut:
    return ScheduleOut(**repo.schedule_to_dict(row))


@router.get("/agents/{agent_id}/schedules", response_model=list[ScheduleOut])
async def list_agent_schedules(
    agent_id: str,
    status: Optional[str] = Query(default=None),
    user_key: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> list[ScheduleOut]:
    enforce_agent_scope(auth, agent_id)
    agent = await repo.get_agent(session, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    status_filter = None if (status or "").lower() in ("", "all") else status
    rows = await repo.list_schedules_for_agent(
        session,
        agent_id=agent_id,
        status=status_filter,
        user_key=user_key,
        limit=limit,
    )
    return [_to_out(r) for r in rows]


@router.get(
    "/agents/{agent_id}/schedules/stats",
    response_model=ScheduleStatsOut,
)
async def agent_schedule_stats(
    agent_id: str,
    user_key: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> ScheduleStatsOut:
    enforce_agent_scope(auth, agent_id)
    agent = await repo.get_agent(session, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    policy = resolve_scheduler_policy(
        getattr(getattr(agent, "runtime", None), "scheduler", None)
    )
    data = await repo.schedule_stats_for_agent(
        session,
        agent_id=agent_id,
        user_key=user_key,
        max_active_jobs=int(policy.max_active_jobs or 50),
    )
    return ScheduleStatsOut(**data)


@router.get("/schedules/{job_id}", response_model=ScheduleOut)
async def get_schedule(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> ScheduleOut:
    row = await repo.get_schedule(session, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="schedule not found")
    enforce_agent_scope(auth, row.agent_id)
    return _to_out(row)


@router.post("/schedules/{job_id}/cancel", response_model=ScheduleOut)
async def cancel_schedule(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> ScheduleOut:
    row = await repo.get_schedule(session, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="schedule not found")
    enforce_agent_scope(auth, row.agent_id)
    updated = await repo.cancel_schedule(session, job_id)
    if not updated:
        raise HTTPException(status_code=404, detail="schedule not found")
    return _to_out(updated)


@router.delete("/schedules/{job_id}", status_code=204)
async def delete_schedule(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> None:
    row = await repo.get_schedule(session, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="schedule not found")
    enforce_agent_scope(auth, row.agent_id)
    ok = await repo.delete_schedule(session, job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="schedule not found")


@router.post("/schedules/{job_id}/pause", response_model=ScheduleOut)
async def pause_schedule(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> ScheduleOut:
    row = await repo.get_schedule(session, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="schedule not found")
    enforce_agent_scope(auth, row.agent_id)
    updated = await repo.pause_schedule(session, job_id)
    if not updated:
        raise HTTPException(status_code=404, detail="schedule not found")
    return _to_out(updated)


@router.post("/schedules/{job_id}/resume", response_model=ScheduleOut)
async def resume_schedule(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> ScheduleOut:
    row = await repo.get_schedule(session, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="schedule not found")
    enforce_agent_scope(auth, row.agent_id)
    updated = await repo.resume_schedule(session, job_id)
    if not updated:
        raise HTTPException(status_code=404, detail="schedule not found")
    return _to_out(updated)
