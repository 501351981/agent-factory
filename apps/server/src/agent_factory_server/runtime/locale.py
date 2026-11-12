"""Session locale helpers: zh / en / ja."""

from __future__ import annotations

from typing import Literal, Mapping, Optional

Locale = Literal["zh", "en", "ja"]
SUPPORTED_LOCALES: tuple[Locale, ...] = ("zh", "en", "ja")

_ALIASES: dict[str, Locale] = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "cn": "zh",
    "chinese": "zh",
    "中文": "zh",
    "en": "en",
    "en-us": "en",
    "en_us": "en",
    "english": "en",
    "ja": "ja",
    "jp": "ja",
    "ja-jp": "ja",
    "ja_jp": "ja",
    "japanese": "ja",
    "日本語": "ja",
}


def normalize_locale(raw: Optional[str], *, default: Locale = "zh") -> Locale:
    if raw is None:
        return default
    key = str(raw).strip().lower()
    if not key:
        return default
    if key in _ALIASES:
        return _ALIASES[key]
    if key in SUPPORTED_LOCALES:
        return key  # type: ignore[return-value]
    return default


def pick_system_prompt_template(
    *,
    system_prompt: str,
    system_prompts: Mapping[str, str] | None,
    locale: Locale,
) -> str:
    """Pick Agent template for locale; fallback zh → en → ja → legacy system_prompt."""
    prompts = {
        str(k).strip().lower(): str(v)
        for k, v in (system_prompts or {}).items()
        if str(v or "").strip()
    }
    if not prompts and system_prompt.strip():
        prompts = {"zh": system_prompt}

    for key in (locale, "zh", "en", "ja"):
        text = prompts.get(key)
        if text and text.strip():
            return text.strip()
    return (system_prompt or "").strip()
