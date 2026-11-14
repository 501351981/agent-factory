from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Optional
from uuid import uuid4

from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.config import get_settings
from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import (
    AgentOut,
    AgentRuntimeConfig,
    ContextPolicy,
    MemoryPolicy,
    SkillDef,
    ToolDef,
)
from agent_factory_server.llm.openai_compat import OpenAICompatClient
from agent_factory_server.runtime.cancel import run_cancel_registry
from agent_factory_server.runtime.context import assemble_llm_context, resolve_context_policy
from agent_factory_server.runtime.hitl import (
    ASK_USER_TOOL_NAME,
    HITL_KIND_APPROVAL,
    ApprovalDecision,
    approval_display_fields,
    approval_reject_result,
    ask_user_tool,
    normalize_approval_decision,
    parse_ask_user_arguments,
    parse_tool_call_payload,
    parse_client_approval_check_result,
    resolve_approval_decision,
    waiting_expires_at,
)
from agent_factory_server.runtime.memory import (
    execute_memory_tool,
    load_memory_block,
    merge_agent_tools_with_memory,
    resolve_memory_policy,
)
from agent_factory_server.runtime.skill_runtime import execute_skill_tool
from agent_factory_server.runtime.skills import (
    SKILL_TOOL_NAMES,
    format_skills_catalog,
    has_client_origin_skills,
    merge_agent_tools_with_skills,
)
from agent_factory_server.tools.executors import execute_tool


def merge_effective_tools(
    agent_tools: list[ToolDef],
    *,
    memory_enabled: bool,
    locale: str = "zh",
    skills: Optional[list[SkillDef]] = None,
) -> list[ToolDef]:
    merged = merge_agent_tools_with_memory(
        agent_tools, enabled=memory_enabled, locale=locale
    )
    merged = merge_agent_tools_with_skills(
        merged, skills=skills or [], locale=locale
    )
    existing = {t.name for t in merged}
    if ASK_USER_TOOL_NAME not in existing:
        merged.append(ask_user_tool(locale=locale))
    return merged


async def _emit_skipped_siblings(
    session: AsyncSession,
    *,
    run_id: str,
    session_id: str,
    tool_calls: list[dict[str, Any]],
    start_index: int,
    skip_reason: str,
) -> AsyncIterator[dict[str, Any]]:
    """Close remaining sibling tool_calls so LLM projection stays valid."""
    for sibling in tool_calls[start_index:]:
        sib_id = sibling.get("id") or str(uuid4())
        sib_fn = sibling.get("function") or {}
        sib_name = sib_fn.get("name") or ""
        sib_args = _parse_tool_arguments(sib_fn.get("arguments"))
        await repo.add_item(
            session,
            run_id=run_id,
            session_id=session_id,
            type="tool_call",
            call_id=sib_id,
            name=sib_name,
            content=None,
            payload={"arguments": sib_args, "raw": sibling},
        )
        skip = {"ok": False, "error": skip_reason, "skipped": True}
        skip_text = json.dumps(skip, ensure_ascii=False)
        await repo.add_item(
            session,
            run_id=run_id,
            session_id=session_id,
            type="tool_result",
            call_id=sib_id,
            name=sib_name,
            content=skip_text,
            payload={"ok": False, "skipped": True},
        )
        yield {
            "type": "tool_call",
            "run_id": run_id,
            "call_id": sib_id,
            "name": sib_name,
            "arguments": sib_args,
        }
        yield {
            "type": "tool_result",
            "run_id": run_id,
            "call_id": sib_id,
            "name": sib_name,
            "result": skip,
        }


async def _resolve_needs_approval(
    *,
    tool: ToolDef | None,
    name: str,
    arguments: dict[str, Any],
    session_id: str,
    client_device_id: Optional[str],
    timeout_seconds: float,
) -> bool:
    """Apply approval_policy; may ask Bridge when default=ask_client."""
    decision = resolve_approval_decision(tool, arguments)
    if decision == "skip":
        return False
    if decision == "require":
        return True
    # ask_client
    if tool is None or tool.execution != "client":
        return True
    from agent_factory_server.tools.client_hub import client_hub

    device = (client_device_id or "").strip()
    if not device:
        return True
    check_id = f"apprchk_{uuid4().hex[:16]}"
    payload = await client_hub.approval_check(
        device_id=device,
        check_id=check_id,
        name=name,
        arguments=arguments if isinstance(arguments, dict) else {},
        session_id=session_id,
        timeout=max(3.0, min(float(timeout_seconds or 15.0), 60.0)),
    )
    return parse_client_approval_check_result(payload) == "require"


