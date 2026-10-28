from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=settings.probe_timeout_seconds,
        hide_parameters=True,
        connect_args={"timeout": settings.probe_timeout_seconds},
    )
