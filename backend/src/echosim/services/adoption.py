from uuid import uuid4

from echosim.domain.accounts import Fault
from echosim.domain.narrative import readable
from echosim.domain.story_facts import setting_sources
from echosim.services.story import task_public
from echosim.services.works import identifier


class Adoptions:
    def __init__(self, stories, facts):
        self.stories, self.store, self.facts = stories, stories.store, facts

    async def choose(self, owner, work, simulation, key, data, discard=False):
        identifier(key)
        identifier(simulation, "NOT_FOUND", 404)
        async with self.store.transaction() as db:
            await self.stories.owned(db, owner, work)
            await db.story_lock(work)
            source = await db.task_get(owner, work, simulation)
            if not source or source["kind"] != "simulation":
                raise Fault("NOT_FOUND", 404)
            request = {**data, "simulation_id": simulation}
            if discard:
                previous = await db.discard_previous(owner, work, key)
                if previous:
                    if str(previous["simulation_id"]) != simulation:
                        raise Fault("IDEMPOTENCY_CONFLICT", 409)
                    return task_public(source)
            else:
                previous = await db.task_existing(owner, work, "adoption", key)
                if previous:
                    if previous["request_data"] != request:
                        raise Fault("IDEMPOTENCY_CONFLICT", 409)
                    return task_public(previous)
                if source["decision"] == "adopted":
                    row = await db.adopted_task(owner, work, simulation)
                    return task_public(await db.task_get(owner, work, str(row["adoption_task_id"])))
            if await db.task_active(work):
                raise Fault("WORK_BUSY", 409)
            head = await db.story_head(owner, work)
            if source["decision"] != "pending" or str(head["pending_simulation_id"]) != simulation:
                raise Fault("RESULT_INELIGIBLE", 409)
            if discard:
                await db.discard(owner, work, simulation, key)
                return task_public(await db.task_get(owner, work, simulation))
            if (
                data.get("acknowledge_irreversible") is not True
                or type(data.get("expected_canon_revision")) is not int
            ):
                raise Fault("INVALID_REQUEST")
            if data["expected_canon_revision"] != head["canon_revision"]:
                raise Fault("CANON_CONFLICT", 409)
            draft = await db.chapter(owner, work, simulation)
            review_id = data.get("review_id")
            if draft or review_id:
                if not draft or not review_id:
                    raise Fault("CHAPTER_REVIEW_REQUIRED", 409)
                review = await db.task_get(owner, work, identifier(review_id, "NOT_FOUND", 404))
                if (
                    not review
                    or review["kind"] != "chapter_review"
                    or review["input_data"]["simulation_id"] != simulation
                ):
                    raise Fault("NOT_FOUND", 404)
                if (
                    review["state"] != "succeeded"
                    or review["input_data"]["version"] != draft["version"]
                ):
                    raise Fault("CHAPTER_STALE", 409)
            task = str(uuid4())
            config = await self.stories.configurations.acquire(db, owner, task)
            payload = {**source["input_data"], "simulation_id": simulation}
            if review_id:
                payload["review_id"] = review_id
            await db.task_insert(task, owner, work, "adoption", key, request, payload, config)
            await db.decision(source, "checking")
            return task_public(await db.task_get(owner, work, task))

    async def run(self, task, config):
        owner, work, data = task["user_id"], task["work_id"], task["input_data"]
        async with self.store.transaction() as db:
            base = data["canon"]
            events = await db.simulation_events(owner, work, data["simulation_id"])
            if data.get("review_id"):
                review = await db.task_get(owner, work, data["review_id"])
                content = review["progress"]["canon"]
            else:
                content = None
        if content is None:
            content = await self.original_content(task, config, data, base, events)
        async with self.store.transaction() as db:
            await self.stories.owned(db, owner, work)
            await db.story_lock(work)
            if not await db.task_guard(task):
                return
            if data.get("review_id"):
                draft = await db.chapter(owner, work, data["simulation_id"])
                if draft["version"] != review["input_data"]["version"]:
                    raise Fault("CHAPTER_STALE", 409)
            await self.commit(db, task, data, content)

    async def original_content(self, task, config, data, base, events):
        sources = {**setting_sources(data["story"], data["version_id"]), **base["sources"]}
        memories = list(base["memories"])
        new_sources = {}
        for e in events:
            for part in readable(e)["content"]["segments"]:
                key = f"event:{data['simulation_id']}:{e['sequence']}:{part['segment_id']}"
                sources[key] = {"text": part["text"], "visibility": part["visibility"]}
                new_sources[key] = sources[key]
                memories.append({"source": key, **sources[key]})
        result = await self.facts.build(task, config, base, new_sources)
        content = {
            "schema_version": 1,
            **result,
            "sources": sources,
            "memories": memories,
            "events": base["events"] + [str(e["id"]) for e in events],
        }
        return content

    async def commit(self, db, task, data, content):
        owner, work = task["user_id"], task["work_id"]
        head = await db.story_head(owner, work)
        source = await db.task_get(owner, work, data["simulation_id"])
        if (
            head["canon_revision"] != data["canon_revision"]
            or head["confirmed_id"] != data["version_id"]
            or str(head["pending_simulation_id"]) != data["simulation_id"]
            or source["decision"] != "checking"
        ):
            raise Fault("CANON_CONFLICT", 409)
        await db.adoption_commit(task, content)
        await db.task_finish(task, "succeeded")
