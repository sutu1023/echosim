"""Zep 固定图谱：幂等键、响应丢失、旧记录与精确检索。"""

from __future__ import annotations

import types
import unittest
from typing import Any

from app.engines.contracts import build_acceptance_material, build_graph_fact
from app.engines.graph import GraphCallError, GraphRunner
from app.engines.settings import EngineSettings


def settings(**overrides: Any) -> EngineSettings:
    values: dict[str, Any] = {
        "zep_api_key": "secret-zep-key",
        "zep_acceptance_graph_id": "00000000-0000-0000-0000-000000000001",
        "graph_write_timeout_seconds": 20.0,
        "graph_status_timeout_seconds": 10.0,
        "graph_index_timeout_seconds": 60.0,
        "graph_poll_interval_seconds": 3.0,
    }
    values.update(overrides)
    return EngineSettings(**values)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += max(seconds, 0.1)


class FakeClient:
    def __init__(
        self,
        *,
        add_responses: list[Any],
        search_responses: list[Any] | None = None,
        default_search: list[Any] | None = None,
    ) -> None:
        self.add_responses = list(add_responses)
        self.search_responses = list(search_responses or [])
        self.default_search = list(default_search or [])
        self.add_keys: list[str] = []
        self.add_bodies: list[str] = []
        self.graph = types.SimpleNamespace(
            episode=types.SimpleNamespace(add=self._add),
            search_episodes=self._search,
        )

    def _add(self, graph_uuid: str, **kwargs: Any) -> dict[str, Any]:
        self.add_keys.append(str(kwargs["idempotency_key"]))
        self.add_bodies.append(str(kwargs["data"]))
        if not self.add_responses:
            raise AssertionError("写入响应不足")
        response = self.add_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def _search(self, graph_uuid: str, **kwargs: Any) -> Any:
        if self.search_responses:
            items = self.search_responses.pop(0)
        else:
            items = list(self.default_search)
        return types.SimpleNamespace(items=items)

    def close(self) -> None:
        return None


def episode(uuid: str, material: Any) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "content": build_graph_fact(material),
        "metadata": {"runId": material.run_id, "marker": material.marker},
    }


class GraphRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.material = build_acceptance_material("graph-run")
        self.clock = FakeClock()

    def run_with(self, client: FakeClient) -> Any:
        return GraphRunner(
            settings(),
            client_factory=lambda _: client,
            clock=self.clock,
            sleep=self.clock.sleep,
        ).run(self.material)

    def test_write_and_search_success(self) -> None:
        client = FakeClient(
            add_responses=[{"episode": {"uuid": "ep-1"}}],
            search_responses=[[], [episode("ep-1", self.material)]],
        )

        result = self.run_with(client)

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.evidence["episodeId"], "ep-1")
        self.assertEqual(result.evidence["state"], "searchable")
        self.assertTrue(result.evidence["firstSearchMiss"])
        self.assertEqual(len(set(client.add_keys)), 1)

    def test_response_lost_first_search_miss_reuses_key(self) -> None:
        client = FakeClient(
            add_responses=[
                GraphCallError("response lost", response_lost=True),
                {"episode": {"uuid": "ep-1"}},
            ],
            search_responses=[[], [episode("ep-1", self.material)]],
        )

        result = self.run_with(client)

        self.assertEqual(result.status, "passed")
        self.assertTrue(result.evidence["writeResponseLost"])
        self.assertTrue(result.evidence["firstSearchMiss"])
        self.assertEqual(len(set(client.add_keys)), 1)
        self.assertEqual(len(set(client.add_bodies)), 1)

    def test_write_unconfirmed_does_not_change_key(self) -> None:
        client = FakeClient(
            add_responses=[
                GraphCallError("response lost", response_lost=True),
                GraphCallError("response lost again", response_lost=True),
            ],
            default_search=[],
        )

        result = self.run_with(client)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "write_unconfirmed")
        self.assertEqual(len(set(client.add_keys)), 1)

    def test_old_record_and_current_record_pass(self) -> None:
        old_material = build_acceptance_material("old-run")
        client = FakeClient(
            add_responses=[{"episode": {"uuid": "ep-current"}}],
            search_responses=[[episode("ep-old", old_material), episode("ep-current", self.material)]],
        )

        result = self.run_with(client)

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.evidence["episodeId"], "ep-current")

    def test_wrong_uuid_fails(self) -> None:
        client = FakeClient(
            add_responses=[{"episode": {"uuid": "ep-current"}}],
            search_responses=[[episode("ep-other", self.material)]],
        )

        result = self.run_with(client)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "stale_or_foreign_hit")

    def test_auth_failure_stops(self) -> None:
        client = FakeClient(
            add_responses=[GraphCallError("unauthorized", status_code=401)]
        )

        result = self.run_with(client)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "auth_failed")
        self.assertEqual(result.calls.real, 1)

    def test_missing_config_does_not_create_client(self) -> None:
        created = False

        def factory(_: EngineSettings) -> FakeClient:
            nonlocal created
            created = True
            return FakeClient(add_responses=[])

        result = GraphRunner(
            settings(zep_api_key=""), client_factory=factory, clock=self.clock
        ).run(self.material)

        self.assertEqual(result.status, "not_run")
        self.assertEqual(result.reason, "missing_config")
        self.assertFalse(created)


if __name__ == "__main__":
    unittest.main()
