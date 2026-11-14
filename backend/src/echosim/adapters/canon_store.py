import json
from uuid import uuid4


class CanonQueries:
    async def canon(self, owner, work, revision):
        row = await self.one(
            "SELECT content FROM story_canons WHERE user_id=%s AND work_id=%s AND revision=%s",
            (owner, work, revision),
        )
        return (
            row["content"]
            if row
            else {"entities": [], "relations": [], "memories": [], "sources": {}, "events": []}
        )

    async def decision(self, task, value):
        await self.execute(
            "UPDATE story_tasks SET decision=%s,updated_at=now() WHERE id=%s", (value, task["id"])
        )

    async def pending_result(self, task, state):
        if task["kind"] == "simulation" and "canon_revision" in task["input_data"]:
            eligible = state in ("succeeded", "stopped") and await self.one(
                "SELECT id FROM simulation_events WHERE task_id=%s LIMIT 1", (task["id"],)
            )
            await self.decision(task, "pending" if eligible else "ineligible")
            if eligible:
                await self.execute(
                    "UPDATE work_story_heads SET "
                    "pending_simulation_id=%s,updated_at=now() WHERE work_id=%s",
                    (task["id"], task["work_id"]),
                )
        if task["kind"] == "adoption" and state == "failed":
            await self.decision({"id": task["input_data"]["simulation_id"]}, "pending")

    async def adoption_commit(self, task, content):
        data = task["input_data"]
        revision = data["canon_revision"] + 1
        await self.execute(
            "INSERT INTO "
            "story_canons(id,user_id,work_id,revision,simulation_id,adoption_task_id,content) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                task["user_id"],
                task["work_id"],
                revision,
                data["simulation_id"],
                task["id"],
                json.dumps(content),
            ),
        )
        await self.decision({"id": data["simulation_id"]}, "adopted")
        await self.execute(
            "UPDATE work_story_heads SET "
            "canon_revision=%s,pending_simulation_id=NULL,updated_at=now() WHERE work_id=%s",
            (revision, task["work_id"]),
        )

    async def discard(self, owner, work, simulation, key):
        await self.execute(
            "INSERT INTO "
            "story_decisions(id,user_id,work_id,operation,request_key,simulation_id) "
            "VALUES (%s,%s,%s,'discard',%s,%s)",
            (uuid4(), owner, work, key, simulation),
        )
        await self.decision({"id": simulation}, "discarded")
        await self.execute(
            "UPDATE work_story_heads SET pending_simulation_id=NULL,updated_at=now() "
            "WHERE work_id=%s",
            (work,),
        )

    async def discard_previous(self, owner, work, key):
        return await self.one(
            "SELECT simulation_id FROM story_decisions WHERE user_id=%s AND work_id=%s "
            "AND operation='discard' AND request_key=%s",
            (owner, work, key),
        )

    async def adopted_task(self, owner, work, simulation):
        return await self.one(
            "SELECT adoption_task_id FROM story_canons WHERE user_id=%s AND work_id=%s "
            "AND simulation_id=%s",
            (owner, work, simulation),
        )
