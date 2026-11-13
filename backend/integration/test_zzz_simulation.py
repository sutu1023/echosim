import asyncio
import json
import os
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from test_zz_story import CONTENT

from echosim.adapters.credentials import Credentials
from echosim.adapters.passwords import Passwords
from echosim.adapters.store import PostgresStore
from echosim.domain.accounts import Fault
from echosim.services.accounts import Accounts
from echosim.services.model_config import BASE_URL, ModelConfigurations
from echosim.services.simulation import Simulations
from echosim.services.story import Stories
from echosim.services.story_worker import StoryWorker
from echosim.services.works import Works


class Provider:
    def __init__(self):
        self.seen = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.pause = False

    async def generate(self, key, model, messages, **kwargs):
        kind = kwargs.get("prompt_version")
        context = json.loads(messages[-1]["content"]) if kind != "configuration-test-v1" else {}
        self.seen.append((kind, context))
        if kind == "configuration-test-v1":
            return {"text": "ok"}
        if kind == "preparation-v2":
            value = CONTENT
        elif kind == "architect-v1":
            assert "私藏的钥匙" not in json.dumps(context, ensure_ascii=False)
            value = dict(scene="灯室", order=["1", "2", "3"])
        elif kind == "role-action-v2":
            actor = context["actor_id"]
            for other in {"1", "2", "3"} - {actor}:
                assert "私藏的钥匙" + other not in json.dumps(context, ensure_ascii=False)
            if self.pause:
                self.entered.set()
                await self.release.wait()
            value = dict(
                actor_id=actor,
                action="检查线路",
                dialogue="这里有裂纹",
                result="看见旧线路",
                visibility=["1", "2", "3"],
                references=[],
            )
        else:
            value = {"accept": True, "reasons": "consistent"}
        return {"text": json.dumps(value)}


def test_nine_events_stop_replay_and_isolation():
    async def scenario():
        store = PostgresStore(os.environ["ECHOSIM_TEST_DATABASE_URL"])
        await store.migrate()
        auth = Accounts(store, Passwords())
        await auth.provision("simulation-owner", "integration-password")
        session = await auth.login("simulation-owner", "integration-password", "sim")
        owner = (await auth.current(session["token"]))["user_id"]
        provider = Provider()
        configs = ModelConfigurations(store, Credentials(Fernet.generate_key().decode()), provider)
        await configs.save(owner, BASE_URL, "test", "key", None)
        stories = Stories(store, configs)
        simulations = Simulations(stories, provider)
        worker = StoryWorker(store, configs, provider, simulations.run)
        work = (await Works(store).create(owner, "推演验证", str(uuid4())))["id"]
        with pytest.raises(Fault, match="CONFIRMATION_REQUIRED"):
            await stories.submit(owner, work, "simulation", str(uuid4()), {})
        prep = await stories.submit(owner, work, "preparation", str(uuid4()), {"idea": "灯塔"})
        await worker.tick()
        await stories.confirm(owner, work, prep["id"], prep["id"])
        key = str(uuid4())
        task = await stories.submit(owner, work, "simulation", key, {})
        assert (await stories.submit(owner, work, "simulation", key, {}))["id"] == task["id"]
        await worker.tick()
        result = await simulations.read(owner, work, task["id"])
        assert result["state"] == "succeeded"
        assert [e["sequence"] for e in result["events"]] == list(range(1, 10))
        assert len((await simulations.read(owner, work, task["id"], 4))["events"]) == 5
        for method in [simulations.read, simulations.stop]:
            with pytest.raises(Fault, match="NOT_FOUND"):
                await method(str(uuid4()), work, task["id"])
        assert (await simulations.stop(owner, work, task["id"]))["state"] == "succeeded"
        stopped = await stories.submit(owner, work, "simulation", str(uuid4()), {})
        assert (await simulations.stop(owner, work, stopped["id"]))["state"] == "stopped"
        assert not await worker.tick()
        running = await stories.submit(owner, work, "simulation", str(uuid4()), {})
        provider.pause = True
        execution = asyncio.create_task(worker.tick())
        await asyncio.wait_for(provider.entered.wait(), 3)
        assert (await simulations.stop(owner, work, running["id"]))["state"] == "stopping"
        provider.release.set()
        await execution
        result = await simulations.read(owner, work, running["id"])
        assert result["state"] == "stopped" and len(result["events"]) == 1

    asyncio.run(scenario())
