from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = f"sqlite+aiosqlite:///{ROOT_DIR / 'data' / 'factory.db'}"
    # Local file uploads (agent logos, etc.). Relative paths resolve under ROOT_DIR.
    upload_dir: str = "data/uploads"
    factory_api_key: str = ""
    # JWT secret for embed tokens; falls back to factory_api_key when empty.
    embed_token_secret: str = ""
    # Fernet passphrase for model provider API keys at rest.
    # Falls back to FACTORY_API_KEY → EMBED_TOKEN_SECRET → weak dev default.
    model_secrets_key: str = ""
    cors_origins: str = (
        "http://localhost:8801,http://localhost:8802,http://localhost:8803"
    )

    openai_api_base: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    tool_timeout_seconds: float = 30.0
    max_tool_rounds: int = 8
    # Per-run token cap fallback when Agent.runtime.max_run_tokens is 0.
    # Coarse backstop against runaway loops: ~1M allows 8–20 large-context
    # rounds. Set a smaller per-Agent budget when you pay per token.
    max_run_tokens: int = 1_000_000

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
