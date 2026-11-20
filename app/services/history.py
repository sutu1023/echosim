"""项目历史服务：只聚合当前用户自有项目的事件。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ServiceUnavailable

logger = logging.getLogger(__name__)
DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)

HISTORY_LIMIT = 50


@dataclass(frozen=True)
class HistoryEvent:
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    type: str
    occurred_at: datetime


async def list_owned_history(
    db: AsyncSession, *, owner_id: uuid.UUID
) -> list[HistoryEvent]:
    """按所有者过滤项目后返回最近事件，他人事件不会出现在结果集中。"""
    try:
        result = await db.execute(
            text(
                "SELECT e.id, e.project_id, p.name AS project_name, "
                "e.type, e.created_at AS occurred_at "
                "FROM project_events e JOIN projects p ON p.id = e.project_id "
                "WHERE p.owner_id = :owner_id "
                "ORDER BY e.created_at DESC, e.id DESC LIMIT :limit"
            ),
            {"owner_id": owner_id, "limit": HISTORY_LIMIT},
        )
        rows = result.fetchall()
    except DB_ERRORS as exc:
        logger.warning("历史查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    return [
        HistoryEvent(
            id=row.id,
            project_id=row.project_id,
            project_name=row.project_name,
            type=row.type,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]
