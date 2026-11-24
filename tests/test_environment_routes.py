"""角色与环境准备路由：鉴权、归属、状态码、版本与错误脱敏。"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db import get_db
from app.deps import require_user
from app.errors import (
    EnvironmentBuildNotFound,
    EnvironmentConflict,
    EnvironmentPreparationNotFound,
    ProjectNotFound,
    ServiceUnavailable,
)
from app.main import create_app
from app.services import environment as store
from app.services.projects import Project
from app.services.sessions import SessionUser

USER = SessionUser(id=uuid.uuid4(), email="a@example.com", display_name="A")
PROJECT = Project(
    id=uuid.uuid4(),
    name="原创项目",
    description="描述",
    created_at=datetime.now(timezone.utc),
)
BUILD = uuid.uuid4()
SNAPSHOT = uuid.uuid4()
NOW = datetime.now(timezone.utc)
PREP = store.EnvironmentPreparation(
    id=uuid.uuid4(),
    project_id=PROJECT.id,
    build_id=BUILD,
    snapshot_id=SNAPSHOT,
    run_id=uuid.uuid4(),
    question="原创问题",
    phase="preparing",
    duration_hours=72,
    minutes_per_round=60,
    rounds_mode="auto",
    manual_rounds=None,
    rounds=72,
    settings_revision=1,
    confirmed_settings_revision=None,
    preview_role_count=0,
    profile_batch_count=0,
    provider_calls_real=0,
    provider_calls_double=0,
    failure_code=None,
    failure_message=None,
    profile_topics=[],
    created_at=NOW,
    updated_at=NOW,
    finished_at=None,
    job_id=uuid.uuid4(),
    job_status="queued",
    frozen=False,
)
BASE = f"/api/projects/{PROJECT.id}/environment"
PREP_PATH = f"{BASE}/preparations/{PREP.id}"


def build_client(*, authenticated: bool = True) -> TestClient:
    app = create_app()

    async def fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = fake_db
    if authenticated:
        app.dependency_overrides[require_user] = lambda: USER
    return TestClient(app)


class AnonymousEnvironmentTests(unittest.TestCase):
    def test_all_endpoints_are_401_without_database_access(self) -> None:
        client = build_client(authenticated=False)
        paths = [
            ("GET", BASE, None),
            ("POST", f"{BASE}/preparations", None),
            ("POST", f"{PREP_PATH}/retry", None),
            ("GET", PREP_PATH, None),
            (
                "PATCH",
                f"{PREP_PATH}/configuration",
                {
                    "settingsRevision": 1,
                    "durationHours": 72,
                    "minutesPerRound": 60,
                    "roundsMode": "auto",
                },
            ),
            ("POST", f"{PREP_PATH}/confirm", {"settingsRevision": 1}),
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


class EnvironmentBoundaryTests(unittest.TestCase):
    def test_other_project_is_404_for_all_endpoints(self) -> None:
        client = build_client()
        with patch(
            "app.routers.environment.projects.resolve_owned_project",
            new=AsyncMock(side_effect=ProjectNotFound),
        ):
            responses = [
                client.get(f"{BASE}?build={BUILD}"),
                client.post(f"{BASE}/preparations?build={BUILD}"),
                client.post(f"{PREP_PATH}/retry"),
                client.get(PREP_PATH),
                client.post(f"{PREP_PATH}/confirm", json={"settingsRevision": 1}),
            ]
        self.assertEqual([item.status_code for item in responses], [404] * 5)
        self.assertEqual(
            {item.text for item in responses}, {'{"detail":"项目不存在"}'
            }
        )

    def test_missing_build_and_preparation_have_stable_messages(self) -> None:
        client = build_client()
        with patch(
            "app.routers.environment.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.environment.environment_service.read_context",
            new=AsyncMock(side_effect=EnvironmentBuildNotFound),
        ):
            response = client.get(f"{BASE}?build={uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "图谱不存在"})

        with patch(
            "app.routers.environment.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.environment.environment_service.get_preparation",
            new=AsyncMock(side_effect=EnvironmentPreparationNotFound),
        ):
            response = client.get(f"{BASE}/preparations/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "准备不存在"})

    def test_invalid_configuration_payload_is_422(self) -> None:
        client = build_client()
        for payload in (
            {},
            {"settingsRevision": 0, "durationHours": 72, "minutesPerRound": 60},
            {"settingsRevision": 1, "durationHours": 72},
        ):
            with self.subTest(payload=payload):
                response = client.patch(
                    f"{PREP_PATH}/configuration", json=payload
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(
            client.post(f"{PREP_PATH}/confirm", json={}).status_code, 422
        )


class EnvironmentStatusTests(unittest.TestCase):
    def _start(self, *, created: bool):
        client = build_client()
        context = AsyncMock(
            return_value=SimpleNamespace(build=SimpleNamespace(id=BUILD))
        )
        with patch(
            "app.routers.environment.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.environment.environment_service.read_context",
            new=context,
        ), patch(
            "app.routers.environment.environment_service.start_preparation",
            new=AsyncMock(return_value=(PREP, created)),
        ), patch(
            "app.routers.environment.environment_service.serialize_preparation",
            new=AsyncMock(return_value={"preparationId": str(PREP.id)}),
        ):
            return client.post(f"{BASE}/preparations?build={BUILD}")

    def test_start_returns_202_for_new_and_200_for_existing(self) -> None:
        self.assertEqual(self._start(created=True).status_code, 202)
        self.assertEqual(self._start(created=False).status_code, 200)

    def test_retry_returns_202(self) -> None:
        client = build_client()
        with patch(
            "app.routers.environment.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.environment.environment_service.get_preparation",
            new=AsyncMock(return_value=PREP),
        ), patch(
            "app.routers.environment.environment_service.start_preparation",
            new=AsyncMock(return_value=(PREP, True)),
        ), patch(
            "app.routers.environment.environment_service.serialize_preparation",
            new=AsyncMock(return_value={"preparationId": str(PREP.id)}),
        ):
            response = client.post(f"{PREP_PATH}/retry")
        self.assertEqual(response.status_code, 202)

    def test_conflict_codes_map_to_status(self) -> None:
        cases = [
            ("no_role_candidates", 422),
            ("duration_invalid", 422),
            ("rounds_invalid", 422),
            ("rounds_not_integral", 422),
            ("graph_not_ready", 409),
            ("preparation_retry_required", 409),
            ("preparation_not_retryable", 409),
            ("preparation_not_ready", 409),
            ("settings_conflict", 409),
            ("preparation_frozen", 409),
        ]
        client = build_client()
        for code, status in cases:
            with self.subTest(code=code):
                with patch(
                    "app.routers.environment.projects.resolve_owned_project",
                    new=AsyncMock(return_value=PROJECT),
                ), patch(
                    "app.routers.environment.environment_service.read_context",
                    new=AsyncMock(
                        return_value=SimpleNamespace(
                            build=SimpleNamespace(id=BUILD)
                        )
                    ),
                ), patch(
                    "app.routers.environment.environment_service.start_preparation",
                    new=AsyncMock(
                        side_effect=EnvironmentConflict(code, "冲突说明")
                    ),
                ):
                    response = client.post(f"{BASE}/preparations?build={BUILD}")
                self.assertEqual(response.status_code, status)
                body = response.json()
                self.assertEqual(body["code"], code)
                self.assertEqual(body["detail"], "冲突说明")
                self.assertNotIn("secret", response.text)
                self.assertNotIn("/app/", response.text)

    def test_service_unavailable_keeps_session_cookie(self) -> None:
        client = build_client()
        with patch(
            "app.routers.environment.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.environment.environment_service.get_preparation",
            new=AsyncMock(side_effect=ServiceUnavailable),
        ):
            response = client.get(PREP_PATH, cookies={"echosim_session": "token"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("echosim_session", response.headers.get("set-cookie", ""))


if __name__ == "__main__":
    unittest.main()
