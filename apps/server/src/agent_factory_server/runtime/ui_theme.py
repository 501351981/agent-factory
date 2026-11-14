"""Agent chat UI theme tokens → CSS variables on ``.af-chat``.

Only whitelisted keys and safe color/length values are kept (no raw CSS).
"""

from __future__ import annotations

import re
from typing import Any

# Public API keys → CSS custom properties on `.af-chat`
THEME_CSS_VARS: dict[str, str] = {
    # Page
    "bg": "--af-bg",
    "surface": "--af-surface",
    "border": "--af-border",
    "borderSoft": "--af-border-soft",
    "text": "--af-text",
    "muted": "--af-muted",
    "accent": "--af-accent",
    "accentSoft": "--af-accent-soft",
    # User message
    "userBubble": "--af-user-bubble",
    "userBubbleText": "--af-user-bubble-text",
    "userBubbleBorder": "--af-user-bubble-border",
    # Assistant message
    "assistantBubble": "--af-assistant-bubble",
    "assistantBubbleText": "--af-assistant-bubble-text",
    "assistantBubbleBorder": "--af-assistant-bubble-border",
    # Composer / input
    "composerBg": "--af-composer-bg",
    "composerCard": "--af-composer-card",
    "composerBorder": "--af-composer-border",
    "composerText": "--af-composer-text",
    "composerMuted": "--af-composer-muted",
    "composerSendBg": "--af-composer-send-bg",
    "composerSendText": "--af-composer-send-text",
    # Process accents (optional)
    "think": "--af-think",
    "thinkSoft": "--af-think-soft",
    "tool": "--af-tool",
    "toolSoft": "--af-tool-soft",
    "ok": "--af-ok",
    "warn": "--af-warn",
    "danger": "--af-danger",
    # Radii
    "radiusLg": "--af-radius-lg",
    "radiusMd": "--af-radius-md",
    "radiusBubble": "--af-radius-bubble",
}

THEME_TOKEN_KEYS = frozenset(THEME_CSS_VARS) | frozenset({"preset"})

PRESET_DARK: dict[str, str] = {
    "preset": "dark",
    "bg": "#0f1115",
    "surface": "#1a1d24",
    "border": "#2a2f3a",
    "borderSoft": "rgba(255, 255, 255, 0.08)",
    "text": "#f3f4f6",
    "muted": "#9ca3af",
    "accent": "#e5e7eb",
    "accentSoft": "#252a33",
    "userBubble": "#2563eb",
    "userBubbleText": "#ffffff",
    "userBubbleBorder": "transparent",
    "assistantBubble": "#1a1d24",
    "assistantBubbleText": "#f3f4f6",
    "assistantBubbleBorder": "#2a2f3a",
    "composerBg": "#0f1115",
    "composerCard": "#1a1d24",
    "composerBorder": "#2a2f3a",
    "composerText": "#f3f4f6",
    "composerMuted": "#9ca3af",
    "composerSendBg": "#3b82f6",
    "composerSendText": "#ffffff",
    "think": "#818cf8",
    "thinkSoft": "#1e1b4b",
    "tool": "#2dd4bf",
    "toolSoft": "#134e4a",
    "ok": "#4ade80",
    "warn": "#fbbf24",
    "danger": "#f87171",
}

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNC = re.compile(
    r"^(?:rgba?|hsla?)\(\s*[\d.%\s,/\-+]+\s*\)$",
    re.IGNORECASE,
)
_LENGTH = re.compile(r"^\d+(?:\.\d+)?(?:px|rem|%)$")
_KEYWORD = re.compile(r"^(?:transparent|currentColor)$", re.IGNORECASE)


def _safe_theme_value(value: str) -> str | None:
    text = (value or "").strip()
    if not text or len(text) > 64:
        return None
    if any(ch in text for ch in (";", "{", "}", "<", ">", "url(", "expression", "@")):
        return None
    if (
        _HEX.match(text)
        or _FUNC.match(text)
        or _LENGTH.match(text)
        or _KEYWORD.match(text)
    ):
        return text
    return None


def normalize_ui_theme(raw: object) -> dict[str, str]:
    """Return a sanitized theme dict (may be empty). Invalid entries dropped."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    preset = str(raw.get("preset") or "").strip().lower()
    if preset == "dark":
        out.update(PRESET_DARK)
    elif preset == "light":
        out["preset"] = "light"

    for key in THEME_CSS_VARS:
        if key not in raw:
            continue
        safe = _safe_theme_value(str(raw.get(key) or ""))
        if safe is not None:
            out[key] = safe
    return out


def theme_to_css_vars(theme: dict[str, str] | None) -> dict[str, str]:
    """Map normalized theme tokens to ``{ '--af-bg': '#…', … }`` for inline style."""
    if not theme:
        return {}
    vars_out: dict[str, str] = {}
    for key, css_name in THEME_CSS_VARS.items():
        val = theme.get(key)
        if val:
            vars_out[css_name] = val
    return vars_out


def parse_ui_theme_json(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    import json

    try:
        parsed: Any = json.loads(raw)
    except Exception:
        return {}
    return normalize_ui_theme(parsed)
