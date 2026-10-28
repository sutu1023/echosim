import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import time

import pytest
from fastapi import Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.main import create_app
from app.repositories.health import HealthRepository
from app.services.health import HealthService

from conftest import Probe, request, serve


@pytest.mark.normal
def test_SC_RT_01_health_and_real_postgres(healthy, settings):
    status, headers, body = request(healthy)
    assert status == 200 and body["code"] == "OK"
    assert body["data"]["status"] == "ready"
    assert body["data"]["version"] == "test-v1"
    assert body["data"]["time"].endswith("Z")
    assert body["request_id"] == headers["x-request-id"]
    dsn = os.environ.get("TEST_DATABASE_URL")
    assert dsn and "/echosim_test" in dsn, "dedicated test database required"

    async def ping_real():
        engine = create_async_engine(dsn)
        try:
            await HealthRepository(engine).ping()
        finally:
            await engine.dispose()

    asyncio.run(ping_real())


@pytest.mark.failure
def test_SC_RT_02_missing_and_secret_config(monkeypatch, capsys):
    for name in ["APP_ENV", "APP_VERSION", "DATABASE_URL", "MODEL_MODE", "API_HOST", "API_PORT"]:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as error:
        create_app()
    assert error.value.code == 2
    output = json.loads(capsys.readouterr().err)
    assert "database_url" in [field["field"] for field in output["fields"]]
    monkeypatch.setenv("DATABASE_URL", "secret-sentinel-invalid-url")
    with pytest.raises(SystemExit):
        create_app()
    assert "secret-sentinel" not in capsys.readouterr().err


@pytest.mark.failure
@pytest.mark.parametrize(
    "field,value",
    [
        ("app_env", "staging"),
        ("model_mode", "deepseek"),
        ("api_port", 0),
        ("probe_timeout_seconds", 6),
    ],
)
def test_SC_RT_03_invalid_config(settings, field, value):
    data = settings.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        Settings(**data)


@pytest.mark.failure
def test_SC_RT_04_dependency_failure(settings, capsys):
    with serve(create_app(settings, Probe(failure=True))) as url:
        status, _, body = request(url)
    assert status == 503 and body["code"] == "DEPENDENCY_UNAVAILABLE"
    assert body["data"]["dependencies"]["postgres"] == "unavailable"
    assert "must-not-leak" not in json.dumps(body) + capsys.readouterr().err


@pytest.mark.failure
def test_SC_RT_05_unregistered_route_never_executes(settings):
    app = create_app(settings, Probe())
    touched = []

    @app.get("/unregistered")
    async def forbidden(request: Request):
        touched.append(True)
        return {"secret": "not-visible"}

    with serve(app) as url:
        assert request(url, "/missing")[0] == 404
        status, _, body = request(url, "/unregistered")
        assert status == 403 and body["code"] == "ACCESS_DENIED"
        assert request(url, "/health", "POST")[0] == 405
    assert touched == []


@pytest.mark.boundary
@pytest.mark.failure
def test_SC_RT_06_probe_timeout_liveness_and_draining(settings):
    app = create_app(settings, Probe(delay=0.5))
    with serve(app) as url:
        started = time.monotonic()
        assert request(url)[0] == 503
        assert time.monotonic() - started < 1
        assert request(url, "/livez")[2]["data"]["status"] == "alive"
        app.state.health.draining = True
        assert request(url)[0] == 503


@pytest.mark.normal
def test_SC_RT_07_allowlisted_json_logs(settings, capsys):
    with serve(create_app(settings, Probe())) as healthy:
        _, _, body = request(
            healthy, headers={"Authorization": "secret-sentinel", "Cookie": "private"}
        )
        request(healthy, "/secret-value?key=must-not-log")
    logs = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")
    ]
    assert any(line["request_id"] == body["request_id"] for line in logs)
    assert all(
        {"timestamp", "level", "module", "event", "request_id", "duration_ms"} <= line.keys()
        for line in logs
    )
    assert not any(
        word in json.dumps(logs)
        for word in ["secret-sentinel", "must-not-log", "private", "127.0.0.1"]
    )


@pytest.mark.boundary
@pytest.mark.failure
@pytest.mark.parametrize("supplied", ["", "x" * 1000, "\xff", "contains\ttab"])
def test_SC_RT_08_request_id_is_server_generated(healthy, supplied):
    _, headers, body = request(healthy, headers={"X-Request-ID": supplied})
    assert re.fullmatch("[a-f0-9]{32}", body["request_id"])
    assert body["request_id"] != supplied
    assert headers["x-request-id"] == body["request_id"]


@pytest.mark.normal
def test_SC_GATE_03_mutation_assertion():
    async def check():
        ready, data = await HealthService(Probe(failure=True), "test", 0.1).inspect()
        assert not ready and data["status"] == "not_ready"

    asyncio.run(check())


@pytest.mark.normal
def test_unit_probe_response_is_bounded(healthy):
    request(healthy)
    samples = []
    for _ in range(30):
        started = time.perf_counter()
        assert request(healthy)[0] == 200
        samples.append((time.perf_counter() - started) * 1000)
    p95 = sorted(samples)[28]
    print({"p95_ms": p95, "mean_ms": statistics.mean(samples), "samples": 30})
    assert p95 <= 1000


@pytest.mark.normal
def test_D04_empty_migration_roundtrip():
    dsn = os.environ["TEST_DATABASE_URL"]
    assert "/echosim_test" in dsn
    env = {**os.environ, "DATABASE_URL": dsn}
    for action in [
        ("upgrade", "head"),
        ("upgrade", "head"),
        ("downgrade", "base"),
        ("upgrade", "head"),
    ]:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *action], env=env, capture_output=True
        )
        assert result.returncode == 0, result.stderr.decode()
