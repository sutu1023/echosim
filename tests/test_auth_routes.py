"""鉴权路由与 Cookie 属性的行为测试。"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db import get_db
from app.errors import InvalidCredentials, ServiceUnavailable, Unauthenticated
from app.main import create_app
from app.services.accounts import Account
from app.services.sessions import SessionUser

TOKEN = "t" * 43
ACCOUNT = Account(id=uuid.uuid4(), email="a@example.com", display_name="A")
USER = SessionUser(id=ACCOUNT.id, email=ACCOUNT.email, display_name="A")


def build_client() -> TestClient:
    app = create_app()

    async def fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = fake_db
    return TestClient(app)


class LoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = build_client()

    def test_login_sets_browser_session_cookie(self) -> None:
        with (
            patch(
                "app.services.accounts.authenticate",
                new=AsyncMock(return_value=ACCOUNT),
            ),
            patch(
                "app.services.sessions.create_session",
                new=AsyncMock(return_value=TOKEN),
            ),
        ):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "a@example.com", "password": "dev-password-123"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["email"], "a@example.com")
        cookie = response.headers["set-cookie"]
        self.assertIn(f"echosim_session={TOKEN}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Max-Age", cookie)
        self.assertNotIn("Expires", cookie)

    def test_invalid_credentials_returns_401_without_cookie(self) -> None:
        with patch(
            "app.services.accounts.authenticate",
            new=AsyncMock(side_effect=InvalidCredentials()),
        ):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "a@example.com", "password": "wrong-password"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "邮箱或密码不正确"})
        self.assertNotIn("set-cookie", response.headers)

    def test_malformed_login_payload_returns_422(self) -> None:
        response = self.client.post(
            "/api/auth/login", json={"email": "not-an-email", "password": ""}
        )

        self.assertEqual(response.status_code, 422)

    def test_login_when_database_is_down_returns_503_without_cookie(self) -> None:
        with patch(
            "app.services.accounts.authenticate",
            new=AsyncMock(side_effect=ServiceUnavailable()),
        ):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "a@example.com", "password": "dev-password-123"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "服务暂时不可用"})
        self.assertNotIn("set-cookie", response.headers)


class SessionAndLogoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = build_client()

    def test_anonymous_session_query_is_401_without_load(self) -> None:
        with patch("app.deps.load_session", new=AsyncMock()) as loader:
            response = self.client.get("/api/auth/session")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "未登录"})
        loader.assert_not_awaited()

    def test_invalid_cookie_is_401_and_cleared(self) -> None:
        self.client.cookies.set("echosim_session", TOKEN)
        with patch(
            "app.deps.load_session",
            new=AsyncMock(side_effect=Unauthenticated(clear_cookie=True)),
        ):
            response = self.client.get("/api/auth/session")

        self.assertEqual(response.status_code, 401)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])

    def test_database_down_with_cookie_is_503_and_keeps_cookie(self) -> None:
        self.client.cookies.set("echosim_session", TOKEN)
        with patch(
            "app.deps.load_session",
            new=AsyncMock(side_effect=ServiceUnavailable()),
        ):
            response = self.client.get("/api/auth/session")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "服务暂时不可用"})
        self.assertNotIn("set-cookie", response.headers)

    def test_session_query_returns_current_user(self) -> None:
        self.client.cookies.set("echosim_session", TOKEN)
        with patch(
            "app.deps.load_session", new=AsyncMock(return_value=USER)
        ):
            response = self.client.get("/api/auth/session")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["id"], str(USER.id))

    def test_logout_revokes_and_clears_cookie(self) -> None:
        self.client.cookies.set("echosim_session", TOKEN)
        with patch(
            "app.routers.auth.sessions.revoke_session", new=AsyncMock()
        ) as revoke:
            response = self.client.post("/api/auth/logout")

        self.assertEqual(response.status_code, 204)
        revoke.assert_awaited_once()
        self.assertIn("Max-Age=0", response.headers["set-cookie"])

    def test_logout_without_cookie_is_idempotent(self) -> None:
        response = self.client.post("/api/auth/logout")

        self.assertEqual(response.status_code, 204)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])

    def test_logout_when_database_is_down_returns_503(self) -> None:
        self.client.cookies.set("echosim_session", TOKEN)
        with patch(
            "app.routers.auth.sessions.revoke_session",
            new=AsyncMock(side_effect=ServiceUnavailable()),
        ):
            response = self.client.post("/api/auth/logout")

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("set-cookie", response.headers)


if __name__ == "__main__":
    unittest.main()
