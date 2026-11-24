"""证据图谱持久化服务的真实 PostgreSQL 测试。"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.errors import (
    GraphBindingConflict,
    GraphBuildNotFound,
    GraphEpisodeConflict,
    GraphLimitExceeded,
    GraphSnapshotNotFound,
)
from app.services import evidence_graph as graph
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


def _nodes() -> list[graph.GraphNode]:
    return [
        graph.GraphNode(
            key="node-1", node_type="entity", label="校园接驳车", origin="material"
        ),
        graph.GraphNode(
            key="node-2", node_type="entity", label="雨天班次", origin="material"
        ),
    ]


def _edges() -> list[graph.GraphEdge]:
    return [
        graph.GraphEdge(
            key="edge-1",
            edge_type="relation",
            label="调整发车间隔",
            origin="material",
            source_key="node-1",
            target_key="node-2",
        )
    ]


def _evidence(
    seed: SeedMaterial,
    *,
    target_key: str = "node-1",
    evidence_index: int = 1,
    target_kind: str = "node",
) -> graph.GraphEvidence:
    return graph.GraphEvidence(
        target_kind=target_kind,
        target_key=target_key,
        evidence_index=evidence_index,
        document_id=seed.document_id,
        chunk_index=1,
        source_start=0,
        source_end=len(seed.text),
        quote="EchoSim 原创测试材料",
    )


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class EvidenceGraphServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed(self, marker: str = "service") -> SeedMaterial:
        async with self.session_factory() as db:
            seed = await seed_original_material(db, marker=marker)
            await db.commit()
        return seed

    async def _mark_job_terminal(
        self, *, job_id: uuid.UUID, status: str, phase: str
    ) -> None:
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE jobs SET status = :status, finished_at = now(), "
                    "lease_token = NULL, lease_expires_at = NULL WHERE id = :job_id"
                ),
                {"status": status, "job_id": job_id},
            )
            await db.execute(
                text(
                    "UPDATE graph_builds SET phase = :phase, updated_at = now() "
                    "WHERE run_id = (SELECT run_id FROM jobs WHERE id = :job_id)"
                ),
                {"phase": phase, "job_id": job_id},
            )
            await db.commit()

    async def _set_build_running(self, build: graph.GraphBuild) -> None:
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE graph_builds SET phase = 'drawing', "
                    "can_continue = false, finished_at = NULL, updated_at = now() "
                    "WHERE id = :build_id"
                ),
                {"build_id": build.id},
            )
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'running', attempts = 1, "
                    "started_at = now(), finished_at = NULL WHERE id = :job_id"
                ),
                {"job_id": build.job_id},
            )
            await db.commit()

    async def _apply_completion(
        self,
        db: AsyncSession,
        *,
        build: graph.GraphBuild,
        phase: str = "ready",
        job_status: str = "succeeded",
        can_continue: bool = True,
        event_seq: int = 1,
    ) -> None:
        """在调用方事务内写 build 状态、job 终态与完成事件，不提交。"""
        await db.execute(
            text(
                "UPDATE graph_builds SET phase = :phase, "
                "can_continue = :can_continue, finished_at = now(), "
                "updated_at = now() WHERE id = :build_id"
            ),
            {
                "build_id": build.id,
                "phase": phase,
                "can_continue": can_continue,
            },
        )
        await db.execute(
            text(
                "UPDATE jobs SET status = :status, finished_at = now(), "
                "lease_token = NULL, lease_expires_at = NULL "
                "WHERE id = :job_id"
            ),
            {"job_id": build.job_id, "status": job_status},
        )
        await db.execute(
            text(
                "INSERT INTO run_events (run_id, seq, type, stage, simulated_at, "
                "payload) VALUES (:run_id, :seq, 'completed', "
                "'evidence-graph', now(), '{}'::jsonb)"
            ),
            {"run_id": build.run_id, "seq": event_seq},
        )

    async def _published_revisions(self, build_id: uuid.UUID) -> list[int]:
        async with self.session_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT revision FROM graph_snapshots "
                        "WHERE build_id = :build_id AND published = true "
                        "ORDER BY revision"
                    ),
                    {"build_id": build_id},
                )
            ).fetchall()
        return [int(row.revision) for row in rows]

    async def _create_build(self, **kwargs: object) -> graph.GraphBuild:
        async with self.session_factory() as db:
            return await graph.create_build(db, **kwargs)

    async def _create_snapshot(self, **kwargs: object) -> graph.GraphSnapshot:
        async with self.session_factory() as db:
            snapshot = await graph.create_snapshot(db, **kwargs)
            await db.commit()
            return snapshot

    async def _publish_snapshot(self, **kwargs: object) -> graph.GraphSnapshot:
        async with self.session_factory() as db:
            snapshot = await graph.publish_snapshot(db, **kwargs)
            await db.commit()
            return snapshot

    async def test_create_build_persists_run_job_build_and_documents(self) -> None:
        seed = await self._seed()

        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="雨天校园接驳车如何调整班次？",
            document_ids=[seed.document_id],
        )
        async with self.session_factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT r.kind, r.stage, r.total_steps, j.operation_key, "
                        "j.status, b.document_sha256, b.chunk_count, b.ordinal "
                        "FROM graph_builds g "
                        "JOIN runs r ON r.id = g.run_id "
                        "JOIN jobs j ON j.run_id = g.run_id "
                        "JOIN graph_build_documents b ON b.build_id = g.id "
                        "WHERE g.id = :build_id"
                    ),
                    {"build_id": build.id},
                )
            ).one()

        self.assertEqual(build.phase, "preparing")
        self.assertEqual(build.job_status, "queued")
        self.assertEqual(row.kind, graph.GRAPH_BUILD_KIND)
        self.assertEqual(row.stage, graph.GRAPH_BUILD_STAGE)
        self.assertEqual(int(row.total_steps), graph.GRAPH_BUILD_TOTAL_STEPS)
        self.assertEqual(row.operation_key, graph.GRAPH_BUILD_OPERATION_KEY)
        self.assertEqual(row.status, "queued")
        self.assertEqual(row.document_sha256.strip(), seed.sha256)
        self.assertEqual(int(row.chunk_count), 1)
        self.assertEqual(int(row.ordinal), 1)

    async def test_default_read_returns_second_build_without_snapshot(self) -> None:
        seed = await self._seed()
        first = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="第一次建图问题",
            document_ids=[seed.document_id],
        )
        await self._mark_job_terminal(
            job_id=first.job_id, status="succeeded", phase="ready"
        )
        first_snapshot = await self._create_snapshot(
            build_id=first.id,
            revision=1,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            complete=True,
            published=True,
            episode_count=1,
        )
        second = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="第二次建图问题",
            document_ids=[seed.document_id],
        )

        async with self.session_factory() as db:
            default_build, default_snapshot = await graph.read_build_graph(
                db, project_id=seed.project_id
            )
            old_build, old_snapshot = await graph.read_build_graph(
                db, project_id=seed.project_id, build_id=first.id
            )
            with self.assertRaises(GraphSnapshotNotFound):
                await graph.get_published_snapshot(db, build_id=second.id)

        self.assertEqual(default_build.id, second.id)
        self.assertEqual(default_build.phase, "preparing")
        self.assertIsNone(default_snapshot)
        self.assertEqual(old_build.id, first.id)
        self.assertIsNotNone(old_snapshot)
        assert old_snapshot is not None
        self.assertEqual(old_snapshot.id, first_snapshot.id)
        self.assertEqual(old_snapshot.revision, 1)
        self.assertEqual(old_snapshot.node_count, 2)
        self.assertEqual(old_snapshot.edge_count, 1)

    async def test_active_build_wins_over_newer_terminal_build(self) -> None:
        seed = await self._seed()
        active = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="活动建图",
            document_ids=[seed.document_id],
        )
        await self._mark_job_terminal(
            job_id=active.job_id, status="succeeded", phase="ready"
        )
        newer = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="较新的终态建图",
            document_ids=[seed.document_id],
        )
        await self._mark_job_terminal(
            job_id=newer.job_id, status="failed", phase="failed"
        )
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'running', attempts = 1 "
                    "WHERE id = :job_id"
                ),
                {"job_id": active.job_id},
            )
            await db.execute(
                text(
                    "UPDATE graph_builds SET created_at = now() - interval '1 hour' "
                    "WHERE id = :build_id"
                ),
                {"build_id": active.id},
            )
            await db.commit()
            selected, _ = await graph.read_build_graph(
                db, project_id=seed.project_id
            )

        self.assertEqual(selected.id, active.id)
        self.assertEqual(selected.job_status, "running")

    async def test_candidate_snapshot_is_invisible_even_with_known_revision(self) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="候选快照测试",
            document_ids=[seed.document_id],
        )
        published = await self._create_snapshot(
            build_id=build.id,
            revision=1,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            published=True,
            episode_count=1,
        )
        candidate = await self._create_snapshot(
            build_id=build.id,
            revision=2,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            complete=True,
            published=False,
            episode_count=1,
        )

        async with self.session_factory() as db:
            latest = await graph.get_published_snapshot(db, build_id=build.id)
            with self.assertRaises(GraphSnapshotNotFound):
                await graph.get_published_snapshot(
                    db, build_id=build.id, revision=candidate.revision
                )
            explicit = await graph.get_published_snapshot(
                db, build_id=build.id, revision=published.revision
            )

        self.assertEqual(latest.revision, 1)
        self.assertEqual(explicit.revision, 1)
        self.assertFalse(candidate.published)
        self.assertTrue(candidate.complete)

    async def test_final_snapshot_retains_only_recent_partial_snapshots(self) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="部分快照保留测试",
            document_ids=[seed.document_id],
        )
        for revision in range(1, 6):
            await self._create_snapshot(
                build_id=build.id,
                revision=revision,
                nodes=_nodes(),
                edges=_edges(),
                evidence=[_evidence(seed)],
                published=True,
                episode_count=1,
            )
        final_candidate = await self._create_snapshot(
            build_id=build.id,
            revision=6,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            complete=True,
            published=False,
            episode_count=1,
        )

        final = await self._publish_snapshot(
            snapshot_id=final_candidate.id,
            complete=True,
            keep_partials=graph.PARTIAL_SNAPSHOT_RETENTION,
        )

        async with self.session_factory() as db:
            for removed in (1, 2):
                with self.assertRaises(GraphSnapshotNotFound):
                    await graph.get_published_snapshot(
                        db, build_id=build.id, revision=removed
                    )
            kept = [
                await graph.get_published_snapshot(
                    db, build_id=build.id, revision=revision
                )
                for revision in (3, 4, 5, 6)
            ]
            counts = (
                await db.execute(
                    text(
                        "SELECT count(*) AS snapshots, "
                        "(SELECT count(*) FROM graph_nodes WHERE snapshot_id = "
                        "      ANY(:kept_ids)) AS nodes, "
                        "(SELECT count(*) FROM graph_edges WHERE snapshot_id = "
                        "      ANY(:kept_ids)) AS edges, "
                        "(SELECT count(*) FROM graph_evidence WHERE snapshot_id = "
                        "      ANY(:kept_ids)) AS evidence "
                        "FROM graph_snapshots WHERE build_id = :build_id"
                    ),
                    {
                        "kept_ids": [item.id for item in kept],
                        "build_id": build.id,
                    },
                )
            ).one()

        self.assertTrue(final.complete)
        self.assertTrue(final.published)
        self.assertEqual([item.revision for item in kept], [3, 4, 5, 6])
        self.assertEqual(int(counts.snapshots), 4)
        self.assertEqual(int(counts.nodes), 8)
        self.assertEqual(int(counts.edges), 4)
        self.assertEqual(int(counts.evidence), 4)

    async def test_limits_reject_nodes_edges_and_sources_without_snapshot(self) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="规模上限测试",
            document_ids=[seed.document_id],
        )
        too_many_nodes = [
            graph.GraphNode(
                key=f"node-{index}",
                node_type="entity",
                label="原创节点",
                origin="material",
            )
            for index in range(graph.MAX_GRAPH_NODES + 1)
        ]
        too_many_edges = [
            graph.GraphEdge(
                key=f"edge-{index}",
                edge_type="relation",
                label="原创关系",
                origin="material",
                source_key="node-1",
                target_key="node-2",
            )
            for index in range(graph.MAX_GRAPH_EDGES + 1)
        ]
        too_many_sources = [
            _evidence(seed, evidence_index=index)
            for index in range(1, graph.MAX_EVIDENCE_PER_TARGET + 2)
        ]

        for nodes, edges, evidence, code in (
            (too_many_nodes, [], [], "graph_too_large"),
            (_nodes(), too_many_edges, [], "graph_too_large"),
            (_nodes(), [], too_many_sources, "too_many_sources"),
        ):
            with self.assertRaises(GraphLimitExceeded) as caught:
                await self._create_snapshot(
                    build_id=build.id,
                    revision=1,
                    nodes=nodes,
                    edges=edges,
                    evidence=evidence,
                    published=True,
                )
            self.assertEqual(caught.exception.code, code)

        async with self.session_factory() as db:
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM graph_snapshots "
                        "WHERE build_id = :build_id"
                    ),
                    {"build_id": build.id},
                )
            ).scalar_one()
        self.assertEqual(int(count), 0)

    async def test_create_snapshot_complete_published_waits_for_caller_commit(
        self,
    ) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="候选最终快照事务测试",
            document_ids=[seed.document_id],
        )
        await self._set_build_running(build)
        old = await self._create_snapshot(
            build_id=build.id,
            revision=1,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            published=True,
            episode_count=1,
        )
        self.assertFalse(old.complete)

        async with self.session_factory() as writer:
            await graph.create_snapshot(
                writer,
                build_id=build.id,
                revision=2,
                nodes=_nodes(),
                edges=_edges(),
                evidence=[_evidence(seed)],
                complete=True,
                published=True,
                episode_count=1,
            )
            await self._apply_completion(writer, build=build)
            async with self.session_factory() as reader:
                # 未提交前使用非锁定读取，确认读者仍只看到上次已发布版本。
                before_build = await graph.get_build(
                    reader, project_id=seed.project_id, build_id=build.id
                )
                before_snapshot = await graph.get_published_snapshot(
                    reader, build_id=build.id
                )
            self.assertEqual(before_build.phase, "drawing")
            self.assertEqual(before_build.job_status, "running")
            self.assertFalse(before_build.can_continue)
            assert before_snapshot is not None
            self.assertEqual(before_snapshot.revision, 1)
            await writer.rollback()

        async with self.session_factory() as reader:
            rolled_build, rolled_snapshot = await graph.read_build_graph(
                reader, project_id=seed.project_id, build_id=build.id
            )
            with self.assertRaises(GraphSnapshotNotFound):
                await graph.get_published_snapshot(
                    reader, build_id=build.id, revision=2
                )
        self.assertEqual(rolled_build.phase, "drawing")
        self.assertEqual(rolled_build.job_status, "running")
        assert rolled_snapshot is not None
        self.assertEqual(rolled_snapshot.revision, 1)

        async with self.session_factory() as writer:
            await graph.create_snapshot(
                writer,
                build_id=build.id,
                revision=2,
                nodes=_nodes(),
                edges=_edges(),
                evidence=[_evidence(seed)],
                complete=True,
                published=True,
                episode_count=1,
            )
            await self._apply_completion(writer, build=build)
            await writer.commit()
        async with self.session_factory() as reader:
            after_build, after_snapshot = await graph.read_build_graph(
                reader, project_id=seed.project_id, build_id=build.id
            )
            committed_snapshot = await graph.get_published_snapshot(
                reader, build_id=build.id, revision=2
            )
        self.assertEqual(after_build.phase, "ready")
        self.assertEqual(after_build.job_status, "succeeded")
        self.assertTrue(after_build.can_continue)
        assert after_snapshot is not None
        self.assertEqual(after_snapshot.revision, 2)
        self.assertTrue(after_snapshot.complete)
        self.assertEqual(after_snapshot.node_count, len(after_snapshot.nodes))
        self.assertEqual(after_snapshot.edge_count, len(after_snapshot.edges))
        self.assertEqual(committed_snapshot.revision, 2)

    async def test_publish_snapshot_waits_for_caller_commit_and_rollback(self) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="发布事务测试",
            document_ids=[seed.document_id],
        )
        await self._set_build_running(build)
        await self._create_snapshot(
            build_id=build.id,
            revision=1,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            published=True,
            episode_count=1,
        )
        candidate = await self._create_snapshot(
            build_id=build.id,
            revision=2,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            complete=True,
            published=False,
            episode_count=1,
        )

        async with self.session_factory() as writer:
            await graph.publish_snapshot(
                writer, snapshot_id=candidate.id, complete=True
            )
            await self._apply_completion(writer, build=build)
            async with self.session_factory() as reader:
                # 未提交前使用非锁定读取，确认读者仍只看到上次已发布版本。
                before_build = await graph.get_build(
                    reader, project_id=seed.project_id, build_id=build.id
                )
                before_snapshot = await graph.get_published_snapshot(
                    reader, build_id=build.id
                )
            self.assertEqual(before_build.phase, "drawing")
            self.assertEqual(before_build.job_status, "running")
            assert before_snapshot is not None
            self.assertEqual(before_snapshot.revision, 1)
            await writer.rollback()

        async with self.session_factory() as reader:
            rolled_build, rolled_snapshot = await graph.read_build_graph(
                reader, project_id=seed.project_id, build_id=build.id
            )
            with self.assertRaises(GraphSnapshotNotFound):
                await graph.get_published_snapshot(
                    reader, build_id=build.id, revision=2
                )
        self.assertEqual(rolled_build.phase, "drawing")
        assert rolled_snapshot is not None
        self.assertEqual(rolled_snapshot.revision, 1)
        self.assertEqual(await self._published_revisions(build.id), [1])

        async with self.session_factory() as writer:
            await graph.publish_snapshot(
                writer, snapshot_id=candidate.id, complete=True
            )
            await self._apply_completion(writer, build=build)
            await writer.commit()
        async with self.session_factory() as reader:
            after_build, after_snapshot = await graph.read_build_graph(
                reader, project_id=seed.project_id, build_id=build.id
            )
        self.assertEqual(after_build.phase, "ready")
        self.assertEqual(after_build.job_status, "succeeded")
        self.assertTrue(after_build.can_continue)
        assert after_snapshot is not None
        self.assertEqual(after_snapshot.revision, 2)
        self.assertTrue(after_snapshot.complete)

    async def test_read_build_graph_returns_old_version_while_publish_waits(
        self,
    ) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="读取发布竞争测试",
            document_ids=[seed.document_id],
        )
        await self._set_build_running(build)
        await self._create_snapshot(
            build_id=build.id,
            revision=1,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            published=True,
            episode_count=1,
        )
        candidate = await self._create_snapshot(
            build_id=build.id,
            revision=2,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            complete=True,
            published=False,
            episode_count=1,
        )

        reader_has_row = asyncio.Event()
        allow_reader = asyncio.Event()
        original_snapshot_from_row = graph._snapshot_from_build_graph_row

        async def delayed_snapshot(row: object) -> graph.GraphSnapshot | None:
            # 单语句读取已经完成；暂停在构造返回值之前，固定“旧版本已读全”的窗口。
            reader_has_row.set()
            await allow_reader.wait()
            return await original_snapshot_from_row(row)

        async def reader_task() -> tuple[graph.GraphBuild, graph.GraphSnapshot | None]:
            async with self.session_factory() as db:
                with patch.object(
                    graph,
                    "_snapshot_from_build_graph_row",
                    new=delayed_snapshot,
                ):
                    return await graph.read_build_graph(
                        db, project_id=seed.project_id, build_id=build.id
                    )

        async def publish_and_complete() -> None:
            async with self.session_factory() as db:
                await graph.publish_snapshot(
                    db, snapshot_id=candidate.id, complete=True
                )
                await self._apply_completion(db, build=build)
                await db.commit()

        reading = asyncio.create_task(reader_task())
        await asyncio.wait_for(reader_has_row.wait(), timeout=5)
        await publish_and_complete()
        allow_reader.set()
        old_build, old_snapshot = await asyncio.wait_for(reading, timeout=5)

        self.assertEqual(old_build.phase, "drawing")
        self.assertEqual(old_build.job_status, "running")
        self.assertFalse(old_build.can_continue)
        assert old_snapshot is not None
        self.assertEqual(old_snapshot.revision, 1)
        self.assertFalse(old_snapshot.complete)
        self.assertEqual(old_snapshot.node_count, len(old_snapshot.nodes))
        self.assertEqual(old_snapshot.edge_count, len(old_snapshot.edges))

        async with self.session_factory() as db:
            new_build, new_snapshot = await graph.read_build_graph(
                db, project_id=seed.project_id, build_id=build.id
            )
        self.assertEqual(new_build.phase, "ready")
        self.assertEqual(new_build.job_status, "succeeded")
        self.assertTrue(new_build.can_continue)
        assert new_snapshot is not None
        self.assertEqual(new_snapshot.revision, 2)
        self.assertTrue(new_snapshot.complete)

    async def test_read_build_graph_does_not_deadlock_with_worker_job_lock(
        self,
    ) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="读取与租约写入死锁回归",
            document_ids=[seed.document_id],
        )
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE graph_builds SET phase = 'drawing', "
                    "can_continue = false WHERE id = :build_id"
                ),
                {"build_id": build.id},
            )
            await db.commit()
        await self._create_snapshot(
            build_id=build.id,
            revision=1,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            published=True,
            episode_count=1,
        )

        reader_started = asyncio.Event()

        async def reader_task() -> tuple[graph.GraphBuild, graph.GraphSnapshot | None]:
            async with self.session_factory() as db:
                # 固定旧读取已取得 build 共享锁的窗口，使旧查询顺序可稳定复现。
                await db.execute(
                    text(
                        "SELECT id FROM graph_builds WHERE id = :build_id "
                        "FOR SHARE"
                    ),
                    {"build_id": build.id},
                )
                reader_started.set()
                return await graph.read_build_graph(
                    db, project_id=seed.project_id, build_id=build.id
                )

        async with self.session_factory() as writer_db:
            claim = await worker_service.claim_next(
                writer_db,
                worker_id="deadlock-regression",
                lease_seconds=30,
                job_id=build.job_id,
            )
            self.assertIsNotNone(claim)
            assert claim is not None
            locked_job = await worker_service._locked_job(writer_db, claim)
            self.assertIsNotNone(locked_job)

            reading = asyncio.create_task(reader_task())
            await asyncio.wait_for(reader_started.wait(), timeout=5)
            await asyncio.sleep(0.02)
            await graph.record_episode(
                writer_db,
                build_id=build.id,
                episode_key="episode-1",
                content="原创 episode 内容",
                content_sha256="a" * 64,
                state="written",
                episode_uuid=uuid.uuid4(),
                attempts_increment=1,
            )
            await writer_db.commit()
            old_build, old_snapshot = await asyncio.wait_for(reading, timeout=5)

        self.assertEqual(old_build.phase, "drawing")
        self.assertEqual(old_build.job_status, "running")
        self.assertFalse(old_build.can_continue)
        assert old_snapshot is not None
        self.assertEqual(old_snapshot.revision, 1)
        self.assertEqual(old_snapshot.node_count, len(old_snapshot.nodes))
        self.assertEqual(old_snapshot.edge_count, len(old_snapshot.edges))
        async with self.session_factory() as db:
            committed = await graph.get_build(
                db, project_id=seed.project_id, build_id=build.id
            )
        self.assertEqual(committed.planned_episodes, 1)
        self.assertEqual(committed.accepted_episodes, 1)
        self.assertEqual(committed.confirmed_episodes, 0)

    async def test_snapshot_read_keeps_header_and_details_during_cleanup(
        self,
    ) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="快照清理竞争测试",
            document_ids=[seed.document_id],
        )
        await self._create_snapshot(
            build_id=build.id,
            revision=1,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            published=True,
            episode_count=1,
        )
        await self._create_snapshot(
            build_id=build.id,
            revision=2,
            nodes=_nodes(),
            edges=_edges(),
            evidence=[_evidence(seed)],
            complete=True,
            published=True,
            episode_count=1,
        )

        reader_has_header = asyncio.Event()
        allow_reader = asyncio.Event()
        cleaner_started = asyncio.Event()
        original_details = graph._read_snapshot_details

        async def delayed_details(
            db: AsyncSession, row: object
        ) -> graph.GraphSnapshot:
            reader_has_header.set()
            await allow_reader.wait()
            return await original_details(db, row)

        async def reader_task() -> graph.GraphSnapshot:
            async with self.session_factory() as db:
                with patch.object(
                    graph, "_read_snapshot_details", new=delayed_details
                ):
                    return await graph.get_published_snapshot(
                        db, build_id=build.id, revision=1
                    )

        async def cleaner_task() -> list[int]:
            async with self.session_factory() as db:
                cleaner_started.set()
                deleted = await graph.prune_partial_snapshots(
                    db, build_id=build.id, keep=0
                )
                await db.commit()
                return deleted

        reading = asyncio.create_task(reader_task())
        await asyncio.wait_for(reader_has_header.wait(), timeout=5)
        cleaning = asyncio.create_task(cleaner_task())
        await asyncio.wait_for(cleaner_started.wait(), timeout=5)
        await asyncio.sleep(0.02)
        self.assertFalse(cleaning.done())
        allow_reader.set()
        old_snapshot = await asyncio.wait_for(reading, timeout=5)
        deleted = await asyncio.wait_for(cleaning, timeout=5)

        self.assertEqual(old_snapshot.revision, 1)
        self.assertEqual(old_snapshot.node_count, len(old_snapshot.nodes))
        self.assertEqual(old_snapshot.edge_count, len(old_snapshot.edges))
        self.assertEqual(len(old_snapshot.evidence), 1)
        self.assertEqual(deleted, [1])
        async with self.session_factory() as db:
            with self.assertRaises(GraphSnapshotNotFound):
                await graph.get_published_snapshot(
                    db, build_id=build.id, revision=1
                )
            latest = await graph.get_published_snapshot(db, build_id=build.id)
        self.assertEqual(latest.revision, 2)
        self.assertTrue(latest.complete)

    async def test_episode_retry_preserves_content_and_does_not_regress_state(
        self,
    ) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="episode 重试语义测试",
            document_ids=[seed.document_id],
        )
        episode_uuid = uuid.uuid4()
        async with self.session_factory() as db:
            await graph.record_episode(
                db,
                build_id=build.id,
                episode_key="episode-1",
                content="原创 episode 内容",
                content_sha256="a" * 64,
            )
            await db.commit()
        async with self.session_factory() as db:
            original_key = (
                await db.execute(
                    text(
                        "SELECT idempotency_key FROM graph_episodes "
                        "WHERE build_id = :build_id AND episode_key = 'episode-1'"
                    ),
                    {"build_id": build.id},
                )
            ).scalar_one()
        for state, sha in (("written", "b"), ("matched", "c")):
            async with self.session_factory() as db:
                await graph.record_episode(
                    db,
                    build_id=build.id,
                    episode_key="episode-1",
                    content="不应覆盖的内容",
                    content_sha256=sha * 64,
                    state=state,
                    episode_uuid=episode_uuid,
                    attempts_increment=1,
                )
                await db.commit()

        async with self.session_factory() as db:
            # 迟到的 written/planned 不能把 matched 倒退。
            await graph.record_episode(
                db,
                build_id=build.id,
                episode_key="episode-1",
                content="仍不应覆盖",
                content_sha256="d" * 64,
                state="written",
                episode_uuid=episode_uuid,
                attempts_increment=1,
            )
            await graph.record_episode(
                db,
                build_id=build.id,
                episode_key="episode-1",
                content="仍不应覆盖",
                content_sha256="e" * 64,
                state="planned",
                episode_uuid=episode_uuid,
                attempts_increment=1,
            )
            await db.commit()

        async with self.session_factory() as db:
            updated = await graph.get_build(
                db, project_id=seed.project_id, build_id=build.id
            )
            row = (
                await db.execute(
                    text(
                        "SELECT content, content_sha256, idempotency_key, "
                        "episode_uuid, state, attempts FROM graph_episodes "
                        "WHERE build_id = :build_id AND episode_key = 'episode-1'"
                    ),
                    {"build_id": build.id},
                )
            ).one()

        self.assertEqual(updated.planned_episodes, 1)
        self.assertEqual(updated.accepted_episodes, 1)
        self.assertEqual(updated.confirmed_episodes, 1)
        self.assertEqual(row.content, "原创 episode 内容")
        self.assertEqual(row.content_sha256, "a" * 64)
        self.assertEqual(row.idempotency_key, original_key)
        self.assertEqual(row.state, "matched")
        self.assertEqual(row.episode_uuid, episode_uuid)
        self.assertEqual(int(row.attempts), 4)

    async def test_episode_uuid_conflict_is_rejected_without_overwrite(self) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="episode UUID 冲突测试",
            document_ids=[seed.document_id],
        )
        original_uuid = uuid.uuid4()
        async with self.session_factory() as db:
            await graph.record_episode(
                db,
                build_id=build.id,
                episode_key="episode-1",
                content="原创 episode 内容",
                content_sha256="a" * 64,
                state="written",
                episode_uuid=original_uuid,
                attempts_increment=1,
            )
            await db.commit()
        async with self.session_factory() as db:
            before = (
                await db.execute(
                    text(
                        "SELECT content, content_sha256, idempotency_key, "
                        "episode_uuid, state, attempts FROM graph_episodes "
                        "WHERE build_id = :build_id AND episode_key = 'episode-1'"
                    ),
                    {"build_id": build.id},
                )
            ).one()

        async with self.session_factory() as db:
            with self.assertRaises(GraphEpisodeConflict):
                await graph.record_episode(
                    db,
                    build_id=build.id,
                    episode_key="episode-1",
                    content="不应覆盖的内容",
                    content_sha256="b" * 64,
                    state="matched",
                    episode_uuid=uuid.uuid4(),
                    attempts_increment=1,
                )
            await db.rollback()

        async with self.session_factory() as db:
            after = (
                await db.execute(
                    text(
                        "SELECT content, content_sha256, idempotency_key, "
                        "episode_uuid, state, attempts FROM graph_episodes "
                        "WHERE build_id = :build_id AND episode_key = 'episode-1'"
                    ),
                    {"build_id": build.id},
                )
            ).one()
            updated = await graph.get_build(
                db, project_id=seed.project_id, build_id=build.id
            )

        self.assertEqual(after.content, before.content)
        self.assertEqual(after.content_sha256, before.content_sha256)
        self.assertEqual(after.idempotency_key, before.idempotency_key)
        self.assertEqual(after.episode_uuid, original_uuid)
        self.assertEqual(after.state, "written")
        self.assertEqual(int(after.attempts), int(before.attempts))
        self.assertEqual(updated.planned_episodes, 1)
        self.assertEqual(updated.accepted_episodes, 1)
        self.assertEqual(updated.confirmed_episodes, 0)

    async def test_concurrent_episodes_keep_summary_in_sync(self) -> None:
        seed = await self._seed()
        build = await self._create_build(
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question="episode 并发汇总测试",
            document_ids=[seed.document_id],
        )
        uuid_a = uuid.uuid4()
        uuid_b = uuid.uuid4()
        b_started = asyncio.Event()

        async def record_b() -> None:
            async with self.session_factory() as db:
                b_started.set()
                await graph.record_episode(
                    db,
                    build_id=build.id,
                    episode_key="episode-b",
                    content="原创 episode B",
                    content_sha256="b" * 64,
                    state="written",
                    episode_uuid=uuid_b,
                    attempts_increment=1,
                )
                await db.commit()

        async with self.session_factory() as db_a:
            await graph.record_episode(
                db_a,
                build_id=build.id,
                episode_key="episode-a",
                content="原创 episode A",
                content_sha256="a" * 64,
                state="written",
                episode_uuid=uuid_a,
                attempts_increment=1,
            )
            task = asyncio.create_task(record_b())
            await b_started.wait()
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            await db_a.commit()
        await task

        for key, value in (("episode-a", uuid_a), ("episode-b", uuid_b)):
            async with self.session_factory() as db:
                await graph.record_episode(
                    db,
                    build_id=build.id,
                    episode_key=key,
                    content=f"原创 {key}",
                    content_sha256=("a" if key == "episode-a" else "b") * 64,
                    state="matched",
                    episode_uuid=value,
                )
                await db.commit()

        async with self.session_factory() as db:
            await graph.record_episode(
                db,
                build_id=build.id,
                episode_key="episode-a",
                content="重试不应新增计数",
                content_sha256="f" * 64,
                state="matched",
                episode_uuid=uuid_a,
            )
            await db.commit()

        async with self.session_factory() as db:
            updated = await graph.get_build(
                db, project_id=seed.project_id, build_id=build.id
            )
            rows = (
                await db.execute(
                    text(
                        "SELECT episode_key, idempotency_key, episode_uuid, state "
                        "FROM graph_episodes WHERE build_id = :build_id "
                        "ORDER BY episode_key"
                    ),
                    {"build_id": build.id},
                )
            ).fetchall()

        self.assertEqual(updated.planned_episodes, 2)
        self.assertEqual(updated.accepted_episodes, 2)
        self.assertEqual(updated.confirmed_episodes, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row.idempotency_key for row in rows}), 2)
        self.assertEqual(
            {row.episode_key: row.episode_uuid for row in rows},
            {"episode-a": uuid_a, "episode-b": uuid_b},
        )
        self.assertTrue(all(row.state == "matched" for row in rows))


    async def test_unknown_build_is_not_found(self) -> None:
        seed = await self._seed()
        async with self.session_factory() as db:
            with self.assertRaises(GraphBuildNotFound):
                await graph.get_build(
                    db,
                    project_id=seed.project_id,
                    build_id=uuid.uuid4(),
                )


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class GraphBindingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)
        async with self.session_factory() as db:
            self.seed = await seed_original_material(db, marker="binding")
            await db.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _count_bindings(self, project_id: uuid.UUID) -> int:
        async with self.session_factory() as db:
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM project_graph_bindings "
                        "WHERE project_id = :project_id"
                    ),
                    {"project_id": project_id},
                )
            ).scalar_one()
        return int(count)

    async def test_first_binding_is_committed_with_stable_params(self) -> None:
        async with self.session_factory() as db:
            first = await graph.get_or_create_binding(
                db, project_id=self.seed.project_id
            )
        async with self.session_factory() as db:
            second = await graph.require_binding(
                db, project_id=self.seed.project_id
            )

        self.assertEqual(first.create_idempotency_key, second.create_idempotency_key)
        self.assertEqual(first.create_params(), second.create_params())
        self.assertEqual(first.state, "pending")
        self.assertIsNone(first.graph_id)
        self.assertEqual(await self._count_bindings(self.seed.project_id), 1)

    async def test_concurrent_first_binding_and_restart_keep_same_identity(self) -> None:
        async def fetch() -> graph.GraphBinding:
            async with self.session_factory() as db:
                return await graph.get_or_create_binding(
                    db, project_id=self.seed.project_id
                )

        left, right = await asyncio.gather(fetch(), fetch())
        self.assertEqual(left.create_idempotency_key, right.create_idempotency_key)
        self.assertEqual(left.create_params(), right.create_params())
        self.assertEqual(await self._count_bindings(self.seed.project_id), 1)

        await self.engine.dispose()
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False
        )
        async with self.session_factory() as db:
            restarted = await graph.require_binding(
                db, project_id=self.seed.project_id
            )

        self.assertEqual(left.create_idempotency_key, restarted.create_idempotency_key)
        self.assertEqual(left.create_params(), restarted.create_params())
        self.assertEqual(await self._count_bindings(self.seed.project_id), 1)

    async def test_save_uuid_is_idempotent_and_never_overwrites(self) -> None:
        async with self.session_factory() as db:
            await graph.get_or_create_binding(db, project_id=self.seed.project_id)
        graph_id = uuid.uuid4()
        async with self.session_factory() as db:
            first = await graph.save_binding_graph_id(
                db, project_id=self.seed.project_id, graph_id=graph_id
            )
        async with self.session_factory() as db:
            repeated = await graph.save_binding_graph_id(
                db, project_id=self.seed.project_id, graph_id=str(graph_id)
            )

        self.assertEqual(first.graph_id, graph_id)
        self.assertEqual(repeated.graph_id, graph_id)
        self.assertTrue(repeated.bound)

        async with self.session_factory() as db:
            with self.assertRaises(GraphBindingConflict) as empty:
                await graph.save_binding_graph_id(
                    db, project_id=self.seed.project_id, graph_id=None
                )
            self.assertEqual(empty.exception.code, "invalid_graph_response")
        async with self.session_factory() as db:
            with self.assertRaises(GraphBindingConflict) as zero:
                await graph.save_binding_graph_id(
                    db,
                    project_id=self.seed.project_id,
                    graph_id=uuid.UUID(int=0),
                )
            self.assertEqual(zero.exception.code, "invalid_graph_response")
        async with self.session_factory() as db:
            with self.assertRaises(GraphBindingConflict) as conflict:
                await graph.save_binding_graph_id(
                    db,
                    project_id=self.seed.project_id,
                    graph_id=uuid.uuid4(),
                )
            self.assertEqual(conflict.exception.code, "invalid_graph_scope")
        async with self.session_factory() as db:
            stored = await graph.require_binding(
                db, project_id=self.seed.project_id
            )

        self.assertEqual(stored.graph_id, graph_id)
        self.assertEqual(await self._count_bindings(self.seed.project_id), 1)

    async def test_other_project_cannot_reuse_bound_graph(self) -> None:
        async with self.session_factory() as db:
            other = await seed_original_material(db, marker="binding-other")
            await db.commit()
        async with self.session_factory() as db:
            await graph.get_or_create_binding(db, project_id=self.seed.project_id)
            await graph.get_or_create_binding(db, project_id=other.project_id)
        graph_id = uuid.uuid4()
        async with self.session_factory() as db:
            await graph.save_binding_graph_id(
                db, project_id=self.seed.project_id, graph_id=graph_id
            )
        async with self.session_factory() as db:
            with self.assertRaises(GraphBindingConflict) as conflict:
                await graph.save_binding_graph_id(
                    db, project_id=other.project_id, graph_id=graph_id
                )
            self.assertEqual(conflict.exception.code, "invalid_graph_scope")
        async with self.session_factory() as db:
            owner = await graph.require_binding(
                db, project_id=self.seed.project_id
            )
            other_binding = await graph.require_binding(
                db, project_id=other.project_id
            )

        self.assertEqual(owner.graph_id, graph_id)
        self.assertIsNone(other_binding.graph_id)
        self.assertEqual(other_binding.state, "pending")
        self.assertEqual(await self._count_bindings(self.seed.project_id), 1)
        self.assertEqual(await self._count_bindings(other.project_id), 1)


if __name__ == "__main__":
    unittest.main()
