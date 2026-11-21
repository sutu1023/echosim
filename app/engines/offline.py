"""离线契约检查：用测试替身驱动同一套引擎契约，不访问真实服务。"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .baseline import DependencyBaseline, PINS, StageResult
from .contracts import (
    AcceptanceMaterial,
    CallCounts,
    ItemResult,
    PASSED,
    FAILED,
    NOT_RUN,
    build_acceptance_material,
    build_action_content,
    build_target_post_content,
    merge_calls,
    utf16_len,
)
from .graph import GraphCallError, GraphRunner
from .model import HttpResponse, ModelRunner, build_chat_completions_url
from .settings import EngineSettings
from .simulation import (
    PlatformOutcome,
    SharedSetupError,
    run_platform,
)
from .smoke import run_smoke

DOUBLE = "double"


@dataclass
class Scenario:
    scenario_id: str
    status: str
    detail: str
    calls: CallCounts = field(default_factory=CallCounts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.scenario_id,
            "status": self.status,
            "detail": self.detail,
            "calls": self.calls.to_dict(),
        }


def _settings(**overrides: Any) -> EngineSettings:
    base: dict[str, Any] = {
        "model_api_base_url": "https://model.test.local/v1",
        "model_api_key": "offline-model-key",
        "model_name": "offline-model",
        "model_timeout_seconds": 30.0,
        "zep_api_key": "offline-zep-key",
        "zep_acceptance_graph_id": "00000000-0000-0000-0000-000000000001",
        "graph_write_timeout_seconds": 20.0,
        "graph_status_timeout_seconds": 10.0,
        "graph_index_timeout_seconds": 60.0,
        "graph_poll_interval_seconds": 3.0,
        "simulation_timeout_seconds": 60.0,
        "smoke_live_total_timeout_seconds": 300.0,
    }
    base.update(overrides)
    return EngineSettings(**base)


def _valid_content(material: AcceptanceMaterial) -> str:
    start = utf16_len(material.text[: material.text.index(material.marker)])
    end = start + utf16_len(material.marker)
    return json.dumps(
        {
            "materialId": material.material_id,
            "summary": "社区图书角收到蓝色书箱并安排周末开放。",
            "facts": [
                {
                    "text": "蓝色书箱编号",
                    "evidenceQuote": material.marker,
                    "sourceStart": start,
                    "sourceEnd": end,
                    "kind": "ownership",
                }
            ],
        },
        ensure_ascii=False,
    )


def _chat_body(material: AcceptanceMaterial, content: str | None = None) -> str:
    return json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": content if content is not None else _valid_content(material)
                    }
                }
            ]
        },
        ensure_ascii=False,
    )


class SequenceTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(
        self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> HttpResponse:
        self.calls += 1
        if not self.responses:
            raise AssertionError("测试替身响应不足")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _model_scenario(
    scenario_id: str,
    material: AcceptanceMaterial,
    responses: list[HttpResponse],
    *,
    expected_status: str,
    expected_reason: str | None = None,
    expected_calls: int = 1,
) -> Scenario:
    transport = SequenceTransport(responses)
    runner = ModelRunner(
        _settings(),
        transport=transport,
        call_kind=DOUBLE,
        clock=lambda: 0.0,
        sleep=lambda _: None,
    )
    result = runner.run(material)
    ok = (
        result.status == expected_status
        and result.reason == expected_reason
        and result.calls.double == expected_calls
        and result.calls.real == 0
    )
    return Scenario(
        scenario_id,
        PASSED if ok else FAILED,
        f"status={result.status} reason={result.reason} calls={result.calls.to_dict()}",
        result.calls,
    )


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += max(seconds, 0.1)


class FakeGraphClient:
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
        self.search_calls = 0
        self.graph = types.SimpleNamespace(
            episode=types.SimpleNamespace(add=self._add),
            search_episodes=self._search,
        )

    def _add(self, graph_uuid: str, **kwargs: Any) -> dict[str, Any]:
        self.add_keys.append(str(kwargs.get("idempotency_key")))
        if not self.add_responses:
            raise AssertionError("测试替身写入响应不足")
        response = self.add_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def _search(self, graph_uuid: str, **kwargs: Any) -> Any:
        self.search_calls += 1
        if self.search_responses:
            items = self.search_responses.pop(0)
        else:
            items = list(self.default_search)
        return types.SimpleNamespace(items=items)

    def close(self) -> None:
        return None


def _episode(uuid: str, material: AcceptanceMaterial) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "content": f"河畔社区图书角验收批次 {material.run_id} 的蓝色书箱编号为 {material.marker}，保管人是林舟。",
        "metadata": {"runId": material.run_id, "marker": material.marker},
    }


def _graph_scenario(
    scenario_id: str,
    material: AcceptanceMaterial,
    *,
    add_responses: list[Any],
    search_responses: list[Any] | None = None,
    default_search: list[Any] | None = None,
    expected_status: str,
    expected_reason: str | None = None,
    expect_single_key: bool | None = None,
    expect_uuid_key: bool = False,
) -> Scenario:
    clock = FakeClock()
    client = FakeGraphClient(
        add_responses=add_responses,
        search_responses=search_responses,
        default_search=default_search,
    )
    runner = GraphRunner(
        _settings(),
        client_factory=lambda _: client,
        call_kind=DOUBLE,
        clock=clock,
        sleep=clock.sleep,
    )
    result = runner.run(material)
    same_key = len(set(client.add_keys)) <= 1
    uuid_key_ok = True
    if expect_uuid_key:
        try:
            parsed = uuid.UUID(client.add_keys[0])
            uuid_key_ok = parsed.version == 4 and parsed.variant == uuid.RFC_4122
        except (IndexError, ValueError):
            uuid_key_ok = False
    ok = result.status == expected_status and result.reason == expected_reason
    if expect_single_key is not None:
        ok = ok and same_key == expect_single_key
    ok = ok and uuid_key_ok
    return Scenario(
        scenario_id,
        PASSED if ok else FAILED,
        f"status={result.status} reason={result.reason} keys={client.add_keys}",
        result.calls,
    )


class FakePlatform:
    def __init__(self, state: dict[str, Any], *, fail_close: bool = False) -> None:
        self.state = state
        self.fail_close = fail_close
        self.next_id = 100

    async def sign_up(self, agent_id: int, message: tuple[str, str, str]) -> dict[str, Any]:
        self.state["role"] = message[1]
        return {"success": True, "user_id": agent_id}

    async def create_post(self, agent_id: int, content: str) -> dict[str, Any]:
        post_id = self.state["next_post"]
        self.state["next_post"] += 1
        self.state["posts"][content] = post_id
        return {"success": True, "post_id": post_id}

    async def create_comment(self, agent_id: int, message: tuple[int, str]) -> dict[str, Any]:
        post_id, content = message
        comment_id = self.state["next_comment"]
        self.state["next_comment"] += 1
        self.state["comments"][(post_id, content)] = comment_id
        return {"success": True, "comment_id": comment_id}

    def find_post_id(self, content: str) -> int | None:
        return self.state["posts"].get(content)

    def close(self) -> None:
        if self.fail_close:
            raise RuntimeError("模拟清理失败")


def _fake_readback(
    state: dict[str, Any], *, wrong_target: bool = False
) -> Callable[[Path, str, AcceptanceMaterial], dict[str, Any] | None]:
    def read(path: Path, platform_key: str, material: AcceptanceMaterial) -> dict[str, Any] | None:
        if platform_key == "platform_a":
            content = build_action_content(platform_key, material)
            post_id = state["posts"].get(content)
            if post_id is None:
                return None
            return {
                "platformKey": "platform_a",
                "nativePlatform": "twitter",
                "nativeAction": "create_post",
                "role": "居民代表",
                "content": content,
                "recordId": f"post:{post_id}",
                "result": "success",
                "targetPostId": None,
            }
        target_content = build_target_post_content(platform_key, material)
        comment_content = build_action_content(platform_key, material)
        target_post_id = state["posts"].get(target_content)
        if target_post_id is None:
            return None
        comment_id = state["comments"].get((target_post_id, comment_content))
        if comment_id is None:
            return None
        return {
            "platformKey": "platform_b",
            "nativePlatform": "reddit",
            "nativeAction": "create_comment",
            "role": "值班志愿者",
            "content": comment_content,
            "recordId": f"comment:{comment_id}",
            "result": "success",
            "targetPostId": target_post_id + (1 if wrong_target else 0),
        }

    return read


def _run_fake_platform(
    platform_key: str,
    material: AcceptanceMaterial,
    *,
    fail_factory: bool = False,
    fail_close: bool = False,
    wrong_target: bool = False,
) -> PlatformOutcome:
    state: dict[str, Any] = {
        "posts": {},
        "comments": {},
        "next_post": 1,
        "next_comment": 1,
    }

    def factory(key: str, db_path: Path, work_root: Path) -> FakePlatform:
        if fail_factory:
            raise RuntimeError("模拟平台环境失败")
        return FakePlatform(state, fail_close=fail_close)

    with tempfile.TemporaryDirectory(prefix="echosim-offline-sim-") as tmp:
        return run_platform(
            platform_key,
            _settings(),
            material,
            Path(tmp),
            platform_factory=factory,
            readback=_fake_readback(state, wrong_target=wrong_target),
            call_kind=DOUBLE,
        )


def _verified_baseline() -> DependencyBaseline:
    stages = {
        name: StageResult("passed", "offline", 0, {})
        for name in ("resolve", "install", "import", "minimalRun")
    }
    return DependencyBaseline("verified", stages, dict(PINS), "offline", "3.11")


def _pass_item(item_id: str, dependency: str, stage: str) -> ItemResult:
    return ItemResult(
        item_id,
        dependency,
        stage,
        PASSED,
        None,
        1,
        CallCounts(double=1),
        {"offline": True},
    )


def _graph_pass_item() -> ItemResult:
    return ItemResult(
        "graph.searchable",
        "graph",
        "episode-search",
        PASSED,
        None,
        1,
        CallCounts(double=2),
        {"accepted": True, "state": "searchable", "episodeId": "offline-episode"},
    )


def _platform_pass(key: str) -> PlatformOutcome:
    return PlatformOutcome(
        ItemResult(
            f"simulation.{key}",
            "simulation",
            "create_post" if key == "platform_a" else "create_comment",
            PASSED,
            None,
            1,
            CallCounts(double=1),
            {
                "platformKey": key,
                "nativePlatform": "twitter" if key == "platform_a" else "reddit",
                "nativeAction": "create_post" if key == "platform_a" else "create_comment",
                "role": "居民代表" if key == "platform_a" else "值班志愿者",
                "recordId": f"offline-{key}",
                "targetPostId": 1 if key == "platform_b" else None,
                "cleanup": "passed",
            },
        ),
        "passed",
    )


def _platform_fail(key: str, reason: str) -> PlatformOutcome:
    return PlatformOutcome(
        ItemResult(
            f"simulation.{key}",
            "simulation",
            "create_post" if key == "platform_a" else "create_comment",
            FAILED,
            reason,
            1,
            CallCounts(double=1),
            {"cleanup": "passed"},
        ),
        "passed",
    )


def _smoke_case(
    scenario_id: str,
    *,
    settings: EngineSettings,
    platform_runner: Callable[[str, EngineSettings, AcceptanceMaterial, Path], PlatformOutcome],
    dirty: bool = False,
    allow_dirty: bool = False,
) -> tuple[Scenario, dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="echosim-offline-smoke-") as tmp:
        outcome = run_smoke(
            settings=settings,
            report_dir=Path(tmp),
            tested_sha="offline-sha",
            dirty=dirty,
            allow_dirty=allow_dirty,
            mode="offline-contract",
            baseline_evaluator=_verified_baseline,
            model_runner=lambda material: _pass_item(
                "model.structured", "model", "structured-extraction"
            ),
            graph_runner=lambda material: _graph_pass_item(),
            platform_runner=platform_runner,
        )
    items = {item["id"]: item for item in outcome.report["items"]}
    return (
        Scenario(
            scenario_id,
            PASSED if outcome.exit_code in {0, 1, 2} else FAILED,
            f"exit={outcome.exit_code} result={outcome.report['result']}",
            merge_calls(
                [
                    ItemResult(
                        "scenario",
                        "offline",
                        "smoke",
                        PASSED,
                        None,
                        0,
                        CallCounts(
                            real=outcome.report["calls"]["real"],
                            double=outcome.report["calls"]["double"],
                        ),
                        {},
                    )
                ]
            ),
        ),
        items,
    )


def run_offline() -> dict[str, Any]:
    material = build_acceptance_material("offline-run")
    scenarios: list[Scenario] = []

    scenarios.append(
        Scenario(
            "model-url-v1",
            PASSED
            if build_chat_completions_url("https://model.test.local/v1")
            == "https://model.test.local/v1/chat/completions"
            else FAILED,
            "根地址含 /v1 时只拼一次 /chat/completions",
        )
    )
    scenarios.append(
        _model_scenario(
            "model-valid",
            material,
            [HttpResponse(200, _chat_body(material))],
            expected_status=PASSED,
        )
    )
    scenarios.append(
        _model_scenario(
            "model-invalid-output",
            material,
            [HttpResponse(200, _chat_body(material, "{\"materialId\": \"bad\"}"))],
            expected_status=FAILED,
            expected_reason="invalid_output",
        )
    )
    scenarios.append(
        _model_scenario(
            "model-response-format-retry",
            material,
            [
                HttpResponse(400, "response_format is not supported"),
                HttpResponse(200, _chat_body(material)),
            ],
            expected_status=PASSED,
            expected_calls=2,
        )
    )
    scenarios.append(
        _model_scenario(
            "model-auth-no-retry",
            material,
            [HttpResponse(401, "unauthorized")],
            expected_status=FAILED,
            expected_reason="auth_failed",
        )
    )
    scenarios.append(
        _model_scenario(
            "model-rate-limit-retry",
            material,
            [HttpResponse(429, "slow down"), HttpResponse(200, _chat_body(material))],
            expected_status=PASSED,
            expected_calls=2,
        )
    )
    missing_model = ModelRunner(_settings(model_api_key=""), call_kind=DOUBLE).run(material)
    scenarios.append(
        Scenario(
            "model-missing-config",
            PASSED
            if missing_model.status == NOT_RUN and missing_model.reason == "missing_config"
            else FAILED,
            f"status={missing_model.status} reason={missing_model.reason}",
        )
    )

    current = _episode("episode-current", material)
    old = _episode("episode-old", build_acceptance_material("offline-old"))
    scenarios.append(
        _graph_scenario(
            "graph-response-lost-first-search-miss",
            material,
            add_responses=[
                GraphCallError("response lost", response_lost=True),
                {"episode": {"uuid": "episode-current"}},
            ],
            search_responses=[[], [current]],
            expected_status=PASSED,
            expect_single_key=True,
            expect_uuid_key=True,
        )
    )
    scenarios.append(
        _graph_scenario(
            "graph-write-unconfirmed",
            material,
            add_responses=[
                GraphCallError("response lost", response_lost=True),
                GraphCallError("response lost again", response_lost=True),
            ],
            default_search=[],
            expected_status=FAILED,
            expected_reason="write_unconfirmed",
            expect_single_key=True,
        )
    )
    scenarios.append(
        _graph_scenario(
            "graph-old-record-exact-hit",
            material,
            add_responses=[{"episode": {"uuid": "episode-current"}}],
            search_responses=[[old, current]],
            expected_status=PASSED,
        )
    )
    scenarios.append(
        _graph_scenario(
            "graph-wrong-uuid",
            material,
            add_responses=[{"episode": {"uuid": "episode-current"}}],
            search_responses=[[_episode("episode-other", material)]],
            default_search=[_episode("episode-other", material)],
            expected_status=FAILED,
            expected_reason="stale_or_foreign_hit",
        )
    )
    metadata_only = {
        "uuid": "episode-current",
        "content": "与本次运行无关的正文",
        "metadata": {"runId": material.run_id, "marker": material.marker},
    }
    scenarios.append(
        _graph_scenario(
            "graph-metadata-marker-only",
            material,
            add_responses=[{"episode": {"uuid": "episode-current"}}],
            search_responses=[[metadata_only]],
            default_search=[metadata_only],
            expected_status=FAILED,
            expected_reason="index_timeout",
        )
    )
    wrong_fact = {
        "uuid": "episode-current",
        "content": f"错误事实 {material.marker}",
        "metadata": {"runId": material.run_id, "marker": material.marker},
    }
    scenarios.append(
        _graph_scenario(
            "graph-content-marker-wrong-fact",
            material,
            add_responses=[{"episode": {"uuid": "episode-current"}}],
            search_responses=[[wrong_fact]],
            default_search=[wrong_fact],
            expected_status=FAILED,
            expected_reason="index_timeout",
        )
    )
    scenarios.append(
        _graph_scenario(
            "graph-wrong-uuid-before-exact",
            material,
            add_responses=[{"episode": {"uuid": "episode-current"}}],
            search_responses=[[_episode("episode-other", material), current]],
            expected_status=PASSED,
        )
    )
    independent_keys: list[str] = []
    for _ in range(2):
        run_clock = FakeClock()
        run_client = FakeGraphClient(
            add_responses=[{"episode": {"uuid": "episode-current"}}],
            search_responses=[[], [current]],
        )
        run_result = GraphRunner(
            _settings(),
            client_factory=lambda _: run_client,
            call_kind=DOUBLE,
            clock=run_clock,
            sleep=run_clock.sleep,
        ).run(material)
        independent_keys.append(str(run_result.evidence.get("idempotencyKey")))
    try:
        unique_key_ok = len(set(independent_keys)) == 2 and all(
            uuid.UUID(key).version == 4 and uuid.UUID(key).variant == uuid.RFC_4122
            for key in independent_keys
        )
    except (ValueError, TypeError):
        unique_key_ok = False
    scenarios.append(
        Scenario(
            "graph-idempotency-key-unique-runs",
            PASSED if unique_key_ok else FAILED,
            f"keys={independent_keys}",
            CallCounts(double=6),
        )
    )
    missing_graph = GraphRunner(
        _settings(zep_api_key=""), client_factory=lambda _: FakeGraphClient(add_responses=[]),
        call_kind=DOUBLE,
    ).run(material)
    scenarios.append(
        Scenario(
            "graph-missing-config",
            PASSED
            if missing_graph.status == NOT_RUN and missing_graph.reason == "missing_config"
            else FAILED,
            f"status={missing_graph.status} reason={missing_graph.reason}",
        )
    )

    for key in ("platform_a", "platform_b"):
        outcome = _run_fake_platform(key, material)
        expected_action = "create_post" if key == "platform_a" else "create_comment"
        ok = (
            outcome.item.status == PASSED
            and outcome.item.evidence.get("nativeAction") == expected_action
            and outcome.cleanup == "passed"
        )
        scenarios.append(
            Scenario(
                f"simulation-{key}-pass",
                PASSED if ok else FAILED,
                f"status={outcome.item.status} cleanup={outcome.cleanup}",
                outcome.item.calls,
            )
        )
    wrong = _run_fake_platform("platform_b", material, wrong_target=True)
    scenarios.append(
        Scenario(
            "simulation-platform-b-target-mismatch",
            PASSED
            if wrong.item.status == FAILED and wrong.item.reason == "platform_b_record"
            else FAILED,
            f"status={wrong.item.status} reason={wrong.item.reason}",
            wrong.item.calls,
        )
    )
    cleanup = _run_fake_platform("platform_a", material, fail_close=True)
    scenarios.append(
        Scenario(
            "simulation-cleanup-failure",
            PASSED
            if cleanup.item.status == PASSED and cleanup.cleanup == "failed"
            else FAILED,
            f"status={cleanup.item.status} cleanup={cleanup.cleanup}",
            cleanup.item.calls,
        )
    )
    shared = _run_fake_platform("platform_a", material, fail_factory=True)
    scenarios.append(
        Scenario(
            "simulation-platform-setup-failure",
            PASSED
            if shared.item.status == FAILED and shared.item.reason == "platform_a_setup"
            else FAILED,
            f"status={shared.item.status} reason={shared.item.reason}",
            shared.item.calls,
        )
    )

    def both_pass(key: str, settings: EngineSettings, mat: AcceptanceMaterial, root: Path) -> PlatformOutcome:
        return _platform_pass(key)

    def one_fail(key: str, settings: EngineSettings, mat: AcceptanceMaterial, root: Path) -> PlatformOutcome:
        return _platform_pass(key) if key == "platform_a" else _platform_fail(
            key, "platform_b_action"
        )

    def cleanup_fail(key: str, settings: EngineSettings, mat: AcceptanceMaterial, root: Path) -> PlatformOutcome:
        item = _platform_pass(key)
        if key == "platform_a":
            item.item.evidence["cleanup"] = "failed"
            return PlatformOutcome(item.item, "failed")
        return item

    def shared_fail(key: str, settings: EngineSettings, mat: AcceptanceMaterial, root: Path) -> PlatformOutcome:
        raise SharedSetupError("offline shared setup")

    smoke_all, _ = _smoke_case("smoke-all-pass", settings=_settings(), platform_runner=both_pass)
    scenarios.append(smoke_all)
    missing_zep, zep_items = _smoke_case(
        "smoke-missing-zep-keeps-oasis",
        settings=_settings(zep_api_key=""),
        platform_runner=both_pass,
    )
    zep_ok = (
        missing_zep.status == PASSED
        and zep_items["graph.searchable"]["status"] == NOT_RUN
        and zep_items["simulation.platform_a"]["status"] == PASSED
        and zep_items["simulation.platform_b"]["status"] == PASSED
    )
    scenarios.append(
        Scenario(
            "smoke-missing-zep-keeps-oasis",
            PASSED if zep_ok else FAILED,
            f"exit={missing_zep.detail} graph={zep_items['graph.searchable']['status']}",
            missing_zep.calls,
        )
    )
    missing_model_smoke, model_items = _smoke_case(
        "smoke-missing-model-keeps-oasis",
        settings=_settings(model_name=""),
        platform_runner=both_pass,
    )
    model_ok = (
        model_items["model.structured"]["status"] == NOT_RUN
        and model_items["simulation.platform_a"]["status"] == PASSED
        and model_items["simulation.platform_b"]["status"] == PASSED
    )
    scenarios.append(
        Scenario(
            "smoke-missing-model-keeps-oasis",
            PASSED if model_ok else FAILED,
            f"model={model_items['model.structured']['status']}",
            missing_model_smoke.calls,
        )
    )
    both_missing, both_items = _smoke_case(
        "smoke-missing-both-keeps-oasis",
        settings=_settings(model_api_key="", zep_api_key=""),
        platform_runner=both_pass,
    )
    both_ok = (
        both_items["model.structured"]["status"] == NOT_RUN
        and both_items["graph.searchable"]["status"] == NOT_RUN
        and both_items["simulation.platform_a"]["status"] == PASSED
        and both_items["simulation.platform_b"]["status"] == PASSED
    )
    scenarios.append(
        Scenario(
            "smoke-missing-both-keeps-oasis",
            PASSED if both_ok else FAILED,
            f"model={both_items['model.structured']['status']} graph={both_items['graph.searchable']['status']}",
            both_missing.calls,
        )
    )
    dirty, _ = _smoke_case(
        "smoke-dirty-refused",
        settings=_settings(),
        platform_runner=both_pass,
        dirty=True,
        allow_dirty=False,
    )
    scenarios.append(
        Scenario(
            "smoke-dirty-refused",
            PASSED if dirty.detail.startswith("exit=2") and dirty.calls.real == 0 else FAILED,
            dirty.detail,
            dirty.calls,
        )
    )
    one_fail_case, one_items = _smoke_case(
        "smoke-single-platform-fail",
        settings=_settings(),
        platform_runner=one_fail,
    )
    one_ok = (
        one_items["simulation.platform_a"]["status"] == PASSED
        and one_items["simulation.platform_b"]["status"] == FAILED
        and one_fail_case.detail.startswith("exit=1")
    )
    scenarios.append(
        Scenario(
            "smoke-single-platform-fail",
            PASSED if one_ok else FAILED,
            one_fail_case.detail,
            one_fail_case.calls,
        )
    )
    cleanup_case, cleanup_items = _smoke_case(
        "smoke-cleanup-failure",
        settings=_settings(),
        platform_runner=cleanup_fail,
    )
    cleanup_ok = (
        cleanup_items["cleanup"]["status"] == FAILED
        and cleanup_items["simulation.platform_a"]["status"] == PASSED
    )
    scenarios.append(
        Scenario(
            "smoke-cleanup-failure",
            PASSED if cleanup_ok else FAILED,
            f"cleanup={cleanup_items['cleanup']['status']}",
            cleanup_case.calls,
        )
    )
    shared_case, shared_items = _smoke_case(
        "smoke-shared-setup",
        settings=_settings(),
        platform_runner=shared_fail,
    )
    shared_ok = (
        shared_items["simulation.platform_a"]["status"] == NOT_RUN
        and shared_items["simulation.platform_b"]["status"] == NOT_RUN
    )
    scenarios.append(
        Scenario(
            "smoke-shared-setup",
            PASSED if shared_ok else FAILED,
            f"a={shared_items['simulation.platform_a']['status']} b={shared_items['simulation.platform_b']['status']}",
            shared_case.calls,
        )
    )

    total_calls = merge_calls(
        [
            ItemResult("scenario", "offline", "contract", PASSED, None, 0, s.calls, {})
            for s in scenarios
        ]
    )
    return {
        "schemaVersion": 1,
        "mode": "offline-contract",
        "result": PASSED if all(s.status == PASSED for s in scenarios) else FAILED,
        "calls": total_calls.to_dict(),
        "scenarios": [s.to_dict() for s in scenarios],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EchoSim 引擎离线契约检查")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    payload = run_offline()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["result"] == PASSED and payload["calls"]["real"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
