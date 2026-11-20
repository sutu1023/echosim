"""项目与历史接口的鉴权、归属与校验行为测试。"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db import get_db
from app.deps import require_user
from app.errors import ProjectNotFound
from app.main import create_app
from app.services.history import HistoryEvent
from app.services.projects import Project
from app.services.sessions import SessionUser

USER = SessionUser(id=uuid.uuid4(), email="a@example.com", display_name="A")
OTHER = SessionUser(id=uuid.uuid4(), email="b@example.com", display_name="B")
PROJECT = Project(
    id=uuid.uuid4(),
    name="城市韧性推演",
    description="描述",
    created_at=datetime.now(timezone.utc),
)


def build_client(*, authenticated: bool = True) -> TestClient:
    app = create_app()

    async def fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = fake_db
    if authenticated:
        app.dependency_overrides[require_user] = lambda: USER
    return TestClient(app)


class AnonymousAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = build_client(authenticated=False)

    def test_private_endpoints_are_401_without_database_access(self) -> None:
        with patch("app.deps.load_session", new=AsyncMock()) as loader:
            responses = [
                self.client.get("/api/projects"),
                self.client.get(f"/api/projects/{PROJECT.id}"),
                self.client.get("/api/projects/not-a-uuid"),
                self.client.get("/api/history"),
                self.client.post("/api/projects", json={"name": "x"}),
            ]

        loader.assert_not_awaited()
        for response in responses:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": "未登录"})


class ProjectRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = build_client()

    def test_list_returns_only_service_result(self) -> None:
        with patch(
            "app.routers.projects.projects.list_owned_projects",
            new=AsyncMock(return_value=[PROJECT]),
        ) as service:
            response = self.client.get("/api/projects")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["projects"][0]["name"], PROJECT.name)
        service.assert_awaited_once()
        self.assertEqual(service.await_args.kwargs["owner_id"], USER.id)

    def test_create_returns_201_and_normalized_payload(self) -> None:
        with patch(
            "app.routers.projects.projects.create_project",
            new=AsyncMock(return_value=PROJECT),
        ) as service:
            response = self.client.post(
                "/api/projects",
                json={"name": "  城市韧性推演  ", "description": "  描述  "},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["project"]["name"], PROJECT.name)
        self.assertEqual(service.await_args.kwargs["name"], "城市韧性推演")
        self.assertEqual(service.await_args.kwargs["description"], "描述")

    def test_invalid_project_payload_is_422_and_service_not_called(self) -> None:
        payloads = [
            {"name": ""},
            {"name": "   "},
            {"name": "x" * 81},
            {"name": "ok", "description": "x" * 501},
            {"name": 123},
        ]
        with patch(
            "app.routers.projects.projects.create_project", new=AsyncMock()
        ) as service:
            for payload in payloads:
                response = self.client.post("/api/projects", json=payload)
                self.assertEqual(response.status_code, 422, payload)

        service.assert_not_awaited()

    def test_owner_can_read_detail(self) -> None:
        with patch(
            "app.routers.projects.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ):
            response = self.client.get(f"/api/projects/{PROJECT.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project"]["id"], str(PROJECT.id))

    def test_foreign_nonexistent_and_invalid_ids_are_identical_404(self) -> None:
        with patch(
            "app.routers.projects.projects.resolve_owned_project",
            new=AsyncMock(side_effect=ProjectNotFound()),
        ):
            foreign = self.client.get(f"/api/projects/{PROJECT.id}")
            missing = self.client.get(f"/api/projects/{uuid.uuid4()}")
            invalid = self.client.get("/api/projects/not-a-uuid")

        for response in (foreign, missing, invalid):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {"detail": "项目不存在"})
        self.assertEqual(foreign.content, missing.content)
        self.assertEqual(foreign.content, invalid.content)

    def test_cross_origin_write_is_403_and_does_not_reach_service(self) -> None:
        with patch(
            "app.routers.projects.projects.create_project", new=AsyncMock()
        ) as service:
            response = self.client.post(
                "/api/projects",
                json={"name": "ok"},
                headers={"Origin": "https://evil.example"},
            )

        self.assertEqual(response.status_code, 403)
        service.assert_not_awaited()

    def test_same_origin_write_is_allowed(self) -> None:
        with patch(
            "app.routers.projects.projects.create_project",
            new=AsyncMock(return_value=PROJECT),
        ):
            response = self.client.post(
                "/api/projects",
                json={"name": "ok"},
                headers={"Origin": "http://testserver"},
            )

        self.assertEqual(response.status_code, 201)


class HistoryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = build_client()

    def test_history_returns_owned_events(self) -> None:
        event = HistoryEvent(
            id=uuid.uuid4(),
            project_id=PROJECT.id,
            project_name=PROJECT.name,
            type="project.created",
            occurred_at=datetime.now(timezone.utc),
        )
        with patch(
            "app.routers.history.history.list_owned_history",
            new=AsyncMock(return_value=[event]),
        ) as service:
            response = self.client.get("/api/history")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"][0]["project_name"], PROJECT.name)
        self.assertEqual(service.await_args.kwargs["owner_id"], USER.id)


if __name__ == "__main__":
    unittest.main()
