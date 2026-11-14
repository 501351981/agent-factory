from __future__ import annotations

from typing import Any, Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import MemoryPolicy, ToolDef
from agent_factory_server.runtime.locale import Locale, normalize_locale

MEMORY_TOOL_NAMES = frozenset({"memory_write", "memory_read", "memory_forget"})
MEMORY_KINDS = frozenset({"profile", "playbook", "detail"})

_MEMORY_I18N: dict[Locale, dict[str, str]] = {
    "zh": {
        "title": "# 记忆",
        "discipline": (
            "记忆写入纪律："
            "profile=仅稳定用户事实/偏好；"
            "playbook=极短目录标题（只说明「存的是什么」，不含步骤/命令/域名列表等细节；"
            "推荐≤30字，硬上限80字；例：「阿里云(39.106.34.112)上的域名信息」）；"
            "detail=同 key 的完整步骤、命令、链接、域名明细（默认不进上下文，用 memory_read 取）；"
            "禁止把明细写进 playbook，否则每轮注入会膨胀。"
            "不要写入一次性对话过程。"
            "优先更新已有 key；一次探索最多一条 playbook+detail。"
        ),
        "profile_heading": "## 画像（稳定事实与偏好）",
        "playbook_heading": (
            "## 实践目录（仅极短标题；步骤/明细请 memory_read kind=detail）"
        ),
        "truncated": "…(已截断)",
        "write_extra": (
            "kind=profile|playbook|detail；同实践共用 key。"
            "playbook content 推荐≤30字、不得超过80字；细节只写 detail。"
        ),
        "read_desc": "读取记忆（含 inactive/冷目录）。查完整步骤用 kind=detail + playbook 的 key。",
        "forget_desc": "按 key 遗忘。kind=playbook（或缺省）时同时遗忘对应 detail。",
    },
    "en": {
        "title": "# Memory",
        "discipline": (
            "Memory write discipline: "
            "profile = stable user facts/preferences only; "
            "playbook = ultra-short catalog title (what is stored, NO steps/commands/lists; "
            "prefer ≤30 chars, hard max 80; "
            "e.g. 'Domain records on Aliyun (39.106.34.112)'); "
            "detail = full steps/URLs for that key (not injected by default; memory_read); "
            "Never put details into playbook or every-turn context will bloat. "
            "Do NOT store one-off conversation process. "
            "Prefer updating an existing key; one exploration → at most one playbook+detail."
        ),
        "profile_heading": "## Profile (stable facts & preferences)",
        "playbook_heading": (
            "## Playbooks (short titles only — memory_read kind=detail for steps)"
        ),
        "truncated": "…(truncated)",
        "write_extra": (
            "kind=profile|playbook|detail; same key for a practice. "
            "playbook content: prefer ≤30 chars, max 80; details go in detail only."
        ),
        "read_desc": (
            "Read memories (including inactive/cold catalog items). "
            "Use kind=detail + playbook key for full steps."
        ),
        "forget_desc": "Forget by key. kind=playbook (or omitted) also forgets matching detail.",
    },
    "ja": {
        "title": "# 記憶",
        "discipline": (
            "記憶書き込み規律："
            "profile=安定したユーザー事実/好みのみ；"
            "playbook=極短いカタログ見出し（何が保存されているかだけ。手順・コマンド・一覧は書かない；"
            "推奨≤30文字、上限80文字）；"
            "detail=同一 key の手順/URL（既定では注入しない；memory_read）；"
            "明細を playbook に書かない（毎ターン注入が膨張する）。"
            "一度きりの会話過程は保存しない。"
            "既存 key 更新を優先；1回の探索で playbook+detail は最大1件。"
        ),
        "profile_heading": "## プロファイル（安定した事実と好み）",
        "playbook_heading": (
            "## 実践カタログ（短い見出しのみ；詳細は memory_read kind=detail）"
        ),
        "truncated": "…(省略)",
        "write_extra": (
            "kind=profile|playbook|detail。同一実践は同じ key。"
            "playbook content は推奨≤30文字・上限80文字；詳細は detail へ。"
        ),
        "read_desc": "記憶を読む（inactive/コールド含む）。詳細手順は kind=detail + playbook の key。",
        "forget_desc": "key で忘れる。kind=playbook（または省略）なら対応 detail も削除。",
    },
}

