import asyncio
import hashlib
import math
import re
import secrets
from datetime import UTC, datetime, timedelta

from echosim.domain.accounts import Fault, PasswordHasher, Store


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def username(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", value):
        raise Fault("INVALID_REQUEST")
    return value.lower()


class Accounts:
    def __init__(self, store: Store, passwords: PasswordHasher, clock=None):
        self.store, self.passwords = store, passwords
        self.clock = clock or (lambda: datetime.now(UTC))
        self.dummy = passwords.hash(secrets.token_urlsafe(24))

    async def provision(self, name, password, *, reset=False, preserve=False):
        name = username(name)
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise Fault("INVALID_REQUEST")
        encoded = await asyncio.to_thread(self.passwords.hash, password)
        async with self.store.transaction() as db:
            await db.lock("account:" + name)
            user = await db.user(name)
            if user and not reset:
                if preserve:
                    return
                raise Fault("ACCOUNT_EXISTS", 409)
            if reset and not user:
                raise Fault("NOT_FOUND", 404)
            await db.save_user(name, encoded, self.clock())
            if user:
                await db.revoke_user(user["id"])

    async def login(self, name, password, source):
        name = username(name)
        now = self.clock()
        failure = None
        async with self.store.transaction() as db:
            # Deterministic locks serialize attempts even for nonexistent accounts.
            keys = [("name:" + name, 5), ("source:" + source, 30)]
            for key, _ in sorted(keys):
                await db.lock("rate:" + key)
            for key, maximum in keys:
                row = await db.attempt(key)
                if row and row["until"] > now and row["count"] >= maximum:
                    raise Fault(
                        "RATE_LIMITED", 429, math.ceil((row["until"] - now).total_seconds())
                    )
            await db.lock("account:" + name)
            user = await db.user(name)
            valid = await asyncio.to_thread(
                self.passwords.verify, user["password_hash"] if user else self.dummy, password
            )
            if not user or not valid:
                for key, _ in keys:
                    await db.fail_attempt(key, now, now + timedelta(minutes=15))
                failure = Fault("AUTHENTICATION_FAILED", 401)
            else:
                result = await self.issue(db, user, now)
        if failure:
            raise failure  # Commit failed-attempt counters before returning the error.
        return result

    async def issue(self, db, user, now):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        await db.save_session(digest(token), user["id"], csrf, now, now + timedelta(days=7))
        return {"token": token, "csrf": csrf, "username": user["username"]}

    async def current(self, token):
        if not token or len(token) > 256:
            raise Fault("UNAUTHENTICATED", 401)
        async with self.store.transaction() as db:
            row = await db.session(digest(token))
        if not row or row["expires_at"] <= self.clock():
            raise Fault("UNAUTHENTICATED", 401)
        return row

    async def logout(self, token):
        async with self.store.transaction() as db:
            await db.revoke(digest(token or ""))

    async def quick(self, target, old_token):
        if target not in ("A", "B"):
            raise Fault("INVALID_REQUEST")
        name = "experience-" + target.lower()
        async with self.store.transaction() as db:
            await db.lock("account:" + name)
            user = await db.user(name)
            if not user:
                raise Fault("DEMO_UNAVAILABLE", 503)
            result = await self.issue(db, user, self.clock())
            await db.revoke(digest(old_token or ""))
            return result
