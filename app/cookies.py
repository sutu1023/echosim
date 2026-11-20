"""会话 Cookie 读写：浏览器会话 Cookie，不设置 Max-Age/Expires。"""

from __future__ import annotations

from fastapi import Response

from .config import settings

SESSION_COOKIE_NAME = "echosim_session"


def set_session_cookie(response: Response, token: str) -> None:
    """下发 HttpOnly 会话 Cookie。

    刻意不传 `max_age` 与 `expires`：浏览器关闭后丢弃 Cookie；服务端仍以
    `SESSION_TTL_SECONDS` 作为绝对有效期兜底。
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.session_cookie_secure,
    )


def clear_session_cookie(response: Response) -> None:
    """清除会话 Cookie（退出或会话失效时）。"""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.session_cookie_secure,
    )