# Backward-compatible alias used by older tests/imports.
WRITE_DISCIPLINE = _MEMORY_I18N["zh"]["discipline"]


def _copy(locale: Locale) -> dict[str, str]:
    return _MEMORY_I18N[normalize_locale(locale)]


def resolve_memory_policy(policy: MemoryPolicy | None) -> MemoryPolicy:
    p = policy or MemoryPolicy()
    profile_k = p.profile_top_k
    playbook_k = p.playbook_top_k
    if p.longterm_top_k is not None and profile_k == 30 and p.longterm_top_k:
        profile_k = int(p.longterm_top_k)
    return MemoryPolicy(
        enabled=p.enabled,
        profile_top_k=profile_k,
        playbook_top_k=playbook_k,
        max_chars=p.max_chars,
        max_profile_items=p.max_profile_items,
        max_playbook_items=p.max_playbook_items,
        max_profile_chars=p.max_profile_chars,
        max_playbook_chars=p.max_playbook_chars,
        max_detail_chars=p.max_detail_chars,
        evict_when_full=p.evict_when_full,
        longterm_top_k=p.longterm_top_k,
    )


def builtin_memory_tools(locale: Locale | str = "zh") -> list[ToolDef]:
    copy = _copy(normalize_locale(locale))
    return [
        ToolDef(
            name="memory_write",
            description=f"{copy['discipline']} {copy['write_extra']}",
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["profile", "playbook", "detail"],
                    },
                    "key": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["kind", "key", "content"],
            },
        ),
        ToolDef(
            name="memory_read",
            description=copy["read_desc"],
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["profile", "playbook", "detail"],
                    },
                    "key": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": [],
            },
        ),
        ToolDef(
            name="memory_forget",
            description=copy["forget_desc"],
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["profile", "playbook", "detail"],
                    },
                    "key": {"type": "string"},
                },
                "required": ["key"],
            },
        ),
    ]


def merge_agent_tools_with_memory(
    agent_tools: list[ToolDef],
    *,
    enabled: bool,
    locale: Locale | str = "zh",
) -> list[ToolDef]:
    if not enabled:
        return list(agent_tools)
    existing = {t.name for t in agent_tools}
    merged = list(agent_tools)
    for tool in builtin_memory_tools(locale):
        if tool.name not in existing:
            merged.append(tool)
    return merged


def _clip_catalog_line(content: str, max_len: int, truncated_mark: str) -> str:
    text = (content or "").strip()
    limit = max(16, int(max_len))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + truncated_mark[:1]


def format_memory_block(
    profiles: list[Any],
    playbooks: list[Any],
    *,
    max_chars: int,
    locale: Locale | str = "zh",
    max_playbook_chars: int = 80,
) -> str:
    max_chars = max(200, int(max_chars))
    copy = _copy(normalize_locale(locale))
    profile_rows = list(profiles)
    playbook_rows = list(playbooks)
    if not profile_rows and not playbook_rows:
        return f"{copy['title']}\n({copy['discipline']})"

    def _render(profs: list[Any], books: list[Any]) -> str:
        parts = [copy["title"], f"({copy['discipline']})"]
        if profs:
            parts.append(copy["profile_heading"])
            for row in profs:
                parts.append(f"- [{row.key}] {row.content}")
        if books:
            parts.append(copy["playbook_heading"])
            for row in books:
                clipped = _clip_catalog_line(
                    str(getattr(row, "content", "") or ""),
                    max_playbook_chars,
                    copy["truncated"],
                )
                parts.append(f"- [{row.key}] {clipped}")
        return "\n".join(parts).strip()

    text = _render(profile_rows, playbook_rows)
    while len(text) > max_chars and playbook_rows:
        playbook_rows.pop()
        text = _render(profile_rows, playbook_rows)
    while len(text) > max_chars and profile_rows:
        profile_rows.pop()
        text = _render(profile_rows, playbook_rows)
    if len(text) > max_chars:
        text = text[: max_chars - 20].rstrip() + f"\n{copy['truncated']}"
    return text


