import json
import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

from starlette.requests import Request
from starlette.routing import Match

from app.api.contracts import respond
from app.api.policies import PUBLIC_ROUTES
from app.log_retention import DailyJSONHandler

logger = logging.getLogger("echosim.http")


def configure_logging(directory=None) -> None:
    handler = DailyJSONHandler(directory) if directory is not None else logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


class BoundaryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        request_id = uuid4().hex  # Never echo arbitrary caller-controlled header content.
        scope.setdefault("state", {})["request_id"] = request_id
        request = Request(scope, receive)
        status = 500
        response_started = False

        async def wrapped_send(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
            await send(message)

        # Resolve route metadata without invoking a handler.
        method, path = scope["method"], scope["path"]
        known_path = any(r.matches(scope)[0] != Match.NONE for r in scope["app"].routes)
        try:
            if (method, path) not in PUBLIC_ROUTES:
                code = "ACCESS_DENIED" if known_path else "NOT_FOUND"
                if known_path and path in {"/health", "/livez"}:
                    code = "METHOD_NOT_ALLOWED"
                await respond(request, code)(scope, receive, wrapped_send)
            else:
                await self.app(scope, receive, wrapped_send)
        except Exception:
            if not response_started:
                await respond(request, "INTERNAL_ERROR")(scope, receive, wrapped_send)
        finally:
            # Fixed field allowlist: never emit raw path/query/body/IP/headers/exceptions.
            logger.info(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "level": "INFO",
                        "module": "api",
                        "event": "request.completed",
                        "request_id": request_id,
                        "method": method,
                        "route": path if (method, path) in PUBLIC_ROUTES else "unregistered",
                        "status_code": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    },
                    ensure_ascii=False,
                )
            )
