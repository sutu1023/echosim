"""EchoSim FastAPI 应用入口。"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .config import settings
from .cookies import clear_session_cookie
from .db import dispose_engine
from .errors import (
    DocumentNotFound,
    DocumentNotText,
    GraphBuildConflict,
    GraphBuildNotFound,
    GraphLimitExceeded,
    GraphSnapshotInconsistent,
    GraphSnapshotNotFound,
    InvalidCursor,
    InvalidSnapshotRevision,
    JobNotFound,
    ProjectNotFound,
    ServiceUnavailable,
    Unauthenticated,
)
from .health import router as health_router
from .middleware import enforce_same_origin
from .routers.auth import router as auth_router
from .routers.documents import router as documents_router
from .routers.evidence_graph import router as evidence_graph_router
from .routers.history import router as history_router
from .routers.jobs import router as jobs_router
from .routers.projects import router as projects_router
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

    app.middleware("http")(enforce_same_origin)

    @app.exception_handler(Unauthenticated)
    async def handle_unauthenticated(
        _: Request, exc: Unauthenticated
    ) -> JSONResponse:
        response = JSONResponse({"detail": "未登录"}, status_code=401)
        if exc.clear_cookie:
            clear_session_cookie(response)
        return response

    @app.exception_handler(ServiceUnavailable)
    async def handle_unavailable(_: Request, __: ServiceUnavailable) -> JSONResponse:
        return JSONResponse({"detail": "服务暂时不可用"}, status_code=503)

    @app.exception_handler(ProjectNotFound)
    async def handle_project_not_found(
        _: Request, __: ProjectNotFound
    ) -> JSONResponse:
        return JSONResponse({"detail": "项目不存在"}, status_code=404)

    @app.exception_handler(JobNotFound)
    async def handle_job_not_found(_: Request, __: JobNotFound) -> JSONResponse:
        return JSONResponse({"detail": "任务不存在"}, status_code=404)

    @app.exception_handler(DocumentNotFound)
    async def handle_document_not_found(
        _: Request, __: DocumentNotFound
    ) -> JSONResponse:
        return JSONResponse({"detail": "材料不存在"}, status_code=404)

    @app.exception_handler(DocumentNotText)
    async def handle_document_not_text(
        _: Request, __: DocumentNotText
    ) -> JSONResponse:
        return JSONResponse(
            {"detail": "该材料没有可定位的文本原文"}, status_code=409
        )

    @app.exception_handler(InvalidCursor)
    async def handle_invalid_cursor(_: Request, __: InvalidCursor) -> JSONResponse:
        return JSONResponse({"detail": "游标无效"}, status_code=400)

    @app.exception_handler(GraphBuildNotFound)
    async def handle_graph_build_not_found(
        _: Request, __: GraphBuildNotFound
    ) -> JSONResponse:
        return JSONResponse({"detail": "建图不存在"}, status_code=404)

    @app.exception_handler(GraphSnapshotNotFound)
    async def handle_graph_snapshot_not_found(
        _: Request, __: GraphSnapshotNotFound
    ) -> JSONResponse:
        return JSONResponse({"detail": "快照不存在"}, status_code=404)

    @app.exception_handler(InvalidSnapshotRevision)
    async def handle_snapshot_revision(
        _: Request, __: InvalidSnapshotRevision
    ) -> JSONResponse:
        return JSONResponse({"detail": "快照版本无效"}, status_code=400)

    @app.exception_handler(GraphBuildConflict)
    async def handle_graph_build_conflict(
        _: Request, exc: GraphBuildConflict
    ) -> JSONResponse:
        status = 422 if exc.code == "too_many_documents" else 409
        detail = (
            "单次最多选择 5 份材料"
            if exc.code == "too_many_documents"
            else "请等待所选材料解析完成"
            if exc.code == "document_not_ready"
            else "建图请求与当前任务状态冲突"
        )
        return JSONResponse(
            {"detail": detail, "code": exc.code}, status_code=status
        )

    @app.exception_handler(GraphLimitExceeded)
    async def handle_graph_limit(
        _: Request, exc: GraphLimitExceeded
    ) -> JSONResponse:
        return JSONResponse(
            {
                "detail": "构建结果超过规模上限，未发布截断结果",
                "code": exc.code,
                "limit": exc.limit,
                "actual": exc.actual,
            },
            status_code=409,
        )

    @app.exception_handler(GraphSnapshotInconsistent)
    async def handle_graph_snapshot_inconsistent(
        _: Request, __: GraphSnapshotInconsistent
    ) -> JSONResponse:
        return JSONResponse({"detail": "服务暂时不可用"}, status_code=503)

    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(jobs_router)
    app.include_router(documents_router)
    app.include_router(evidence_graph_router)
    app.include_router(history_router)
    app.include_router(health_router)
    register_static_routes(app, web_dist_dir or settings.web_dist_dir)
    return app


app = create_app()
