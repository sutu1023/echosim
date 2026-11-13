import asyncio

import pytest
from fastapi.testclient import TestClient

from echosim.api.app import create_app
from echosim.domain.model_connection import ModelFailure
from echosim.services.health import HealthService
from echosim.services.model_connection import ModelConnectionService


class Probe:
    async def check(self):
        return True


class Provider:
    calls = 0

    async def invoke(self, key):
        self.calls += 1
        return {"choices": [{"message": {"content": "ok"}}]}


def client(provider=None, **kwargs):
    return TestClient(
        create_app(
            HealthService(Probe(), 1),
            "test",
            model_test=ModelConnectionService(provider or Provider()),
            **kwargs,
        )
    )


@pytest.mark.parametrize(
    "env,enabled", [("production", True), ("test", True), ("development", False)]
)
def test_disabled(env, enabled):
    with client(app_env=env, model_test_enabled=enabled) as c:
        assert (
            c.post("/api/v1/model-connection-tests", json={"api_key": "marker"}).status_code == 404
        )
        assert c.get("/dev/model-test").status_code == 404


def test_success_and_origin(caplog):
    p = Provider()
    with client(p, app_env="development", model_test_enabled=True) as c:
        r = c.post("/api/v1/model-connection-tests", json={"api_key": "private-marker"})
        assert r.status_code == 200
        assert r.json()["usage"] is None
        assert r.headers["cache-control"] == "no-store"
        assert "private-marker" not in r.text + caplog.text
        assert (
            c.post(
                "/api/v1/model-connection-tests",
                json={"api_key": "marker"},
                headers={"Origin": "https://foreign.example"},
            ).status_code
            == 403
        )
        assert p.calls == 1


@pytest.mark.parametrize("key", ["", " ", "a" * 4097, "bad\nkey"])
def test_invalid_key(key):
    p = Provider()
    with client(p, app_env="development", model_test_enabled=True) as c:
        assert c.post("/api/v1/model-connection-tests", json={"api_key": key}).status_code == 422
        assert p.calls == 0


def test_deadline():
    class Slow:
        async def invoke(self, key):
            await asyncio.sleep(5)

    with pytest.raises(ModelFailure) as e:
        asyncio.run(ModelConnectionService(Slow(), timeout=0.01).test("marker"))
    assert e.value.code == "MODEL_TIMEOUT"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"error": "private-marker"},
        {"choices": []},
        {"choices": [{"message": {"content": " "}}]},
        {"choices": [{"message": {"reasoning_content": "thought"}}]},
    ],
)
def test_invalid_response(data):
    class Invalid:
        async def invoke(self, key):
            return data

    with pytest.raises(ModelFailure) as e:
        asyncio.run(ModelConnectionService(Invalid()).test("marker"))
    assert e.value.code == "MODEL_INVALID_RESPONSE"


def test_public_backend_rejects_private(monkeypatch):
    from echosim.adapters.model_connection import PublicBackend

    async def run():
        loop = asyncio.get_running_loop()

        async def resolve(*args, **kwargs):
            return [(2, 1, 6, "", ("127.0.0.1", 443))]

        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        with pytest.raises(ModelFailure):
            await PublicBackend().connect_tcp("opencode.ai", 443)

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, {}, "MODEL_AUTH_FAILED"),
        (403, {}, "MODEL_AUTH_FAILED"),
        (429, {}, "MODEL_RATE_LIMITED"),
        (500, {}, "MODEL_UPSTREAM_ERROR"),
        (302, {}, "MODEL_UPSTREAM_ERROR"),
        (200, [], "MODEL_INVALID_RESPONSE"),
    ],
)
def test_transport_errors(monkeypatch, status, body, code):
    import httpx

    import echosim.adapters.model_connection as module

    original = httpx.AsyncClient
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["user-agent"] == "EchoSim-Connection-Test/0.0.1"
        from uuid import UUID

        UUID(request.headers["x-opencode-session"])
        assert request.url.path == "/inference/go/openai/v1/chat/completions"
        return httpx.Response(status, json=body)

    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    with pytest.raises(ModelFailure) as e:
        asyncio.run(module.OpenCodeProvider().invoke("private-marker"))
    assert e.value.code == code
    assert len(calls) == 1


def test_public_backend_pins_validated_address(monkeypatch):
    from httpcore._backends.auto import AutoBackend

    from echosim.adapters.model_connection import PublicBackend

    async def run():
        async def resolve(*args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 443))]

        async def connect(self, host, port, *args):
            assert host == "8.8.8.8" and port == 443
            return "connected"

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(AutoBackend, "connect_tcp", connect)
        assert await PublicBackend().connect_tcp("opencode.ai", 443) == "connected"

    asyncio.run(run())


@pytest.mark.parametrize("body", [b"not-json", b"x" * 1048577])
def test_malformed_and_large_transport(monkeypatch, body):
    import httpx

    import echosim.adapters.model_connection as module

    original = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
        ),
    )
    with pytest.raises(ModelFailure) as e:
        asyncio.run(module.OpenCodeProvider().invoke("marker"))
    assert e.value.code == "MODEL_INVALID_RESPONSE"


def test_valid_usage_and_unknown_fields():
    class WithUsage:
        async def invoke(self, key):
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            }

    assert (
        asyncio.run(ModelConnectionService(WithUsage()).test("marker"))["usage"]["total_tokens"]
        == 5
    )
    with client(app_env="development", model_test_enabled=True) as c:
        r = c.post("/api/v1/model-connection-tests", json={"api_key": "marker", "model": "other"})
        assert r.status_code == 422 and "marker" not in r.text


def test_safe_diagnostic_metadata(caplog):
    import json
    import logging

    from echosim.logging import JsonFormatter

    caplog.set_level(logging.INFO, logger="echosim")
    with client(app_env="development", model_test_enabled=True) as c:
        assert (
            c.post("/api/v1/model-connection-tests", json={"api_key": "private-marker"}).status_code
            == 200
        )
    record = next(r for r in caplog.records if r.getMessage() == "model_connection_test")
    data = json.loads(JsonFormatter().format(record))
    assert data["model"] == "deepseek-v4.1-flash"
    assert data["configuration_version"] == "opencode-go-test-v1"
    assert data["prompt_version"] == "v1"
    assert data["timeout_seconds"] == 60
    assert data["max_tokens"] == 256
    assert data["temperature"] == "provider-default"
    assert data["usage"] is None
    assert "private-marker" not in json.dumps(data)