async def load_memory_block(
    session: AsyncSession,
    *,
    agent_id: str,
    session_id: str,
    user_key: Optional[str],
    policy: MemoryPolicy,
    locale: Locale | str = "zh",
) -> str:
    del session_id
    policy = resolve_memory_policy(policy)
    if not policy.enabled or not (user_key or "").strip():
        return ""
    profiles, playbooks = await repo.load_memories_for_prompt(
        session,
        agent_id=agent_id,
        user_key=user_key,
        profile_top_k=policy.profile_top_k,
        playbook_top_k=policy.playbook_top_k,
    )
    return format_memory_block(
        profiles,
        playbooks,
        max_chars=policy.max_chars,
        locale=locale,
        max_playbook_chars=policy.max_playbook_chars,
    )


async def execute_memory_tool(
    session: AsyncSession,
    *,
    name: str,
    arguments: dict[str, Any],
    agent_id: str,
    session_id: str,
    user_key: Optional[str],
    run_id: Optional[str] = None,
    policy: Optional[MemoryPolicy] = None,
) -> dict[str, Any]:
    del session_id
    policy = resolve_memory_policy(policy)
    if not (user_key or "").strip():
        return {"ok": False, "error": "user_key required for memory"}

    if name == "memory_write":
        kind = str(arguments.get("kind") or "").strip()
        key = str(arguments.get("key") or "").strip()
        content = str(arguments.get("content") or "").strip()
        try:
            row, extras = await repo.upsert_memory(
                session,
                agent_id=agent_id,
                kind=kind,
                key=key,
                content=content,
                user_key=user_key,
                source_run_id=run_id,
                policy=policy,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "code": "memory_quota"}
        return {
            "ok": True,
            "data": {
                "id": row.id,
                "kind": row.kind,
                "key": row.key,
                "content": row.content,
                "active": row.active,
                **extras,
            },
        }

    if name == "memory_read":
        kind = (arguments.get("kind") or "").strip() or None
        if kind and kind not in MEMORY_KINDS:
            return {"ok": False, "error": "kind must be profile, playbook, or detail"}
        key = (arguments.get("key") or "").strip() or None
        query = (arguments.get("query") or "").strip() or None
        rows = await repo.search_memories(
            session,
            agent_id=agent_id,
            user_key=user_key,
            kind=kind,
            key=key,
            query=query,
            limit=50,
            include_inactive=True,
        )
        if rows:
            await repo.touch_memory_access(session, rows)
        return {
            "ok": True,
            "data": [
                {
                    "id": r.id,
                    "kind": getattr(r, "kind", None) or "profile",
                    "key": r.key,
                    "content": r.content,
                    "active": bool(getattr(r, "active", True)),
                }
                for r in rows
            ],
        }

    if name == "memory_forget":
        key = str(arguments.get("key") or "").strip()
        kind = (arguments.get("kind") or "").strip() or None
        if not key:
            return {"ok": False, "error": "key is required"}
        if kind and kind not in MEMORY_KINDS:
            return {"ok": False, "error": "kind must be profile, playbook, or detail"}
        result = await repo.forget_memory_by_key(
            session,
            agent_id=agent_id,
            key=key,
            user_key=user_key,
            kind=kind,
        )
        return {"ok": True, "data": result}

    return {"ok": False, "error": f"unknown memory tool: {name}"}
