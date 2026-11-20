"""健康检查路由：每次请求真实执行 `SELECT 1`，不做缓存。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .config import settings
from .db import select_one

logger = logging.getLogger(__name__)

router = APIRouter()

HEALTHY_BODY = {"status": "ok", "database": "ok"}
DEGRADED_BODY = {"status": "degraded", "database": "unavailable"}


async def probe_database() -> bool:
    """执行一次数据库探测。

    只在服务端日志记录异常类型，响应固定文案，绝不回显 DSN、凭据或主机名。
    """
    try:
        async with asyncio.timeout(settings.health_db_timeout_seconds):
            await select_one()
    except Exception as exc:  # noqa: BLE001 - 任何探测失败都必须降级而不是抛出
        logger.warning("数据库探测失败: %s", type(exc).__name__)
        logger.debug("数据库探测异常堆栈", exc_info=True)
        return False
    return True


@router.get("/livez")
async def livez() -> JSONResponse:
    """存活信号：不访问数据库，进程可响应即 200。"""
    return JSONResponse({"status": "ok"}, status_code=200)


@router.get("/health")
async def health() -> JSONResponse:
    """就绪信号：数据库可用 200，不可用或超时 503。"""
    if await probe_database():
        return JSONResponse(HEALTHY_BODY, status_code=200)
    return JSONResponse(DEGRADED_BODY, status_code=503)
