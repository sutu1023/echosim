import asyncio
import json
import os
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from echosim.adapters.credentials import Credentials
from echosim.adapters.passwords import Passwords
from echosim.adapters.store import PostgresStore
from echosim.domain.accounts import Fault
from echosim.services.accounts import Accounts
from echosim.services.model_config import BASE_URL, ModelConfigurations
from echosim.services.story import Stories
from echosim.services.story_worker import StoryWorker
from echosim.services.works import Works

CONTENT = dict(
    synopsis="灯塔熄灭",
    world_rules="没有魔法",
    outline="恢复灯光",
    characters=[
        dict(
            id=str(i),
            name="角色" + str(i),
            identity="守塔人",
            goal="恢复灯光",
            background="海边居民",
            public_information="认识灯塔",
            private_knowledge="私藏的钥匙" + str(i),
        )
        for i in range(1, 4)
    ],
)


class Provider:
    async def generate(self, key, model, messages, **kwargs):
        return {
            "text": json.dumps(CONTENT)
            if kwargs.get("prompt_version") == "preparation-v2"
            else "ok"
        }


def test_story_persistence_exclusivity_confirmation_and_fencing():
    async def scenario():
        store = PostgresStore(os.environ["ECHOSIM_TEST_DATABASE_URL"])
        await store.migrate()
        auth = Accounts(store, Passwords())
        await auth.provision("story-owner", "integration-password")
        session = await auth.login("story-owner", "integration-password", "story")
        owner = (await auth.current(session["token"]))["user_id"]
        provider = Provider()
        config = ModelConfigurations(store, Credentials(Fernet.generate_key().decode()), provider)
        await config.save(owner, BASE_URL, "test", "key", None)
        service = Stories(store, config)
        work = (await Works(store).create(owner, "故事验证", str(uuid4())))["id"]
        key = str(uuid4())
        data = {"idea": "灯塔", "base_version_id": None}
        # Remove the earlier configuration-contract test's synthetic queue item.
        async with store.transaction() as db:
            await db.execute(
                "UPDATE story_tasks SET state='failed' WHERE kind='preparation' "
                "AND input_data='{}'::jsonb"
            )
        rows = await asyncio.gather(
            *(service.submit(owner, work, "preparation", key, data) for _ in range(6))
        )
        assert len({r["id"] for r in rows}) == 1
        with pytest.raises(Fault, match="IDEMPOTENCY_CONFLICT"):
            await service.submit(owner, work, "preparation", key, {"idea": "不同"})
        with pytest.raises(Fault, match="WORK_BUSY"):
            await service.submit(owner, work, "preparation", str(uuid4()), data)
        with pytest.raises(Fault, match="NOT_FOUND"):
            await service.history(str(uuid4()), work)
        worker = StoryWorker(store, config, provider)
        assert await worker.tick()
        first = rows[0]["id"]
        assert (await service.task(owner, work, first))["state"] == "succeeded"
        assert (await service.history(owner, work))["head"]["confirmed_id"] is None
        await service.confirm(owner, work, first, first)
        await service.confirm(owner, work, first, first)
        second = await service.submit(
            owner, work, "preparation", str(uuid4()), {"idea": "改成雨夜", "base_version_id": first}
        )
        await worker.tick()
        with pytest.raises(Fault, match="CANDIDATE_CONFLICT"):
            await service.confirm(owner, work, first, first)
        assert (await service.history(owner, work))["head"]["confirmed_id"] == first
        await service.confirm(owner, work, second["id"], second["id"])

        class BrokenProvider:
            async def generate(self, *args, **kwargs):
                from echosim.domain.model_connection import ModelFailure

                raise ModelFailure("MODEL_AUTH_FAILED")

        failed = await service.submit(owner, work, "preparation", str(uuid4()), data)
        await StoryWorker(store, config, BrokenProvider()).tick()
        assert (await service.task(owner, work, failed["id"]))["state"] == "failed"
        assert (await service.history(owner, work))["head"]["candidate_id"] == second["id"]
        third = await service.submit(owner, work, "preparation", str(uuid4()), data)
        async with store.transaction() as db:
            claimed = await db.task_claim()
            assert claimed["id"] == third["id"]
            await db.execute(
                "UPDATE story_tasks SET lease_until=now()-interval '1 second' WHERE id=%s",
                (third["id"],),
            )
        async with store.transaction() as db:
            await db.task_reap()
            assert await db.task_guard(claimed) is None
        assert (await service.task(owner, work, third["id"]))["state"] == "failed"
        history = await Stories(PostgresStore(store.url), config).history(owner, work)
        assert len(history["versions"]) == 2
        assert history["head"]["confirmed_id"] == second["id"]

    asyncio.run(scenario())
