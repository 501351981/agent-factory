from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db.models import (
    AgentRecord,
    AgentSkillMountRecord,
    ItemRecord,
    LlmUsageRecord,
    MemoryRecord,
    MessageRecord,
    ModelProfileRecord,
    PlatformSkillPackageRecord,
    RunRecord,
    SessionQueueItemRecord,
    SessionRecord,
    SkillPackageFileRecord,
    SkillPackageRecord,
    utcnow,
)
from agent_factory_server.runtime.ui_theme import normalize_ui_theme, parse_ui_theme_json
from agent_factory_server.domain.schemas import (
    AgentCreate,
    AgentOut,
    AgentRuntimeConfig,
    AgentUpdate,
    LlmUsageOut,
    LlmUsagePage,
    MemoryCreate,
    MemoryOut,
    MemoryPage,
    MemoryUpdate,
    MessageOut,
    ModelProfileCreate,
    ModelProfileOut,
    ModelProfileUpdate,
    SessionCreate,
    SessionOut,
    SessionPage,
    SessionQueueItemOut,
    SessionQueueOut,
    SessionTimeline,
    SessionUpdate,
    AgentSkillMountOut,
    PlatformSkillPackageOut,
    SkillDef,
    SkillPackageFileOut,
    SkillPackageOut,
    TimelineRun,
    TimelineStep,
    ToolDef,
    UsageSummaryBucket,
    UsageSummaryOut,
)


def _loads_list(raw: str) -> list[Any]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _loads_obj(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _parse_runtime(raw: str | None) -> AgentRuntimeConfig:
    data = _loads_obj(raw)
    try:
        cfg = AgentRuntimeConfig.model_validate(data)
        if isinstance(data, dict) and "context_policy" not in data and "tool_result_max_chars" in data:
            cfg.context_policy.tool_result_max_chars = int(data["tool_result_max_chars"])
        return cfg
    except Exception:  # noqa: BLE001
        return AgentRuntimeConfig()


def _mask_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}****{key[-4:]}"


def reveal_profile_api_key(row: ModelProfileRecord) -> str:
    """Decrypt stored model API key for outbound LLM calls."""
    from agent_factory_server.runtime.model_secrets import reveal_model_api_key

    return reveal_model_api_key(row.api_key)


