from pathlib import Path

import uvicorn

from echosim.adapters.postgres import PostgresProbe
from echosim.api.app import create_app
from echosim.config import load_settings
from echosim.logging import configure_logging
from echosim.services.health import HealthService


def main() -> None:
    configure_logging()
    settings = load_settings()
    probe = PostgresProbe(settings.database_url.get_secret_value())
    app = create_app(
        HealthService(probe, settings.health_timeout_seconds), settings.app_version, Path("static")
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
