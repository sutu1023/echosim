"""迁移器解析、顺序与事务语义的单元测试。"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.migrate import (
    MigrationRunner,
    discover_migrations,
    pending_migrations,
    split_sql_statements,
)
from app.services import documents
from tests.db_support import (
    HAS_TEST_DATABASE,
    SKIP_REASON,
    TEST_DATABASE_URL,
    TEST_SCHEMA,
    apply_all_migrations,
    apply_baseline_migrations,
    make_test_engine,
    reset_public_schema,
    seed_original_material,
)


class FakeResult:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str]]:
        return self._rows


class FakeTransaction:
    async def __aenter__(self) -> "FakeTransaction":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeConnection:
    """只实现迁移器用到的最小接口，用于断言顺序与失败路径。"""

    def __init__(
        self, applied: list[str] | None = None, fail_contains: str | None = None
    ) -> None:
        self.applied = set(applied or [])
        self.fail_contains = fail_contains
        self.executed: list[tuple[str, str]] = []

    async def execute(self, clause: object, params: dict[str, str] | None = None):
        sql = str(clause)
        if "SELECT version FROM schema_migrations" in sql:
            return FakeResult([(version,) for version in self.applied])
        if "INSERT INTO schema_migrations" in sql:
            version = (params or {})["version"]
            self.applied.add(version)
            self.executed.append(("migration", version))
            return FakeResult([])
        if self.fail_contains and self.fail_contains in sql:
            raise RuntimeError("模拟 SQL 失败")
        self.executed.append(("sql", sql))
        return FakeResult([])

    async def commit(self) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction()


class SqlSplitTests(unittest.TestCase):
    def test_splits_statements_and_ignores_comments(self) -> None:
        sql = """
        -- 注释里的分号; 不应拆分
        CREATE TABLE a (id int);
        INSERT INTO a VALUES (1);
        """

        statements = split_sql_statements(sql)

        self.assertEqual(len(statements), 2)
        self.assertIn("CREATE TABLE a", statements[0])
        self.assertNotIn("注释", statements[0])

    def test_keeps_semicolons_inside_quotes_and_dollar_blocks(self) -> None:
        sql = "INSERT INTO a VALUES ('x;y'); SELECT $$a;b$$; SELECT 'it''s;ok';"

        statements = split_sql_statements(sql)

        self.assertEqual(len(statements), 3)
        self.assertIn("'x;y'", statements[0])
        self.assertIn("$$a;b$$", statements[1])
        self.assertIn("'it''s;ok'", statements[2])


class MigrationOrderTests(unittest.TestCase):
    def test_discovers_sql_files_in_name_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "0002_b.sql").write_text("SELECT 2;", encoding="utf-8")
            (directory / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
            (directory / "README.md").write_text("ignore", encoding="utf-8")

            migrations = discover_migrations(directory)

        self.assertEqual([m.version for m in migrations], ["0001_a", "0002_b"])

    def test_pending_skips_applied_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
            (directory / "0002_b.sql").write_text("SELECT 2;", encoding="utf-8")
            migrations = discover_migrations(directory)

        pending = pending_migrations(migrations, {"0001_a"})

        self.assertEqual([m.version for m in pending], ["0002_b"])


class RepositoryMigrationTests(unittest.TestCase):
    def test_0002_is_discovered_after_0001(self) -> None:
        migrations = discover_migrations(Path("app/migrations"))

        versions = [m.version for m in migrations]

        self.assertEqual(versions[0], "0001_identity_and_projects")
        self.assertIn("0002_jobs_runs_events", versions)
        self.assertLess(versions.index("0001_identity_and_projects"), versions.index("0002_jobs_runs_events"))

    def test_0003_is_discovered_after_0002(self) -> None:
        versions = [m.version for m in discover_migrations(Path("app/migrations"))]

        self.assertIn("0003_documents", versions)
        self.assertLess(
            versions.index("0002_jobs_runs_events"),
            versions.index("0003_documents"),
        )

    def test_0004_adds_failure_detail_column(self) -> None:
        versions = [m.version for m in discover_migrations(Path("app/migrations"))]
        self.assertIn("0004_document_failure_detail", versions)
        migration = next(
            m
            for m in discover_migrations(Path("app/migrations"))
            if m.version == "0004_document_failure_detail"
        )
        statements = split_sql_statements(migration.path.read_text(encoding="utf-8"))
        joined = "\n".join(statements)

        self.assertIn("ALTER TABLE documents", joined)
        self.assertIn("failure_detail jsonb", joined)
        self.assertIn("DEFAULT '{}'::jsonb", joined)

    def test_0003_statements_cover_documents_and_kind_extension(self) -> None:
        migration = next(
            m
            for m in discover_migrations(Path("app/migrations"))
            if m.version == "0003_documents"
        )
        statements = split_sql_statements(migration.path.read_text(encoding="utf-8"))
        joined = "\n".join(statements)

        self.assertIn("CREATE TABLE documents", joined)
        self.assertIn("CREATE TABLE document_chunks", joined)
        self.assertIn("documents_project_content_idx", joined)
        self.assertIn("WHERE status IN ('queued', 'parsing', 'ready')", joined)
        self.assertIn("ALTER TABLE runs", joined)
        self.assertIn("DROP CONSTRAINT runs_kind_check", joined)
        self.assertIn("'document.ingest'", joined)

    def test_0003_alters_stay_in_one_migration_transaction(self) -> None:
        migration = next(
            m
            for m in discover_migrations(Path("app/migrations"))
            if m.version == "0003_documents"
        )
        statements = split_sql_statements(migration.path.read_text(encoding="utf-8"))
        alters = [statement for statement in statements if statement.startswith("ALTER TABLE runs")]

        self.assertEqual(len(alters), 3)

    def test_0005_adds_business_graph_tables_and_kind(self) -> None:
        migrations = discover_migrations(Path("app/migrations"))
        versions = [m.version for m in migrations]
        self.assertIn("0005_evidence_graph_builds", versions)
        self.assertLess(
            versions.index("0004_document_failure_detail"),
            versions.index("0005_evidence_graph_builds"),
        )
        migration = next(
            m for m in migrations if m.version == "0005_evidence_graph_builds"
        )
        joined = "\n".join(
            split_sql_statements(migration.path.read_text(encoding="utf-8"))
        )

        for table in (
            "project_graph_bindings",
            "graph_builds",
            "graph_build_documents",
            "graph_episodes",
            "graph_snapshots",
            "graph_nodes",
            "graph_edges",
            "graph_evidence",
        ):
            self.assertIn(f"CREATE TABLE {table}", joined)
        self.assertIn("'evidence.graph.build'", joined)
        self.assertIn("project_graph_bindings_key_unique", joined)
        self.assertIn("graph_episodes_idempotency_unique", joined)
        self.assertIn("graph_snapshots_revision_unique", joined)
        self.assertIn("graph_evidence_chunk_fk", joined)
        self.assertIn("REFERENCES document_chunks (document_id, chunk_index)", joined)

    def test_0002_statements_cover_jobs_tables_and_indexes(self) -> None:
        migration = next(
            m
            for m in discover_migrations(Path("app/migrations"))
            if m.version == "0002_jobs_runs_events"
        )
        statements = split_sql_statements(migration.path.read_text(encoding="utf-8"))
        joined = "\n".join(statements)

        for table in ("runs", "jobs", "run_steps", "run_events"):
            self.assertIn(f"CREATE TABLE {table}", joined)
        self.assertIn("jobs_active_operation_idx", joined)
        self.assertIn("jobs_claimable_idx", joined)
        self.assertIn("WHERE status IN ('queued', 'running')", joined)
        self.assertIn("fail_at_step", joined)
        self.assertIn("lease_token", joined)


class MigrationRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_applies_in_order_and_records_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "0001_a.sql").write_text(
                "CREATE TABLE a (id int);", encoding="utf-8"
            )
            (directory / "0002_b.sql").write_text(
                "CREATE TABLE b (id int);", encoding="utf-8"
            )
            connection = FakeConnection()
            applied = await MigrationRunner(connection).run(  # type: ignore[arg-type]
                discover_migrations(directory)
            )

        self.assertEqual(applied, ["0001_a", "0002_b"])
        self.assertEqual(
            [entry for entry in connection.executed if entry[0] == "migration"],
            [("migration", "0001_a"), ("migration", "0002_b")],
        )

    async def test_rerun_skips_already_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
            connection = FakeConnection(applied=["0001_a"])

            applied = await MigrationRunner(connection).run(  # type: ignore[arg-type]
                discover_migrations(directory)
            )

        self.assertEqual(applied, [])

    async def test_failed_migration_is_not_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
            (directory / "0002_b.sql").write_text("EXPLODE;", encoding="utf-8")
            connection = FakeConnection(fail_contains="EXPLODE")

            with self.assertRaises(RuntimeError):
                await MigrationRunner(connection).run(  # type: ignore[arg-type]
                    discover_migrations(directory)
                )

        self.assertIn("0001_a", connection.applied)
        self.assertNotIn("0002_b", connection.applied)


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class PostgresMigrationTests(unittest.IsolatedAsyncioTestCase):
    """在独立临时数据库验证空库、旧库升级、重复迁移与约束。"""

    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _scalar(self, sql: str, params: dict[str, object] | None = None) -> int:
        async with self.session_factory() as db:
            result = await db.execute(text(sql), params or {})
            return int(result.scalar_one())

    async def _expect_integrity_error(
        self, sql: str, params: dict[str, object]
    ) -> None:
        async with self.session_factory() as db:
            with self.assertRaises(IntegrityError):
                await db.execute(text(sql), params)
            await db.rollback()

    async def test_empty_database_applies_all_migrations_once(self) -> None:
        await reset_public_schema(self.engine)

        applied = await apply_all_migrations(self.engine)
        rerun = await apply_all_migrations(self.engine)

        self.assertEqual(
            applied,
            [
                "0001_identity_and_projects",
                "0002_jobs_runs_events",
                "0003_documents",
                "0004_document_failure_detail",
                "0005_evidence_graph_builds",
                "0006_graph_episode_write_outcome",
                "0007_backfill_graph_episode_write_outcome",
                "0008_graph_build_provider_calls",
                "0009_graph_claim_nature",
                "0010_author_recommendation",
            ],
        )
        self.assertEqual(rerun, [])
        self.assertEqual(
            await self._scalar(
                "SELECT count(*) FROM schema_migrations "
                "WHERE version = '0005_evidence_graph_builds'"
            ),
            1,
        )

    async def test_reset_targets_dedicated_schema_and_leaves_public_untouched(
        self,
    ) -> None:
        await reset_public_schema(self.engine)
        async with self.session_factory() as db:
            await db.execute(text("DROP TABLE IF EXISTS public.echosim_shared_sentinel"))
            await db.execute(
                text(
                    "CREATE TABLE public.echosim_shared_sentinel "
                    "(id integer PRIMARY KEY)"
                )
            )
            await db.execute(
                text("INSERT INTO public.echosim_shared_sentinel (id) VALUES (1)")
            )
            await db.commit()

        await reset_public_schema(self.engine)

        async with self.session_factory() as db:
            current_schema = (
                await db.execute(text("SELECT current_schema()"))
            ).scalar_one()
            sentinel = (
                await db.execute(
                    text("SELECT count(*) FROM public.echosim_shared_sentinel")
                )
            ).scalar_one()
            await db.execute(text("DROP TABLE public.echosim_shared_sentinel"))
            await db.commit()

        self.assertEqual(current_schema, TEST_SCHEMA)
        self.assertEqual(int(sentinel), 1)

    async def test_reset_refuses_engine_not_matching_test_database(self) -> None:
        expected = make_url(TEST_DATABASE_URL or "")
        other_url = expected.set(database="echosim_shared_not_the_env_database")
        other_engine = create_async_engine(other_url)
        try:
            with self.assertRaises(RuntimeError):
                await reset_public_schema(other_engine)
        finally:
            await other_engine.dispose()

    async def test_upgrade_from_0004_preserves_identity_and_material(self) -> None:
        await reset_public_schema(self.engine)
        baseline = await apply_baseline_migrations(self.engine)
        self.assertEqual(len(baseline), 4)

        async with self.session_factory() as db:
            seed = await seed_original_material(db, marker="upgrade")
            await db.commit()

        applied = await apply_all_migrations(self.engine)
        rerun = await apply_all_migrations(self.engine)

        self.assertEqual(
            applied,
            [
                "0005_evidence_graph_builds",
                "0006_graph_episode_write_outcome",
                "0007_backfill_graph_episode_write_outcome",
                "0008_graph_build_provider_calls",
                "0009_graph_claim_nature",
                "0010_author_recommendation",
            ],
        )
        self.assertEqual(rerun, [])
        self.assertEqual(
            await self._scalar(
                "SELECT count(*) FROM schema_migrations "
                "WHERE version = '0005_evidence_graph_builds'"
            ),
            1,
        )
        self.assertEqual(await self._scalar("SELECT count(*) FROM users"), 1)
        self.assertEqual(await self._scalar("SELECT count(*) FROM projects"), 1)
        self.assertEqual(await self._scalar("SELECT count(*) FROM documents"), 1)
        self.assertEqual(
            await self._scalar("SELECT count(*) FROM document_chunks"), 1
        )

        async with self.session_factory() as db:
            listed = await documents.list_documents(
                db, project_id=seed.project_id
            )
            _, chunks = await documents.list_chunks(
                db,
                project_id=seed.project_id,
                document_id=seed.document_id,
            )

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].status, "ready")
        self.assertEqual(listed[0].chunk_count, 1)
        self.assertEqual(listed[0].sha256, seed.sha256)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["text"], seed.chunk_text)
        self.assertEqual(chunks[0]["sourceStart"], 0)

    async def test_unique_constraints_and_material_chunk_foreign_key(self) -> None:
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)

        async with self.session_factory() as db:
            seed = await seed_original_material(db, marker="constraints")
            second_project = uuid.uuid4()
            third_project = uuid.uuid4()
            for project_id in (second_project, third_project):
                await db.execute(
                    text(
                        "INSERT INTO projects (id, owner_id, name, description) "
                        "VALUES (:id, :owner_id, :name, :description)"
                    ),
                    {
                        "id": project_id,
                        "owner_id": seed.user_id,
                        "name": f"原创约束项目 {project_id}",
                        "description": "EchoSim 原创测试项目",
                    },
                )

            binding_key_one = uuid.uuid4()
            binding_key_two = uuid.uuid4()
            graph_one = uuid.uuid4()
            graph_two = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO project_graph_bindings (project_id, "
                    "create_idempotency_key, request_name, request_description, "
                    "request_time_zone, graph_id, state) VALUES "
                    "(:project_id, :key, '原创绑定一', '原创绑定描述', 'UTC', "
                    ":graph_id, 'bound')"
                ),
                {
                    "project_id": seed.project_id,
                    "key": binding_key_one,
                    "graph_id": graph_one,
                },
            )
            await db.execute(
                text(
                    "INSERT INTO project_graph_bindings (project_id, "
                    "create_idempotency_key, request_name, request_description, "
                    "request_time_zone, graph_id, state) VALUES "
                    "(:project_id, :key, '原创绑定二', '原创绑定描述', 'UTC', "
                    ":graph_id, 'bound')"
                ),
                {
                    "project_id": second_project,
                    "key": binding_key_two,
                    "graph_id": graph_two,
                },
            )

            run_id = uuid.uuid4()
            job_id = uuid.uuid4()
            build_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO runs (id, project_id, created_by, kind, stage, "
                    "total_steps, simulated_epoch) VALUES (:id, :project_id, "
                    ":created_by, 'evidence.graph.build', 'evidence-graph', 6, now())"
                ),
                {
                    "id": run_id,
                    "project_id": seed.project_id,
                    "created_by": seed.user_id,
                },
            )
            await db.execute(
                text(
                    "INSERT INTO jobs (id, run_id, project_id, operation_key, status) "
                    "VALUES (:id, :run_id, :project_id, 'evidence-graph-build:v1', "
                    "'queued')"
                ),
                {"id": job_id, "run_id": run_id, "project_id": seed.project_id},
            )
            await db.execute(
                text(
                    "INSERT INTO graph_builds (id, project_id, run_id, question) "
                    "VALUES (:id, :project_id, :run_id, '原创建图问题')"
                ),
                {"id": build_id, "project_id": seed.project_id, "run_id": run_id},
            )

            episode_key = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO graph_episodes (build_id, episode_key, "
                    "idempotency_key, content, content_sha256) VALUES "
                    "(:build_id, 'episode-1', :key, '原创 episode 内容', :sha)"
                ),
                {
                    "build_id": build_id,
                    "key": episode_key,
                    "sha": "a" * 64,
                },
            )

            snapshot_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO graph_snapshots (id, build_id, revision, "
                    "published, node_count, edge_count) VALUES "
                    "(:id, :build_id, 1, true, 2, 1)"
                ),
                {"id": snapshot_id, "build_id": build_id},
            )
            for node_key in ("node-1", "node-2"):
                await db.execute(
                    text(
                        "INSERT INTO graph_nodes (snapshot_id, node_key, node_type, "
                        "label, origin) VALUES (:snapshot_id, :node_key, "
                        "'entity', '原创节点', 'material')"
                    ),
                    {"snapshot_id": snapshot_id, "node_key": node_key},
                )
            await db.execute(
                text(
                    "INSERT INTO graph_edges (snapshot_id, edge_key, edge_type, "
                    "label, origin, source_node_key, target_node_key) VALUES "
                    "(:snapshot_id, 'edge-1', 'relation', '原创关系', 'material', "
                    "'node-1', 'node-2')"
                ),
                {"snapshot_id": snapshot_id},
            )
            await db.execute(
                text(
                    "INSERT INTO graph_evidence (snapshot_id, target_kind, "
                    "target_key, evidence_index, document_id, chunk_index, "
                    "source_start, source_end, quote) VALUES "
                    "(:snapshot_id, 'node', 'node-1', 1, :document_id, 1, 0, "
                    ":source_end, 'EchoSim 原创测试材料')"
                ),
                {
                    "snapshot_id": snapshot_id,
                    "document_id": seed.document_id,
                    "source_end": len(seed.text),
                },
            )
            await db.commit()

        await self._expect_integrity_error(
            "INSERT INTO project_graph_bindings (project_id, "
            "create_idempotency_key, request_name, request_description, "
            "request_time_zone, state) VALUES (:project_id, :key, '重复', "
            "'重复', 'UTC', 'pending')",
            {"project_id": seed.project_id, "key": uuid.uuid4()},
        )
        await self._expect_integrity_error(
            "INSERT INTO project_graph_bindings (project_id, "
            "create_idempotency_key, request_name, request_description, "
            "request_time_zone, graph_id, state) VALUES (:project_id, :key, "
            "'重复键', '重复键', 'UTC', :graph_id, 'bound')",
            {
                "project_id": third_project,
                "key": binding_key_one,
                "graph_id": uuid.uuid4(),
            },
        )
        await self._expect_integrity_error(
            "UPDATE project_graph_bindings SET graph_id = :graph_id "
            "WHERE project_id = :project_id",
            {"project_id": second_project, "graph_id": graph_one},
        )
        await self._expect_integrity_error(
            "INSERT INTO graph_episodes (build_id, episode_key, idempotency_key, "
            "content, content_sha256) VALUES (:build_id, 'episode-1', :key, "
            "'另一个内容', :sha)",
            {"build_id": build_id, "key": uuid.uuid4(), "sha": "b" * 64},
        )
        await self._expect_integrity_error(
            "INSERT INTO graph_episodes (build_id, episode_key, idempotency_key, "
            "content, content_sha256) VALUES (:build_id, 'episode-2', :key, "
            "'原创 episode 内容', :sha)",
            {"build_id": build_id, "key": episode_key, "sha": "c" * 64},
        )
        await self._expect_integrity_error(
            "INSERT INTO graph_snapshots (id, build_id, revision) VALUES "
            "(:id, :build_id, 1)",
            {"id": uuid.uuid4(), "build_id": build_id},
        )
        await self._expect_integrity_error(
            "INSERT INTO graph_nodes (snapshot_id, node_key, node_type, label, "
            "origin) VALUES (:snapshot_id, 'node-1', 'entity', '重复节点', "
            "'material')",
            {"snapshot_id": snapshot_id},
        )
        await self._expect_integrity_error(
            "INSERT INTO graph_edges (snapshot_id, edge_key, edge_type, label, "
            "origin, source_node_key, target_node_key) VALUES "
            "(:snapshot_id, 'edge-1', 'relation', '重复边', 'material', "
            "'node-1', 'node-2')",
            {"snapshot_id": snapshot_id},
        )
        await self._expect_integrity_error(
            "INSERT INTO graph_edges (snapshot_id, edge_key, edge_type, label, "
            "origin, source_node_key, target_node_key) VALUES "
            "(:snapshot_id, 'edge-2', 'relation', '悬空端点', 'material', "
            "'missing', 'node-2')",
            {"snapshot_id": snapshot_id},
        )
        await self._expect_integrity_error(
            "INSERT INTO graph_evidence (snapshot_id, target_kind, target_key, "
            "evidence_index, document_id, chunk_index, source_start, source_end, "
            "quote) VALUES (:snapshot_id, 'node', 'node-1', 2, :document_id, 99, "
            "0, 5, '不存在的分块')",
            {"snapshot_id": snapshot_id, "document_id": seed.document_id},
        )
        await self._expect_integrity_error(
            "INSERT INTO graph_evidence (snapshot_id, target_kind, target_key, "
            "evidence_index, document_id, chunk_index, source_start, source_end, "
            "quote) VALUES (:snapshot_id, 'node', 'node-1', 1, :document_id, 1, "
            "0, 5, '重复来源')",
            {"snapshot_id": snapshot_id, "document_id": seed.document_id},
        )

        self.assertEqual(
            await self._scalar("SELECT count(*) FROM project_graph_bindings"), 2
        )
        self.assertEqual(await self._scalar("SELECT count(*) FROM graph_episodes"), 1)
        self.assertEqual(await self._scalar("SELECT count(*) FROM graph_snapshots"), 1)
        self.assertEqual(await self._scalar("SELECT count(*) FROM graph_nodes"), 2)
        self.assertEqual(await self._scalar("SELECT count(*) FROM graph_edges"), 1)
        self.assertEqual(await self._scalar("SELECT count(*) FROM graph_evidence"), 1)


if __name__ == "__main__":
    unittest.main()
