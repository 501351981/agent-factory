from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.config import get_settings
from agent_factory_server.db.session import get_session
from agent_factory_server.runtime.credentials import verify_secret_key
from agent_factory_server.runtime.embed_token import decode_embed_token

AuthKind = Literal["anonymous", "platform", "agent_key", "embed"]


@dataclass
class AuthContext:
    kind: AuthKind
    agent_id: Optional[str] = None
    user_key: Optional[str] = None


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


async def resolve_auth(
    *,
    authorization: str | None,
    x_api_key: str | None,
    x_access_key: str | None,
    x_secret_key: str | None,
    session: AsyncSession,
) -> AuthContext:
    """Resolve credentials: embed_token → AK/SK → platform key → anonymous."""
    from agent_factory_server.db import repo

    bearer = _extract_bearer(authorization)
    if bearer:
        claims = decode_embed_token(bearer)
        if claims is not None:
            return AuthContext(
                kind="embed",
                agent_id=claims.agent_id,
                user_key=claims.user_key,
            )

    access_key = (x_access_key or "").strip()
    secret_key = (x_secret_key or "").strip()
    if access_key or secret_key:
        if not access_key or not secret_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-Access-Key and X-Secret-Key are both required",
            )
        row = await repo.get_agent_record_by_access_key(session, access_key)
        if not row or not verify_secret_key(secret_key, getattr(row, "secret_key_hash", None)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access key or secret key"
            )
        return AuthContext(kind="agent_key", agent_id=row.id)

    settings = get_settings()
    expected = settings.factory_api_key.strip()
    provided = (x_api_key or "").strip() or (bearer or "")
    if expected:
        if provided != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
            )
        return AuthContext(kind="platform")
    # Dev mode: no platform key configured.
    return AuthContext(kind="anonymous")


async def get_auth_context(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_access_key: str | None = Header(default=None, alias="X-Access-Key"),
    x_secret_key: str | None = Header(default=None, alias="X-Secret-Key"),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    return await resolve_auth(
        authorization=authorization,
        x_api_key=x_api_key,
        x_access_key=x_access_key,
        x_secret_key=x_secret_key,
        session=session,
    )


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_access_key: str | None = Header(default=None, alias="X-Access-Key"),
    x_secret_key: str | None = Header(default=None, alias="X-Secret-Key"),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Platform admin auth. Agent AK/SK and embed tokens are rejected."""
    auth = await resolve_auth(
        authorization=authorization,
        x_api_key=x_api_key,
        x_access_key=x_access_key,
        x_secret_key=x_secret_key,
        session=session,
    )
    if auth.kind in ("agent_key", "embed"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform API key required",
        )
    settings = get_settings()
    if settings.factory_api_key.strip() and auth.kind != "platform":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )
    return auth


async def require_caller(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_access_key: str | None = Header(default=None, alias="X-Access-Key"),
    x_secret_key: str | None = Header(default=None, alias="X-Secret-Key"),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Chat/session auth: platform, agent AK/SK, or embed token."""
    auth = await resolve_auth(
        authorization=authorization,
        x_api_key=x_api_key,
        x_access_key=x_access_key,
        x_secret_key=x_secret_key,
        session=session,
    )
    settings = get_settings()
    if (
        settings.factory_api_key.strip()
        and auth.kind == "anonymous"
        and not (x_access_key or "").strip()
    ):
        # Platform key configured but nothing valid provided.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return auth


async def require_agent_manager(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_access_key: str | None = Header(default=None, alias="X-Access-Key"),
    x_secret_key: str | None = Header(default=None, alias="X-Secret-Key"),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Manage a single agent: platform key or that agent's AK/SK (not embed)."""
    auth = await resolve_auth(
        authorization=authorization,
        x_api_key=x_api_key,
        x_access_key=x_access_key,
        x_secret_key=x_secret_key,
        session=session,
    )
    settings = get_settings()
    if (
        settings.factory_api_key.strip()
        and auth.kind == "anonymous"
        and not (x_access_key or "").strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    if auth.kind == "embed":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent access key/secret or platform API key required",
        )
    return auth


def enforce_agent_scope(auth: AuthContext, agent_id: str) -> None:
    if auth.kind in ("agent_key", "embed"):
        if not auth.agent_id or auth.agent_id != agent_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Credential is not allowed for this agent",
            )


def enforce_user_key_binding(auth: AuthContext, user_key: str | None) -> str | None:
    """If embed token binds user_key, force it; mismatch → 403."""
    bound = (auth.user_key or "").strip() if auth.kind == "embed" else ""
    if not bound:
        return (user_key or "").strip() or None
    provided = (user_key or "").strip()
    if provided and provided != bound:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_key does not match embed token binding",
        )
    return bound


def check_ws_token(token: str | None) -> None:
    settings = get_settings()
    expected = settings.factory_api_key.strip()
    if not expected:
        return
    if token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
