"""真实 PostgreSQL 测试支持：临时库安全闸门、迁移与原创小材料夹具。

只有设置 `TEST_DATABASE_URL` 时才运行依赖数据库的用例；数据库名必须包含 `test`，
防止误把开发库当作可重置的测试库。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.migrate import MIGRATIONS_DIR, apply_migrations

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
HAS_TEST_DATABASE = bool(TEST_DATABASE_URL)
SKIP_REASON = "未设置 TEST_DATABASE_URL，跳过真实 PostgreSQL 测试"

# 每次测试进程独占一个 schema，避免清理 public 或共享测试库中的其它数据。
TEST_SCHEMA = f"echosim_test_{uuid.uuid4().hex[:12]}"

ORIGINAL_MATERIAL_TEXT = (
    "EchoSim 原创测试材料：校园接驳车在雨天把发车间隔调整为十分钟，"
    "并在东门与图书馆之间增加一班备用车。"
)


@dataclass(frozen=True)
class SeedMaterial:
    user_id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    chunk_text: str
    text: str
    sha256: str


def assert_safe_test_database(url: str) -> None:
    database = make_url(url).database or ""
    if "test" not in database.lower():
        raise RuntimeError(
            "拒绝在数据库名不含 test 的库上执行破坏性测试操作"
        )


def make_test_engine() -> AsyncEngine:
    if not TEST_DATABASE_URL:
        raise RuntimeError(SKIP_REASON)
    assert_safe_test_database(TEST_DATABASE_URL)
    return create_async_engine(
        TEST_DATABASE_URL,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {"search_path": f'"{TEST_SCHEMA}",public'}
        },
    )


def _assert_engine_matches_test_database(engine: AsyncEngine) -> None:
    """拒绝清理传入 engine 与 TEST_DATABASE_URL 不一致的数据库。"""
    if not TEST_DATABASE_URL:
        raise RuntimeError(SKIP_REASON)
    expected = make_url(TEST_DATABASE_URL)
    actual = make_url(str(engine.url))
    if (
        actual.host,
        actual.port,
        actual.database,
    ) != (
        expected.host,
        expected.port,
        expected.database,
    ):
        raise RuntimeError("传入 engine 与 TEST_DATABASE_URL 不一致，拒绝清理")
    assert_safe_test_database(str(engine.url))


async def reset_public_schema(engine: AsyncEngine) -> None:
    """重置本测试进程独占的 schema，不触碰 public 或共享库中的其它对象。"""
    _assert_engine_matches_test_database(engine)
    async with engine.begin() as connection:
        current_database = (
            await connection.execute(text("SELECT current_database()"))
        ).scalar_one()
        expected = make_url(TEST_DATABASE_URL).database
        if current_database != expected:
            raise RuntimeError("engine 实际连接的数据库与 TEST_DATABASE_URL 不一致")
        await connection.execute(
            text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
        )
        await connection.execute(text(f'CREATE SCHEMA "{TEST_SCHEMA}"'))
        current_schema = (
            await connection.execute(text("SELECT current_schema()"))
        ).scalar_one()
        if current_schema != TEST_SCHEMA:
            raise RuntimeError("测试连接未使用本次独占 schema")
        await connection.execute(
            text(f'SET search_path TO "{TEST_SCHEMA}", public')
        )


async def apply_all_migrations(engine: AsyncEngine) -> list[str]:
    return await apply_migrations(engine, MIGRATIONS_DIR)


async def apply_baseline_migrations(engine: AsyncEngine) -> list[str]:
    """只应用 Change 01–06 的 0001–0004，用于构造升级前旧库。"""
    with tempfile.TemporaryDirectory() as tmp:
        baseline = Path(tmp)
        for migration in sorted(MIGRATIONS_DIR.glob("000[1-4]_*.sql")):
            shutil.copy2(migration, baseline / migration.name)
        return await apply_migrations(engine, baseline)


async def seed_original_material(
    db: AsyncSession, *, marker: str
) -> SeedMaterial:
    """写入原创账户、项目、文本材料与一个分块；调用方负责提交。"""
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    document_id = uuid.uuid4()
    text_body = ORIGINAL_MATERIAL_TEXT
    sha256 = hashlib.sha256(text_body.encode("utf-8")).hexdigest()
    await db.execute(
        text(
            "INSERT INTO users (id, email, display_name, password_hash) "
            "VALUES (:id, :email, :display_name, :password_hash)"
        ),
        {
            "id": user_id,
            "email": f"evidence-{marker}@example.invalid",
            "display_name": "原创测试账户",
            "password_hash": "test-only-password-hash",
        },
    )
    await db.execute(
        text(
            "INSERT INTO projects (id, owner_id, name, description) "
            "VALUES (:id, :owner_id, :name, :description)"
        ),
        {
            "id": project_id,
            "owner_id": user_id,
            "name": f"原创证据图谱项目 {marker}",
            "description": "EchoSim 原创测试项目",
        },
    )
    await db.execute(
        text(
            "INSERT INTO documents (id, project_id, uploaded_by, display_name, kind, "
            "content_type, size_bytes, sha256, storage_path, text_encoding, status, "
            "chunk_count) VALUES (:id, :project_id, :uploaded_by, :display_name, "
            "'text', 'text/plain', :size_bytes, :sha256, :storage_path, 'utf-8', "
            "'ready', 1)"
        ),
        {
            "id": document_id,
            "project_id": project_id,
            "uploaded_by": user_id,
            "display_name": "原创接驳车小材料.txt",
            "size_bytes": len(text_body.encode("utf-8")),
            "sha256": sha256,
            "storage_path": f"test/{project_id}/{document_id}.txt",
        },
    )
    await db.execute(
        text(
            "INSERT INTO document_chunks (document_id, chunk_index, paragraph_start, "
            "paragraph_end, source_start, source_end, text) "
            "VALUES (:document_id, 1, 1, 1, 0, :source_end, :text)"
        ),
        {
            "document_id": document_id,
            "source_end": len(text_body),
            "text": text_body,
        },
    )
    return SeedMaterial(
        user_id=user_id,
        project_id=project_id,
        document_id=document_id,
        chunk_index=1,
        chunk_text=text_body,
        text=text_body,
        sha256=sha256,
    )
