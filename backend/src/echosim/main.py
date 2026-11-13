from pathlib import Path

import uvicorn

from echosim.adapters.model_connection import OpenCodeProvider
from echosim.adapters.postgres import PostgresProbe
from echosim.api.app import create_app
from echosim.config import load_settings
from echosim.logging import configure_logging
from echosim.services.health import HealthService
from echosim.services.model_connection import ModelConnectionService


def main() -> None:
    configure_logging()
    settings = load_settings()
    probe = PostgresProbe(settings.database_url.get_secret_value())
    app = create_app(
        HealthService(probe, settings.health_timeout_seconds),
        settings.app_version,
        Path("static"),
        app_env=settings.app_env,
        model_test_enabled=settings.model_connection_test_enabled,
        model_test=ModelConnectionService(OpenCodeProvider()),
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()
