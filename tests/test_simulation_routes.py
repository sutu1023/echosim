"""双平台运行路由：匿名 401、归属 404、校验 422、缺失 409 与停止幂等。"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db import get_db
from app.deps import require_user
from app.errors import (
    ProjectNotFound,
    ServiceUnavailable,
    SimulationConflict,
    SimulationNotFound,
)
from app.main import create_app
from app.services.projects import Project
from app.services.sessions import SessionUser

USER = SessionUser(id=uuid.uuid4(), email="a@example.com", display_name="A")
PROJECT = Project(
    id=uuid.uuid4(),
    name="原创项目",
    description="描述",
    created_at=datetime.now(timezone.utc),
)
BASE = f"/api/projects/{PROJECT.id}/simulations"
RUN = uuid.uuid4()


def build_client(*, authenticated: bool = True) -> TestClient:
    app = create_app()

    async def fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = fake_db
    if authenticated:
        app.dependency_overrides[require_user] = lambda: USER
    return TestClient(app)


def simulation_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "runId": str(RUN),
        "jobId": str(uuid.uuid4()),
        "selectionId": str(uuid.uuid4()),
        "preparationId": str(uuid.uuid4()),
        "buildId": str(uuid.uuid4()),
        "snapshotId": str(uuid.uuid4()),
        "question": "原创问题",
        "engine": "double",
        "phase": "running",
        "terminal": False,
        "partial": False,
        "failedPlatforms": [],
        "failure": None,
        "rounds": 3,
        "minutesPerRound": 60,
        "durationHours": 3,
        "simulatedStart": "2026-11-24T00:00:00Z",
        "simulatedAt": "2026-11-24T01:00:00Z",
        "platforms": [],
        "counts": {"total": 0, "byAction": {}},
        "actions": {"items": [], "nextAfter": 0},
        "canStop": True,
        "createdAt": "2026-11-24T00:00:00Z",
        "updatedAt": "2026-11-24T00:00:00Z",
        "finishedAt": None,
    }
    body.update(overrides)
    return body


class AnonymousSimulationTests(unittest.TestCase):
    def test_all_endpoints_are_401_without_database_access(self) -> None:
        client = build_client(authenticated=False)
        paths = [
            ("POST", BASE, {"selectionId": str(uuid.uuid4())}),
            ("GET", BASE, None),
            ("GET", f"{BASE}/{RUN}", None),
            ("GET", f"{BASE}/{RUN}/actions", None),
            ("GET", f"{BASE}/{RUN}/memory?round=1", None),
            ("POST", f"{BASE}/{RUN}/memory/retry", None),
            ("POST", f"{BASE}/{RUN}/stop", None),
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


class SimulationBoundaryTests(unittest.TestCase):
    def test_other_project_is_404_for_all_endpoints(self) -> None:
        client = build_client()
        with patch(
            "app.routers.simulations.projects.resolve_owned_project",
            new=AsyncMock(side_effect=ProjectNotFound),
        ):
            responses = [
                client.post(BASE, json={"selectionId": str(uuid.uuid4())}),
                client.get(BASE),
                client.get(f"{BASE}/{RUN}"),
                client.get(f"{BASE}/{RUN}/actions"),
                client.get(f"{BASE}/{RUN}/memory?round=1"),
                client.post(f"{BASE}/{RUN}/memory/retry"),
                client.post(f"{BASE}/{RUN}/stop"),
            ]
        self.assertEqual([item.status_code for item in responses], [404] * 7)
        self.assertEqual(
            {item.text for item in responses}, {'{"detail":"项目不存在"}'
            }
        )

    def test_missing_selection_is_409_and_invalid_uuid_is_422(self) -> None:
        client = build_client()
        with patch(
            "app.routers.simulations.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.simulations.simulation_service.start_simulation",
            new=AsyncMock(
                side_effect=SimulationConflict(
                    "selection_missing", "缺少已确认的冻结选择"
                )
            ),
        ):
            response = client.post(BASE, json={"selectionId": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "selection_missing")

        self.assertEqual(
            client.post(BASE, json={"selectionId": "not-a-uuid"}).status_code,
            422,
        )

    def test_unknown_run_is_404(self) -> None:
        client = build_client()
        with patch(
            "app.routers.simulations.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.simulations.simulation_service.get_simulation",
            new=AsyncMock(side_effect=SimulationNotFound),
        ):
            response = client.get(f"{BASE}/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "运行不存在"})

    def test_stop_is_idempotent(self) -> None:
        client = build_client()
        with patch(
            "app.routers.simulations.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.simulations.simulation_service.stop_simulation",
            new=AsyncMock(
                side_effect=[
                    (
                        type("S", (), {"run_id": RUN, "id": uuid.uuid4()})(),
                        True,
                    ),
                    (
                        type("S", (), {"run_id": RUN, "id": uuid.uuid4()})(),
                        False,
                    ),
                ]
            ),
        ), patch(
            "app.routers.simulations.simulation_service.serialize_simulation",
            new=AsyncMock(return_value=simulation_body()),
        ):
            first = client.post(f"{BASE}/{RUN}/stop")
            second = client.post(f"{BASE}/{RUN}/stop")
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["cancelApplied"])
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["cancelApplied"])

    def test_service_unavailable_keeps_session_cookie(self) -> None:
        client = build_client()
        with patch(
            "app.routers.simulations.projects.resolve_owned_project",
            new=AsyncMock(side_effect=ServiceUnavailable),
        ):
            response = client.get(
                f"{BASE}/{RUN}", cookies={"echosim_session": "token"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("echosim_session", response.headers.get("set-cookie", ""))

    def test_negative_cursor_is_400(self) -> None:
        client = build_client()
        with patch(
            "app.routers.simulations.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.simulations.simulation_service.get_simulation",
            new=AsyncMock(return_value=type("S", (), {"run_id": RUN, "id": uuid.uuid4()})()),
        ):
            response = client.get(f"{BASE}/{RUN}/actions?after=-1")
        self.assertEqual(response.status_code, 400)


class MemoryRouteTests(unittest.TestCase):
    def test_memory_view_and_invalid_round(self) -> None:
        client = build_client()
        simulation = type("S", (), {"run_id": RUN, "id": uuid.uuid4()})()
        view = {
            "runId": str(RUN),
            "round": 1,
            "platform": "platform_b",
            "status": {"status": "complete"},
            "material": {"snapshotId": "s", "nodes": [], "edges": []},
            "runtime": {"nodes": [], "edges": []},
            "entries": [],
        }
        with patch(
            "app.routers.simulations.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.simulations.simulation_service.get_simulation",
            new=AsyncMock(return_value=simulation),
        ), patch(
            "app.routers.simulations.memory_service.memory_view",
            new=AsyncMock(return_value=view),
        ):
            ok = client.get(f"{BASE}/{RUN}/memory?round=1&platform=platform_b")
            invalid = client.get(f"{BASE}/{RUN}/memory?round=0")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["round"], 1)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["code"], "round_invalid")

    def test_memory_retry_returns_202_then_200(self) -> None:
        client = build_client()
        simulation = type("S", (), {"run_id": RUN, "id": uuid.uuid4()})()
        job_id = uuid.uuid4()
        with patch(
            "app.routers.simulations.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.simulations.simulation_service.get_simulation",
            new=AsyncMock(return_value=simulation),
        ), patch(
            "app.routers.simulations.memory_service.retry_memory_sync",
            new=AsyncMock(side_effect=[(job_id, True), (job_id, False)]),
        ):
            created = client.post(f"{BASE}/{RUN}/memory/retry")
            reused = client.post(f"{BASE}/{RUN}/memory/retry")
        self.assertEqual(created.status_code, 202)
        self.assertTrue(created.json()["created"])
        self.assertEqual(reused.status_code, 200)
        self.assertFalse(reused.json()["created"])
        self.assertEqual(created.json()["memoryJobId"], str(job_id))
