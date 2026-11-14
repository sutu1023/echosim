import json


class ChapterQueries:
    async def chapter(self, owner, work, simulation):
        return await self.one(
            "SELECT title,body,version FROM chapter_drafts WHERE "
            "user_id=%s AND work_id=%s AND id=%s",
            (owner, work, simulation),
        )

    async def chapter_save(self, owner, work, simulation, title, body):
        await self.execute(
            "INSERT INTO "
            "chapter_drafts(id,user_id,work_id,version,title,body) VALUES"
            " (%s,%s,%s,1,%s,%s) ON CONFLICT(id) DO UPDATE SET "
            "title=excluded.title, body=excluded.body,"
            "version=chapter_drafts.version+1,updated_at=now()",
            (simulation, owner, work, title, body),
        )

    async def chapter_job(self, owner, work, simulation):
        return await self.one(
            "SELECT * FROM story_tasks WHERE user_id=%s AND work_id=%s "
            "AND input_data->>'simulation_id'=%s AND kind IN "
            "('chapter','chapter_review','chapter_polish') ORDER BY "
            "created_at DESC,id DESC LIMIT 1",
            (owner, work, simulation),
        )

    async def chapter_progress(self, task, progress):
        await self.execute(
            "UPDATE story_tasks SET progress=%s,updated_at=now() WHERE id=%s",
            (json.dumps(progress), task["id"]),
        )
