import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from memory_store import MemoryStore, Passwords

from echosim.domain.accounts import Fault
from echosim.services.accounts import Accounts
from echosim.services.works import Works


def run(coro):
    return asyncio.run(coro)


def test_account_reset_sessions_and_expiry():
    async def scenario():
        store = MemoryStore()
        now = datetime(2026, 11, 13, tzinfo=UTC)
        auth = Accounts(store, Passwords(), lambda: now)
        await auth.provision("alice", "long-password-1")
        with pytest.raises(Fault, match="ACCOUNT_EXISTS"):
            await auth.provision("Alice", "long-password-2")
        one = await auth.login("alice", "long-password-1", "local")
        two = await auth.login("alice", "long-password-1", "local")
        assert (await auth.current(one["token"]))["username"] == "alice"
        await auth.logout(one["token"])
        with pytest.raises(Fault, match="UNAUTHENTICATED"):
            await auth.current(one["token"])
        await auth.current(two["token"])
        await auth.provision("alice", "long-password-2", reset=True)
        with pytest.raises(Fault, match="UNAUTHENTICATED"):
            await auth.current(two["token"])
        with pytest.raises(Fault, match="AUTHENTICATION_FAILED"):
            await auth.login("alice", "long-password-1", "local")
        fresh = await auth.login("alice", "long-password-2", "local")
        now += timedelta(days=7)
        with pytest.raises(Fault, match="UNAUTHENTICATED"):
            await auth.current(fresh["token"])

    run(scenario())


def test_failures_rate_limit_recovery():
    async def scenario():
        now = datetime(2026, 11, 13, tzinfo=UTC)
        auth = Accounts(MemoryStore(), Passwords(), lambda: now)
        await auth.provision("alice", "long-password-1")
        for name in ["alice", "missing"]:
            for _ in range(5):
                with pytest.raises(Fault, match="AUTHENTICATION_FAILED"):
                    await auth.login(name, "wrong", "source")
            with pytest.raises(Fault, match="RATE_LIMITED") as exc:
                await auth.login(name, "wrong", "source")
            assert exc.value.retry_after > 0
        now += timedelta(minutes=15)
        assert (await auth.login("alice", "long-password-1", "source"))["token"]

    run(scenario())


def test_work_validation_isolation_and_idempotency():
    async def scenario():
        store = MemoryStore()
        works = Works(store)
        a, b = str(uuid4()), str(uuid4())
        key = str(uuid4())
        first = await works.create(a, " 春山 ", key)
        assert first["title"] == "春山"
        assert await works.create(a, "春山", key) == first
        with pytest.raises(Fault, match="IDEMPOTENCY_CONFLICT"):
            await works.create(a, "秋水", key)
        other = await works.create(b, "春山", key)
        assert other["id"] != first["id"]
        assert len((await works.list(a, 20, None))["items"]) == 1
        for identifier in [other["id"], str(uuid4()), "unknown"]:
            with pytest.raises(Fault, match="NOT_FOUND"):
                await works.get(a, identifier)
        for title in ["", "  ", "x" * 101, 123]:
            with pytest.raises(Fault, match="INVALID_REQUEST"):
                await works.create(a, title, str(uuid4()))
        for title in ["x", "😀" * 100]:
            await works.create(a, title, str(uuid4()))
        with pytest.raises(Fault, match="INVALID_REQUEST"):
            await works.create(a, "ok", "bad-key")
        with pytest.raises(Fault, match="INVALID_REQUEST"):
            await works.list(a, 101, None)
        with pytest.raises(Fault, match="INVALID_REQUEST"):
            await works.list(a, 20, "broken")

    run(scenario())


def test_quick_switch_and_missing_account():
    async def scenario():
        auth = Accounts(MemoryStore(), Passwords())
        await auth.provision("experience-a", "long-password-1")
        first = await auth.quick("A", None)
        with pytest.raises(Fault, match="DEMO_UNAVAILABLE"):
            await auth.quick("B", first["token"])
        await auth.current(first["token"])
        await auth.provision("experience-b", "long-password-2")
        await auth.quick("B", first["token"])
        with pytest.raises(Fault, match="UNAUTHENTICATED"):
            await auth.current(first["token"])

    run(scenario())


@pytest.mark.parametrize(
    "name,password",
    [
        ("ab", "long-password-1"),
        ("a" * 65, "long-password-1"),
        ("valid", "short"),
        ("valid", "x" * 129),
    ],
)
def test_provision_invalid_input(name, password):
    with pytest.raises(Fault, match="INVALID_REQUEST"):
        run(Accounts(MemoryStore(), Passwords()).provision(name, password))


def test_password_hash_is_not_plaintext():
    from echosim.adapters.passwords import Passwords as ArgonPasswords

    passwords = ArgonPasswords()
    encoded = passwords.hash("unique-password-marker")
    assert encoded.startswith("$argon2id$")
    assert "unique-password-marker" not in encoded
    assert passwords.verify(encoded, "unique-password-marker")
    assert not passwords.verify(encoded, "wrong")


def test_source_limit_applies_across_usernames():
    async def scenario():
        auth = Accounts(MemoryStore(), Passwords())
        for i in range(30):
            with pytest.raises(Fault, match="AUTHENTICATION_FAILED"):
                await auth.login("absent-" + str(i), "wrong", "shared-source")
        with pytest.raises(Fault, match="RATE_LIMITED"):
            await auth.login("new-name", "wrong", "shared-source")
        with pytest.raises(Fault, match="AUTHENTICATION_FAILED"):
            await auth.login("new-name", "wrong", "independent-source")

    run(scenario())