async def _prepare_tool_calls(
    *,
    tool_calls: list[dict[str, Any]],
    tool_map: dict[str, ToolDef],
    session_id: str,
    client_device_id: Optional[str],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Resolve id/name/args/tool/approval for every call before executing any.

    Deciding up front lets us pick parallel vs. sequential execution without
    asking Bridge twice; approval checks are read-only policy lookups.
    """
    prepared: list[dict[str, Any]] = []
    for call in tool_calls:
        call_id = str(call.get("id") or uuid4())
        fn = call.get("function") or {}
        name = str(fn.get("name") or "")
        arguments = _parse_tool_arguments(fn.get("arguments"))
        tool = tool_map.get(name)
        needs_approval = False
        approval_display: dict[str, Any] = {}
        if name != ASK_USER_TOOL_NAME:
            needs_approval = await _resolve_needs_approval(
                tool=tool,
                name=name,
                arguments=arguments,
                session_id=session_id,
                client_device_id=client_device_id,
                timeout_seconds=timeout_seconds,
            )
            if needs_approval:
                approval_display = approval_display_fields(tool, arguments)
        prepared.append(
            {
                "call": call,
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
                "tool": tool,
                "needs_approval": needs_approval,
                "approval_display": approval_display,
            }
        )
    return prepared


def _tool_call_item_payload(prep: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "arguments": prep["arguments"],
        "raw": prep["call"],
    }
    if prep["needs_approval"]:
        display = prep["approval_display"]
        payload["hitl"] = HITL_KIND_APPROVAL
        if display.get("reason"):
            payload["reason"] = display["reason"]
        if display.get("tool_description"):
            payload["tool_description"] = display["tool_description"]
    return payload


async def _execute_prepared_tool(
    prep: dict[str, Any],
    *,
    session: AsyncSession,
    agent: AgentOut,
    session_id: str,
    session_user_key: Optional[str],
    run_id: str,
    runtime: AgentRuntimeConfig,
    client_device_id: Optional[str],
    needs_client_tools: bool,
) -> dict[str, Any]:
    tool = prep["tool"]
    name = prep["name"]
    if not tool:
        return {"ok": False, "error": f"unknown tool: {name}"}
    if tool.execution == "builtin" and name == ASK_USER_TOOL_NAME:
        return {"ok": False, "error": "ask_user must pause"}
    return await _execute_mapped_tool(
        session,
        tool=tool,
        name=name,
        arguments=prep["arguments"],
        agent=agent,
        session_id=session_id,
        session_user_key=session_user_key,
        run_id=run_id,
        runtime=runtime,
        client_device_id=client_device_id,
        needs_client_tools=needs_client_tools,
        call_id=prep["call_id"],
    )


async def _persist_tool_result(
    session: AsyncSession,
    *,
    run_id: str,
    session_id: str,
    prep: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    result_text = json.dumps(result, ensure_ascii=False)
    # Persist full tool output; truncation happens only in LLM projection (L1).
    await repo.add_item(
        session,
        run_id=run_id,
        session_id=session_id,
        type="tool_result",
        call_id=prep["call_id"],
        name=prep["name"],
        content=result_text,
        payload={
            "ok": result.get("ok", True),
            "original_chars": len(result_text),
        },
    )
    return {
        "type": "tool_result",
        "run_id": run_id,
        "call_id": prep["call_id"],
        "name": prep["name"],
        "result": result,
    }


# Cap fan-out so one assistant message with 20 tool_calls cannot stampede the
# Bridge / upstream HTTP endpoints.
MAX_PARALLEL_TOOL_CALLS = 4


async def _execute_prepared_tools_parallel(
    prepared: list[dict[str, Any]],
    *,
    session: AsyncSession,
    agent: AgentOut,
    session_id: str,
    session_user_key: Optional[str],
    run_id: str,
    runtime: AgentRuntimeConfig,
    client_device_id: Optional[str],
    needs_client_tools: bool,
) -> list[dict[str, Any]]:
    """Run sibling external tool calls concurrently; results keep call order."""
    semaphore = asyncio.Semaphore(MAX_PARALLEL_TOOL_CALLS)

    async def _one(prep: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                return await _execute_prepared_tool(
                    prep,
                    session=session,
                    agent=agent,
                    session_id=session_id,
                    session_user_key=session_user_key,
                    run_id=run_id,
                    runtime=runtime,
                    client_device_id=client_device_id,
                    needs_client_tools=needs_client_tools,
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

    return list(await asyncio.gather(*(_one(prep) for prep in prepared)))


async def _execute_mapped_tool(
    session: AsyncSession,
    *,
    tool: ToolDef,
    name: str,
    arguments: dict[str, Any],
    agent: AgentOut,
    session_id: str,
    session_user_key: Optional[str],
    run_id: str,
    runtime: AgentRuntimeConfig,
    client_device_id: Optional[str],
    needs_client_tools: bool,
    call_id: Optional[str] = None,
) -> dict[str, Any]:
    try:
        if tool.execution == "builtin":
            if name in SKILL_TOOL_NAMES:
                return await execute_skill_tool(
                    session,
                    name=name,
                    arguments=arguments,
                    agent_id=agent.id,
                    skills=agent.skills or [],
                    session_id=session_id,
                    client_device_id=client_device_id,
                    timeout_seconds=float(
                        getattr(runtime, "tool_timeout_seconds", None) or 30
                    ),
                )
            return await execute_memory_tool(
                session,
                name=name,
                arguments=arguments,
                agent_id=agent.id,
                session_id=session_id,
                user_key=session_user_key,
                run_id=run_id,
                policy=runtime.memory,
            )
        if (
            tool.execution == "client"
            and needs_client_tools
            and not client_device_id
        ):
            from agent_factory_server.tools.client_hub import client_hub

            online = client_hub.online_ids()
            if len(online) > 1:
                return {
                    "ok": False,
                    "error": (
                        "session has no client_device_id and "
                        f"{len(online)} bridges are online; "
                        "pass client_device_id when creating the session"
                    ),
                }
            return {
                "ok": False,
                "error": (
                    "session has no client_device_id and "
                    "no local bridge is online"
                ),
            }
        return await execute_tool(
            tool,
            arguments=arguments,
            session_id=session_id,
            client_device_id=client_device_id,
            timeout_seconds=runtime.tool_timeout_seconds,
            call_id=call_id,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def build_system_prompt(
    agent: AgentOut,
    *,
    resolved_system_prompt: Optional[str] = None,
    locale: str = "zh",
) -> str:
    """Build the system message.

    Prefer the session-instantiated prompt (template vars already resolved).
    Fall back to the Agent template for legacy sessions without a snapshot.
    Skills catalog (name + description only) is always appended from the live Agent.
    """
    base = (
        resolved_system_prompt
        if resolved_system_prompt is not None and str(resolved_system_prompt).strip() != ""
        else agent.system_prompt
    )
    parts = [base.strip()]
    catalog = format_skills_catalog(agent.skills or [], locale=locale)
    if catalog:
        parts.append("\n\n" + catalog)
    return "\n".join(parts).strip()


def tools_to_openai(tools: list[ToolDef]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def resolve_runtime(agent: AgentOut) -> AgentRuntimeConfig:
    settings = get_settings()
    runtime = agent.runtime or AgentRuntimeConfig()
    policy = resolve_context_policy(
        runtime.tool_result_max_chars,
        runtime.context_policy or ContextPolicy(),
    )
    memory = resolve_memory_policy(runtime.memory)
    max_rounds = int(runtime.max_tool_rounds or settings.max_tool_rounds)
    # Defensive clamp: older rows may hold out-of-range values.
    max_rounds = max(1, min(max_rounds, 32))
    return AgentRuntimeConfig(
        max_tool_rounds=max_rounds,
        on_max_rounds=runtime.on_max_rounds,
        max_run_tokens=int(runtime.max_run_tokens or settings.max_run_tokens),
        tool_timeout_seconds=runtime.tool_timeout_seconds or settings.tool_timeout_seconds,
        run_timeout_seconds=runtime.run_timeout_seconds,
        parallel_tool_calls=runtime.parallel_tool_calls,
        tool_result_max_chars=policy.tool_result_max_chars,
        context_policy=policy,
        memory=memory,
    )


async def resolve_llm_client(
    session: AsyncSession,
    agent: AgentOut,
    *,
    model_profile_id: Optional[str] = None,
) -> tuple[OpenAICompatClient, str, float | None, bool, int, int, Optional[str], float, float]:
    """Return client + model settings + profile id + input/output CNY per 1M tokens."""
    settings = get_settings()
    profile = None
    override_id = (model_profile_id or "").strip() or None
    preferred_id = override_id or agent.model_profile_id
    if preferred_id:
        profile = await repo.get_model_profile(session, preferred_id)
        if profile is None and override_id:
            raise RuntimeError(f"模型配置不存在：{override_id}")
        if profile and not profile.enabled:
            raise RuntimeError(f"模型配置已禁用：{profile.name}")
    if profile is None:
        profile = await repo.get_default_model_profile(session)

    if profile is not None:
        model_name = agent.model or profile.model_name
        temperature = float(profile.temperature if profile.temperature is not None else 0.3)
        include_reasoning = "deepseek" in (profile.provider or "").lower() or "deepseek" in (
            model_name or ""
        ).lower()
        window = int(getattr(profile, "context_window_tokens", None) or 128000)
        reserve = int(getattr(profile, "reserve_output_tokens", None) or 8192)
        in_price = float(getattr(profile, "input_price_per_1m", None) or 0.0)
        out_price = float(getattr(profile, "output_price_per_1m", None) or 0.0)
        return (
            OpenAICompatClient(
                base_url=profile.base_url,
                api_key=repo.reveal_profile_api_key(profile),
                default_model=profile.model_name,
            ),
            model_name,
            temperature,
            include_reasoning,
            window,
            reserve,
            profile.id,
            in_price,
            out_price,
        )

    model_name = agent.model or settings.openai_model
    include_reasoning = "deepseek" in (model_name or "").lower()
    return OpenAICompatClient(), model_name, None, include_reasoning, 128000, 8192, None, 0.0, 0.0


def _estimate_tokens_from_text(text: str, *, chars_per_token: float = 4.0) -> int:
    chars = len(text or "")
    if chars <= 0:
        return 0
    return max(1, int((chars + chars_per_token - 1) // chars_per_token))


async def _record_round_usage(
    session: AsyncSession,
    *,
    session_id: str,
    run_id: str,
    agent_id: str,
    model_profile_id: Optional[str],
    model_name: str,
    input_price_per_1m: float,
    output_price_per_1m: float,
    usage: Optional[dict[str, Any]],
    messages: list[dict[str, Any]],
    message: dict[str, Any],
) -> int:
    """Record one model round's usage; returns the token total (0 on failure)."""
    prompt = 0
    completion = 0
    total = 0
    cached = 0
    source = "api"
    if isinstance(usage, dict) and usage:
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        if isinstance(details, dict):
            cached = int(
                details.get("cached_tokens")
                or details.get("cache_read_tokens")
                or details.get("cache_read_input_tokens")
                or 0
            )
        # DeepSeek / some gateways expose top-level cache hit counters.
        cached = int(
            usage.get("cached_tokens")
            or usage.get("prompt_cache_hit_tokens")
            or usage.get("cache_read_input_tokens")
            or cached
            or 0
        )
        if cached <= 0:
            miss = int(usage.get("prompt_cache_miss_tokens") or 0)
            if miss > 0 and prompt > miss:
                cached = prompt - miss
        if cached > prompt > 0:
            cached = prompt
    if prompt <= 0 and completion <= 0:
        source = "estimate"
        prompt = sum(
            _estimate_tokens_from_text(str(m.get("content") or ""))
            + _estimate_tokens_from_text(json.dumps(m.get("tool_calls") or [], ensure_ascii=False))
            for m in messages
        )
        completion = _estimate_tokens_from_text(str(message.get("content") or ""))
        completion += _estimate_tokens_from_text(
            json.dumps(message.get("tool_calls") or [], ensure_ascii=False)
        )
        completion += _estimate_tokens_from_text(str(message.get("reasoning_content") or ""))
        total = prompt + completion
    try:
        await repo.record_llm_usage(
            session,
            session_id=session_id,
            run_id=run_id,
            agent_id=agent_id,
            model_profile_id=model_profile_id,
            model_name=model_name,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cached_tokens=cached,
            source=source,
            input_price_per_1m=input_price_per_1m,
            output_price_per_1m=output_price_per_1m,
        )
    except Exception:  # noqa: BLE001
        # Usage accounting must not break the chat loop.
        return 0
    return total


def _parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if not isinstance(raw_args, str):
        return {}
    try:
        parsed = json.loads(raw_args)
        return parsed if isinstance(parsed, dict) else {"_raw": raw_args}
    except json.JSONDecodeError:
        return {"_raw": raw_args}


async def _stream_model_round(
    llm: OpenAICompatClient,
    *,
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]],
    model_name: str,
    temperature: Optional[float],
    run_id: str,
    should_cancel: Optional[Callable[[], Awaitable[bool]]] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield SSE-facing deltas; final yield is `{type:'complete', message:...}`."""
    async for event in llm.chat_stream(
        messages=messages,
        tools=tools,
        model=model_name,
        temperature=temperature,
        should_cancel=should_cancel,
    ):
        et = event.get("type")
        if et == "reasoning_delta":
            yield {"type": "reasoning_delta", "run_id": run_id, "delta": event.get("delta") or ""}
        elif et == "message_delta":
            yield {"type": "message_delta", "run_id": run_id, "delta": event.get("delta") or ""}
        elif et == "cancelled":
            yield {"type": "cancelled", "run_id": run_id}
        elif et == "complete":
            yield event


async def run_chat_loop(
    session: AsyncSession,
    *,
    agent: AgentOut,
    session_id: str,
    user_message: str,
    model_profile_id: Optional[str] = None,
    queue_item_id: Optional[str] = None,
) -> AsyncIterator[dict[str, Any]]:
    runtime = resolve_runtime(agent)
    try:
        (
            llm,
            model_name,
            temperature,
            include_reasoning,
            context_window_tokens,
            reserve_output_tokens,
            resolved_profile_id,
            input_price_per_1m,
            output_price_per_1m,
        ) = await resolve_llm_client(
            session, agent, model_profile_id=model_profile_id
        )
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "error": str(exc)}
        return

    await repo.expire_waiting_human_runs(session)
    waiting = await repo.get_waiting_human_run(session, session_id)
    if waiting:
        yield {
            "type": "error",
            "error": f"session is waiting_human: {waiting.id}",
            "code": "waiting_human",
        }
        return

    existing = await repo.get_running_run(session, session_id)
    if existing:
        yield {
            "type": "error",
            "error": f"session already has a running run: {existing.id}",
            "code": "run_conflict",
        }
        return

    run = await repo.create_run(
        session,
        session_id=session_id,
        user_input=user_message,
        model_name=model_name,
    )
    run_id = run.id
    if queue_item_id:
        await repo.bind_queue_item_run(session, queue_item_id, run_id)
    await run_cancel_registry.clear(run_id)

    # Title sticks to the first user question; later messages only bump updated_at.
    await repo.touch_session(
        session,
        session_id,
        title=(user_message or "").strip()[:80] or None,
    )
    await repo.add_item(
        session,
        run_id=run_id,
        session_id=session_id,
        type="user_message",
        content=user_message,
    )

    yield {
        "type": "run_start",
        "run_id": run_id,
        "session_id": session_id,
        "status": "running",
    }

    async for event in continue_chat_loop(
        session,
        agent=agent,
        session_id=session_id,
        run_id=run_id,
        model_profile_id=resolved_profile_id or model_profile_id,
        llm=llm,
        model_name=model_name,
        temperature=temperature,
        include_reasoning=include_reasoning,
        context_window_tokens=context_window_tokens,
        reserve_output_tokens=reserve_output_tokens,
        input_price_per_1m=input_price_per_1m,
        output_price_per_1m=output_price_per_1m,
        runtime=runtime,
    ):
        yield event


async def continue_chat_loop(
    session: AsyncSession,
    *,
    agent: AgentOut,
    session_id: str,
    run_id: str,
    model_profile_id: Optional[str] = None,
    llm: Optional[OpenAICompatClient] = None,
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    include_reasoning: Optional[bool] = None,
    context_window_tokens: Optional[int] = None,
    reserve_output_tokens: Optional[int] = None,
    input_price_per_1m: Optional[float] = None,
    output_price_per_1m: Optional[float] = None,
    runtime: Optional[AgentRuntimeConfig] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Continue an existing run (fresh chat after user_message, or after ask_user resume)."""
    runtime = runtime or resolve_runtime(agent)
    if llm is None or model_name is None:
        try:
            (
                llm,
                model_name,
                temperature,
                include_reasoning,
                context_window_tokens,
                reserve_output_tokens,
                resolved_profile_id,
                input_price_per_1m,
                output_price_per_1m,
            ) = await resolve_llm_client(
                session, agent, model_profile_id=model_profile_id
            )
            model_profile_id = resolved_profile_id or model_profile_id
        except Exception as exc:  # noqa: BLE001
            await repo.finalize_run(
                session,
                run_id,
                status="failed",
                finish_reason="error",
                error_message=str(exc),
            )
            yield {"type": "error", "error": str(exc), "run_id": run_id}
            yield {"type": "done", "session_id": session_id, "run_id": run_id, "status": "failed"}
            return

    assert llm is not None
    assert model_name is not None
    assert include_reasoning is not None
    assert context_window_tokens is not None
    assert reserve_output_tokens is not None
    in_price = float(input_price_per_1m or 0.0)
    out_price = float(output_price_per_1m or 0.0)

    session_row = await repo.get_session_row(session, session_id)
    session_user_key = getattr(session_row, "user_key", None) if session_row else None
    session_system_prompt = (
        getattr(session_row, "system_prompt", None) if session_row else None
    )
    from agent_factory_server.runtime.locale import normalize_locale

    session_locale = normalize_locale(
        getattr(session_row, "locale", None) if session_row else None
    )
    client_device_id = await repo.ensure_session_client_device(session, session_id)
    needs_client_tools = any(
        (t.execution == "client") for t in (agent.tools or [])
    ) or has_client_origin_skills(agent.skills)
    effective_tools = merge_effective_tools(
        agent.tools,
        memory_enabled=bool(runtime.memory and runtime.memory.enabled),
        locale=session_locale,
        skills=agent.skills or [],
    )
    tool_map = {t.name: t for t in effective_tools}
    openai_tools = tools_to_openai(effective_tools) if effective_tools else None
    system_prompt = build_system_prompt(
        agent,
        resolved_system_prompt=session_system_prompt,
        locale=session_locale,
    )

    timeout_seconds = float(runtime.run_timeout_seconds or 0.0)
    deadline = (
        time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    )

    # --- Per-run budgets (single user turn) -------------------------------
    # rounds_used and llm_usage rows persist, so ask_user / approval resumes
    # continue the same budget instead of resetting it.
    run_row = await repo.get_run(session, run_id)
    rounds_used = int(getattr(run_row, "rounds_used", 0) or 0) if run_row else 0
    max_rounds = max(0, int(runtime.max_tool_rounds or 0) - rounds_used)
    token_budget = int(runtime.max_run_tokens or 0)
    tokens_used = (
        await repo.sum_run_tokens(session, run_id) if token_budget > 0 else 0
    )

    async def _finish_token_budget() -> AsyncIterator[dict[str, Any]]:
        msg = f"单次对话 token 预算已用完（上限 {token_budget}）"
        await repo.add_item(
            session,
            run_id=run_id,
            session_id=session_id,
            type="system_notice",
            content=f"{msg}，本次运行已停止。可下一条消息继续。",
        )
        await repo.finalize_run(
            session,
            run_id,
            status="limit_reached",
            finish_reason="limit_reached",
            error_message=msg,
        )
        yield {
            "type": "error",
            "error": msg,
            "code": "limit_reached",
            "run_id": run_id,
            "session_id": session_id,
        }
        yield {
            "type": "done",
            "session_id": session_id,
            "run_id": run_id,
            "status": "limit_reached",
        }

    async def _abort_reason() -> Optional[str]:
        if await run_cancel_registry.is_cancelled(run_id):
            return "cancelled"
        if deadline is not None and time.monotonic() >= deadline:
            return "timeout"
        return None

    async def _should_stop() -> bool:
        return (await _abort_reason()) is not None

    async def _finish_abort(reason: str) -> AsyncIterator[dict[str, Any]]:
        if reason == "timeout":
            msg = f"run timed out after {timeout_seconds:g}s"
            await repo.finalize_run(
                session,
                run_id,
                status="failed",
                finish_reason="timeout",
                error_message=msg,
            )
            yield {
                "type": "error",
                "error": msg,
                "code": "timeout",
                "run_id": run_id,
                "session_id": session_id,
            }
            yield {
                "type": "done",
                "session_id": session_id,
                "run_id": run_id,
                "status": "failed",
            }
            return
        await repo.finalize_run(
            session, run_id, status="cancelled", finish_reason="cancelled"
        )
        yield {"type": "cancelled", "run_id": run_id, "session_id": session_id}
        yield {"type": "done", "session_id": session_id, "run_id": run_id, "status": "cancelled"}

    async def _prepare_messages() -> tuple[Optional[list[dict[str, Any]]], list[dict[str, Any]]]:
        memory_block = await load_memory_block(
            session,
            agent_id=agent.id,
            session_id=session_id,
            user_key=session_user_key,
            policy=runtime.memory or MemoryPolicy(),
            locale=session_locale,
        )
        messages, report = await assemble_llm_context(
            session,
            session_id=session_id,
            system_prompt=system_prompt,
            current_run_id=run_id,
            policy=runtime.context_policy,
            context_window_tokens=context_window_tokens,
            reserve_output_tokens=reserve_output_tokens,
            include_reasoning=include_reasoning,
            llm=llm,
            model_name=model_name,
            temperature=temperature,
            memory_block=memory_block,
        )
        # Debug audit: prefix may change across rounds (memory write / summary).
        summary_text = ""
        for msg in messages[1:3]:
            if isinstance(msg, dict) and str(msg.get("content") or "").startswith(
                "# Prior context summary"
            ):
                summary_text = str(msg.get("content") or "")
                break
        await repo.add_item(
            session,
            run_id=run_id,
            session_id=session_id,
            type="prompt_snapshot",
            content=None,
            visibility="debug",
            payload={
                "agent_system_prompt": system_prompt,
                "memory_block": memory_block or "",
                "context_summary": summary_text,
                "message_count": len(messages),
                "estimated_tokens_after": report.estimated_tokens_after,
                "layers_applied": list(report.layers_applied),
            },
        )
        events: list[dict[str, Any]] = []
        if report.estimated_tokens_before >= report.soft or len(report.layers_applied) > 1:
            events.append(report.to_event(session_id=session_id, run_id=run_id))
        if report.overflow:
            msg = (
                f"上下文仍超过预算（估点 {report.estimated_tokens_after} > {report.budget}），"
                "请新开会话或缩短输入"
            )
            await repo.finalize_run(
                session,
                run_id,
                status="failed",
                finish_reason="error",
                error_message=msg,
            )
            events.append(
                {"type": "error", "error": msg, "code": "context_overflow", "run_id": run_id}
            )
            events.append(
                {"type": "done", "session_id": session_id, "run_id": run_id, "status": "failed"}
            )
            return None, events
        return messages, events

    try:
        for _round in range(max_rounds):
            abort = await _abort_reason()
            if abort:
                async for ev in _finish_abort(abort):
                    yield ev
                return

            if token_budget > 0 and tokens_used >= token_budget:
                async for ev in _finish_token_budget():
                    yield ev
                return

            messages, prep_events = await _prepare_messages()
            for ev in prep_events:
                yield ev
            if messages is None:
                return
            await repo.bump_run_rounds(session, run_id)

            message: dict[str, Any] | None = None
            round_usage: dict[str, Any] | None = None
            stream_aborted = False
            try:
                async for event in _stream_model_round(
                    llm,
                    messages=messages,
                    tools=openai_tools,
                    model_name=model_name,
                    temperature=temperature,
                    run_id=run_id,
                    should_cancel=_should_stop,
                ):
                    if event.get("type") == "cancelled":
                        stream_aborted = True
                        break
                    if event.get("type") == "complete":
                        message = event.get("message") or {}
                        usage_raw = event.get("usage")
                        round_usage = usage_raw if isinstance(usage_raw, dict) else None
                    else:
                        yield event
            except Exception as exc:  # noqa: BLE001
                await repo.finalize_run(
                    session,
                    run_id,
                    status="failed",
                    finish_reason="error",
                    error_message=str(exc),
                )
                yield {"type": "error", "error": str(exc), "run_id": run_id}
                yield {"type": "done", "session_id": session_id, "run_id": run_id, "status": "failed"}
                return

            if stream_aborted:
                async for ev in _finish_abort((await _abort_reason()) or "cancelled"):
                    yield ev
                return

            if message is None:
                abort = await _abort_reason()
                if abort:
                    async for ev in _finish_abort(abort):
                        yield ev
                    return
                await repo.finalize_run(
                    session,
                    run_id,
                    status="failed",
                    finish_reason="error",
                    error_message="empty model response",
                )
                yield {"type": "error", "error": "empty model response", "run_id": run_id}
                yield {"type": "done", "session_id": session_id, "run_id": run_id, "status": "failed"}
                return

            tokens_used += await _record_round_usage(
                session,
                session_id=session_id,
                run_id=run_id,
                agent_id=agent.id,
                model_profile_id=model_profile_id,
                model_name=model_name,
                input_price_per_1m=in_price,
                output_price_per_1m=out_price,
                usage=round_usage,
                messages=messages,
                message=message,
            )

            content = message.get("content")
            content_text = None if content is None else str(content)
            tool_calls = message.get("tool_calls") or []
            reasoning_content = message.get("reasoning_content")

            if reasoning_content:
                await repo.add_item(
                    session,
                    run_id=run_id,
                    session_id=session_id,
                    type="reasoning",
                    content=str(reasoning_content),
                )
                yield {
                    "type": "reasoning_done",
                    "run_id": run_id,
                    "content": str(reasoning_content),
                }

            if tool_calls:
                payload: dict[str, Any] = {"tool_calls": tool_calls}
                if reasoning_content:
                    payload["reasoning_content"] = reasoning_content
                await repo.add_item(
                    session,
                    run_id=run_id,
                    session_id=session_id,
                    type="assistant_message",
                    content=content_text,
                    payload=payload,
                )

                paused_for_human = False
                prepared = await _prepare_tool_calls(
                    tool_calls=tool_calls,
                    tool_map=tool_map,
                    session_id=session_id,
                    client_device_id=client_device_id,
                    timeout_seconds=float(
                        getattr(runtime, "tool_timeout_seconds", None) or 15
                    ),
                )
                abort = await _abort_reason()
                if abort:
                    async for ev in _finish_abort(abort):
                        yield ev
                    return

                # Siblings in one assistant message are independent (the model
                # has not seen their results), so external tools may run
                # concurrently. Builtins touch the shared DB session and HITL
                # needs ordering, so those stay sequential.
                run_parallel = (
                    bool(runtime.parallel_tool_calls)
                    and len(prepared) > 1
                    and all(
                        item["tool"] is not None
                        and item["tool"].execution in {"client", "remote_http"}
                        and not item["needs_approval"]
                        for item in prepared
                    )
                )

                if run_parallel:
                    for item in prepared:
                        await repo.add_item(
                            session,
                            run_id=run_id,
                            session_id=session_id,
                            type="tool_call",
                            call_id=item["call_id"],
                            name=item["name"],
                            content=None,
                            payload=_tool_call_item_payload(item),
                        )
                        yield {
                            "type": "tool_call",
                            "run_id": run_id,
                            "call_id": item["call_id"],
                            "name": item["name"],
                            "arguments": item["arguments"],
                        }

                    abort = await _abort_reason()
                    if abort:
                        for item in prepared:
                            cancelled_result = {
                                "ok": False,
                                "error": abort,
                                "cancelled": True,
                            }
                            yield await _persist_tool_result(
                                session,
                                run_id=run_id,
                                session_id=session_id,
                                prep=item,
                                result=cancelled_result,
                            )
                        async for ev in _finish_abort(abort):
                            yield ev
                        return

                    results = await _execute_prepared_tools_parallel(
                        prepared,
                        session=session,
                        agent=agent,
                        session_id=session_id,
                        session_user_key=session_user_key,
                        run_id=run_id,
                        runtime=runtime,
                        client_device_id=client_device_id,
                        needs_client_tools=needs_client_tools,
                    )
                    for item, result in zip(prepared, results):
                        yield await _persist_tool_result(
                            session,
                            run_id=run_id,
                            session_id=session_id,
                            prep=item,
                            result=result,
                        )
                    abort = await _abort_reason()
                    if abort:
                        async for ev in _finish_abort(abort):
                            yield ev
                        return
                    continue

                for call_index, item in enumerate(prepared):
                    abort = await _abort_reason()
                    if abort:
                        async for ev in _finish_abort(abort):
                            yield ev
                        return

                    call_id = item["call_id"]
                    name = item["name"]
                    arguments = item["arguments"]
                    needs_approval = item["needs_approval"]
                    approval_display = item["approval_display"]

                    await repo.add_item(
                        session,
                        run_id=run_id,
                        session_id=session_id,
                        type="tool_call",
                        call_id=call_id,
                        name=name,
                        content=None,
                        payload=_tool_call_item_payload(item),
                    )
                    yield {
                        "type": "tool_call",
                        "run_id": run_id,
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    }

                    if name == ASK_USER_TOOL_NAME:
                        ask = parse_ask_user_arguments(arguments)
                        async for ev in _emit_skipped_siblings(
                            session,
                            run_id=run_id,
                            session_id=session_id,
                            tool_calls=tool_calls,
                            start_index=call_index + 1,
                            skip_reason="skipped_due_to_ask_user",
                        ):
                            yield ev

                        expires = waiting_expires_at()
                        await repo.pause_run_waiting_human(
                            session, run_id, waiting_expires_at=expires
                        )
                        yield {
                            "type": "human_input_required",
                            "run_id": run_id,
                            "session_id": session_id,
                            "call_id": call_id,
                            "question": ask["question"],
                            "options": ask["options"],
                            "allow_free_text": ask["allow_free_text"],
                            "expires_at": expires.isoformat(),
                        }
                        yield {
                            "type": "done",
                            "session_id": session_id,
                            "run_id": run_id,
                            "status": "waiting_human",
                        }
                        paused_for_human = True
                        break

                    if needs_approval:
                        async for ev in _emit_skipped_siblings(
                            session,
                            run_id=run_id,
                            session_id=session_id,
                            tool_calls=tool_calls,
                            start_index=call_index + 1,
                            skip_reason="skipped_due_to_approval",
                        ):
                            yield ev

                        expires = waiting_expires_at()
                        await repo.pause_run_waiting_human(
                            session, run_id, waiting_expires_at=expires
                        )
                        yield {
                            "type": "tool_approval_required",
                            "run_id": run_id,
                            "session_id": session_id,
                            "call_id": call_id,
                            "name": name,
                            "arguments": arguments,
                            "reason": approval_display.get("reason"),
                            "tool_description": approval_display.get("tool_description"),
                            "expires_at": expires.isoformat(),
                        }
                        yield {
                            "type": "done",
                            "session_id": session_id,
                            "run_id": run_id,
                            "status": "waiting_human",
                        }
                        paused_for_human = True
                        break

                    result = await _execute_prepared_tool(
                        item,
                        session=session,
                        agent=agent,
                        session_id=session_id,
                        session_user_key=session_user_key,
                        run_id=run_id,
                        runtime=runtime,
                        client_device_id=client_device_id,
                        needs_client_tools=needs_client_tools,
                    )
                    yield await _persist_tool_result(
                        session,
                        run_id=run_id,
                        session_id=session_id,
                        prep=item,
                        result=result,
                    )
                if paused_for_human:
                    return
                continue

            final_text = content_text or ""
            payload = {}
            if reasoning_content:
                payload["reasoning_content"] = reasoning_content
            await repo.add_item(
                session,
                run_id=run_id,
                session_id=session_id,
                type="assistant_message",
                content=final_text,
                payload=payload or None,
            )
            yield {
                "type": "message",
                "role": "assistant",
                "content": final_text,
                "run_id": run_id,
            }
            yield {
                "type": "message_done",
                "role": "assistant",
                "content": final_text,
                "run_id": run_id,
            }
            await repo.finalize_run(
                session, run_id, status="completed", finish_reason="completed"
            )
            yield {
                "type": "done",
                "session_id": session_id,
                "run_id": run_id,
                "status": "completed",
            }
            return

        # Max rounds
        if runtime.on_max_rounds == "summarize":
            # Do not spend another round just to summarize when the token budget
            # is already gone.
            if token_budget > 0 and tokens_used >= token_budget:
                async for ev in _finish_token_budget():
                    yield ev
                return
            messages, prep_events = await _prepare_messages()
            for ev in prep_events:
                yield ev
            if messages is None:
                return
            await repo.bump_run_rounds(session, run_id)
            message = None
            round_usage: dict[str, Any] | None = None
            stream_aborted = False
            try:
                async for event in _stream_model_round(
                    llm,
                    messages=messages,
                    tools=None,
                    model_name=model_name,
                    temperature=temperature,
                    run_id=run_id,
                    should_cancel=_should_stop,
                ):
                    if event.get("type") == "cancelled":
                        stream_aborted = True
                        break
                    if event.get("type") == "complete":
                        message = event.get("message") or {}
                        usage_raw = event.get("usage")
                        round_usage = usage_raw if isinstance(usage_raw, dict) else None
                    else:
                        yield event
            except Exception as exc:  # noqa: BLE001
                await repo.finalize_run(
                    session,
                    run_id,
                    status="limit_reached",
                    finish_reason="limit_reached",
                    error_message=str(exc),
                )
                yield {"type": "error", "error": str(exc), "run_id": run_id}
                yield {
                    "type": "done",
                    "session_id": session_id,
                    "run_id": run_id,
                    "status": "limit_reached",
                }
                return

            if stream_aborted:
                async for ev in _finish_abort((await _abort_reason()) or "cancelled"):
                    yield ev
                return

            if message is not None:
                await _record_round_usage(
                    session,
                    session_id=session_id,
                    run_id=run_id,
                    agent_id=agent.id,
                    model_profile_id=model_profile_id,
                    model_name=model_name,
                    input_price_per_1m=in_price,
                    output_price_per_1m=out_price,
                    usage=round_usage,
                    messages=messages,
                    message=message,
                )

            final_text = str((message or {}).get("content") or "")
            await repo.add_item(
                session,
                run_id=run_id,
                session_id=session_id,
                type="system_notice",
                content="已达到最大工具轮次，以下为收尾总结。",
            )
            await repo.add_item(
                session,
                run_id=run_id,
                session_id=session_id,
                type="assistant_message",
                content=final_text,
                payload={"limit_reached": True},
            )
            yield {
                "type": "message",
                "role": "assistant",
                "content": final_text,
                "run_id": run_id,
            }
            yield {
                "type": "message_done",
                "role": "assistant",
                "content": final_text,
                "run_id": run_id,
            }
            await repo.finalize_run(
                session, run_id, status="limit_reached", finish_reason="limit_reached"
            )
            yield {
                "type": "done",
                "session_id": session_id,
                "run_id": run_id,
                "status": "limit_reached",
            }
            return

        await repo.add_item(
            session,
            run_id=run_id,
            session_id=session_id,
            type="system_notice",
            content="已达到最大工具轮次。",
        )
        await repo.finalize_run(
            session,
            run_id,
            status="limit_reached",
            finish_reason="limit_reached",
            error_message="max tool rounds exceeded",
        )
        yield {
            "type": "error",
            "error": "max tool rounds exceeded",
            "run_id": run_id,
            "code": "limit_reached",
        }
        yield {
            "type": "done",
            "session_id": session_id,
            "run_id": run_id,
            "status": "limit_reached",
        }
    finally:
        # Keep cancel flag if still waiting_human so a later cancel still works;
        # clear only when the run is no longer parked for human input.
        row = await repo.get_run(session, run_id)
        if not row or row.status != "waiting_human":
            await run_cancel_registry.clear(run_id)


