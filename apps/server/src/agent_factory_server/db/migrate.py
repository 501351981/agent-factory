from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_sqlite_columns(conn: AsyncConnection) -> None:
    """Add newly introduced columns for existing SQLite databases."""
    result = await conn.execute(text("PRAGMA table_info(agents)"))
    columns = {row[1] for row in result.fetchall()}
    if "model_profile_id" not in columns:
        await conn.execute(text("ALTER TABLE agents ADD COLUMN model_profile_id VARCHAR"))
    if "runtime_json" not in columns:
        await conn.execute(text("ALTER TABLE agents ADD COLUMN runtime_json TEXT DEFAULT '{}' NOT NULL"))
    if "system_prompts_json" not in columns:
        await conn.execute(text("ALTER TABLE agents ADD COLUMN system_prompts_json TEXT"))
    if "access_key" not in columns:
        await conn.execute(text("ALTER TABLE agents ADD COLUMN access_key VARCHAR"))
    if "secret_key_hash" not in columns:
        await conn.execute(text("ALTER TABLE agents ADD COLUMN secret_key_hash TEXT"))
    if "logo_url" not in columns:
        await conn.execute(text("ALTER TABLE agents ADD COLUMN logo_url TEXT"))
    if "welcome_title" not in columns:
        await conn.execute(text("ALTER TABLE agents ADD COLUMN welcome_title TEXT"))
    if "welcome_subtitle" not in columns:
        await conn.execute(text("ALTER TABLE agents ADD COLUMN welcome_subtitle TEXT"))
    if "suggested_prompts_json" not in columns:
        await conn.execute(
            text("ALTER TABLE agents ADD COLUMN suggested_prompts_json TEXT DEFAULT '[]'")
        )

    result = await conn.execute(text("PRAGMA table_info(model_profiles)"))
    model_columns = {row[1] for row in result.fetchall()}
    if "temperature" not in model_columns:
        await conn.execute(
            text("ALTER TABLE model_profiles ADD COLUMN temperature FLOAT DEFAULT 0.3 NOT NULL")
        )
    if "context_window_tokens" not in model_columns:
        await conn.execute(
            text(
                "ALTER TABLE model_profiles ADD COLUMN context_window_tokens INTEGER DEFAULT 128000 NOT NULL"
            )
        )
    if "reserve_output_tokens" not in model_columns:
        await conn.execute(
            text(
                "ALTER TABLE model_profiles ADD COLUMN reserve_output_tokens INTEGER DEFAULT 8192 NOT NULL"
            )
        )
    if "input_price_per_1m" not in model_columns:
        await conn.execute(
            text(
                "ALTER TABLE model_profiles ADD COLUMN input_price_per_1m FLOAT DEFAULT 0 NOT NULL"
            )
        )
    if "output_price_per_1m" not in model_columns:
        await conn.execute(
            text(
                "ALTER TABLE model_profiles ADD COLUMN output_price_per_1m FLOAT DEFAULT 0 NOT NULL"
            )
        )

    result = await conn.execute(text("PRAGMA table_info(runs)"))
    run_columns = {row[1] for row in result.fetchall()}
    if run_columns and "waiting_expires_at" not in run_columns:
        await conn.execute(
            text("ALTER TABLE runs ADD COLUMN waiting_expires_at DATETIME")
        )

    result = await conn.execute(text("PRAGMA table_info(sessions)"))
    session_columns = {row[1] for row in result.fetchall()}
    if "status" not in session_columns:
        await conn.execute(text("ALTER TABLE sessions ADD COLUMN status VARCHAR DEFAULT 'active'"))
    if "user_key" not in session_columns:
        await conn.execute(text("ALTER TABLE sessions ADD COLUMN user_key VARCHAR"))
    if "system_prompt" not in session_columns:
        await conn.execute(text("ALTER TABLE sessions ADD COLUMN system_prompt TEXT"))
    if "prompt_vars_json" not in session_columns:
        await conn.execute(text("ALTER TABLE sessions ADD COLUMN prompt_vars_json TEXT"))
    if "locale" not in session_columns:
        await conn.execute(text("ALTER TABLE sessions ADD COLUMN locale VARCHAR DEFAULT 'zh'"))
    if "client_device_id" not in session_columns:
        await conn.execute(text("ALTER TABLE sessions ADD COLUMN client_device_id VARCHAR"))

    result = await conn.execute(text("PRAGMA table_info(memories)"))
    memory_rows = result.fetchall()
    if memory_rows:
        memory_columns = {row[1] for row in memory_rows}
        if "kind" not in memory_columns:
            await conn.execute(
                text("ALTER TABLE memories ADD COLUMN kind VARCHAR DEFAULT 'profile'")
            )
            # Existing rows were flat longterm facts → treat as profile.
            await conn.execute(
                text(
                    "UPDATE memories SET kind = 'profile' "
                    "WHERE kind IS NULL OR kind = ''"
                )
            )
        if "active" not in memory_columns:
            await conn.execute(
                text("ALTER TABLE memories ADD COLUMN active BOOLEAN DEFAULT 1 NOT NULL")
            )
        if "pinned" not in memory_columns:
            await conn.execute(
                text("ALTER TABLE memories ADD COLUMN pinned BOOLEAN DEFAULT 0 NOT NULL")
            )
        if "last_accessed_at" not in memory_columns:
            await conn.execute(
                text("ALTER TABLE memories ADD COLUMN last_accessed_at DATETIME")
            )
