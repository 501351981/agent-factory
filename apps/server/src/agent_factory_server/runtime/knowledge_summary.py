"""Upload-time summaries for knowledge-base documents.

The factory is the single writer: the model only ever sees ``summary`` in the
catalog, so a bad summary degrades retrieval for everyone. Generation is:

1. deterministic extraction (always available, no model needed);
2. LLM rewrite when a model profile is configured.

Any LLM failure falls back to extraction rather than failing the upload.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.llm.openai_compat import OpenAICompatClient

logger = logging.getLogger(__name__)

SUMMARY_MAX_CHARS = 120
_LLM_INPUT_CHARS = 6000

_FENCE_RE = re.compile(r"```.*?```", re.S)
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n(?:---|\.\.\.)\n", re.S)
_HTML_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_EMPHASIS_RE = re.compile(r"(\*\*|__|`|\*|_)(.+?)\1")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.M)
_SPACES_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])|(?<=\.)\s+")


def _clean_text(text: str) -> str:
    value = _FRONTMATTER_RE.sub("", text or "")
    value = _FENCE_RE.sub(" ", value)
    value = _HTML_RE.sub(" ", value)
    value = _LINK_RE.sub(r"\1", value)
    value = _HEADING_RE.sub("", value)
    value = _BULLET_RE.sub("", value)
    value = _EMPHASIS_RE.sub(r"\2", value)
    value = value.replace("|", " ")
    return _SPACES_RE.sub(" ", value).strip()


def extractive_summary(
    text: str, *, title: str = "", max_chars: int = SUMMARY_MAX_CHARS
) -> str:
    """First meaningful sentences; deterministic and dependency-free."""
    cleaned = _clean_text(text)
    if not cleaned:
        return (title or "").strip()[:max_chars]

    # Drop leading boilerplate lines that only repeat the title.
    pieces = [piece.strip() for piece in _SENTENCE_SPLIT_RE.split(cleaned) if piece.strip()]
    summary = ""
    for piece in pieces:
        candidate = f"{summary}{piece}" if summary else piece
        if len(candidate) > max_chars:
            break
        summary = candidate
        if len(summary) >= max_chars * 0.6:
            break
    if not summary:
        summary = cleaned
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary.strip()


async def generate_summary(
    session: AsyncSession,
    *,
    title: str,
    text: str,
    max_chars: int = SUMMARY_MAX_CHARS,
    use_llm: bool = True,
) -> tuple[str, str]:
    """Return (summary, source) where source is ``llm`` or ``auto``."""
    baseline = extractive_summary(text, title=title, max_chars=max_chars)
    if not use_llm or not (text or "").strip():
        return baseline, "auto"

    try:
        profile = await repo.get_default_model_profile(session)
    except Exception as exc:  # noqa: BLE001 - summary must never break upload
        logger.warning("kb summary: profile lookup failed: %s", exc)
        return baseline, "auto"
    if profile is None:
        return baseline, "auto"

    snippet = _clean_text(text)[:_LLM_INPUT_CHARS]
    if not snippet:
        return baseline, "auto"

    prompt = [
        {
            "role": "system",
            "content": (
                f"你为知识库文档写一句话摘要，供检索时判断相关性。"
                f"不超过 {max_chars} 字，只写文档实际包含的内容，不要前缀、不要 Markdown、"
                "不要评价文档质量。用文档本身的语言。"
            ),
        },
        {
            "role": "user",
            "content": f"标题：{title or '(无标题)'}\n\n正文：\n{snippet}",
        },
    ]
    try:
        client = OpenAICompatClient(
            base_url=profile.base_url,
            api_key=repo.reveal_profile_api_key(profile),
            default_model=profile.model_name,
        )
        data = await client.chat(
            messages=prompt,
            tools=None,
            model=profile.model_name,
            temperature=0.2,
        )
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = _SPACES_RE.sub(" ", str(message.get("content") or "")).strip()
    except Exception as exc:  # noqa: BLE001
        logger.info("kb summary: LLM unavailable, using extraction: %s", exc)
        return baseline, "auto"

    if not content:
        return baseline, "auto"
    if len(content) > max_chars:
        content = content[: max_chars - 1].rstrip() + "…"
    return content, "llm"


def is_manual_source(source: Optional[str]) -> bool:
    return (source or "") == "manual"
