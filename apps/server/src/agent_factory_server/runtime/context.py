from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.db.models import ItemRecord, RunRecord
from agent_factory_server.domain.schemas import ContextPolicy
from agent_factory_server.llm.openai_compat import OpenAICompatClient

logger = logging.getLogger(__name__)


@dataclass
class CompactReport:
    estimated_tokens_before: int
    estimated_tokens_after: int
    budget: int
    soft: int
    hard: int
    layers_applied: list[str] = field(default_factory=list)
    covered_run_ids: list[str] = field(default_factory=list)
    covered_until_run_id: Optional[str] = None
    summarize_failed: bool = False
    overflow: bool = False

    def to_event(self, *, session_id: str, run_id: Optional[str] = None) -> dict[str, Any]:
        return {
            "type": "context_compacted",
            "session_id": session_id,
            "run_id": run_id,
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "budget": self.budget,
            "soft": self.soft,
            "hard": self.hard,
            "layers": list(self.layers_applied),
            "covered_run_ids": list(self.covered_run_ids),
            "covered_until_run_id": self.covered_until_run_id,
            "summarize_failed": self.summarize_failed,
            "overflow": self.overflow,
        }

    def to_out_dict(self) -> dict[str, Any]:
        return {
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "budget": self.budget,
            "soft": self.soft,
            "hard": self.hard,
            "layers_applied": list(self.layers_applied),
            "covered_run_ids": list(self.covered_run_ids),
            "covered_until_run_id": self.covered_until_run_id,
            "summarize_failed": self.summarize_failed,
            "overflow": self.overflow,
        }


def resolve_context_policy(runtime_tool_max: int, policy: Optional[ContextPolicy]) -> ContextPolicy:
    base = policy or ContextPolicy()
    # Prefer nested policy; fall back to legacy top-level tool_result_max_chars.
    if policy is None or (
        policy.tool_result_max_chars == 12000 and runtime_tool_max != 12000
    ):
        return base.model_copy(update={"tool_result_max_chars": runtime_tool_max})
    return base


def estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    cpt = chars_per_token if chars_per_token > 0 else 4.0
    return max(0, math.ceil(len(text or "") / cpt))


def estimate_messages_tokens(messages: list[dict[str, Any]], chars_per_token: float = 4.0) -> int:
    total = 0
    for msg in messages:
        total += estimate_tokens(json.dumps(msg, ensure_ascii=False), chars_per_token)
    return total


def _loads_obj(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _tool_call_ids(tool_calls: Any) -> list[str]:
    if not isinstance(tool_calls, list):
        return []
    ids: list[str] = []
    for call in tool_calls:
        if isinstance(call, dict) and call.get("id"):
            ids.append(str(call["id"]))
    return ids


def _append_missing_tool_results(messages: list[dict[str, Any]], pending_ids: list[str]) -> None:
    for call_id in pending_ids:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps({"ok": False, "error": "missing tool result"}, ensure_ascii=False),
            }
        )


def truncate_tool_content(content: str, max_chars: int) -> tuple[str, bool]:
    return repo.truncate_tool_content(content, max_chars)


