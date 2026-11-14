import asyncio
from pathlib import Path

import uvicorn

from echosim.adapters.credentials import Credentials
from echosim.adapters.model_connection import OpenCodeProvider
from echosim.adapters.passwords import Passwords
from echosim.adapters.postgres import PostgresProbe
from echosim.adapters.store import PostgresStore
from echosim.api.app import create_app
from echosim.config import load_settings
from echosim.logging import configure_logging
from echosim.services.accounts import Accounts
from echosim.services.adoption import Adoptions
from echosim.services.chapters import Chapters
from echosim.services.health import HealthService
from echosim.services.model_config import ModelConfigurations
from echosim.services.model_connection import ModelConnectionService
from echosim.services.simulation import Simulations
from echosim.services.story import Stories
from echosim.services.story_facts import StoryFacts
from echosim.services.story_graph import StoryGraph
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
    try:
        vault = Credentials(settings.model_master_key.get_secret_value())
    except ValueError:
        raise SystemExit("Invalid runtime configuration; MODEL_MASTER_KEY required") from None
    store = PostgresStore(settings.database_url.get_secret_value())
    try:
        asyncio.run(store.check_schema())
    except Exception:
        raise SystemExit("Business schema unavailable; run the migration command") from None
    configurations = ModelConfigurations(store, vault, OpenCodeProvider())
    stories = Stories(store, configurations)
    probe = PostgresProbe(settings.database_url.get_secret_value())
    app = create_app(
        HealthService(probe, settings.health_timeout_seconds),
        settings.app_version,
        Path("static"),
        app_env=settings.app_env,
        model_test_enabled=settings.model_connection_test_enabled,
        model_test=ModelConnectionService(OpenCodeProvider()),
        accounts=Accounts(store, Passwords()),
        model_config=configurations,
        stories=stories,
        graph=StoryGraph(stories, StoryFacts(OpenCodeProvider())),
        chapters=Chapters(stories, StoryFacts(OpenCodeProvider())),
        adoptions=Adoptions(stories, StoryFacts(OpenCodeProvider())),
        simulations=Simulations(stories, OpenCodeProvider()),
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
