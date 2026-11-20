from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import AuthContext, enforce_agent_scope, require_caller
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import (
    BoardArtifactOut,
    BoardArtifactWrite,
    BoardProjectOut,
    ChildRunOut,
)

router = APIRouter(prefix="/api", tags=["board"])


@router.get(
    "/sessions/{session_id}/board",
    response_model=BoardProjectOut,
)
async def get_session_board(
    session_id: str,
    create: bool = False,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> BoardProjectOut:
    row = await repo.get_session_row(session, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    enforce_agent_scope(auth, row.agent_id)
    out = await repo.get_board_project_out(
        session,
        session_id,
        create=create,
        user_key=getattr(row, "user_key", None) or "",
    )
    if not out:
        raise HTTPException(status_code=404, detail="board not found")
    return out


@router.post(
    "/sessions/{session_id}/board/artifacts",
    response_model=BoardArtifactOut,
    status_code=status.HTTP_201_CREATED,
)
async def write_session_board_artifact(
    session_id: str,
    payload: BoardArtifactWrite,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> BoardArtifactOut:
    row = await repo.get_session_row(session, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    enforce_agent_scope(auth, row.agent_id)
    project = await repo.get_or_create_board_project(
        session,
        lead_session_id=session_id,
        user_key=getattr(row, "user_key", None) or "",
    )
    try:
        return await repo.write_board_artifact(
            session,
            project_id=project.id,
            payload=payload,
            author_agent_id=payload.author_agent_id or row.agent_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}/children", response_model=list[ChildRunOut])
async def list_run_children(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> list[ChildRunOut]:
    run = await repo.get_run(session, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    sess = await repo.get_session_row(session, run.session_id)
    if sess:
        enforce_agent_scope(auth, sess.agent_id)
    return await repo.list_child_runs(session, run_id)


@router.get(
    "/sessions/{session_id}/delegations",
    response_model=list[ChildRunOut],
)
async def list_session_delegations(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_caller),
) -> list[ChildRunOut]:
    """List every child Run spawned under this lead session (hidden system sessions)."""
    row = await repo.get_session_row(session, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    enforce_agent_scope(auth, row.agent_id)
    return await repo.list_child_runs_for_root_session(session, session_id)
