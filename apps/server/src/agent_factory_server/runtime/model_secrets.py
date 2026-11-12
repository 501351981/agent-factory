"""Encrypt model provider API keys at rest (Fernet, prefix enc:v1:)."""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from agent_factory_server.config import get_settings

logger = logging.getLogger(__name__)

SEALED_PREFIX = "enc:v1:"
_DEV_FALLBACK = "agent-factory-dev-model-secrets-key-32b!"


def _resolve_passphrase() -> tuple[str, str]:
    """Return (passphrase, source_label)."""
    settings = get_settings()
    for label, value in (
        ("MODEL_SECRETS_KEY", (settings.model_secrets_key or "").strip()),
        ("FACTORY_API_KEY", (settings.factory_api_key or "").strip()),
        ("EMBED_TOKEN_SECRET", (settings.embed_token_secret or "").strip()),
    ):
        if value:
            return value, label
    return _DEV_FALLBACK, "dev-fallback"


def _fernet_key(passphrase: str) -> bytes:
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache
def _fernet() -> Fernet:
    passphrase, source = _resolve_passphrase()
    if source == "dev-fallback":
        logger.warning(
            "MODEL_SECRETS_KEY unset; using weak dev fallback to seal model API keys. "
            "Set MODEL_SECRETS_KEY (or FACTORY_API_KEY) before production use."
        )
    return Fernet(_fernet_key(passphrase))


def clear_model_secrets_cache() -> None:
    """Test helper: reset cached Fernet after env changes."""
    _fernet.cache_clear()


def is_sealed(value: str | None) -> bool:
    return bool(value) and str(value).startswith(SEALED_PREFIX)


def seal_model_api_key(plaintext: str | None) -> str:
    """Encrypt a plaintext key for DB storage. Empty stays empty."""
    raw = (plaintext or "").strip()
    if not raw:
        return ""
    if is_sealed(raw):
        return raw
    token = _fernet().encrypt(raw.encode("utf-8")).decode("ascii")
    return f"{SEALED_PREFIX}{token}"


def reveal_model_api_key(stored: str | None) -> str:
    """Decrypt a stored key. Legacy plaintext is returned unchanged."""
    raw = (stored or "").strip()
    if not raw:
        return ""
    if not is_sealed(raw):
        return raw
    token = raw[len(SEALED_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "无法解密模型 API Key（密钥可能已更换）。请在管理台重新填写该模型的 API Key。"
        ) from exc


def maybe_reseal_model_api_key(stored: str | None) -> tuple[str, bool]:
    """Return (stored_or_sealed, changed). Seals legacy plaintext in place."""
    raw = (stored or "").strip()
    if not raw or is_sealed(raw):
        return raw, False
    return seal_model_api_key(raw), True
