"""Seed bundled platform skills (e.g. skill-creator) into the factory DB."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.runtime.skill_package import (
    SKILL_NAME_RE,
    classify_path,
    parse_frontmatter,
)

logger = logging.getLogger(__name__)

_BUILTIN_ROOT = Path(__file__).resolve().parent.parent / "builtin_skills"


def _pack_dir(skill_dir: Path) -> dict[str, Any] | None:
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.is_file():
        return None
    skill_md_text = skill_md_path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(skill_md_text)
    name = str(meta.get("name") or skill_dir.name).strip()
    if not SKILL_NAME_RE.match(name or ""):
        logger.warning("skip builtin skill with invalid name: %s", skill_dir)
        return None
    description = str(meta.get("description") or "").strip()
    version = str(meta.get("version") or "").strip() or None

    files: list[dict[str, Any]] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if rel.startswith(".") or "/." in rel:
            continue
        data = path.read_bytes()
        is_binary = b"\x00" in data[:2048]
        kind = classify_path(rel, 0)
        entry: dict[str, Any] = {
            "path": rel,
            "kind": kind,
            "is_binary": is_binary,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": None,
        }
        if is_binary:
            entry["blob"] = data
            entry["text_content"] = None
        else:
            entry["text_content"] = data.decode("utf-8")
            entry["blob"] = None
        files.append(entry)

    return {
        "name": name,
        "description": description,
        "version": version,
        "warnings": [],
        "total_bytes": sum(int(f["bytes"]) for f in files),
        "files": files,
    }


async def seed_platform_skills(session: AsyncSession) -> None:
    """Install missing builtin platform skills. Never force-overwrite admin edits."""
    if not _BUILTIN_ROOT.is_dir():
        return
    for skill_dir in sorted(p for p in _BUILTIN_ROOT.iterdir() if p.is_dir()):
        unpacked = _pack_dir(skill_dir)
        if not unpacked:
            continue
        name = unpacked["name"]
        existing = await repo.get_platform_skill_package_record(session, name)
        if existing:
            continue
        await repo.install_platform_skill_package(
            session,
            unpacked=unpacked,
            force=False,
            source="builtin",
        )
        logger.info("seeded platform skill: %s", name)
