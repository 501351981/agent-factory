"""Scheduled jobs: tools + worker (Phase H)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.db.models import AgentScheduleRecord
from agent_factory_server.db.session import SessionLocal
from agent_factory_server.domain.schemas import (
    AgentOut,
    AgentRuntimeConfig,
    SchedulerPolicy,
    ToolDef,
)

logger = logging.getLogger(__name__)

SCHEDULE_CREATE = "schedule_create"
SCHEDULE_LIST = "schedule_list"
SCHEDULE_CANCEL = "schedule_cancel"
SCHEDULE_PAUSE = "schedule_pause"
SCHEDULE_RESUME = "schedule_resume"
SCHEDULER_TOOL_NAMES = frozenset(
    {
        SCHEDULE_CREATE,
        SCHEDULE_LIST,
        SCHEDULE_CANCEL,
        SCHEDULE_PAUSE,
        SCHEDULE_RESUME,
    }
)

PAYLOAD_MAX_CHARS = 8000
EVERY_MS_MIN = 60_000
EVERY_MS_MAX = 30 * 24 * 60 * 60 * 1000  # 30 days
MAX_ATTEMPTS_BEFORE_FAIL = 5
WORKER_TICK_SECONDS = 5.0
BACKOFF_BASE_SECONDS = 30

_worker_task: Optional[asyncio.Task[None]] = None


def resolve_scheduler_policy(raw: Any) -> SchedulerPolicy:
    if raw is None:
        return SchedulerPolicy()
    if isinstance(raw, SchedulerPolicy):
        return raw
    if isinstance(raw, dict):
        try:
            return SchedulerPolicy.model_validate(raw)
        except Exception:
            return SchedulerPolicy()
    return SchedulerPolicy()


def schedule_tools(*, locale: str = "zh") -> list[ToolDef]:
    zh = locale.startswith("zh")
    return [
        ToolDef(
            name=SCHEDULE_CREATE,
            description=(
                "创建定时任务。"
                "选 kind 的规则（重要）："
                "「N 分钟/小时后」「到某时刻提醒一次」→ 必须用 at + run_at（单次）；"
                "「每隔 N 分钟/小时」→ every；"
                "「每天/每周/工作日 …」→ cron。"
                "禁止：把「X 分钟后」建成 every，或建成 cron '* * * * *'（那是每分钟）。"
                "同一请求只建一个任务；建错了先 schedule_cancel 再重建。"
                "run_mode：assistant_message=直接插入助手文案；"
                "synthetic_user=隐藏触发后走 Agent 循环（可调工具）；"
                "isolated_session=新建定时专用会话再跑 Agent。"
                if zh
                else "Create a scheduled job. "
                "Kind rules (important): "
                "'in N minutes/hours' / one-shot remind → must use at + run_at; "
                "'every N minutes' → every; "
                "'daily/weekly/weekdays…' → cron. "
                "Never use every or cron '* * * * *' for one-shot 'in X minutes'. "
                "One user request → one job; cancel then recreate if wrong. "
                "run_mode: assistant_message | synthetic_user | isolated_session."
            ),
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "schedule_kind": {
                        "type": "string",
                        "enum": ["at", "every", "cron"],
                        "description": (
                            "at=单次（N分钟后/某时刻一次）；"
                            "every=固定间隔循环；"
                            "cron=按日历循环（每天/每周等）"
                            if zh
                            else "at=one-shot; every=interval loop; cron=calendar loop"
                        ),
                    },
                    "run_at": {
                        "type": "string",
                        "description": (
                            "ISO8601 绝对时间。at 必填（把「N分钟后」换成具体时间点）；"
                            "every 可选作首次开火时间"
                            if zh
                            else "ISO8601 absolute time; required for at "
                            "(convert 'in N minutes' to a concrete timestamp)"
                        ),
                    },
                    "every_ms": {
                        "type": "integer",
                        "description": (
                            "仅 every：间隔毫秒，≥60000。不要用于单次提醒"
                            if zh
                            else "every only: interval ms (>=60000). Not for one-shot."
                        ),
                    },
                    "cron_expr": {
                        "type": "string",
                        "description": "5-field cron: min hour dom month dow",
                    },
                    "timezone": {"type": "string"},
                    "session_target": {
                        "type": "string",
                        "enum": ["origin", "latest", "session_id"],
                    },
                    "session_id": {"type": "string"},
                    "run_mode": {
                        "type": "string",
                        "enum": [
                            "assistant_message",
                            "synthetic_user",
                            "isolated_session",
                        ],
                    },
                    "payload_text": {"type": "string"},
                    "delete_after_run": {
                        "type": "boolean",
                        "description": (
                            "at 默认 true（单次跑完结束）；周期任务默认 false"
                            if zh
                            else "at defaults true; recurring defaults false"
                        ),
                    },
                },
                "required": ["schedule_kind", "run_mode", "payload_text"],
            },
        ),
        ToolDef(
            name=SCHEDULE_LIST,
            description=(
                "列出当前用户在本 Agent 下的定时任务"
                if zh
                else "List scheduled jobs for this user and agent"
            ),
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "active",
                            "paused",
                            "completed",
                            "cancelled",
                            "failed",
                            "all",
                        ],
                    },
                },
            },
        ),
        ToolDef(
            name=SCHEDULE_CANCEL,
            description=(
                "删除定时任务（永久移除，建错了请用这个清掉再重建）"
                if zh
                else "Permanently delete a scheduled job (use when correcting a mistake)"
            ),
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
        ToolDef(
            name=SCHEDULE_PAUSE,
            description="暂停定时任务" if zh else "Pause a scheduled job",
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
        ToolDef(
            name=SCHEDULE_RESUME,
            description="恢复已暂停的定时任务" if zh else "Resume a paused job",
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
    ]


def merge_agent_tools_with_scheduler(
    agent_tools: list[ToolDef],
    *,
    enabled: bool,
    locale: str = "zh",
) -> list[ToolDef]:
    if not enabled:
        return list(agent_tools)
    merged = list(agent_tools)
    existing = {t.name for t in merged}
    for tool in schedule_tools(locale=locale):
        if tool.name not in existing:
            merged.append(tool)
    return merged


def _parse_run_at(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo((name or "Asia/Shanghai").strip() or "Asia/Shanghai")
    except Exception as exc:
        raise ValueError(f"invalid timezone: {name}") from exc


def validate_cron_expr(expr: str, *, tz_name: str = "Asia/Shanghai") -> None:
    text = (expr or "").strip()
    parts = text.split()
    if len(parts) != 5:
        raise ValueError("cron_expr must have 5 fields (min hour dom month dow)")
    tz = _resolve_tz(tz_name)
    base = datetime.now(tz)
    if not croniter.is_valid(text):
        raise ValueError(f"invalid cron_expr: {text}")
    # Force a next() to catch runtime errors early.
    croniter(text, base).get_next(datetime)


def compute_next_run_at(
    job: AgentScheduleRecord,
    *,
    after: Optional[datetime] = None,
) -> Optional[datetime]:
    """Next fire time in UTC after ``after`` (default now)."""
    kind = (job.schedule_kind or "at").strip().lower()
    after = after or datetime.now(timezone.utc)
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)

    if kind == "at":
        return None

    if kind == "every":
        every = int(job.every_ms or 0)
        if every < EVERY_MS_MIN:
            raise ValueError(f"every_ms must be >= {EVERY_MS_MIN}")
        # Avoid catch-up storms: base on max(now, last planned).
        base = after
        if job.next_run_at and job.next_run_at.tzinfo is None:
            planned = job.next_run_at.replace(tzinfo=timezone.utc)
        else:
            planned = job.next_run_at
        if planned and planned > base:
            base = planned
        return base + timedelta(milliseconds=every)

    if kind == "cron":
        expr = (job.cron_expr or "").strip()
        if not expr:
            raise ValueError("cron_expr missing")
        tz = _resolve_tz(job.timezone or "Asia/Shanghai")
        local_after = after.astimezone(tz)
        itr = croniter(expr, local_after)
        nxt = itr.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=tz)
        return nxt.astimezone(timezone.utc)

    raise ValueError(f"unknown schedule_kind: {kind}")


def schedule_trigger_text(job_id: str, payload: str) -> str:
    body = (payload or "").strip()
    return f"[schedule:{job_id}] {body}".strip()


def is_schedule_trigger_text(text: Optional[str]) -> bool:
    return bool(text and str(text).startswith("[schedule:"))


def _job_summary(row: AgentScheduleRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "schedule_kind": row.schedule_kind,
        "run_mode": row.run_mode,
        "session_target": row.session_target,
        "status": row.status,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "payload_preview": (row.payload_text or "")[:120],
    }


async def execute_scheduler_tool(
    session: AsyncSession,
    *,
    name: str,
    arguments: dict[str, Any],
    agent: AgentOut,
    runtime: AgentRuntimeConfig,
    session_id: str,
    run_id: str,
    session_user_key: str,
    locale: str = "zh",
) -> dict[str, Any]:
    policy = resolve_scheduler_policy(getattr(runtime, "scheduler", None))
    if not policy.enabled:
        return {"ok": False, "error": "scheduler disabled for this agent"}

    user_key = session_user_key or ""

    if name == SCHEDULE_CREATE:
        return await _tool_create(
            session,
            arguments=arguments,
            agent=agent,
            policy=policy,
            session_id=session_id,
            run_id=run_id,
            user_key=user_key,
        )
    if name == SCHEDULE_LIST:
        status = str(arguments.get("status") or "active").strip().lower()
        status_filter = None if status == "all" else (status or "active")
        rows = await repo.list_schedules(
            session,
            agent_id=agent.id,
            user_key=user_key,
            status=status_filter,
        )
        return {"ok": True, "jobs": [_job_summary(r) for r in rows]}
    if name == SCHEDULE_CANCEL:
        job_id = str(arguments.get("job_id") or "").strip()
        if not job_id:
            return {"ok": False, "error": "job_id is required"}
        # Agent self-correction should not leave soft-cancelled ghosts in admin.
        ok = await repo.delete_schedule(
            session, job_id, agent_id=agent.id, user_key=user_key
        )
        if not ok:
            return {"ok": False, "error": "job not found"}
        return {"ok": True, "job_id": job_id, "deleted": True}
    if name == SCHEDULE_PAUSE:
        job_id = str(arguments.get("job_id") or "").strip()
        if not job_id:
            return {"ok": False, "error": "job_id is required"}
        row = await repo.pause_schedule(
            session, job_id, agent_id=agent.id, user_key=user_key
        )
        if not row:
            return {"ok": False, "error": "job not found"}
        return {"ok": True, "job_id": row.id, "status": row.status}
    if name == SCHEDULE_RESUME:
        job_id = str(arguments.get("job_id") or "").strip()
        if not job_id:
            return {"ok": False, "error": "job_id is required"}
        row = await repo.resume_schedule(
            session, job_id, agent_id=agent.id, user_key=user_key
        )
        if not row:
            return {"ok": False, "error": "job not found"}
        return {
            "ok": True,
            "job_id": row.id,
            "status": row.status,
            "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        }
    return {"ok": False, "error": f"unknown scheduler tool: {name}"}


async def _tool_create(
    session: AsyncSession,
    *,
    arguments: dict[str, Any],
    agent: AgentOut,
    policy: SchedulerPolicy,
    session_id: str,
    run_id: str,
    user_key: str,
) -> dict[str, Any]:
    kind = str(arguments.get("schedule_kind") or "at").strip().lower()
    run_mode = str(arguments.get("run_mode") or "assistant_message").strip().lower()
    payload = str(arguments.get("payload_text") or "").strip()
    if not payload:
        return {"ok": False, "error": "payload_text is required"}
    if len(payload) > PAYLOAD_MAX_CHARS:
        return {"ok": False, "error": f"payload_text exceeds {PAYLOAD_MAX_CHARS} chars"}
    if kind not in ("at", "every", "cron"):
        return {"ok": False, "error": "schedule_kind must be at|every|cron"}
    if run_mode not in ("assistant_message", "synthetic_user", "isolated_session"):
        return {"ok": False, "error": "invalid run_mode"}

    session_target = str(arguments.get("session_target") or "origin").strip().lower()
    explicit_session_id = str(arguments.get("session_id") or "").strip() or None
    if run_mode == "isolated_session":
        session_target = "origin"  # ignored at fire time
    else:
        if session_target not in ("origin", "latest", "session_id"):
            return {"ok": False, "error": "invalid session_target"}
        if session_target == "session_id":
            if not explicit_session_id:
                return {"ok": False, "error": "session_id required when session_target=session_id"}
            target = await repo.get_session_row(session, explicit_session_id)
            if not target:
                return {"ok": False, "error": "session not found"}
            if target.agent_id != agent.id or (target.user_key or "") != user_key:
                return {"ok": False, "error": "session not owned by this user/agent"}

    tz_name = str(arguments.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
    now = datetime.now(timezone.utc)
    run_at = _parse_run_at(arguments.get("run_at"))
    every_ms: Optional[int] = None
    cron_expr: Optional[str] = None
    next_run: Optional[datetime] = None

    try:
        if kind == "at":
            if not run_at:
                return {"ok": False, "error": "run_at must be a valid ISO8601 datetime"}
            if run_at < now - timedelta(seconds=30):
                return {"ok": False, "error": "run_at is in the past"}
            next_run = run_at
        elif kind == "every":
            raw_every = arguments.get("every_ms")
            try:
                every_ms = int(raw_every)
            except (TypeError, ValueError):
                return {"ok": False, "error": "every_ms must be an integer"}
            if every_ms < EVERY_MS_MIN or every_ms > EVERY_MS_MAX:
                return {
                    "ok": False,
                    "error": f"every_ms must be between {EVERY_MS_MIN} and {EVERY_MS_MAX}",
                }
            next_run = run_at if run_at and run_at >= now - timedelta(seconds=30) else (
                now + timedelta(milliseconds=every_ms)
            )
        else:  # cron
            cron_expr = str(arguments.get("cron_expr") or "").strip()
            if not cron_expr:
                return {"ok": False, "error": "cron_expr is required"}
            validate_cron_expr(cron_expr, tz_name=tz_name)
            probe = AgentScheduleRecord(
                schedule_kind="cron",
                cron_expr=cron_expr,
                timezone=tz_name,
            )
            next_run = compute_next_run_at(probe, after=now)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    active = await repo.count_active_schedules(
        session, agent_id=agent.id, user_key=user_key
    )
    if active >= int(policy.max_active_jobs or 50):
        return {
            "ok": False,
            "error": f"active job quota exceeded ({policy.max_active_jobs})",
        }

    delete_after = arguments.get("delete_after_run")
    if delete_after is None:
        delete_after = kind == "at"
    # One-shot reminders must use at+run_at. Models often misuse
    # cron "* * * * *" / every with delete_after_run for "in 1 minute".
    if kind != "at" and bool(delete_after):
        return {
            "ok": False,
            "error": (
                "单次提醒请用 schedule_kind=at 且提供 run_at（把「N分钟后」换成绝对时间）；"
                "every/cron 仅用于重复任务，不要设置 delete_after_run=true"
            ),
        }
    if kind == "cron" and cron_expr:
        parts = cron_expr.split()
        if len(parts) == 5 and parts[0] == "*" and parts[1] == "*":
            return {
                "ok": False,
                "error": (
                    "cron '* * …' 表示每分钟执行，不是「1分钟后一次」。"
                    "单次请改用 schedule_kind=at + run_at"
                ),
            }

    row = await repo.create_schedule(
        session,
        agent_id=agent.id,
        user_key=user_key,
        created_from_session_id=session_id,
        created_by_run_id=run_id,
        name=str(arguments.get("name") or ""),
        schedule_kind=kind,
        run_at=run_at,
        every_ms=every_ms,
        cron_expr=cron_expr,
        timezone=tz_name,
        session_target=session_target,
        session_id=explicit_session_id,
        run_mode=run_mode,
        payload_text=payload,
        delete_after_run=bool(delete_after),
        next_run_at=next_run,
    )
    return {
        "ok": True,
        "job_id": row.id,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "run_mode": row.run_mode,
        "schedule_kind": row.schedule_kind,
        "session_target": row.session_target,
    }


async def resolve_fire_session_id(
    session: AsyncSession, job: AgentScheduleRecord
) -> str:
    mode = (job.run_mode or "").strip().lower()
    if mode == "isolated_session":
        # Caller creates the session; this path unused for isolated.
        raise RuntimeError("isolated_session resolves its own session")

    target = (job.session_target or "origin").strip().lower()
    if target == "session_id":
        sid = (job.session_id or "").strip()
        if not sid:
            raise RuntimeError("session_id missing on job")
        row = await repo.get_session_row(session, sid)
        if not row:
            raise RuntimeError(f"session not found: {sid}")
        if row.agent_id != job.agent_id or (row.user_key or "") != (job.user_key or ""):
            raise RuntimeError("session ownership mismatch")
        return sid

    if target == "latest":
        latest = await repo.find_latest_user_session_id(
            session, agent_id=job.agent_id, user_key=job.user_key or ""
        )
        if latest:
            return latest
        # Fall back to origin per design.
        target = "origin"

    if target == "origin":
        sid = (job.created_from_session_id or "").strip()
        if not sid:
            raise RuntimeError("origin session missing")
        row = await repo.get_session_row(session, sid)
        if not row:
            raise RuntimeError(f"origin session not found: {sid}")
        return sid

    raise RuntimeError(f"unknown session_target: {target}")


async def fire_assistant_message(
    session: AsyncSession,
    job: AgentScheduleRecord,
    *,
    session_id: str,
) -> dict[str, Any]:
    trigger = schedule_trigger_text(job.id, job.payload_text)
    run = await repo.create_run(
        session,
        session_id=session_id,
        user_input=trigger,
        model_name=None,
    )
    await repo.add_item(
        session,
        run_id=run.id,
        session_id=session_id,
        type="assistant_message",
        content=(job.payload_text or "").strip() or trigger,
        visibility="user",
    )
    await repo.finalize_run(
        session,
        run.id,
        status="completed",
        finish_reason="schedule",
    )
    await repo.touch_session(session, session_id)
    return {"ok": True, "session_id": session_id, "run_id": run.id}


async def fire_synthetic_user(
    *,
    agent: AgentOut,
    session_id: str,
    trigger: str,
) -> dict[str, Any]:
    from agent_factory_server.runtime.runner import (
        ChatEnqueued,
        ChatStarted,
        WaitingHumanError,
        run_supervisor,
    )

    try:
        result = await run_supervisor.enqueue_or_start(
            agent=agent,
            session_id=session_id,
            user_message=trigger,
        )
    except WaitingHumanError as exc:
        raise RuntimeError(f"session waiting_human: {exc.run_id}") from exc

    run_id = None
    if isinstance(result, ChatStarted):
        run_id = run_supervisor.active_run_for_session(session_id)
    return {
        "ok": True,
        "session_id": session_id,
        "run_id": run_id,
        "enqueued": isinstance(result, ChatEnqueued),
    }


async def fire_schedule_job(
    job: AgentScheduleRecord,
    *,
    db: Optional[AsyncSession] = None,
) -> dict[str, Any]:
    """Execute one claimed job. Returns {ok, session_id, run_id?}."""
    trigger = schedule_trigger_text(job.id, job.payload_text)
    mode = (job.run_mode or "assistant_message").strip().lower()

    async def _run(session: AsyncSession) -> dict[str, Any]:
        agent = await repo.get_agent(session, job.agent_id)
        if not agent:
            raise RuntimeError(f"agent not found: {job.agent_id}")

        if mode == "isolated_session":
            title = (job.name or "").strip() or f"定时任务 {job.id[:8]}"
            isolated = await repo.create_delegate_session(
                session,
                agent_id=job.agent_id,
                user_key=job.user_key or "",
                title=title[:80],
                schedule_job_id=job.id,
            )
            session_id = isolated.id
            # Always run an agent turn in isolated sessions.
            return await fire_synthetic_user(
                agent=agent, session_id=session_id, trigger=trigger
            )

        session_id = await resolve_fire_session_id(session, job)
        sess = await repo.get_session_row(session, session_id)
        if not sess:
            raise RuntimeError(f"session not found: {session_id}")
        if sess.agent_id != job.agent_id:
            raise RuntimeError("session agent_id mismatch")

        if mode == "assistant_message":
            return await fire_assistant_message(session, job, session_id=session_id)

        if mode == "synthetic_user":
            return await fire_synthetic_user(
                agent=agent, session_id=session_id, trigger=trigger
            )

        raise RuntimeError(f"unknown run_mode: {mode}")

    if db is not None:
        return await _run(db)
    async with SessionLocal() as session:
        return await _run(session)


def _backoff_seconds(attempt: int) -> int:
    return min(30 * 60, BACKOFF_BASE_SECONDS * (2 ** max(0, attempt - 1)))


def _should_complete(job: AgentScheduleRecord) -> bool:
    kind = (job.schedule_kind or "at").strip().lower()
    if kind == "at":
        return bool(job.delete_after_run)
    return False


async def process_due_schedules(*, limit: int = 10) -> int:
    """Claim and fire due jobs. Returns number fired successfully."""
    async with SessionLocal() as session:
        claimed = await repo.claim_due_schedules(session, limit=limit)
    if not claimed:
        return 0

    ok_count = 0
    for job in claimed:
        try:
            result = await fire_schedule_job(job)
            completed = _should_complete(job)
            next_run: Optional[datetime] = None
            if not completed:
                # Advance from "now" after a successful fire.
                next_run = compute_next_run_at(
                    job, after=datetime.now(timezone.utc)
                )
            fired_session_id = str(result.get("session_id") or "")
            fired_run_id = result.get("run_id")
            async with SessionLocal() as session:
                await repo.mark_schedule_success(
                    session,
                    job.id,
                    fired_session_id=fired_session_id,
                    fired_run_id=fired_run_id,
                    next_run_at=next_run,
                    completed=completed,
                )
            logger.info(
                "schedule_fire_ok job_id=%s agent_id=%s kind=%s mode=%s "
                "session_id=%s run_id=%s completed=%s attempt=%s",
                job.id,
                job.agent_id,
                job.schedule_kind,
                job.run_mode,
                fired_session_id,
                fired_run_id,
                completed,
                int(job.attempt_count or 0) + 1,
            )
            ok_count += 1
        except Exception as exc:
            attempt = int(job.attempt_count or 0) + 1
            failed = attempt >= MAX_ATTEMPTS_BEFORE_FAIL
            delay = _backoff_seconds(attempt)
            next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            logger.warning(
                "schedule_fire_fail job_id=%s agent_id=%s kind=%s mode=%s "
                "attempt=%s/%s failed=%s error=%s",
                job.id,
                job.agent_id,
                job.schedule_kind,
                job.run_mode,
                attempt,
                MAX_ATTEMPTS_BEFORE_FAIL,
                failed,
                exc,
                exc_info=True,
            )
            async with SessionLocal() as session:
                await repo.mark_schedule_failure(
                    session,
                    job.id,
                    error=str(exc),
                    next_run_at=None if failed else next_at,
                    failed=failed,
                )
    return ok_count


async def _worker_loop() -> None:
    logger.info("scheduler worker started (tick=%ss)", WORKER_TICK_SECONDS)
    while True:
        try:
            await process_due_schedules()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler worker tick failed")
        await asyncio.sleep(WORKER_TICK_SECONDS)


def start_scheduler_worker() -> asyncio.Task[None]:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return _worker_task
    _worker_task = asyncio.create_task(_worker_loop(), name="agent-scheduler")
    return _worker_task


async def stop_scheduler_worker() -> None:
    global _worker_task
    task = _worker_task
    _worker_task = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
