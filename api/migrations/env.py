import asyncio

from alembic import context

from app.adapters.database import create_engine
from app.config import Settings

target_metadata = None  # M0 has no business tables.


async def run():
    engine = create_engine(Settings())
    async with engine.connect() as connection:

        def migrate(sync_connection):
            context.configure(connection=sync_connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()

        await connection.run_sync(migrate)
    await engine.dispose()


asyncio.run(run())
