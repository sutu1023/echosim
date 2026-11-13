import json
from uuid import uuid4


class StoryQueries:
    async def story_lock(self, work):
        await self.lock("story:" + work)

    async def story_head(self, owner, work):
        return await self.one(
            "SELECT * FROM work_story_heads WHERE user_id=%s AND work_id=%s", (owner, work)
        )

    async def story_version(self, owner, work, version):
        return await self.one(
            "SELECT * FROM story_preparation_versions WHERE user_id=%s AND work_id=%s AND id=%s",
            (owner, work, version),
        )

    async def story_history(self, owner, work, before=None):
        return await self.all(
            "SELECT * FROM story_preparation_versions WHERE user_id=%s AND work_id=%s AND "
            "(%s::timestamptz IS NULL OR created_at<%s) ORDER BY created_at DESC,id DESC "
            "LIMIT 100",
            (owner, work, before, before),
        )

    async def task_get(self, owner, work, task):
        return await self.one(
            "SELECT * FROM story_tasks WHERE user_id=%s AND work_id=%s AND id=%s",
            (owner, work, task),
        )

    async def task_existing(self, owner, work, kind, key):
        return await self.one(
            "SELECT * FROM story_tasks WHERE user_id=%s AND work_id=%s AND kind=%s AND "
            "request_key=%s",
            (owner, work, kind, key),
        )

    async def task_active(self, work):
        return await self.one(
            "SELECT * FROM story_tasks WHERE work_id=%s AND state IN "
            "('queued','running','stopping')",
            (work,),
        )

    async def task_list(self, owner, work):
        return await self.all(
            "SELECT * FROM story_tasks WHERE user_id=%s AND work_id=%s ORDER BY "
            "created_at DESC,id DESC LIMIT 100",
            (owner, work),
        )

    async def task_insert(self, task, owner, work, kind, key, request, payload, config):
        await self.execute(
            "INSERT INTO "
            "story_tasks(id,user_id,work_id,kind,request_key,"
            "request_data,input_data,configuration_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (task, owner, work, kind, key, json.dumps(request), json.dumps(payload), config),
        )

    async def story_publish(self, task, content):
        await self.execute(
            "INSERT INTO story_preparation_versions(id,user_id,work_id,content) VALUES "
            "(%s,%s,%s,%s)",
            (task["id"], task["user_id"], task["work_id"], json.dumps(content)),
        )
        await self.execute(
            "INSERT INTO work_story_heads(work_id,user_id,candidate_id) VALUES (%s,%s,%s) "
            "ON CONFLICT(work_id) DO UPDATE SET "
            "candidate_id=EXCLUDED.candidate_id,updated_at=now()",
            (task["work_id"], task["user_id"], task["id"]),
        )

    async def story_confirm(self, owner, work, version):
        await self.execute(
            "UPDATE work_story_heads SET confirmed_id=%s,updated_at=now() WHERE "
            "user_id=%s AND work_id=%s",
            (version, owner, work),
        )

    async def task_claim(self):
        row = await self.one(
            "SELECT * FROM story_tasks WHERE state='queued' ORDER BY created_at,id FOR "
            "UPDATE SKIP LOCKED LIMIT 1"
        )
        if row:
            row["fence"] = str(uuid4())
            await self.execute(
                "UPDATE story_tasks SET state='running',fence=%s,lease_until=now()+interval "
                "'30 seconds',updated_at=now() WHERE id=%s",
                (row["fence"], row["id"]),
            )
        return row

    async def task_heartbeat(self, task):
        await self.execute(
            "UPDATE story_tasks SET lease_until=now()+interval '30 seconds' WHERE id=%s "
            "AND fence=%s AND state IN ('running','stopping')",
            (task["id"], task["fence"]),
        )

    async def task_guard(self, task):
        return await self.one(
            "SELECT * FROM story_tasks WHERE id=%s AND fence=%s AND state IN "
            "('running','stopping') AND lease_until>now() FOR UPDATE",
            (task["id"], task["fence"]),
        )

    async def task_finish(self, task, state, error=None):
        await self.execute(
            "UPDATE story_tasks SET state=%s,error_code=%s,updated_at=now() WHERE id=%s",
            (state, error, task["id"]),
        )
        await self.config_release(task["id"])
        await self.config_cleanup()

    async def task_reap(self):
        rows = await self.all(
            "SELECT * FROM story_tasks WHERE state IN ('running','stopping') AND "
            "lease_until<=now() FOR UPDATE SKIP LOCKED"
        )
        for row in rows:
            await self.task_finish(row, "failed", "WORKER_LOST")
