"""Knowledge base API — nested under one Agent.

Every document belongs to exactly one Agent. Inside an Agent there are two
kinds of documents: ``scope=agent`` (shared by all sessions of that Agent) and
``scope=user`` (owned by one end user's ``user_key`` of that Agent). There is no
factory-wide corpus and no standalone knowledge-base menu.

The folder tree is derived from ``KbDocumentRecord.path``; there is no folder
table. Uploading into ``售后/退货/`` creates the folders implicitly, and folder
rename/delete rewrite every document under the prefix.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import AuthContext, enforce_agent_scope, require_agent_manager
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import (
    KbDocumentContent,
    KbDocumentOut,
    KbDocumentPage,
    KbDocumentUpdate,
    KbFolderDelete,
    KbFolderMove,
    KbFolderResult,
    KbUploadResult,
)
from agent_factory_server.runtime.knowledge_summary import generate_summary

router = APIRouter(
    prefix="/api/agents/{agent_id}/knowledge", tags=["knowledge"]
)

MAX_DOC_BYTES = 2 * 1024 * 1024
DOC_EXTENSIONS = frozenset(
    {".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml", ".csv"}
)


def _resolve_scope(scope: str, user_key: Optional[str]) -> tuple[str, str]:
    normalized = (scope or "agent").strip().lower()
    if normalized not in repo.KB_SCOPES:
        raise HTTPException(status_code=400, detail="scope must be agent or user")
    if normalized == "agent":
        return "agent", ""
    key = (user_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="user scope requires user_key")
    return "user", key


async def _require_agent(session: AsyncSession, agent_id: str) -> None:
    if await repo.get_agent(session, agent_id) is None:
        raise HTTPException(status_code=404, detail="Agent not found")


async def _require_doc(
    session: AsyncSession, agent_id: str, doc_id: str
) -> KbDocumentOut:
    """Load a document and make sure it really belongs to this Agent."""
    doc = await repo.get_kb_document(session, doc_id)
    if doc is None or (doc.agent_id or "") != agent_id:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


@router.get("/documents", response_model=KbDocumentPage)
async def list_documents(
    agent_id: str,
    scope: str = "agent",
    user_key: Optional[str] = None,
    path_prefix: Optional[str] = None,
    keyword: Optional[str] = None,
    include_disabled: bool = False,
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> KbDocumentPage:
    """Paginated table data.

    ``scope=user`` without ``user_key`` returns every end user of this Agent —
    that is the admin default; pass ``user_key`` to filter one user.
    """
    enforce_agent_scope(auth, agent_id)
    await _require_agent(session, agent_id)
    normalized = (scope or "agent").strip().lower()
    if normalized not in repo.KB_SCOPES:
        raise HTTPException(status_code=400, detail="scope must be agent or user")
    items, total, user_keys = await repo.page_kb_documents(
        session,
        agent_id=agent_id,
        scope=normalized,
        user_key=user_key,
        path_prefix=path_prefix,
        keyword=keyword,
        include_disabled=include_disabled,
        page=page,
        page_size=page_size,
    )
    return KbDocumentPage(
        items=items,
        total=total,
        page=max(1, int(page or 1)),
        page_size=max(1, min(int(page_size or 20), 200)),
        user_keys=user_keys,
    )


@router.get("/documents/{doc_id}/content", response_model=KbDocumentContent)
async def get_document_content(
    agent_id: str,
    doc_id: str,
    max_chars: int = 8000,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> KbDocumentContent:
    """Body preview for the detail drawer (正文不进列表接口)."""
    enforce_agent_scope(auth, agent_id)
    await _require_doc(session, agent_id, doc_id)
    row = await repo.get_kb_document_record(session, doc_id)
    text = (row.text_content or "") if row else ""
    limit = max(200, min(int(max_chars or 8000), 40000))
    return KbDocumentContent(
        id=doc_id,
        path=row.path if row else "",
        content=text[:limit],
        total_chars=len(text),
        truncated=len(text) > limit,
    )


@router.get("/documents/{doc_id}", response_model=KbDocumentOut)
async def get_document(
    agent_id: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> KbDocumentOut:
    enforce_agent_scope(auth, agent_id)
    return await _require_doc(session, agent_id, doc_id)


@router.post(
    "/documents",
    response_model=KbUploadResult,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    agent_id: str,
    file: UploadFile = File(...),
    scope: str = Form("agent"),
    user_key: Optional[str] = Form(None),
    dir: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    summary: Optional[str] = Form(None),
    generate: bool = Form(True),
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> KbUploadResult:
    """Upload one text document into this Agent's knowledge base.

    The factory always owns the summary: when ``summary`` is omitted it is
    generated here (LLM when a model profile exists, extraction otherwise).
    Re-uploading the same path keeps an existing *manual* summary.
    """
    enforce_agent_scope(auth, agent_id)
    resolved_scope, owner = _resolve_scope(scope, user_key)
    await _require_agent(session, agent_id)

    filename = (file.filename or "").strip().replace("\\", "/")
    if not filename:
        raise HTTPException(status_code=400, detail="file name is required")
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in DOC_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "unsupported document type "
                f"{suffix or '(none)'}; allowed: {', '.join(sorted(DOC_EXTENSIONS))}"
            ),
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(raw) > MAX_DOC_BYTES:
        raise HTTPException(status_code=400, detail="document too large (max 2MB)")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="document must be UTF-8 text"
        ) from exc

    leaf = PurePosixPath(filename).name
    try:
        path = repo.normalize_kb_path(f"{dir.strip().strip('/')}/{leaf}" if dir else leaf)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    digest = hashlib.sha256(raw).hexdigest()
    doc_title = (title or "").strip() or PurePosixPath(leaf).stem

    existing = await repo.find_kb_document_by_path(
        session,
        agent_id=agent_id,
        scope=resolved_scope,
        user_key=owner,
        path=path,
    )
    manual_summary = ""
    if existing is not None and (existing.summary_source or "") == "manual":
        manual_summary = existing.summary or ""

    warnings: list[str] = []
    if (summary or "").strip():
        final_summary, summary_source = summary.strip(), "manual"
    elif manual_summary:
        final_summary, summary_source = manual_summary, "manual"
        warnings.append("保留了已有的人工摘要")
    elif generate:
        final_summary, summary_source = await generate_summary(
            session, title=doc_title, text=text
        )
        if summary_source != "llm":
            warnings.append("未配置可用模型，摘要由本地规则抽取")
    else:
        final_summary, summary_source = "", "auto"

    doc, created = await repo.upsert_kb_document(
        session,
        agent_id=agent_id,
        scope=resolved_scope,
        user_key=owner,
        path=path,
        title=doc_title,
        text=text,
        summary=final_summary,
        summary_source=summary_source,
        sha256=digest,
        size_bytes=len(raw),
    )
    return KbUploadResult(
        document=doc,
        created=created,
        summary_source=summary_source,
        warnings=warnings,
    )


@router.patch("/documents/{doc_id}", response_model=KbDocumentOut)
async def patch_document(
    agent_id: str,
    doc_id: str,
    payload: KbDocumentUpdate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> KbDocumentOut:
    enforce_agent_scope(auth, agent_id)
    await _require_doc(session, agent_id, doc_id)
    try:
        doc = await repo.update_kb_document(session, doc_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    agent_id: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> None:
    enforce_agent_scope(auth, agent_id)
    await _require_doc(session, agent_id, doc_id)
    ok = await repo.delete_kb_document(session, doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="document not found")


@router.post("/folders/move", response_model=KbFolderResult)
async def move_folder(
    agent_id: str,
    payload: KbFolderMove,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> KbFolderResult:
    enforce_agent_scope(auth, agent_id)
    resolved_scope, owner = _resolve_scope(payload.scope, payload.user_key)
    await _require_agent(session, agent_id)
    try:
        moved = await repo.move_kb_folder(
            session,
            agent_id=agent_id,
            scope=resolved_scope,
            user_key=owner,
            path=payload.path,
            new_path=payload.new_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KbFolderResult(affected=moved)


@router.post("/folders/delete", response_model=KbFolderResult)
async def delete_folder(
    agent_id: str,
    payload: KbFolderDelete,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_agent_manager),
) -> KbFolderResult:
    enforce_agent_scope(auth, agent_id)
    resolved_scope, owner = _resolve_scope(payload.scope, payload.user_key)
    await _require_agent(session, agent_id)
    try:
        deleted = await repo.delete_kb_folder(
            session,
            agent_id=agent_id,
            scope=resolved_scope,
            user_key=owner,
            path=payload.path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KbFolderResult(affected=deleted)
