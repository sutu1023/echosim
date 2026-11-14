import json
from uuid import uuid4

from echosim.domain.accounts import Fault
from echosim.services.story_graph import enqueue_graph
from echosim.services.works import identifier


def task_public(row):
    result = {
        k: row[k]
        for k in ("id", "kind", "state", "error_code", "configuration_id", "created_at", "decision")
    }
    result["source_simulation_id"] = row.get("input_data", {}).get("simulation_id")
    return result


class Stories:
    def __init__(self, store, configurations):
        self.store, self.configurations = store, configurations

    async def owned(self, db, owner, work):
        identifier(work, "NOT_FOUND", 404)
        if not await db.work(owner, work):
            raise Fault("NOT_FOUND", 404)

    async def submit(self, owner, work, kind, key, data):
        key = identifier(key)
        if kind == "preparation":
            idea = data.get("idea")
            if not isinstance(idea, str) or not 1 <= len(idea.strip()) <= 2000:
                raise Fault("INVALID_REQUEST")
        async with self.store.transaction() as db:
            await self.owned(db, owner, work)
            await db.story_lock(work)
            previous = await db.task_existing(owner, work, kind, key)
            if previous:
                if previous["request_data"] != data:
                    raise Fault("IDEMPOTENCY_CONFLICT", 409)
                return task_public(previous)
            if await db.task_active(work):
                raise Fault("WORK_BUSY", 409)
            payload = dict(data)
            head = await db.story_head(owner, work)
            if head and head.get("pending_simulation_id"):
                raise Fault("RESULT_PENDING", 409)
            if kind == "preparation" and head and head.get("canon_revision", 0):
                raise Fault("PREPARATION_FROZEN", 409)
            if kind == "simulation":
                if not head or not head.get("confirmed_id"):
                    raise Fault("CONFIRMATION_REQUIRED", 409)
                revision = (head or {}).get("canon_revision", 0)
                canon = (
                    await db.canon(owner, work, revision)
                    if revision
                    else await db.graph_baseline(owner, work, (head or {}).get("confirmed_id"))
                )
                if not canon:
                    raise Fault("GRAPH_NOT_READY", 409)
                if len(json.dumps(canon, ensure_ascii=False)) > 60000:
                    raise Fault("STORY_CAPACITY", 409)
                payload.update(canon_revision=revision, canon=canon)
            version = (
                data.get("base_version_id")
                if kind == "preparation"
                else (head or {}).get("confirmed_id")
            )
            if kind == "simulation" and not version:
                raise Fault("CONFIRMATION_REQUIRED", 409)
            if version:
                row = await db.story_version(owner, work, identifier(version, "NOT_FOUND", 404))
                if not row:
                    raise Fault("NOT_FOUND", 404)
                payload.update(version_id=version, story=row["content"])
            task = str(uuid4())
            config = await self.configurations.acquire(db, owner, task)
            await db.task_insert(task, owner, work, kind, key, data, payload, config)
            return task_public(await db.task_get(owner, work, task))

    async def history(self, owner, work):
        async with self.store.transaction() as db:
            await self.owned(db, owner, work)
            return {
                "head": await db.story_head(owner, work),
                "versions": await db.story_history(owner, work),
                "tasks": [task_public(t) for t in await db.task_list(owner, work)],
            }

    async def confirm(self, owner, work, version, expected):
        version = identifier(version, "NOT_FOUND", 404)
        async with self.store.transaction() as db:
            await self.owned(db, owner, work)
            await db.story_lock(work)
            if not await db.story_version(owner, work, version):
                raise Fault("NOT_FOUND", 404)
            head = await db.story_head(owner, work)
            if head.get("canon_revision", 0):
                raise Fault("PREPARATION_FROZEN", 409)
            if head.get("pending_simulation_id"):
                raise Fault("RESULT_PENDING", 409)
            if head["candidate_id"] != version or expected != version:
                raise Fault("CANDIDATE_CONFLICT", 409)
            if head["confirmed_id"] == version:
                return {"confirmed_id": version}
            if await db.task_active(work):
                raise Fault("WORK_BUSY", 409)
            await db.story_confirm(owner, work, version)
            await enqueue_graph(db, self.configurations, owner, work, version)
            return {"confirmed_id": version}

    async def task(self, owner, work, task):
        async with self.store.transaction() as db:
            await self.owned(db, owner, work)
            row = await db.task_get(owner, work, identifier(task, "NOT_FOUND", 404))
            if not row:
                raise Fault("NOT_FOUND", 404)
            return task_public(row)
