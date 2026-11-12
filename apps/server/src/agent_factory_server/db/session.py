from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.config import get_settings
from agent_factory_server.db.migrate import ensure_sqlite_columns

_settings = get_settings()
_db_url = _settings.database_url
_is_sqlite = _db_url.startswith("sqlite")

if _is_sqlite:
    # Ensure parent directory exists for file-based sqlite.
    raw = _db_url.split(":///", 1)[-1]
    Path(raw).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(_db_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        if _is_sqlite:
            await ensure_sqlite_columns(conn)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
