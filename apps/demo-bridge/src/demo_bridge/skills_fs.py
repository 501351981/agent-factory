from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


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
            "files": _list_files(skill_dir),
        },
    }


def skill_read(arguments: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    skills_root = root or resolve_skills_root()
    name = _validate_name(str(arguments.get("name") or ""))
    rel = str(arguments.get("path") or "").strip().replace("\\", "/")
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
    if len(text) > 100_000:
        text = text[:100_000] + "\n...[truncated]"
    return {
        "ok": True,
        "data": {"name": name, "path": rel, "content": text},
    }
