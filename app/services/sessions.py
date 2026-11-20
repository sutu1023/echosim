"""服务端会话：不透明令牌、SHA-256 存储、7 天绝对有效期与撤销。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..errors import ServiceUnavailable, Unauthenticated

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)
# token_urlsafe(32) 固定 43 个字符；提前拒绝明显非法的 Cookie，避免无谓查询。
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
TOKEN_BYTES = 32


@dataclass(frozen=True)
class SessionUser:
    id: uuid.UUID
    email: str
    display_name: str | None


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    previous_token: str | None = None,
) -> str:
    """创建会话并对本次请求携带的旧会话执行删除，避免会话固定。"""
    token = new_token()
    try:
        if previous_token:
            await db.execute(
                text("DELETE FROM sessions WHERE token_hash = :token_hash"),
                {"token_hash": hash_token(previous_token)},
            )
        await db.execute(
            text(
                "INSERT INTO sessions (user_id, token_hash, expires_at) "
                "VALUES (:user_id, :token_hash, :expires_at)"
            ),
            {
                "user_id": user_id,
                "token_hash": hash_token(token),
                "expires_at": datetime.now(timezone.utc)
                + timedelta(seconds=settings.session_ttl_seconds),
            },
        )
        await db.commit()
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("会话写入失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    return token


async def load_session(db: AsyncSession, token: str) -> SessionUser:
    """按 Cookie 令牌加载会话。

    无 Cookie 的判断由调用方完成；这里处理格式非法、查无记录、已过期与数据库
    不可达。数据库不可达抛出 `ServiceUnavailable`，由全局处理器返回 503 且不清
    Cookie，保证恢复后同一会话仍可继续使用。
    """
    if not TOKEN_PATTERN.fullmatch(token):
        raise Unauthenticated(clear_cookie=True)

    try:
        result = await db.execute(
            text(
                "SELECT s.id AS session_id, s.expires_at, u.id, u.email, u.display_name "
                "FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = :token_hash"
            ),
            {"token_hash": hash_token(token)},
        )
        row = result.first()
        if row is None:
            raise Unauthenticated(clear_cookie=True)
        if row.expires_at <= datetime.now(timezone.utc):
            await db.execute(
                text("DELETE FROM sessions WHERE id = :session_id"),
                {"session_id": row.session_id},
            )
            await db.commit()
            raise Unauthenticated(clear_cookie=True)
    except Unauthenticated:
        raise
    except DB_ERRORS as exc:
        logger.warning("会话查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc

    return SessionUser(id=row.id, email=row.email, display_name=row.display_name)


async def revoke_session(db: AsyncSession, token: str) -> None:
    """删除当前会话；不存在时保持幂等。"""
    try:
        await db.execute(
            text("DELETE FROM sessions WHERE token_hash = :token_hash"),
            {"token_hash": hash_token(token)},
        )
        await db.commit()
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("会话撤销失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
