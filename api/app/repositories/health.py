from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class HealthRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
