import asyncio
from pathlib import Path

import uvicorn

from echosim.adapters.model_connection import OpenCodeProvider
from echosim.adapters.passwords import Passwords
from echosim.adapters.postgres import PostgresProbe
from echosim.adapters.store import PostgresStore
from echosim.api.app import create_app
from echosim.config import load_settings
from echosim.logging import configure_logging
from echosim.services.accounts import Accounts
from echosim.services.health import HealthService
from echosim.services.model_connection import ModelConnectionService
from echosim.services.works import Works


def main() -> None:
    configure_logging()
    settings = load_settings()
    if not settings.public_origin or (
        settings.app_env == "production" and not settings.public_origin.startswith("https://")
    ):
        raise SystemExit(
            "Invalid runtime configuration; PUBLIC_ORIGIN required (HTTPS in production)"
        )
    store = PostgresStore(settings.database_url.get_secret_value())
    try:
        asyncio.run(store.check_schema())
    except Exception:
        raise SystemExit("Business schema unavailable; run the migration command") from None
    probe = PostgresProbe(settings.database_url.get_secret_value())
    app = create_app(
        HealthService(probe, settings.health_timeout_seconds),
        settings.app_version,
        Path("static"),
        app_env=settings.app_env,
        model_test_enabled=settings.model_connection_test_enabled,
        model_test=ModelConnectionService(OpenCodeProvider()),
        accounts=Accounts(store, Passwords()),
        works=Works(store),
        public_origin=settings.public_origin,
        quick_login_enabled=settings.dev_quick_login_enabled,
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
