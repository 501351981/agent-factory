"""Agent system_prompt template rendering: ``{{var}}`` → value at session create."""

from __future__ import annotations

import re
from typing import Any, Mapping

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def normalize_prompt_vars(raw: Mapping[str, Any] | None) -> dict[str, str]:
    if not raw:
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        out[name] = "" if value is None else str(value)
    return out


def render_prompt_template(template: str, vars_map: Mapping[str, str] | None) -> str:
    """Replace ``{{name}}`` with values; missing keys become empty string."""
    if not template:
        return ""
    values = vars_map or {}

    def _replace(match: re.Match[str]) -> str:
        return values.get(match.group(1), "")

    return _VAR_RE.sub(_replace, template)
