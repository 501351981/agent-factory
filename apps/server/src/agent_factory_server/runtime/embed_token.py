"""Short-lived embed JWT for chat-web / iframe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt

from agent_factory_server.config import get_settings

DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 24 * 3600


@dataclass
class EmbedClaims:
    agent_id: str
    user_key: Optional[str]
    exp: int
    iat: int


def _signing_secret() -> str:
    settings = get_settings()
    secret = (settings.embed_token_secret or settings.factory_api_key or "").strip()
    if not secret:
        # Dev fallback so local embed tokens still verify when no keys configured.
        secret = "agent-factory-dev-embed-secret-32b!"
    return secret


def mint_embed_token(
    *,
    agent_id: str,
    user_key: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> tuple[str, EmbedClaims]:
    ttl = int(ttl_seconds) if ttl_seconds is not None else DEFAULT_TTL_SECONDS
    ttl = max(60, min(ttl, MAX_TTL_SECONDS))
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=ttl)
    payload: dict[str, Any] = {
        "typ": "embed",
        "agent_id": agent_id,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    uk = (user_key or "").strip()
    if uk:
        payload["user_key"] = uk
    token = jwt.encode(payload, _signing_secret(), algorithm="HS256")
    claims = EmbedClaims(
        agent_id=agent_id,
        user_key=uk or None,
        exp=int(exp.timestamp()),
        iat=int(now.timestamp()),
    )
    return token, claims


def decode_embed_token(token: str) -> Optional[EmbedClaims]:
    raw = (token or "").strip()
    if not raw or raw.count(".") != 2:
        return None
    try:
        payload = jwt.decode(raw, _signing_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "embed":
        return None
    agent_id = str(payload.get("agent_id") or "").strip()
    if not agent_id:
        return None
    user_key = payload.get("user_key")
    uk = str(user_key).strip() if user_key else None
    return EmbedClaims(
        agent_id=agent_id,
        user_key=uk or None,
        exp=int(payload.get("exp") or 0),
        iat=int(payload.get("iat") or 0),
    )
