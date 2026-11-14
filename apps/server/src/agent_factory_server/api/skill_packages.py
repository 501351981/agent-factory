from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import AuthContext, enforce_agent_scope, require_agent_manager
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import SkillPackageOut, SkillPackageUpdate
from agent_factory_server.runtime.skill_package import PackageError, unpack_skill_zip

router = APIRouter(prefix="/api/agents/{agent_id}/skill-packages", tags=["skill-packages"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.get("", response_model=list[SkillPackageOut])
async def list_packages(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> list[SkillPackageOut]:
    enforce_agent_scope(auth, agent_id)
    agent = await repo.get_agent(session, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await repo.list_skill_packages(session, agent_id)


@router.get("/{name}", response_model=SkillPackageOut)
async def get_package(
    agent_id: str,
    name: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> SkillPackageOut:
    enforce_agent_scope(auth, agent_id)
    pkg = await repo.get_skill_package(session, agent_id, name)
    if not pkg:
        raise HTTPException(status_code=404, detail="Skill package not found")
    return pkg


@router.patch("/{name}", response_model=SkillPackageOut)
async def patch_package(
    agent_id: str,
    name: str,
    payload: SkillPackageUpdate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> SkillPackageOut:
    """Update catalog description without re-uploading the zip. Name is immutable."""
    enforce_agent_scope(auth, agent_id)
    pkg = await repo.update_skill_package_meta(
        session,
        agent_id=agent_id,
        name=name,
        description=payload.description.strip(),
    )
    if not pkg:
        raise HTTPException(status_code=404, detail="Skill package not found")
    return pkg


@router.post("", response_model=SkillPackageOut, status_code=status.HTTP_201_CREATED)
async def install_package(
    agent_id: str,
    file: UploadFile = File(...),
    force: bool = Form(False),
    keep_description: bool = Form(False),
    expected_name: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> SkillPackageOut:
    """Upload a skill zip; unpack into DB and merge into Agent.skills[].

    - force: replace an already-installed package with the same name (single current version).
    - keep_description: on replace, keep the existing catalog description instead of zip frontmatter.
    - expected_name: when set (row-level update), zip frontmatter name must match.
    """
    enforce_agent_scope(auth, agent_id)
    agent = await repo.get_agent(session, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="zip too large (max 50MB)")

    try:
        unpacked = unpack_skill_zip(raw)
        return await repo.install_skill_package(
            session,
            agent_id=agent_id,
            unpacked=unpacked,
            force=force,
            source="upload",
            expected_name=expected_name,
            keep_description=keep_description,
        )
    except PackageError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_package(
    agent_id: str,
    name: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> None:
    enforce_agent_scope(auth, agent_id)
    ok = await repo.uninstall_skill_package(session, agent_id=agent_id, name=name)
    if not ok:
        raise HTTPException(status_code=404, detail="Skill package not found")
