import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from memory_store import MemoryStore, Passwords
from test_runtime import Probe

from echosim.api.app import create_app
from echosim.services.accounts import Accounts
from echosim.services.health import HealthService
from echosim.services.works import Works

ORIGIN = "http://testserver"


def make_client(env="development", enabled=True):
    store = MemoryStore()
    auth = Accounts(store, Passwords())
    for name in ["alice", "bob", "experience-a", "experience-b"]:
        asyncio.run(auth.provision(name, "test-password-123"))
    app = create_app(
        HealthService(Probe(), 1),
        "test",
        accounts=auth,
        works=Works(store),
        app_env=env,
        quick_login_enabled=enabled,
        public_origin=ORIGIN,
    )
    return TestClient(app, base_url=ORIGIN), auth, store


def login(c, name="alice"):
    response = c.post(
        "/api/v1/sessions",
        json={"username": name, "password": "test-password-123"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf"]}


def test_api_two_users_and_validation():
    c, _, _ = make_client()
    with c:
        assert (
            c.get("/api/v1/works", headers={"Authorization": "Bearer arbitrary"}).status_code == 401
        )
        headers = login(c)
        key = str(uuid4())
        headers["Idempotency-Key"] = key
        r = c.post("/api/v1/works", json={"title": "春山"}, headers=headers)
        assert r.status_code == 201
        work = r.json()
        assert c.post("/api/v1/works", json={"title": "春山"}, headers=headers).json() == work
        assert c.post("/api/v1/works", json={"title": "秋水"}, headers=headers).status_code == 409
        assert (
            c.post(
                "/api/v1/works", json={"title": "x", "user_id": "bob"}, headers=headers
            ).status_code
            == 422
        )
        for query in ["limit=101", "limit=0", "limit=no", "cursor=broken", "user_id=other"]:
            assert c.get("/api/v1/works?" + query).status_code == 422
        login(c, "bob")
        assert c.get("/api/v1/works").json()["items"] == []
        for identifier in [work["id"], str(uuid4()), "unknown"]:
            response = c.get("/api/v1/works/" + identifier, headers={"X-Request-ID": "fixed"})
            assert response.status_code == 404
            assert response.json() == {
                "error": {"code": "NOT_FOUND", "message": "资源不存在"},
                "request_id": "fixed",
            }


def test_csrf_cookie_and_logs(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="echosim")
    c, _, _ = make_client()
    with c:
        for origin in [None, "https://evil.example"]:
            h = {"Origin": origin} if origin else {}
            for path, body in [
                ("/api/v1/sessions", {"username": "alice", "password": "test-password-123"}),
                ("/api/v1/dev-sessions", {"target": "A"}),
            ]:
                assert c.post(path, json=body, headers=h).status_code == 403
        h = login(c)
        cookie = c.cookies.get("echosim_session")
        assert cookie
        for extra in [{}, {"Origin": ORIGIN}, {**h, "Origin": "https://evil.example"}]:
            assert (
                c.post("/api/v1/works", json={"title": "private-title"}, headers=extra).status_code
                == 403
            )
        assert (
            c.delete(
                "/api/v1/sessions/current", headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert c.get("/api/v1/sessions/current").status_code == 200
        assert c.delete("/api/v1/sessions/current", headers=h).status_code == 204
        c.cookies.set("echosim_session", cookie)
        assert c.get("/api/v1/works").status_code == 401
        assert all(x not in caplog.text for x in [cookie, "test-password-123", "private-title"])


@pytest.mark.parametrize(
    "env,enabled", [("production", True), ("test", True), ("development", False)]
)
def test_quick_disabled_server(env, enabled):
    c, _, _ = make_client(env, enabled)
    with c:
        assert c.get("/api/v1/capabilities").json()["quick_login"] is False
        assert (
            c.post(
                "/api/v1/dev-sessions", json={"target": "A"}, headers={"Origin": ORIGIN}
            ).status_code
            == 404
        )


def test_quick_api_switch():
    c, _, _ = make_client()
    with c:
        for target in ["A", "B"]:
            old = c.cookies.get("echosim_session")
            response = c.post(
                "/api/v1/dev-sessions", json={"target": target}, headers={"Origin": ORIGIN}
            )
            assert response.status_code == 200
            assert response.json()["username"] == "experience-" + target.lower()
            if old:
                assert old != c.cookies.get("echosim_session")
        assert (
            c.post(
                "/api/v1/dev-sessions", json={"target": "alice"}, headers={"Origin": ORIGIN}
            ).status_code
            == 422
        )


def test_production_cookie_attributes():
    c, _, _ = make_client("production")
    with c:
        response = c.post(
            "/api/v1/sessions",
            json={"username": "alice", "password": "test-password-123"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        for flag in ["httponly", "secure", "samesite=lax", "max-age=604800", "path=/"]:
            assert flag in cookie
        assert "token" not in response.json()


def test_input_boundaries_and_invalid_json():
    c, _, store = make_client()
    with c:
        h = login(c)
        h["Idempotency-Key"] = str(uuid4())
        for payload in [{"title": " "}, {"title": "x" * 101}, {"title": 42}, {"title": None}]:
            assert c.post("/api/v1/works", json=payload, headers=h).status_code == 422
        assert store.works == {}
        for text in ["x", "😀" * 100, "\ufeff 春山\ufeff"]:
            h["Idempotency-Key"] = str(uuid4())
            response = c.post("/api/v1/works", json={"title": text}, headers=h)
            assert response.status_code == 201
        assert response.json()["title"] == "春山"
        for data in ["{", "x" * 5000, "[1]", "null"]:
            assert (
                c.post(
                    "/api/v1/works", content=data, headers={**h, "Content-Type": "application/json"}
                ).status_code
                == 422
            )
        assert c.get("/api/v1/works?limit=1&limit=2").status_code == 422
        assert (
            c.post(
                "/api/v1/works",
                json={"title": "missing key"},
                headers={k: v for k, v in h.items() if k != "Idempotency-Key"},
            ).status_code
            == 422
        )


def test_expired_session_write_and_reset_endpoint():
    c, auth, _ = make_client()
    with c:
        h = login(c)
        asyncio.run(auth.provision("alice", "replacement-password", reset=True))
        assert c.get("/api/v1/sessions/current").status_code == 401
        assert c.post("/api/v1/works", json={"title": "rejected"}, headers=h).status_code == 401
        assert c.delete("/api/v1/sessions/current", headers=h).status_code == 204
        assert c.delete("/api/v1/sessions/current", headers=h).status_code == 204


def test_private_routes_refresh(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>EchoSim</title>")
    with TestClient(create_app(HealthService(Probe(), 1), "test", tmp_path)) as c:
        for path in ["/login", "/works", "/works/" + str(uuid4())]:
            assert c.get(path).status_code == 200
        assert c.get("/works/unknown/prepare").status_code == 404
