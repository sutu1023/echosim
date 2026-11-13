import re
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from echosim.api.model_connection import register_model_test
from echosim.logging import emit
from echosim.services.health import HealthService


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message},
            "request_id": request.state.request_id,
        },
    )


def create_app(
    health: HealthService,
    version: str,
    static_dir: Path | None = None,
    *,
    app_env: str = "production",
    model_test_enabled: bool = False,
    model_test=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        emit("api_started", service="api")
        yield
        emit("api_stopped", service="api")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable):
        candidate = request.headers.get("x-request-id", "")
        request.state.request_id = (
            candidate if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", candidate) else uuid4().hex
        )
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            emit("request_failed", service="api", request_id=request.state.request_id)
            response = error_response(request, 500, "INTERNAL_ERROR", "服务暂时异常，请稍后重试")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        emit(
            "request_completed",
            service="api",
            request_id=request.state.request_id,
            method=request.method,
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {404: ("NOT_FOUND", "资源不存在"), 405: ("METHOD_NOT_ALLOWED", "不支持此操作")}
        code, message = codes.get(exc.status_code, ("INVALID_REQUEST", "请求无效"))
        response = error_response(request, exc.status_code, code, message)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(request, 422, "INVALID_REQUEST", "请求参数无效")

    @app.get("/health")
    async def health_endpoint(request: Request):
        ready = await health.ready()
        return JSONResponse(
            status_code=200 if ready else 503,
            headers={"Cache-Control": "no-store"},
            content={
                "status": "ok" if ready else "degraded",
                "version": version,
                "time": datetime.now(UTC).isoformat(),
                "request_id": request.state.request_id,
                "dependencies": {"database": "up" if ready else "down"},
            },
        )

    enabled = app_env == "development" and model_test_enabled
    if enabled and model_test is not None:
        register_model_test(app, model_test)

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})

        if enabled:
            app.add_api_route("/dev/model-test", index, methods=["GET"])

    return app
