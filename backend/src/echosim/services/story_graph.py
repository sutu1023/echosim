from uuid import uuid4

from echosim.domain.accounts import Fault
from echosim.domain.story_facts import setting_sources


async def enqueue_graph(db, configurations, owner, work, version):
    if await db.graph_baseline(owner, work, version):
        return
    row = await db.story_version(owner, work, version)
    task = str(uuid4())
    config = await configurations.acquire(db, owner, task)
    await db.task_insert(
        task,
        owner,
        work,
        "graph",
        task,
        {},
        {"version_id": version, "story": row["content"]},
        config,
    )


class StoryGraph:
    def __init__(self, stories, facts):
        self.stories, self.facts = stories, facts
        self.store = stories.store

    async def read(self, owner, work):
        async with self.store.transaction() as db:
            await self.stories.owned(db, owner, work)
            head = await db.story_head(owner, work)
            version = (head or {}).get("confirmed_id")
            revision = (head or {}).get("canon_revision", 0)
            content = (
                await db.canon(owner, work, revision)
                if revision
                else await db.graph_baseline(owner, work, version)
            )
            task = await db.graph_task(owner, work, version) if version else None
            return {
                "version_id": version,
                "canon_revision": revision,
                "state": "ready" if content else (task or {}).get("state", "missing"),
                "error_code": (task or {}).get("error_code"),
                "content": content,
            }

    async def build(self, owner, work):
        async with self.store.transaction() as db:
            await self.stories.owned(db, owner, work)
            await db.story_lock(work)
            head = await db.story_head(owner, work)
            if not head or not head["confirmed_id"]:
                raise Fault("CONFIRMATION_REQUIRED", 409)
            if head["canon_revision"] or await db.graph_baseline(owner, work, head["confirmed_id"]):
                return {"state": "ready"}
            if await db.task_active(work):
                raise Fault("WORK_BUSY", 409)
            await enqueue_graph(db, self.stories.configurations, owner, work, head["confirmed_id"])
            return {"state": "queued"}

    async def run(self, task, config):
        data = task["input_data"]
        sources = setting_sources(data["story"], data["version_id"])
        entities = [
            {
                "id": c["id"],
                "kind": "character",
                "name": c["name"],
                "source": f"setting:{data['version_id']}:{c['id']}:name",
                "quote": c["name"],
                "visibility": ["1", "2", "3"],
                "active": True,
            }
            for c in data["story"]["characters"]
        ]
        base = {"entities": entities, "relations": []}
        result = await self.facts.build(task, config, base, sources)
        content = {"schema_version": 1, **result, "sources": sources, "memories": [], "events": []}
        async with self.store.transaction() as db:
            await db.story_lock(task["work_id"])
            if not await db.task_guard(task):
                return
            head = await db.story_head(task["user_id"], task["work_id"])
            if head["confirmed_id"] != data["version_id"] or head["canon_revision"]:
                raise Fault("CANON_CONFLICT", 409)
            await db.graph_publish(task, content)
            await db.task_finish(task, "succeeded")
