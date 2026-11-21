"""任务路由的鉴权、归属、游标与 JSON 交叉核对的接口测试。"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db import get_db
from app.deps import require_user
from app.errors import JobNotFound, ProjectNotFound
from app.main import create_app
from app.services import jobs
from app.services.projects import Project
from app.services.sessions import SessionUser

USER = SessionUser(id=uuid.uuid4(), email="a@example.com", display_name="A")
PROJECT = Project(
    id=uuid.uuid4(),
    name="城市韧性推演",
    description="描述",
    created_at=datetime.now(timezone.utc),
)
JOB = jobs.Job(
    id=uuid.uuid4(),
    run_id=uuid.uuid4(),
    project_id=PROJECT.id,
    status="running",
    next_step=4,
    attempts=1,
    total_steps=10,
    stage="dual-platform-run",
    lease_expires_at=datetime.now(timezone.utc),
    terminal_reason=None,
    created_at=datetime.now(timezone.utc),
    started_at=datetime.now(timezone.utc),
    finished_at=None,
    cancel_requested_at=None,
    awaiting_takeover=False,
    lease_expired_seconds=0.0,
)
BASE = f"/api/projects/{PROJECT.id}/jobs"


def build_client(*, authenticated: bool = True) -> TestClient:
    app = create_app()

    async def fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = fake_db
    if authenticated:
        app.dependency_overrides[require_user] = lambda: USER
    return TestClient(app)


class AnonymousJobAccessTests(unittest.TestCase):
    def test_all_job_endpoints_are_401_without_database_access(self) -> None:
        client = build_client(authenticated=False)
        paths = [
            ("POST", BASE, {"operation": "test-simulation", "stage": "report"}),
            ("GET", BASE, None),
            ("GET", f"{BASE}/{JOB.id}", None),
            ("POST", f"{BASE}/{JOB.id}/cancel", None),
            ("GET", f"{BASE}/{JOB.id}/effects", None),
            ("GET", f"{BASE}/{JOB.id}/events", None),
        ]
        with patch("app.deps.load_session", new=AsyncMock()) as loader:
            responses = [
                client.request(method, path, json=payload)
                for method, path, payload in paths
            ]

        loader.assert_not_awaited()
        for response in responses:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": "未登录"})
            self.assertNotIn("text/event-stream", response.headers.get("content-type", ""))


class JobAccessBoundaryTests(unittest.TestCase):
    def test_other_project_is_indistinguishable_from_missing(self) -> None:
        client = build_client()
        with patch(
            "app.routers.jobs.projects.resolve_owned_project",
            new=AsyncMock(side_effect=ProjectNotFound),
        ):
            responses = [
                client.get(f"/api/projects/{uuid.uuid4()}/jobs"),
                client.get(f"/api/projects/{uuid.uuid4()}/jobs/{JOB.id}"),
                client.get(f"/api/projects/{uuid.uuid4()}/jobs/{JOB.id}/events"),
            ]

        bodies = {response.text for response in responses}
        self.assertEqual([r.status_code for r in responses], [404, 404, 404])
        self.assertEqual(bodies, {'{"detail":"项目不存在"}'})

    def test_unknown_job_id_maps_to_job_not_found(self) -> None:
        client = build_client()
        with patch(
            "app.routers.jobs.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.jobs.job_service.get_job",
            new=AsyncMock(side_effect=JobNotFound),
        ):
            response = client.get(f"{BASE}/not-a-uuid")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "任务不存在"})

    def test_invalid_start_payload_is_422(self) -> None:
        client = build_client()
        payloads = [
            {"operation": "real-simulation", "stage": "report"},
            {"operation": "test-simulation", "stage": "unknown-stage"},
            {"operation": "test-simulation", "stage": "report", "failAtStep": 0},
            {"operation": "test-simulation", "stage": "report", "failAtStep": 11},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = client.post(BASE, json=payload)
                self.assertEqual(response.status_code, 422)


class CursorAndJsonHistoryTests(unittest.TestCase):
    def test_cursor_beyond_max_is_400_json(self) -> None:
        client = build_client()
        with patch(
            "app.routers.jobs.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.jobs.job_service.get_job",
            new=AsyncMock(return_value=JOB),
        ), patch(
            "app.routers.jobs.run_events.max_seq",
            new=AsyncMock(return_value=4),
        ):
            response = client.get(f"{BASE}/{JOB.id}/events?after=5")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "游标无效"})
        self.assertNotIn("text/event-stream", response.headers.get("content-type", ""))

    def test_json_history_uses_same_cursor_contract(self) -> None:
        client = build_client()
        with patch(
            "app.routers.jobs.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.jobs.job_service.get_job",
            new=AsyncMock(return_value=JOB),
        ), patch(
            "app.routers.jobs.run_events.max_seq",
            new=AsyncMock(return_value=4),
        ), patch(
            "app.routers.jobs.run_events.list_events_after",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.routers.jobs.run_events.terminal_seq",
            new=AsyncMock(return_value=None),
        ):
            response = client.get(f"{BASE}/{JOB.id}/events?format=json&after=4")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["runId"], str(JOB.run_id))
        self.assertEqual(body["after"], 4)
        self.assertEqual(body["lastSeq"], 4)
        self.assertEqual(body["events"], [])


if __name__ == "__main__":
    unittest.main()
