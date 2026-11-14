import asyncio

from echosim.domain.accounts import Fault
from echosim.domain.model_connection import ModelFailure
from echosim.services.story_generation import generate_candidate


class StoryWorker:
    def __init__(
        self,
        store,
        configurations,
        provider,
        simulation=None,
        adoption=None,
        graph=None,
        chapters=None,
    ):
        self.store, self.configurations, self.provider = store, configurations, provider
        self.chapters = chapters
        self.simulation, self.adoption, self.graph = simulation, adoption, graph

    async def tick(self):
        async with self.store.transaction() as db:
            await db.task_reap()
            await db.config_cleanup()
            task = await db.task_claim()
        if not task:
            return False
        heartbeat = asyncio.create_task(self.heartbeat(task))
        try:
            config = await self.configurations.bound(task["user_id"], task["configuration_id"])
            async with asyncio.timeout(
                {
                    "preparation": 240,
                    "simulation": 2700,
                    "adoption": 600,
                    "graph": 240,
                    "chapter": 360,
                    "chapter_review": 600,
                    "chapter_polish": 240,
                }[task["kind"]]
            ):
                if task["kind"] == "preparation":
                    content = await generate_candidate(
                        self.provider,
                        config,
                        task["input_data"],
                        task["configuration_id"],
                        session=task["id"],
                    )
                    async with self.store.transaction() as db:
                        if await db.task_guard(task):
                            await db.story_publish(task, content)
                            await db.task_finish(task, "succeeded")
                elif task["kind"] == "simulation":
                    await self.simulation(task, config)
                elif task["kind"] in ("chapter", "chapter_review", "chapter_polish"):
                    await self.chapters(task, config)
                elif task["kind"] == "graph":
                    await self.graph(task, config)
                elif task["kind"] == "adoption":
                    await self.adoption(task, config)
        except (Fault, ModelFailure, TimeoutError) as exc:
            await self.fail(task, getattr(exc, "code", "TASK_TIMEOUT"))
        except Exception:
            await self.fail(task, "TASK_FAILED")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def heartbeat(self, task):
        while True:
            await asyncio.sleep(5)
            async with self.store.transaction() as db:
                await db.task_heartbeat(task)
                await db.task_reap()

    async def fail(self, task, code):
        async with self.store.transaction() as db:
            row = await db.task_guard(task)
            if row:
                await db.task_finish(
                    task, "stopped" if row["state"] == "stopping" else "failed", code
                )
