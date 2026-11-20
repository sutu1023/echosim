"""EchoSim FastAPI 应用入口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .config import settings
from .db import dispose_engine
from .health import router as health_router
from .static import register_static_routes


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """应用关闭时释放数据库连接池。"""
    yield
    await dispose_engine()


def create_app(web_dist_dir: Path | None = None) -> FastAPI:
    """构建应用：API 路由先注册，静态资源与回退路由最后注册。"""
    app = FastAPI(title="EchoSim", lifespan=lifespan)
    app.include_router(health_router)
    register_static_routes(app, web_dist_dir or settings.web_dist_dir)
    return app


app = create_app()
