import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient
from memory_store import MemoryStore, Passwords
from test_private_api import ORIGIN, login
from test_runtime import Probe

from echosim.api.app import create_app
from echosim.domain.accounts import Fault
from echosim.services.accounts import Accounts
from echosim.services.health import HealthService


class Resource:
    def __init__(self, owner, work):
        self.owner, self.work, self.writes = owner, work, 0

    async def read(self, owner, work):
        if owner != self.owner or work != self.work:
            raise Fault("NOT_FOUND", 404)
        return {"state": "ready", "content": {"sources": {"s": "<script>secret</script>"}}}

    async def build(self, owner, work):
        await self.read(owner, work)
        self.writes += 1
        return {"state": "queued"}

    async def choose(self, owner, work, task, key, data, discard=False):
        await self.read(owner, work)
        self.writes += 1
        return {"state": "queued"}


def test_graph_adoption_auth_csrf_and_strict_payload():
    store = MemoryStore()
    accounts = Accounts(store, Passwords())
    asyncio.run(accounts.provision("alice", "test-password-123"))
    asyncio.run(accounts.provision("bob", "test-password-123"))
    session = asyncio.run(accounts.login("alice", "test-password-123", "test"))
    owner = asyncio.run(accounts.current(session["token"]))["user_id"]
    resource = Resource(owner, str(uuid4()))
    app = create_app(
        HealthService(Probe(), 1),
        "test",
        accounts=accounts,
        app_env="development",
        public_origin=ORIGIN,
        graph=resource,
        adoptions=resource,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        path = f"/api/v1/works/{resource.work}/story-graph"
        adoption = f"/api/v1/works/{resource.work}/simulations/{uuid4()}/adoptions"
        assert client.get(path).status_code == 401
        headers = login(client)
        assert "secret" in client.get(path).text
        assert client.post(path + "/builds").status_code == 403
        assert client.post(path + "/builds", headers=headers).status_code == 202
        assert (
            client.post(
                adoption,
                headers=headers,
                json={"expected_canon_revision": "0", "acknowledge_irreversible": True},
            ).status_code
            == 422
        )
        assert (
            client.post(
                adoption, json={"expected_canon_revision": 0, "acknowledge_irreversible": True}
            ).status_code
            == 403
        )
        headers = login(client, "bob")
        for url in (path, f"/api/v1/works/{uuid4()}/story-graph"):
            response = client.get(url)
            assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
        assert client.post(path + "/builds", headers=headers).status_code == 404
        assert resource.writes == 1
