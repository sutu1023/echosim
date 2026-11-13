from psycopg import AsyncConnection


class PostgresProbe:
    def __init__(self, url: str):
        self.url = url

    async def check(self) -> bool:
        async with await AsyncConnection.connect(self.url, autocommit=True) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                return await cursor.fetchone() == (1,)
