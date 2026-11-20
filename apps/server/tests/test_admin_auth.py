from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_factory_server.api import auth_routes
from agent_factory_server.auth import require_api_key, resolve_auth
from agent_factory_server.config import get_settings
from agent_factory_server.runtime import admin_session as admin

ADMIN_ENV = {
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "unit-test-password",
    "ADMIN_SESSION_HOURS": "1",
    "ADMIN_LOGIN_MAX_ATTEMPTS": "3",
    "ADMIN_LOGIN_LOCK_SECONDS": "60",
}


class AdminSessionUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(os.environ, ADMIN_ENV, clear=False)
        self._env.start()
        get_settings.cache_clear()
        auth_routes._login_failures.clear()
        self.client = TestClient(self._app())

    def tearDown(self) -> None:
        auth_routes._login_failures.clear()
        self._env.stop()
        get_settings.cache_clear()

    @staticmethod
    def _app() -> FastAPI:
        # Login endpoints never touch the DB, so a bare app is enough.
        app = FastAPI()
        app.include_router(auth_routes.router)
        return app

    def login(self, username: str = "admin", password: str = "unit-test-password"):
        return self.client.post(
            "/api/auth/admin-login", json={"username": username, "password": password}
        )

    def test_login_returns_session_token(self):
        resp = self.login()
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["username"], "admin")
        claims = admin.decode_admin_token(body["token"])
        assert claims is not None
        self.assertEqual(claims.username, "admin")
        self.assertGreater(claims.exp, int(datetime.now(timezone.utc).timestamp()))

    def test_wrong_credentials_rejected(self):
        self.assertEqual(self.login(password="nope").status_code, 401)
        self.assertEqual(self.login(username="nope").status_code, 401)

    def test_login_locked_after_repeated_failures(self):
        for _ in range(3):
            self.assertEqual(self.login(password="nope").status_code, 401)
        resp = self.login(password="nope")
        self.assertEqual(resp.status_code, 429)
        # Even the correct password is blocked while the lock is active.
        self.assertEqual(self.login().status_code, 429)

    def test_session_endpoint(self):
        token = self.login().json()["token"]
        ok = self.client.get(
            "/api/auth/admin-session", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["username"], "admin")
        self.assertEqual(self.client.get("/api/auth/admin-session").status_code, 401)


class AdminTokenVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(os.environ, ADMIN_ENV, clear=False)
        self._env.start()
        get_settings.cache_clear()

    def tearDown(self) -> None:
        self._env.stop()
        get_settings.cache_clear()

    def test_admin_token_is_platform_auth(self):
        token, _ = admin.mint_admin_token(username="admin")
        auth = asyncio.run(
            resolve_auth(
                authorization=f"Bearer {token}",
                x_api_key=None,
                x_access_key=None,
                x_secret_key=None,
                session=None,  # type: ignore[arg-type]
            )
        )
        self.assertEqual(auth.kind, "platform")

    def test_admin_token_rejected_by_embed_decoder(self):
        from agent_factory_server.runtime.embed_token import decode_embed_token

        token, _ = admin.mint_admin_token(username="admin")
        self.assertIsNone(decode_embed_token(token))

    def test_password_rotation_invalidates_token(self):
        token, _ = admin.mint_admin_token(username="admin")
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "rotated"}, clear=False):
            get_settings.cache_clear()
            self.assertIsNone(admin.decode_admin_token(token))

    def test_expired_token_rejected(self):
        now = datetime.now(timezone.utc)
        payload = {
            "typ": "admin",
            "sub": "admin",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        }
        secret = admin._admin_signing_secret()
        token = jwt.encode(payload, secret, algorithm="HS256")
        self.assertIsNone(admin.decode_admin_token(token))

    def test_anonymous_platform_call_requires_login(self):
        with self.assertRaises(Exception) as ctx:
            asyncio.run(
                require_api_key(
                    authorization=None,
                    x_api_key=None,
                    x_access_key=None,
                    x_secret_key=None,
                    session=None,  # type: ignore[arg-type]
                )
            )
        self.assertEqual(getattr(ctx.exception, "status_code", None), 401)


if __name__ == "__main__":
    unittest.main()
