import asyncio
import os
from uuid import uuid4

from cryptography.fernet import Fernet

from echosim.adapters.credentials import Credentials
from echosim.adapters.passwords import Passwords
from echosim.adapters.store import PostgresStore
from echosim.domain.accounts import Fault
from echosim.services.accounts import Accounts
from echosim.services.model_config import BASE_URL, ModelConfigurations
from echosim.services.works import Works


class Provider:
    async def generate(self, *args, **kwargs):
        return {"text": "ok"}


def test_real_configuration_rotation_and_restart():
    async def scenario():
        store = PostgresStore(os.environ["ECHOSIM_TEST_DATABASE_URL"])
        await store.migrate()
        auth = Accounts(store, Passwords())
        await auth.provision("config-owner", "integration-password")
        session = await auth.login("config-owner", "integration-password", "config-test")
        owner = (await auth.current(session["token"]))["user_id"]
        vault = Credentials(Fernet.generate_key().decode())
        service = ModelConfigurations(store, vault, Provider())
        first = await service.save(owner, BASE_URL, "model", "secret-marker", None)
        work = await Works(store).create(owner, "配置验证", str(uuid4()))
        reference = str(uuid4())
        async with store.transaction() as db:
            assert await service.acquire(db, owner, reference) == first["id"]
            await db.task_insert(
                reference, owner, work["id"], "preparation", str(uuid4()), {}, {}, first["id"]
            )
            raw = await db.config_get(owner, first["id"])
            assert "secret-marker" not in str(raw)
        results = await asyncio.gather(
            *(service.save(owner, BASE_URL, "new", "new-key", first["id"]) for _ in range(8)),
            return_exceptions=True,
        )
        assert sum(isinstance(x, dict) for x in results) == 1
        assert all(isinstance(x, dict) or isinstance(x, Fault) and x.status == 409 for x in results)
        recreated = ModelConfigurations(PostgresStore(store.url), vault, Provider())
        assert (await recreated.current(owner))["id"] != first["id"]
        assert (await recreated.bound(owner, first["id"]))["key"] == "secret-marker"
        async with store.transaction() as db:
            await recreated.release(db, reference)
        async with store.transaction() as db:
            assert (await db.config_get(owner, first["id"]))["ciphertext"] is None

    asyncio.run(scenario())
