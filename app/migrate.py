"""极简 SQL 迁移器：按文件名顺序应用 `app/migrations/*.sql`。

设计取舍：本次只有一份初始 schema，不引入 Alembic；迁移只前向执行，版本记录在
`schema_migrations`，并用 PostgreSQL 会话级 advisory lock 防止并发重复应用。
SQL 文件按语句拆分后逐条执行，整个迁移在同一事务内，失败时不记录版本。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .db import engine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_SUFFIX = ".sql"
# 固定 key，保证同一数据库上的迁移进程串行执行。
ADVISORY_LOCK_KEY = 0x0EC40_0001

CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    """一个待应用的迁移文件。"""

    version: str
    path: Path


def discover_migrations(directory: Path) -> list[Migration]:
    """按文件名排序返回迁移，忽略非 `.sql` 文件与目录。"""
    return [
        Migration(version=path.stem, path=path)
        for path in sorted(directory.glob(f"*{MIGRATION_SUFFIX}"))
        if path.is_file()
    ]


def pending_migrations(
    migrations: list[Migration], applied_versions: set[str]
) -> list[Migration]:
    """过滤掉已记录版本，保留原有顺序。"""
    return [m for m in migrations if m.version not in applied_versions]


def split_sql_statements(sql: str) -> list[str]:
    """把 SQL 脚本拆成独立语句。

    处理 `--` 行注释、单引号字符串与 `$$`/`$tag$` 美元引号，避免在字符串或函数体
    内部误拆分号。空语句会被丢弃。
    """
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(sql)
    dollar_tag: str | None = None
    in_single_quote = False

    while index < length:
        char = sql[index]

        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
                continue
            buffer.append(char)
            index += 1
            continue

        if in_single_quote:
            buffer.append(char)
            if char == "'":
                # SQL 中的 '' 表示转义后的单引号，需要成对消费。
                if index + 1 < length and sql[index + 1] == "'":
                    buffer.append(sql[index + 1])
                    index += 2
                    continue
                in_single_quote = False
            index += 1
            continue

        if char == "-" and sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = length if newline == -1 else newline + 1
            buffer.append("\n")
            continue

        if char == "'":
            in_single_quote = True
            buffer.append(char)
            index += 1
            continue

        if char == "$":
            end = sql.find("$", index + 1)
            if end != -1 and sql[index + 1 : end].replace("_", "").isalnum():
                dollar_tag = sql[index : end + 1]
                buffer.append(dollar_tag)
                index = end + 1
                continue
            if end == index + 1:  # $$ 匿名美元引号
                dollar_tag = "$$"
                buffer.append(dollar_tag)
                index += 2
                continue

        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


class MigrationRunner:
    """在给定连接上应用待执行迁移；供真实连接与测试替身复用。"""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def _applied_versions(self) -> set[str]:
        result = await self.connection.execute(text("SELECT version FROM schema_migrations"))
        return {row[0] for row in result.fetchall()}

    async def run(self, migrations: list[Migration]) -> list[str]:
        applied = await self._applied_versions()
        # 结束读取版本产生的隐式事务，下一行才能显式开启每个迁移的事务。
        await self.connection.commit()
        applied_now: list[str] = []
        for migration in pending_migrations(migrations, applied):
            statements = split_sql_statements(migration.path.read_text(encoding="utf-8"))
            async with self.connection.begin():
                for statement in statements:
                    await self.connection.execute(text(statement))
                await self.connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": migration.version},
                )
            applied_now.append(migration.version)
            logger.info("已应用迁移 %s", migration.version)
        return applied_now


async def apply_migrations(
    target_engine: AsyncEngine,
    directory: Path = MIGRATIONS_DIR,
) -> list[str]:
    """连接数据库、加锁并应用全部待执行迁移。"""
    migrations = discover_migrations(directory)
    async with target_engine.connect() as connection:
        await connection.execute(text(CREATE_MIGRATIONS_TABLE))
        await connection.commit()
        await connection.execute(
            text("SELECT pg_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        )
        await connection.commit()
        try:
            return await MigrationRunner(connection).run(migrations)
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
            )
            await connection.commit()


async def _main() -> int:
    try:
        applied = await apply_migrations(engine)
    except (SQLAlchemyError, OSError, asyncio.TimeoutError) as exc:
        logger.error("数据库迁移失败: %s", type(exc).__name__)
        print(f"数据库迁移失败: {type(exc).__name__}")
        return 1
    if applied:
        print("已应用迁移: " + ", ".join(applied))
    else:
        print("无待应用迁移")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
