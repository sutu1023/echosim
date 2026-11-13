from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4


class Passwords:
    def hash(self, password):
        return "test-hash:" + password

    def verify(self, encoded, password):
        return encoded == self.hash(password)


class MemoryStore:
    def __init__(self):
        self.users, self.sessions, self.attempts, self.works, self.requests = {}, {}, {}, {}, {}

    @asynccontextmanager
    async def transaction(self):
        before = deepcopy(self.__dict__)
        try:
            yield self
        except Exception:
            self.__dict__ = before
            raise

    async def lock(self, key):
        pass

    async def user(self, name):
        return self.users.get(name)

    async def save_user(self, name, encoded, now):
        self.users[name] = {
            **self.users.get(name, {"id": str(uuid4())}),
            "username": name,
            "password_hash": encoded,
        }

    async def revoke_user(self, owner):
        self.sessions = {k: v for k, v in self.sessions.items() if v["user_id"] != owner}

    async def save_session(self, token, owner, csrf, now, expiry):
        user = next(u for u in self.users.values() if u["id"] == owner)
        self.sessions[token] = {
            "user_id": owner,
            "csrf": csrf,
            "expires_at": expiry,
            "username": user["username"],
        }

    async def session(self, token):
        return self.sessions.get(token)

    async def revoke(self, token):
        self.sessions.pop(token, None)

    async def attempt(self, key):
        return self.attempts.get(key)

    async def fail_attempt(self, key, now, until):
        previous = self.attempts.get(key)
        self.attempts[key] = (
            {"count": previous["count"] + 1, "until": previous["until"]}
            if previous and previous["until"] > now
            else {"count": 1, "until": until}
        )

    async def created_work(self, owner, key):
        work_id = self.requests.get((owner, key))
        return await self.work(owner, work_id)

    async def create_work(self, owner, title, key):
        now, work_id = datetime.now(UTC), str(uuid4())
        self.works[(owner, work_id)] = {
            "id": work_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }
        self.requests[(owner, key)] = work_id
        return self.works[(owner, work_id)]

    async def work(self, owner, work_id):
        return self.works.get((owner, work_id))

    async def list_works(self, owner, limit, position):
        rows = [
            r
            for (u, _), r in self.works.items()
            if u == owner and (not position or (r["created_at"], r["id"]) < position)
        ]
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)[:limit]
