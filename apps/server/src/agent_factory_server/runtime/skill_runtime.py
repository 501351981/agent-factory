"""Execute skill_load / skill_read against agent or platform packages (or Bridge)."""

from __future__ import annotations

from typing import Any, Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import SkillDef
from agent_factory_server.runtime.skill_package import PAGE_CHARS
from agent_factory_server.runtime.skills import SKILL_LOAD_TOOL_NAME, SKILL_READ_TOOL_NAME
from agent_factory_server.tools.client_hub import client_hub


def _skill_origin(skills: list[SkillDef], name: str) -> str:
    cleaned = (name or "").strip()
    for skill in skills:
        if (skill.name or "").strip() == cleaned:
            return skill.origin or "package"
    return "package"


def _file_meta(files: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "path": f.path,
            "kind": f.kind,
            "binary": bool(f.is_binary),
            "bytes": int(f.size_bytes or 0),
        }
        for f in files
    ]


async def _load_by_package_id(
    session: AsyncSession, *, package_id: str, name: str, origin: str
) -> dict[str, Any]:
    files = await repo.list_skill_package_files(session, package_id)
    skill_md = next((f for f in files if f.path == "SKILL.md"), None)
    if skill_md is None or skill_md.is_binary or not skill_md.text_content:
        return {"ok": False, "error": f"SKILL.md missing for skill: {name}"}
    body = skill_md.text_content
    return {
        "ok": True,
        "data": {
            "name": name,
            "origin": origin,
            "skill_md": body,
            "skill_md_chars": len(body),
            "skill_md_page_chars": PAGE_CHARS,
            "files": [f.path for f in files],
            "file_meta": _file_meta(files),
        },
    }


async def _read_by_package_id(
    session: AsyncSession,
    *,
    package_id: str,
    name: str,
    origin: str,
    path: str,
    offset: int,
) -> dict[str, Any]:
    rel = (path or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return {"ok": False, "error": "invalid path: must be relative inside the skill package"}
    file_row = await repo.get_skill_package_file(session, package_id, rel)
    if file_row is None:
        return {"ok": False, "error": f"file not found: {rel}"}
    if file_row.is_binary:
        return {
            "ok": False,
            "error": (
                f"binary file cannot be read as text: {rel} "
                "(scripts/docs only; assets are not projected into the model)"
            ),
            "code": "binary_file",
        }
    text = file_row.text_content or ""
    total = len(text)
    if offset > total:
        return {"ok": False, "error": f"offset {offset} beyond end of file ({total} chars)"}
    chunk = text[offset : offset + PAGE_CHARS]
    next_offset = offset + len(chunk) if offset + len(chunk) < total else None
    return {
        "ok": True,
        "data": {
            "name": name,
            "origin": origin,
            "path": rel,
            "content": chunk,
            "offset": offset,
            "total_chars": total,
            "next_offset": next_offset,
        },
    }


async def _forward_client(
    *,
    name: str,
    arguments: dict[str, Any],
    session_id: str,
    client_device_id: Optional[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    from uuid import uuid4

    device = (client_device_id or "").strip()
    if not device:
        return {
            "ok": False,
            "error": "session has no client_device_id; bind a Bridge to load client skills",
        }
    result = await client_hub.call_tool(
        device_id=device,
        call_id=str(uuid4()),
        name=name,
        arguments=arguments,
        session_id=session_id,
        timeout=timeout_seconds,
    )
    if "ok" not in result:
        return {"ok": True, "data": result}
    return result


async def execute_skill_tool(
    session: AsyncSession,
    *,
    name: str,
    arguments: dict[str, Any],
    agent_id: str,
    skills: list[SkillDef],
    session_id: str,
    client_device_id: Optional[str],
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    skill_name = str(arguments.get("name") or "").strip()
    if not skill_name:
        return {"ok": False, "error": "name is required"}

    origin = _skill_origin(skills, skill_name)

    if origin == "client":
        return await _forward_client(
            name=name,
            arguments=arguments,
            session_id=session_id,
            client_device_id=client_device_id,
            timeout_seconds=timeout_seconds,
        )

    package_id, resolved_origin = await repo.resolve_skill_package_for_agent(
        session, agent_id=agent_id, name=skill_name
    )
    if not package_id or not resolved_origin:
        return {"ok": False, "error": f"skill package not found: {skill_name}"}

    if name == SKILL_LOAD_TOOL_NAME:
        return await _load_by_package_id(
            session,
            package_id=package_id,
            name=skill_name,
            origin=resolved_origin,
        )

    if name == SKILL_READ_TOOL_NAME:
        try:
            offset = int(arguments.get("offset") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid offset: must be a non-negative integer"}
        if offset < 0:
            return {"ok": False, "error": "invalid offset: must be a non-negative integer"}
        path = str(arguments.get("path") or "")
        return await _read_by_package_id(
            session,
            package_id=package_id,
            name=skill_name,
            origin=resolved_origin,
            path=path,
            offset=offset,
        )

    return {"ok": False, "error": f"unknown skill tool: {name}"}
