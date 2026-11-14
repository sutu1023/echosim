import json


class GraphQueries:
    async def graph_baseline(self, owner, work, version):
        row = await self.one(
            "SELECT content FROM story_graph_baselines "
            "WHERE user_id=%s AND work_id=%s AND version_id=%s",
            (owner, work, version),
        )
        return row["content"] if row else None

    async def graph_task(self, owner, work, version):
        return await self.one(
            "SELECT state,error_code FROM story_tasks WHERE user_id=%s AND work_id=%s "
            "AND kind='graph' AND input_data->>'version_id'=%s ORDER BY created_at DESC LIMIT 1",
            (owner, work, str(version)),
        )

    async def graph_publish(self, task, content):
        await self.execute(
            "INSERT INTO story_graph_baselines(user_id,work_id,version_id,content) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                task["user_id"],
                task["work_id"],
                task["input_data"]["version_id"],
                json.dumps(content),
            ),
        )
