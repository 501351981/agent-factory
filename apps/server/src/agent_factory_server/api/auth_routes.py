from __future__ import annotations

import hmac
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import _extract_bearer
from agent_factory_server.config import get_settings
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import (
    AdminLoginRequest,
    AdminSessionOut,
    EmbedTokenOut,
    EmbedTokenRequest,
)
from agent_factory_server.runtime.admin_session import decode_admin_token, mint_admin_token
from agent_factory_server.runtime.credentials import verify_secret_key
from agent_factory_server.runtime.embed_token import mint_embed_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

# In-memory brute-force guard: client key → (failed count, locked-until epoch).
_login_failures: dict[str, tuple[int, float]] = {}


def _client_key(request: Request) -> str:
    client = request.client
    return (client.host if client else "") or "unknown"


def _check_locked(key: str) -> None:
    settings = get_settings()
    count, locked_until = _login_failures.get(key, (0, 0.0))
    now = time.time()
    if count < settings.admin_login_max_attempts or locked_until <= now:
        return
    wait = int(locked_until - now) + 1
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"登录失败次数过多，请 {wait} 秒后重试",
    )


def _record_failure(key: str) -> None:
    settings = get_settings()
    count, _ = _login_failures.get(key, (0, 0.0))
    count += 1
    locked_until = 0.0
    if count >= settings.admin_login_max_attempts:
        locked_until = time.time() + settings.admin_login_lock_seconds
        count = settings.admin_login_max_attempts
    _login_failures[key] = (count, locked_until)


@router.post("/admin-login", response_model=AdminSessionOut)
async def admin_login(
    payload: AdminLoginRequest,
    request: Request,
) -> AdminSessionOut:
    """Exchange admin username/password for a console session token."""
    settings = get_settings()
    if not settings.admin_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理台登录未启用（ADMIN_PASSWORD 为空）",
        )
    key = _client_key(request)
    _check_locked(key)

    expected_user = settings.admin_username.strip() or "admin"
    expected_password = settings.admin_password.strip()
    user_ok = hmac.compare_digest(payload.username.strip(), expected_user)
    password_ok = hmac.compare_digest(payload.password, expected_password)
    if not (user_ok and password_ok):
        _record_failure(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )

    _login_failures.pop(key, None)
    token, claims = mint_admin_token(username=expected_user)
    return AdminSessionOut(
        token=token,
        username=claims.username,
        expires_at=datetime.fromtimestamp(claims.exp, tz=timezone.utc),
    )


@router.get("/admin-session", response_model=AdminSessionOut)
async def admin_session(
    authorization: str | None = Header(default=None),
) -> AdminSessionOut:
    """Validate the bearer token and describe the current console session."""
    claims = decode_admin_token(_extract_bearer(authorization) or "")
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )
    return AdminSessionOut(
        token=_extract_bearer(authorization) or "",
        username=claims.username,
        expires_at=datetime.fromtimestamp(claims.exp, tz=timezone.utc),
    )


@router.post("/embed-token", response_model=EmbedTokenOut)
async def create_embed_token(
    payload: EmbedTokenRequest,
    session: AsyncSession = Depends(get_session),
    x_access_key: str | None = Header(default=None, alias="X-Access-Key"),
    x_secret_key: str | None = Header(default=None, alias="X-Secret-Key"),
) -> EmbedTokenOut:
    """Mint a short-lived embed token. Requires per-agent AK/SK (not platform key)."""
    access_key = (x_access_key or "").strip()
    secret_key = (x_secret_key or "").strip()
    if not access_key or not secret_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Access-Key and X-Secret-Key are required",
        )
    row = await repo.get_agent_record_by_access_key(session, access_key)
    if not row or not verify_secret_key(secret_key, getattr(row, "secret_key_hash", None)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access key or secret key",
        )
    token, claims = mint_embed_token(
        agent_id=row.id,
        user_key=payload.user_key,
        ttl_seconds=payload.ttl_seconds,
    )
    return EmbedTokenOut(
        embed_token=token,
        agent_id=row.id,
        user_key=claims.user_key,
        expires_at=datetime.fromtimestamp(claims.exp, tz=timezone.utc),
    )
