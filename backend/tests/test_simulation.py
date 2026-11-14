import json

import pytest
from test_story_generation import story

from echosim.domain.model_connection import ModelFailure
from echosim.services.simulation import action_valid, public_story, role_context


def test_role_context_never_contains_other_secrets():
    data = story()
    for i, c in enumerate(data["characters"]):
        c["private_knowledge"] = "secret-marker-" + str(i)
    assert "secret-marker" not in json.dumps(public_story(data))
    context = json.dumps(role_context(data, "1", [], {"scene": "灯室"}))
    assert "secret-marker-0" in context
    assert "secret-marker-1" not in context and "secret-marker-2" not in context


def test_invalid_actor_visibility_and_references():
    valid = dict(
        actor_id="1",
        action="观察灯座",
        dialogue="",
        result="发现裂纹",
        visibility=["1", "2", "3"],
        references=[],
    )
    assert action_valid(valid, "1", []) == valid
    for field, value in [
        ("actor_id", "4"),
        ("visibility", ["4"]),
        ("references", [99]),
        ("action", ""),
    ]:
        with pytest.raises(ModelFailure):
            action_valid({**valid, field: value}, "1", [])


def test_stream_rechecks_session_before_each_event():
    from fastapi.testclient import TestClient
    from test_runtime import Probe

    from echosim.api.app import create_app
    from echosim.api.simulation import register_simulation
    from echosim.domain.accounts import Fault
    from echosim.services.health import HealthService

    class Service:
        async def read(self, *args):
            return {"state": "succeeded", "events": [{"sequence": 1, "content": "private-marker"}]}

    calls = 0

    async def current(request):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise Fault("UNAUTHENTICATED", 401)
        return {"user_id": "owner"}

    app = create_app(HealthService(Probe(), 1), "test")
    register_simulation(app, Service(), current, current)
    with TestClient(app) as client:
        response = client.get("/api/v1/works/w/simulations/s/stream")
        assert response.status_code == 200
        assert "private-marker" not in response.text
        assert calls == 3


def test_rejected_action_never_enters_following_role_context():
    import asyncio
    from contextlib import asynccontextmanager

    from echosim.services.simulation import Simulations

    class Store:
        accepted = []

        @asynccontextmanager
        async def transaction(self):
            yield self

        async def task_guard(self, task):
            return {"state": "running"}

        async def simulation_progress(self, task, stage, ending=None):
            pass

        async def simulation_append(self, task, sequence, content):
            self.accepted.append(content)

        async def task_finish(self, task, state):
            assert state == "succeeded"

    class Stories:
        store = Store()

    class Run(Simulations):
        attempted = 0

        async def ask(self, task, config, prompt, context, validate, name, *args):
            assert "rejected-marker" not in json.dumps(context)
            if name == "architect-v8":
                return {"scene": "灯室", "next_actor": "1", "finished": False, "reason": "继续"}
            self.attempted += 1
            from test_narrative import event

            return validate(
                event(context["actor_id"], "rejected-marker" if self.attempted == 1 else "观察灯座")
            )

        async def check(self, task, config, context, proposed):
            return "rejected-marker" not in json.dumps(proposed)

    run = Run(Stories(), None)
    asyncio.run(run.run({"input_data": {"story": story()}}, {}))
    assert len(run.store.accepted) == 24
    assert "rejected-marker" not in json.dumps(run.store.accepted)
    assert run.attempted == 25


def test_worker_total_deadline_fails_and_releases_task(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from echosim.services.story_worker import StoryWorker

    task = {"kind": "simulation", "user_id": "owner", "configuration_id": "version"}
    db = AsyncMock()
    db.task_claim.return_value = task
    db.task_guard.return_value = {"state": "running"}

    class Store:
        @asynccontextmanager
        async def transaction(self):
            yield db

    timeout = asyncio.timeout
    seen = []

    def immediate_deadline(seconds):
        seen.append(seconds)
        return timeout(0)

    async def unfinished(task, config):
        await asyncio.sleep(10)

    async def scenario():
        configs = AsyncMock()
        configs.bound.return_value = {}
        monkeypatch.setattr(asyncio, "timeout", immediate_deadline)
        assert await StoryWorker(Store(), configs, None, unfinished).tick()
        db.task_finish.assert_awaited_once_with(task, "failed", "TASK_TIMEOUT")
        assert seen == [2700]

    asyncio.run(scenario())
