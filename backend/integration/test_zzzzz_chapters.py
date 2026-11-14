import asyncio
import json
import os
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from test_zzz_simulation import Provider

from echosim.adapters.credentials import Credentials
from echosim.adapters.passwords import Passwords
from echosim.adapters.store import PostgresStore
from echosim.domain.accounts import Fault
from echosim.services.accounts import Accounts
from echosim.services.adoption import Adoptions
from echosim.services.chapters import Chapters
from echosim.services.model_config import BASE_URL, ModelConfigurations
from echosim.services.simulation import Simulations, role_canon
from echosim.services.story import Stories
from echosim.services.story_facts import StoryFacts
from echosim.services.story_graph import StoryGraph
from echosim.services.story_worker import StoryWorker
from echosim.services.works import Works

TRANSFER = "角色1把钥匙交给角色3。"


class ChapterProvider(Provider):
    def __init__(self):
        super().__init__()
        self.streamed = asyncio.Event()
        self.finish = asyncio.Event()

    async def generate(self, key, model, messages, **kwargs):
        prompt = kwargs["prompt_version"]
        if prompt == "chapter-prose-v2":
            await kwargs["on_text"]("钥匙\n")
            self.streamed.set()
            await self.finish.wait()
            return {"text": "钥匙\n角色1把钥匙交给角色2。"}
        if prompt == "chapter-fidelity-v2":
            data = json.loads(messages[-1]["content"])
            return {"text": json.dumps({"accept": "改写" not in data["正文"]})}
        if prompt == "chapter-review-v2":
            data = json.loads(messages[-1]["content"])
            result = (
                {"conflict": True, "reason": "轮前钥匙由角色1持有"}
                if "一直" in data["本轮正文"]
                else {"conflict": False, "events": [{"text": TRANSFER, "visibility": ["1", "3"]}]}
            )
            return {"text": json.dumps(result)}
        if prompt == "story-facts-v3":
            data = json.loads(messages[-1]["content"])
            source = next(iter(data["sources"]))
            if source.startswith("chapter:"):
                delta = {
                    "entities": [
                        dict(id="key", kind="object", name="钥匙", source=source, quote="钥匙")
                    ],
                    "relations": [
                        dict(
                            id="holder",
                            subject="key",
                            predicate="持有者",
                            object="3",
                            source=source,
                            quote=TRANSFER,
                        )
                    ],
                    "retire": [],
                    "conflict": False,
                }
                return {"text": json.dumps(delta)}
        return await super().generate(key, model, messages, **kwargs)


def test_chapter_lifecycle():
    asyncio.run(lifecycle())