def project_run_items(
    items: list[ItemRecord],
    *,
    include_reasoning: bool = False,
    tool_result_max_chars: int = 12000,
    fold_tools: bool = False,
) -> list[dict[str, Any]]:
    """Project one run's items into OpenAI messages (no system)."""
    messages: list[dict[str, Any]] = []
    pending_ids: list[str] = []
    covered_call_ids: set[str] = set()

    for item in items:
        if item.type in ("context_summary", "system_notice", "reasoning", "prompt_snapshot"):
            continue

        if item.type == "user_message":
            if pending_ids:
                _append_missing_tool_results(messages, pending_ids)
                pending_ids = []
            messages.append({"role": "user", "content": item.content or ""})
            continue

        if item.type == "assistant_message":
            if pending_ids:
                _append_missing_tool_results(messages, pending_ids)
                pending_ids = []

            payload = _loads_obj(item.payload_json)
            tool_calls = payload.get("tool_calls")
            if fold_tools and isinstance(tool_calls, list) and tool_calls:
                # Keep assistant text if any; fold tool calls into a short notice.
                names = []
                for call in tool_calls:
                    if isinstance(call, dict):
                        fn = call.get("function") or {}
                        names.append(str(fn.get("name") or "tool"))
                note = f"[compacted] tools called: {', '.join(names) or 'tool'}"
                content = item.content or note
                if item.content:
                    content = f"{item.content}\n{note}"
                messages.append({"role": "assistant", "content": content})
                covered_call_ids.update(_tool_call_ids(tool_calls))
                pending_ids = []
                continue

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
            if fold_tools:
                continue
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
                covered_call_ids.add(cid)
                continue
            messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
            pending_ids = [cid]
            covered_call_ids.add(cid)
            continue

        if item.type == "tool_result":
            if fold_tools:
                # Already folded at assistant_message; skip raw results.
                call_id = item.call_id or ""
                if call_id in pending_ids:
                    pending_ids = [x for x in pending_ids if x != call_id]
                continue
            call_id = item.call_id or ""
            content = item.content or ""
            truncated, was_truncated = truncate_tool_content(content, tool_result_max_chars)
            if was_truncated:
                # Annotate for model honesty; DB still has full content.
                pass
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": truncated,
                }
            )
            if call_id in pending_ids:
                pending_ids = [x for x in pending_ids if x != call_id]
            continue

    if pending_ids:
        _append_missing_tool_results(messages, pending_ids)
    return messages


def _summary_message(content: str) -> dict[str, Any]:
    return {
        "role": "system",
        "content": (
            "# Prior context summary\n"
            "The following summarizes earlier turns in this session. "
            "Details remain available in the product UI; do not invent tool results.\n\n"
            f"{content.strip()}"
        ),
    }


def _memory_message(content: str) -> dict[str, Any]:
    return {"role": "system", "content": content.strip()}


