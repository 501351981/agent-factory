from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_factory_server.auth import (
    AuthContext,
    enforce_agent_scope,
    enforce_user_key_binding,
    require_agent_manager,
)
from agent_factory_server.runtime.credentials import (
    hash_secret_key,
    new_access_key,
    new_secret_key,
    verify_secret_key,
)
from agent_factory_server.runtime.embed_token import decode_embed_token, mint_embed_token
from fastapi import HTTPException


class CredentialsTest(unittest.TestCase):
    def test_hash_roundtrip(self) -> None:
        sk = new_secret_key()
        digest = hash_secret_key(sk)
        self.assertTrue(verify_secret_key(sk, digest))
        self.assertFalse(verify_secret_key(sk + "x", digest))
        self.assertTrue(new_access_key().startswith("ak_"))
        self.assertTrue(sk.startswith("sk_"))


class EmbedTokenTest(unittest.TestCase):
    def test_mint_and_decode_with_user_key(self) -> None:
        token, claims = mint_embed_token(
            agent_id="agent-1", user_key="u-1", ttl_seconds=600
        )
        self.assertEqual(claims.agent_id, "agent-1")
        self.assertEqual(claims.user_key, "u-1")
        decoded = decode_embed_token(token)
        assert decoded is not None
        self.assertEqual(decoded.agent_id, "agent-1")
        self.assertEqual(decoded.user_key, "u-1")

    def test_mint_without_user_key(self) -> None:
        token, claims = mint_embed_token(agent_id="agent-2")
        self.assertIsNone(claims.user_key)
        decoded = decode_embed_token(token)
        assert decoded is not None
        self.assertIsNone(decoded.user_key)

    def test_expired_token_rejected(self) -> None:
        import jwt
        from agent_factory_server.runtime import embed_token as et

        now = datetime.now(timezone.utc)
        payload = {
            "typ": "embed",
            "agent_id": "a",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        }
        token = jwt.encode(payload, et._signing_secret(), algorithm="HS256")
        self.assertIsNone(decode_embed_token(token))


class ScopeTest(unittest.TestCase):
    def test_agent_scope(self) -> None:
        auth = AuthContext(kind="agent_key", agent_id="a1")
        enforce_agent_scope(auth, "a1")
        with self.assertRaises(HTTPException) as ctx:
            enforce_agent_scope(auth, "a2")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_user_key_binding(self) -> None:
        auth = AuthContext(kind="embed", agent_id="a1", user_key="u1")
        self.assertEqual(enforce_user_key_binding(auth, None), "u1")
        self.assertEqual(enforce_user_key_binding(auth, "u1"), "u1")
        with self.assertRaises(HTTPException):
            enforce_user_key_binding(auth, "u2")

    def test_platform_no_binding(self) -> None:
        auth = AuthContext(kind="platform")
        self.assertEqual(enforce_user_key_binding(auth, "u9"), "u9")


class AgentManagerAuthTest(unittest.IsolatedAsyncioTestCase):
    async def test_embed_rejected_for_agent_manager(self) -> None:
        from unittest.mock import AsyncMock, patch

        with patch(
            "agent_factory_server.auth.resolve_auth",
            new=AsyncMock(
                return_value=AuthContext(kind="embed", agent_id="a1", user_key="u1")
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await require_agent_manager(
                    authorization="Bearer embed",
                    x_api_key=None,
                    x_access_key=None,
                    x_secret_key=None,
                    session=AsyncMock(),
                )
            self.assertEqual(ctx.exception.status_code, 403)

    async def test_agent_key_allowed_for_agent_manager(self) -> None:
        from unittest.mock import AsyncMock, patch

        with patch(
            "agent_factory_server.auth.resolve_auth",
            new=AsyncMock(return_value=AuthContext(kind="agent_key", agent_id="a1")),
        ):
            auth = await require_agent_manager(
                authorization=None,
                x_api_key=None,
                x_access_key="ak_x",
                x_secret_key="sk_x",
                session=AsyncMock(),
            )
            self.assertEqual(auth.kind, "agent_key")
            self.assertEqual(auth.agent_id, "a1")


if __name__ == "__main__":
    unittest.main()
