"""`/health` 与数据库探测的行为测试。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import create_app

LEAK_MARKERS = (
    "supersecret",
    "postgresql",
    "password",
    "asyncpg",
    "db:5432",
    "localhost",
)


class HealthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())

    def test_health_returns_200_when_database_is_available(self) -> None:
        probe = AsyncMock(return_value=None)
        with patch("app.health.select_one", new=probe):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})
        probe.assert_awaited_once()

    def test_health_returns_503_when_database_is_unavailable(self) -> None:
        probe = AsyncMock(
            side_effect=RuntimeError(
                "connect failed dsn=postgresql+asyncpg://echosim:supersecret@db:5432/echosim"
            )
        )
        with patch("app.health.select_one", new=probe):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"status": "degraded", "database": "unavailable"}
        )

    def test_health_response_never_leaks_connection_details(self) -> None:
        probe = AsyncMock(
            side_effect=RuntimeError(
                "auth failed for postgresql+asyncpg://echosim:supersecret@db:5432/echosim"
            )
        )
        with patch("app.health.select_one", new=probe):
            response = self.client.get("/health")

        body = response.text.lower()
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, body)

    def test_each_request_probes_database_again(self) -> None:
        probe = AsyncMock(return_value=None)
        with patch("app.health.select_one", new=probe):
            self.client.get("/health")
            self.client.get("/health")

        self.assertEqual(probe.await_count, 2)

    def test_livez_stays_200_while_health_is_degraded(self) -> None:
        probe = AsyncMock(side_effect=RuntimeError("database is down"))
        with patch("app.health.select_one", new=probe):
            livez = self.client.get("/livez")
            health = self.client.get("/health")

        self.assertEqual(livez.status_code, 200)
        self.assertEqual(livez.json(), {"status": "ok"})
        self.assertEqual(health.status_code, 503)
        # 存活探针不访问数据库：只有 /health 触发探测。
        probe.assert_awaited_once()

    def test_health_echoes_valid_request_id(self) -> None:
        probe = AsyncMock(return_value=None)
        with patch("app.health.select_one", new=probe):
            response = self.client.get("/health", headers={"X-Request-ID": "accept-123"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-request-id"], "accept-123")

    def test_health_generates_request_id_when_missing(self) -> None:
        probe = AsyncMock(return_value=None)
        with patch("app.health.select_one", new=probe):
            response = self.client.get("/health")

        request_id = response.headers["x-request-id"]
        self.assertRegex(request_id, r"^[0-9a-f]{32}$")

    def test_health_replaces_invalid_request_id(self) -> None:
        for invalid in ("bad id!!", "a" * 65, "id;rm -rf", ""):
            with self.subTest(invalid=invalid):
                probe = AsyncMock(return_value=None)
                with patch("app.health.select_one", new=probe):
                    response = self.client.get(
                        "/health", headers={"X-Request-ID": invalid}
                    )

                request_id = response.headers["x-request-id"]
                self.assertRegex(request_id, r"^[0-9a-f]{32}$")
                if invalid:
                    self.assertNotIn(invalid.strip(), request_id)

    def test_probe_failure_log_contains_request_id(self) -> None:
        probe = AsyncMock(side_effect=RuntimeError("database is down"))
        with patch("app.health.select_one", new=probe):
            with self.assertLogs("app.health", level="WARNING") as captured:
                response = self.client.get(
                    "/health", headers={"X-Request-ID": "req-42"}
                )

        self.assertEqual(response.status_code, 503)
        self.assertIn("request_id=req-42", "\n".join(captured.output))
        self.assertEqual(
            response.json(), {"status": "degraded", "database": "unavailable"}
        )



class _RecordingConnection:
    """记录执行语句的假连接，用于断言探测只读。"""

    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    async def __aenter__(self) -> "_RecordingConnection":
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def execute(self, statement: object) -> None:
        self._statements.append(str(statement))


class _RecordingEngine:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def connect(self) -> _RecordingConnection:
        return _RecordingConnection(self.statements)


class HealthMethodAndSideEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())

    def test_non_get_methods_return_405(self) -> None:
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)("/health")
                self.assertEqual(response.status_code, 405)

    def test_probe_only_runs_select_one(self) -> None:
        async def run_probe() -> list[str]:
            from app.db import select_one

            fake_engine = _RecordingEngine()
            with patch("app.db.engine", fake_engine):
                await select_one()
            return fake_engine.statements

        statements = asyncio.run(run_probe())

        self.assertEqual(statements, ["SELECT 1"])
        for statement in statements:
            self.assertEqual(statement.strip().upper(), "SELECT 1")

    def test_health_probe_uses_read_only_path(self) -> None:
        probe = AsyncMock(return_value=None)
        with patch("app.health.select_one", new=probe):
            self.client.get("/health")

        probe.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
