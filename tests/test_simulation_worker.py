"""双平台运行 worker：三轮完成、接手补记、不可确认的部分结果。"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engines.duet_config import DuetConfig
from app.engines.duet_contracts import KnownPost, parse_selection, plan_round
from app.engines.duet_engine import execute_round
from app.services import jobs as job_service
from app.services import simulation as store
from app.services import simulation_worker
from app.services import worker as worker_service
from tests.db_support import (
    HAS_TEST_DATABASE,
    SKIP_REASON,
    apply_all_migrations,
    make_test_engine,
    reset_public_schema,
)
from tests.simulation_support import expire_lease, prepare_selection


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class SimulationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)
        self.frozen = await prepare_selection(self.session_factory, marker="sim-worker")
        self.project_id = self.frozen.seed.project_id
        self.actor_id = self.frozen.seed.user_id
        self.data_dir = Path(tempfile.mkdtemp(prefix="echosim-sim-"))

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def config(self, **overrides) -> DuetConfig:
        values = dict(
            engine="double",
            allow_oasis=False,
            data_dir=self.data_dir,
            actions_per_round=6,
            round_timeout_seconds=120.0,
            round_delay_ms=0,
            max_actions=5000,
        )
        values.update(overrides)
        return DuetConfig(**values)

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

    async def _claim(self, simulation, *, lease_seconds: float = 120.0):
        async with self.session_factory() as db:
            claim = await worker_service.claim_next(
                db,
                worker_id="simulation-worker-test",
                lease_seconds=lease_seconds,
                job_id=simulation.job_id,
                simulation_engine="double",
            )
        self.assertIsNotNone(claim)
        return claim

    async def _execute(self, claim, config: DuetConfig) -> str:
        return await simulation_worker.execute_simulation(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=120.0,
            heartbeat_seconds=30.0,
            config=config,
        )

    async def _detail(self, simulation):
        async with self.session_factory() as db:
            refreshed = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
            return await store.serialize_simulation(db, refreshed)

    async def test_three_rounds_on_both_platforms(self) -> None:
        simulation = await self._start()
        claim = await self._claim(simulation)
        outcome = await self._execute(claim, self.config())
        self.assertEqual(outcome, "succeeded")
        detail = await self._detail(simulation)
        self.assertEqual(detail["phase"], "succeeded")
        self.assertFalse(detail["partial"])
        self.assertEqual(detail["engine"], "double")
        platforms = {item["platformKey"]: item for item in detail["platforms"]}
        self.assertEqual(platforms["platform_a"]["nativePlatform"], "twitter")
        self.assertEqual(platforms["platform_b"]["nativePlatform"], "reddit")
        for platform in platforms.values():
            self.assertEqual(platform["roundsCompleted"], 3)
            self.assertEqual(platform["status"], "succeeded")
            self.assertGreater(platform["actionCount"], 0)
        self.assertEqual(
            platforms["platform_a"]["actionCount"]
            + platforms["platform_b"]["actionCount"],
            detail["counts"]["total"],
        )
        self.assertGreater(len(detail["actions"]["items"]), 0)
        self.assertTrue(
            any(
                item["targetResolved"] and item["target"]
                for item in detail["actions"]["items"]
            )
        )
        async with self.session_factory() as db:
            seqs = [
                int(row.seq)
                for row in (
                    await db.execute(
                        text(
                            "SELECT seq FROM run_events WHERE run_id = :r ORDER BY seq"
                        ),
                        {"r": simulation.run_id},
                    )
                ).fetchall()
            ]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        # 四步各一条 + 两平台各三轮：共 10 条连续事件。
        self.assertEqual(len(seqs), 10)

    async def test_takeover_adopts_native_post_without_duplicates(self) -> None:
        simulation = await self._start()
        # 复现“原生帖子已落库、轮次标记尚未写入”的现场：直接写引擎状态。
        async with self.session_factory() as db:
            simulation_row = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
            plan = simulation_row.plan
            await db.commit()
        actions = plan_round(
            plan,
            platform_key="platform_a",
            round_index=1,
            run_marker=str(simulation.run_id),
            known_posts=[],
        )
        execute_round(
            mode="double",
            platform_key="platform_a",
            runtime_dir=self.data_dir / str(simulation.id) / "platform_a",
            round_index=1,
            actions=[
                {
                    "actionKey": action.action_key,
                    "actionIndex": action.action_index,
                    "nativeAction": action.native_action,
                    "actorRoleId": action.actor_role_id,
                    "actorAgentId": action.actor_agent_id,
                    "actorName": action.actor_name,
                    "content": action.content,
                    "marker": action.marker,
                    "targetActionKey": action.target_action_key,
                    "targetEngineRecordId": action.target_engine_record_id,
                }
                for action in actions
            ],
            fault="after_first_action",
        )
        claim = await self._claim(simulation)
        await expire_lease(self.session_factory, job_id=simulation.job_id)
        takeover = await self._claim(simulation)
        self.assertEqual(takeover.attempt, 2)
        outcome = await self._execute(takeover, self.config())
        self.assertEqual(outcome, "succeeded")
        detail = await self._detail(simulation)
        self.assertEqual(detail["phase"], "succeeded")
        async with self.session_factory() as db:
            attempts = await db.execute(
                text("SELECT attempts FROM jobs WHERE id = :id"),
                {"id": simulation.job_id},
            )
            self.assertEqual(int(attempts.scalar_one()), 2)
            duplicates = await db.execute(
                text(
                    "SELECT COUNT(*) FROM (SELECT engine_record_id FROM simulation_actions "
                    "WHERE simulation_id = :s AND platform_key = 'platform_a' "
                    "GROUP BY engine_record_id HAVING COUNT(*) > 1) AS dup"
                ),
                {"s": simulation.id},
            )
            self.assertEqual(int(duplicates.scalar_one()), 0)
        platforms = {item["platformKey"]: item for item in detail["platforms"]}
        self.assertEqual(platforms["platform_a"]["roundsCompleted"], 3)
        # 与一次干净完成对照：接手补记既不重复也不丢失动作。
        second = await self._start()
        second_claim = await self._claim(second)
        self.assertEqual(await self._execute(second_claim, self.config()), "succeeded")
        clean = await self._detail(second)
        clean_platforms = {item["platformKey"]: item for item in clean["platforms"]}
        for platform_key in ("platform_a", "platform_b"):
            self.assertEqual(
                platforms[platform_key]["actionCount"],
                clean_platforms[platform_key]["actionCount"],
            )
            self.assertEqual(
                platforms[platform_key]["byAction"],
                clean_platforms[platform_key]["byAction"],
            )

    async def test_unconfirmable_engine_effect_keeps_partial_results(self) -> None:
        simulation = await self._start()
        claim = await self._claim(simulation)
        outcome = await self._execute(
            claim,
            self.config(fault={"platform_a": ("1", "strip_marker")}),
        )
        self.assertEqual(outcome, "failed")
        detail = await self._detail(simulation)
        self.assertEqual(detail["phase"], "failed")
        self.assertTrue(detail["partial"])
        self.assertIn("platform_a", detail["failedPlatforms"])
        self.assertEqual(detail["failure"]["code"], "action_unconfirmed")
        platforms = {item["platformKey"]: item for item in detail["platforms"]}
        self.assertEqual(platforms["platform_a"]["status"], "failed")
        self.assertEqual(platforms["platform_b"]["status"], "succeeded")
        self.assertEqual(platforms["platform_b"]["roundsCompleted"], 3)
        self.assertGreater(platforms["platform_b"]["actionCount"], 0)


class SimulationRuntimeBoundaryTests(SimulationWorkerTests):
    """运行边界：平台独立推进、共享预算、单轮超时、清理失败与双平台失败。"""

    async def _engine_counts(self, simulation, config):
        return await simulation_worker.engine_counts(
            simulation_id=simulation.id, config=config
        )

    async def test_platforms_advance_independently(self) -> None:
        simulation = await self._start()
        claim = await self._claim(simulation)
        config = self.config(
            platform_delays={"platform_a": 700}, keep_runtime=True
        )
        task = asyncio.create_task(self._execute(claim, config))
        observed_gap = 0
        deadline = time.time() + 60
        while not task.done() and time.time() < deadline:
            async with self.session_factory() as db:
                rows = (
                    await db.execute(
                        text(
                            "SELECT platform_key, rounds_completed FROM "
                            "simulation_platforms WHERE simulation_id = :id"
                        ),
                        {"id": simulation.id},
                    )
                ).fetchall()
            completed = {row.platform_key: int(row.rounds_completed) for row in rows}
            observed_gap = max(
                observed_gap,
                completed.get("platform_b", 0) - completed.get("platform_a", 0),
            )
            await asyncio.sleep(0.1)
        self.assertEqual(await task, "succeeded")
        self.assertGreaterEqual(
            observed_gap, 1, "慢平台不应阻塞另一平台提交下一轮"
        )
        detail = await self._detail(simulation)
        for platform in detail["platforms"]:
            self.assertEqual(platform["roundsCompleted"], 3)

    async def test_shared_budget_blocks_before_engine_call(self) -> None:
        simulation = await self._start()
        claim = await self._claim(simulation)
        config = self.config(
            max_actions=5, actions_per_round=2, keep_runtime=True
        )
        outcome = await self._execute(claim, config)
        self.assertEqual(outcome, "failed")
        detail = await self._detail(simulation)
        self.assertEqual(detail["phase"], "failed")
        self.assertLessEqual(detail["counts"]["total"], 5)
        codes = {
            platform["failure"]["code"]
            for platform in detail["platforms"]
            if platform["failure"]
        }
        self.assertIn("simulation_too_large", codes)
        engine = await self._engine_counts(simulation, config)
        for platform in detail["platforms"]:
            native = {
                action: count
                for action, count in engine.get(platform["platformKey"], {}).items()
                if count
            }
            self.assertEqual(native, dict(platform["byAction"]))
        async with self.session_factory() as db:
            duplicates = (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM (SELECT engine_record_id FROM "
                        "simulation_actions WHERE simulation_id = :s "
                        "GROUP BY platform_key, engine_record_id "
                        "HAVING COUNT(*) > 1) AS dup"
                    ),
                    {"s": simulation.id},
                )
            ).scalar_one()
        self.assertEqual(int(duplicates), 0)

    async def test_round_timeout_books_confirmed_actions_once(self) -> None:
        simulation = await self._start()
        claim = await self._claim(simulation)
        config = self.config(round_timeout_seconds=0.25, keep_runtime=True)
        with patch.dict(os.environ, {"DUET_CHILD_SLEEP_MS": "600"}):
            outcome = await self._execute(claim, config)
        self.assertEqual(outcome, "failed")
        detail = await self._detail(simulation)
        self.assertEqual(detail["phase"], "failed")
        self.assertEqual(
            {platform["failure"]["code"] for platform in detail["platforms"]},
            {"simulation_timeout"},
        )
        engine = await self._engine_counts(simulation, config)
        for platform in detail["platforms"]:
            native = {
                action: count
                for action, count in engine.get(platform["platformKey"], {}).items()
                if count
            }
            self.assertEqual(native, dict(platform["byAction"]))
        async with self.session_factory() as db:
            rounds = (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM simulation_rounds WHERE simulation_id = :s"
                    ),
                    {"s": simulation.id},
                )
            ).scalar_one()
        # 超时的那一轮只记账已读回的动作，且不推进“已完成轮次”。
        self.assertEqual(int(rounds), 2)
        self.assertEqual(
            {platform["roundsCompleted"] for platform in detail["platforms"]}, {0}
        )
        async with self.session_factory() as db:
            outcomes = (
                await db.execute(
                    text(
                        "SELECT DISTINCT engine_write_outcome FROM simulation_rounds "
                        "WHERE simulation_id = :s"
                    ),
                    {"s": simulation.id},
                )
            ).fetchall()
        self.assertEqual([row.engine_write_outcome for row in outcomes], ["unconfirmed"])

    async def test_cleanup_failure_keeps_terminal_state(self) -> None:
        simulation = await self._start()
        claim = await self._claim(simulation)
        config = self.config(cleanup_fail=("platform_a",))
        self.assertEqual(await self._execute(claim, config), "succeeded")
        detail = await self._detail(simulation)
        self.assertEqual(detail["phase"], "succeeded")
        self.assertFalse(detail["partial"])
        self.assertIsNotNone(detail["cleanup"])
        self.assertIn("platform_a_cleanup", detail["cleanup"]["code"])
        self.assertGreater(detail["counts"]["total"], 0)
        async with self.session_factory() as db:
            job_status = (
                await db.execute(
                    text("SELECT status FROM jobs WHERE id = :id"),
                    {"id": simulation.job_id},
                )
            ).scalar_one()
        self.assertEqual(str(job_status), "succeeded")

    async def test_both_platforms_fail_after_first_round(self) -> None:
        simulation = await self._start()
        claim = await self._claim(simulation)
        config = self.config(
            fault={
                "platform_a": ("2", "strip_marker"),
                "platform_b": ("2", "strip_marker"),
            },
            keep_runtime=True,
        )
        self.assertEqual(await self._execute(claim, config), "failed")
        detail = await self._detail(simulation)
        self.assertEqual(detail["phase"], "failed")
        self.assertEqual(
            sorted(detail["failedPlatforms"]), ["platform_a", "platform_b"]
        )
        platforms = {item["platformKey"]: item for item in detail["platforms"]}
        for platform_key in ("platform_a", "platform_b"):
            self.assertEqual(platforms[platform_key]["status"], "failed")
            self.assertEqual(
                platforms[platform_key]["failure"]["code"], "action_unconfirmed"
            )
            self.assertGreaterEqual(platforms[platform_key]["actionCount"], 1)
            self.assertEqual(platforms[platform_key]["roundsCompleted"], 1)
        engine = await self._engine_counts(simulation, config)
        for platform_key in ("platform_a", "platform_b"):
            native = {
                action: count
                for action, count in engine.get(platform_key, {}).items()
                if count
            }
            booked = dict(platforms[platform_key]["byAction"])
            # 第一轮动作全部保留；第二轮只有那一条不可确认的动作留在引擎里。
            self.assertEqual(
                sum(native.values()) - sum(booked.values()),
                1,
                f"{platform_key} 未记账动作应只有不可确认的那一条",
            )
            for action, count in booked.items():
                self.assertLessEqual(count, native.get(action, 0))

    async def test_stop_request_is_not_terminal_until_worker_boundary(self) -> None:
        simulation = await self._start()
        async with self.session_factory() as db:
            job, applied = await job_service.cancel_job(
                db, project_id=self.project_id, job_id=simulation.job_id
            )
        self.assertTrue(applied)
        self.assertNotEqual(job.status, "canceled")
        self.assertIsNotNone(job.cancel_requested_at)
        detail = await self._detail(simulation)
        self.assertTrue(detail["stopRequested"])
        self.assertFalse(detail["terminal"])

        claim = await self._claim(simulation)
        self.assertEqual(await self._execute(claim, self.config()), "canceled")
        detail = await self._detail(simulation)
        self.assertEqual(detail["phase"], "canceled")
        self.assertEqual(detail["counts"]["total"], 0)
        async with self.session_factory() as db:
            events = (
                await db.execute(
                    text(
                        "SELECT type FROM run_events WHERE run_id = :r "
                        "AND type IN ('completed', 'failed', 'canceled')"
                    ),
                    {"r": simulation.run_id},
                )
            ).fetchall()
            job_status = (
                await db.execute(
                    text("SELECT status FROM jobs WHERE id = :id"),
                    {"id": simulation.job_id},
                )
            ).scalar_one()
        self.assertEqual([row.type for row in events], ["canceled"])
        self.assertEqual(str(job_status), "canceled")


class SimulationClockTests(SimulationWorkerTests):
    """推演时钟：从首发角色最早活跃小时起算，调度/动作/详情共用。"""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()

    async def test_clock_starts_at_first_speaker_hour(self) -> None:
        engine = make_test_engine()
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        await reset_public_schema(engine)
        await apply_all_migrations(engine)
        self.addAsyncCleanup(engine.dispose)
        frozen = await prepare_selection(
            session_factory,
            marker="clock",
            first_speaker_hours=[18, 19, 20, 21],
        )
        self.project_id = frozen.seed.project_id
        self.actor_id = frozen.seed.user_id
        self.frozen = frozen
        self.session_factory = session_factory

        simulation = await self._start()
        claim = await self._claim(simulation)
        self.assertEqual(await self._execute(claim, self.config()), "succeeded")
        detail = await self._detail(simulation)
        start = datetime.fromisoformat(detail["simulatedStart"].replace("Z", "+00:00"))
        self.assertEqual((start.hour, start.minute), (18, 0))
        first_action = detail["actions"]["items"][0]
        first_at = datetime.fromisoformat(
            first_action["simulatedAt"].replace("Z", "+00:00")
        )
        self.assertEqual((first_at.hour, first_at.minute), (18, 0))
        final = datetime.fromisoformat(
            detail["simulatedAt"].replace("Z", "+00:00")
        )
        self.assertEqual((final.hour, final.minute), (21, 0))
        refreshed = await self._detail(simulation)
        self.assertEqual(refreshed["simulatedStart"], detail["simulatedStart"])
        self.assertEqual(refreshed["simulatedAt"], detail["simulatedAt"])
        async with self.session_factory() as db:
            epoch = (
                await db.execute(
                    text("SELECT simulated_epoch FROM runs WHERE id = :r"),
                    {"r": simulation.run_id},
                )
            ).scalar_one()
        self.assertEqual(epoch.hour, 18)
