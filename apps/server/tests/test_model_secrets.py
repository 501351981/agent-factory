from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.config import get_settings
from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import ModelProfileCreate, ModelProfileUpdate
from agent_factory_server.runtime import model_secrets as ms


class ModelSecretsUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()
        ms.clear_model_secrets_cache()
        self._env = patch.dict(
            os.environ,
            {"MODEL_SECRETS_KEY": "unit-test-model-secrets-passphrase"},
            clear=False,
        )
        self._env.start()
        get_settings.cache_clear()
        ms.clear_model_secrets_cache()

    def tearDown(self) -> None:
        self._env.stop()
        get_settings.cache_clear()
        ms.clear_model_secrets_cache()

    def test_seal_reveal_roundtrip(self):
        sealed = ms.seal_model_api_key("sk-test-1234567890")
        self.assertTrue(ms.is_sealed(sealed))
        self.assertNotIn("sk-test", sealed)
        self.assertEqual(ms.reveal_model_api_key(sealed), "sk-test-1234567890")

    def test_legacy_plaintext_passthrough(self):
        self.assertEqual(ms.reveal_model_api_key("plain-legacy-key"), "plain-legacy-key")
        sealed, changed = ms.maybe_reseal_model_api_key("plain-legacy-key")
        self.assertTrue(changed)
        self.assertTrue(ms.is_sealed(sealed))
        self.assertEqual(ms.reveal_model_api_key(sealed), "plain-legacy-key")

    def test_empty_and_idempotent_seal(self):
        self.assertEqual(ms.seal_model_api_key(""), "")
        once = ms.seal_model_api_key("abc")
        twice = ms.seal_model_api_key(once)
        self.assertEqual(once, twice)

    def test_wrong_secret_raises(self):
        sealed = ms.seal_model_api_key("sk-secret")
        get_settings.cache_clear()
        ms.clear_model_secrets_cache()
        with patch.dict(os.environ, {"MODEL_SECRETS_KEY": "different-passphrase"}):
            get_settings.cache_clear()
            ms.clear_model_secrets_cache()
            with self.assertRaises(ValueError):
                ms.reveal_model_api_key(sealed)


class ModelSecretsRepoTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        get_settings.cache_clear()
        ms.clear_model_secrets_cache()
        self._env = patch.dict(
            os.environ,
            {"MODEL_SECRETS_KEY": "repo-test-model-secrets-passphrase"},
            clear=False,
        )
        self._env.start()
        get_settings.cache_clear()
        ms.clear_model_secrets_cache()

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._env.stop()
        get_settings.cache_clear()
        ms.clear_model_secrets_cache()

    async def test_create_stores_sealed_and_masks_out(self):
        async with self.Session() as session:
            out = await repo.create_model_profile(
                session,
                ModelProfileCreate(
                    name="m1",
                    provider="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-live-abcdefghijklmnop",
                    model_name="gpt-4o-mini",
                ),
            )
            self.assertTrue(out.has_api_key)
            self.assertIn("****", out.api_key_masked)
            self.assertNotIn("live-abcdefgh", out.api_key_masked)

            row = await repo.get_model_profile(session, out.id)
            assert row is not None
            self.assertTrue(ms.is_sealed(row.api_key))
            self.assertEqual(
                repo.reveal_profile_api_key(row), "sk-live-abcdefghijklmnop"
            )

    async def test_update_reseals_new_key(self):
        async with self.Session() as session:
            created = await repo.create_model_profile(
                session,
                ModelProfileCreate(
                    name="m2",
                    provider="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-old-key-11111111",
                    model_name="gpt-4o-mini",
                ),
            )
            await repo.update_model_profile(
                session,
                created.id,
                ModelProfileUpdate(api_key="sk-new-key-22222222"),
            )
            row = await repo.get_model_profile(session, created.id)
            assert row is not None
            self.assertEqual(repo.reveal_profile_api_key(row), "sk-new-key-22222222")

    async def test_reseal_plaintext_migration(self):
        async with self.Session() as session:
            created = await repo.create_model_profile(
                session,
                ModelProfileCreate(
                    name="m3",
                    provider="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-will-overwrite",
                    model_name="gpt-4o-mini",
                ),
            )
            row = await repo.get_model_profile(session, created.id)
            assert row is not None
            # Simulate legacy plaintext row.
            row.api_key = "sk-legacy-plaintext-zzzz"
            session.add(row)
            await session.commit()

            n = await repo.reseal_plaintext_model_api_keys(session)
            self.assertEqual(n, 1)
            row2 = await repo.get_model_profile(session, created.id)
            assert row2 is not None
            self.assertTrue(ms.is_sealed(row2.api_key))
            self.assertEqual(
                repo.reveal_profile_api_key(row2), "sk-legacy-plaintext-zzzz"
            )
            n2 = await repo.reseal_plaintext_model_api_keys(session)
            self.assertEqual(n2, 0)


if __name__ == "__main__":
    unittest.main()
