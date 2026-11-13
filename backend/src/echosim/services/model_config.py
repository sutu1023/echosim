import asyncio
from uuid import uuid4

from echosim.domain.accounts import Fault
from echosim.domain.model_connection import ModelFailure
from echosim.services.works import identifier

BASE_URL = "https://opencode.ai/inference/go/openai/v1"


class ModelConfigurations:
    def __init__(self, store, vault, provider):
        self.store, self.vault, self.provider = store, vault, provider

    @staticmethod
    def validate(url, model, key):
        if url != BASE_URL or not isinstance(model, str) or not 1 <= len(model) <= 200:
            raise Fault("INVALID_REQUEST")
        if not model.strip() or any(ord(c) < 33 for c in model):
            raise Fault("INVALID_REQUEST")
        if not isinstance(key, str) or not 1 <= len(key) <= 4096:
            raise Fault("INVALID_REQUEST")
        if any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise Fault("INVALID_REQUEST")

    @staticmethod
    def public(row):
        if row is None:
            return None
        return {k: row[k] for k in ("id", "base_url", "model")} | {"api_key": "********"}

    async def test(self, url, model, key):
        self.validate(url, model, key)
        try:
            async with asyncio.timeout(60):
                result = await self.provider.generate(
                    key,
                    model,
                    [{"role": "user", "content": "请仅回复：连接测试成功。"}],
                    max_tokens=256,
                    temperature=0,
                    prompt_version="configuration-test-v1",
                )
        except TimeoutError:
            raise ModelFailure("MODEL_TIMEOUT", 504) from None
        if not isinstance(result.get("text"), str) or not result["text"].strip():
            raise ModelFailure("MODEL_INVALID_RESPONSE")
        return {"status": "success", "usage": result.get("usage")}

    async def current(self, owner):
        async with self.store.transaction() as db:
            return self.public(await db.config_current(owner))

    async def get(self, owner, version):
        async with self.store.transaction() as db:
            row = await db.config_get(owner, identifier(version, "NOT_FOUND", 404))
        if not row:
            raise Fault("NOT_FOUND", 404)
        return self.public(row)

    async def save(self, owner, url, model, key, expected):
        if expected is not None:
            expected = identifier(expected)
            await self.get(owner, expected)
        await self.test(url, model, key)
        version, encrypted = str(uuid4()), self.vault.encrypt(key)
        async with self.store.transaction() as db:
            await db.config_lock(owner)
            previous = await db.config_current(owner)
            if (previous["id"] if previous else None) != expected:
                raise Fault("CONFIGURATION_CONFLICT", 409)
            await db.config_insert(owner, version, model, encrypted)
            await db.config_cleanup()
            return self.public(await db.config_current(owner))

    async def acquire(self, db, owner, reference):
        await db.config_lock(owner)
        row = await db.config_current(owner)
        if not row:
            raise Fault("MODEL_CONFIGURATION_REQUIRED", 409)
        await db.config_acquire(owner, row["id"], reference)
        return row["id"]

    async def release(self, db, reference):
        await db.config_release(reference)
        await db.config_cleanup()

    async def bound(self, owner, version):
        async with self.store.transaction() as db:
            row = await db.config_get(owner, version)
        if not row:
            raise Fault("NOT_FOUND", 404)
        return {"model": row["model"], "key": self.vault.decrypt(row["ciphertext"])}
