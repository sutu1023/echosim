"""数据库访问层：SQLAlchemy async engine + asyncpg。

短超时 + `pool_pre_ping` 让数据库重启后连接池自愈：`/health` 下一次请求会
丢弃失效连接、重建连接并返回 200，无需重启应用进程。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings

logger = logging.getLogger(__name__)

# 连接超时固定 2 秒；命令超时放宽到 30 秒以容纳建图快照批量写入。
# `/health` 仍由外层 HEALTH_DB_TIMEOUT_SECONDS 保证 5 秒内返回。
CONNECT_TIMEOUT_SECONDS = 2.0
COMMAND_TIMEOUT_SECONDS = 30.0

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_timeout=CONNECT_TIMEOUT_SECONDS,
    connect_args={
        "timeout": CONNECT_TIMEOUT_SECONDS,
        "command_timeout": COMMAND_TIMEOUT_SECONDS,
    },
)


SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个 AsyncSession，请求结束自动关闭。"""
    async with SessionLocal() as session:
        yield session


async def select_one() -> None:
    """执行一次真实只读探测查询；失败时抛出原始异常由调用方处理。"""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    """释放连接池，在应用关闭时调用。"""
    await engine.dispose()


async def _smoke() -> int:
    try:
        await select_one()
    except Exception as exc:  # noqa: BLE001 - 冒烟命令需要报告任意连接失败
        logger.error("SELECT 1 失败: %s", type(exc).__name__)
        print(f"SELECT 1 失败: {type(exc).__name__}")
        return 1
    print("SELECT 1 成功: 数据库可连接")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_smoke()))
