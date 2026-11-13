import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from echosim.api.app import create_app
from echosim.config import Settings
from echosim.logging import JsonFormatter
from echosim.services.health import HealthService
from echosim.worker import run_worker

ENV = {
    "APP_ENV": "test",
    "APP_VERSION": "test-1",
    "PORT": "8080",
    "DATABASE_URL": "postgresql://test:test@127.0.0.1:1/test",
    "HEALTH_TIMEOUT_SECONDS": "1",
    "WORKER_POLL_SECONDS": "60",
}


class Probe:
    def __init__(self, result=True):
        self.result = result

    async def check(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client(probe=None):
    return TestClient(create_app(HealthService(probe or Probe(), 1), "test-1"))


def test_normal_health_and_correlation():
    with client() as c:
        r = c.get("/health", headers={"X-Request-ID": "request_123"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["dependencies"] == {"database": "up"}
    assert r.json()["version"] == "test-1"
    assert r.json()["request_id"] == r.headers["x-request-id"] == "request_123"
    assert r.headers["cache-control"] == "no-store"


def test_failure_dependency_and_recovery():
    probe = Probe(RuntimeError("private-database-password"))
    with client(probe) as c:
        r = c.get("/health")
        assert r.status_code == 503
        assert r.json()["status"] == "degraded"
        assert r.json()["dependencies"] == {"database": "down"}
        assert "private" not in r.text
        probe.result = True
        assert c.get("/health").status_code == 200


def test_boundary_probe_timeout():
    class Slow:
        async def check(self):
            await asyncio.sleep(30)
            return True

    start = time.monotonic()
    assert asyncio.run(HealthService(Slow(), 0.02).ready()) is False
    assert time.monotonic() - start < 1


@pytest.mark.parametrize("value", ["a" * 65, "invalid space", ""])
def test_boundary_request_identifier(value):
    with client() as c:
        r = c.get("/health", headers={"X-Request-ID": value})
    assert r.headers["x-request-id"] != value
    assert len(r.headers["x-request-id"]) == 32
    assert r.json()["request_id"] == r.headers["x-request-id"]


def test_boundary_request_identifier_maximum():
    with client() as c:
        r = c.get("/health", headers={"X-Request-ID": "a" * 64})
    assert r.headers["x-request-id"] == "a" * 64


@pytest.mark.parametrize("path", ["/missing", "/api/v1/works", "/api/v1/works/unknown"])
def test_failure_unknown_and_unavailable_business(path):
    with client() as c:
        r = c.get(path, headers={"Authorization": "Bearer arbitrary", "X-Request-ID": "fixed"})
    assert r.status_code == 404
    assert r.json() == {
        "error": {"code": "NOT_FOUND", "message": "资源不存在"},
        "request_id": "fixed",
    }


def test_failure_method():
    with client() as c:
        r = c.post("/health")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in r.headers["allow"]


def test_failure_unexpected_and_log_redaction(caplog):
    app = create_app(HealthService(Probe(), 1), "test")

    @app.get("/broken")
    async def broken():
        raise RuntimeError("secret-marker")

    caplog.set_level(logging.INFO, logger="echosim")
    with TestClient(app) as c:
        r = c.get(
            "/broken?key=secret-marker",
            headers={
                "Authorization": "Bearer secret-marker",
                "Cookie": "secret-marker",
                "X-Request-ID": "trace",
            },
        )
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret-marker" not in r.text
    records = [json.loads(JsonFormatter().format(x)) for x in caplog.records if x.name == "echosim"]
    assert "secret-marker" not in json.dumps(records)
    completed = next(x for x in records if x["event"] == "request_completed")
    assert completed["request_id"] == "trace"
    assert completed["status"] == 500
    assert completed["duration_ms"] >= 0
    assert {"timestamp", "level", "service", "event", "request_id"} <= completed.keys()
    assert any(x["event"] == "request_failed" for x in records)


@pytest.mark.parametrize("entry", ["echosim.main", "echosim.worker"])
@pytest.mark.parametrize(
    "override", [{"DATABASE_URL": ""}, {"PORT": "0"}, {"DATABASE_URL": "secret-marker"}]
)
def test_failure_configuration_process(entry, override):
    result = subprocess.run(
        [sys.executable, "-m", entry],
        env={**os.environ, **ENV, **override},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "Invalid runtime configuration" in result.stderr
    assert "secret-marker" not in result.stderr + result.stdout
    assert "Traceback" not in result.stderr


def test_failure_missing_configuration(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "key,value",
    [
        ("PORT", "65536"),
        ("APP_ENV", "unknown"),
        ("HEALTH_TIMEOUT_SECONDS", "0"),
        ("HEALTH_TIMEOUT_SECONDS", "11"),
        ("WORKER_POLL_SECONDS", "61"),
        ("APP_VERSION", ""),
    ],
)
def test_boundary_configuration(monkeypatch, key, value):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings()


def test_normal_configuration(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    assert Settings().port == 8080


def test_normal_static_page_and_unknown_assets(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>EchoSim</title>")
    app = create_app(HealthService(Probe(), 1), "test", tmp_path)
    with TestClient(app) as c:
        assert "EchoSim" in c.get("/").text
        r = c.get("/assets/missing.js")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"
        assert c.get("/api/missing").status_code == 404


def test_boundary_worker_stop_during_probe():
    class Slow:
        async def check(self):
            await asyncio.sleep(30)
            return True

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(run_worker(HealthService(Slow(), 10), 60, stop))
        await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(scenario())


def test_normal_worker_sigterm():
    process = subprocess.Popen(
        [sys.executable, "-m", "echosim.worker"],
        env={**os.environ, **ENV},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Bound the readiness wait: avoid a blocked readline if startup regresses.
        import select

        assert select.select([process.stdout], [], [], 5)[0]
        started = json.loads(process.stdout.readline())
        assert started["event"] == "worker_started"
        before = time.monotonic()
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=5)
        assert process.returncode == 0, err
        assert time.monotonic() - before < 5
        assert any(json.loads(line)["event"] == "worker_stopped" for line in out.splitlines())
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