def model_to_out(row: ModelProfileRecord) -> ModelProfileOut:
    from agent_factory_server.runtime.model_secrets import reveal_model_api_key

    stored = (row.api_key or "").strip()
    try:
        plaintext = reveal_model_api_key(stored) if stored else ""
    except ValueError:
        # Key sealed under a different secret — still report presence.
        plaintext = ""
    return ModelProfileOut(
        id=row.id,
        name=row.name,
        provider=row.provider,
        base_url=row.base_url,
        api_key_masked=_mask_api_key(plaintext) if plaintext else ("****" if stored else ""),
        has_api_key=bool(stored),
        model_name=row.model_name,
        temperature=float(row.temperature if row.temperature is not None else 0.3),
        context_window_tokens=int(getattr(row, "context_window_tokens", None) or 128000),
        reserve_output_tokens=int(getattr(row, "reserve_output_tokens", None) or 8192),
        input_price_per_1m=float(getattr(row, "input_price_per_1m", None) or 0.0),
        output_price_per_1m=float(getattr(row, "output_price_per_1m", None) or 0.0),
        is_default=row.is_default,
        enabled=row.enabled,
        remark=row.remark,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _normalize_system_prompts(
    *,
    system_prompt: str,
    system_prompts: Optional[dict[str, str]],
) -> tuple[str, dict[str, str]]:
    """Return (primary system_prompt, cleaned locale map)."""
    cleaned: dict[str, str] = {}
    for key, value in (system_prompts or {}).items():
        loc = str(key).strip().lower()
        text = str(value or "").strip()
        if loc in ("zh", "en", "ja") and text:
            cleaned[loc] = text
    primary = (system_prompt or "").strip()
    if not cleaned and primary:
        cleaned = {"zh": primary}
    if not primary:
        primary = cleaned.get("zh") or next(iter(cleaned.values()), "你是一个有用的助手。")
    if "zh" not in cleaned and primary:
        cleaned = {"zh": primary, **cleaned}
    return primary, cleaned


def _agent_system_prompts(row: AgentRecord) -> dict[str, str]:
    raw = getattr(row, "system_prompts_json", None)
    data = _loads_obj(raw)
    cleaned = {
        str(k).strip().lower(): str(v).strip()
        for k, v in data.items()
        if str(k).strip().lower() in ("zh", "en", "ja") and str(v or "").strip()
    }
    if not cleaned and (row.system_prompt or "").strip():
        return {"zh": row.system_prompt.strip()}
    if "zh" not in cleaned and (row.system_prompt or "").strip():
        cleaned = {"zh": row.system_prompt.strip(), **cleaned}
    return cleaned


def _normalize_suggested_prompts(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        out.append(text[:120])
        if len(out) >= 12:
            break
    return out


def _agent_suggested_prompts(row: AgentRecord) -> list[str]:
    raw = getattr(row, "suggested_prompts_json", None)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return _normalize_suggested_prompts(parsed)


def agent_to_out(row: AgentRecord, profile_name: Optional[str] = None) -> AgentOut:
    prompts = _agent_system_prompts(row)
    return AgentOut(
        id=row.id,
        name=row.name,
        system_prompt=row.system_prompt,
        system_prompts=prompts,
        model=row.model,
        model_profile_id=row.model_profile_id,
        model_profile_name=profile_name,
        tools=[ToolDef.model_validate(item) for item in _loads_list(row.tools_json)],
        skills=[SkillDef.model_validate(item) for item in _loads_list(row.skills_json)],
        runtime=_parse_runtime(getattr(row, "runtime_json", None)),
        logo_url=(getattr(row, "logo_url", None) or None),
        welcome_title=(getattr(row, "welcome_title", None) or None),
        welcome_subtitle=(getattr(row, "welcome_subtitle", None) or None),
        suggested_prompts=_agent_suggested_prompts(row),
        ui_theme=parse_ui_theme_json(getattr(row, "ui_theme_json", None)),
        access_key=getattr(row, "access_key", None),
        has_secret_key=bool(getattr(row, "secret_key_hash", None)),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _profile_name(session: AsyncSession, profile_id: Optional[str]) -> Optional[str]:
    if not profile_id:
        return None
    row = await session.get(ModelProfileRecord, profile_id)
    return row.name if row else None


async def _clear_default_flags(session: AsyncSession, except_id: Optional[str] = None) -> None:
    result = await session.exec(select(ModelProfileRecord).where(ModelProfileRecord.is_default == True))  # noqa: E712
    for row in result.all():
        if except_id and row.id == except_id:
            continue
        row.is_default = False
        session.add(row)


async def create_model_profile(
    session: AsyncSession, payload: ModelProfileCreate
) -> ModelProfileOut:
    from agent_factory_server.runtime.model_secrets import seal_model_api_key

    if payload.is_default:
        await _clear_default_flags(session)
    row = ModelProfileRecord(
        name=payload.name,
        provider=payload.provider,
        base_url=payload.base_url.rstrip("/"),
        api_key=seal_model_api_key(payload.api_key),
        model_name=payload.model_name,
        temperature=payload.temperature,
        context_window_tokens=payload.context_window_tokens,
        reserve_output_tokens=payload.reserve_output_tokens,
        input_price_per_1m=float(payload.input_price_per_1m or 0.0),
        output_price_per_1m=float(payload.output_price_per_1m or 0.0),
        is_default=payload.is_default,
        enabled=payload.enabled,
        remark=payload.remark,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return model_to_out(row)


async def list_model_profiles(session: AsyncSession) -> list[ModelProfileOut]:
    result = await session.exec(
        select(ModelProfileRecord).order_by(ModelProfileRecord.created_at.desc())
    )
    return [model_to_out(row) for row in result.all()]


async def get_model_profile(
    session: AsyncSession, profile_id: str
) -> Optional[ModelProfileRecord]:
    return await session.get(ModelProfileRecord, profile_id)


async def get_default_model_profile(
    session: AsyncSession,
) -> Optional[ModelProfileRecord]:
    result = await session.exec(
        select(ModelProfileRecord).where(
            ModelProfileRecord.is_default == True,  # noqa: E712
            ModelProfileRecord.enabled == True,  # noqa: E712
        )
    )
    row = result.first()
    if row:
        return row
    result = await session.exec(
        select(ModelProfileRecord)
        .where(ModelProfileRecord.enabled == True)  # noqa: E712
        .order_by(ModelProfileRecord.created_at.desc())
    )
    return result.first()


async def update_model_profile(
    session: AsyncSession, profile_id: str, payload: ModelProfileUpdate
) -> Optional[ModelProfileOut]:
    from agent_factory_server.runtime.model_secrets import seal_model_api_key

    row = await session.get(ModelProfileRecord, profile_id)
    if not row:
        return None
    data = payload.model_dump(exclude_unset=True)
    if data.get("is_default"):
        await _clear_default_flags(session, except_id=profile_id)
    for key, value in data.items():
        if key == "base_url" and isinstance(value, str):
            value = value.rstrip("/")
        if key == "api_key":
            if value == "" or value is None:
                continue
            value = seal_model_api_key(str(value))
        setattr(row, key, value)
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return model_to_out(row)


async def reseal_plaintext_model_api_keys(session: AsyncSession) -> int:
    """One-shot: seal legacy plaintext model keys. Returns number updated."""
    from agent_factory_server.runtime.model_secrets import maybe_reseal_model_api_key

    result = await session.exec(select(ModelProfileRecord))
    rows = list(result.all())
    changed = 0
    for row in rows:
        sealed, did = maybe_reseal_model_api_key(row.api_key)
        if not did:
            continue
        row.api_key = sealed
        row.updated_at = utcnow()
        session.add(row)
        changed += 1
    if changed:
        await session.commit()
    return changed


async def delete_model_profile(session: AsyncSession, profile_id: str) -> bool:
    row = await session.get(ModelProfileRecord, profile_id)
    if not row:
        return False
    await session.delete(row)
    await session.commit()
    return True


def _session_prompt_vars(row: SessionRecord) -> dict[str, str]:
    raw = getattr(row, "prompt_vars_json", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


def session_to_out(row: SessionRecord) -> SessionOut:
    return SessionOut(
        id=row.id,
        agent_id=row.agent_id,
        title=row.title,
        user_key=getattr(row, "user_key", None),
        client_device_id=getattr(row, "client_device_id", None),
        status=getattr(row, "status", None) or "active",
        locale=getattr(row, "locale", None) or "zh",
        system_prompt=getattr(row, "system_prompt", None),
        prompt_vars=_session_prompt_vars(row),
        history_cleared_at=getattr(row, "history_cleared_at", None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def session_history_cleared_at(
    session: AsyncSession, session_id: str
) -> Optional[datetime]:
    row = await session.get(SessionRecord, session_id)
    if not row:
        return None
    return getattr(row, "history_cleared_at", None)


def memory_to_out(row: MemoryRecord) -> MemoryOut:
    return MemoryOut(
        id=row.id,
        agent_id=row.agent_id,
        kind=getattr(row, "kind", None) or "profile",
        key=row.key,
        content=row.content,
        user_key=row.user_key,
        active=bool(getattr(row, "active", True)),
        pinned=bool(getattr(row, "pinned", False)),
        metadata=_loads_obj(row.metadata_json) or None,
        source_run_id=row.source_run_id,
        last_accessed_at=getattr(row, "last_accessed_at", None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def message_to_out(row: MessageRecord) -> MessageOut:
    tool_calls = None
    if row.tool_calls_json:
        try:
            parsed = json.loads(row.tool_calls_json)
            if isinstance(parsed, list):
                tool_calls = parsed
        except json.JSONDecodeError:
            tool_calls = None
    return MessageOut(
        id=row.id,
        session_id=row.session_id,
        role=row.role,
        content=row.content,
        tool_call_id=row.tool_call_id,
        tool_calls=tool_calls,
        created_at=row.created_at,
    )


async def create_agent(session: AsyncSession, payload: AgentCreate) -> AgentOut:
    primary, prompts = _normalize_system_prompts(
        system_prompt=payload.system_prompt,
        system_prompts=payload.system_prompts,
    )
    suggested = _normalize_suggested_prompts(payload.suggested_prompts)
    row = AgentRecord(
        name=payload.name,
        system_prompt=primary,
        system_prompts_json=json.dumps(prompts, ensure_ascii=False),
        model=payload.model,
        model_profile_id=payload.model_profile_id,
        tools_json=json.dumps([t.model_dump() for t in payload.tools], ensure_ascii=False),
        skills_json=json.dumps([s.model_dump() for s in payload.skills], ensure_ascii=False),
        runtime_json=json.dumps(payload.runtime.model_dump(), ensure_ascii=False),
        logo_url=(payload.logo_url or "").strip() or None,
        welcome_title=(payload.welcome_title or "").strip() or None,
        welcome_subtitle=(payload.welcome_subtitle or "").strip() or None,
        suggested_prompts_json=json.dumps(suggested, ensure_ascii=False),
        ui_theme_json=json.dumps(
            normalize_ui_theme(payload.ui_theme), ensure_ascii=False
        ),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return agent_to_out(row, await _profile_name(session, row.model_profile_id))


async def list_agents(session: AsyncSession) -> list[AgentOut]:
    result = await session.exec(select(AgentRecord).order_by(AgentRecord.created_at.desc()))
    rows = result.all()
    out: list[AgentOut] = []
    for row in rows:
        out.append(agent_to_out(row, await _profile_name(session, row.model_profile_id)))
    return out


async def get_agent(session: AsyncSession, agent_id: str) -> Optional[AgentOut]:
    row = await session.get(AgentRecord, agent_id)
    if not row:
        return None
    return agent_to_out(row, await _profile_name(session, row.model_profile_id))


async def update_agent(
    session: AsyncSession, agent_id: str, payload: AgentUpdate
) -> Optional[AgentOut]:
    row = await session.get(AgentRecord, agent_id)
    if not row:
        return None
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        row.name = data["name"]
    if "system_prompts" in data or "system_prompt" in data:
        current = _agent_system_prompts(row)
        incoming = data.get("system_prompts")
        if isinstance(incoming, dict):
            current = {**current, **{str(k): str(v or "") for k, v in incoming.items()}}
            # Explicit empty string clears that locale.
            current = {k: v for k, v in current.items() if str(v).strip()}
        primary_in = data.get("system_prompt")
        if primary_in is not None and str(primary_in).strip():
            current["zh"] = str(primary_in).strip()
        primary, prompts = _normalize_system_prompts(
            system_prompt=str(primary_in) if primary_in is not None else row.system_prompt,
            system_prompts=current,
        )
        row.system_prompt = primary
        row.system_prompts_json = json.dumps(prompts, ensure_ascii=False)
    if "model" in data:
        row.model = data["model"]
    if "model_profile_id" in data:
        row.model_profile_id = data["model_profile_id"]
    if "tools" in data and payload.tools is not None:
        row.tools_json = json.dumps([t.model_dump() for t in payload.tools], ensure_ascii=False)
    if "skills" in data and payload.skills is not None:
        row.skills_json = json.dumps([s.model_dump() for s in payload.skills], ensure_ascii=False)
    if "runtime" in data and payload.runtime is not None:
        row.runtime_json = json.dumps(payload.runtime.model_dump(), ensure_ascii=False)
    if "logo_url" in data:
        row.logo_url = (str(data["logo_url"] or "").strip() or None)
    if "welcome_title" in data:
        row.welcome_title = (str(data["welcome_title"] or "").strip() or None)
    if "welcome_subtitle" in data:
        row.welcome_subtitle = (str(data["welcome_subtitle"] or "").strip() or None)
    if "suggested_prompts" in data and payload.suggested_prompts is not None:
        suggested = _normalize_suggested_prompts(payload.suggested_prompts)
        row.suggested_prompts_json = json.dumps(suggested, ensure_ascii=False)
    if "ui_theme" in data and payload.ui_theme is not None:
        row.ui_theme_json = json.dumps(
            normalize_ui_theme(payload.ui_theme), ensure_ascii=False
        )
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return agent_to_out(row, await _profile_name(session, row.model_profile_id))


async def delete_agent(session: AsyncSession, agent_id: str) -> bool:
    row = await session.get(AgentRecord, agent_id)
    if not row:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def get_agent_record_by_access_key(
    session: AsyncSession, access_key: str
) -> Optional[AgentRecord]:
    key = (access_key or "").strip()
    if not key:
        return None
    result = await session.exec(
        select(AgentRecord).where(AgentRecord.access_key == key)
    )
    return result.first()


async def rotate_agent_credentials(
    session: AsyncSession, agent_id: str
) -> Optional[tuple[AgentOut, str]]:
    """Generate new AK/SK. Returns (AgentOut, plaintext_secret) or None."""
    from agent_factory_server.runtime.credentials import (
        hash_secret_key,
        new_access_key,
        new_secret_key,
    )

    row = await session.get(AgentRecord, agent_id)
    if not row:
        return None
    access_key = new_access_key()
    # Ensure uniqueness (extremely unlikely collision).
    for _ in range(5):
        existing = await get_agent_record_by_access_key(session, access_key)
        if existing is None or existing.id == agent_id:
            break
        access_key = new_access_key()
    secret_key = new_secret_key()
    row.access_key = access_key
    row.secret_key_hash = hash_secret_key(secret_key)
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    out = agent_to_out(row, await _profile_name(session, row.model_profile_id))
    return out, secret_key


async def revoke_agent_credentials(session: AsyncSession, agent_id: str) -> Optional[AgentOut]:
    row = await session.get(AgentRecord, agent_id)
    if not row:
        return None
    row.access_key = None
    row.secret_key_hash = None
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return agent_to_out(row, await _profile_name(session, row.model_profile_id))


async def create_session(
    session: AsyncSession,
    payload: SessionCreate,
    *,
    system_prompt: Optional[str] = None,
    prompt_vars: Optional[dict[str, str]] = None,
    locale: Optional[str] = None,
) -> SessionOut:
    user_key = (payload.user_key or "").strip()
    if not user_key:
        raise ValueError("user_key is required")
    vars_map = prompt_vars if prompt_vars is not None else {}
    from agent_factory_server.runtime.locale import normalize_locale

    loc = normalize_locale(locale if locale is not None else payload.locale)
    device = (payload.client_device_id or "").strip() or None
    row = SessionRecord(
        agent_id=payload.agent_id,
        title=payload.title or "新会话",
        user_key=user_key,
        client_device_id=device,
        status="active",
        locale=loc,
        system_prompt=system_prompt,
        prompt_vars_json=json.dumps(vars_map, ensure_ascii=False),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return session_to_out(row)


async def get_session_row(session: AsyncSession, session_id: str) -> Optional[SessionRecord]:
    return await session.get(SessionRecord, session_id)


def _sessions_filter(
    agent_id: Optional[str] = None, *, include_archived: bool = False
):
    # Prefer active sessions; treat NULL/missing as active for legacy rows.
    clauses = []
    if not include_archived:
        clauses.append(
            or_(SessionRecord.status.is_(None), SessionRecord.status != "archived")
        )
    if agent_id:
        clauses.append(SessionRecord.agent_id == agent_id)
    return clauses


async def list_sessions(
    session: AsyncSession,
    agent_id: Optional[str] = None,
    *,
    page: int = 1,
    page_size: int = 20,
    include_archived: bool = False,
) -> SessionPage:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    clauses = _sessions_filter(agent_id, include_archived=include_archived)

    count_stmt = select(func.count()).select_from(SessionRecord)
    if clauses:
        count_stmt = count_stmt.where(*clauses)
    total = int((await session.exec(count_stmt)).one() or 0)

    stmt = select(SessionRecord)
    if clauses:
        stmt = stmt.where(*clauses)
    stmt = (
        stmt.order_by(SessionRecord.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await session.exec(stmt)).all())
    return SessionPage(
        items=[session_to_out(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


async def update_session(
    session: AsyncSession, session_id: str, payload: SessionUpdate
) -> Optional[SessionOut]:
    row = await session.get(SessionRecord, session_id)
    if not row:
        return None
    data = payload.model_dump(exclude_unset=True)
    if "title" in data:
        title = data["title"]
        row.title = (str(title).strip() if title is not None else "") or row.title
    if "status" in data and data["status"]:
        row.status = data["status"]
    if "client_device_id" in data:
        raw = data["client_device_id"]
        row.client_device_id = (str(raw).strip() or None) if raw is not None else None
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return session_to_out(row)


async def archive_session(session: AsyncSession, session_id: str) -> Optional[SessionOut]:
    """Soft-delete: hide from lists; data retained until hard delete."""
    row = await session.get(SessionRecord, session_id)
    if not row:
        return None
    row.status = "archived"
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return session_to_out(row)


async def clear_session_history(
    session: AsyncSession, session_id: str
) -> Optional[SessionOut]:
    """Soft-clear chat: watermark history; cancel queue / active runs."""
    row = await session.get(SessionRecord, session_id)
    if not row:
        return None
    if (getattr(row, "status", None) or "active") == "archived":
        return None

    for item in await list_queued_items(session, session_id):
        await cancel_queue_item(session, session_id, item.id)

    running = await get_running_run(session, session_id)
    if running:
        await finalize_run(
            session, running.id, status="cancelled", finish_reason="history_cleared"
        )
    waiting = await get_waiting_human_run(session, session_id)
    if waiting:
        await finalize_run(
            session, waiting.id, status="cancelled", finish_reason="history_cleared"
        )

    row.history_cleared_at = utcnow()
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return session_to_out(row)


async def hard_delete_session(session: AsyncSession, session_id: str) -> bool:
    """Permanently remove session and related rows (admin / platform only)."""
    row = await session.get(SessionRecord, session_id)
    if not row:
        return False

    from sqlalchemy import delete

    await session.exec(delete(ItemRecord).where(ItemRecord.session_id == session_id))  # type: ignore[call-overload]
    await session.exec(delete(RunRecord).where(RunRecord.session_id == session_id))  # type: ignore[call-overload]
    await session.exec(
        delete(MessageRecord).where(MessageRecord.session_id == session_id)
    )  # type: ignore[call-overload]
    await session.exec(
        delete(SessionQueueItemRecord).where(
            SessionQueueItemRecord.session_id == session_id
        )
    )  # type: ignore[call-overload]
    await session.exec(
        delete(LlmUsageRecord).where(LlmUsageRecord.session_id == session_id)
    )  # type: ignore[call-overload]
    await session.delete(row)
    await session.commit()
    return True


async def ensure_session_client_device(
    session: AsyncSession,
    session_id: str,
    *,
    preferred_device_id: Optional[str] = None,
) -> Optional[str]:
    """Sticky bind Session → Bridge device.

    - Existing binding wins (preferred cannot silently steal another device).
    - If unbound and preferred is set, bind preferred.
    - If unbound and exactly one bridge is online, auto-bind that bridge.
    - If unbound and multiple/zero bridges, return None (caller must error clearly).
    """
    row = await session.get(SessionRecord, session_id)
    if not row:
        return None
    current = (getattr(row, "client_device_id", None) or "").strip() or None
    if current:
        return current

    preferred = (preferred_device_id or "").strip() or None
    if preferred:
        row.client_device_id = preferred
        row.updated_at = utcnow()
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return preferred

    from agent_factory_server.tools.client_hub import client_hub

    online = client_hub.online_ids()
    if len(online) == 1:
        row.client_device_id = online[0]
        row.updated_at = utcnow()
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return online[0]
    return None


_DEFAULT_SESSION_TITLES = frozenset({"", "新会话", "New chat", "新しいセッション"})


async def touch_session(
    session: AsyncSession,
    session_id: str,
    *,
    title: Optional[str] = None,
) -> Optional[SessionOut]:
    """Bump updated_at; set title only when still a placeholder (first question wins)."""
    row = await session.get(SessionRecord, session_id)
    if not row:
        return None
    row.updated_at = utcnow()
    next_title = (title or "").strip()
    if next_title:
        current = (row.title or "").strip()
        if current in _DEFAULT_SESSION_TITLES:
            row.title = next_title[:80]
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return session_to_out(row)


async def add_message(
    session: AsyncSession,
    *,
    session_id: str,
    role: str,
    content: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    tool_calls: Optional[list[dict[str, Any]]] = None,
) -> MessageOut:
    """Legacy writer — prefer add_item. Kept for emergency compat."""
    row = MessageRecord(
        session_id=session_id,
        role=role,
        content=content,
        tool_call_id=tool_call_id,
        tool_calls_json=json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return message_to_out(row)


async def _session_has_items(session: AsyncSession, session_id: str) -> bool:
    result = await session.exec(
        select(ItemRecord.id).where(ItemRecord.session_id == session_id).limit(1)
    )
    return result.first() is not None


def _items_to_message_outs(session_id: str, items: list[ItemRecord]) -> list[MessageOut]:
    """Project items into legacy MessageOut list for old chat-ui."""
    out: list[MessageOut] = []
    pending_assistant_tools: Optional[MessageOut] = None
    for item in items:
        if item.type == "user_message":
            pending_assistant_tools = None
            out.append(
                MessageOut(
                    id=item.id,
                    session_id=session_id,
                    role="user",
                    content=item.content,
                    created_at=item.created_at,
                )
            )
        elif item.type == "assistant_message":
            payload = _loads_obj(item.payload_json)
            tool_calls = payload.get("tool_calls")
            msg = MessageOut(
                id=item.id,
                session_id=session_id,
                role="assistant",
                content=item.content,
                tool_calls=tool_calls if isinstance(tool_calls, list) else None,
                created_at=item.created_at,
            )
            out.append(msg)
            pending_assistant_tools = msg if tool_calls else None
        elif item.type == "tool_call":
            # Prefer embedding into prior assistant; if missing, synthesize.
            payload = _loads_obj(item.payload_json)
            call = payload.get("raw") or {
                "id": item.call_id,
                "type": "function",
                "function": {
                    "name": item.name or "",
                    "arguments": json.dumps(payload.get("arguments") or {}, ensure_ascii=False),
                },
            }
            if pending_assistant_tools and pending_assistant_tools.tool_calls is not None:
                # already stored on assistant_message with tool_calls
                continue
            if pending_assistant_tools is None:
                pending_assistant_tools = MessageOut(
                    id=item.id,
                    session_id=session_id,
                    role="assistant",
                    content=None,
                    tool_calls=[],
                    created_at=item.created_at,
                )
                out.append(pending_assistant_tools)
            assert pending_assistant_tools.tool_calls is not None
            if not any(
                isinstance(c, dict) and c.get("id") == item.call_id
                for c in pending_assistant_tools.tool_calls
            ):
                pending_assistant_tools.tool_calls.append(call)
        elif item.type == "tool_result":
            out.append(
                MessageOut(
                    id=item.id,
                    session_id=session_id,
                    role="tool",
                    content=item.content,
                    tool_call_id=item.call_id,
                    created_at=item.created_at,
                )
            )
        elif item.type == "reasoning":
            # Not part of legacy message list for old UI; skip or attach later.
            continue
    return out


async def list_messages(session: AsyncSession, session_id: str) -> list[MessageOut]:
    cleared_at = await session_history_cleared_at(session, session_id)
    if await _session_has_items(session, session_id):
        clauses = [ItemRecord.session_id == session_id]
        if cleared_at is not None:
            clauses.append(ItemRecord.created_at > cleared_at)
        result = await session.exec(
            select(ItemRecord)
            .where(*clauses)
            .order_by(ItemRecord.created_at.asc(), ItemRecord.seq.asc())
        )
        return _items_to_message_outs(session_id, list(result.all()))

    clauses = [MessageRecord.session_id == session_id]
    if cleared_at is not None:
        clauses.append(MessageRecord.created_at > cleared_at)
    result = await session.exec(
        select(MessageRecord)
        .where(*clauses)
        .order_by(MessageRecord.created_at.asc())
    )
    return [message_to_out(row) for row in result.all()]


# ---- Runs / Items ----


async def get_running_run(session: AsyncSession, session_id: str) -> Optional[RunRecord]:
    result = await session.exec(
        select(RunRecord).where(
            RunRecord.session_id == session_id,
            RunRecord.status == "running",
        )
    )
    return result.first()


async def get_waiting_human_run(
    session: AsyncSession, session_id: str
) -> Optional[RunRecord]:
    result = await session.exec(
        select(RunRecord).where(
            RunRecord.session_id == session_id,
            RunRecord.status == "waiting_human",
        )
    )
    return result.first()


async def get_blocking_run(session: AsyncSession, session_id: str) -> Optional[RunRecord]:
    """Running compute or paused waiting for human — blocks a new concurrent run."""
    result = await session.exec(
        select(RunRecord).where(
            RunRecord.session_id == session_id,
            RunRecord.status.in_(("running", "waiting_human")),
        )
    )
    return result.first()


async def list_running_runs(session: AsyncSession) -> list[RunRecord]:
    result = await session.exec(select(RunRecord).where(RunRecord.status == "running"))
    return list(result.all())


async def list_waiting_human_runs(session: AsyncSession) -> list[RunRecord]:
    result = await session.exec(
        select(RunRecord).where(RunRecord.status == "waiting_human")
    )
    return list(result.all())

async def list_open_tool_call_ids(session: AsyncSession, run_id: str) -> list[str]:
    """call_ids with tool_call but no tool_result yet (may be waiting on Bridge)."""
    result = await session.exec(
        select(ItemRecord).where(ItemRecord.run_id == run_id).order_by(ItemRecord.seq.asc())
    )
    items = list(result.all())
    seen_results = {i.call_id for i in items if i.type == "tool_result" and i.call_id}
    out: list[str] = []
    for item in items:
        if item.type == "tool_call" and item.call_id and item.call_id not in seen_results:
            out.append(item.call_id)
    return out


async def repair_orphan_tool_calls(session: AsyncSession, run_id: str) -> None:
    result = await session.exec(
        select(ItemRecord).where(ItemRecord.run_id == run_id).order_by(ItemRecord.seq.asc())
    )
    items = list(result.all())
    seen_results = {i.call_id for i in items if i.type == "tool_result" and i.call_id}
    next_seq = (max((i.seq for i in items), default=0)) + 1
    for item in items:
        if item.type == "tool_call" and item.call_id and item.call_id not in seen_results:
            await add_item(
                session,
                run_id=run_id,
                session_id=item.session_id,
                seq=next_seq,
                type="tool_result",
                call_id=item.call_id,
                name=item.name,
                content=json.dumps(
                    {"ok": False, "error": "interrupted"}, ensure_ascii=False
                ),
                payload={"ok": False, "error": "interrupted", "repaired": True},
            )
            next_seq += 1
            seen_results.add(item.call_id)


async def create_run(
    session: AsyncSession,
    *,
    session_id: str,
    user_input: str,
    model_name: Optional[str] = None,
) -> RunRecord:
    row = RunRecord(
        session_id=session_id,
        status="running",
        user_input=user_input,
        model_name=model_name,
        rounds_used=0,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_run(session: AsyncSession, run_id: str) -> Optional[RunRecord]:
    return await session.get(RunRecord, run_id)


async def bump_run_rounds(session: AsyncSession, run_id: str) -> int:
    row = await session.get(RunRecord, run_id)
    if not row:
        return 0
    row.rounds_used = int(row.rounds_used or 0) + 1
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row.rounds_used


async def sum_run_tokens(session: AsyncSession, run_id: str) -> int:
    """Tokens already spent by this run (survives ask_user / approval resumes)."""
    stmt = select(func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0)).where(
        LlmUsageRecord.run_id == run_id
    )
    return int((await session.exec(stmt)).one() or 0)


async def finalize_run(
    session: AsyncSession,
    run_id: str,
    *,
    status: str,
    finish_reason: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Optional[RunRecord]:
    row = await session.get(RunRecord, run_id)
    if not row:
        return None
    row.status = status
    row.finish_reason = finish_reason or status
    row.error_message = error_message
    row.finished_at = utcnow()
    row.waiting_expires_at = None
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def pause_run_waiting_human(
    session: AsyncSession,
    run_id: str,
    *,
    waiting_expires_at: datetime,
) -> Optional[RunRecord]:
    """Park run for HITL without holding a worker task. finished_at stays null."""
    row = await session.get(RunRecord, run_id)
    if not row:
        return None
    row.status = "waiting_human"
    row.finish_reason = None
    row.error_message = None
    row.finished_at = None
    row.waiting_expires_at = waiting_expires_at
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def resume_run_to_running(session: AsyncSession, run_id: str) -> Optional[RunRecord]:
    row = await session.get(RunRecord, run_id)
    if not row:
        return None
    row.status = "running"
    row.finish_reason = None
    row.error_message = None
    row.finished_at = None
    row.waiting_expires_at = None
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def find_pending_hitl_call(
    session: AsyncSession,
    run_id: str,
    *,
    call_id: Optional[str] = None,
    kind: Optional[str] = None,
) -> Optional[ItemRecord]:
    """Find a pending HITL tool_call (ask_user or approval) without a tool_result."""
    from agent_factory_server.runtime.hitl import hitl_kind_from_item

    result = await session.exec(
        select(ItemRecord)
        .where(ItemRecord.run_id == run_id, ItemRecord.type == "tool_call")
        .order_by(ItemRecord.seq.asc())
    )
    calls = list(result.all())
    result_ids = {
        i.call_id
        for i in (
            await session.exec(
                select(ItemRecord).where(
                    ItemRecord.run_id == run_id, ItemRecord.type == "tool_result"
                )
            )
        ).all()
        if i.call_id
    }
    for item in calls:
        if not item.call_id or item.call_id in result_ids:
            continue
        if call_id and item.call_id != call_id:
            continue
        item_kind = hitl_kind_from_item(name=item.name, payload_json=item.payload_json)
        if item_kind is None:
            continue
        if kind and item_kind != kind:
            continue
        return item
    return None


async def find_pending_ask_user_call(
    session: AsyncSession, run_id: str, *, call_id: Optional[str] = None
) -> Optional[ItemRecord]:
    from agent_factory_server.runtime.hitl import HITL_KIND_ASK

    return await find_pending_hitl_call(
        session, run_id, call_id=call_id, kind=HITL_KIND_ASK
    )


async def find_pending_approval_call(
    session: AsyncSession, run_id: str, *, call_id: Optional[str] = None
) -> Optional[ItemRecord]:
    from agent_factory_server.runtime.hitl import HITL_KIND_APPROVAL

    return await find_pending_hitl_call(
        session, run_id, call_id=call_id, kind=HITL_KIND_APPROVAL
    )


async def cancel_waiting_human_run(
    session: AsyncSession,
    run_id: str,
    *,
    reason: str,
    error_message: Optional[str] = None,
) -> Optional[RunRecord]:
    """Cancel a waiting_human run and close any pending HITL tool_call."""
    row = await session.get(RunRecord, run_id)
    if not row or row.status != "waiting_human":
        return None
    pending = await find_pending_hitl_call(session, run_id)
    if pending and pending.call_id:
        await add_item(
            session,
            run_id=run_id,
            session_id=row.session_id,
            type="tool_result",
            call_id=pending.call_id,
            name=pending.name or "tool",
            content=json.dumps(
                {"ok": False, "error": reason, "cancelled": True}, ensure_ascii=False
            ),
            payload={"ok": False, "error": reason, "cancelled": True},
        )
    return await finalize_run(
        session,
        run_id,
        status="cancelled",
        finish_reason=reason,
        error_message=error_message or reason,
    )


async def supersede_waiting_human_for_session(
    session: AsyncSession, session_id: str
) -> Optional[RunRecord]:
    waiting = await get_waiting_human_run(session, session_id)
    if not waiting:
        return None
    return await cancel_waiting_human_run(
        session,
        waiting.id,
        reason="superseded",
        error_message="已被同会话新消息抢占",
    )


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite may round-trip timezone-aware columns as naive; normalize for compare."""
    if value.tzinfo is None:
        from datetime import timezone

        return value.replace(tzinfo=timezone.utc)
    return value


async def expire_waiting_human_runs(session: AsyncSession) -> int:
    """Cancel waiting_human runs past waiting_expires_at. Safe to call often."""
    rows = await list_waiting_human_runs(session)
    now = _as_aware_utc(utcnow())
    count = 0
    for run in rows:
        expires = run.waiting_expires_at
        if expires is None or _as_aware_utc(expires) > now:
            continue
        await cancel_waiting_human_run(
            session,
            run.id,
            reason="timeout",
            error_message="等待用户回答超时",
        )
        count += 1
    return count


def queue_item_to_out(row: SessionQueueItemRecord) -> SessionQueueItemOut:
    return SessionQueueItemOut(
        id=row.id,
        session_id=row.session_id,
        seq=row.seq,
        message=row.message,
        model_profile_id=row.model_profile_id,
        status=row.status,
        run_id=row.run_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def next_queue_seq(session: AsyncSession, session_id: str) -> int:
    result = await session.exec(
        select(SessionQueueItemRecord.seq)
        .where(SessionQueueItemRecord.session_id == session_id)
        .order_by(SessionQueueItemRecord.seq.desc())
    )
    current = result.first()
    return int(current or 0) + 1


async def enqueue_session_message(
    session: AsyncSession,
    *,
    session_id: str,
    message: str,
    model_profile_id: Optional[str] = None,
) -> SessionQueueItemOut:
    seq = await next_queue_seq(session, session_id)
    row = SessionQueueItemRecord(
        session_id=session_id,
        seq=seq,
        message=message,
        model_profile_id=(model_profile_id or "").strip() or None,
        status="queued",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return queue_item_to_out(row)


async def list_queued_items(
    session: AsyncSession, session_id: str
) -> list[SessionQueueItemOut]:
    result = await session.exec(
        select(SessionQueueItemRecord)
        .where(
            SessionQueueItemRecord.session_id == session_id,
            SessionQueueItemRecord.status == "queued",
        )
        .order_by(SessionQueueItemRecord.seq.asc())
    )
    return [queue_item_to_out(r) for r in result.all()]


async def get_session_queue(
    session: AsyncSession, session_id: str
) -> SessionQueueOut:
    return SessionQueueOut(
        session_id=session_id,
        items=await list_queued_items(session, session_id),
    )


async def get_queue_item(
    session: AsyncSession, item_id: str
) -> Optional[SessionQueueItemRecord]:
    return await session.get(SessionQueueItemRecord, item_id)


async def cancel_queue_item(
    session: AsyncSession, session_id: str, item_id: str
) -> Optional[SessionQueueItemOut]:
    row = await session.get(SessionQueueItemRecord, item_id)
    if not row or row.session_id != session_id:
        return None
    if row.status != "queued":
        return queue_item_to_out(row)
    row.status = "cancelled"
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return queue_item_to_out(row)


async def claim_next_queue_item(
    session: AsyncSession, session_id: str
) -> Optional[SessionQueueItemRecord]:
    """Atomically claim the oldest queued item for dispatch (single-process safe)."""
    result = await session.exec(
        select(SessionQueueItemRecord)
        .where(
            SessionQueueItemRecord.session_id == session_id,
            SessionQueueItemRecord.status == "queued",
        )
        .order_by(SessionQueueItemRecord.seq.asc())
    )
    row = result.first()
    if not row:
        return None
    row.status = "dispatched"
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def bind_queue_item_run(
    session: AsyncSession, item_id: str, run_id: str
) -> None:
    row = await session.get(SessionQueueItemRecord, item_id)
    if not row:
        return
    row.run_id = run_id
    row.status = "dispatched"
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()


async def requeue_item(session: AsyncSession, item_id: str) -> None:
    """Put a claimed item back to queued if start failed before run_start."""
    row = await session.get(SessionQueueItemRecord, item_id)
    if not row or row.status != "dispatched" or row.run_id:
        return
    row.status = "queued"
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()


async def list_session_ids_with_queued(session: AsyncSession) -> list[str]:
    result = await session.exec(
        select(SessionQueueItemRecord.session_id)
        .where(SessionQueueItemRecord.status == "queued")
        .distinct()
    )
    return [str(x) for x in result.all() if x]


async def next_item_seq(session: AsyncSession, run_id: str) -> int:
    result = await session.exec(
        select(ItemRecord.seq).where(ItemRecord.run_id == run_id).order_by(ItemRecord.seq.desc())
    )
    current = result.first()
    return int(current or 0) + 1


async def add_item(
    session: AsyncSession,
    *,
    run_id: str,
    session_id: str,
    type: str,
    content: Optional[str] = None,
    call_id: Optional[str] = None,
    name: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    visibility: str = "user",
    seq: Optional[int] = None,
) -> ItemRecord:
    if seq is None:
        seq = await next_item_seq(session, run_id)
    row = ItemRecord(
        run_id=run_id,
        session_id=session_id,
        seq=seq,
        type=type,
        call_id=call_id,
        name=name,
        content=content,
        payload_json=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
        visibility=visibility,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_items_for_session(session: AsyncSession, session_id: str) -> list[ItemRecord]:
    cleared_at = await session_history_cleared_at(session, session_id)
    clauses = [ItemRecord.session_id == session_id]
    if cleared_at is not None:
        clauses.append(ItemRecord.created_at > cleared_at)
    result = await session.exec(
        select(ItemRecord)
        .where(*clauses)
        .order_by(ItemRecord.created_at.asc(), ItemRecord.seq.asc())
    )
    return list(result.all())


async def list_runs_for_session(session: AsyncSession, session_id: str) -> list[RunRecord]:
    cleared_at = await session_history_cleared_at(session, session_id)
    clauses = [RunRecord.session_id == session_id]
    if cleared_at is not None:
        clauses.append(RunRecord.started_at > cleared_at)
    result = await session.exec(
        select(RunRecord)
        .where(*clauses)
        .order_by(RunRecord.started_at.asc())
    )
    return list(result.all())


async def get_latest_context_summary(
    session: AsyncSession, session_id: str
) -> Optional[ItemRecord]:
    cleared_at = await session_history_cleared_at(session, session_id)
    clauses = [
        ItemRecord.session_id == session_id,
        ItemRecord.type == "context_summary",
    ]
    if cleared_at is not None:
        clauses.append(ItemRecord.created_at > cleared_at)
    result = await session.exec(
        select(ItemRecord)
        .where(*clauses)
        .order_by(ItemRecord.created_at.desc())
    )
    return result.first()


async def list_prompt_snapshots(
    session: AsyncSession, session_id: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    cleared_at = await session_history_cleared_at(session, session_id)
    clauses = [
        ItemRecord.session_id == session_id,
        ItemRecord.type == "prompt_snapshot",
    ]
    if cleared_at is not None:
        clauses.append(ItemRecord.created_at > cleared_at)
    result = await session.exec(
        select(ItemRecord)
        .where(*clauses)
        .order_by(ItemRecord.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    out: list[dict[str, Any]] = []
    for item in result.all():
        payload = _loads_obj(item.payload_json)
        out.append(
            {
                "id": item.id,
                "run_id": item.run_id,
                "created_at": item.created_at,
                "agent_system_prompt": str(payload.get("agent_system_prompt") or ""),
                "memory_block": str(payload.get("memory_block") or ""),
                "context_summary": str(payload.get("context_summary") or ""),
                "message_count": int(payload.get("message_count") or 0),
                "estimated_tokens_after": payload.get("estimated_tokens_after"),
                "layers_applied": list(payload.get("layers_applied") or []),
            }
        )
    return out


async def get_session_timeline(session: AsyncSession, session_id: str) -> SessionTimeline:
    runs = await list_runs_for_session(session, session_id)
    items = await list_items_for_session(session, session_id)
    by_run: dict[str, list[ItemRecord]] = {}
    for item in items:
        by_run.setdefault(item.run_id, []).append(item)

    timeline_runs: list[TimelineRun] = []
    for run in runs:
        steps: list[TimelineStep] = []
        for item in by_run.get(run.id, []):
            if item.type in ("user_message", "context_summary", "prompt_snapshot"):
                continue
            steps.append(
                TimelineStep(
                    id=item.id,
                    type=item.type,
                    call_id=item.call_id,
                    name=item.name,
                    content=item.content,
                    payload=_loads_obj(item.payload_json) or None,
                    created_at=item.created_at,
                )
            )
        timeline_runs.append(
            TimelineRun(
                run_id=run.id,
                status=run.status,
                user_message=run.user_input,
                model_name=run.model_name,
                rounds_used=run.rounds_used,
                finish_reason=run.finish_reason,
                error_message=run.error_message,
                started_at=run.started_at,
                finished_at=run.finished_at,
                waiting_expires_at=getattr(run, "waiting_expires_at", None),
                steps=steps,
            )
        )
    return SessionTimeline(session_id=session_id, runs=timeline_runs)


def truncate_tool_content(content: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(content) <= max_chars:
        return content, False
    keep = max(max_chars // 2 - 20, 0)
    truncated = (
        content[:keep]
        + f"\n...[truncated {len(content) - max_chars} chars]...\n"
        + content[-keep:]
    )
    return truncated, True


def _tool_call_ids(tool_calls: Any) -> list[str]:
    if not isinstance(tool_calls, list):
        return []
    ids: list[str] = []
    for call in tool_calls:
        if isinstance(call, dict) and call.get("id"):
            ids.append(str(call["id"]))
    return ids


def _append_missing_tool_results(
    messages: list[dict[str, Any]], pending_ids: list[str]
) -> None:
    """Ensure every tool_call_id has a following tool message (API hard requirement)."""
    for call_id in pending_ids:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(
                    {"ok": False, "error": "missing tool result"}, ensure_ascii=False
                ),
            }
        )


def project_items_for_llm(
    system_prompt: str,
    items: list[ItemRecord],
    *,
    include_reasoning: bool = False,
) -> list[dict[str, Any]]:
    """Build OpenAI-compatible messages from stored items.

    Important: `tool_call` items are UI/audit duplicates. The model-facing
    assistant turn already carries `payload.tool_calls`. Re-emitting those as
    new assistant messages after the first tool_result breaks the invariant
    "assistant.tool_calls must be followed by tool messages for each id".
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    pending_ids: list[str] = []
    covered_call_ids: set[str] = set()

    for item in items:
        if item.type == "user_message":
            if pending_ids:
                _append_missing_tool_results(messages, pending_ids)
                pending_ids = []
            messages.append({"role": "user", "content": item.content or ""})
            continue

        if item.type == "reasoning":
            # Replay handled via assistant payload.reasoning_content when needed.
            continue

        if item.type == "assistant_message":
            if pending_ids:
                _append_missing_tool_results(messages, pending_ids)
                pending_ids = []

            payload = _loads_obj(item.payload_json)
            tool_calls = payload.get("tool_calls")
            msg: dict[str, Any] = {"role": "assistant", "content": item.content}
            if isinstance(tool_calls, list) and tool_calls:
                msg["tool_calls"] = tool_calls
                if item.content is None:
                    msg["content"] = None
                pending_ids = _tool_call_ids(tool_calls)
                covered_call_ids.update(pending_ids)
            reasoning = payload.get("reasoning_content")
            if include_reasoning and reasoning:
                msg["reasoning_content"] = reasoning
            messages.append(msg)
            continue

        if item.type == "tool_call":
            # Skip: already represented on assistant_message.tool_calls.
            # Only synthesize if this call was never declared (legacy/orphan rows).
            call_id = item.call_id or ""
            if call_id and call_id in covered_call_ids:
                continue
            if call_id and call_id in pending_ids:
                continue
            raw = _loads_obj(item.payload_json).get("raw")
            call = raw or {
                "id": call_id or f"call_{item.id}",
                "type": "function",
                "function": {
                    "name": item.name or "",
                    "arguments": json.dumps(
                        _loads_obj(item.payload_json).get("arguments") or {},
                        ensure_ascii=False,
                    ),
                },
            }
            cid = str(call.get("id") or call_id)
            if pending_ids:
                # Still waiting on a previous assistant — do not open a new one.
                covered_call_ids.add(cid)
                continue
            messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
            pending_ids = [cid]
            covered_call_ids.add(cid)
            continue

        if item.type == "tool_result":
            call_id = item.call_id or ""
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": item.content or "",
                }
            )
            if call_id in pending_ids:
                pending_ids = [x for x in pending_ids if x != call_id]
            continue

        # system_notice etc. — UI only

    if pending_ids:
        _append_missing_tool_results(messages, pending_ids)

    return messages


async def project_for_llm(
    session: AsyncSession,
    session_id: str,
    system_prompt: str,
    *,
    include_reasoning: bool = False,
) -> list[dict[str, Any]]:
    items = await list_items_for_session(session, session_id)
    if items:
        return project_items_for_llm(
            system_prompt, items, include_reasoning=include_reasoning
        )
    # Legacy fallback
    history = await list_messages(session, session_id)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for item in history:
        if item.role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.tool_call_id or "",
                    "content": item.content or "",
                }
            )
        elif item.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": item.content}
            if item.tool_calls:
                msg["tool_calls"] = item.tool_calls
            messages.append(msg)
        else:
            messages.append({"role": item.role, "content": item.content or ""})
    return messages


async def list_memories(
    session: AsyncSession,
    *,
    agent_id: Optional[str] = None,
    user_key: Optional[str] = None,
    kind: Optional[str] = None,
    include_deleted: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> MemoryPage:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    clauses = [MemoryRecord.scope == "longterm"]
    if agent_id:
        clauses.append(MemoryRecord.agent_id == agent_id)
    if user_key:
        clauses.append(MemoryRecord.user_key == user_key)
    if kind:
        clauses.append(MemoryRecord.kind == kind)
    if not include_deleted:
        clauses.append(MemoryRecord.deleted_at.is_(None))

    count_stmt = select(func.count()).select_from(MemoryRecord).where(*clauses)
    total = int((await session.exec(count_stmt)).one() or 0)
    stmt = (
        select(MemoryRecord)
        .where(*clauses)
        .order_by(MemoryRecord.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await session.exec(stmt)).all())
    return MemoryPage(
        items=[memory_to_out(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_memory(session: AsyncSession, memory_id: str) -> Optional[MemoryOut]:
    row = await session.get(MemoryRecord, memory_id)
    if not row or row.deleted_at is not None:
        return None
    if row.scope != "longterm":
        return None
    return memory_to_out(row)


async def _find_active_memory(
    session: AsyncSession,
    *,
    kind: str,
    key: str,
    agent_id: str,
    user_key: Optional[str],
) -> Optional[MemoryRecord]:
    stmt = select(MemoryRecord).where(
        MemoryRecord.scope == "longterm",
        MemoryRecord.kind == kind,
        MemoryRecord.key == key,
        MemoryRecord.agent_id == agent_id,
        MemoryRecord.user_key == user_key,
        MemoryRecord.deleted_at.is_(None),
    )
    result = await session.exec(stmt)
    return result.first()


async def _count_active_memories(
    session: AsyncSession,
    *,
    agent_id: str,
    user_key: str,
    kind: str,
) -> int:
    stmt = select(MemoryRecord).where(
        MemoryRecord.agent_id == agent_id,
        MemoryRecord.user_key == user_key,
        MemoryRecord.kind == kind,
        MemoryRecord.scope == "longterm",
        MemoryRecord.deleted_at.is_(None),
        MemoryRecord.active == True,  # noqa: E712
    )
    rows = list((await session.exec(stmt)).all())
    return len(rows)


async def _deactivate_oldest(
    session: AsyncSession,
    *,
    agent_id: str,
    user_key: str,
    kind: str,
    need: int,
) -> list[str]:
    """Deactivate oldest unpinned active rows (LRU by last_accessed then updated_at)."""
    if need <= 0:
        return []
    stmt = (
        select(MemoryRecord)
        .where(
            MemoryRecord.agent_id == agent_id,
            MemoryRecord.user_key == user_key,
            MemoryRecord.kind == kind,
            MemoryRecord.scope == "longterm",
            MemoryRecord.deleted_at.is_(None),
            MemoryRecord.active == True,  # noqa: E712
            MemoryRecord.pinned == False,  # noqa: E712
        )
        .order_by(MemoryRecord.updated_at.asc())
        .limit(need)
    )
    victims = list((await session.exec(stmt)).all())
    deactivated: list[str] = []
    now = utcnow()
    for row in victims:
        row.active = False
        row.updated_at = now
        session.add(row)
        deactivated.append(row.key)
        if kind == "playbook":
            detail = await _find_active_memory(
                session,
                kind="detail",
                key=row.key,
                agent_id=agent_id,
                user_key=user_key,
            )
            if detail and detail.deleted_at is None and detail.active:
                detail.active = False
                detail.updated_at = now
                session.add(detail)
    return deactivated


async def upsert_memory(
    session: AsyncSession,
    *,
    agent_id: str,
    kind: str,
    key: str,
    content: str,
    user_key: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    source_run_id: Optional[str] = None,
    policy: Optional[Any] = None,
) -> tuple[MemoryOut, dict[str, Any]]:
    """Upsert memory. Returns (row, meta) where meta may include evicted_keys / truncated."""
    from agent_factory_server.domain.schemas import MemoryPolicy

    policy = policy if isinstance(policy, MemoryPolicy) else MemoryPolicy()
    key = (key or "").strip()
    content = (content or "").strip()
    kind = (kind or "").strip() or "profile"
    user_key = (user_key or "").strip() or None
    extras: dict[str, Any] = {"evicted_keys": [], "truncated": False}
    if kind not in ("profile", "playbook", "detail"):
        raise ValueError("kind must be profile, playbook, or detail")
    if not key:
        raise ValueError("memory key is required")
    if not content:
        raise ValueError("memory content is required")
    if not user_key:
        raise ValueError("memory requires user_key")

    max_chars = {
        "profile": policy.max_profile_chars,
        "playbook": policy.max_playbook_chars,
        "detail": policy.max_detail_chars,
    }[kind]
    if len(content) > max_chars:
        content = content[: max(0, max_chars - 20)].rstrip() + "…(truncated)"
        extras["truncated"] = True

    row = await _find_active_memory(
        session,
        kind=kind,
        key=key,
        agent_id=agent_id,
        user_key=user_key,
    )
    now = utcnow()
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
    is_new = row is None
    was_inactive = bool(row and not getattr(row, "active", True))

    if kind in ("profile", "playbook") and (is_new or was_inactive):
        limit = policy.max_profile_items if kind == "profile" else policy.max_playbook_items
        active_count = await _count_active_memories(
            session, agent_id=agent_id, user_key=user_key, kind=kind
        )
        # Reactivating or inserting needs a free slot.
        if active_count >= limit:
            if not policy.evict_when_full:
                raise ValueError(
                    f"{kind} quota full ({active_count}/{limit}); enable evict_when_full or forget old memories"
                )
            need = active_count - limit + 1
            extras["evicted_keys"] = await _deactivate_oldest(
                session,
                agent_id=agent_id,
                user_key=user_key,
                kind=kind,
                need=need,
            )
            if not extras["evicted_keys"] and active_count >= limit:
                raise ValueError(
                    f"{kind} quota full ({active_count}/{limit}) and no unpinned rows to evict"
                )

    if row:
        row.content = content
        row.updated_at = now
        row.active = True
        row.last_accessed_at = now
        if metadata is not None:
            row.metadata_json = meta_json
        if source_run_id:
            row.source_run_id = source_run_id
        session.add(row)
    else:
        row = MemoryRecord(
            agent_id=agent_id,
            scope="longterm",
            kind=kind,
            key=key,
            content=content,
            user_key=user_key,
            session_id=None,
            active=True,
            pinned=False,
            metadata_json=meta_json,
            source_run_id=source_run_id,
            last_accessed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    await session.commit()
    await session.refresh(row)
    return memory_to_out(row), extras


async def create_memory(
    session: AsyncSession,
    payload: MemoryCreate,
    *,
    policy: Optional[Any] = None,
) -> MemoryOut:
    row, _extras = await upsert_memory(
        session,
        agent_id=payload.agent_id,
        kind=payload.kind,
        key=payload.key,
        content=payload.content,
        user_key=payload.user_key,
        metadata=payload.metadata,
        policy=policy,
    )
    return row


async def update_memory(
    session: AsyncSession, memory_id: str, payload: MemoryUpdate
) -> Optional[MemoryOut]:
    row = await session.get(MemoryRecord, memory_id)
    if not row or row.deleted_at is not None:
        return None
    data = payload.model_dump(exclude_unset=True)
    if "content" in data and data["content"] is not None:
        content = str(data["content"]).strip()
        if not content:
            raise ValueError("memory content is required")
        row.content = content
    if "metadata" in data:
        meta = data["metadata"]
        row.metadata_json = json.dumps(meta, ensure_ascii=False) if meta is not None else None
    if "active" in data and data["active"] is not None:
        row.active = bool(data["active"])
    if "pinned" in data and data["pinned"] is not None:
        row.pinned = bool(data["pinned"])
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return memory_to_out(row)


async def soft_delete_memory(session: AsyncSession, memory_id: str) -> bool:
    row = await session.get(MemoryRecord, memory_id)
    if not row or row.deleted_at is not None:
        return False
    row.deleted_at = utcnow()
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    return True


async def forget_memory_by_key(
    session: AsyncSession,
    *,
    agent_id: str,
    key: str,
    user_key: Optional[str] = None,
    kind: Optional[str] = None,
) -> dict[str, Any]:
    """Soft-delete. If kind omitted and key matches a playbook, also drop its detail."""
    forgotten: list[str] = []
    kinds = [kind] if kind else ["profile", "playbook", "detail"]
    if kind == "playbook" or kind is None:
        # When deleting a playbook entry (explicit or via bare key), cascade detail.
        pass
    for knd in kinds:
        if not knd:
            continue
        row = await _find_active_memory(
            session,
            kind=knd,
            key=key,
            agent_id=agent_id,
            user_key=user_key,
        )
        if not row:
            continue
        row.deleted_at = utcnow()
        row.updated_at = utcnow()
        session.add(row)
        forgotten.append(knd)
        if knd == "playbook":
            detail = await _find_active_memory(
                session,
                kind="detail",
                key=key,
                agent_id=agent_id,
                user_key=user_key,
            )
            if detail and detail.deleted_at is None:
                detail.deleted_at = utcnow()
                detail.updated_at = utcnow()
                session.add(detail)
                if "detail" not in forgotten:
                    forgotten.append("detail")
    if forgotten:
        await session.commit()
    return {"forgotten": bool(forgotten), "kinds": forgotten}


async def load_memories_by_kind(
    session: AsyncSession,
    *,
    agent_id: str,
    user_key: Optional[str],
    kind: str,
    limit: int,
    active_only: bool = True,
) -> list[MemoryRecord]:
    if not user_key:
        return []
    stmt = (
        select(MemoryRecord)
        .where(
            MemoryRecord.agent_id == agent_id,
            MemoryRecord.scope == "longterm",
            MemoryRecord.kind == kind,
            MemoryRecord.user_key == user_key,
            MemoryRecord.deleted_at.is_(None),
        )
        .order_by(MemoryRecord.updated_at.desc())
        .limit(max(0, limit))
    )
    if active_only:
        stmt = stmt.where(MemoryRecord.active == True)  # noqa: E712
    return list((await session.exec(stmt)).all())


async def touch_memory_access(
    session: AsyncSession,
    rows: list[MemoryRecord],
) -> None:
    if not rows:
        return
    now = utcnow()
    for row in rows:
        row.last_accessed_at = now
        session.add(row)
    await session.commit()


async def search_memories(
    session: AsyncSession,
    *,
    agent_id: str,
    user_key: Optional[str],
    kind: Optional[str] = None,
    key: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 50,
    include_inactive: bool = True,
) -> list[MemoryRecord]:
    if not user_key:
        return []
    stmt = (
        select(MemoryRecord)
        .where(
            MemoryRecord.agent_id == agent_id,
            MemoryRecord.scope == "longterm",
            MemoryRecord.user_key == user_key,
            MemoryRecord.deleted_at.is_(None),
        )
        .order_by(MemoryRecord.updated_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    if kind:
        stmt = stmt.where(MemoryRecord.kind == kind)
    if key:
        stmt = stmt.where(MemoryRecord.key == key)
    if not include_inactive:
        stmt = stmt.where(MemoryRecord.active == True)  # noqa: E712
    rows = list((await session.exec(stmt)).all())
    if query:
        q = query.lower()
        rows = [r for r in rows if q in f"{r.key}\n{r.content}".lower()]
    return rows


async def memory_usage(
    session: AsyncSession,
    *,
    agent_id: str,
    user_key: str,
    policy: Any,
) -> Any:
    from agent_factory_server.domain.schemas import MemoryPolicy, MemoryUsageOut

    policy = policy if isinstance(policy, MemoryPolicy) else MemoryPolicy()
    stmt = select(MemoryRecord).where(
        MemoryRecord.agent_id == agent_id,
        MemoryRecord.user_key == user_key,
        MemoryRecord.scope == "longterm",
        MemoryRecord.deleted_at.is_(None),
    )
    rows = list((await session.exec(stmt)).all())
    usage = MemoryUsageOut(agent_id=agent_id, user_key=user_key, quotas=policy)
    chars = 0
    for row in rows:
        chars += len(row.content or "")
        kind = getattr(row, "kind", None) or "profile"
        active = bool(getattr(row, "active", True))
        if kind == "profile":
            if active:
                usage.profile_active += 1
            else:
                usage.profile_inactive += 1
        elif kind == "playbook":
            if active:
                usage.playbook_active += 1
            else:
                usage.playbook_inactive += 1
        elif kind == "detail":
            if active:
                usage.detail_active += 1
            else:
                usage.detail_inactive += 1
    usage.approx_chars = chars
    return usage


async def load_memories_for_prompt(
    session: AsyncSession,
    *,
    agent_id: str,
    user_key: Optional[str],
    profile_top_k: int,
    playbook_top_k: int,
) -> tuple[list[MemoryRecord], list[MemoryRecord]]:
    """Return active (profile, playbook catalog). Detail never auto-injected."""
    profile = await load_memories_by_kind(
        session,
        agent_id=agent_id,
        user_key=user_key,
        kind="profile",
        limit=profile_top_k,
        active_only=True,
    )
    playbooks = await load_memories_by_kind(
        session,
        agent_id=agent_id,
        user_key=user_key,
        kind="playbook",
        limit=playbook_top_k,
        active_only=True,
    )
    return profile, playbooks


def _uncached_prompt_tokens(prompt_tokens: int, cached_tokens: int) -> int:
    prompt = max(0, int(prompt_tokens or 0))
    cached = max(0, int(cached_tokens or 0))
    return max(0, prompt - min(prompt, cached))


def _usage_to_out(row: LlmUsageRecord) -> LlmUsageOut:
    prompt = int(row.prompt_tokens or 0)
    cached = int(row.cached_tokens or 0)
    return LlmUsageOut(
        id=row.id,
        session_id=row.session_id,
        run_id=row.run_id,
        agent_id=row.agent_id,
        model_profile_id=row.model_profile_id,
        model_name=row.model_name,
        prompt_tokens=prompt,
        completion_tokens=int(row.completion_tokens or 0),
        total_tokens=int(row.total_tokens or 0),
        cached_tokens=cached,
        uncached_prompt_tokens=_uncached_prompt_tokens(prompt, cached),
        source=row.source or "api",
        input_price_per_1m=float(row.input_price_per_1m or 0.0),
        output_price_per_1m=float(row.output_price_per_1m or 0.0),
        estimated_cost=float(row.estimated_cost or 0.0),
        created_at=row.created_at,
    )


def _add_usage_to_bucket(bucket: UsageSummaryBucket, row: LlmUsageRecord) -> None:
    prompt = int(row.prompt_tokens or 0)
    cached = int(row.cached_tokens or 0)
    bucket.calls += 1
    bucket.prompt_tokens += prompt
    bucket.completion_tokens += int(row.completion_tokens or 0)
    bucket.total_tokens += int(row.total_tokens or 0)
    bucket.cached_tokens += cached
    bucket.uncached_prompt_tokens += _uncached_prompt_tokens(prompt, cached)
    bucket.estimated_cost += float(row.estimated_cost or 0.0)


def estimate_usage_cost(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    input_price_per_1m: float,
    output_price_per_1m: float,
) -> float:
    return (
        (max(0, prompt_tokens) / 1_000_000.0) * float(input_price_per_1m or 0.0)
        + (max(0, completion_tokens) / 1_000_000.0) * float(output_price_per_1m or 0.0)
    )


async def record_llm_usage(
    session: AsyncSession,
    *,
    session_id: str,
    run_id: str,
    agent_id: Optional[str],
    model_profile_id: Optional[str],
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: Optional[int] = None,
    cached_tokens: int = 0,
    source: str = "api",
    input_price_per_1m: float = 0.0,
    output_price_per_1m: float = 0.0,
) -> LlmUsageOut:
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    total = int(total_tokens) if total_tokens is not None else prompt + completion
    cached = max(0, int(cached_tokens or 0))
    cost = estimate_usage_cost(
        prompt_tokens=prompt,
        completion_tokens=completion,
        input_price_per_1m=input_price_per_1m,
        output_price_per_1m=output_price_per_1m,
    )
    row = LlmUsageRecord(
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_id,
        model_profile_id=model_profile_id,
        model_name=model_name or "",
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=max(0, total),
        cached_tokens=cached,
        source=source if source in ("api", "estimate") else "api",
        input_price_per_1m=float(input_price_per_1m or 0.0),
        output_price_per_1m=float(output_price_per_1m or 0.0),
        estimated_cost=cost,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _usage_to_out(row)


async def list_llm_usage(
    session: AsyncSession,
    *,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    model_profile_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> LlmUsagePage:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    clauses = []
    if session_id:
        clauses.append(LlmUsageRecord.session_id == session_id)
    if agent_id:
        clauses.append(LlmUsageRecord.agent_id == agent_id)
    if model_profile_id:
        clauses.append(LlmUsageRecord.model_profile_id == model_profile_id)

    count_stmt = select(func.count()).select_from(LlmUsageRecord)
    if clauses:
        count_stmt = count_stmt.where(*clauses)
    total = int((await session.exec(count_stmt)).one() or 0)

    stmt = select(LlmUsageRecord).order_by(LlmUsageRecord.created_at.desc())
    if clauses:
        stmt = stmt.where(*clauses)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.exec(stmt)).all())
    return LlmUsagePage(
        items=[_usage_to_out(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


async def summarize_llm_usage(
    session: AsyncSession,
    *,
    group_by: str = "day",
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None,
    agent_id: Optional[str] = None,
) -> UsageSummaryOut:
    group = (group_by or "day").strip().lower()
    if group not in ("day", "model", "agent"):
        group = "day"

    stmt = select(LlmUsageRecord)
    if from_time is not None:
        stmt = stmt.where(LlmUsageRecord.created_at >= from_time)
    if to_time is not None:
        stmt = stmt.where(LlmUsageRecord.created_at <= to_time)
    if agent_id:
        stmt = stmt.where(LlmUsageRecord.agent_id == agent_id)
    rows = (await session.exec(stmt)).all()

    totals = UsageSummaryBucket(key="total", label="合计")
    buckets: dict[str, UsageSummaryBucket] = {}

    agent_names: dict[str, str] = {}
    profile_names: dict[str, str] = {}
    if group == "agent":
        agents = (await session.exec(select(AgentRecord))).all()
        agent_names = {a.id: a.name for a in agents}
    if group == "model":
        profiles = (await session.exec(select(ModelProfileRecord))).all()
        profile_names = {p.id: p.name for p in profiles}

    for row in rows:
        _add_usage_to_bucket(totals, row)

        if group == "day":
            created = row.created_at
            key = created.strftime("%Y-%m-%d") if created else "unknown"
            label = key
        elif group == "agent":
            key = row.agent_id or "unknown"
            label = agent_names.get(key) or key
        else:
            key = row.model_profile_id or row.model_name or "unknown"
            label = profile_names.get(key) or row.model_name or key

        bucket = buckets.get(key)
        if bucket is None:
            bucket = UsageSummaryBucket(key=key, label=label)
            buckets[key] = bucket
        _add_usage_to_bucket(bucket, row)

    ordered = sorted(
        buckets.values(),
        key=lambda b: b.key,
        reverse=(group == "day"),
    )
    return UsageSummaryOut(
        group_by=group,
        from_time=from_time,
        to_time=to_time,
        totals=totals,
        buckets=ordered,
    )


def _package_to_out(
    pkg: SkillPackageRecord,
    files: list[SkillPackageFileRecord],
    *,
    warnings: list[str] | None = None,
) -> SkillPackageOut:
    return SkillPackageOut(
        id=pkg.id,
        agent_id=pkg.agent_id,
        name=pkg.name,
        description=pkg.description or "",
        version=pkg.version,
        source=pkg.source or "upload",
        status=pkg.status or "active",
        total_bytes=int(pkg.total_bytes or 0),
        file_count=int(pkg.file_count or 0),
        files=[
            SkillPackageFileOut(
                path=f.path,
                kind=f.kind,
                is_binary=bool(f.is_binary),
                size_bytes=int(f.size_bytes or 0),
                sha256=f.sha256 or "",
                mode=f.mode,
            )
            for f in files
        ],
        warnings=list(warnings or []),
        created_at=pkg.created_at,
        updated_at=pkg.updated_at,
    )


async def list_skill_packages(
    session: AsyncSession, agent_id: str
) -> list[SkillPackageOut]:
    result = await session.exec(
        select(SkillPackageRecord)
        .where(SkillPackageRecord.agent_id == agent_id)
        .order_by(SkillPackageRecord.name)
    )
    packages = list(result.all())
    out: list[SkillPackageOut] = []
    for pkg in packages:
        files = await list_skill_package_files(session, pkg.id)
        out.append(_package_to_out(pkg, files))
    return out


async def get_skill_package(
    session: AsyncSession, agent_id: str, name: str
) -> Optional[SkillPackageOut]:
    pkg = await get_skill_package_record(session, agent_id, name)
    if not pkg:
        return None
    files = await list_skill_package_files(session, pkg.id)
    return _package_to_out(pkg, files)


async def get_skill_package_record(
    session: AsyncSession, agent_id: str, name: str
) -> Optional[SkillPackageRecord]:
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    result = await session.exec(
        select(SkillPackageRecord).where(
            SkillPackageRecord.agent_id == agent_id,
            SkillPackageRecord.name == cleaned,
        )
    )
    return result.first()


async def list_skill_package_files(
    session: AsyncSession, package_id: str
) -> list[SkillPackageFileRecord]:
    result = await session.exec(
        select(SkillPackageFileRecord)
        .where(SkillPackageFileRecord.package_id == package_id)
        .order_by(SkillPackageFileRecord.path)
    )
    return list(result.all())


async def get_skill_package_file(
    session: AsyncSession, package_id: str, path: str
) -> Optional[SkillPackageFileRecord]:
    rel = (path or "").strip().replace("\\", "/")
    result = await session.exec(
        select(SkillPackageFileRecord).where(
            SkillPackageFileRecord.package_id == package_id,
            SkillPackageFileRecord.path == rel,
        )
    )
    return result.first()


async def _upsert_catalog_skill(
    session: AsyncSession,
    *,
    agent_id: str,
    name: str,
    description: str,
    origin: str = "package",
) -> None:
    row = await session.get(AgentRecord, agent_id)
    if not row:
        raise ValueError("agent not found")
    if origin == "platform":
        origin_lit: str = "platform"
    elif origin == "client":
        origin_lit = "client"
    else:
        origin_lit = "package"
    skills = [SkillDef.model_validate(item) for item in _loads_list(row.skills_json)]
    next_skills: list[SkillDef] = []
    found = False
    for skill in skills:
        if skill.name == name:
            next_skills.append(
                SkillDef(name=name, description=description, origin=origin_lit)  # type: ignore[arg-type]
            )
            found = True
        else:
            next_skills.append(skill)
    if not found:
        next_skills.append(
            SkillDef(name=name, description=description, origin=origin_lit)  # type: ignore[arg-type]
        )
    row.skills_json = json.dumps([s.model_dump() for s in next_skills], ensure_ascii=False)
    row.updated_at = utcnow()
    session.add(row)


async def _remove_catalog_skill(
    session: AsyncSession,
    *,
    agent_id: str,
    name: str,
    origin: str = "package",
) -> None:
    row = await session.get(AgentRecord, agent_id)
    if not row:
        return
    skills = [SkillDef.model_validate(item) for item in _loads_list(row.skills_json)]
    kept = [
        s
        for s in skills
        if not (s.name == name and (s.origin or "package") == origin)
    ]
    row.skills_json = json.dumps([s.model_dump() for s in kept], ensure_ascii=False)
    row.updated_at = utcnow()
    session.add(row)


async def update_skill_package_meta(
    session: AsyncSession,
    *,
    agent_id: str,
    name: str,
    description: str,
) -> Optional[SkillPackageOut]:
    """Update catalog-facing description; name stays immutable."""
    pkg = await get_skill_package_record(session, agent_id, name)
    if not pkg:
        return None
    pkg.description = description
    pkg.updated_at = utcnow()
    session.add(pkg)
    await _upsert_catalog_skill(
        session,
        agent_id=agent_id,
        name=pkg.name,
        description=description,
        origin="package",
    )
    await session.commit()
    await session.refresh(pkg)
    files = await list_skill_package_files(session, pkg.id)
    return _package_to_out(pkg, files)


async def install_skill_package(
    session: AsyncSession,
    *,
    agent_id: str,
    unpacked: dict[str, Any],
    force: bool = False,
    source: str = "upload",
    expected_name: str | None = None,
    keep_description: bool = False,
) -> SkillPackageOut:
    from agent_factory_server.runtime.skill_package import PackageError

    name = str(unpacked["name"])
    expect = (expected_name or "").strip()
    if expect and expect != name:
        raise PackageError(
            "name_mismatch",
            f"zip skill name {name!r} does not match target {expect!r}",
        )

    existing = await get_skill_package_record(session, agent_id, name)
    if existing and not force:
        raise PackageError(
            "skill_exists",
            f"skill already installed: {name} (pass force=true to replace)",
        )

    # Reject shadowing a client-origin catalog entry unless force replaces package only
    # and we convert origin — still refuse if client skill same name without force.
    agent = await session.get(AgentRecord, agent_id)
    if not agent:
        raise PackageError("agent_not_found", "agent not found")
    catalog = [SkillDef.model_validate(item) for item in _loads_list(agent.skills_json)]
    for skill in catalog:
        if skill.name == name and (skill.origin or "package") == "client" and not existing:
            raise PackageError(
                "client_skill_conflict",
                f"catalog already has client skill {name!r}; remove it first or use a different name",
            )

    platform = await get_platform_skill_package_record(session, name)
    if platform and not existing:
        mounted = await session.exec(
            select(AgentSkillMountRecord).where(
                AgentSkillMountRecord.agent_id == agent_id,
                AgentSkillMountRecord.package_id == platform.id,
            )
        )
        if mounted.first():
            raise PackageError(
                "platform_mount_conflict",
                f"agent already mounts platform skill {name!r}; unmount it first",
            )

    zip_description = str(unpacked.get("description") or "")
    if existing and keep_description:
        description = existing.description or zip_description
    else:
        description = zip_description

    if existing:
        old_files = await list_skill_package_files(session, existing.id)
        for f in old_files:
            await session.delete(f)
        pkg = existing
        pkg.description = description
        pkg.version = unpacked.get("version")
        pkg.source = source
        pkg.status = "active"
        pkg.total_bytes = int(unpacked.get("total_bytes") or 0)
        pkg.file_count = len(unpacked.get("files") or [])
        pkg.updated_at = utcnow()
    else:
        pkg = SkillPackageRecord(
            agent_id=agent_id,
            name=name,
            description=description,
            version=unpacked.get("version"),
            source=source,
            status="active",
            total_bytes=int(unpacked.get("total_bytes") or 0),
            file_count=len(unpacked.get("files") or []),
        )
    session.add(pkg)
    await session.flush()

    stored: list[SkillPackageFileRecord] = []
    for entry in unpacked.get("files") or []:
        file_row = SkillPackageFileRecord(
            package_id=pkg.id,
            path=str(entry["path"]),
            kind=str(entry.get("kind") or "doc"),
            is_binary=bool(entry.get("is_binary")),
            text_content=entry.get("text_content"),
            blob=entry.get("blob"),
            size_bytes=int(entry.get("bytes") or 0),
            sha256=str(entry.get("sha256") or ""),
            mode=entry.get("mode"),
        )
        session.add(file_row)
        stored.append(file_row)

    await _upsert_catalog_skill(
        session,
        agent_id=agent_id,
        name=name,
        description=description,
        origin="package",
    )
    await session.commit()
    await session.refresh(pkg)
    files = await list_skill_package_files(session, pkg.id)
    return _package_to_out(pkg, files, warnings=list(unpacked.get("warnings") or []))


async def uninstall_skill_package(
    session: AsyncSession, *, agent_id: str, name: str
) -> bool:
    pkg = await get_skill_package_record(session, agent_id, name)
    if not pkg:
        return False
    files = await list_skill_package_files(session, pkg.id)
    for f in files:
        await session.delete(f)
    await session.delete(pkg)
    await _remove_catalog_skill(session, agent_id=agent_id, name=name, origin="package")
    await session.commit()
    return True


def _platform_package_to_out(
    pkg: PlatformSkillPackageRecord,
    files: list[SkillPackageFileRecord],
    *,
    warnings: list[str] | None = None,
    mount_count: int = 0,
) -> PlatformSkillPackageOut:
    return PlatformSkillPackageOut(
        id=pkg.id,
        name=pkg.name,
        description=pkg.description or "",
        version=pkg.version,
        source=pkg.source or "upload",
        status=pkg.status or "active",
        total_bytes=int(pkg.total_bytes or 0),
        file_count=int(pkg.file_count or 0),
        files=[
            SkillPackageFileOut(
                path=f.path,
                kind=f.kind,
                is_binary=bool(f.is_binary),
                size_bytes=int(f.size_bytes or 0),
                sha256=f.sha256 or "",
                mode=f.mode,
            )
            for f in files
        ],
        warnings=list(warnings or []),
        mount_count=mount_count,
        created_at=pkg.created_at,
        updated_at=pkg.updated_at,
    )


async def _count_platform_mounts(session: AsyncSession, package_id: str) -> int:
    result = await session.exec(
        select(func.count())
        .select_from(AgentSkillMountRecord)
        .where(AgentSkillMountRecord.package_id == package_id)
    )
    return int(result.one() or 0)


async def get_platform_skill_package_record(
    session: AsyncSession, name: str
) -> Optional[PlatformSkillPackageRecord]:
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    result = await session.exec(
        select(PlatformSkillPackageRecord).where(PlatformSkillPackageRecord.name == cleaned)
    )
    return result.first()


async def get_platform_skill_package_by_id(
    session: AsyncSession, package_id: str
) -> Optional[PlatformSkillPackageRecord]:
    return await session.get(PlatformSkillPackageRecord, package_id)


async def list_platform_skill_packages(
    session: AsyncSession,
) -> list[PlatformSkillPackageOut]:
    result = await session.exec(
        select(PlatformSkillPackageRecord).order_by(PlatformSkillPackageRecord.name)
    )
    packages = list(result.all())
    out: list[PlatformSkillPackageOut] = []
    for pkg in packages:
        files = await list_skill_package_files(session, pkg.id)
        mounts = await _count_platform_mounts(session, pkg.id)
        out.append(_platform_package_to_out(pkg, files, mount_count=mounts))
    return out


async def get_platform_skill_package(
    session: AsyncSession, name: str
) -> Optional[PlatformSkillPackageOut]:
    pkg = await get_platform_skill_package_record(session, name)
    if not pkg:
        return None
    files = await list_skill_package_files(session, pkg.id)
    mounts = await _count_platform_mounts(session, pkg.id)
    return _platform_package_to_out(pkg, files, mount_count=mounts)


async def install_platform_skill_package(
    session: AsyncSession,
    *,
    unpacked: dict[str, Any],
    force: bool = False,
    source: str = "upload",
    keep_description: bool = False,
) -> PlatformSkillPackageOut:
    from agent_factory_server.runtime.skill_package import PackageError

    name = str(unpacked["name"])
    existing = await get_platform_skill_package_record(session, name)
    if existing and not force:
        raise PackageError(
            "skill_exists",
            f"platform skill already installed: {name} (pass force=true to replace)",
        )

    zip_description = str(unpacked.get("description") or "")
    if existing and keep_description:
        description = existing.description or zip_description
    else:
        description = zip_description

    if existing:
        old_files = await list_skill_package_files(session, existing.id)
        for f in old_files:
            await session.delete(f)
        pkg = existing
        pkg.description = description
        pkg.version = unpacked.get("version")
        pkg.source = source
        pkg.status = "active"
        pkg.total_bytes = int(unpacked.get("total_bytes") or 0)
        pkg.file_count = len(unpacked.get("files") or [])
        pkg.updated_at = utcnow()
    else:
        pkg = PlatformSkillPackageRecord(
            name=name,
            description=description,
            version=unpacked.get("version"),
            source=source,
            status="active",
            total_bytes=int(unpacked.get("total_bytes") or 0),
            file_count=len(unpacked.get("files") or []),
        )
    session.add(pkg)
    await session.flush()

    for entry in unpacked.get("files") or []:
        session.add(
            SkillPackageFileRecord(
                package_id=pkg.id,
                path=str(entry["path"]),
                kind=str(entry.get("kind") or "doc"),
                is_binary=bool(entry.get("is_binary")),
                text_content=entry.get("text_content"),
                blob=entry.get("blob"),
                size_bytes=int(entry.get("bytes") or 0),
                sha256=str(entry.get("sha256") or ""),
                mode=entry.get("mode"),
            )
        )

    # Refresh catalog description on all agents that mount this package.
    mounts = await session.exec(
        select(AgentSkillMountRecord).where(AgentSkillMountRecord.package_id == pkg.id)
    )
    for mount in list(mounts.all()):
        await _upsert_catalog_skill(
            session,
            agent_id=mount.agent_id,
            name=name,
            description=description,
            origin="platform",
        )

    await session.commit()
    await session.refresh(pkg)
    files = await list_skill_package_files(session, pkg.id)
    mount_count = await _count_platform_mounts(session, pkg.id)
    return _platform_package_to_out(
        pkg, files, warnings=list(unpacked.get("warnings") or []), mount_count=mount_count
    )


async def update_platform_skill_package_meta(
    session: AsyncSession,
    *,
    name: str,
    description: str,
) -> Optional[PlatformSkillPackageOut]:
    pkg = await get_platform_skill_package_record(session, name)
    if not pkg:
        return None
    pkg.description = description
    pkg.updated_at = utcnow()
    session.add(pkg)
    mounts = await session.exec(
        select(AgentSkillMountRecord).where(AgentSkillMountRecord.package_id == pkg.id)
    )
    for mount in list(mounts.all()):
        await _upsert_catalog_skill(
            session,
            agent_id=mount.agent_id,
            name=pkg.name,
            description=description,
            origin="platform",
        )
    await session.commit()
    await session.refresh(pkg)
    files = await list_skill_package_files(session, pkg.id)
    mount_count = await _count_platform_mounts(session, pkg.id)
    return _platform_package_to_out(pkg, files, mount_count=mount_count)


async def uninstall_platform_skill_package(session: AsyncSession, *, name: str) -> bool:
    pkg = await get_platform_skill_package_record(session, name)
    if not pkg:
        return False
    mounts = await session.exec(
        select(AgentSkillMountRecord).where(AgentSkillMountRecord.package_id == pkg.id)
    )
    for mount in list(mounts.all()):
        await _remove_catalog_skill(
            session, agent_id=mount.agent_id, name=pkg.name, origin="platform"
        )
        await session.delete(mount)
    files = await list_skill_package_files(session, pkg.id)
    for f in files:
        await session.delete(f)
    await session.delete(pkg)
    await session.commit()
    return True


async def list_agent_skill_mounts(
    session: AsyncSession, agent_id: str
) -> list[AgentSkillMountOut]:
    result = await session.exec(
        select(AgentSkillMountRecord).where(AgentSkillMountRecord.agent_id == agent_id)
    )
    mounts = list(result.all())
    out: list[AgentSkillMountOut] = []
    for mount in mounts:
        pkg = await get_platform_skill_package_by_id(session, mount.package_id)
        if not pkg:
            continue
        files = await list_skill_package_files(session, pkg.id)
        out.append(
            AgentSkillMountOut(
                agent_id=agent_id,
                package_id=pkg.id,
                name=pkg.name,
                description=pkg.description or "",
                version=pkg.version,
                source=pkg.source or "upload",
                status=pkg.status or "active",
                total_bytes=int(pkg.total_bytes or 0),
                file_count=int(pkg.file_count or 0),
                files=[
                    SkillPackageFileOut(
                        path=f.path,
                        kind=f.kind,
                        is_binary=bool(f.is_binary),
                        size_bytes=int(f.size_bytes or 0),
                        sha256=f.sha256 or "",
                        mode=f.mode,
                    )
                    for f in files
                ],
                created_at=pkg.created_at,
                mounted_at=mount.created_at,
            )
        )
    out.sort(key=lambda item: item.name)
    return out


async def mount_platform_skill(
    session: AsyncSession, *, agent_id: str, name: str
) -> AgentSkillMountOut:
    from agent_factory_server.runtime.skill_package import PackageError

    agent = await session.get(AgentRecord, agent_id)
    if not agent:
        raise PackageError("agent_not_found", "agent not found")
    pkg = await get_platform_skill_package_record(session, name)
    if not pkg or (pkg.status or "active") != "active":
        raise PackageError("platform_skill_not_found", f"platform skill not found: {name}")

    owned = await get_skill_package_record(session, agent_id, pkg.name)
    if owned:
        raise PackageError(
            "name_conflict",
            f"agent already has owned skill {pkg.name!r}; rename/remove it before mounting",
        )

    existing = await session.exec(
        select(AgentSkillMountRecord).where(
            AgentSkillMountRecord.agent_id == agent_id,
            AgentSkillMountRecord.package_id == pkg.id,
        )
    )
    mount = existing.first()
    if not mount:
        mount = AgentSkillMountRecord(agent_id=agent_id, package_id=pkg.id)
        session.add(mount)

    await _upsert_catalog_skill(
        session,
        agent_id=agent_id,
        name=pkg.name,
        description=pkg.description or "",
        origin="platform",
    )
    await session.commit()
    await session.refresh(mount)
    files = await list_skill_package_files(session, pkg.id)
    return AgentSkillMountOut(
        agent_id=agent_id,
        package_id=pkg.id,
        name=pkg.name,
        description=pkg.description or "",
        version=pkg.version,
        source=pkg.source or "upload",
        status=pkg.status or "active",
        total_bytes=int(pkg.total_bytes or 0),
        file_count=int(pkg.file_count or 0),
        files=[
            SkillPackageFileOut(
                path=f.path,
                kind=f.kind,
                is_binary=bool(f.is_binary),
                size_bytes=int(f.size_bytes or 0),
                sha256=f.sha256 or "",
                mode=f.mode,
            )
            for f in files
        ],
        created_at=pkg.created_at,
        mounted_at=mount.created_at,
    )


async def unmount_platform_skill(
    session: AsyncSession, *, agent_id: str, name: str
) -> bool:
    pkg = await get_platform_skill_package_record(session, name)
    if not pkg:
        return False
    result = await session.exec(
        select(AgentSkillMountRecord).where(
            AgentSkillMountRecord.agent_id == agent_id,
            AgentSkillMountRecord.package_id == pkg.id,
        )
    )
    mount = result.first()
    if not mount:
        return False
    await session.delete(mount)
    await _remove_catalog_skill(
        session, agent_id=agent_id, name=pkg.name, origin="platform"
    )
    await session.commit()
    return True


async def resolve_skill_package_for_agent(
    session: AsyncSession, *, agent_id: str, name: str
) -> tuple[Optional[str], Optional[str]]:
    """Return (package_id, origin) for load/read. Agent-owned wins over platform mount."""
    owned = await get_skill_package_record(session, agent_id, name)
    if owned and (owned.status or "active") == "active":
        return owned.id, "package"
    platform = await get_platform_skill_package_record(session, name)
    if not platform or (platform.status or "active") != "active":
        return None, None
    mount = await session.exec(
        select(AgentSkillMountRecord).where(
            AgentSkillMountRecord.agent_id == agent_id,
            AgentSkillMountRecord.package_id == platform.id,
        )
    )
    if mount.first():
        return platform.id, "platform"
    return None, None
