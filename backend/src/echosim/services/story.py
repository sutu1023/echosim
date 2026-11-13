from uuid import uuid4

from echosim.domain.accounts import Fault
from echosim.services.works import identifier


def task_public(row):
    return {
        k: row[k] for k in ("id", "kind", "state", "error_code", "configuration_id", "created_at")
    }


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
            if await db.task_active(work):
                raise Fault("WORK_BUSY", 409)
            head = await db.story_head(owner, work)
            if head["candidate_id"] != version or expected != version:
                raise Fault("CANDIDATE_CONFLICT", 409)
            await db.story_confirm(owner, work, version)
            return {"confirmed_id": version}

    async def task(self, owner, work, task):
        async with self.store.transaction() as db:
            await self.owned(db, owner, work)
            row = await db.task_get(owner, work, identifier(task, "NOT_FOUND", 404))
            if not row:
                raise Fault("NOT_FOUND", 404)
            return task_public(row)
