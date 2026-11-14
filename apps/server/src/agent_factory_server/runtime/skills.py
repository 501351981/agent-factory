from __future__ import annotations

from typing import Any

from agent_factory_server.domain.schemas import SkillDef, ToolDef
from agent_factory_server.runtime.locale import Locale, normalize_locale

SKILL_LOAD_TOOL_NAME = "skill_load"
SKILL_READ_TOOL_NAME = "skill_read"
SKILL_TOOL_NAMES = frozenset({SKILL_LOAD_TOOL_NAME, SKILL_READ_TOOL_NAME})

_COPY: dict[str, dict[str, str]] = {
    "zh": {
        "catalog_title": "# Skills（仅目录）",
        "catalog_discipline": (
            "正文不在本提示中。当某 skill 的描述匹配当前用户请求时，"
            f"先调用 `{SKILL_LOAD_TOOL_NAME}(name=...)` 获取 SKILL.md，再按其步骤执行；"
            f"需要附属文件时再调用 `{SKILL_READ_TOOL_NAME}(name=..., path=...)`。"
            "无关 skill 不要加载。"
        ),
        "load_desc": (
            "按名称加载 skill 正文（SKILL.md）与文件清单。"
            "工厂托管包与本地客户端包均可；在遵循某 skill 流程之前必须先调用本工具。"
        ),
        "read_desc": (
            "读取已安装 skill 包内的相对路径文件（如 reference.md）。"
            "禁止绝对路径与 .. 逃逸；二进制资源不可读。"
            "文件较大时用 offset 分段读取，按返回的 next_offset 继续，直到为 null。"
        ),
    },
    "en": {
        "catalog_title": "# Skills (catalog only)",
        "catalog_discipline": (
            "Skill bodies are NOT in this prompt. When a skill description matches the request, "
            f"call `{SKILL_LOAD_TOOL_NAME}(name=...)` first to fetch SKILL.md, then follow it; "
            f"use `{SKILL_READ_TOOL_NAME}(name=..., path=...)` for extra files. "
            "Do not load unrelated skills."
        ),
        "load_desc": (
            "Load a skill body (SKILL.md) and file list from a factory package or local client. "
            "Call this before following a skill procedure."
        ),
        "read_desc": (
            "Read a relative file inside an installed skill package. "
            "Absolute paths, .. escapes, and binary assets are rejected. "
            "For large files, page with offset and follow next_offset until it is null."
        ),
    },
    "ja": {
        "catalog_title": "# Skills（カタログのみ）",
        "catalog_discipline": (
            "本文はこのプロンプトに含まれません。説明がリクエストに合う skill は、"
            f"先に `{SKILL_LOAD_TOOL_NAME}(name=...)` で SKILL.md を取得してから従い、"
            f"追加ファイルは `{SKILL_READ_TOOL_NAME}` で読みます。無関係な skill は読み込まないでください。"
        ),
        "load_desc": (
            "工場パッケージまたはローカルクライアントから skill 本文（SKILL.md）とファイル一覧を読み込みます。"
            "手順に従う前に必ず呼び出してください。"
        ),
        "read_desc": (
            "インストール済み skill パッケージ内の相対パスファイルを読みます。"
            "絶対パス・..・バイナリは拒否されます。"
            "大きいファイルは offset で分割読み込みし、next_offset が null になるまで続けてください。"
        ),
    },
}


def _copy(locale: Locale) -> dict[str, str]:
    return _COPY.get(locale) or _COPY["zh"]


def format_skills_catalog(
    skills: list[SkillDef],
    *,
    locale: Locale | str = "zh",
) -> str:
    if not skills:
        return ""
    copy = _copy(normalize_locale(locale))
    lines = [copy["catalog_title"], copy["catalog_discipline"], ""]
    for skill in skills:
        name = (skill.name or "").strip()
        if not name:
            continue
        desc = (skill.description or "").strip() or "(no description)"
        lines.append(f"- `{name}`: {desc}")
    return "\n".join(lines).strip()


def skill_tools(locale: Locale | str = "zh") -> list[ToolDef]:
    """Builtin skill tools — runtime routes package vs client by SkillDef.origin."""
    copy = _copy(normalize_locale(locale))
    return [
        ToolDef(
            name=SKILL_LOAD_TOOL_NAME,
            description=copy["load_desc"],
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill directory name (lowercase/digits/hyphens).",
                    }
                },
                "required": ["name"],
            },
        ),
        ToolDef(
            name=SKILL_READ_TOOL_NAME,
            description=copy["read_desc"],
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the skill package.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "Character offset to start from (default 0). "
                            "Use the next_offset from a truncated response to continue."
                        ),
                    },
                },
                "required": ["name", "path"],
            },
        ),
    ]


# Back-compat alias
client_skill_tools = skill_tools


def merge_agent_tools_with_skills(
    agent_tools: list[ToolDef],
    *,
    skills: list[SkillDef],
    locale: Locale | str = "zh",
) -> list[ToolDef]:
    """Attach skill_load / skill_read when the agent has a skills catalog.

    Tools are builtin; package-origin skills read from factory DB, client-origin
    skills are forwarded to the Bound Bridge (which must still implement them).
    """
    if not skills:
        return list(agent_tools)
    existing = {t.name for t in agent_tools}
    merged = list(agent_tools)
    for tool in skill_tools(locale):
        if tool.name not in existing:
            merged.append(tool)
    return merged


def has_client_origin_skills(skills: list[SkillDef] | None) -> bool:
    """Legacy: Bridge-local skills. New installs are package-only."""
    return any((s.origin or "package") == "client" for s in (skills or []))


def skill_names(skills: list[SkillDef]) -> list[str]:
    return [s.name.strip() for s in skills if (s.name or "").strip()]
