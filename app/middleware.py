"""会话型写请求的来源校验（CSRF 的第二道防线）。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_same_origin(origin: str, host: str) -> bool:
    """判断 Origin 是否与请求 Host 同源；`null` 与畸形值一律拒绝。"""
    if not host:
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"}:
        return False
    if not parts.netloc or parts.username or parts.password:
        return False
    return parts.netloc.lower() == host.lower()


async def enforce_same_origin(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """非安全方法且带 Origin 时，跨源请求在业务代码前返回 403。"""
    if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin is not None and not is_same_origin(
            origin, request.headers.get("host", "")
        ):
            return JSONResponse(
                {"detail": "请求来源不被允许"}, status_code=403
            )
    return await call_next(request)
