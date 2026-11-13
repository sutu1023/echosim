"""Run only against a disposable database supplied by scripts/private_integration.py."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from echosim.adapters.passwords import Passwords
from echosim.adapters.store import PostgresStore
from echosim.domain.accounts import Fault
from echosim.services.accounts import Accounts, digest
from echosim.services.works import Works


def test_postgres_contracts(tmp_path: Path):
    async def scenario():
        store = PostgresStore(os.environ["ECHOSIM_TEST_DATABASE_URL"])
        with pytest.raises(Fault):
            await store.check_schema()
        await asyncio.gather(store.migrate(), store.migrate())
        await store.migrate()
        await store.check_schema()
        broken = tmp_path / "002_failure.sql"
        broken.write_text("CREATE TABLE must_rollback (id integer); SELECT invalid_column;")
        with pytest.raises(Fault):
            await store.migrate(tmp_path)
        async with store.transaction() as db:
            assert (await db.one("SELECT to_regclass('must_rollback') AS name"))["name"] is None
            assert not await db.one(
                "SELECT version FROM schema_migrations WHERE version=%s", (broken.name,)
            )
        now = datetime.now(UTC)
        auth = Accounts(store, Passwords(), lambda: now)
        await auth.provision("alice", "integration-password-1")
        await auth.provision("bob", "integration-password-2")
        with pytest.raises(Fault, match="ACCOUNT_EXISTS"):
            await auth.provision("alice", "integration-password-3")
        first = await auth.login("alice", "integration-password-1", "local")
        second = await auth.login("alice", "integration-password-1", "local")
        other = await auth.login("bob", "integration-password-2", "local")
        a = (await auth.current(first["token"]))["user_id"]
        b = (await auth.current(other["token"]))["user_id"]
        works = Works(store)
        key = str(uuid4())
        rows = await asyncio.gather(*(works.create(a, "春山", key) for _ in range(8)))
        assert len({r["id"] for r in rows}) == 1
        recreated = Works(PostgresStore(store.url))
        assert await recreated.create(a, "春山", key) == rows[0]
        assert (await Accounts(PostgresStore(store.url), Passwords()).current(first["token"]))[
            "user_id"
        ] == a
        with pytest.raises(Fault, match="IDEMPOTENCY_CONFLICT"):
            await works.create(a, "秋水", key)
        other_work = await works.create(b, "春山", key)
        for identifier in [other_work["id"], str(uuid4()), "unknown"]:
            with pytest.raises(Fault, match="NOT_FOUND"):
                await works.get(a, identifier)
        for _ in range(5):
            await works.create(a, "同名", str(uuid4()))
        # Make all timestamps equal to exercise the unique id tie-breaker.
        async with store.transaction() as db:
            await db.execute("UPDATE works SET created_at=%s WHERE user_id=%s", (now, a))
        page, ids = None, []
        while True:
            result = await works.list(a, 2, page)
            ids.extend(r["id"] for r in result["items"])
            page = result["next_cursor"]
            if not page:
                break
        assert len(ids) == len(set(ids)) == 6
        rollback_key = str(uuid4())
        with pytest.raises(RuntimeError):
            async with store.transaction() as db:
                await db.create_work(a, "回滚", rollback_key)
                raise RuntimeError("injected")
        async with store.transaction() as db:
            assert await db.created_work(a, rollback_key) is None
        await works.create(a, "回滚", rollback_key)
        # Database failure during switch must roll back both insertion and revocation.
        await auth.provision("experience-a", "integration-password-1")
        await auth.provision("experience-b", "integration-password-2")
        quick = await auth.quick("A", None)
        await auth.provision("experience-a", "will-not-replace-password", preserve=True)
        await auth.login("experience-a", "integration-password-1", "demo")
        async with store.transaction() as db:
            await db.execute(
                "CREATE FUNCTION reject_session() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'injected'; END $$"
            )
            await db.execute(
                "CREATE TRIGGER reject_session BEFORE INSERT ON sessions "
                "FOR EACH ROW EXECUTE FUNCTION reject_session()"
            )
        try:
            with pytest.raises(Fault):
                await auth.quick("B", quick["token"])
            await auth.current(quick["token"])
        finally:
            async with store.transaction() as db:
                await db.execute("DROP TRIGGER reject_session ON sessions")
        await auth.quick("B", quick["token"])
        with pytest.raises(Fault, match="UNAUTHENTICATED"):
            await auth.current(quick["token"])
        await auth.provision("alice", "integration-password-new", reset=True)
        for old in [first, second]:
            with pytest.raises(Fault, match="UNAUTHENTICATED"):
                await auth.current(old["token"])
        # Race old-password login against reset: any token minted before reset is revoked.
        results = await asyncio.gather(
            auth.login("alice", "integration-password-new", "race"),
            auth.provision("alice", "integration-password-final", reset=True),
            return_exceptions=True,
        )
        if isinstance(results[0], dict):
            with pytest.raises(Fault, match="UNAUTHENTICATED"):
                await auth.current(results[0]["token"])
        else:
            assert isinstance(results[0], Fault)
        fresh = await auth.login("alice", "integration-password-final", "local")
        assert len((await works.list(a, 100))["items"]) == 7
        attempts = await asyncio.gather(
            *(auth.login("absent", "wrong", "source") for _ in range(8)), return_exceptions=True
        )
        assert (
            sum(isinstance(e, Fault) and e.code == "AUTHENTICATION_FAILED" for e in attempts) == 5
        )
        assert sum(isinstance(e, Fault) and e.code == "RATE_LIMITED" for e in attempts) == 3
        renewed = Accounts(PostgresStore(store.url), Passwords(), lambda: now)
        with pytest.raises(Fault, match="RATE_LIMITED"):
            await renewed.login("absent", "wrong", "source")
        now += timedelta(minutes=15)
        with pytest.raises(Fault, match="AUTHENTICATION_FAILED"):
            await renewed.login("absent", "wrong", "source")
        now += timedelta(days=7)
        with pytest.raises(Fault, match="UNAUTHENTICATED"):
            await auth.current(fresh["token"])
        async with store.transaction() as db:
            stored = await db.user("alice")
            assert stored["password_hash"].startswith("$argon2id$")
            assert await db.session(digest(first["token"])) is None

    asyncio.run(scenario())
