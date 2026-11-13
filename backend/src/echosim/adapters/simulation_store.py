import json
from uuid import uuid4


class SimulationQueries:
    async def simulation_events(self, owner, work, task, after=0):
        return await self.all(
            "SELECT id,sequence,content,created_at FROM simulation_events "
            "WHERE user_id=%s AND work_id=%s AND task_id=%s AND sequence>%s "
            "ORDER BY sequence LIMIT 100",
            (owner, work, task, after),
        )

    async def simulation_append(self, task, sequence, content):
        await self.execute(
            "INSERT INTO simulation_events(id,user_id,work_id,task_id,sequence,content) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (uuid4(), task["user_id"], task["work_id"], task["id"], sequence, json.dumps(content)),
        )

    async def simulation_stop(self, task):
        row = await self.one("SELECT state FROM story_tasks WHERE id=%s FOR UPDATE", (task["id"],))
        if row["state"] == "queued":
            await self.task_finish(task, "stopped")
        elif row["state"] == "running":
            await self.execute(
                "UPDATE story_tasks SET state='stopping',updated_at=now() WHERE id=%s",
                (task["id"],),
            )
