"""迁移器解析、顺序与事务语义的单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.migrate import (
    MigrationRunner,
    discover_migrations,
    pending_migrations,
    split_sql_statements,
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


if __name__ == "__main__":
    unittest.main()
