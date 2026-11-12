"""Per-agent access key / secret key helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def new_access_key() -> str:
    return f"ak_{secrets.token_urlsafe(18)}"


def new_secret_key() -> str:
    return f"sk_{secrets.token_urlsafe(32)}"


def hash_secret_key(secret_key: str) -> str:
    return hashlib.sha256(secret_key.encode("utf-8")).hexdigest()


def verify_secret_key(secret_key: str, secret_key_hash: str | None) -> bool:
    if not secret_key or not secret_key_hash:
        return False
    digest = hash_secret_key(secret_key)
    return hmac.compare_digest(digest, secret_key_hash)
