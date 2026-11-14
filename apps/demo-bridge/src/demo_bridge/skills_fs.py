from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Page size for skill_read. Must stay well under the server's
# context_policy.skill_result_max_chars so JSON escaping never re-triggers truncation.
PAGE_CHARS = 16000

# Read ceiling per file. Paging exists for long SOPs, not for streaming a
# multi-megabyte blob 16KB at a time.
MAX_READ_BYTES = 2 * 1024 * 1024

# Manifest cap. Kept at 50 for compatibility with existing clients.
MAX_MANIFEST_FILES = 50


def resolve_skills_root() -> Path:
    env = (os.getenv("FACTORY_SKILLS_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    # .../apps/demo-bridge/src/demo_bridge/skills_fs.py → apps/demo-bridge/skills
    packaged = here.parents[2] / "skills"
    candidates = [
        Path.cwd() / "skills",
        packaged,
        Path.home() / ".agent-factory" / "skills",
    ]
    for path in candidates:
        if path.is_dir():
            return path.resolve()
    return packaged.resolve()


def resolve_install_root(explicit: str | None = None) -> Path:
    """Pick where a package should be unpacked.

    It must be a directory that ``resolve_skills_root`` will actually read from,
    otherwise the install succeeds and the skill stays invisible.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = (os.getenv("FACTORY_SKILLS_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    current = resolve_skills_root()
    if current.is_dir():
        return current
    # Nothing configured yet: create the user-level root, which resolve_skills_root
    # then picks up (the higher-priority candidates do not exist).
    fallback = (Path.home() / ".agent-factory" / "skills").resolve()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _validate_name(name: str) -> str | None:
    cleaned = (name or "").strip()
    if not cleaned or not _SKILL_NAME_RE.match(cleaned):
        return None
    return cleaned


def _skill_dir(root: Path, name: str) -> Path | None:
    valid = _validate_name(name)
    if not valid:
        return None
    path = (root / valid).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _list_files(skill_dir: Path) -> list[str]:
    files: list[str] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if rel.startswith("."):
            continue
        files.append(rel)
        if len(files) >= 50:
            break
    return files


def skill_load(arguments: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    skills_root = root or resolve_skills_root()
    name = _validate_name(str(arguments.get("name") or ""))
    if not name:
        return {"ok": False, "error": "invalid skill name"}
    skill_dir = _skill_dir(skills_root, name)
    if skill_dir is None or not skill_dir.is_dir():
        return {
            "ok": False,
            "error": f"skill not found: {name} (root={skills_root})",
        }
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return {"ok": False, "error": f"SKILL.md missing for skill: {name}"}
    try:
        body = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "data": {
            "name": name,
            "root": str(skills_root),
            "skill_md": body,
            "skill_md_chars": len(body),
            # Model must page with skill_read(..., path="SKILL.md", offset=...)
            # when the projection cap cuts the body.
            "skill_md_page_chars": PAGE_CHARS,
            "files": _list_files(skill_dir),
        },
    }


def skill_read(arguments: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    skills_root = root or resolve_skills_root()
    name = _validate_name(str(arguments.get("name") or ""))
    rel = str(arguments.get("path") or "").strip().replace("\\", "/")
    try:
        offset = int(arguments.get("offset") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid offset: must be a non-negative integer"}
    if offset < 0:
        return {"ok": False, "error": "invalid offset: must be a non-negative integer"}
    if not name:
        return {"ok": False, "error": "invalid skill name"}
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return {"ok": False, "error": "invalid path: must be relative inside the skill package"}
    skill_dir = _skill_dir(skills_root, name)
    if skill_dir is None or not skill_dir.is_dir():
        return {"ok": False, "error": f"skill not found: {name}"}
    target = (skill_dir / rel).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError:
        return {"ok": False, "error": "path escapes skill package"}
    if not target.is_file():
        return {"ok": False, "error": f"file not found: {rel}"}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    total = len(text)
    if offset > total:
        return {"ok": False, "error": f"offset {offset} beyond end of file ({total} chars)"}
    chunk = text[offset : offset + PAGE_CHARS]
    next_offset = offset + len(chunk) if offset + len(chunk) < total else None
    return {
        "ok": True,
        "data": {
            "name": name,
            "path": rel,
            "content": chunk,
            "offset": offset,
            "total_chars": total,
            "next_offset": next_offset,
        },
    }
