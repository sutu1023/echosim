"""鉴权路由：登录、当前会话查询、退出。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..cookies import SESSION_COOKIE_NAME, clear_session_cookie, set_session_cookie
from ..db import get_db
from ..deps import require_user
from ..errors import InvalidCredentials
from ..schemas import LoginRequest
from ..services import accounts, sessions
from ..services.sessions import SessionUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


def user_body(user: SessionUser) -> dict[str, str | None]:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
    }


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """校验本地凭据并下发 HttpOnly 会话 Cookie。"""
    try:
        account = await accounts.authenticate(db, payload.email, payload.password)
    except InvalidCredentials:
        # 未知邮箱与错误密码返回同一响应，避免账户枚举。
        raise HTTPException(status_code=401, detail="邮箱或密码不正确") from None

    token = await sessions.create_session(
        db,
        account.id,
        previous_token=request.cookies.get(SESSION_COOKIE_NAME),
    )
    set_session_cookie(response, token)
    return {"user": user_body(account)}


@router.get("/session")
async def current_session(
    user: SessionUser = Depends(require_user),
) -> dict[str, object]:
    return {"user": user_body(user)}


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """删除当前会话并清除 Cookie；无会话调用同样返回 204。"""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await sessions.revoke_session(db, token)
    response.status_code = 204
    clear_session_cookie(response)
    return response
