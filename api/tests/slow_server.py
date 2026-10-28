"""In-flight SIGTERM fixture, never included in the production image."""

import asyncio
import sys
from pathlib import Path

import uvicorn

from app.config import Settings
from app.main import create_app
from app.server import DrainingServer


class SlowProbe:
    async def ping(self):
        await asyncio.to_thread(Path(sys.argv[2]).write_text, "started")
        await asyncio.sleep(1)


settings = Settings(
    app_env="test",
    app_version="signal-fixture",
    model_mode="fake",
    database_url="postgresql+asyncpg://test:test@localhost/test",
    api_host="127.0.0.1",
    api_port=int(sys.argv[1]),
    probe_timeout_seconds=2,
)
app = create_app(settings, SlowProbe())
DrainingServer(
    app,
    uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=30,
    ),
).run()