def _build_messages(
    *,
    system_prompt: str,
    memory_block: Optional[str],
    summary_text: Optional[str],
    runs: list[RunRecord],
    items_by_run: dict[str, list[ItemRecord]],
    include_run_ids: list[str],
    fold_run_ids: set[str],
    include_reasoning: bool,
    tool_result_max_chars: int,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if memory_block and memory_block.strip():
        messages.append(_memory_message(memory_block))
    if summary_text:
        messages.append(_summary_message(summary_text))
    for run in runs:
        if run.id not in include_run_ids:
            continue
        msgs = project_run_items(
            items_by_run.get(run.id, []),
            include_reasoning=include_reasoning,
            tool_result_max_chars=tool_result_max_chars,
            fold_tools=run.id in fold_run_ids,
        )
        messages.extend(msgs)
    return messages


def _run_digest_text(run: RunRecord, items: list[ItemRecord], max_chars: int) -> str:
    parts: list[str] = [f"## Run {run.id}\nUser: {run.user_input}"]
    for item in items:
        if item.type == "assistant_message" and item.content:
            payload = _loads_obj(item.payload_json)
            has_tools = bool(payload.get("tool_calls"))
            if not has_tools:
                parts.append(f"Assistant: {item.content}")
        elif item.type == "tool_call":
            parts.append(f"Tool call: {item.name or 'tool'}")
        elif item.type == "tool_result":
            payload = _loads_obj(item.payload_json)
            ok = payload.get("ok", True)
            parts.append(f"Tool result ({item.name or 'tool'}): ok={ok}")
    text = "\n".join(parts)
    if len(text) > max_chars:
        return text[: max_chars // 2] + "\n...\n" + text[-max_chars // 2 :]
    return text


def _mechanical_summary(
    *,
    old_summary: Optional[str],
    runs: list[RunRecord],
    items_by_run: dict[str, list[ItemRecord]],
    max_chars: int,
) -> str:
    chunks: list[str] = []
    if old_summary:
        chunks.append(old_summary.strip())
    budget = max_chars
    for run in runs:
        piece = _run_digest_text(run, items_by_run.get(run.id, []), max(800, budget // max(len(runs), 1)))
        chunks.append(piece)
        budget -= len(piece)
        if budget <= 0:
            break
    text = "\n\n".join(chunks)
    if len(text) > max_chars:
        return text[:max_chars]
    return text


async def _llm_summary(
    llm: OpenAICompatClient,
    *,
    model_name: str,
    temperature: Optional[float],
    old_summary: Optional[str],
    digest: str,
) -> str:
    prompt = [
        {
            "role": "system",
            "content": (
                "You compress earlier chat turns into a durable session summary. "
                "Keep goals, constraints, decisions, open questions, and key facts. "
                "Do not invent tool outputs. Write in the same language as the content. "
                "Be concise."
            ),
        },
        {
            "role": "user",
            "content": (
                (f"Existing summary:\n{old_summary}\n\n" if old_summary else "")
                + f"New turns to merge:\n{digest}\n\n"
                "Return the updated summary only."
            ),
        },
    ]
    data = await llm.chat(messages=prompt, tools=None, model=model_name, temperature=temperature)
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    return str(content).strip() or digest


async def assemble_llm_context(
    session: AsyncSession,
    *,
    session_id: str,
    system_prompt: str,
    current_run_id: Optional[str],
    policy: ContextPolicy,
    context_window_tokens: int,
    reserve_output_tokens: int,
    include_reasoning: bool = False,
    llm: Optional[OpenAICompatClient] = None,
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    memory_block: Optional[str] = None,
) -> tuple[list[dict[str, Any]], CompactReport]:
    runs = await repo.list_runs_for_session(session, session_id)
    items = await repo.list_items_for_session(session, session_id)
    items_by_run: dict[str, list[ItemRecord]] = {}
    for item in items:
        if item.type == "context_summary":
            continue
        items_by_run.setdefault(item.run_id, []).append(item)

    latest_summary = await repo.get_latest_context_summary(session, session_id)
    summary_payload = _loads_obj(latest_summary.payload_json) if latest_summary else {}
    covered: set[str] = set(summary_payload.get("covered_run_ids") or [])
    summary_text: Optional[str] = latest_summary.content if latest_summary else None
    covered_until = summary_payload.get("covered_until_run_id")
    if isinstance(covered_until, str):
        pass
    else:
        covered_until = None

    memory_text = (memory_block or "").strip() or None
    fixed_prefix_tokens = estimate_tokens(system_prompt, policy.chars_per_token) + (
        estimate_tokens(memory_text, policy.chars_per_token) if memory_text else 0
    )
    budget = max(
        1024,
        int(context_window_tokens) - int(reserve_output_tokens) - fixed_prefix_tokens,
    )
    soft = int(budget * policy.soft_ratio)
    hard = int(budget * policy.hard_ratio)
    keep_n = max(1, int(policy.keep_last_runs))

    # Baseline: summary + all non-covered runs, L1 only.
    include_ids = [r.id for r in runs if r.id not in covered]
    fold_ids: set[str] = set()
    messages = _build_messages(
        system_prompt=system_prompt,
        memory_block=memory_text,
        summary_text=summary_text,
        runs=runs,
        items_by_run=items_by_run,
        include_run_ids=include_ids,
        fold_run_ids=fold_ids,
        include_reasoning=include_reasoning,
        tool_result_max_chars=policy.tool_result_max_chars,
    )
    before = estimate_messages_tokens(messages, policy.chars_per_token)
    layers: list[str] = ["L1"]

    report = CompactReport(
        estimated_tokens_before=before,
        estimated_tokens_after=before,
        budget=budget,
        soft=soft,
        hard=hard,
        layers_applied=layers,
        covered_run_ids=sorted(covered),
        covered_until_run_id=covered_until,
    )

    if policy.mode == "off":
        report.overflow = before > budget
        return messages, report

    if before < soft:
        return messages, report

    # L2: fold tools on older runs outside keep window / current.
    layers.append("L2")
    recent = include_ids[-keep_n:]
    recent_set = set(recent)
    if current_run_id:
        recent_set.add(current_run_id)
    fold_ids = {rid for rid in include_ids if rid not in recent_set}
    # Never fold current run.
    if current_run_id:
        fold_ids.discard(current_run_id)

    messages = _build_messages(
        system_prompt=system_prompt,
        memory_block=memory_text,
        summary_text=summary_text,
        runs=runs,
        items_by_run=items_by_run,
        include_run_ids=include_ids,
        fold_run_ids=fold_ids,
        include_reasoning=include_reasoning,
        tool_result_max_chars=policy.tool_result_max_chars,
    )
    after_l2 = estimate_messages_tokens(messages, policy.chars_per_token)
    report.estimated_tokens_after = after_l2
    report.layers_applied = list(layers)

    # soft..hard: stop after L1/L2 to keep summary prefix stable for cache.
    if after_l2 < hard:
        report.overflow = after_l2 > budget
        return messages, report

    # L3: keep only recent window (+ current).
    layers.append("L3")
    window_ids = [rid for rid in include_ids if rid in recent_set]
    # Preserve order from runs
    window_ids = [r.id for r in runs if r.id in set(window_ids)]
    outside_ids = [r.id for r in runs if r.id in include_ids and r.id not in set(window_ids)]

    messages = _build_messages(
        system_prompt=system_prompt,
        memory_block=memory_text,
        summary_text=summary_text,
        runs=runs,
        items_by_run=items_by_run,
        include_run_ids=window_ids,
        fold_run_ids=set(),  # recent stays detailed; L1 still applies
        include_reasoning=include_reasoning,
        tool_result_max_chars=policy.tool_result_max_chars,
    )
    after_l3 = estimate_messages_tokens(messages, policy.chars_per_token)
    report.estimated_tokens_after = after_l3
    report.layers_applied = list(layers)

    # If nothing new outside window, or already covered conceptually, maybe skip L4.
    uncovered_outside = [rid for rid in outside_ids if rid not in covered]
    if not uncovered_outside and after_l3 <= budget:
        return messages, report

    if after_l3 < soft and not uncovered_outside:
        return messages, report

    # L4: summarize uncovered outside runs (incremental).
    if uncovered_outside and policy.summarize_enabled:
        layers.append("L4")
        to_cover_runs = [r for r in runs if r.id in set(uncovered_outside)]
        digest = _mechanical_summary(
            old_summary=None,
            runs=to_cover_runs,
            items_by_run=items_by_run,
            max_chars=policy.summarize_max_input_chars,
        )
        new_summary = digest
        summarize_failed = False
        if llm is not None and model_name:
            try:
                new_summary = await _llm_summary(
                    llm,
                    model_name=model_name,
                    temperature=temperature,
                    old_summary=summary_text,
                    digest=digest,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("context summary LLM failed: %s", exc)
                summarize_failed = True
                new_summary = _mechanical_summary(
                    old_summary=summary_text,
                    runs=to_cover_runs,
                    items_by_run=items_by_run,
                    max_chars=policy.summarize_max_input_chars,
                )
        else:
            new_summary = _mechanical_summary(
                old_summary=summary_text,
                runs=to_cover_runs,
                items_by_run=items_by_run,
                max_chars=policy.summarize_max_input_chars,
            )

        new_covered = sorted(set(covered) | set(uncovered_outside))
        new_until = uncovered_outside[-1]
        payload = {
            "covered_run_ids": new_covered,
            "covered_until_run_id": new_until,
            "created_from_summary_id": latest_summary.id if latest_summary else None,
            "token_estimate_before": before,
            "layer": "L4",
        }
        # Attach summary item to current run when possible; else first covered run.
        anchor_run = current_run_id or (new_covered[-1] if new_covered else None)
        if anchor_run:
            stored = await repo.add_item(
                session,
                run_id=anchor_run,
                session_id=session_id,
                type="context_summary",
                content=new_summary,
                payload=payload,
                visibility="debug",
            )
            payload["token_estimate_after"] = after_l3
            # update payload with after after rebuild — fine as-is
            _ = stored

        summary_text = new_summary
        covered = set(new_covered)
        covered_until = new_until
        report.summarize_failed = summarize_failed
        report.covered_run_ids = new_covered
        report.covered_until_run_id = covered_until

        messages = _build_messages(
            system_prompt=system_prompt,
            memory_block=memory_text,
            summary_text=summary_text,
            runs=runs,
            items_by_run=items_by_run,
            include_run_ids=window_ids,
            fold_run_ids=set(),
            include_reasoning=include_reasoning,
            tool_result_max_chars=policy.tool_result_max_chars,
        )
        after_l4 = estimate_messages_tokens(messages, policy.chars_per_token)
        report.estimated_tokens_after = after_l4
        report.layers_applied = list(layers)
    elif uncovered_outside and not policy.summarize_enabled:
        # Drop outside without summary — last resort mechanical.
        layers.append("L3_drop")
        report.layers_applied = list(layers)

    report.overflow = report.estimated_tokens_after > budget
    return messages, report
