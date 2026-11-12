from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import (
    AuthContext,
    enforce_agent_scope,
    require_agent_manager,
    require_api_key,
    require_caller,
)
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import (
    AgentCreate,
    AgentCredentialOut,
    AgentOut,
    AgentUpdate,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_api_key),
) -> list[AgentOut]:
    return await repo.list_agents(session)


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate,
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_api_key),
) -> AgentOut:
    return await repo.create_agent(session, payload)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> AgentOut:
    enforce_agent_scope(auth, agent_id)
    agent = await repo.get_agent(session, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> AgentOut:
    """Platform or this agent's AK/SK may update config (not embed tokens)."""
    enforce_agent_scope(auth, agent_id)
    agent = await repo.update_agent(session, agent_id, payload)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_api_key),
) -> None:
    ok = await repo.delete_agent(session, agent_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/credentials/rotate", response_model=AgentCredentialOut)
async def rotate_credentials(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> AgentCredentialOut:
    """Platform or this agent's current AK/SK may rotate credentials."""
    enforce_agent_scope(auth, agent_id)
    result = await repo.rotate_agent_credentials(session, agent_id)
    if not result:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent, secret_key = result
    assert agent.access_key
    return AgentCredentialOut(
        agent_id=agent.id,
        access_key=agent.access_key,
        secret_key=secret_key,
        has_secret_key=True,
    )


@router.post("/{agent_id}/credentials/revoke", response_model=AgentOut)
async def revoke_credentials(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> AgentOut:
    enforce_agent_scope(auth, agent_id)
    agent = await repo.revoke_agent_credentials(session, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent
