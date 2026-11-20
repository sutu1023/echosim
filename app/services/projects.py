"""项目服务：创建、自有列表与单一归属校验入口。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ProjectNotFound, ServiceUnavailable

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)


@dataclass(frozen=True)
class Project:
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime


async def create_project(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    name: str,
    description: str | None,
) -> Project:
    """创建项目并写入 `project.created` 事件（同一事务）。"""
    project_id = uuid.uuid4()
    try:
        result = await db.execute(
            text(
                "INSERT INTO projects (id, owner_id, name, description) "
                "VALUES (:id, :owner_id, :name, :description) "
                "RETURNING id, name, description, created_at"
            ),
            {
                "id": project_id,
                "owner_id": owner_id,
                "name": name,
                "description": description,
            },
        )
        row = result.first()
        await db.execute(
            text(
                "INSERT INTO project_events (project_id, actor_user_id, type) "
                "VALUES (:project_id, :actor_user_id, 'project.created')"
            ),
            {"project_id": project_id, "actor_user_id": owner_id},
        )
        await db.commit()
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("项目创建失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    return Project(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
    )


async def list_owned_projects(db: AsyncSession, *, owner_id: uuid.UUID) -> list[Project]:
    """只返回当前用户拥有的项目，查询层即按所有者过滤。"""
    try:
        result = await db.execute(
            text(
                "SELECT id, name, description, created_at FROM projects "
                "WHERE owner_id = :owner_id ORDER BY created_at DESC, id DESC"
            ),
            {"owner_id": owner_id},
        )
        rows = result.fetchall()
    except DB_ERRORS as exc:
        logger.warning("项目列表查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    return [
        Project(
            id=row.id,
            name=row.name,
            description=row.description,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def resolve_owned_project(
    db: AsyncSession, *, actor_id: uuid.UUID, project_id: str
) -> Project:
    """唯一的项目归属校验入口。

    他人项目、不存在的项目与非法 ID 都抛出同一个 `ProjectNotFound`，调用方与
    外部观察者无法区分；后续材料、运行与报告必须复用本函数。
    """
    try:
        parsed_id = uuid.UUID(project_id)
    except (ValueError, AttributeError, TypeError):
        logger.info("项目归属校验失败 actor=%s reason=invalid_id", actor_id)
        raise ProjectNotFound from None

    try:
        result = await db.execute(
            text(
                "SELECT id, name, description, created_at FROM projects "
                "WHERE id = :project_id AND owner_id = :actor_id"
            ),
            {"project_id": parsed_id, "actor_id": actor_id},
        )
        row = result.first()
    except DB_ERRORS as exc:
        logger.warning("项目归属查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc

    if row is None:
        logger.info("项目归属校验失败 actor=%s reason=not_found_or_not_owner", actor_id)
        raise ProjectNotFound
    return Project(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
    )
