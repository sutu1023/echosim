"""服务端会话生命周期与错误分类的单元测试。"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.exc import OperationalError

from app.errors import ServiceUnavailable, Unauthenticated
from app.services.sessions import (
    SessionUser,
    hash_token,
    load_session,
    new_token,
)


class FakeResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def first(self) -> object | None:
        return self._row


class FakeDB:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.queries: list[tuple[str, dict[str, object] | None]] = []
        self.commits = 0

    async def execute(self, clause: object, params: dict[str, object] | None = None):
        self.queries.append((str(clause), params))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def session_row(expires_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=uuid.uuid4(),
        expires_at=expires_at,
        id=uuid.uuid4(),
        email="a@example.com",
        display_name="A",
    )


class TokenTests(unittest.TestCase):
    def test_tokens_are_unique_and_hash_is_stable(self) -> None:
        first, second = new_token(), new_token()

        self.assertNotEqual(first, second)
        self.assertEqual(hash_token(first), hash_token(first))
        self.assertNotEqual(hash_token(first), hash_token(second))
        self.assertNotIn(first, hash_token(first))


class LoadSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_token_is_rejected_without_query(self) -> None:
        db = FakeDB([])

        with self.assertRaises(Unauthenticated) as caught:
            await load_session(db, "short")

        self.assertTrue(caught.exception.clear_cookie)
        self.assertEqual(db.queries, [])

    async def test_unknown_token_is_rejected(self) -> None:
        db = FakeDB([FakeResult(None)])

        with self.assertRaises(Unauthenticated) as caught:
            await load_session(db, "a" * 43)

        self.assertTrue(caught.exception.clear_cookie)

    async def test_expired_session_is_deleted_and_rejected(self) -> None:
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        db = FakeDB([FakeResult(session_row(expired)), FakeResult(None)])

        with self.assertRaises(Unauthenticated):
            await load_session(db, "a" * 43)

        self.assertEqual(len(db.queries), 2)
        self.assertIn("DELETE FROM sessions", db.queries[1][0])
        self.assertEqual(db.commits, 1)

    async def test_database_error_becomes_service_unavailable(self) -> None:
        db = FakeDB([OperationalError("SELECT 1", {}, Exception("down"))])

        with self.assertRaises(ServiceUnavailable):
            await load_session(db, "a" * 43)

    async def test_valid_session_returns_user(self) -> None:
        row = session_row(datetime.now(timezone.utc) + timedelta(days=1))
        db = FakeDB([FakeResult(row)])

        user = await load_session(db, "a" * 43)

        self.assertEqual(
            user,
            SessionUser(id=row.id, email="a@example.com", display_name="A"),
        )
        self.assertEqual(user.email, "a@example.com")

    async def test_commit_failure_during_expiry_cleanup_is_503(self) -> None:
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        db = FakeDB([FakeResult(session_row(expired)), FakeResult(None)])
        db.commit = AsyncMock(side_effect=OperationalError("DELETE", {}, Exception("down")))

        with self.assertRaises(ServiceUnavailable):
            await load_session(db, "a" * 43)


if __name__ == "__main__":
    unittest.main()
