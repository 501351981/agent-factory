from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import EmbedTokenOut, EmbedTokenRequest
from agent_factory_server.runtime.credentials import verify_secret_key
from agent_factory_server.runtime.embed_token import mint_embed_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
