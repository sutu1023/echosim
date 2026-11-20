"""账户服务：本地凭据认证与账户创建。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import AccountExists, InvalidCredentials, ServiceUnavailable
from ..security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    normalize_email,
    verify_password,
)

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)


@dataclass(frozen=True)
class Account:
    id: uuid.UUID
    email: str
    display_name: str | None


async def authenticate(db: AsyncSession, email: str, password: str) -> Account:
    """校验邮箱与密码；账户不存在与密码错误对外不可区分。"""
    normalized = normalize_email(email)
    try:
        result = await db.execute(
            text(
                "SELECT id, email, display_name, password_hash "
                "FROM users WHERE email = :email"
            ),
            {"email": normalized},
        )
        row = result.first()
    except DB_ERRORS as exc:
        logger.warning("账户查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc

    encoded = row.password_hash if row is not None else DUMMY_PASSWORD_HASH
    matches = verify_password(password, encoded)
    if row is None or not matches:
        raise InvalidCredentials
    return Account(id=row.id, email=row.email, display_name=row.display_name)


async def create_account(
    db: AsyncSession,
    email: str,
    password: str,
    display_name: str | None = None,
    *,
    update_password: bool = False,
) -> Account:
    """创建账户；邮箱已存在时按 `update_password` 决定失败或重设密码。

    重设密码会删除该用户已有会话，使旧 Cookie 立即失效。
    """
    normalized = normalize_email(email)
    password_hash = hash_password(password)
    try:
        if update_password:
            result = await db.execute(
                text(
                    "INSERT INTO users (email, display_name, password_hash) "
                    "VALUES (:email, :display_name, :password_hash) "
                    "ON CONFLICT (email) DO UPDATE SET "
                    "password_hash = EXCLUDED.password_hash, "
                    "display_name = COALESCE(EXCLUDED.display_name, users.display_name) "
                    "RETURNING id, email, display_name"
                ),
                {
                    "email": normalized,
                    "display_name": display_name,
                    "password_hash": password_hash,
                },
            )
            account = _to_account(result.first())
            await db.execute(
                text("DELETE FROM sessions WHERE user_id = :user_id"),
                {"user_id": account.id},
            )
        else:
            result = await db.execute(
                text(
                    "INSERT INTO users (email, display_name, password_hash) "
                    "VALUES (:email, :display_name, :password_hash) "
                    "RETURNING id, email, display_name"
                ),
                {
                    "email": normalized,
                    "display_name": display_name,
                    "password_hash": password_hash,
                },
            )
            account = _to_account(result.first())
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if update_password:
            logger.warning("账户更新失败: %s", type(exc).__name__)
            raise ServiceUnavailable from exc
        raise AccountExists from exc
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("账户写入失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    return account


def _to_account(row: object) -> Account:
    return Account(id=row.id, email=row.email, display_name=row.display_name)
