"""2.8 bind/plan/extract 服务步骤：交接、租约、持久化与预算。"""

from __future__ import annotations

import time
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engines.business_config import BusinessGraphConfig
from app.engines.business_model import BusinessModelPlanner, ChunkInput
from app.engines.business_stub import StubGraphClient, StubModelTransport
from app.services import evidence_graph as graph_store
from app.services import worker as worker_service
from tests.db_support import (
    HAS_TEST_DATABASE,
    SKIP_REASON,
    SeedMaterial,
    apply_all_migrations,
    make_test_engine,
    reset_public_schema,
    seed_original_material,
)

QUESTION = "雨天是否应该增加一班校园接驳车？"


class CountingPlanner:
    def __init__(self, inner: BusinessModelPlanner, sleep_seconds: float = 0.0) -> None:
        self.inner = inner
        self.sleep_seconds = sleep_seconds
        self.plan_calls = 0
        self.extract_calls = 0

    def plan(self, **kwargs: object):
        self.plan_calls += 1
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        return self.inner.plan(**kwargs)

    def restore_plan(self, *args: object, **kwargs: object):
        return self.inner.restore_plan(*args, **kwargs)

    def extract_batch(self, **kwargs: object):
        self.extract_calls += 1
        return self.inner.extract_batch(**kwargs)

    def validate_result(self, *args: object, **kwargs: object):
        return self.inner.validate_result(*args, **kwargs)


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class GraphWorkerStepTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)
        async with self.session_factory() as db:
            self.seed: SeedMaterial = await seed_original_material(
                db, marker="graph-worker"
            )
            await db.commit()
        async with self.session_factory() as db:
            self.build = await graph_store.create_build(
                db,
                project_id=self.seed.project_id,
                actor_id=self.seed.user_id,
                question=QUESTION,
                document_ids=[self.seed.document_id],
            )
        self.config = BusinessGraphConfig.from_env(
            {"EVIDENCE_GRAPH_ALLOW_STUB": "true"}
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def _planner(self, sleep_seconds: float = 0.0) -> CountingPlanner:
        chunk = ChunkInput(
            document_id=str(self.seed.document_id),
            document_name="原创接驳车小材料.txt",
            chunk_index=1,
            text=self.seed.text,
            source_start=0,
            source_end=len(self.seed.text),
            paragraph_start=1,
            paragraph_end=1,
        )
        transport = StubModelTransport(chunks=[chunk], question=QUESTION)
        inner = BusinessModelPlanner(
            model_api_base_url="https://model.test.local/v1",
            model_api_key="test-key",
            model_name="test-model",
            timeout_seconds=5,
            transport=transport,
            call_kind="double",
            sleep=lambda _: None,
        )
        return CountingPlanner(inner, sleep_seconds=sleep_seconds)

    async def _claim(self, worker_id: str, lease_seconds: float = 30.0):
        async with self.session_factory() as db:
            claim = await worker_service.claim_next(
                db,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                job_id=self.build.job_id,
            )
        self.assertIsNotNone(claim)
        return claim

    async def _expire_lease(self) -> None:
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE jobs SET lease_expires_at = clock_timestamp() "
                    "- interval '1 second' WHERE id = :job_id"
                ),
                {"job_id": self.build.job_id},
            )
            await db.commit()

    async def _step_rows(self) -> list[tuple[int, str, str]]:
        async with self.session_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT step_index, step_key, payload::text "
                        "FROM run_steps WHERE run_id = :run_id ORDER BY step_index"
                    ),
                    {"run_id": self.build.run_id},
                )
            ).fetchall()
        return [(int(r.step_index), r.step_key, r.payload) for r in rows]

    async def _job(self) -> object:
        async with self.session_factory() as db:
            return (
                await db.execute(
                    text(
                        "SELECT next_step, status FROM jobs WHERE id = :job_id"
                    ),
                    {"job_id": self.build.job_id},
                )
            ).one()

    async def test_bind_plan_extract_handoff_and_old_token_rejection(self) -> None:
        claim_one = await self._claim("worker-one")
        bind_outcome = await worker_service.execute_graph_bind(
            claim_one,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=StubGraphClient,
        )
        self.assertEqual(bind_outcome, "progress")

        planner = self._planner()
        plan_outcome = await worker_service.execute_graph_plan(
            claim_one,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            planner=planner,
        )
        self.assertEqual(plan_outcome, "progress")
        self.assertEqual(planner.plan_calls, 1)
        duplicate_plan = await worker_service.execute_graph_plan(
            claim_one,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            planner=planner,
        )
        self.assertEqual(duplicate_plan, "duplicate")
        self.assertEqual(planner.plan_calls, 1)
        rows = await self._step_rows()
        self.assertEqual([row[1] for row in rows], ["bind", "plan"])
        self.assertIn("entityTypes", rows[1][2])
        self.assertIn("batches", rows[1][2])

        await self._expire_lease()
        claim_two = await self._claim("worker-two")
        self.assertEqual(claim_two.next_step, 2)
        extract_outcome = await worker_service.execute_graph_extract(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            planner=planner,
        )
        self.assertEqual(extract_outcome, "progress")
        self.assertEqual(planner.plan_calls, 1)
        self.assertEqual(planner.extract_calls, 1)
        duplicate_extract = await worker_service.execute_graph_extract(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            planner=planner,
        )
        self.assertEqual(duplicate_extract, "duplicate")
        self.assertEqual(planner.extract_calls, 1)
        rows = await self._step_rows()
        self.assertEqual([row[1] for row in rows], ["bind", "plan", "extract"])
        manifest_before = rows[2][2]
        self.assertIn('"batchKey"', manifest_before)
        self.assertIn('"nodes"', manifest_before)
        self.assertIn('"edges"', manifest_before)
        job = await self._job()
        self.assertEqual(int(job.next_step), 3)
        self.assertEqual(job.status, "running")
        async with self.session_factory() as db:
            build = await graph_store.get_build(
                db, project_id=self.seed.project_id, build_id=self.build.id
            )
            episode_count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM graph_episodes WHERE build_id = :id"
                    ),
                    {"id": self.build.id},
                )
            ).scalar_one()
            snapshot_count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM graph_snapshots WHERE build_id = :id"
                    ),
                    {"id": self.build.id},
                )
            ).scalar_one()
            completed_events = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM run_events WHERE run_id = :run_id "
                        "AND type = 'completed'"
                    ),
                    {"run_id": self.build.run_id},
                )
            ).scalar_one()
        self.assertEqual(build.phase, "preparing")
        self.assertEqual(build.planned_episodes, 1)
        self.assertEqual(int(episode_count), 0)
        self.assertEqual(int(snapshot_count), 0)
        self.assertEqual(int(completed_events), 0)

        old_outcome = await worker_service.execute_graph_extract(
            claim_one,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            planner=planner,
        )
        self.assertEqual(old_outcome, "lost")
        rows_after = await self._step_rows()
        self.assertEqual(rows_after, rows)
        self.assertEqual(rows_after[2][2], manifest_before)
        job_after = await self._job()
        self.assertEqual(int(job_after.next_step), 3)
        self.assertEqual(planner.plan_calls, 1)

    async def test_bind_rejects_not_ready_document(self) -> None:
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE documents SET status = 'parsing' WHERE id = :document_id"
                ),
                {"document_id": self.seed.document_id},
            )
            await db.commit()
        claim = await self._claim("worker-bind")
        with self.assertRaises(worker_service.GraphWorkError) as caught:
            await worker_service.execute_graph_bind(
                claim,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=30,
                heartbeat_seconds=1,
                config=self.config,
                graph_client_factory=StubGraphClient,
            )
        self.assertEqual(caught.exception.code, "document_not_ready")
        self.assertEqual(await self._step_rows(), [])

    async def test_plan_provider_runs_while_lease_is_renewed(self) -> None:
        claim = await self._claim("worker-heartbeat", lease_seconds=0.4)
        bind_outcome = await worker_service.execute_graph_bind(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=0.4,
            heartbeat_seconds=0.05,
            config=self.config,
            graph_client_factory=StubGraphClient,
        )
        self.assertEqual(bind_outcome, "progress")
        planner = self._planner(sleep_seconds=0.8)
        outcome = await worker_service.execute_graph_plan(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=0.4,
            heartbeat_seconds=0.05,
            config=self.config,
            planner=planner,
        )
        self.assertEqual(outcome, "progress")
        self.assertEqual(
            [row[1] for row in await self._step_rows()], ["bind", "plan"]
        )


if __name__ == "__main__":
    unittest.main()
