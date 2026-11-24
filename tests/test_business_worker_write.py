"""2.8 bind/plan/extract 交接与 write 服务步骤。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import unittest
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engines.business_config import BusinessGraphConfig
from app.engines.business_model import BusinessModelPlanner, ChunkInput
from app.engines.business_stub import (
    StubGraphClient,
    StubModelTransport,
    StubPager,
)
from app.engines.graph import GraphCallError
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
SECOND_TEXT = "图书馆在考试周延长开放到晚上十点。"


class PausedCreateClient:
    """共享幂等图谱，但第一次 create 暂停到测试释放。"""

    def __init__(self) -> None:
        self.graph = self
        self.graphs: dict[str, str] = {}
        self.create_keys: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self._paused = False

    def create(self, **kwargs: Any) -> dict[str, Any]:
        key = str(kwargs["idempotency_key"])
        self.create_keys.append(key)
        graph_id = self.graphs.setdefault(key, str(uuid.uuid4()))
        if not self._paused:
            self._paused = True
            self.started.set()
            if not self.release.wait(10):
                raise TimeoutError("测试未释放 create")
        return {"uuid": graph_id}


class PausedBatchPlanner:
    def __init__(self, inner: BusinessModelPlanner, batch_key: str) -> None:
        self.inner = inner
        self.batch_key = batch_key
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: dict[str, int] = {}

    def plan(self, **kwargs: Any):
        return self.inner.plan(**kwargs)

    def restore_plan(self, *args: Any, **kwargs: Any):
        return self.inner.restore_plan(*args, **kwargs)

    def extract_batch(self, **kwargs: Any):
        batch = kwargs["batch"]
        self.calls[batch.key] = self.calls.get(batch.key, 0) + 1
        if batch.key == self.batch_key and not self.started.is_set():
            self.started.set()
            if not self.release.wait(10):
                raise TimeoutError("测试未释放 extract")
        return self.inner.extract_batch(**kwargs)

    def validate_result(self, *args: Any, **kwargs: Any):
        return self.inner.validate_result(*args, **kwargs)


class StopAfterFirstAddClient:
    def __init__(self, inner: StubGraphClient, stop_event: asyncio.Event) -> None:
        self.graph = self
        self.episode = self
        self.inner = inner
        self.stop_event = stop_event
        self.calls = 0

    def add(self, *args: Any, **kwargs: Any):
        result = self.inner.add(*args, **kwargs)
        self.calls += 1
        if self.calls == 1:
            self.stop_event.set()
        return result


class CrashOnSecondAddClient:
    def __init__(self, inner: StubGraphClient, *, after_accept: bool) -> None:
        self.graph = self
        self.episode = self
        self.inner = inner
        self.after_accept = after_accept
        self.calls = 0

    def add(self, *args: Any, **kwargs: Any):
        self.calls += 1
        if self.calls == 2 and not self.after_accept:
            raise RuntimeError("worker exited before send")
        result = self.inner.add(*args, **kwargs)
        if self.calls == 2 and self.after_accept:
            raise RuntimeError("worker exited after accept")
        return result


class SharedGraphClient:
    def __init__(self, inner: StubGraphClient) -> None:
        self.graph = self
        self.episode = self
        self.inner = inner
        self.add_calls = 0
        self.search_calls = 0
        self.add_keys: list[str] = []

    def add(self, *args: Any, **kwargs: Any):
        self.add_calls += 1
        self.add_keys.append(str(kwargs.get("idempotency_key")))
        return self.inner.add(*args, **kwargs)

    def search_episodes(self, *args: Any, **kwargs: Any):
        self.search_calls += 1
        return self.inner.search_episodes(*args, **kwargs)


class InFlightLostSecondAddClient(SharedGraphClient):
    def __init__(self, inner: StubGraphClient) -> None:
        super().__init__(inner)
        self.accepted = threading.Event()
        self.release = threading.Event()

    def add(self, *args: Any, **kwargs: Any):
        result = super().add(*args, **kwargs)
        if self.add_calls == 2:
            self.accepted.set()
            if not self.release.wait(10):
                raise TimeoutError("测试未释放 in-flight add")
            raise TimeoutError("模拟 add 响应丢失")
        return result


class RejectSecondAddOnceClient(SharedGraphClient):
    def __init__(self, inner: StubGraphClient, stop_event: asyncio.Event) -> None:
        super().__init__(inner)
        self.stop_event = stop_event
        self.rejected = False

    def add(self, *args: Any, **kwargs: Any):
        if self.add_calls == 1:
            self.add_calls += 1
            self.add_keys.append(str(kwargs.get("idempotency_key")))
            self.stop_event.set()
            self.rejected = True
            raise GraphCallError(
                "explicit retryable rejection",
                status_code=429,
                response_lost=False,
                retryable=True,
            )
        return super().add(*args, **kwargs)


class Step5ControlClient:
    def __init__(
        self,
        episodes: list[graph_store.GraphEpisode],
    ) -> None:
        self.graph = self
        self.episodes = {item.episode_key: item for item in episodes}
        self.a_key = sorted(self.episodes)[0]
        self.b_key = sorted(self.episodes)[1]
        self.lock = threading.Lock()
        self.a_searches = 0
        self.b_searches = 0
        self.b_started = threading.Event()
        self.b_release = threading.Event()
        self.allow_a_processed = threading.Event()

    def _items(self) -> list[dict[str, Any]]:
        items = []
        for key, episode in self.episodes.items():
            if key == self.a_key:
                processed = self.allow_a_processed.is_set()
            else:
                processed = self.b_release.is_set()
            items.append(
                {
                    "uuid": str(episode.episode_uuid),
                    "content": episode.content,
                    "metadata": {
                        "buildId": str(episode.build_id),
                        "episodeKey": episode.episode_key,
                        "contentSha256": episode.content_sha256,
                    },
                    "processed": processed,
                }
            )
        return items

    def _search_text(self, key: str) -> str:
        try:
            payload = json.loads(self.episodes[key].content)
        except json.JSONDecodeError:
            return self.episodes[key].content[:120]
        return str(payload.get("searchText") or "")[:120]

    def search_episodes(self, graph_uuid: str, *, query: str, page_size: int = 50):
        del graph_uuid, page_size
        with self.lock:
            if query == self._search_text(self.b_key):
                self.b_searches += 1
                first_b = self.b_searches == 1
            else:
                self.a_searches += 1
                first_b = False
        if first_b:
            self.b_started.set()
            if not self.b_release.wait(10):
                raise TimeoutError("测试未释放步骤 5")
        return StubPager(self._items())


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class GraphWriteStepTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)
        async with self.session_factory() as db:
            self.seed: SeedMaterial = await seed_original_material(
                db, marker="graph-write"
            )
            await db.execute(
                text(
                    "INSERT INTO document_chunks (document_id, chunk_index, "
                    "paragraph_start, paragraph_end, source_start, source_end, text) "
                    "VALUES (:document_id, 2, 2, 2, 0, :source_end, :text)"
                ),
                {
                    "document_id": self.seed.document_id,
                    "source_end": len(SECOND_TEXT),
                    "text": SECOND_TEXT,
                },
            )
            await db.execute(
                text(
                    "UPDATE documents SET chunk_count = 2 WHERE id = :document_id"
                ),
                {"document_id": self.seed.document_id},
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
            {
                "EVIDENCE_GRAPH_ALLOW_STUB": "true",
                "EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS": "5",
                "EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS": "5",
            }
        )
        self.chunks = [
            ChunkInput(
                document_id=str(self.seed.document_id),
                document_name="原创接驳车小材料.txt",
                chunk_index=1,
                text=self.seed.text,
                source_start=0,
                source_end=len(self.seed.text),
                paragraph_start=1,
                paragraph_end=1,
            ),
            ChunkInput(
                document_id=str(self.seed.document_id),
                document_name="原创接驳车小材料.txt",
                chunk_index=2,
                text=SECOND_TEXT,
                source_start=0,
                source_end=len(SECOND_TEXT),
                paragraph_start=2,
                paragraph_end=2,
            ),
        ]

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def _planner(self) -> BusinessModelPlanner:
        return BusinessModelPlanner(
            model_api_base_url="https://model.test.local/v1",
            model_api_key="test-key",
            model_name="test-model",
            timeout_seconds=5,
            transport=StubModelTransport(
                chunks=self.chunks, question=QUESTION
            ),
            call_kind="double",
            sleep=lambda _: None,
        )

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

    async def _build_state(self):
        async with self.session_factory() as db:
            build = await graph_store.get_build(
                db, project_id=self.seed.project_id, build_id=self.build.id
            )
            episodes = await graph_store.list_episodes(
                db, build_id=self.build.id
            )
            snapshots = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM graph_snapshots WHERE build_id = :id"
                    ),
                    {"id": self.build.id},
                )
            ).scalar_one()
            completed = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM run_events WHERE run_id = :run_id "
                        "AND type = 'completed'"
                    ),
                    {"run_id": self.build.run_id},
                )
            ).scalar_one()
            job = (
                await db.execute(
                    text(
                        "SELECT next_step, status FROM jobs WHERE id = :job_id"
                    ),
                    {"job_id": self.build.job_id},
                )
            ).one()
        return build, episodes, int(snapshots), int(completed), job

    async def _bind_plan(
        self,
        claim,
        client: StubGraphClient,
        config: BusinessGraphConfig | None = None,
    ) -> BusinessModelPlanner:
        cfg = config or self.config
        planner = self._planner()
        bind = await worker_service.execute_graph_bind(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=cfg,
            graph_client_factory=lambda: client,
        )
        self.assertEqual(bind, "progress")
        plan = await worker_service.execute_graph_plan(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=cfg,
            planner=planner,
        )
        self.assertEqual(plan, "progress")
        return planner

    async def _extract(
        self,
        claim,
        planner: BusinessModelPlanner,
        config: BusinessGraphConfig | None = None,
    ) -> None:
        outcome = await worker_service.execute_graph_extract(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=config or self.config,
            planner=planner,
        )
        self.assertEqual(outcome, "progress")

    async def test_normal_write_persists_two_episodes_and_waiting_index(self) -> None:
        claim = await self._claim("worker-write")
        client = StubGraphClient()
        planner = await self._bind_plan(claim, client)
        await self._extract(claim, planner)
        outcome = await worker_service.execute_graph_write(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: client,
        )
        self.assertEqual(outcome, "progress")
        build, episodes, snapshots, completed, job = await self._build_state()
        self.assertEqual(
            [row[1] for row in await self._step_rows()],
            ["bind", "plan", "extract", "write"],
        )
        self.assertEqual(int(job.next_step), 4)
        self.assertEqual(job.status, "running")
        self.assertEqual(build.phase, "waiting_index")
        self.assertFalse(build.can_continue)
        self.assertEqual(build.planned_episodes, 2)
        self.assertEqual(build.accepted_episodes, 2)
        self.assertEqual(build.confirmed_episodes, 0)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(len({item.idempotency_key for item in episodes}), 2)
        self.assertTrue(all(item.episode_uuid for item in episodes))
        self.assertEqual(snapshots, 0)
        self.assertEqual(completed, 0)
        self.assertEqual(len(client.episode_keys), 2)

    async def test_takeover_after_a_accepted_skips_a_and_sends_b(self) -> None:
        claim_one = await self._claim("worker-a")
        client = StubGraphClient()
        planner = await self._bind_plan(claim_one, client)
        await self._extract(claim_one, planner)
        stop_event = asyncio.Event()
        stopping_client = StopAfterFirstAddClient(client, stop_event)
        outcome = await worker_service.execute_graph_write(
            claim_one,
            session_factory=self.session_factory,
            stop_event=stop_event,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: stopping_client,
        )
        self.assertEqual(outcome, "stopped")
        build, episodes, _, _, job = await self._build_state()
        self.assertEqual(int(job.next_step), 3)
        self.assertEqual(build.phase, "waiting_index")
        self.assertEqual(build.planned_episodes, 2)
        self.assertEqual(build.accepted_episodes, 1)
        self.assertEqual(sum(1 for item in episodes if item.attempts > 0), 1)
        pending = [
            item for item in episodes if item.state == "planned" and item.attempts == 0
        ]
        self.assertEqual(len(pending), 1)

        await self._expire_lease()
        claim_two = await self._claim("worker-b")
        new_client = StubGraphClient()
        outcome = await worker_service.execute_graph_write(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: new_client,
        )
        self.assertEqual(outcome, "progress")
        build, episodes, _, _, job = await self._build_state()
        self.assertEqual(int(job.next_step), 4)
        self.assertEqual(build.accepted_episodes, 2)
        self.assertEqual(len(new_client.episode_keys), 1)
        self.assertEqual(
            new_client.episode_keys, [str(pending[0].idempotency_key)]
        )

    async def test_takeover_after_b_accepted_without_save_confirms_by_search(self) -> None:
        claim_one = await self._claim("worker-c")
        client = StubGraphClient()
        planner = await self._bind_plan(claim_one, client)
        await self._extract(claim_one, planner)
        crashing = CrashOnSecondAddClient(client, after_accept=True)
        with self.assertRaises(worker_service.GraphWorkError):
            await worker_service.execute_graph_write(
                claim_one,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=30,
                heartbeat_seconds=1,
                config=self.config,
                graph_client_factory=lambda: crashing,
            )
        build, episodes, _, _, job = await self._build_state()
        self.assertEqual(int(job.next_step), 3)
        self.assertEqual(build.accepted_episodes, 1)
        self.assertEqual(len(client.episodes), 2)
        self.assertEqual(sum(1 for item in episodes if item.attempts > 0), 2)

        await self._expire_lease()
        claim_two = await self._claim("worker-d")
        add_before = len(client.episode_keys)
        outcome = await worker_service.execute_graph_write(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: client,
        )
        self.assertEqual(outcome, "progress")
        self.assertEqual(len(client.episode_keys), add_before)
        self.assertGreaterEqual(client.search_calls, 1)
        build, episodes, _, _, job = await self._build_state()
        self.assertEqual(int(job.next_step), 4)
        self.assertEqual(build.accepted_episodes, 2)
        self.assertEqual(build.confirmed_episodes, 1)
        self.assertEqual(build.phase, "drawing")
        events = [event for event, _ in client.events]
        self.assertIn("search", events)
        self.assertEqual(events.count("add"), 2)

    async def test_waiting_search_does_not_trigger_add_and_write_unconfirmed(self) -> None:
        short_config = BusinessGraphConfig.from_env(
            {
                "EVIDENCE_GRAPH_ALLOW_STUB": "true",
                "EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS": "0.01",
                "EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS": "0.05",
            }
        )
        claim_one = await self._claim("worker-e")
        client = StubGraphClient()
        planner = await self._bind_plan(claim_one, client, short_config)
        await self._extract(claim_one, planner, short_config)
        crashing = CrashOnSecondAddClient(client, after_accept=False)
        with self.assertRaises(worker_service.GraphWorkError):
            await worker_service.execute_graph_write(
                claim_one,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=30,
                heartbeat_seconds=1,
                config=short_config,
                graph_client_factory=lambda: crashing,
            )
        # 服务端没有 B 记录；新 worker 只能检索，不能重新 add。
        empty_client = StubGraphClient()
        # 让新客户端共享 A 已存在的绑定/body 但不共享 B。
        await self._expire_lease()
        claim_two = await self._claim("worker-f")
        add_before = len(empty_client.episode_keys)
        with self.assertRaises(worker_service.GraphWorkError) as caught:
            await worker_service.execute_graph_write(
                claim_two,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=30,
                heartbeat_seconds=1,
                config=short_config,
                graph_client_factory=lambda: empty_client,
            )
        self.assertEqual(caught.exception.code, "write_unconfirmed")
        self.assertEqual(len(empty_client.episode_keys), add_before)
        build, _, _, _, job = await self._build_state()
        self.assertEqual(int(job.next_step), 3)
        self.assertEqual(build.accepted_episodes, 1)

    async def test_committed_write_does_not_call_provider_again(self) -> None:
        claim = await self._claim("worker-g")
        client = StubGraphClient()
        planner = await self._bind_plan(claim, client)
        await self._extract(claim, planner)
        outcome = await worker_service.execute_graph_write(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: client,
        )
        self.assertEqual(outcome, "progress")
        calls = len(client.episode_keys)
        duplicate = await worker_service.execute_graph_write(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: client,
        )
        self.assertEqual(duplicate, "duplicate")
        self.assertEqual(len(client.episode_keys), calls)

    async def test_takeover_before_episode_plan_persisted_adds_zero_rows(self) -> None:
        claim_one = await self._claim("worker-prepare-a")
        client_one = StubGraphClient()
        planner = await self._bind_plan(claim_one, client_one)
        await self._extract(claim_one, planner)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before_prepare() -> None:
            entered.set()
            await release.wait()

        task = asyncio.create_task(
            worker_service.execute_graph_write(
                claim_one,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=30,
                heartbeat_seconds=1,
                config=self.config,
                graph_client_factory=lambda: client_one,
                before_prepare=before_prepare,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        _, episodes_before, _, _, _ = await self._build_state()
        self.assertEqual(episodes_before, [])

        await self._expire_lease()
        claim_two = await self._claim("worker-prepare-b")
        client_two = StubGraphClient()
        new_outcome = await worker_service.execute_graph_write(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: client_two,
        )
        self.assertEqual(new_outcome, "progress")
        release.set()
        old_outcome = await task
        self.assertEqual(old_outcome, "lost")
        _, episodes_after, _, _, _ = await self._build_state()
        self.assertEqual(len(episodes_after), 2)
        self.assertEqual(client_one.episode_keys, [])
        self.assertEqual(len(client_two.episode_keys), 2)

    async def test_inflight_add_takeover_old_worker_starts_no_search(self) -> None:
        claim_one = await self._claim("worker-inflight-a")
        inner = StubGraphClient()
        planner = await self._bind_plan(claim_one, inner)
        await self._extract(claim_one, planner)
        old_client = InFlightLostSecondAddClient(inner)
        task = asyncio.create_task(
            worker_service.execute_graph_write(
                claim_one,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=30,
                heartbeat_seconds=1,
                config=self.config,
                graph_client_factory=lambda: old_client,
            )
        )
        await asyncio.to_thread(old_client.accepted.wait, 5)
        build, episodes, _, _, _ = await self._build_state()
        self.assertEqual(build.accepted_episodes, 1)
        self.assertEqual(
            sum(1 for item in episodes if item.write_outcome == "in_flight"), 1
        )

        await self._expire_lease()
        claim_two = await self._claim("worker-inflight-b")
        new_client = SharedGraphClient(inner)
        new_outcome = await worker_service.execute_graph_write(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: new_client,
        )
        self.assertEqual(new_outcome, "progress")
        old_client.release.set()
        old_outcome = await task
        self.assertEqual(old_outcome, "lost")
        self.assertEqual(old_client.search_calls, 0)
        self.assertEqual(new_client.add_calls, 0)
        self.assertGreaterEqual(new_client.search_calls, 1)
        build, episodes, _, _, job = await self._build_state()
        self.assertEqual(int(job.next_step), 4)
        self.assertEqual(build.accepted_episodes, 2)
        self.assertEqual(build.confirmed_episodes, 1)
        self.assertEqual(build.phase, "drawing")
        self.assertEqual(len(inner.episode_keys), 2)

    async def test_explicit_rejection_takeover_reuses_same_key_and_body(self) -> None:
        claim_one = await self._claim("worker-reject-a")
        inner = StubGraphClient()
        planner = await self._bind_plan(claim_one, inner)
        await self._extract(claim_one, planner)
        stop_event = asyncio.Event()
        old_client = RejectSecondAddOnceClient(inner, stop_event)
        old_outcome = await worker_service.execute_graph_write(
            claim_one,
            session_factory=self.session_factory,
            stop_event=stop_event,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: old_client,
        )
        self.assertEqual(old_outcome, "stopped")
        _, episodes, _, _, _ = await self._build_state()
        rejected = [
            item for item in episodes if item.write_outcome == "rejected_retryable"
        ]
        self.assertEqual(len(rejected), 1)
        rejected_key = str(rejected[0].idempotency_key)

        await self._expire_lease()
        claim_two = await self._claim("worker-reject-b")
        new_client = SharedGraphClient(inner)
        new_outcome = await worker_service.execute_graph_write(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: new_client,
        )
        self.assertEqual(new_outcome, "progress")
        self.assertEqual(new_client.add_keys, [rejected_key])
        self.assertEqual(new_client.search_calls, 0)
        build, episodes, _, _, job = await self._build_state()
        self.assertEqual(int(job.next_step), 4)
        self.assertEqual(build.accepted_episodes, 2)
        self.assertEqual(build.phase, "waiting_index")
        self.assertEqual(len({item.idempotency_key for item in episodes}), 2)

    async def test_step5_persists_matched_then_processed_with_intermediate_reader(self) -> None:
        claim = await self._claim("worker-step5")
        client = StubGraphClient()
        planner = await self._bind_plan(claim, client)
        await self._extract(claim, planner)
        outcome = await worker_service.execute_graph_write(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: client,
        )
        self.assertEqual(outcome, "progress")
        _, episodes, _, _, _ = await self._build_state()
        control = Step5ControlClient(episodes)
        task = asyncio.create_task(
            worker_service.execute_graph_confirm_episodes(
                claim,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=30,
                heartbeat_seconds=1,
                config=self.config,
                graph_client_factory=lambda: control,
                poll_interval_seconds=0.01,
            )
        )
        await asyncio.to_thread(control.b_started.wait, 5)
        intermediate = None
        for _ in range(100):
            build, current, snapshots, completed, job = await self._build_state()
            if build.confirmed_episodes == 1:
                intermediate = (build, current, snapshots, completed, job)
                break
            await asyncio.sleep(0.01)
        if intermediate is None:
            if task.done() and task.exception() is not None:
                raise task.exception()  # type: ignore[misc]
            self.fail(
                f"no intermediate a={control.a_searches} b={control.b_searches} "
                f"done={task.done()}"
            )
        build, current, snapshots, completed, job = intermediate
        self.assertEqual(build.planned_episodes, 2)
        self.assertEqual(build.accepted_episodes, 2)
        self.assertEqual(build.confirmed_episodes, 1)
        self.assertEqual(build.phase, "drawing")
        self.assertFalse(build.can_continue)
        self.assertEqual(int(job.next_step), 4)
        self.assertEqual(len([row for row in await self._step_rows() if row[1] == "write"]), 1)
        self.assertNotIn("readback", [row[1] for row in await self._step_rows()])
        self.assertEqual(snapshots, 0)
        self.assertEqual(completed, 0)

        control.allow_a_processed.set()
        control.b_release.set()
        self.assertEqual(await task, "progress")
        build, current, snapshots, completed, job = await self._build_state()
        self.assertEqual(build.planned_episodes, 2)
        self.assertEqual(build.accepted_episodes, 2)
        self.assertEqual(build.confirmed_episodes, 2)
        self.assertEqual(build.phase, "drawing")
        self.assertEqual(int(job.next_step), 4)
        self.assertNotIn("readback", [row[1] for row in await self._step_rows()])
        self.assertEqual(snapshots, 0)
        self.assertEqual(completed, 0)
        searches_before = (control.a_searches, control.b_searches)
        repeated = await worker_service.execute_graph_confirm_episodes(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: control,
            poll_interval_seconds=0.01,
        )
        self.assertEqual(repeated, "progress")
        self.assertEqual((control.a_searches, control.b_searches), searches_before)

    async def test_step5_late_old_worker_writes_and_searches_nothing(self) -> None:
        claim_one = await self._claim("worker-step5-old")
        client = StubGraphClient()
        planner = await self._bind_plan(claim_one, client)
        await self._extract(claim_one, planner)
        await worker_service.execute_graph_write(
            claim_one,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: client,
        )
        _, episodes, _, _, _ = await self._build_state()
        control = Step5ControlClient(episodes)
        control.allow_a_processed.set()
        control.b_release.set()
        await self._expire_lease()
        claim_two = await self._claim("worker-step5-new")
        outcome = await worker_service.execute_graph_confirm_episodes(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: control,
            poll_interval_seconds=0.01,
        )
        self.assertEqual(outcome, "progress")
        old_client = SharedGraphClient(client)
        old_outcome = await worker_service.execute_graph_confirm_episodes(
            claim_one,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: old_client,
            poll_interval_seconds=0.01,
        )
        self.assertEqual(old_outcome, "lost")
        self.assertEqual(old_client.search_calls, 0)
        build, _, _, _, job = await self._build_state()
        self.assertEqual(int(job.next_step), 4)
        self.assertEqual(build.confirmed_episodes, 2)

    async def test_step5_cancel_stops_without_search(self) -> None:
        claim = await self._claim("worker-step5-cancel")
        client = StubGraphClient()
        planner = await self._bind_plan(claim, client)
        await self._extract(claim, planner)
        await worker_service.execute_graph_write(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: client,
        )
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'canceled', terminal_reason = "
                    "'canceled_by_user' WHERE id = :job_id"
                ),
                {"job_id": self.build.job_id},
            )
            await db.commit()
        search_client = SharedGraphClient(client)
        outcome = await worker_service.execute_graph_confirm_episodes(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: search_client,
            poll_interval_seconds=0.01,
        )
        self.assertEqual(outcome, "canceled")
        self.assertEqual(search_client.search_calls, 0)

    async def test_bind_handoff_keeps_pending_then_one_logical_graph(self) -> None:
        claim_one = await self._claim("worker-bind-a")
        client = PausedCreateClient()
        task = asyncio.create_task(
            worker_service.execute_graph_bind(
                claim_one,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=30,
                heartbeat_seconds=1,
                config=self.config,
                graph_client_factory=lambda: client,
            )
        )
        await asyncio.to_thread(client.started.wait, 5)
        async with self.session_factory() as db:
            pending = await graph_store.require_binding(
                db, project_id=self.seed.project_id
            )
        self.assertIsNone(pending.graph_id)
        self.assertEqual(pending.state, "pending")

        await self._expire_lease()
        claim_two = await self._claim("worker-bind-b")
        new_outcome = await worker_service.execute_graph_bind(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            graph_client_factory=lambda: client,
        )
        self.assertEqual(new_outcome, "progress")
        client.release.set()
        old_outcome = await task
        self.assertEqual(old_outcome, "lost")
        self.assertEqual(len(client.graphs), 1)
        self.assertEqual(len(set(client.create_keys)), 1)
        async with self.session_factory() as db:
            bound = await graph_store.require_binding(
                db, project_id=self.seed.project_id
            )
        self.assertIsNotNone(bound.graph_id)
        self.assertEqual(bound.state, "bound")

    async def test_extract_handoff_old_worker_never_calls_second_batch(self) -> None:
        claim_one = await self._claim("worker-extract-a")
        client = StubGraphClient()
        planner = await self._bind_plan(claim_one, client)
        old_planner = PausedBatchPlanner(self._planner(), "batch-1")
        task = asyncio.create_task(
            worker_service.execute_graph_extract(
                claim_one,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=30,
                heartbeat_seconds=1,
                config=self.config,
                planner=old_planner,
            )
        )
        await asyncio.to_thread(old_planner.started.wait, 5)
        await self._expire_lease()
        claim_two = await self._claim("worker-extract-b")
        new_outcome = await worker_service.execute_graph_extract(
            claim_two,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=30,
            heartbeat_seconds=1,
            config=self.config,
            planner=planner,
        )
        self.assertEqual(new_outcome, "progress")
        old_planner.release.set()
        old_outcome = await task
        self.assertIn(old_outcome, {"duplicate", "lost"})
        self.assertEqual(old_planner.calls.get("batch-1"), 1)
        self.assertNotIn("batch-2", old_planner.calls)


if __name__ == "__main__":
    unittest.main()
