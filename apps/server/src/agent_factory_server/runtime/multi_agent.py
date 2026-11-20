"""Multi-agent delegation + project board builtins (Phase G)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.db.session import SessionLocal
from agent_factory_server.domain.schemas import (
    AgentOut,
    AgentRuntimeConfig,
    BoardArtifactWrite,
    MultiAgentPolicy,
    TeamOut,
    ToolDef,
)
from agent_factory_server.runtime.cancel import run_cancel_registry

logger = logging.getLogger(__name__)

DELEGATE_TOOL_NAME = "agent_delegate"
DELEGATE_STATUS_TOOL_NAME = "agent_delegate_status"
BOARD_LIST_TOOL_NAME = "board_list"
BOARD_READ_TOOL_NAME = "board_read"
BOARD_WRITE_TOOL_NAME = "board_write"

DELEGATE_TOOL_NAMES = frozenset({DELEGATE_TOOL_NAME, DELEGATE_STATUS_TOOL_NAME})
BOARD_TOOL_NAMES = frozenset(
    {BOARD_LIST_TOOL_NAME, BOARD_READ_TOOL_NAME, BOARD_WRITE_TOOL_NAME}
)
MULTI_AGENT_TOOL_NAMES = DELEGATE_TOOL_NAMES | BOARD_TOOL_NAMES

# Background async children: child_run_id → asyncio.Task
_async_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}


@dataclass
class DelegateContext:
    parent_agent: AgentOut
    parent_session_id: str
    parent_run_id: str
    parent_depth: int
    root_session_id: str
    user_key: str
    locale: str
    client_device_id: Optional[str]
    call_id: Optional[str]
    team: TeamOut
    runtime: AgentRuntimeConfig


def resolve_multi_agent_policy(
    raw: MultiAgentPolicy | dict[str, Any] | None,
) -> MultiAgentPolicy:
    if isinstance(raw, MultiAgentPolicy):
        return raw
    if isinstance(raw, dict):
        try:
            return MultiAgentPolicy.model_validate(raw)
        except Exception:  # noqa: BLE001
            return MultiAgentPolicy()
    return MultiAgentPolicy()


def delegate_tools(*, locale: str = "zh", allow_async: bool = True) -> list[ToolDef]:
    wait_enum = ["await", "async"] if allow_async else ["await"]
    wait_desc = (
        "await=阻塞等结果；async=立即返回 child_run_id，再用 agent_delegate_status 查询"
        if locale.startswith("zh")
        else "await=block until done; async=return child_run_id and poll with agent_delegate_status"
    )
    tools = [
        ToolDef(
            name=DELEGATE_TOOL_NAME,
            description=(
                "把任务委托给团队中的另一个 Agent（子 Agent）。"
                "用 role 或 agent_id 指定对象；task 写清目标。"
                "可把 Board 上的 path 放进 context_refs 供对方阅读。"
                if locale.startswith("zh")
                else "Delegate a task to another team Agent. "
                "Specify role or agent_id; put board paths in context_refs."
            ),
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Target agent id"},
                    "role": {
                        "type": "string",
                        "description": "Team role alias, e.g. pm / dev / qa",
                    },
                    "task": {
                        "type": "string",
                        "description": "Task instructions for the child agent",
                    },
                    "context_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Board artifact paths to inject",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "Child run wall-clock timeout; default 600s (10 min), max 1800",
                    },
                    "max_tool_rounds": {"type": "integer"},
                    "wait": {
                        "type": "string",
                        "enum": wait_enum,
                        "description": wait_desc,
                    },
                },
                "required": ["task"],
            },
        ),
    ]
    if allow_async:
        tools.append(
            ToolDef(
                name=DELEGATE_STATUS_TOOL_NAME,
                description=(
                    "查询异步委托的子 Run 状态与结果摘要"
                    if locale.startswith("zh")
                    else "Poll status/summary of an async delegated child run"
                ),
                execution="builtin",
                parameters={
                    "type": "object",
                    "properties": {
                        "child_run_id": {"type": "string"},
                    },
                    "required": ["child_run_id"],
                },
            )
        )
    return tools


def board_tools(*, locale: str = "zh") -> list[ToolDef]:
    zh = locale.startswith("zh")
    return [
        ToolDef(
            name=BOARD_LIST_TOOL_NAME,
            description=(
                "列出当前项目 Board 上的产物（path / title / version）"
                if zh
                else "List artifacts on the current project board"
            ),
            execution="builtin",
            parameters={"type": "object", "properties": {}},
        ),
        ToolDef(
            name=BOARD_READ_TOOL_NAME,
            description=(
                "读取 Board 产物正文（path 或 id）"
                if zh
                else "Read a board artifact by path or id"
            ),
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "id": {"type": "string"},
                },
            },
        ),
        ToolDef(
            name=BOARD_WRITE_TOOL_NAME,
            description=(
                "写入或更新 Board 产物（同 path 会升 version）"
                if zh
                else "Create or update a board artifact (version++ on same path)"
            ),
            execution="builtin",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
    ]


async def merge_agent_tools_with_multi_agent(
    tools: list[ToolDef],
    *,
    agent: AgentOut,
    runtime: AgentRuntimeConfig,
    delegation_depth: int = 0,
    locale: str = "zh",
    session: Optional[AsyncSession] = None,
) -> list[ToolDef]:
    """Inject delegate + board tools when the agent participates in a team."""
    merged = list(tools)
    existing = {t.name for t in merged}
    policy = resolve_multi_agent_policy(
        runtime.multi_agent if runtime else None
    )
    team: Optional[TeamOut] = None
    if session is not None and policy.enabled and policy.team_id:
        team = await repo.get_team(session, policy.team_id)

    # Board tools: lead (enabled multi_agent) OR any team member agent.
    board_ok = False
    if team and team.enabled:
        member_ids = {m.agent_id for m in team.members} | {team.lead_agent_id}
        if agent.id in member_ids:
            board_ok = True
    elif policy.enabled and policy.team_id and session is not None:
        # team missing / disabled → no board
        board_ok = False

    # Also allow board if this agent is a member of any enabled team (child runs).
    if session is not None and not board_ok:
        for t in await repo.list_teams(session):
            if not t.enabled:
                continue
            ids = {m.agent_id for m in t.members} | {t.lead_agent_id}
            if agent.id in ids:
                team = team or t
                board_ok = True
                break

    if board_ok:
        for tool in board_tools(locale=locale):
            if tool.name not in existing:
                merged.append(tool)
                existing.add(tool.name)

    # Delegate tools only for lead, and only when depth < max_depth.
    if (
        team
        and team.enabled
        and policy.enabled
        and policy.team_id == team.id
        and agent.id == team.lead_agent_id
        and int(delegation_depth or 0) < int(team.max_depth or 1)
    ):
        for tool in delegate_tools(locale=locale, allow_async=bool(team.allow_async)):
            if tool.name not in existing:
                merged.append(tool)
                existing.add(tool.name)

    return merged


def _resolve_member(
    team: TeamOut, *, agent_id: Optional[str], role: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    aid = (agent_id or "").strip()
    role_key = (role or "").strip().lower()
    members = list(team.members)
    if aid:
        for m in members:
            if m.agent_id == aid:
                return m.agent_id, m.role or None
        if aid == team.lead_agent_id:
            return None, None  # cannot delegate to self as member via id alone
        return None, None
    if role_key:
        for m in members:
            if (m.role or "").strip().lower() == role_key:
                return m.agent_id, m.role
    return None, None


async def _load_context_refs(
    session: AsyncSession,
    *,
    root_session_id: str,
    refs: list[str],
    team_id: Optional[str],
    user_key: str,
) -> str:
    if not refs:
        return ""
    project = await repo.get_or_create_board_project(
        session,
        lead_session_id=root_session_id,
        team_id=team_id,
        user_key=user_key,
    )
    chunks: list[str] = []
    for ref in refs:
        path = str(ref or "").strip().lstrip("/")
        if not path:
            continue
        art = await repo.get_board_artifact(session, project_id=project.id, path=path)
        if not art:
            chunks.append(f"### {path}\n(missing)")
            continue
        body = art.content or ""
        if len(body) > 8000:
            body = body[:7980] + "\n…(truncated)"
        chunks.append(f"### {art.path} (v{art.version})\n{body}")
    if not chunks:
        return ""
    return "## Board context\n\n" + "\n\n".join(chunks)


def _summarize_text(text: str, max_chars: int) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if len(raw) <= max_chars:
        return raw
    return raw[: max_chars - 20] + "\n…(truncated)"


async def _run_child_loop(
    *,
    child_agent: AgentOut,
    child_session_id: str,
    child_run_id: str,
    parent_run_id: str,
    max_tool_rounds: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    from agent_factory_server.runtime.loop import continue_chat_loop, resolve_runtime

    runtime = resolve_runtime(child_agent)
    # Cap child rounds by team/request.
    runtime = runtime.model_copy(
        update={"max_tool_rounds": max(1, int(max_tool_rounds))}
    )

    final_text = ""
    final_status = "completed"
    hitl: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    async def _consume() -> None:
        nonlocal final_text, final_status, hitl, error
        async with SessionLocal() as db:
            # Seed user_message item if missing (create_run alone doesn't add it).
            from agent_factory_server.db.models import ItemRecord
            from sqlmodel import select

            existing = (
                await db.exec(
                    select(ItemRecord).where(
                        ItemRecord.run_id == child_run_id,
                        ItemRecord.type == "user_message",
                    )
                )
            ).first()
            if not existing:
                run_row = await repo.get_run(db, child_run_id)
                await repo.add_item(
                    db,
                    run_id=child_run_id,
                    session_id=child_session_id,
                    type="user_message",
                    content=run_row.user_input if run_row else "",
                )

            async for event in continue_chat_loop(
                db,
                agent=child_agent,
                session_id=child_session_id,
                run_id=child_run_id,
                runtime=runtime,
            ):
                if await run_cancel_registry.is_cancelled(child_run_id):
                    await repo.finalize_run(
                        db,
                        child_run_id,
                        status="cancelled",
                        finish_reason="cancelled",
                        error_message="parent cancelled",
                    )
                    final_status = "cancelled"
                    error = "cancelled"
                    return
                et = event.get("type")
                if et == "message" and event.get("role") == "assistant":
                    final_text = str(event.get("content") or final_text)
                if et == "assistant_message":
                    final_text = str(event.get("content") or final_text)
                if et == "ask_user" or (
                    et == "tool_call" and event.get("name") == "ask_user"
                ):
                    hitl = {
                        "kind": "ask_user",
                        "call_id": event.get("call_id"),
                        "payload": event.get("arguments") or event.get("payload"),
                    }
                if et == "tool_approval_required":
                    hitl = {
                        "kind": "approval",
                        "call_id": event.get("call_id"),
                        "name": event.get("name"),
                        "arguments": event.get("arguments"),
                    }
                if et == "done":
                    final_status = str(event.get("status") or final_status)
                if et == "error":
                    error = str(event.get("error") or "error")
                    final_status = "failed"

            run_row = await repo.get_run(db, child_run_id)
            if run_row:
                final_status = run_row.status or final_status
                if run_row.status == "waiting_human":
                    # v1: abort HITL children for the parent.
                    pending = {
                        "kind": "hitl",
                        "status": "waiting_human",
                    }
                    hitl = hitl or pending
                    await repo.finalize_run(
                        db,
                        child_run_id,
                        status="failed",
                        finish_reason="child_hitl_required",
                        error_message="child requires human input (not bubbled in v1)",
                    )
                    final_status = "failed"
                if not final_text:
                    final_text = (
                        await repo._last_assistant_summary(db, child_run_id) or ""
                    )

    try:
        await asyncio.wait_for(_consume(), timeout=max(5.0, float(timeout_seconds)))
    except asyncio.TimeoutError:
        async with SessionLocal() as db:
            await run_cancel_registry.request_cancel(child_run_id)
            await repo.finalize_run(
                db,
                child_run_id,
                status="cancelled",
                finish_reason="timeout",
                error_message="delegate timeout",
            )
        return {
            "ok": False,
            "error": "timeout",
            "child_run_id": child_run_id,
            "status": "cancelled",
        }
    finally:
        await run_cancel_registry.unregister_child(parent_run_id, child_run_id)

    if hitl:
        return {
            "ok": False,
            "error": "child_hitl_required",
            "hitl": hitl,
            "child_run_id": child_run_id,
            "status": final_status,
            "summary": _summarize_text(final_text, 2000),
        }
    if final_status in ("cancelled",):
        return {
            "ok": False,
            "error": error or "cancelled",
            "child_run_id": child_run_id,
            "status": final_status,
        }
    if final_status in ("failed",):
        return {
            "ok": False,
            "error": error or "child failed",
            "child_run_id": child_run_id,
            "status": final_status,
            "summary": _summarize_text(final_text, 2000),
        }
    return {
        "ok": True,
        "child_run_id": child_run_id,
        "status": final_status,
        "summary": final_text,
    }


async def execute_delegate(
    *,
    ctx: DelegateContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    team = ctx.team
    task = str(arguments.get("task") or "").strip()
    if not task:
        return {"ok": False, "error": "task is required"}

    target_id, role = _resolve_member(
        team,
        agent_id=str(arguments.get("agent_id") or "") or None,
        role=str(arguments.get("role") or "") or None,
    )
    if not target_id:
        return {
            "ok": False,
            "error": "target agent not in team whitelist (provide agent_id or role)",
        }
    if target_id == ctx.parent_agent.id:
        return {"ok": False, "error": "cannot delegate to self"}

    child_depth = int(ctx.parent_depth or 0) + 1
    if child_depth > int(team.max_depth or 1):
        return {"ok": False, "error": "delegation depth exceeded"}

    wait_mode = str(arguments.get("wait") or "await").strip().lower()
    if wait_mode not in ("await", "async"):
        wait_mode = "await"
    if wait_mode == "async" and not team.allow_async:
        return {"ok": False, "error": "async delegation disabled for this team"}

    # Default 10 minutes — heavy PRD/design/code tasks often exceed 3 minutes.
    timeout = float(arguments.get("timeout_seconds") or 600)
    timeout = max(5.0, min(timeout, 1800.0))
    max_rounds = int(
        arguments.get("max_tool_rounds") or team.max_child_rounds or 8
    )
    max_rounds = max(1, min(max_rounds, int(team.max_child_rounds or 8)))

    refs_raw = arguments.get("context_refs") or []
    refs = [str(x) for x in refs_raw] if isinstance(refs_raw, list) else []

    async with SessionLocal() as db:
        running = await repo.count_running_child_runs(
            db, root_session_id=ctx.root_session_id
        )
        if running >= int(team.max_parallel or 3):
            return {
                "ok": False,
                "error": f"too many concurrent child runs ({running}/{team.max_parallel})",
            }

        child_agent = await repo.get_agent(db, target_id)
        if not child_agent:
            return {"ok": False, "error": f"agent not found: {target_id}"}

        context_block = await _load_context_refs(
            db,
            root_session_id=ctx.root_session_id,
            refs=refs,
            team_id=team.id,
            user_key=ctx.user_key,
        )
        user_input = task if not context_block else f"{task}\n\n{context_block}"

        child_session = await repo.create_delegate_session(
            db,
            agent_id=child_agent.id,
            user_key=ctx.user_key,
            title=f"[delegate:{role or child_agent.name}] {task[:40]}",
            locale=ctx.locale,
            system_prompt=None,
            client_device_id=ctx.client_device_id,
        )
        from agent_factory_server.runtime.locale import pick_system_prompt_template
        from agent_factory_server.runtime.prompt_template import render_prompt_template

        template = pick_system_prompt_template(
            system_prompt=child_agent.system_prompt,
            system_prompts=getattr(child_agent, "system_prompts", None) or {},
            locale=ctx.locale,
        )
        child_session.system_prompt = render_prompt_template(template, {})
        db.add(child_session)
        await db.commit()

        child_run = await repo.create_run(
            db,
            session_id=child_session.id,
            user_input=user_input,
            parent_run_id=ctx.parent_run_id,
            root_session_id=ctx.root_session_id,
            delegation_call_id=ctx.call_id,
            delegation_depth=child_depth,
        )
        await run_cancel_registry.register_child(ctx.parent_run_id, child_run.id)

        delegation_meta = {
            "child_run_id": child_run.id,
            "child_session_id": child_session.id,
            "child_agent_id": child_agent.id,
            "role": role,
            "wait": wait_mode,
            "status": "running",
        }

        if wait_mode == "async":

            async def _bg() -> dict[str, Any]:
                result = await _run_child_loop(
                    child_agent=child_agent,
                    child_session_id=child_session.id,
                    child_run_id=child_run.id,
                    parent_run_id=ctx.parent_run_id,
                    max_tool_rounds=max_rounds,
                    timeout_seconds=timeout,
                )
                max_chars = int(team.result_max_chars or 4000)
                if result.get("summary"):
                    result["summary"] = _summarize_text(
                        str(result["summary"]), max_chars
                    )
                return result

            task_obj = asyncio.create_task(_bg())
            _async_tasks[child_run.id] = task_obj
            return {
                "ok": True,
                "status": "running",
                "wait": "async",
                "child_run_id": child_run.id,
                "child_agent_id": child_agent.id,
                "role": role,
                "delegation": delegation_meta,
                "message": "child started; poll with agent_delegate_status",
            }

    # await mode
    result = await _run_child_loop(
        child_agent=child_agent,
        child_session_id=child_session.id,
        child_run_id=child_run.id,
        parent_run_id=ctx.parent_run_id,
        max_tool_rounds=max_rounds,
        timeout_seconds=timeout,
    )
    max_chars = int(team.result_max_chars or 4000)
    if result.get("summary"):
        result["summary"] = _summarize_text(str(result["summary"]), max_chars)

    # Attach artifact list snapshot for parent convenience.
    async with SessionLocal() as db:
        board = await repo.get_board_project_out(db, ctx.root_session_id)
        if board:
            result["artifacts"] = [
                {"id": a.id, "path": a.path, "title": a.title, "version": a.version}
                for a in board.artifacts
            ]
    result["delegation"] = {
        **delegation_meta,
        "status": result.get("status") or ("completed" if result.get("ok") else "failed"),
    }
    result["child_agent_id"] = child_agent.id
    result["role"] = role
    return result


async def execute_delegate_status(
    *, child_run_id: str, result_max_chars: int = 4000
) -> dict[str, Any]:
    rid = (child_run_id or "").strip()
    if not rid:
        return {"ok": False, "error": "child_run_id is required"}

    task = _async_tasks.get(rid)
    if task and not task.done():
        async with SessionLocal() as db:
            run = await repo.get_run(db, rid)
            return {
                "ok": True,
                "child_run_id": rid,
                "status": (run.status if run else "running"),
                "running": True,
            }
    if task and task.done():
        try:
            result = task.result()
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc), "child_run_id": rid}
        _async_tasks.pop(rid, None)
        return result

    async with SessionLocal() as db:
        run = await repo.get_run(db, rid)
        if not run:
            return {"ok": False, "error": "child run not found"}
        summary = await repo._last_assistant_summary(
            db, rid, max_chars=result_max_chars
        )
        terminal = run.status in (
            "completed",
            "failed",
            "cancelled",
            "limit_reached",
        )
        return {
            "ok": terminal and run.status in ("completed", "limit_reached"),
            "child_run_id": rid,
            "status": run.status,
            "finish_reason": run.finish_reason,
            "error_message": run.error_message,
            "summary": summary,
            "running": not terminal,
        }


async def execute_board_tool(
    session: AsyncSession,
    *,
    name: str,
    arguments: dict[str, Any],
    agent_id: str,
    root_session_id: str,
    user_key: str,
    team_id: Optional[str] = None,
) -> dict[str, Any]:
    project = await repo.get_or_create_board_project(
        session,
        lead_session_id=root_session_id,
        team_id=team_id,
        user_key=user_key or "",
    )
    if name == BOARD_LIST_TOOL_NAME:
        arts = await repo.list_board_artifacts(session, project.id)
        return {
            "ok": True,
            "project_id": project.id,
            "artifacts": [
                {
                    "id": a.id,
                    "path": a.path,
                    "title": a.title,
                    "version": a.version,
                    "author_agent_id": a.author_agent_id,
                    "updated_at": a.updated_at.isoformat(),
                }
                for a in arts
            ],
        }
    if name == BOARD_READ_TOOL_NAME:
        path = str(arguments.get("path") or "").strip() or None
        artifact_id = str(arguments.get("id") or "").strip() or None
        art = await repo.get_board_artifact(
            session, project_id=project.id, path=path, artifact_id=artifact_id
        )
        if not art:
            return {"ok": False, "error": "artifact not found"}
        return {
            "ok": True,
            "artifact": art.model_dump(mode="json"),
        }
    if name == BOARD_WRITE_TOOL_NAME:
        path = str(arguments.get("path") or "").strip()
        content = str(arguments.get("content") or "")
        title = arguments.get("title")
        try:
            art = await repo.write_board_artifact(
                session,
                project_id=project.id,
                payload=BoardArtifactWrite(
                    path=path,
                    title=str(title) if title is not None else None,
                    content=content,
                ),
                author_agent_id=agent_id,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "artifact": art.model_dump(mode="json")}
    return {"ok": False, "error": f"unknown board tool: {name}"}


async def execute_multi_agent_tool(
    session: AsyncSession,
    *,
    name: str,
    arguments: dict[str, Any],
    agent: AgentOut,
    runtime: AgentRuntimeConfig,
    session_id: str,
    run_id: str,
    session_user_key: Optional[str],
    locale: str = "zh",
    client_device_id: Optional[str] = None,
    call_id: Optional[str] = None,
) -> dict[str, Any]:
    run = await repo.get_run(session, run_id)
    parent_depth = int(getattr(run, "delegation_depth", 0) or 0) if run else 0
    root_session_id = (
        (getattr(run, "root_session_id", None) if run else None)
        or session_id
    )
    user_key = (session_user_key or "").strip()
    policy = resolve_multi_agent_policy(runtime.multi_agent if runtime else None)

    team: Optional[TeamOut] = None
    if policy.team_id:
        team = await repo.get_team(session, policy.team_id)
    if team is None:
        # Child agents: find a team that includes them.
        for t in await repo.list_teams(session):
            if not t.enabled:
                continue
            ids = {m.agent_id for m in t.members} | {t.lead_agent_id}
            if agent.id in ids:
                team = t
                break

    if name in BOARD_TOOL_NAMES:
        return await execute_board_tool(
            session,
            name=name,
            arguments=arguments or {},
            agent_id=agent.id,
            root_session_id=root_session_id,
            user_key=user_key,
            team_id=team.id if team else None,
        )

    if name == DELEGATE_STATUS_TOOL_NAME:
        max_chars = int(team.result_max_chars) if team else 4000
        return await execute_delegate_status(
            child_run_id=str((arguments or {}).get("child_run_id") or ""),
            result_max_chars=max_chars,
        )

    if name == DELEGATE_TOOL_NAME:
        if not team or not team.enabled:
            return {"ok": False, "error": "no enabled team bound for delegation"}
        if not policy.enabled or agent.id != team.lead_agent_id:
            return {"ok": False, "error": "only the team lead may delegate"}
        if parent_depth >= int(team.max_depth or 1):
            return {"ok": False, "error": "delegation depth exceeded"}
        ctx = DelegateContext(
            parent_agent=agent,
            parent_session_id=session_id,
            parent_run_id=run_id,
            parent_depth=parent_depth,
            root_session_id=root_session_id,
            user_key=user_key,
            locale=locale,
            client_device_id=client_device_id,
            call_id=call_id,
            team=team,
            runtime=runtime,
        )
        return await execute_delegate(ctx=ctx, arguments=arguments or {})

    return {"ok": False, "error": f"unknown multi-agent tool: {name}"}
