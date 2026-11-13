import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from echosim.adapters.credentials import Credentials
from echosim.domain.accounts import Fault
from echosim.domain.model_connection import ModelFailure
from echosim.services.model_config import BASE_URL, ModelConfigurations


class ConfigStore:
    def __init__(self):
        self.rows, self.refs = {}, {}
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            before = deepcopy((self.rows, self.refs))
            try:
                yield self
            except Exception:
                self.rows, self.refs = before
                raise

    async def config_lock(self, owner):
        pass

    async def config_current(self, owner):
        return next((r for r in self.rows.values() if r["user_id"] == owner and r["active"]), None)

    async def config_get(self, owner, version):
        row = self.rows.get(version)
        return row if row and row["user_id"] == owner else None

    async def config_insert(self, owner, version, model, ciphertext):
        for r in self.rows.values():
            if r["user_id"] == owner:
                r["active"] = False
        self.rows[version] = dict(
            id=version,
            user_id=owner,
            model=model,
            ciphertext=ciphertext,
            active=True,
            base_url=BASE_URL,
        )

    async def config_cleanup(self):
        for r in self.rows.values():
            if not r["active"] and r["id"] not in self.refs.values():
                r["ciphertext"] = None

    async def config_acquire(self, owner, version, reference):
        self.refs[reference] = version

    async def config_release(self, reference):
        self.refs.pop(reference, None)


class Provider:
    fail = False
    calls = 0

    async def generate(self, key, model, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise ModelFailure("MODEL_AUTH_FAILED")
        return {"text": "连接成功", "usage": None}


def setup():
    store, provider = ConfigStore(), Provider()
    return (
        ModelConfigurations(store, Credentials(Fernet.generate_key().decode()), provider),
        store,
        provider,
    )


def test_config_rotation_conflict_and_secrets():
    async def scenario():
        service, store, provider = setup()
        a = await service.save("a", BASE_URL, "model", "distinctive-secret", None)
        assert "distinctive-secret" not in repr(a) + repr(store.rows)
        assert a["api_key"] == "********"
        async with store.transaction() as db:
            assert await service.acquire(db, "a", "ref") == a["id"]
        provider.fail = True
        with pytest.raises(ModelFailure):
            await service.save("a", BASE_URL, "new", "new-secret", a["id"])
        assert (await service.current("a"))["id"] == a["id"]
        provider.fail = False
        b = await service.save("a", BASE_URL, "new", "new-secret", a["id"])
        assert store.rows[a["id"]]["ciphertext"]
        with pytest.raises(Fault) as exc:
            await service.save("a", BASE_URL, "stale", "secret", a["id"])
        assert exc.value.status == 409
        assert (await service.current("a"))["id"] == b["id"]
        assert (await service.bound("a", a["id"]))["key"] == "distinctive-secret"
        async with store.transaction() as db:
            await service.release(db, "ref")
        assert store.rows[a["id"]]["ciphertext"] is None

    asyncio.run(scenario())


def test_config_isolation_and_input():
    async def scenario():
        service, _, provider = setup()
        row = await service.save("a", BASE_URL, "model", "secret", None)
        for version in [row["id"], str(uuid4())]:
            with pytest.raises(Fault) as exc:
                await service.get("b", version)
            assert (exc.value.status, exc.value.code) == (404, "NOT_FOUND")
        for url, model, key in [
            ("http://localhost", "x", "s"),
            (BASE_URL, "", "s"),
            (BASE_URL, "x", " "),
            (BASE_URL, "x", "s" * 4097),
        ]:
            before = provider.calls
            with pytest.raises(Fault):
                await service.save("a", url, model, key, row["id"])
            assert provider.calls == before

    asyncio.run(scenario())


def test_master_key_and_authenticated_ciphertext():
    with pytest.raises(ValueError):
        Credentials("")
    vault = Credentials(Fernet.generate_key().decode())
    encrypted = vault.encrypt("secret")
    assert vault.decrypt(encrypted) == "secret"
    with pytest.raises(Fault):
        vault.decrypt(encrypted[:-3] + "bad")


def test_configuration_api_ownership_csrf_and_logs(caplog):
    from fastapi.testclient import TestClient
    from memory_store import MemoryStore, Passwords
    from test_private_api import ORIGIN, login
    from test_runtime import Probe

    from echosim.api.app import create_app
    from echosim.services.accounts import Accounts
    from echosim.services.health import HealthService

    auth = Accounts(MemoryStore(), Passwords())
    for name in ["alice", "bob"]:
        asyncio.run(auth.provision(name, "test-password-123"))
    service, _, provider = setup()
    with TestClient(
        create_app(
            HealthService(Probe(), 1),
            "test",
            app_env="development",
            accounts=auth,
            model_config=service,
            public_origin=ORIGIN,
        )
    ) as c:
        assert c.get("/api/v1/model-configurations").status_code == 401
        h = login(c)
        payload = dict(
            base_url=BASE_URL, model="test", api_key="secret-marker", expected_version=None
        )
        assert c.post("/api/v1/model-configurations", json=payload).status_code == 403
        r = c.post("/api/v1/model-configurations", json=payload, headers=h)
        assert r.status_code == 200
        version = r.json()["id"]
        assert "secret-marker" not in r.text + caplog.text
        assert (
            c.post(
                "/api/v1/model-configurations", json={**payload, "extra": 1}, headers=h
            ).status_code
            == 422
        )
        h = login(c, "bob")
        calls = provider.calls
        assert c.get("/api/v1/model-configurations/" + version).status_code == 404
        assert (
            c.post(
                "/api/v1/model-configurations",
                json={**payload, "expected_version": version},
                headers=h,
            ).status_code
            == 404
        )
        assert provider.calls == calls


def test_missing_master_key_process_fails_without_values():
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "APP_ENV": "development",
        "APP_VERSION": "test",
        "PUBLIC_ORIGIN": "http://localhost:8081",
        "DATABASE_URL": "postgresql://test:private-marker@localhost/test",
        "PORT": "8081",
        "HEALTH_TIMEOUT_SECONDS": "1",
        "WORKER_POLL_SECONDS": "1",
        "MODEL_MASTER_KEY": "",
    }
    result = subprocess.run(
        [sys.executable, "-m", "echosim.main"], env=env, capture_output=True, text=True, timeout=5
    )
    assert result.returncode != 0
    assert "MODEL_MASTER_KEY required" in result.stderr
    assert "private-marker" not in result.stderr + result.stdout
