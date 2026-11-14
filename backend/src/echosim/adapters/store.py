from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from psycopg import AsyncConnection, Error
from psycopg.rows import dict_row

from echosim.adapters.canon_store import CanonQueries
from echosim.adapters.chapter_store import ChapterQueries
from echosim.adapters.graph_store import GraphQueries
from echosim.adapters.model_config_store import ModelConfigQueries
from echosim.adapters.simulation_store import SimulationQueries
from echosim.adapters.story_store import StoryQueries
from echosim.domain.accounts import Fault

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class PostgresStore:
    def __init__(self, url):
        self.url = url

    @asynccontextmanager
    async def transaction(self):
        try:
            async with await AsyncConnection.connect(
                self.url,
                row_factory=dict_row,
                connect_timeout=3,
                options="-c statement_timeout=5000 -c lock_timeout=4000",
            ) as connection:
                yield Queries(connection)
        except Error:
            raise Fault("SERVICE_UNAVAILABLE", 503) from None

    async def migrate(self, directory=MIGRATIONS):
        async with self.transaction() as db:
            await db.lock("schema-migration")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY)"
            )
            for path in sorted(directory.glob("*.sql")):
                if not await db.one(
                    "SELECT version FROM schema_migrations WHERE version=%s", (path.name,)
                ):
                    await db.execute(path.read_text())
                    await db.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))

    async def check_schema(self):
        async with self.transaction() as db:
            rows = await db.all("SELECT version FROM schema_migrations")
            if {r["version"] for r in rows} != {p.name for p in MIGRATIONS.glob("*.sql")}:
                raise Fault("SCHEMA_REQUIRED", 503)
            await db.execute("SELECT id FROM users LIMIT 0")
            await db.execute("SELECT token_hash FROM sessions LIMIT 0")
            await db.execute("SELECT id FROM works LIMIT 0")
            await db.execute("SELECT work_id FROM work_creation_requests LIMIT 0")
            await db.execute("SELECT key FROM login_attempt_windows LIMIT 0")


class Queries(
    ModelConfigQueries, StoryQueries, SimulationQueries, CanonQueries, GraphQueries, ChapterQueries
):
    def __init__(self, connection):
        self.connection = connection

    async def execute(self, query, params=()):
        return await self.connection.execute(query, params or None)

    async def one(self, query, params=()):
        row = await (await self.execute(query, params)).fetchone()
        return self.normalize(row)

    async def all(self, query, params=()):
        return [self.normalize(r) for r in await (await self.execute(query, params)).fetchall()]

    @staticmethod
    def normalize(row):
        if row:
            for key in (
                "id",
                "user_id",
                "work_id",
                "candidate_id",
                "confirmed_id",
                "configuration_id",
                "fence",
                "request_key",
                "pending_simulation_id",
            ):
                if key in row and row[key] is not None:
                    row[key] = str(row[key])
        return row

    async def lock(self, key):
        await self.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))

    async def user(self, name):
        return await self.one("SELECT * FROM users WHERE username=%s", (name,))

    async def save_user(self, name, encoded, now):
        await self.execute(
            "INSERT INTO users VALUES (%s,%s,%s,%s,%s) ON CONFLICT (username) "
            "DO UPDATE SET password_hash=EXCLUDED.password_hash, updated_at=EXCLUDED.updated_at",
            (uuid4(), name, encoded, now, now),
        )

    async def revoke_user(self, owner):
        await self.execute("DELETE FROM sessions WHERE user_id=%s", (owner,))

    async def save_session(self, token, owner, csrf, now, expiry):
        await self.execute(
            "INSERT INTO sessions VALUES (%s,%s,%s,%s,%s,%s)",
            (token, owner, csrf, now, now, expiry),
        )

    async def session(self, token):
        return await self.one(
            "SELECT s.user_id,s.csrf,s.expires_at,u.username FROM sessions s "
            "JOIN users u ON u.id=s.user_id WHERE token_hash=%s",
            (token,),
        )

    async def revoke(self, token):
        await self.execute("DELETE FROM sessions WHERE token_hash=%s", (token,))

    async def attempt(self, key):
        return await self.one("SELECT count,until FROM login_attempt_windows WHERE key=%s", (key,))

    async def fail_attempt(self, key, now, until):
        await self.execute(
            "INSERT INTO login_attempt_windows VALUES (%s,1,%s) ON CONFLICT (key) DO UPDATE SET "
            "count=CASE WHEN login_attempt_windows.until<=%s THEN 1 "
            "ELSE login_attempt_windows.count+1 END, "
            "until=CASE WHEN login_attempt_windows.until<=%s THEN EXCLUDED.until "
            "ELSE login_attempt_windows.until END",
            (key, until, now, now),
        )

    async def created_work(self, owner, key):
        return await self.one(
            "SELECT w.id,w.title,w.created_at,w.updated_at FROM works w "
            "JOIN work_creation_requests r ON (w.user_id,w.id)=(r.user_id,r.work_id) "
            "WHERE r.user_id=%s AND r.operation='create' AND r.request_key=%s",
            (owner, key),
        )

    async def create_work(self, owner, title, key):
        now, work_id = datetime.now(UTC), uuid4()
        await self.execute(
            "INSERT INTO works VALUES (%s,%s,%s,%s,%s)", (work_id, owner, title, now, now)
        )
        await self.execute(
            "INSERT INTO work_creation_requests VALUES (%s,'create',%s,%s,%s,%s)",
            (owner, key, work_id, now, now),
        )
        return await self.work(owner, str(work_id))

    async def work(self, owner, work_id):
        return await self.one(
            "SELECT id,title,created_at,updated_at FROM works WHERE user_id=%s AND id=%s",
            (owner, work_id),
        )

    async def list_works(self, owner, limit, position):
        query = "SELECT id,title,created_at,updated_at FROM works WHERE user_id=%s"
        params = [owner]
        if position:
            query += " AND (created_at,id)<(%s,%s)"
            params.extend(position)
        return await self.all(
            query + " ORDER BY created_at DESC,id DESC LIMIT %s", [*params, limit]
        )
