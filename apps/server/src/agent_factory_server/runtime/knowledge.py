"""Knowledge base: catalog injection + kb_list / kb_read tools.

Design notes
------------
* The prompt only carries a *catalog* (path + one-line summary) — never bodies.
* The catalog is capped by ``KnowledgePolicy.max_catalog_chars``. Over budget we
  degrade in fixed steps instead of truncating mid-line:
      full (path + summary) → path only → folders with counts → top folders only
  The trailing hint always tells the model to use ``kb_list`` to expand.
* Folder tree is derived from document ``path``; there is no folder table.
* Visibility: every document belongs to one Agent. A session sees that Agent's
  shared docs (``scope="agent"``) plus its own user docs (``scope="user"``, same
  ``user_key``). No document is shared across Agents.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import KbDocumentOut, KnowledgePolicy, ToolDef
from agent_factory_server.runtime.locale import Locale, normalize_locale

KB_LIST_TOOL_NAME = "kb_list"
KB_READ_TOOL_NAME = "kb_read"
KB_TOOL_NAMES = frozenset({KB_LIST_TOOL_NAME, KB_READ_TOOL_NAME})

# Keep one page under context_policy.tool_result_max_chars (default 12000) so a
# linear document is never head+tail spliced by the projection layer.
KB_PAGE_CHARS = 8000
MAX_FOLDER_LINES = 40

_COPY: dict[str, dict[str, str]] = {
    "zh": {
        "title": "# 知识库（仅目录）",
        "discipline": (
            "正文不在提示中。当用户问题涉及以下内容时，先用 `kb_list(path=...)` 浏览目录，"
            "再用 `kb_read(doc_id=...)` 读正文；只读需要的文档，不要整库遍历。"
            "文档没写的内容不要编造，明确告诉用户知识库里没有。"
        ),
        "collapsed_hint": "（目录过多，已折叠为文件夹；用 kb_list(path=...) 展开）",
        "truncated_hint": "（目录超出注入预算，已截断；用 kb_list(path=...) 浏览）",
        "docs_suffix": "篇",
        "list_desc": (
            "列出知识库某个目录的直接子目录与文档（含标题与一句话摘要）。"
            "path 省略表示根目录。用于目录被折叠时逐层浏览。"
        ),
        "read_desc": (
            "读取知识库文档正文。doc_id 来自目录或 kb_list；"
            "文件较大时用 offset 分段读取，直到 next_offset 为 null。"
        ),
        "read_missing": "文档不存在或无权访问",
        "list_empty": "该目录下没有文档",
    },
    "en": {
        "title": "# Knowledge base (catalog only)",
        "discipline": (
            "Bodies are NOT in this prompt. When the request touches the topics below, "
            "call `kb_list(path=...)` to browse and `kb_read(doc_id=...)` to read. "
            "Only read the documents you need. Never invent policy that the knowledge base "
            "does not contain — say it is missing."
        ),
        "collapsed_hint": "(too many entries; collapsed to folders — expand with kb_list(path=...))",
        "truncated_hint": "(catalog exceeded its budget and was truncated — use kb_list(path=...))",
        "docs_suffix": "docs",
        "list_desc": (
            "List direct subfolders and documents under a knowledge-base folder "
            "(title + one-line summary). Omit path for the root. "
            "Use it when the catalog was collapsed."
        ),
        "read_desc": (
            "Read a knowledge-base document body. doc_id comes from the catalog or kb_list. "
            "Page large files with offset until next_offset is null."
        ),
        "read_missing": "document not found or not accessible",
        "list_empty": "no documents in this folder",
    },
    "ja": {
        "title": "# ナレッジベース（カタログのみ）",
        "discipline": (
            "本文はこのプロンプトに含まれません。下記に関係する質問では "
            "`kb_list(path=...)` で一覧し、`kb_read(doc_id=...)` で本文を読んでください。"
            "必要な文書だけを読み、書かれていない内容を推測しないでください。"
        ),
        "collapsed_hint": "（項目が多いためフォルダに折り畳みました。kb_list(path=...) で展開）",
        "truncated_hint": "（カタログが上限を超えたため切り詰めました。kb_list(path=...) を使用）",
        "docs_suffix": "件",
        "list_desc": (
            "ナレッジベースのフォルダ直下のサブフォルダと文書（タイトル＋一行要約）を一覧します。"
            "path を省略するとルート。カタログが折り畳まれたときに使います。"
        ),
        "read_desc": (
            "ナレッジベース文書の本文を読みます。doc_id はカタログまたは kb_list から取得します。"
            "大きい文書は offset で分割し、next_offset が null になるまで続けてください。"
        ),
        "read_missing": "文書が存在しないか権限がありません",
        "list_empty": "このフォルダに文書はありません",
    },
}


def _copy(locale: Locale | str) -> dict[str, str]:
    return _COPY.get(normalize_locale(locale)) or _COPY["zh"]


def resolve_knowledge_policy(policy: Optional[KnowledgePolicy]) -> KnowledgePolicy:
    p = policy or KnowledgePolicy()
    return KnowledgePolicy(
        enabled=bool(p.enabled),
        max_catalog_chars=max(200, min(int(p.max_catalog_chars or 3000), 20000)),
        include_user_docs=bool(p.include_user_docs),
        max_list_items=max(5, min(int(p.max_list_items or 50), 200)),
    )


# ---------------------------------------------------------------------------
# Catalog rendering (budget-aware)
# ---------------------------------------------------------------------------


def _doc_line_full(doc: KbDocumentOut) -> str:
    summary = (doc.summary or "").strip().replace("\n", " ")
    if summary:
        return f"- `{doc.path}` — {summary}"
    return f"- `{doc.path}`"


def _doc_line_path(doc: KbDocumentOut) -> str:
    return f"- `{doc.path}`"


def _group_by_folder(
    docs: list[KbDocumentOut], depth: int
) -> tuple[OrderedDict[str, int], list[KbDocumentOut]]:
    """Return (folder → doc count, top-level docs at this depth)."""
    folders: OrderedDict[str, int] = OrderedDict()
    loose: list[KbDocumentOut] = []
    for doc in docs:
        parts = doc.path.split("/")
        if len(parts) <= depth:
            loose.append(doc)
            continue
        folders["/".join(parts[:depth])] = folders.get("/".join(parts[:depth]), 0) + 1
    return folders, loose


def _render_full(docs: list[KbDocumentOut]) -> list[str]:
    return [_doc_line_full(d) for d in docs]


def _render_paths(docs: list[KbDocumentOut]) -> list[str]:
    return [_doc_line_path(d) for d in docs]


def _render_folders(
    docs: list[KbDocumentOut], *, depth: int, copy: dict[str, str]
) -> list[str]:
    folders, loose = _group_by_folder(docs, depth)
    lines: list[str] = []
    for name, count in list(folders.items())[:MAX_FOLDER_LINES]:
        lines.append(f"- `{name}/` ({count} {copy['docs_suffix']})")
    if len(folders) > MAX_FOLDER_LINES:
        lines.append(f"- … +{len(folders) - MAX_FOLDER_LINES} folders")
    lines.extend(_doc_line_path(d) for d in loose)
    return lines


def format_knowledge_catalog(
    docs: list[KbDocumentOut],
    *,
    max_chars: int,
    locale: Locale | str = "zh",
) -> str:
    """Render the injected catalog, degrading detail to stay within budget."""
    if not docs:
        return ""
    copy = _copy(locale)
    header = f"{copy['title']}\n({copy['discipline']})"
    budget = max(200, int(max_chars))

    ordered = sorted(docs, key=lambda d: d.path)
    attempts: list[tuple[list[str], str]] = [
        (_render_full(ordered), ""),
        (_render_paths(ordered), ""),
        (_render_folders(ordered, depth=1, copy=copy), copy["collapsed_hint"]),
        (_render_folders(ordered, depth=1, copy=copy), copy["truncated_hint"]),
    ]
    # Degradation ladder; the last two are identical except for the hint.
    for lines, hint in attempts:
        body = list(lines)
        if hint:
            body = [hint, *body]
        text = header + "\n" + "\n".join(body)
        if len(text) <= budget:
            return text

    # Nothing fits: keep as many folder lines as possible, then clip.
    lines, hint = attempts[-1]
    body: list[str] = [hint]
    for line in lines:
        candidate = header + "\n" + "\n".join([*body, line])
        if len(candidate) > budget:
            break
        body.append(line)
    text = header + "\n" + "\n".join(body)
    if len(text) > budget:
        text = text[: max(0, budget - 2)] + "…"
    return text


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def knowledge_tools(locale: Locale | str = "zh") -> list[ToolDef]:
    copy = _copy(locale)
    return [
        ToolDef(
            name=KB_LIST_TOOL_NAME,
            description=copy["list_desc"],
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder path (relative). Omit for the root.",
                    }
                },
                "required": [],
            },
        ),
        ToolDef(
            name=KB_READ_TOOL_NAME,
            description=copy["read_desc"],
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Character offset; follow next_offset when truncated.",
                    },
                },
                "required": ["doc_id"],
            },
        ),
    ]


def merge_agent_tools_with_knowledge(
    agent_tools: list[ToolDef],
    *,
    enabled: bool,
    locale: Locale | str = "zh",
) -> list[ToolDef]:
    if not enabled:
        return list(agent_tools)
    existing = {t.name for t in agent_tools}
    merged = list(agent_tools)
    for tool in knowledge_tools(locale):
        if tool.name not in existing:
            merged.append(tool)
    return merged


# ---------------------------------------------------------------------------
# Prompt block
# ---------------------------------------------------------------------------


async def load_knowledge_block(
    session: AsyncSession,
    *,
    agent_id: str,
    user_key: Optional[str],
    policy: Optional[KnowledgePolicy],
    locale: Locale | str = "zh",
) -> tuple[str, int]:
    """Return (catalog text, visible document count)."""
    resolved = resolve_knowledge_policy(policy)
    if not resolved.enabled:
        return "", 0
    docs = await repo.list_visible_kb_documents(
        session,
        agent_id=agent_id,
        user_key=user_key,
        include_user_docs=resolved.include_user_docs,
    )
    if not docs:
        return "", 0
    return (
        format_knowledge_catalog(
            docs, max_chars=resolved.max_catalog_chars, locale=locale
        ),
        len(docs),
    )


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def _is_visible(doc: KbDocumentOut, agent_id: str, user_key: Optional[str]) -> bool:
    if (doc.status or "active") != "active":
        return False
    if not agent_id or (doc.agent_id or "") != agent_id:
        return False
    if (doc.scope or "agent") == "agent":
        return True
    key = (user_key or "").strip()
    return bool(key) and (doc.user_key or "") == key


async def _list_folder(
    session: AsyncSession,
    *,
    agent_id: str,
    path: str,
    user_key: Optional[str],
    policy: KnowledgePolicy,
    locale: Locale | str,
) -> dict[str, Any]:
    copy = _copy(locale)
    raw = (path or "").strip().strip("/")
    prefix = f"{raw}/" if raw else ""
    docs = [
        d
        for d in await repo.list_visible_kb_documents(
            session,
            agent_id=agent_id,
            user_key=user_key,
            include_user_docs=policy.include_user_docs,
        )
        if _is_visible(d, agent_id, user_key) and d.path.startswith(prefix)
    ]

    folders: dict[str, int] = {}
    files: list[dict[str, Any]] = []
    for doc in sorted(docs, key=lambda d: d.path):
        rest = doc.path[len(prefix) :]
        if not rest:
            continue
        if "/" in rest:
            head = rest.split("/", 1)[0]
            folders[head] = folders.get(head, 0) + 1
            continue
        files.append(
            {
                "doc_id": doc.id,
                "path": doc.path,
                "title": doc.title,
                "summary": doc.summary,
                "chars": doc.chars,
                "scope": doc.scope,
            }
        )

    limit = policy.max_list_items
    entries: list[dict[str, Any]] = [
        {"type": "folder", "path": f"{prefix}{name}", "doc_count": count}
        for name, count in sorted(folders.items())
    ]
    entries.extend({"type": "doc", **item} for item in files)
    truncated = len(entries) > limit
    entries = entries[:limit]
    return {
        "ok": True,
        "data": {
            "path": raw,
            "entries": entries,
            "total": len(folders) + len(files),
            "truncated": truncated,
            "hint": copy["list_empty"] if not entries else "",
            "page_chars": KB_PAGE_CHARS,
        },
    }


async def _read_doc(
    session: AsyncSession,
    *,
    agent_id: str,
    doc_id: str,
    offset: int,
    user_key: Optional[str],
    locale: Locale | str,
) -> dict[str, Any]:
    copy = _copy(locale)
    row = await repo.get_kb_document_record(session, doc_id)
    if row is None:
        return {"ok": False, "error": copy["read_missing"]}
    doc = repo.kb_doc_to_out(row)
    if not _is_visible(doc, agent_id, user_key):
        return {"ok": False, "error": copy["read_missing"]}
    text = row.text_content or ""
    total = len(text)
    if offset > total:
        return {"ok": False, "error": f"offset {offset} beyond end of document ({total} chars)"}
    chunk = text[offset : offset + KB_PAGE_CHARS]
    next_offset = offset + len(chunk) if offset + len(chunk) < total else None
    return {
        "ok": True,
        "data": {
            "doc_id": doc.id,
            "path": doc.path,
            "title": doc.title,
            "summary": doc.summary,
            "scope": doc.scope,
            "content": chunk,
            "offset": offset,
            "total_chars": total,
            "next_offset": next_offset,
        },
    }


async def execute_knowledge_tool(
    session: AsyncSession,
    *,
    agent_id: str,
    name: str,
    arguments: dict[str, Any],
    user_key: Optional[str],
    policy: Optional[KnowledgePolicy],
    locale: Locale | str = "zh",
) -> dict[str, Any]:
    resolved = resolve_knowledge_policy(policy)
    if not resolved.enabled:
        return {"ok": False, "error": "knowledge base is disabled for this agent"}

    if name == KB_LIST_TOOL_NAME:
        return await _list_folder(
            session,
            agent_id=agent_id,
            path=str(arguments.get("path") or ""),
            user_key=user_key,
            policy=resolved,
            locale=locale,
        )

    if name == KB_READ_TOOL_NAME:
        doc_id = str(arguments.get("doc_id") or "").strip()
        if not doc_id:
            return {"ok": False, "error": "doc_id is required"}
        try:
            offset = int(arguments.get("offset") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid offset: must be a non-negative integer"}
        if offset < 0:
            return {"ok": False, "error": "invalid offset: must be a non-negative integer"}
        return await _read_doc(
            session,
            agent_id=agent_id,
            doc_id=doc_id,
            offset=offset,
            user_key=user_key,
            locale=locale,
        )

    return {"ok": False, "error": f"unknown knowledge tool: {name}"}
