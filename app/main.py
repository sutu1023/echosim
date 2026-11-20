"""EchoSim FastAPI 应用入口。"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from .config import settings
from .db import dispose_engine
from .health import router as health_router
from .static import register_static_routes

REQUEST_ID_HEADER = "X-Request-ID"
# 只接受可安全写入日志与响应头的字符，长度上限防止日志注入与超长头。
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def resolve_request_id(candidate: str | None) -> str:
    """复用合法请求 ID，否则生成新的随机 ID。"""
    if candidate and REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """应用关闭时释放数据库连接池。"""
    yield
    await dispose_engine()


def create_app(web_dist_dir: Path | None = None) -> FastAPI:
    """构建应用：API 路由先注册，静态资源与回退路由最后注册。"""
    app = FastAPI(title="EchoSim", lifespan=lifespan)

    @app.middleware("http")
    async def attach_request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """为每个请求确定请求 ID，供失败日志与响应头关联使用。"""
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    app.include_router(health_router)
    register_static_routes(app, web_dist_dir or settings.web_dist_dir)
    return app


app = create_app()
