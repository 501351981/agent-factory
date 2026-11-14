from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import AuthContext, enforce_agent_scope, require_agent_manager, require_api_key
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import (
    AgentSkillMountOut,
    PlatformSkillPackageOut,
    SkillPackageUpdate,
)
from agent_factory_server.runtime.skill_package import PackageError, unpack_skill_zip

router = APIRouter(tags=["platform-skills"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.get("/api/platform-skills", response_model=list[PlatformSkillPackageOut])
async def list_platform_skills(
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_api_key),
) -> list[PlatformSkillPackageOut]:
    _ = auth
    return await repo.list_platform_skill_packages(session)


@router.get("/api/platform-skills/{name}", response_model=PlatformSkillPackageOut)
async def get_platform_skill(
    name: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_api_key),
) -> PlatformSkillPackageOut:
    _ = auth
    pkg = await repo.get_platform_skill_package(session, name)
    if not pkg:
        raise HTTPException(status_code=404, detail="Platform skill not found")
    return pkg


@router.patch("/api/platform-skills/{name}", response_model=PlatformSkillPackageOut)
async def patch_platform_skill(
    name: str,
    payload: SkillPackageUpdate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_api_key),
) -> PlatformSkillPackageOut:
    _ = auth
    pkg = await repo.update_platform_skill_package_meta(
        session, name=name, description=payload.description.strip()
    )
    if not pkg:
        raise HTTPException(status_code=404, detail="Platform skill not found")
    return pkg


@router.post(
    "/api/platform-skills",
    response_model=PlatformSkillPackageOut,
    status_code=status.HTTP_201_CREATED,
)
async def install_platform_skill(
    file: UploadFile = File(...),
    force: bool = Form(False),
    keep_description: bool = Form(False),
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_api_key),
) -> PlatformSkillPackageOut:
    _ = auth
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="zip too large (max 50MB)")
    try:
        unpacked = unpack_skill_zip(raw)
        return await repo.install_platform_skill_package(
            session,
            unpacked=unpacked,
            force=force,
            source="upload",
            keep_description=keep_description,
        )
    except PackageError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.delete("/api/platform-skills/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_platform_skill(
    name: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_api_key),
) -> None:
    _ = auth
    ok = await repo.uninstall_platform_skill_package(session, name=name)
    if not ok:
        raise HTTPException(status_code=404, detail="Platform skill not found")


@router.get(
    "/api/agents/{agent_id}/skill-mounts",
    response_model=list[AgentSkillMountOut],
)
async def list_mounts(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> list[AgentSkillMountOut]:
    enforce_agent_scope(auth, agent_id)
    agent = await repo.get_agent(session, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await repo.list_agent_skill_mounts(session, agent_id)


@router.post(
    "/api/agents/{agent_id}/skill-mounts/{name}",
    response_model=AgentSkillMountOut,
    status_code=status.HTTP_201_CREATED,
)
async def mount_skill(
    agent_id: str,
    name: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> AgentSkillMountOut:
    enforce_agent_scope(auth, agent_id)
    try:
        return await repo.mount_platform_skill(session, agent_id=agent_id, name=name)
    except PackageError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.delete(
    "/api/agents/{agent_id}/skill-mounts/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unmount_skill(
    agent_id: str,
    name: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> None:
    enforce_agent_scope(auth, agent_id)
    ok = await repo.unmount_platform_skill(session, agent_id=agent_id, name=name)
    if not ok:
        raise HTTPException(status_code=404, detail="Mount not found")
