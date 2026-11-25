"""Change 10：运行时序记忆的记账、按轮截断、角色读取与查账恢复。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engines import temporal_memory as contract
from app.engines.business_config import graph_id_fingerprint
from app.engines.duet_config import DuetConfig
from app.services import simulation as store
from app.services import simulation_memory as memory
from app.services import simulation_worker
from app.services import worker as worker_service
from tests.db_support import (
    HAS_TEST_DATABASE,
    SKIP_REASON,
    apply_all_migrations,
    make_test_engine,
    reset_public_schema,
)
from tests.simulation_support import prepare_selection


class TemporalMemoryContractTests(unittest.TestCase):
    def test_ids_and_episode_content_are_deterministic_and_redacted(self) -> None:
        simulation_id = uuid.uuid4()
        action_id = uuid.uuid4()
        first = contract.memory_id_for(simulation_id, action_id)
        second = contract.memory_id_for(simulation_id, action_id)
        self.assertEqual(first, second)
        self.assertNotEqual(
            first, contract.memory_id_for(uuid.uuid4(), action_id)
        )
        content = contract.episode_content_for(
            simulation_id=simulation_id,
            action_id=action_id,
            memory_id=first,
            platform_key="platform_b",
            native_platform="reddit",
            round_index=2,
            simulated_at=datetime.now(timezone.utc),
            actor_role_id=uuid.uuid4(),
            actor_display_name="原创角色",
            native_action="create_comment",
            content="原创行动",
            target_engine_record_id="post:1",
        )
        self.assertIn(str(first), content)
        self.assertNotIn("Authorization", content)
        self.assertEqual(
            contract.node_key_for("role", " 角色  Ａ "),
            "role:角色 a",
        )
        self.assertEqual(contract.content_sha256(content), contract.content_sha256(content))


MEMORY_ENV = {
    "SIMULATION_MEMORY_PROVIDER": "stub",
    "EVIDENCE_GRAPH_ALLOW_STUB": "true",
    "EVIDENCE_GRAPH_GRAPH_PROVIDER": "stub",
    "SIMULATION_MEMORY_MAX_REFS": "20",
}


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class TemporalMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)
        self.frozen = await prepare_selection(self.session_factory, marker="temporal")
        self.project_id = self.frozen.seed.project_id
        self.actor_id = self.frozen.seed.user_id
        self.data_dir = Path(tempfile.mkdtemp(prefix="echosim-memory-"))
        self.env = patch.dict(os.environ, MEMORY_ENV, clear=False)
        self.env.start()

    async def asyncTearDown(self) -> None:
        self.env.stop()
        await self.engine.dispose()

    def config(self) -> DuetConfig:
        return DuetConfig(
            engine="double",
            allow_oasis=False,
            data_dir=self.data_dir,
            actions_per_round=6,
            round_timeout_seconds=120.0,
            round_delay_ms=0,
            max_actions=5000,
            keep_runtime=True,
        )

    async def _start(self):
        async with self.session_factory() as db:
            simulation, created = await store.start_simulation(
                db,
                project_id=self.project_id,
                actor_id=self.actor_id,
                selection_id=self.frozen.selection_id,
                engine_mode=None,
                allow_oasis=False,
                default_engine="double",
            )
        self.assertTrue(created)
        return simulation

    async def _claim(self, simulation):
        async with self.session_factory() as db:
            claim = await worker_service.claim_next(
                db,
                worker_id="memory-test",
                lease_seconds=120.0,
                job_id=simulation.job_id,
                simulation_engine="double",
            )
        self.assertIsNotNone(claim)
        return claim

    async def _drain_memory(self) -> None:
        for _ in range(10):
            async with self.session_factory() as db:
                claim = await worker_service.claim_next(
                    db,
                    worker_id="memory-drain-test",
                    lease_seconds=120.0,
                    run_kinds=("simulation.memory",),
                    simulation_engine="double",
                )
            if claim is None:
                return
            await memory.execute_memory_sync(
                claim,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=120.0,
                heartbeat_seconds=30.0,
                config=memory.MemoryConfig.from_env(),
            )

    async def _run(self, simulation, *, drain_memory: bool = True):
        claim = await self._claim(simulation)
        outcome = await simulation_worker.execute_simulation(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=120.0,
            heartbeat_seconds=30.0,
            config=self.config(),
        )
        self.assertEqual(outcome, "succeeded", outcome)
        if drain_memory:
            await self._drain_memory()
        async with self.session_factory() as db:
            return await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )

    async def test_round_view_role_reads_and_snapshot_readonly(self) -> None:
        simulation = await self._start()
        async with self.session_factory() as db:
            snapshot_before = (
                await db.execute(
                    text(
                        "SELECT (SELECT count(*) FROM graph_nodes WHERE snapshot_id = :s), "
                        "(SELECT count(*) FROM graph_edges WHERE snapshot_id = :s), "
                        "(SELECT count(*) FROM graph_evidence WHERE snapshot_id = :s)"
                    ),
                    {"s": simulation.snapshot_id},
                )
            ).first()
            await db.commit()
        refreshed = await self._run(simulation)
        self.assertEqual(refreshed.memory_status, "complete")
        async with self.session_factory() as db:
            comment = (
                await db.execute(
                    text(
                        "SELECT a.id, a.round_index, a.simulated_at, e.id AS memory_id, "
                        "p.episode_uuid, e.source_event_seq FROM simulation_actions a "
                        "JOIN simulation_memory_entries e ON e.action_id = a.id "
                        "JOIN simulation_memory_episodes p ON p.memory_id = e.id "
                        "WHERE a.simulation_id = :s AND a.platform_key = 'platform_b' "
                        "AND a.round_index = 2 AND a.native_action = 'create_comment' "
                        "ORDER BY a.action_seq LIMIT 1"
                    ),
                    {"s": simulation.id},
                )
            ).first()
            self.assertIsNotNone(comment)
            plan = (
                await db.execute(
                    text(
                        "SELECT request_payload FROM simulation_round_plans WHERE "
                        "simulation_id = :s AND platform_key = 'platform_b' AND round_index = 2"
                    ),
                    {"s": simulation.id},
                )
            ).scalar_one()
            reads = (
                await db.execute(
                    text(
                        "SELECT memory_id FROM simulation_action_memory_reads WHERE "
                        "action_id = :a"
                    ),
                    {"a": comment.id},
                )
            ).fetchall()
            snapshot_after = (
                await db.execute(
                    text(
                        "SELECT (SELECT count(*) FROM graph_nodes WHERE snapshot_id = :s), "
                        "(SELECT count(*) FROM graph_edges WHERE snapshot_id = :s), "
                        "(SELECT count(*) FROM graph_evidence WHERE snapshot_id = :s)"
                    ),
                    {"s": simulation.snapshot_id},
                )
            ).first()
        self.assertEqual(tuple(snapshot_before), tuple(snapshot_after))
        self.assertGreater(len(plan.get("memoryRefs") or []), 0)
        self.assertEqual(
            {str(item["memoryId"]) for item in plan["memoryRefs"]},
            {str(row.memory_id) for row in reads},
        )
        for ref in plan["memoryRefs"]:
            self.assertLess(int(ref["roundIndex"]), 2)
        async with self.session_factory() as db:
            ref_platforms = (
                await db.execute(
                    text(
                        "SELECT DISTINCT e.platform_key FROM simulation_memory_entries e "
                        "WHERE e.simulation_id = :s AND e.id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {
                        "s": simulation.id,
                        "ids": [str(item["memoryId"]) for item in plan["memoryRefs"]],
                    },
                )
            ).fetchall()
        self.assertEqual({row.platform_key for row in ref_platforms}, {"platform_b"})
        async with self.session_factory() as db:
            view1 = await memory.memory_view(
                db, simulation=refreshed, round_index=1, platform_key="platform_b",
                config=memory.MemoryConfig.from_env(),
            )
            view2 = await memory.memory_view(
                db, simulation=refreshed, round_index=2, platform_key="platform_b",
                config=memory.MemoryConfig.from_env(),
            )
            view3 = await memory.memory_view(
                db, simulation=refreshed, round_index=3, platform_key="platform_b",
                config=memory.MemoryConfig.from_env(),
            )
        comment_memory = str(comment.memory_id)
        self.assertNotIn(comment_memory, {item["memoryId"] for item in view1["entries"]})
        self.assertIn(comment_memory, {item["memoryId"] for item in view2["entries"]})
        self.assertIn(comment_memory, {item["memoryId"] for item in view3["entries"]})
        # 同节点在第 2、3 轮各有来源时，第 2 轮不能看到第 3 轮来源或计数。
        for node in view2["runtime"]["nodes"]:
            self.assertTrue(all(int(src["roundIndex"]) <= 2 for src in node["sources"]))
        self.assertTrue(view3["runtime"]["nodes"])
        self.assertTrue(
            any(int(node["lastRound"] or 0) >= 3 for node in view3["runtime"]["nodes"])
        )
        node2 = next(
            node
            for node in view2["runtime"]["nodes"]
            if any(int(src["roundIndex"]) == 2 for src in node["sources"])
        )
        node3 = next(
            node
            for node in view3["runtime"]["nodes"]
            if node["key"] == node2["key"]
        )
        self.assertNotEqual(node2["summary"], node3["summary"])
        self.assertGreater(node3["sourceCount"], node2["sourceCount"])
        async with self.session_factory() as db:
            view2_again = await memory.memory_view(
                db, simulation=refreshed, round_index=2, platform_key="platform_b",
                config=memory.MemoryConfig.from_env(),
            )
        node2_again = next(
            node for node in view2_again["runtime"]["nodes"] if node["key"] == node2["key"]
        )
        self.assertEqual(node2_again["summary"], node2["summary"])
        self.assertEqual(node2_again["sourceCount"], node2["sourceCount"])
        self.assertTrue(
            any(item.get("sources") for item in view2_again["material"]["nodes"])
            or any(item.get("sources") for item in view2_again["material"]["edges"])
        )
        # 引擎实际请求文件包含计划中的 memoryRefs。
        request_path = self.data_dir / str(simulation.id) / "platform_b" / "round-2-request.json"
        self.assertTrue(request_path.is_file())
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request_refs = {
            str(ref["memoryId"])
            for action in request["actions"]
            for ref in action.get("memoryRefs") or []
        }
        self.assertEqual(request_refs, {str(item["memoryId"]) for item in plan["memoryRefs"]})

    async def test_duplicate_projection_and_reconcile_do_not_add_twice(self) -> None:
        simulation = await self._start()
        refreshed = await self._run(simulation)
        async with self.session_factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT memory_id, add_count, episode_uuid FROM "
                        "simulation_memory_episodes WHERE simulation_id = :s "
                        "ORDER BY round_index, memory_id LIMIT 1"
                    ),
                    {"s": simulation.id},
                )
            ).first()
            self.assertEqual(int(row.add_count), 1)
            before = (
                await db.execute(
                    text(
                        "SELECT (SELECT count(*) FROM simulation_memory_entries WHERE simulation_id = :s), "
                        "(SELECT count(*) FROM simulation_memory_episodes WHERE simulation_id = :s), "
                        "(SELECT count(*) FROM simulation_memory_nodes WHERE simulation_id = :s), "
                        "(SELECT count(*) FROM simulation_memory_edges WHERE simulation_id = :s), "
                        "(SELECT count(*) FROM simulation_memory_provenance WHERE simulation_id = :s)"
                    ),
                    {"s": simulation.id},
                )
            ).first()
            await memory.project_pending(
                db, simulation_id=simulation.id, config=memory.MemoryConfig.from_env()
            )
            after = (
                await db.execute(
                    text(
                        "SELECT (SELECT count(*) FROM simulation_memory_entries WHERE simulation_id = :s), "
                        "(SELECT count(*) FROM simulation_memory_episodes WHERE simulation_id = :s), "
                        "(SELECT count(*) FROM simulation_memory_nodes WHERE simulation_id = :s), "
                        "(SELECT count(*) FROM simulation_memory_edges WHERE simulation_id = :s), "
                        "(SELECT count(*) FROM simulation_memory_provenance WHERE simulation_id = :s)"
                    ),
                    {"s": simulation.id},
                )
            ).first()
            await db.commit()
        self.assertEqual(tuple(before), tuple(after))
        # 模拟“服务端已写入、本地未记下 UUID 就中断”：add_count 已为 1，UUID 为空。
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE simulation_memory_episodes SET episode_uuid = NULL, "
                    "state = 'planned', write_outcome = 'in_flight' "
                    "WHERE simulation_id = :s AND memory_id = :m"
                ),
                {"s": simulation.id, "m": row.memory_id},
            )
            await db.commit()
        async with self.session_factory() as db:
            await memory.project_pending(
                db, simulation_id=simulation.id, config=memory.MemoryConfig.from_env()
            )
            await db.commit()
        async with self.session_factory() as db:
            reconciled = (
                await db.execute(
                    text(
                        "SELECT add_count, episode_uuid, state FROM "
                        "simulation_memory_episodes WHERE simulation_id = :s AND memory_id = :m"
                    ),
                    {"s": simulation.id, "m": row.memory_id},
                )
            ).first()
        self.assertEqual(int(reconciled.add_count), 1)
        self.assertIsNotNone(reconciled.episode_uuid)
        self.assertEqual(str(reconciled.state), "processed")

    async def test_stub_provider_record_reconciles_without_second_add(self) -> None:
        simulation = await self._start()
        await self._run(simulation)
        async with self.session_factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT memory_id, add_count, idempotency_key FROM "
                        "simulation_memory_episodes WHERE simulation_id = :s "
                        "ORDER BY round_index, memory_id LIMIT 1"
                    ),
                    {"s": simulation.id},
                )
            ).first()
            await db.execute(
                text(
                    "UPDATE simulation_memory_episodes SET episode_uuid = NULL, "
                    "state = 'planned', write_outcome = 'in_flight' "
                    "WHERE simulation_id = :s AND memory_id = :m"
                ),
                {"s": simulation.id, "m": row.memory_id},
            )
            refreshed = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
            await memory.ensure_sync_job(db, simulation=refreshed)
            await db.commit()
        await self._drain_memory()
        async with self.session_factory() as db:
            after = (
                await db.execute(
                    text(
                        "SELECT add_count, episode_uuid, state FROM "
                        "simulation_memory_episodes WHERE simulation_id = :s AND memory_id = :m"
                    ),
                    {"s": simulation.id, "m": row.memory_id},
                )
            ).first()
            provider_count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM simulation_memory_provider_records "
                        "WHERE simulation_id = :s AND memory_id = :m"
                    ),
                    {"s": simulation.id, "m": row.memory_id},
                )
            ).scalar_one()
        self.assertEqual(int(after.add_count), int(row.add_count))
        self.assertIsNotNone(after.episode_uuid)
        self.assertEqual(str(after.state), "processed")
        self.assertEqual(int(provider_count), 1)

    async def test_stub_missing_provider_record_is_unconfirmed(self) -> None:
        simulation = await self._start()
        await self._run(simulation)
        async with self.session_factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT memory_id, idempotency_key FROM simulation_memory_episodes "
                        "WHERE simulation_id = :s ORDER BY round_index, memory_id LIMIT 1"
                    ),
                    {"s": simulation.id},
                )
            ).first()
            await db.execute(
                text(
                    "DELETE FROM simulation_memory_provider_records WHERE simulation_id = :s "
                    "AND memory_id = :m"
                ),
                {"s": simulation.id, "m": row.memory_id},
            )
            await db.execute(
                text(
                    "UPDATE simulation_memory_episodes SET episode_uuid = NULL, "
                    "add_count = 1, state = 'planned', write_outcome = 'in_flight' "
                    "WHERE simulation_id = :s AND memory_id = :m"
                ),
                {"s": simulation.id, "m": row.memory_id},
            )
            refreshed = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
            await memory.ensure_sync_job(db, simulation=refreshed)
            await db.commit()
        await self._drain_memory()
        async with self.session_factory() as db:
            after = (
                await db.execute(
                    text(
                        "SELECT add_count, episode_uuid, state, write_outcome, "
                        "idempotency_key FROM simulation_memory_episodes WHERE "
                        "simulation_id = :s AND memory_id = :m"
                    ),
                    {"s": simulation.id, "m": row.memory_id},
                )
            ).first()
            status = (
                await db.execute(
                    text("SELECT memory_status FROM simulations WHERE id = :s"),
                    {"s": simulation.id},
                )
            ).scalar_one()
            provider_count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM simulation_memory_provider_records "
                        "WHERE simulation_id = :s AND memory_id = :m"
                    ),
                    {"s": simulation.id, "m": row.memory_id},
                )
            ).scalar_one()
        self.assertEqual(int(after.add_count), 1)
        self.assertIsNone(after.episode_uuid)
        self.assertEqual(str(after.state), "unconfirmed")
        self.assertEqual(str(after.write_outcome), "unconfirmed")
        self.assertEqual(str(after.idempotency_key), str(row.idempotency_key))
        self.assertEqual(str(status), "unconfirmed")
        self.assertEqual(int(provider_count), 0)

    async def test_reconcile_zep_checks_identity_and_processed(self) -> None:
        simulation = await self._start()
        await self._run(simulation)
        async with self.session_factory() as db:
            row = await memory._episode_row(
                db,
                simulation_id=simulation.id,
                memory_id=(
                    await db.execute(
                        text(
                            "SELECT memory_id FROM simulation_memory_episodes WHERE "
                            "simulation_id = :s ORDER BY round_index, memory_id LIMIT 1"
                        ),
                        {"s": simulation.id},
                    )
                ).scalar_one(),
            )
            wrong = SimpleNamespace(
                uuid_="00000000-0000-0000-0000-000000000001",
                processed=True,
                metadata={
                    "simulationId": str(simulation.id),
                    "actionId": "wrong-action",
                    "memoryId": str(row.memory_id),
                    "contentSha256": str(row.content_sha256),
                },
            )
            unprocessed = SimpleNamespace(
                uuid_="00000000-0000-0000-0000-000000000002",
                processed=False,
                metadata={
                    "simulationId": str(simulation.id),
                    "actionId": str(row.action_id),
                    "memoryId": str(row.memory_id),
                    "contentSha256": str(row.content_sha256),
                },
            )
            right = SimpleNamespace(
                uuid_="00000000-0000-0000-0000-000000000003",
                processed=True,
                metadata={
                    "simulationId": str(simulation.id),
                    "actionId": str(row.action_id),
                    "memoryId": str(row.memory_id),
                    "contentSha256": str(row.content_sha256),
                },
            )

            class Pager:
                def __init__(self, items):
                    self.items = items
                    self.has_next = False

            class Client:
                def __init__(self, items):
                    self.graph = SimpleNamespace(
                        episode=SimpleNamespace(list=lambda *a, **k: Pager(items))
                    )

            self.assertFalse(
                await memory._reconcile_zep(db, Client([wrong]), "graph", row)
            )
            await db.rollback()
            self.assertFalse(
                await memory._reconcile_zep(db, Client([unprocessed]), "graph", row)
            )
            await db.rollback()
            self.assertTrue(
                await memory._reconcile_zep(db, Client([right]), "graph", row)
            )
            matched = (
                await db.execute(
                    text(
                        "SELECT state, episode_uuid FROM simulation_memory_episodes "
                        "WHERE simulation_id = :s AND memory_id = :m"
                    ),
                    {"s": simulation.id, "m": row.memory_id},
                )
            ).first()
        self.assertEqual(str(matched.state), "matched")
        self.assertEqual(
            str(matched.episode_uuid), "00000000-0000-0000-0000-000000000003"
        )

    async def test_memory_constraints_reject_cross_run_and_duplicate_state(self) -> None:
        first = await self._start()
        await self._run(first)
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE simulations SET phase = 'succeeded', finished_at = now() "
                    "WHERE id = :id"
                ),
                {"id": first.id},
            )
            await db.execute(
                text("UPDATE jobs SET status = 'succeeded' WHERE run_id = :r"),
                {"r": first.run_id},
            )
            await db.commit()
        second = await self._start()
        async with self.session_factory() as db:
            memory_id = (
                await db.execute(
                    text(
                        "SELECT id FROM simulation_memory_entries WHERE simulation_id = :s LIMIT 1"
                    ),
                    {"s": first.id},
                )
            ).scalar_one()
            with self.assertRaises(Exception):
                await db.execute(
                    text(
                        "INSERT INTO simulation_memory_provenance (simulation_id, "
                        "target_kind, target_key, memory_id, facet, action_id, "
                        "platform_key, round_index, simulated_at, source_event_seq) "
                        "VALUES (:s, 'node', 'bad', :m, 'simulation_result', :a, "
                        "'platform_b', 1, now(), 1)"
                    ),
                    {
                        "s": second.id,
                        "m": memory_id,
                        "a": uuid.uuid4(),
                    },
                )
            await db.rollback()
            with self.assertRaises(Exception):
                await db.execute(
                    text(
                        "UPDATE simulation_memory_episodes SET state = 'bogus' "
                        "WHERE simulation_id = :s"
                    ),
                    {"s": first.id},
                )
            await db.rollback()
            first_episode = (
                await db.execute(
                    text(
                        "SELECT memory_id, idempotency_key FROM simulation_memory_episodes "
                        "WHERE simulation_id = :s LIMIT 1"
                    ),
                    {"s": first.id},
                )
            ).first()
            with self.assertRaises(Exception):
                await db.execute(
                    text(
                        "INSERT INTO simulation_memory_episodes (simulation_id, memory_id, "
                        "action_id, episode_key, idempotency_key, content, content_sha256, "
                        "round_index, simulated_at, source_event_seq) SELECT "
                        "simulation_id, memory_id, action_id, episode_key, idempotency_key, "
                        "content, content_sha256, round_index, simulated_at, source_event_seq "
                        "FROM simulation_memory_episodes WHERE simulation_id = :s AND "
                        "memory_id = :m"
                    ),
                    {"s": first.id, "m": first_episode.memory_id},
                )
            await db.rollback()

    async def test_binding_retry_and_scope_rejection(self) -> None:
        simulation = await self._start()
        created_key = uuid.uuid4()
        created_uuid = str(uuid.uuid4())
        calls: list[str] = []

        class GraphNamespace:
            def create(self, **kwargs):
                calls.append(str(kwargs.get("idempotency_key")))
                if len(calls) == 1:
                    raise TimeoutError("response lost")
                return {"uuid": created_uuid}

        class Client:
            graph = GraphNamespace()

        async with self.session_factory() as db:
            with patch(
                "app.services.simulation_memory._graph_client",
                return_value=Client(),
            ):
                graph_id = await memory.ensure_binding(
                    db,
                    simulation_id=simulation.id,
                    config=memory.MemoryConfig(provider="zep"),
                )
            await db.commit()
        self.assertEqual(graph_id, created_uuid)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        del created_key
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE simulations SET phase = 'succeeded', finished_at = now() "
                    "WHERE id = :id"
                ),
                {"id": simulation.id},
            )
            await db.execute(
                text("UPDATE jobs SET status = 'succeeded' WHERE run_id = :r"),
                {"r": simulation.run_id},
            )
            await db.commit()
        async with self.session_factory() as db:
            second = await self._start()
            with patch(
                "app.services.simulation_memory._graph_client",
                return_value=Client(),
            ):
                with self.assertRaises(memory.MemoryUnavailable) as caught:
                    await memory.ensure_binding(
                        db,
                        simulation_id=second.id,
                        config=memory.MemoryConfig(
                            provider="zep",
                            acceptance_graph_fingerprint=graph_id_fingerprint(created_uuid),
                        ),
                    )
                await db.rollback()
        self.assertEqual(caught.exception.code, "invalid_graph_scope")

    async def test_nfkc_node_merge_and_provenance_dedupe(self) -> None:
        simulation = await self._start()
        await self._run(simulation)
        async with self.session_factory() as db:
            entry = (
                await db.execute(
                    text(
                        "SELECT id, action_id FROM simulation_memory_entries WHERE "
                        "simulation_id = :s LIMIT 1"
                    ),
                    {"s": simulation.id},
                )
            ).first()
            await memory._upsert_node(
                db,
                simulation_id=simulation.id,
                node_key="role:角色 a",
                node_type="role",
                label="角色 A",
                origin="role",
                summary="R1 角色 A",
            )
            await memory._upsert_node(
                db,
                simulation_id=simulation.id,
                node_key="role:角色 a",
                node_type="role",
                label="角色 A",
                origin="role",
                summary="R2 角色 A",
            )
            nodes = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM simulation_memory_nodes WHERE simulation_id = :s "
                        "AND node_key = 'role:角色 a'"
                    ),
                    {"s": simulation.id},
                )
            ).scalar_one()
            row = SimpleNamespace(
                memory_id=entry.id,
                action_id=entry.action_id,
                episode_uuid=None,
                platform_key="platform_b",
                round_index=1,
                simulated_at=datetime.now(timezone.utc),
                source_event_seq=1,
            )
            await memory._upsert_provenance(
                db,
                simulation_id=simulation.id,
                target_kind="node",
                target_key="role:角色 a",
                row=row,
                facet="simulation_result",
                summary="R1 角色 A",
            )
            await memory._upsert_provenance(
                db,
                simulation_id=simulation.id,
                target_kind="node",
                target_key="role:角色 a",
                row=row,
                facet="simulation_result",
                summary="R1 角色 A",
            )
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM simulation_memory_provenance WHERE "
                        "simulation_id = :s AND target_key = 'role:角色 a'"
                    ),
                    {"s": simulation.id},
                )
            ).scalar_one()
            await db.commit()
        self.assertEqual(int(nodes), 1)
        self.assertEqual(int(count), 1)

    async def test_delayed_projection_keeps_plan_refs_but_no_fake_nodes(self) -> None:
        simulation = await self._start()
        refreshed = await self._run(simulation, drain_memory=False)
        self.assertEqual(refreshed.phase, "succeeded")
        async with self.session_factory() as db:
            plan = (
                await db.execute(
                    text(
                        "SELECT request_payload FROM simulation_round_plans WHERE "
                        "simulation_id = :s AND platform_key = 'platform_b' "
                        "AND round_index = 2"
                    ),
                    {"s": simulation.id},
                )
            ).scalar_one()
            view = await memory.memory_view(
                db,
                simulation=refreshed,
                round_index=2,
                platform_key="platform_b",
                config=memory.MemoryConfig.from_env(),
            )
        self.assertGreater(len(plan.get("memoryRefs") or []), 0)
        self.assertEqual(view["runtime"]["nodes"], [])
        await self._drain_memory()
        async with self.session_factory() as db:
            refreshed = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
            view = await memory.memory_view(
                db,
                simulation=refreshed,
                round_index=2,
                platform_key="platform_b",
                config=memory.MemoryConfig.from_env(),
            )
        self.assertTrue(view["runtime"]["nodes"])

    async def test_memory_projection_failure_does_not_block_actions(self) -> None:
        simulation = await self._start()
        with patch(
            "app.services.simulation_memory.project_pending",
            new=AsyncMock(side_effect=memory.MemoryUnavailable("memory_unavailable")),
        ):
            refreshed = await self._run(simulation)
        self.assertEqual(refreshed.phase, "succeeded")
        self.assertEqual(refreshed.memory_status, "unavailable")
        async with self.session_factory() as db:
            await memory.project_pending(
                db, simulation_id=simulation.id, config=memory.MemoryConfig.from_env()
            )
            await db.commit()
        async with self.session_factory() as db:
            refreshed = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
        self.assertEqual(refreshed.memory_status, "complete")

    async def test_round_not_available_before_platform_advances(self) -> None:
        simulation = await self._start()
        config = memory.MemoryConfig.from_env()
        async with self.session_factory() as db:
            refreshed = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
            with self.assertRaises(Exception) as caught:
                await memory.memory_view(
                    db, simulation=refreshed, round_index=1,
                    platform_key="platform_b", config=config,
                )
        self.assertEqual(getattr(caught.exception, "code", ""), "round_not_available")

    async def test_sync_job_reuses_active_and_allows_new_after_terminal(self) -> None:
        simulation = await self._start()
        refreshed = await self._run(simulation)
        async with self.session_factory() as db:
            first, created = await memory.ensure_sync_job(db, simulation=refreshed)
            await db.commit()
            second, created_again = await memory.ensure_sync_job(db, simulation=refreshed)
            await db.commit()
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first, second)
        async with self.session_factory() as db:
            await db.execute(
                text("UPDATE jobs SET status = 'failed' WHERE id = :id"),
                {"id": first},
            )
            await db.commit()
        async with self.session_factory() as db:
            third, created_third = await memory.ensure_sync_job(
                db, simulation=refreshed
            )
            await db.commit()
            history = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM simulation_memory_syncs WHERE simulation_id = :s"
                    ),
                    {"s": simulation.id},
                )
            ).scalar_one()
        self.assertTrue(created_third)
        self.assertNotEqual(first, third)
        self.assertGreaterEqual(int(history), 2)

    async def test_memory_sync_job_reconciles_in_flight_episode(self) -> None:
        simulation = await self._start()
        refreshed = await self._run(simulation)
        async with self.session_factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT memory_id, add_count FROM simulation_memory_episodes "
                        "WHERE simulation_id = :s ORDER BY round_index, memory_id LIMIT 1"
                    ),
                    {"s": simulation.id},
                )
            ).first()
            await db.execute(
                text(
                    "UPDATE simulation_memory_episodes SET episode_uuid = NULL, "
                    "state = 'planned', write_outcome = 'in_flight' "
                    "WHERE simulation_id = :s AND memory_id = :m"
                ),
                {"s": simulation.id, "m": row.memory_id},
            )
            await db.execute(
                text(
                    "UPDATE simulations SET memory_status = 'pending' WHERE id = :s"
                ),
                {"s": simulation.id},
            )
            job_id, created = await memory.ensure_sync_job(db, simulation=refreshed)
            await db.commit()
        self.assertTrue(created)
        async with self.session_factory() as db:
            claim = await worker_service.claim_next(
                db,
                worker_id="memory-sync-test",
                lease_seconds=120.0,
                job_id=job_id,
                simulation_engine="double",
            )
        self.assertIsNotNone(claim)
        outcome = await memory.execute_memory_sync(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=120.0,
            heartbeat_seconds=30.0,
            config=memory.MemoryConfig.from_env(),
        )
        self.assertEqual(outcome, "succeeded")
        async with self.session_factory() as db:
            reconciled = (
                await db.execute(
                    text(
                        "SELECT add_count, episode_uuid, state FROM "
                        "simulation_memory_episodes WHERE simulation_id = :s AND memory_id = :m"
                    ),
                    {"s": simulation.id, "m": row.memory_id},
                )
            ).first()
            status = (
                await db.execute(
                    text("SELECT memory_status FROM simulations WHERE id = :s"),
                    {"s": simulation.id},
                )
            ).scalar_one()
        self.assertEqual(int(reconciled.add_count), int(row.add_count))
        self.assertIsNotNone(reconciled.episode_uuid)
        self.assertEqual(str(reconciled.state), "processed")
        self.assertEqual(str(status), "complete")

    async def test_round_view_boundaries(self) -> None:
        simulation = await self._start()
        refreshed = await self._run(simulation)
        config = memory.MemoryConfig.from_env()
        async with self.session_factory() as db:
            with self.assertRaises(Exception) as invalid:
                await memory.memory_view(
                    db, simulation=refreshed, round_index=0,
                    platform_key="platform_b", config=config,
                )
            self.assertEqual(getattr(invalid.exception, "code", ""), "round_invalid")
            with self.assertRaises(Exception) as platform:
                await memory.memory_view(
                    db, simulation=refreshed, round_index=1,
                    platform_key="twitter", config=config,
                )
            self.assertEqual(getattr(platform.exception, "code", ""), "platform_invalid")
