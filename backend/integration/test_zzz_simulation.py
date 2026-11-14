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
from echosim.services.adoption import Adoptions
from echosim.services.model_config import BASE_URL, ModelConfigurations
from echosim.services.simulation import Simulations
from echosim.services.story import Stories
from echosim.services.story_facts import StoryFacts
from echosim.services.story_graph import StoryGraph
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
        elif kind == "story-facts-v3":
            value = dict(entities=[], relations=[], retire=[], conflict=False)
        elif kind == "architect-v8":
            assert "私藏的钥匙" not in json.dumps(context, ensure_ascii=False)
            value = dict(scene="灯室", next_actor="1", finished=False, reason="继续检查")
        elif kind == "role-action-v8":
            actor = context["actor_id"]
            for other in {"1", "2", "3"} - {actor}:
                assert "私藏的钥匙" + other not in json.dumps(context, ensure_ascii=False)
            if self.pause:
                self.entered.set()
                await self.release.wait()
            value = dict(
                actor_id=actor,
                segments=[
                    {"kind": "narration", "text": "检查线路", "visibility": ["1", "2", "3"]},
                    {
                        "kind": "dialogue",
                        "speaker_id": actor,
                        "text": "这里有裂纹",
                        "visibility": ["1", "2", "3"],
                    },
                    {"kind": "narration", "text": "看见旧线路", "visibility": ["1", "2", "3"]},
                ],
                references=[],
                progress="发现新问题",
            )

        else:
            value = {"accept": True, "reasons": "consistent"}
        return {"text": json.dumps(value)}


def test_twenty_four_events_stop_replay_and_isolation():
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
        worker = StoryWorker(
            store,
            configs,
            provider,
            simulations.run,
            graph=StoryGraph(stories, StoryFacts(provider)).run,
        )
        work = (await Works(store).create(owner, "推演验证", str(uuid4())))["id"]
        with pytest.raises(Fault, match="CONFIRMATION_REQUIRED"):
            await stories.submit(owner, work, "simulation", str(uuid4()), {})
        prep = await stories.submit(owner, work, "preparation", str(uuid4()), {"idea": "灯塔"})
        await worker.tick()
        await stories.confirm(owner, work, prep["id"], prep["id"])
        await worker.tick()
        key = str(uuid4())
        task = await stories.submit(owner, work, "simulation", key, {})
        assert (await stories.submit(owner, work, "simulation", key, {}))["id"] == task["id"]
        await worker.tick()
        result = await simulations.read(owner, work, task["id"])
        assert result["state"] == "succeeded"
        assert [e["sequence"] for e in result["events"]] == list(range(1, 25))
        assert len((await simulations.read(owner, work, task["id"], 4))["events"]) == 20
        with pytest.raises(Fault, match="INVALID_REQUEST"):
            await simulations.read(owner, work, task["id"], 25)
        with pytest.raises(Fault, match="SERVICE_UNAVAILABLE"):
            async with store.transaction() as db:
                await db.simulation_append(
                    {"id": task["id"], "user_id": owner, "work_id": work}, 25, {}
                )
        await store.migrate()
        assert len((await simulations.read(owner, work, task["id"]))["events"]) == 24
        for method in [simulations.read, simulations.stop]:
            with pytest.raises(Fault, match="NOT_FOUND"):
                await method(str(uuid4()), work, task["id"])
        assert (await simulations.stop(owner, work, task["id"]))["state"] == "succeeded"
        await Adoptions(stories, None).choose(
            owner, work, task["id"], str(uuid4()), {}, discard=True
        )
        stopped = await stories.submit(owner, work, "simulation", str(uuid4()), {})
        assert (await simulations.stop(owner, work, stopped["id"]))["state"] == "stopped"
        assert (await simulations.read(owner, work, stopped["id"]))["decision"] == "ineligible"
        assert not await worker.tick()
        from unittest.mock import patch

        timeout_task = await stories.submit(owner, work, "simulation", str(uuid4()), {})
        timeout = asyncio.timeout

        async def stalled(*args, **kwargs):
            await asyncio.Event().wait()

        with (
            patch.object(simulations, "ask", stalled),
            patch.object(
                asyncio, "timeout", lambda seconds: timeout(0 if seconds == 240 else seconds)
            ),
        ):
            await worker.tick()
        result = await simulations.read(owner, work, timeout_task["id"])
        assert result["error_code"] == "TASK_TIMEOUT" and not result["events"]
        expired = await stories.submit(owner, work, "simulation", str(uuid4()), {})
        async with store.transaction() as db:
            stale = await db.task_claim()
            await db.execute(
                "UPDATE story_tasks SET lease_until=now()-interval '1 second' WHERE id=%s",
                (expired["id"],),
            )
            await db.task_reap()
        count = len(provider.seen)
        await simulations.run(stale, {})
        expired_result = await simulations.read(owner, work, expired["id"])
        assert expired_result["error_code"] == "WORKER_LOST" and not expired_result["events"]
        assert len(provider.seen) == count
        running = await stories.submit(owner, work, "simulation", str(uuid4()), {})
        provider.pause = True
        execution = asyncio.create_task(worker.tick())
        await asyncio.wait_for(provider.entered.wait(), 3)
        assert (await simulations.stop(owner, work, running["id"]))["state"] == "stopping"
        provider.release.set()
        await execution
        result = await simulations.read(owner, work, running["id"])
        assert result["state"] == "stopped" and len(result["events"]) == 1
        assert result["decision"] == "pending"
        with pytest.raises(Fault, match="RESULT_PENDING"):
            await stories.submit(owner, work, "simulation", str(uuid4()), {})
        legacy = str(uuid4())
        old_content = dict(
            actor_id="1",
            action="旧动作",
            dialogue="旧对白",
            result="旧结果",
            visibility=["1", "2", "3"],
            round=1,
            references=[],
        )
        async with store.transaction() as db:
            config = await configs.acquire(db, owner, legacy)
            await db.task_insert(
                legacy,
                owner,
                work,
                "simulation",
                legacy,
                {},
                {"story": CONTENT, "version_id": prep["id"]},
                config,
            )
            row = await db.task_get(owner, work, legacy)
            for sequence in range(1, 10):
                await db.simulation_append(row, sequence, old_content)
            await db.task_finish(row, "succeeded")
            stored = await db.simulation_events(owner, work, legacy)
        await store.migrate()
        read = await simulations.read(owner, work, legacy)
        assert read["decision"] == "ineligible" and len(read["events"]) == 9
        assert [e["id"] for e in read["events"]] == [e["id"] for e in stored]
        async with store.transaction() as db:
            assert all(
                e["content"] == old_content for e in await db.simulation_events(owner, work, legacy)
            )

    asyncio.run(scenario())
