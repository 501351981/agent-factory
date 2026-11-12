"""Human-in-the-loop: ask_user + tool approval helpers."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Literal, Optional

from agent_factory_server.db.models import utcnow
from agent_factory_server.domain.schemas import ToolDef

ASK_USER_TOOL_NAME = "ask_user"
HITL_KIND_ASK = "ask"
HITL_KIND_APPROVAL = "approval"
DEFAULT_WAITING_TTL = timedelta(hours=24)

ApprovalDecision = Literal["approve", "edit", "reject"]


def ask_user_tool(*, locale: str = "zh") -> ToolDef:
    descriptions = {
        "zh": (
            "向用户提问以获取澄清信息后再继续。"
            "需要用户选择或补充关键信息时使用；不要用于可自行判断的琐碎决定。"
        ),
        "en": (
            "Ask the user a clarifying question before continuing. "
            "Use when you need a choice or information only the user can provide."
        ),
        "ja": (
            "続行前にユーザーへ確認質問をする。"
            "ユーザーにしか分からない選択や情報が必要なときに使う。"
        ),
    }
    loc = (locale or "zh").lower()
    desc = descriptions.get(loc, descriptions["zh"])
    return ToolDef(
        name=ASK_USER_TOOL_NAME,
        description=desc,
        execution="builtin",
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question to show the user"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional multiple-choice options",
                },
                "allow_free_text": {
                    "type": "boolean",
                    "description": "Whether free-text answer is allowed (default true)",
                },
            },
            "required": ["question"],
        },
    )


def parse_ask_user_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    question = str(args.get("question") or "").strip() or "请提供更多信息以继续。"
    raw_options = args.get("options")
    options: list[str] = []
    if isinstance(raw_options, list):
        options = [str(x).strip() for x in raw_options if str(x).strip()]
    allow_free = args.get("allow_free_text")
    allow_free_text = True if allow_free is None else bool(allow_free)
    if not options and not allow_free_text:
        allow_free_text = True
    return {
        "question": question,
        "options": options,
        "allow_free_text": allow_free_text,
    }


def waiting_expires_at(*, ttl: Optional[timedelta] = None):
    return utcnow() + (ttl or DEFAULT_WAITING_TTL)


def tool_requires_approval(tool: ToolDef | None) -> bool:
    if tool is None:
        return False
    if tool.execution == "builtin":
        return False
    return bool(getattr(tool, "require_approval", False))


def parse_tool_call_payload(payload_json: str | None) -> dict[str, Any]:
    if not payload_json:
        return {}
    try:
        data = json.loads(payload_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def hitl_kind_from_item(*, name: str | None, payload_json: str | None) -> Optional[str]:
    if (name or "") == ASK_USER_TOOL_NAME:
        return HITL_KIND_ASK
    payload = parse_tool_call_payload(payload_json)
    if payload.get("hitl") == HITL_KIND_APPROVAL:
        return HITL_KIND_APPROVAL
    return None


def normalize_approval_decision(raw: str | None) -> Optional[ApprovalDecision]:
    value = (raw or "").strip().lower()
    if value in {"approve", "edit", "reject"}:
        return value  # type: ignore[return-value]
    return None


def approval_reject_result(*, reason: str = "用户拒绝执行该工具") -> dict[str, Any]:
    return {
        "ok": False,
        "rejected": True,
        "error": reason,
    }
