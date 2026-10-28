import asyncio
import json
import sys
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.adapters.database import create_engine
from app.api.contracts import respond
from app.api.routes import router
from app.config import Settings
from app.log_retention import prune_logs
from app.observability import BoundaryMiddleware, configure_logging
from app.repositories.health import HealthRepository
from app.services.health import HealthService


def create_app(settings: Settings | None = None, probe=None) -> FastAPI:
    if settings is None:
        try:
            settings = Settings()
        except ValidationError as error:
            # Pydantic's default repr includes input values, so emit only field names/types.
            fields = [
                {"field": ".".join(map(str, e["loc"])), "type": e["type"]}
                for e in error.errors(include_input=False, include_url=False)
            ]
            print(json.dumps({"event": "configuration.invalid", "fields": fields}), file=sys.stderr)
            raise SystemExit(2) from None
    engine = None if probe is not None else create_engine(settings)
    checker = probe if probe is not None else HealthRepository(engine)
    service = HealthService(checker, settings.app_version, settings.probe_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app):
        async def cleanup_logs():
            while True:
                if settings.log_directory is not None:
                    prune_logs(settings.log_directory, datetime.now(UTC))
                await asyncio.sleep(60)

        cleanup = asyncio.create_task(cleanup_logs())
        try:
            yield
        finally:
            service.draining = True
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            if engine is not None:
                await engine.dispose()

    configure_logging(settings.log_directory)
    app = FastAPI(
        lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False
    )
    app.state.settings, app.state.health = settings, service
    app.add_middleware(BoundaryMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        fields = [
            {"field": ".".join(map(str, e["loc"])), "type": e["type"]} for e in error.errors()
        ]
        return respond(request, "INVALID_INPUT", {"field_errors": fields})

    app.include_router(router)
    return app
