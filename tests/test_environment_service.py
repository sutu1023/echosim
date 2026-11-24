"""角色与环境准备服务的真实 PostgreSQL 测试：绑定、复用、重试、轮数与冻结。"""

from __future__ import annotations

import dataclasses
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engines.environment_config import EnvironmentConfig
from app.engines.environment_model import create_profiler
from app.engines.model import HttpResponse
from app.errors import (
    EnvironmentBuildNotFound,
    EnvironmentConflict,
    EnvironmentPreparationNotFound,
)
from app.services import environment as store
from app.services import jobs as job_service
from app.services import worker as worker_service
from tests.db_support import (
    HAS_TEST_DATABASE,
    SKIP_REASON,
    apply_all_migrations,
    make_test_engine,
    reset_public_schema,
)
from tests.environment_support import (
    QUESTION,
    claim_preparation,
    scalar,
    seed_rich_graph,
)

CONFIG = EnvironmentConfig.from_env(
    {
        "ENVIRONMENT_MODEL_PROVIDER": "stub",
        "ENVIRONMENT_ALLOW_STUB": "true",
        "ENVIRONMENT_PROFILE_BATCH_SIZE": "2",
    }
)


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class EnvironmentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)
        self.seed = await seed_rich_graph(
            self.session_factory, marker="service"
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _start(self, *, retry: bool = False):
        async with self.session_factory() as db:
            return await store.start_preparation(
                db,
                project_id=self.seed.project_id,
                actor_id=self.seed.user_id,
                build_id=self.seed.build_id,
                config=CONFIG,
                retry=retry,
            )

    async def _set_phase(self, preparation_id: uuid.UUID, phase: str) -> None:
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE environment_preparations SET phase = :phase, "
                    "failure_code = CASE WHEN :phase = 'failed' THEN 'model_unavailable' "
                    "ELSE NULL END, finished_at = now() WHERE id = :prep"
                ),
                {"phase": phase, "prep": preparation_id},
            )
            await db.execute(
                text(
                    "UPDATE jobs SET status = :status, finished_at = now(), "
                    "terminal_reason = CASE WHEN :status = 'failed' "
                    "THEN 'model_unavailable' ELSE NULL END, "
                    "lease_token = NULL, lease_expires_at = NULL "
                    "WHERE run_id = (SELECT run_id FROM environment_preparations "
                    "WHERE id = :prep)"
                ),
                {
                    "status": "failed" if phase == "failed" else "canceled",
                    "prep": preparation_id,
                },
            )
            await db.commit()

    async def test_context_binds_ready_build_and_rejects_others(self) -> None:
        async with self.session_factory() as db:
            context = await store.read_context(
                db, project_id=self.seed.project_id, build_id=self.seed.build_id
            )
        self.assertTrue(context.can_prepare)
        self.assertIsNone(context.reason)
        self.assertEqual(context.snapshot.id, self.seed.snapshot_id)
        self.assertEqual(context.build.question, QUESTION)

        other = await seed_rich_graph(
            self.session_factory, marker="not-continue", build_can_continue=False
        )
        async with self.session_factory() as db:
            context = await store.read_context(
                db, project_id=other.project_id, build_id=other.build_id
            )
        self.assertFalse(context.can_prepare)
        self.assertEqual(context.reason, "graph_not_ready")

        with self.assertRaises(EnvironmentBuildNotFound):
            async with self.session_factory() as db:
                await store.read_context(
                    db, project_id=self.seed.project_id, build_id=uuid.uuid4()
                )

    async def test_start_reuses_active_and_requires_explicit_retry(self) -> None:
        first, created = await self._start()
        self.assertTrue(created)
        self.assertEqual(first.phase, "preparing")
        self.assertEqual(first.rounds, 72)
        self.assertEqual(first.settings_revision, 1)

        second, created_again = await self._start()
        self.assertFalse(created_again)
        self.assertEqual(
            (second.id, second.run_id, second.job_id),
            (first.id, first.run_id, first.job_id),
        )
        self.assertEqual(
            await scalar(
                self.session_factory,
                "SELECT count(*) FROM environment_preparations WHERE build_id = :b",
                {"b": self.seed.build_id},
            ),
            1,
        )

        await self._set_phase(first.id, "failed")
        with self.assertRaises(EnvironmentConflict) as caught:
            await self._start()
        self.assertEqual(caught.exception.code, "preparation_retry_required")
        self.assertEqual(
            await scalar(
                self.session_factory,
                "SELECT count(*) FROM environment_preparations WHERE build_id = :b",
                {"b": self.seed.build_id},
            ),
            1,
        )

        retried, created_retry = await self._start(retry=True)
        self.assertTrue(created_retry)
        self.assertNotEqual((retried.id, retried.run_id, retried.job_id), (first.id, first.run_id, first.job_id))
        self.assertEqual(retried.phase, "preparing")
        async with self.session_factory() as db:
            old = await store.get_preparation(
                db, project_id=self.seed.project_id, preparation_id=first.id
            )
        self.assertEqual(old.phase, "failed")

    async def test_retry_is_rejected_for_active_preparation(self) -> None:
        preparation, _ = await self._start()
        with self.assertRaises(EnvironmentConflict) as caught:
            await self._start(retry=True)
        self.assertEqual(caught.exception.code, "preparation_not_retryable")
        async with self.session_factory() as db:
            current = await store.get_preparation(
                db, project_id=self.seed.project_id, preparation_id=preparation.id
            )
        self.assertEqual(current.id, preparation.id)

    async def test_no_role_candidates_creates_no_records(self) -> None:
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE graph_nodes SET node_type = '事件' "
                    "WHERE snapshot_id = :snapshot_id"
                ),
                {"snapshot_id": self.seed.snapshot_id},
            )
            await db.commit()
        before = await scalar(self.session_factory, "SELECT count(*) FROM runs")
        with self.assertRaises(EnvironmentConflict) as caught:
            await self._start()
        self.assertEqual(caught.exception.code, "no_role_candidates")
        self.assertEqual(
            await scalar(self.session_factory, "SELECT count(*) FROM runs"), before
        )
        self.assertEqual(
            await scalar(
                self.session_factory,
                "SELECT count(*) FROM environment_preparations",
            ),
            0,
        )

    async def test_preparation_job_not_in_test_panel(self) -> None:
        preparation, _ = await self._start()
        async with self.session_factory() as db:
            test_job, created = await job_service.start_job(
                db,
                project_id=self.seed.project_id,
                actor_id=self.seed.user_id,
                stage="report",
                fail_at_step=None,
            )
            listed = await job_service.list_jobs(
                db, project_id=self.seed.project_id
            )
        self.assertTrue(created)
        self.assertNotIn(
            preparation.job_id, {item.id for item in listed}
        )
        self.assertIn(test_job.id, {item.id for item in listed})

    async def test_configuration_defaults_save_readback_and_errors(self) -> None:
        preparation, _ = await self._start()
        async with self.session_factory() as db:
            saved = await store.update_configuration(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
                settings_revision=1,
                duration_hours=72,
                minutes_per_round=60,
                rounds_mode="manual",
                manual_rounds=40,
                config=CONFIG,
            )
        self.assertEqual(saved.rounds, 40)
        self.assertEqual(saved.settings_revision, 2)
        async with self.session_factory() as db:
            reread = await store.get_preparation(
                db, project_id=self.seed.project_id, preparation_id=preparation.id
            )
        self.assertEqual(reread.rounds, 40)
        self.assertEqual(reread.settings.auto_rounds, 72)

        with self.assertRaises(EnvironmentConflict) as caught:
            async with self.session_factory() as db:
                await store.update_configuration(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=preparation.id,
                    settings_revision=1,
                    duration_hours=72,
                    minutes_per_round=60,
                    rounds_mode="manual",
                    manual_rounds=3,
                    config=CONFIG,
                )
        self.assertEqual(caught.exception.code, "settings_conflict")
        async with self.session_factory() as db:
            unchanged = await store.get_preparation(
                db, project_id=self.seed.project_id, preparation_id=preparation.id
            )
        self.assertEqual(unchanged.rounds, 40)

        cases = [
            (0, 60, "auto", None, "duration_invalid"),
            (72, 60, "manual", 0, "rounds_invalid"),
            (2, 90, "auto", None, "rounds_not_integral"),
        ]
        current_revision = 2
        for hours, minutes, mode, manual, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(EnvironmentConflict) as caught:
                    async with self.session_factory() as db:
                        await store.update_configuration(
                            db,
                            project_id=self.seed.project_id,
                            preparation_id=preparation.id,
                            settings_revision=current_revision,
                            duration_hours=hours,
                            minutes_per_round=minutes,
                            rounds_mode=mode,
                            manual_rounds=manual,
                            config=CONFIG,
                        )
                self.assertEqual(caught.exception.code, code)

    async def test_confirm_before_ready_is_rejected_without_frozen_record(self) -> None:
        preparation, _ = await self._start()
        with self.assertRaises(EnvironmentConflict) as caught:
            async with self.session_factory() as db:
                await store.confirm_preparation(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=preparation.id,
                    settings_revision=1,
                )
        self.assertEqual(caught.exception.code, "preparation_not_ready")
        self.assertEqual(
            await scalar(
                self.session_factory,
                "SELECT count(*) FROM environment_frozen_selections",
            ),
            0,
        )

    async def test_unknown_preparation_is_not_found(self) -> None:
        with self.assertRaises(EnvironmentPreparationNotFound):
            async with self.session_factory() as db:
                await store.get_preparation(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=uuid.uuid4(),
                )

    async def test_database_constraints_reject_cross_snapshot_and_duplicates(self) -> None:
        preparation, _ = await self._start()
        await self._set_phase(preparation.id, "failed")
        other_seed = await seed_rich_graph(
            self.session_factory, marker="constraints-other"
        )
        async with self.session_factory() as db:
            retried, created = await store.start_preparation(
                db,
                project_id=self.seed.project_id,
                actor_id=self.seed.user_id,
                build_id=self.seed.build_id,
                config=CONFIG,
                retry=True,
            )
        self.assertTrue(created)
        async with self.session_factory() as db:
            # 同一 build 已有活动准备时，旧失败准备不能回到 preparing。
            with self.assertRaises(IntegrityError):
                await db.execute(
                    text(
                        "UPDATE environment_preparations SET phase = 'preparing' "
                        "WHERE id = :prep"
                    ),
                    {"prep": preparation.id},
                )
            await db.rollback()
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE environment_preparations SET phase = 'failed' "
                    "WHERE id = :prep"
                ),
                {"prep": retried.id},
            )
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'failed', finished_at = now(), "
                    "lease_token = NULL, lease_expires_at = NULL WHERE id = :job"
                ),
                {"job": retried.job_id},
            )
            await db.commit()

        async with self.session_factory() as db:
            # 角色节点必须属于准备绑定的同一快照。
            with self.assertRaises(IntegrityError):
                await db.execute(
                    text(
                        "INSERT INTO environment_roles (role_id, preparation_id, "
                        "snapshot_id, node_key, display_name, role_type, batch_index) "
                        "VALUES (:role_id, :prep, :snapshot_id, 'p1', '张同学', "
                        "'个人', 0)"
                    ),
                    {
                        "role_id": uuid.uuid4(),
                        "prep": preparation.id,
                        "snapshot_id": other_seed.snapshot_id,
                    },
                )
            await db.rollback()

        async with self.session_factory() as db:
            # 平台键与原生平台映射固定。
            with self.assertRaises(IntegrityError):
                await db.execute(
                    text(
                        "INSERT INTO environment_platforms (preparation_id, "
                        "platform_key, native_platform) VALUES (:prep, "
                        "'platform_a', 'reddit')"
                    ),
                    {"prep": preparation.id},
                )
            await db.rollback()

        async with self.session_factory() as db:
            # 最终角色标记必须先有预览时间。
            with self.assertRaises(IntegrityError):
                await db.execute(
                    text(
                        "INSERT INTO environment_roles (role_id, preparation_id, "
                        "snapshot_id, node_key, display_name, role_type, batch_index, "
                        "preview_ready_at, published_at) VALUES (:role_id, :prep, "
                        ":snapshot_id, 'p1', '张同学', '个人', 0, NULL, now())"
                    ),
                    {
                        "role_id": uuid.uuid4(),
                        "prep": preparation.id,
                        "snapshot_id": self.seed.snapshot_id,
                    },
                )
            await db.rollback()

    async def test_frozen_selection_is_unique_per_preparation(self) -> None:
        preparation, _ = await self._start()
        async with self.session_factory() as db:
            payload = '{"selectionId":"x"}'
            await db.execute(
                text(
                    "INSERT INTO environment_frozen_selections (selection_id, "
                    "preparation_id, project_id, build_id, snapshot_id, "
                    "settings_revision, payload) VALUES (:selection, :prep, "
                    ":project, :build, :snapshot, 1, CAST(:payload AS jsonb))"
                ),
                {
                    "selection": uuid.uuid4(),
                    "prep": preparation.id,
                    "project": self.seed.project_id,
                    "build": self.seed.build_id,
                    "snapshot": self.seed.snapshot_id,
                    "payload": payload,
                },
            )
            with self.assertRaises(IntegrityError):
                await db.execute(
                    text(
                        "INSERT INTO environment_frozen_selections (selection_id, "
                        "preparation_id, project_id, build_id, snapshot_id, "
                        "settings_revision, payload) VALUES (:selection, :prep, "
                        ":project, :build, :snapshot, 1, CAST(:payload AS jsonb))"
                    ),
                    {
                        "selection": uuid.uuid4(),
                        "prep": preparation.id,
                        "project": self.seed.project_id,
                        "build": self.seed.build_id,
                        "snapshot": self.seed.snapshot_id,
                        "payload": payload,
                    },
                )
            await db.rollback()


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class EnvironmentWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)
        self.seed = await seed_rich_graph(self.session_factory, marker="worker")

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _start(self):
        async with self.session_factory() as db:
            preparation, created = await store.start_preparation(
                db,
                project_id=self.seed.project_id,
                actor_id=self.seed.user_id,
                build_id=self.seed.build_id,
                config=CONFIG,
                retry=False,
            )
        self.assertTrue(created)
        return preparation

    async def _execute(self, preparation, profiler_factory=None):
        claim = await claim_preparation(
            self.session_factory, job_id=preparation.job_id
        )
        return await worker_service.execute_preparation(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
            profiler_factory=profiler_factory
            or (lambda metrics=None: create_profiler(CONFIG, metrics=metrics)),
        )

    async def test_five_steps_publish_and_freeze(self) -> None:
        preparation = await self._start()
        outcome = await self._execute(preparation)
        self.assertEqual(outcome, "succeeded")

        async with self.session_factory() as db:
            detail = await store.serialize_preparation(
                db,
                await store.get_preparation(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=preparation.id,
                ),
            )
        self.assertEqual(detail["phase"], "ready")
        self.assertEqual(detail["previewRoles"], [])
        self.assertEqual(len(detail["roles"]), 5)
        names = {role["displayName"] for role in detail["roles"]}
        self.assertEqual(
            names, {"张同学", "某校学生群体", "某大学", "某报社", "校友李某"}
        )
        self.assertEqual(
            detail["platforms"],
            [
                {"platformKey": "platform_a", "nativePlatform": "twitter", "enabled": True},
                {"platformKey": "platform_b", "nativePlatform": "reddit", "enabled": True},
            ],
        )
        self.assertTrue(
            any(topic["origin"] == "question" for topic in detail["initialTopics"])
        )
        self.assertTrue(
            any(topic["origin"] == "material" for topic in detail["initialTopics"])
        )
        profile_topics = [
            topic
            for topic in detail["initialTopics"]
            if topic["text"].startswith("模拟延伸话题：")
        ]
        self.assertTrue(profile_topics, "批次 initialTopics 未进入最终配置")
        self.assertTrue(
            all(
                topic["origin"] == "simulation"
                and topic["claimNature"] is None
                and topic["claimNatureKnown"] is False
                for topic in profile_topics
            )
        )
        role_ids = [role["roleId"] for role in detail["roles"]]
        order = detail["activation"]["order"]
        self.assertEqual(set(order), set(role_ids))
        self.assertEqual(len(order), len(role_ids))
        first = detail["activation"]["firstSpeakerRoleIds"]
        self.assertTrue(first)
        self.assertEqual(order[: len(first)], first)
        topic_ids = {topic["topicId"] for topic in detail["initialTopics"]}
        mapped = {
            item["roleId"]: item["topicId"]
            for item in detail["activation"]["firstSpeakerTopics"]
        }
        self.assertEqual(set(mapped), set(first))
        self.assertTrue(set(mapped.values()).issubset(topic_ids))
        self.assertIsNone(detail["frozenSelection"])
        self.assertFalse(detail["canContinue"])

        async with self.session_factory() as db:
            steps = (
                await db.execute(
                    text(
                        "SELECT step_key FROM run_steps WHERE run_id = :run "
                        "ORDER BY step_index"
                    ),
                    {"run": preparation.run_id},
                )
            ).fetchall()
            events = (
                await db.execute(
                    text(
                        "SELECT seq, type, payload::text AS payload FROM run_events "
                        "WHERE run_id = :run ORDER BY seq"
                    ),
                    {"run": preparation.run_id},
                )
            ).fetchall()
        self.assertEqual(
            [row.step_key for row in steps],
            ["bind", "select", "profile", "compose", "publish"],
        )
        self.assertEqual([int(row.seq) for row in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[-1].type, "completed")
        self.assertIn('"phase": "ready"', events[-1].payload)
        serialized = " ".join(row.payload for row in events)
        self.assertNotIn("张同学", serialized)
        self.assertNotIn("主张调整", serialized)
        self.assertNotIn("某报社报道某大学", serialized)

        async with self.session_factory() as db:
            refreshed = await store.get_preparation(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
            )
        self.assertEqual(refreshed.provider_calls_real, 0)
        self.assertGreater(refreshed.provider_calls_double, 0)

        # 72 → 40 → 两页并发确认 → 冲突后重读确认 3。
        async with self.session_factory() as db:
            saved_40 = await store.update_configuration(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
                settings_revision=1,
                duration_hours=72,
                minutes_per_round=60,
                rounds_mode="manual",
                manual_rounds=40,
                config=CONFIG,
            )
        self.assertEqual(saved_40.settings_revision, 2)
        async with self.session_factory() as db:
            saved_3 = await store.update_configuration(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
                settings_revision=2,
                duration_hours=72,
                minutes_per_round=60,
                rounds_mode="manual",
                manual_rounds=3,
                config=CONFIG,
            )
        self.assertEqual(saved_3.settings_revision, 3)

        with self.assertRaises(EnvironmentConflict) as caught:
            async with self.session_factory() as db:
                await store.confirm_preparation(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=preparation.id,
                    settings_revision=2,
                )
        self.assertEqual(caught.exception.code, "settings_conflict")
        self.assertEqual(
            await scalar(
                self.session_factory,
                "SELECT count(*) FROM environment_frozen_selections",
            ),
            0,
        )

        async with self.session_factory() as db:
            frozen_prep, frozen = await store.confirm_preparation(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
                settings_revision=3,
            )
        self.assertEqual(frozen_prep.settings_revision, 3)
        self.assertEqual(frozen["settings"]["rounds"], 3)
        self.assertEqual(frozen["buildId"], str(self.seed.build_id))
        self.assertEqual(frozen["snapshotId"], str(self.seed.snapshot_id))
        self.assertEqual(frozen["question"], QUESTION)
        self.assertEqual(
            frozen["platforms"],
            [
                {"platformKey": "platform_a", "nativePlatform": "twitter"},
                {"platformKey": "platform_b", "nativePlatform": "reddit"},
            ],
        )
        self.assertNotIn("create_post", str(frozen))
        self.assertNotIn("create_comment", str(frozen))
        self.assertTrue(frozen["initialTopics"])
        self.assertTrue(
            all(
                topic["origin"] in ("question", "material", "simulation")
                for topic in frozen["initialTopics"]
            )
        )
        first = frozen["activation"]["firstSpeakerRoleIds"]
        self.assertTrue(first)
        mapped = {
            item["roleId"]: item["topicId"]
            for item in frozen["activation"]["firstSpeakerTopics"]
        }
        self.assertEqual(set(mapped), set(first))
        self.assertEqual(
            [role["roleId"] for role in frozen["roles"]],
            frozen["activation"]["order"],
        )

        async with self.session_factory() as db:
            again_prep, again = await store.confirm_preparation(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
                settings_revision=3,
            )
        self.assertEqual(again["selectionId"], frozen["selectionId"])
        self.assertEqual(
            await scalar(
                self.session_factory,
                "SELECT count(*) FROM environment_frozen_selections",
            ),
            1,
        )

        with self.assertRaises(EnvironmentConflict) as caught:
            async with self.session_factory() as db:
                await store.update_configuration(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=preparation.id,
                    settings_revision=3,
                    duration_hours=72,
                    minutes_per_round=60,
                    rounds_mode="manual",
                    manual_rounds=40,
                    config=CONFIG,
                )
        self.assertEqual(caught.exception.code, "preparation_frozen")

    async def test_first_batch_preview_then_resume_without_duplicates(self) -> None:
        preparation = await self._start()
        claim = await claim_preparation(
            self.session_factory, job_id=preparation.job_id
        )
        stop = __import__("asyncio").Event()

        class StopAfterFirst:
            def __init__(self, metrics=None) -> None:
                self.inner = create_profiler(CONFIG, metrics=metrics)

            def profile(self, **kwargs):
                result = self.inner.profile(**kwargs)
                stop.set()
                return result

        await worker_service._prep_bind(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
        )
        await worker_service._prep_select(
            claim,
            session_factory=self.session_factory,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
        )
        profile_claim = __import__("dataclasses").replace(claim, next_step=2)
        outcome = await worker_service._prep_profile(
            profile_claim,
            session_factory=self.session_factory,
            stop_event=stop,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
            profiler_factory=StopAfterFirst,
        )
        self.assertEqual(outcome, "stopped")
        async with self.session_factory() as db:
            detail = await store.serialize_preparation(
                db,
                await store.get_preparation(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=preparation.id,
                ),
            )
        self.assertEqual(detail["phase"], "preparing")
        self.assertEqual(len(detail["previewRoles"]), 2)
        self.assertEqual(len(detail["roles"]), 0)
        self.assertTrue(detail["previewOnly"])
        self.assertEqual(detail["platforms"], [])
        self.assertEqual(detail["initialTopics"], [])
        self.assertIsNone(detail["activation"])
        self.assertFalse(detail["canContinue"])
        self.assertGreater(detail["progress"]["previewBatchCount"], 0)

        # 第二轮从 profile 继续：第一批不重复生成，第二批补齐。
        stop.clear()
        resumed = __import__("dataclasses").replace(claim, next_step=2)
        outcome = await worker_service._prep_profile(
            resumed,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
            profiler_factory=lambda metrics=None: create_profiler(
                CONFIG, metrics=metrics
            ),
        )
        self.assertEqual(outcome, "progress")
        async with self.session_factory() as db:
            roles = await store.load_roles(
                db,
                preparation=await store.get_preparation(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=preparation.id,
                ),
            )
        self.assertEqual(len(roles), 5)
        self.assertTrue(all(role.preview_ready_at is not None for role in roles))
        self.assertGreater(
            await scalar(
                self.session_factory,
                "SELECT count(*) FROM environment_role_sources WHERE preparation_id = :p",
                {"p": preparation.id},
            ),
            0,
        )

        publish_claim = __import__("dataclasses").replace(claim, next_step=3)
        outcome = await worker_service.execute_preparation(
            publish_claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
            profiler_factory=lambda metrics=None: create_profiler(
                CONFIG, metrics=metrics
            ),
        )
        self.assertEqual(outcome, "succeeded")

    async def test_provider_attempts_persist_on_failure(self) -> None:
        preparation = await self._start()
        calls: list[int] = []

        def failing_transport(url, headers, payload, timeout):
            del url, headers, payload, timeout
            calls.append(1)
            return HttpResponse(500, "{}")

        model_config = EnvironmentConfig.from_env(
            {
                "MODEL_API_BASE_URL": "https://model.test.local/v1",
                "MODEL_API_KEY": "test-key",
                "MODEL_NAME": "test-model",
                "ENVIRONMENT_MODEL_PROVIDER": "http",
            }
        )
        outcome = await self._execute(
            preparation,
            profiler_factory=lambda metrics=None: create_profiler(
                model_config,
                transport=failing_transport,
                metrics=metrics,
            ),
        )
        self.assertEqual(outcome, "failed")
        self.assertEqual(len(calls), 2)
        async with self.session_factory() as db:
            failed = await store.get_preparation(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
            )
        self.assertEqual(failed.provider_calls_real, 2)
        self.assertEqual(failed.provider_calls_double, 0)

    async def test_invalid_model_output_still_counts_attempt(self) -> None:
        preparation = await self._start()
        calls: list[int] = []

        def invalid_transport(url, headers, payload, timeout):
            del url, headers, payload, timeout
            calls.append(1)
            return HttpResponse(
                200,
                '{"choices":[{"message":{"content":"not json"}}]}',
            )

        model_config = EnvironmentConfig.from_env(
            {
                "MODEL_API_BASE_URL": "https://model.test.local/v1",
                "MODEL_API_KEY": "test-key",
                "MODEL_NAME": "test-model",
                "ENVIRONMENT_MODEL_PROVIDER": "http",
            }
        )
        outcome = await self._execute(
            preparation,
            profiler_factory=lambda metrics=None: create_profiler(
                model_config,
                transport=invalid_transport,
                metrics=metrics,
            ),
        )
        self.assertEqual(outcome, "failed")
        self.assertEqual(len(calls), 1)
        async with self.session_factory() as db:
            failed = await store.get_preparation(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
            )
        self.assertEqual(failed.failure_code, "invalid_model_output")
        self.assertEqual(failed.provider_calls_real, 1)

    async def test_model_unavailable_fails_without_final_state(self) -> None:
        preparation = await self._start()
        unavailable = EnvironmentConfig.from_env(
            {"ENVIRONMENT_MODEL_PROVIDER": "unconfigured"}
        )
        outcome = await self._execute(
            preparation,
            profiler_factory=lambda metrics=None: create_profiler(
                unavailable, metrics=metrics
            ),
        )
        self.assertEqual(outcome, "failed")
        async with self.session_factory() as db:
            failed = await store.get_preparation(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
            )
            detail = await store.serialize_preparation(db, failed)
            published = await scalar(
                db,
                "SELECT count(*) FROM environment_roles "
                "WHERE preparation_id = :p AND published_at IS NOT NULL",
                {"p": preparation.id},
            )
            frozen = await scalar(
                db,
                "SELECT count(*) FROM environment_frozen_selections "
                "WHERE preparation_id = :p",
                {"p": preparation.id},
            )
        self.assertEqual(failed.phase, "failed")
        self.assertEqual(failed.failure_code, "model_unavailable")
        self.assertFalse(detail["canContinue"])
        self.assertEqual(detail["platforms"], [])
        self.assertEqual(detail["roles"], [])
        self.assertEqual(published, 0)
        self.assertEqual(frozen, 0)

        with self.assertRaises(EnvironmentConflict) as caught:
            async with self.session_factory() as db:
                await store.start_preparation(
                    db,
                    project_id=self.seed.project_id,
                    actor_id=self.seed.user_id,
                    build_id=self.seed.build_id,
                    config=CONFIG,
                    retry=False,
                )
        self.assertEqual(caught.exception.code, "preparation_retry_required")

        async with self.session_factory() as db:
            retried, created = await store.start_preparation(
                db,
                project_id=self.seed.project_id,
                actor_id=self.seed.user_id,
                build_id=self.seed.build_id,
                config=CONFIG,
                retry=True,
            )
        self.assertTrue(created)
        self.assertNotEqual(retried.id, preparation.id)

    async def test_cancel_keeps_preview_only_and_terminal(self) -> None:
        preparation = await self._start()
        claim = await claim_preparation(
            self.session_factory, job_id=preparation.job_id
        )
        await worker_service._prep_bind(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
        )
        await worker_service._prep_select(
            claim,
            session_factory=self.session_factory,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
        )
        async with self.session_factory() as db:
            job, applied = await job_service.cancel_job(
                db, project_id=self.seed.project_id, job_id=preparation.job_id
            )
            prep = await store.get_preparation(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
            )
            detail = await store.serialize_preparation(db, prep)
        self.assertTrue(applied)
        self.assertEqual(job.status, "canceled")
        self.assertEqual(prep.phase, "canceled")
        self.assertFalse(detail["canContinue"])
        self.assertEqual(detail["roles"], [])
        self.assertEqual(detail["platforms"], [])

    async def test_lease_lost_before_batch_commit_keeps_attempt_counts(self) -> None:
        preparation = await self._start()
        claim = await claim_preparation(
            self.session_factory, job_id=preparation.job_id
        )
        await worker_service._prep_bind(
            claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
        )
        await worker_service._prep_select(
            claim,
            session_factory=self.session_factory,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
        )
        profile_claim = dataclasses.replace(claim, next_step=2)

        async def lost_commit(*args, **kwargs):
            return "lost"

        with patch.object(
            worker_service, "_commit_prep_batch", new=lost_commit
        ):
            outcome = await worker_service._prep_profile(
                profile_claim,
                session_factory=self.session_factory,
                stop_event=None,
                lease_seconds=60,
                heartbeat_seconds=5,
                config=CONFIG,
                profiler_factory=lambda metrics=None: create_profiler(
                    CONFIG, metrics=metrics
                ),
            )
        self.assertEqual(outcome, "lost")
        async with self.session_factory() as db:
            refreshed = await store.get_preparation(
                db,
                project_id=self.seed.project_id,
                preparation_id=preparation.id,
            )
        self.assertGreater(refreshed.provider_calls_double, 0)
        self.assertEqual(refreshed.provider_calls_real, 0)

    async def test_takeover_keeps_identity_and_reuses_batches(self) -> None:
        preparation = await self._start()
        first_claim = await claim_preparation(
            self.session_factory, job_id=preparation.job_id, lease_seconds=0.2
        )
        await worker_service._prep_bind(
            first_claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=0.2,
            heartbeat_seconds=0.1,
            config=CONFIG,
        )
        await worker_service._prep_select(
            first_claim,
            session_factory=self.session_factory,
            lease_seconds=0.2,
            heartbeat_seconds=0.1,
            config=CONFIG,
        )
        # 模拟 worker 在第一批之后停止，租约过期后由新 worker 接手。
        stop = __import__("asyncio").Event()

        class StopAfterFirst:
            def __init__(self, metrics=None) -> None:
                self.inner = create_profiler(CONFIG, metrics=metrics)

            def profile(self, **kwargs):
                result = self.inner.profile(**kwargs)
                stop.set()
                return result

        profile_claim = __import__("dataclasses").replace(first_claim, next_step=2)
        await worker_service._prep_profile(
            profile_claim,
            session_factory=self.session_factory,
            stop_event=stop,
            lease_seconds=0.2,
            heartbeat_seconds=0.1,
            config=CONFIG,
            profiler_factory=StopAfterFirst,
        )
        async with self.session_factory() as db:
            before = await store.load_roles(
                db,
                preparation=await store.get_preparation(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=preparation.id,
                ),
            )
            await db.execute(
                text(
                    "UPDATE jobs SET lease_expires_at = clock_timestamp() - "
                    "interval '1 second' WHERE id = :job"
                ),
                {"job": preparation.job_id},
            )
            await db.commit()
        first_preview = {
            role.role_id for role in before if role.preview_ready_at is not None
        }
        self.assertTrue(first_preview)
        second_claim = await claim_preparation(
            self.session_factory, job_id=preparation.job_id
        )
        self.assertGreater(second_claim.attempt, 1)
        self.assertEqual(
            (second_claim.job_id, second_claim.run_id),
            (first_claim.job_id, first_claim.run_id),
        )
        outcome = await worker_service.execute_preparation(
            second_claim,
            session_factory=self.session_factory,
            stop_event=None,
            lease_seconds=60,
            heartbeat_seconds=5,
            config=CONFIG,
            profiler_factory=lambda metrics=None: create_profiler(
                CONFIG, metrics=metrics
            ),
        )
        self.assertEqual(outcome, "succeeded")
        async with self.session_factory() as db:
            after = await store.load_roles(
                db,
                preparation=await store.get_preparation(
                    db,
                    project_id=self.seed.project_id,
                    preparation_id=preparation.id,
                ),
            )
        self.assertTrue(first_preview.issubset({role.role_id for role in after}))
        self.assertEqual(len(after), 5)
        self.assertEqual(
            await scalar(
                self.session_factory,
                "SELECT count(*) FROM environment_preparations WHERE build_id = :b",
                {"b": self.seed.build_id},
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
