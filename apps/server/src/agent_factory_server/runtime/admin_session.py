"""Admin console session tokens.

The console is a static SPA: it logs in once with ADMIN_USERNAME /
ADMIN_PASSWORD, receives a short-lived HS256 JWT, and sends it back as
``Authorization: Bearer <token>``. ``auth.resolve_auth`` maps that token to
``AuthContext(kind="platform")`` so every platform/admin endpoint accepts it.

The signing key is derived from the configured secrets *and* the current
admin password, so:

* rotating ADMIN_PASSWORD invalidates every issued session;
* the public dev fallback secret alone is not enough to forge a token.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt

from agent_factory_server.config import get_settings
from agent_factory_server.runtime.embed_token import signing_secret

TOKEN_TYPE = "admin"


@dataclass
class AdminClaims:
    username: str
    exp: int
    iat: int


def _admin_signing_secret() -> str:
    password = get_settings().admin_password.strip()
    raw = f"admin|{signing_secret()}|{password}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mint_admin_token(
    *, username: str, ttl_seconds: Optional[int] = None
) -> tuple[str, AdminClaims]:
    settings = get_settings()
    ttl = int(ttl_seconds) if ttl_seconds is not None else settings.admin_session_hours * 3600
    ttl = max(60, min(ttl, 7 * 24 * 3600))
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=ttl)
    payload: dict[str, Any] = {
        "typ": TOKEN_TYPE,
        "sub": username,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, _admin_signing_secret(), algorithm="HS256")
    claims = AdminClaims(
        username=username, exp=int(exp.timestamp()), iat=int(now.timestamp())
    )
    return token, claims


def decode_admin_token(token: str) -> Optional[AdminClaims]:
    raw = (token or "").strip()
    if not raw or raw.count(".") != 2:
        return None
    try:
        payload = jwt.decode(raw, _admin_signing_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != TOKEN_TYPE:
        return None
    username = str(payload.get("sub") or "").strip()
    if not username:
        return None
    return AdminClaims(
        username=username,
        exp=int(payload.get("exp") or 0),
        iat=int(payload.get("iat") or 0),
    )