async def lifecycle():
    store = PostgresStore(os.environ["ECHOSIM_TEST_DATABASE_URL"])
    await store.migrate()
    auth = Accounts(store, Passwords())
    await auth.provision("chapter-owner", "integration-password")
    session = await auth.login("chapter-owner", "integration-password", "chapter")
    owner = (await auth.current(session["token"]))["user_id"]
    provider = ChapterProvider()
    configs = ModelConfigurations(store, Credentials(Fernet.generate_key().decode()), provider)
    await configs.save(owner, BASE_URL, "test", "key", None)
    work = (await Works(store).create(owner, "章节验证", str(uuid4())))["id"]
    stories = Stories(store, configs)
    facts = StoryFacts(provider)
    chapters = Chapters(stories, facts)
    adoption = Adoptions(stories, facts)
    worker = StoryWorker(
        store,
        configs,
        provider,
        Simulations(stories, provider).run,
        adoption.run,
        StoryGraph(stories, facts).run,
        chapters.run,
    )
    prep = await stories.submit(owner, work, "preparation", str(uuid4()), {"idea": "钥匙"})
    await worker.tick()
    await stories.confirm(owner, work, prep["id"], prep["id"])
    await worker.tick()
    sim = (await stories.submit(owner, work, "simulation", str(uuid4()), {}))["id"]

    async def change(action, version, **data):
        return await chapters.change(owner, work, sim, str(uuid4()), action, version, **data)

    async def read():
        return await chapters.read(owner, work, sim)

    with pytest.raises(Fault, match="RESULT_INELIGIBLE"):
        await change("generate", 0)
    await worker.tick()
    for who, simulation in [(str(uuid4()), sim), (owner, str(uuid4()))]:
        with pytest.raises(Fault, match="NOT_FOUND"):
            await chapters.change(who, work, simulation, str(uuid4()), "generate", 0)
        with pytest.raises(Fault, match="NOT_FOUND"):
            await chapters.read(who, work, simulation)
    await change("generate", 0)
    running = asyncio.create_task(worker.tick())
    await asyncio.wait_for(provider.streamed.wait(), 5)
    assert (await read())["job"]["text"] == "钥匙\n"
    provider.finish.set()
    await running
    assert (await read())["draft"]["version"] == 1
    candidate = await change("generate", 1)
    await worker.tick()
    assert (await read())["job"]["candidate"]
    await change("save", 1, title="归属", body="钥匙一直在角色3手里。")
    with pytest.raises(Fault, match="CHAPTER_STALE"):
        await change("replace", 1, candidate=candidate["id"])
    await change("review", 2)
    await worker.tick()
    assert (await read())["job"]["error_code"] == "CHAPTER_CONFLICT"
    await change("save", 2, title="归属", body=TRANSFER)
    review = await change("review", 3)
    await worker.tick()
    data = dict(expected_canon_revision=0, acknowledge_irreversible=True, review_id=review["id"])
    await change("save", 3, title="交接", body=TRANSFER)
    with pytest.raises(Fault, match="CHAPTER_STALE"):
        await adoption.choose(owner, work, sim, str(uuid4()), data)
    review = await change("review", 4)
    await worker.tick()
    assert (await read())["job"]["changes"] == [TRANSFER]
    data["review_id"] = review["id"]
    async with store.transaction() as db:
        await db.execute(
            "CREATE FUNCTION reject_chapter_commit() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'rollback test'; END $$"
        )
        await db.execute(
            "CREATE TRIGGER fail_chapter_head BEFORE UPDATE ON work_story_heads "
            "FOR EACH ROW EXECUTE FUNCTION reject_chapter_commit()"
        )
    await adoption.choose(owner, work, sim, str(uuid4()), data)
    await worker.tick()
    async with store.transaction() as db:
        assert (await db.story_head(owner, work))["canon_revision"] == 0
        assert not await db.adopted_task(owner, work, sim)
        assert (await db.task_get(owner, work, sim))["decision"] == "pending"
        await db.execute("DROP TRIGGER fail_chapter_head ON work_story_heads")
    key = str(uuid4())
    job = await adoption.choose(owner, work, sim, key, data)
    await worker.tick()
    assert (await adoption.choose(owner, work, sim, key, data))["id"] == job["id"]
    assert (await read())["decision"] == "adopted"
    await change("save", 4, title="交接", body="改写已采纳的收件人")
    await worker.tick()
    assert (await read())["draft"]["body"] == TRANSFER
    await change("save", 4, title="再读交接", body=TRANSFER)
    await worker.tick()
    assert (await read())["draft"]["version"] == 5
    sim = (await stories.submit(owner, work, "simulation", str(uuid4()), {}))["id"]
    async with store.transaction() as db:
        canon = (await db.task_get(owner, work, sim))["input_data"]["canon"]
        assert canon["relations"][-1]["object"] == "3"
        assert role_canon(canon, "3")["memories"][-1]["text"] == TRANSFER
        assert not role_canon(canon, "2")["memories"]
    await worker.tick()
    await change("generate", 0)
    await worker.tick()
    candidate = await change("generate", 1)
    await worker.tick()
    await change("replace", 1, candidate=candidate["id"])
    assert (await read())["draft"]["version"] == 2
    await adoption.choose(owner, work, sim, str(uuid4()), {}, discard=True)
    assert (await read())["draft"]
    with pytest.raises(Fault, match="RESULT_INELIGIBLE"):
        await change("save", 1, title="历史", body="拒绝修改")
