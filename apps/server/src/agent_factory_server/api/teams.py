from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import require_api_key
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import (
    ChildRunOut,
    TeamCreate,
    TeamOut,
    TeamUpdate,
)

router = APIRouter(
    prefix="/api/teams", tags=["teams"], dependencies=[Depends(require_api_key)]
)


@router.get("", response_model=list[TeamOut])
async def list_teams(session: AsyncSession = Depends(get_session)) -> list[TeamOut]:
    return await repo.list_teams(session)


@router.post("", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: TeamCreate,
    session: AsyncSession = Depends(get_session),
) -> TeamOut:
    lead = await repo.get_agent(session, payload.lead_agent_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead agent not found")
    for member in payload.members:
        agent = await repo.get_agent(session, member.agent_id)
        if not agent:
            raise HTTPException(
                status_code=404, detail=f"member agent not found: {member.agent_id}"
            )
        if member.agent_id == payload.lead_agent_id:
            raise HTTPException(
                status_code=400, detail="lead agent cannot also be a member"
            )
    return await repo.create_team(session, payload)


@router.get("/{team_id}", response_model=TeamOut)
async def get_team(
    team_id: str, session: AsyncSession = Depends(get_session)
) -> TeamOut:
    team = await repo.get_team(session, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="team not found")
    return team


@router.patch("/{team_id}", response_model=TeamOut)
async def update_team(
    team_id: str,
    payload: TeamUpdate,
    session: AsyncSession = Depends(get_session),
) -> TeamOut:
    existing = await repo.get_team(session, team_id)
    if not existing:
        raise HTTPException(status_code=404, detail="team not found")
    lead_id = payload.lead_agent_id or existing.lead_agent_id
    if payload.lead_agent_id:
        lead = await repo.get_agent(session, payload.lead_agent_id)
        if not lead:
            raise HTTPException(status_code=404, detail="lead agent not found")
    members = payload.members if payload.members is not None else existing.members
    for member in members:
        agent = await repo.get_agent(session, member.agent_id)
        if not agent:
            raise HTTPException(
                status_code=404, detail=f"member agent not found: {member.agent_id}"
            )
        if member.agent_id == lead_id:
            raise HTTPException(
                status_code=400, detail="lead agent cannot also be a member"
            )
    updated = await repo.update_team(session, team_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="team not found")
    return updated


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    ok = await repo.delete_team(session, team_id)
    if not ok:
        raise HTTPException(status_code=404, detail="team not found")


@router.get("/{team_id}/children-by-run/{run_id}", response_model=list[ChildRunOut])
async def list_team_child_runs(
    team_id: str,
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[ChildRunOut]:
    team = await repo.get_team(session, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="team not found")
    return await repo.list_child_runs(session, run_id)