async def resume_after_ask_user(
    session: AsyncSession,
    *,
    agent: AgentOut,
    run_id: str,
    call_id: str,
    answer: Optional[str] = None,
    selected_option: Optional[str] = None,
    model_profile_id: Optional[str] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Write ask_user tool_result and continue the same run."""
    await repo.expire_waiting_human_runs(session)
    run = await repo.get_run(session, run_id)
    if not run:
        yield {"type": "error", "error": "Run not found", "code": "not_found", "run_id": run_id}
        return
    session_id = run.session_id
    if run.status == "cancelled" and (run.finish_reason or "") == "timeout":
        yield {
            "type": "error",
            "error": "等待用户回答超时",
            "code": "timeout",
            "run_id": run_id,
        }
        yield {
            "type": "done",
            "session_id": session_id,
            "run_id": run_id,
            "status": "cancelled",
        }
        return
    if run.status != "waiting_human":
        yield {
            "type": "error",
            "error": f"run is not waiting_human (status={run.status})",
            "code": "invalid_status",
            "run_id": run_id,
        }
        yield {"type": "done", "session_id": session_id, "run_id": run_id, "status": run.status}
        return

    pending = await repo.find_pending_ask_user_call(session, run_id, call_id=call_id)
    if not pending:
        yield {
            "type": "error",
            "error": "pending ask_user call not found",
            "code": "call_mismatch",
            "run_id": run_id,
        }
        yield {
            "type": "done",
            "session_id": session_id,
            "run_id": run_id,
            "status": "waiting_human",
        }
        return

    text = (answer or "").strip()
    option = (selected_option or "").strip() or None
    if not text and option:
        text = option
    if not text:
        yield {
            "type": "error",
            "error": "answer or selected_option is required",
            "code": "empty_answer",
            "run_id": run_id,
        }
        return

    result = {
        "ok": True,
        "answer": text,
        "selected_option": option,
    }
    result_text = json.dumps(result, ensure_ascii=False)
    await repo.add_item(
        session,
        run_id=run_id,
        session_id=session_id,
        type="tool_result",
        call_id=call_id,
        name=ASK_USER_TOOL_NAME,
        content=result_text,
        payload={"ok": True, "original_chars": len(result_text)},
    )
    await repo.resume_run_to_running(session, run_id)
    await run_cancel_registry.clear(run_id)

    yield {
        "type": "run_start",
        "run_id": run_id,
        "session_id": session_id,
        "status": "running",
    }
    yield {
        "type": "tool_result",
        "run_id": run_id,
        "call_id": call_id,
        "name": ASK_USER_TOOL_NAME,
        "result": result,
    }

    async for event in continue_chat_loop(
        session,
        agent=agent,
        session_id=session_id,
        run_id=run_id,
        model_profile_id=model_profile_id,
    ):
        yield event


async def resume_after_approval(
    session: AsyncSession,
    *,
    agent: AgentOut,
    run_id: str,
    call_id: str,
    decision: ApprovalDecision,
    edited_arguments: Optional[dict[str, Any]] = None,
    model_profile_id: Optional[str] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Approve/edit/reject a pending require_approval tool_call and continue the run."""
    await repo.expire_waiting_human_runs(session)
    run = await repo.get_run(session, run_id)
    if not run:
        yield {"type": "error", "error": "Run not found", "code": "not_found", "run_id": run_id}
        return
    session_id = run.session_id
    if run.status == "cancelled" and (run.finish_reason or "") == "timeout":
        yield {
            "type": "error",
            "error": "等待审批超时",
            "code": "timeout",
            "run_id": run_id,
        }
        yield {
            "type": "done",
            "session_id": session_id,
            "run_id": run_id,
            "status": "cancelled",
        }
        return
    if run.status != "waiting_human":
        yield {
            "type": "error",
            "error": f"run is not waiting_human (status={run.status})",
            "code": "invalid_status",
            "run_id": run_id,
        }
        yield {"type": "done", "session_id": session_id, "run_id": run_id, "status": run.status}
        return

    pending = await repo.find_pending_approval_call(session, run_id, call_id=call_id)
    if not pending:
        yield {
            "type": "error",
            "error": "pending approval call not found",
            "code": "call_mismatch",
            "run_id": run_id,
        }
        yield {
            "type": "done",
            "session_id": session_id,
            "run_id": run_id,
            "status": "waiting_human",
        }
        return

    name = pending.name or "tool"
    payload = parse_tool_call_payload(pending.payload_json)
    original_args = payload.get("arguments")
    if not isinstance(original_args, dict):
        original_args = {}

    runtime = resolve_runtime(agent)
    session_row = await repo.get_session_row(session, session_id)
    session_user_key = getattr(session_row, "user_key", None) if session_row else None
    from agent_factory_server.runtime.locale import normalize_locale

    session_locale = normalize_locale(
        getattr(session_row, "locale", None) if session_row else None
    )
    client_device_id = await repo.ensure_session_client_device(session, session_id)
    needs_client_tools = any(
        (t.execution == "client") for t in (agent.tools or [])
    ) or has_client_origin_skills(agent.skills)
    effective_tools = merge_effective_tools(
        agent.tools,
        memory_enabled=bool(runtime.memory and runtime.memory.enabled),
        locale=session_locale,
        skills=agent.skills or [],
    )
    tool_map = {t.name: t for t in effective_tools}
    tool = tool_map.get(name)

    if decision == "reject":
        result = approval_reject_result()
    else:
        if decision == "edit":
            if not isinstance(edited_arguments, dict):
                yield {
                    "type": "error",
                    "error": "edited_arguments object is required for edit",
                    "code": "invalid_edit",
                    "run_id": run_id,
                }
                return
            exec_args = edited_arguments
        else:
            exec_args = original_args

        if not tool:
            result = {"ok": False, "error": f"unknown tool: {name}"}
        else:
            result = await _execute_mapped_tool(
                session,
                tool=tool,
                name=name,
                arguments=exec_args,
                agent=agent,
                session_id=session_id,
                session_user_key=session_user_key,
                run_id=run_id,
                runtime=runtime,
                client_device_id=client_device_id,
                needs_client_tools=needs_client_tools,
                call_id=call_id,
            )
            if decision == "edit":
                result = {
                    **result,
                    "edited": True,
                    "arguments": exec_args,
                }

    result_text = json.dumps(result, ensure_ascii=False)
    await repo.add_item(
        session,
        run_id=run_id,
        session_id=session_id,
        type="tool_result",
        call_id=call_id,
        name=name,
        content=result_text,
        payload={
            "ok": result.get("ok", True),
            "original_chars": len(result_text),
            "decision": decision,
        },
    )
    await repo.resume_run_to_running(session, run_id)
    await run_cancel_registry.clear(run_id)

    yield {
        "type": "run_start",
        "run_id": run_id,
        "session_id": session_id,
        "status": "running",
    }
    yield {
        "type": "tool_result",
        "run_id": run_id,
        "call_id": call_id,
        "name": name,
        "result": result,
    }

    async for event in continue_chat_loop(
        session,
        agent=agent,
        session_id=session_id,
        run_id=run_id,
        model_profile_id=model_profile_id,
    ):
        yield event
