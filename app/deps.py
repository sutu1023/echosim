"""HTTP 依赖：从 Cookie 解析当前会话用户。"""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .cookies import SESSION_COOKIE_NAME
from .db import get_db
from .errors import Unauthenticated
from .services.sessions import SessionUser, load_session


async def require_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> SessionUser:
    """要求有效的会话 Cookie。

    读取 Cookie 在访问数据库之前完成：没有 Cookie 时立即 401，因此数据库停机也
    不会把匿名请求变成 503。
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise Unauthenticated(clear_cookie=False)
    return await load_session(db, token)
