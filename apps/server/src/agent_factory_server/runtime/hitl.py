"""Human-in-the-loop: ask_user + tool approval helpers."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any, Literal, Optional

from agent_factory_server.db.models import utcnow
from agent_factory_server.domain.schemas import ApprovalPolicy, ToolDef

ASK_USER_TOOL_NAME = "ask_user"
HITL_KIND_ASK = "ask"
HITL_KIND_APPROVAL = "approval"
DEFAULT_WAITING_TTL = timedelta(hours=24)

ApprovalDecision = Literal["approve", "edit", "reject"]
ApprovalGateDecision = Literal["require", "skip", "ask_client"]


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
    """Master switch only (ignores argument policy). Prefer ``tool_call_needs_approval``."""
    if tool is None:
        return False
    if tool.execution == "builtin":
        return False
    return bool(getattr(tool, "require_approval", False))


def _dig_arg(arguments: dict[str, Any], path: str) -> Any:
    cur: Any = arguments
    for part in str(path or "").split("."):
        key = part.strip()
        if not key:
            return None
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _match_value(actual: Any, matcher: Any) -> bool:
    if isinstance(matcher, dict):
        if "regex" in matcher:
            pattern = str(matcher.get("regex") or "")
            if not pattern:
                return False
            flags = 0
            raw_flags = str(matcher.get("flags") or "")
            if "i" in raw_flags.lower():
                flags |= re.IGNORECASE
            try:
                return re.search(pattern, "" if actual is None else str(actual), flags) is not None
            except re.error:
                return False
        if "contains" in matcher:
            needle = str(matcher.get("contains") or "")
            hay = "" if actual is None else str(actual)
            return bool(needle) and needle in hay
        if "eq" in matcher:
            return actual == matcher.get("eq")
        # Unknown dict shape → no match (avoid accidental allow).
        return False
    return actual == matcher


def rule_matches_arguments(rule: dict[str, Any], arguments: dict[str, Any] | None) -> bool:
    """AND across all path→matcher entries in one rule."""
    if not isinstance(rule, dict) or not rule:
        return False
    args = arguments if isinstance(arguments, dict) else {}
    for path, matcher in rule.items():
        actual = _dig_arg(args, str(path))
        if not _match_value(actual, matcher):
            return False
    return True


def evaluate_approval_policy(
    policy: ApprovalPolicy | dict[str, Any] | None,
    arguments: dict[str, Any] | None,
) -> ApprovalGateDecision:
    """Resolve static policy to require / skip / ask_client."""
    if policy is None:
        return "require"
    if isinstance(policy, dict):
        try:
            policy = ApprovalPolicy.model_validate(policy)
        except Exception:
            return "require"
    assert isinstance(policy, ApprovalPolicy)

    for rule in policy.always_require or []:
        if isinstance(rule, dict) and rule_matches_arguments(rule, arguments):
            return "require"
    for rule in policy.allow or []:
        if isinstance(rule, dict) and rule_matches_arguments(rule, arguments):
            return "skip"
    default = policy.default or "require"
    if default == "skip":
        return "skip"
    if default == "ask_client":
        return "ask_client"
    return "require"


def resolve_approval_decision(
    tool: ToolDef | None, arguments: dict[str, Any] | None = None
) -> ApprovalGateDecision:
    """Static gate only (no Bridge round-trip)."""
    if not tool_requires_approval(tool):
        return "skip"
    assert tool is not None
    return evaluate_approval_policy(getattr(tool, "approval_policy", None), arguments)


def tool_call_needs_approval(
    tool: ToolDef | None, arguments: dict[str, Any] | None = None
) -> bool:
    """Sync helper: True only when static decision is ``require``.

    ``ask_client`` is False here — callers that support Bridge must resolve it
    via ``resolve_approval_decision`` + ``ask_client_approval_gate``.
    """
    return resolve_approval_decision(tool, arguments) == "require"


def parse_client_approval_check_result(payload: dict[str, Any] | None) -> ApprovalGateDecision:
    """Map Bridge approval_check_result → require|skip. Fail closed → require."""
    if not isinstance(payload, dict):
        return "require"
    if payload.get("ok") is False:
        return "require"
    if "require" in payload:
        return "require" if bool(payload.get("require")) else "skip"
    data = payload.get("data")
    if isinstance(data, dict) and "require" in data:
        return "require" if bool(data.get("require")) else "skip"
    return "require"


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


_APPROVAL_REASON_KEYS = ("reason", "purpose", "summary", "intent")


def extract_approval_reason(arguments: dict[str, Any] | None) -> Optional[str]:
    """Pull a human-facing why-this-call string from tool arguments if present."""
    args = arguments if isinstance(arguments, dict) else {}
    for key in _APPROVAL_REASON_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def approval_display_fields(
    tool: ToolDef | None, arguments: dict[str, Any] | None
) -> dict[str, Optional[str]]:
    """Fields shown on the approval card (and stored on tool_call payload)."""
    reason = extract_approval_reason(arguments)
    tool_description = None
    if tool is not None:
        desc = str(getattr(tool, "description", "") or "").strip()
        tool_description = desc or None
    return {
        "reason": reason,
        "tool_description": tool_description,
    }
