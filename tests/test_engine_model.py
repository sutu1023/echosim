"""Chat Completions 模型适配器：路径、json_object、预算与失败分类。"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.engines.baseline import DependencyBaseline, PINS, StageResult
from app.engines.contracts import (
    CallCounts,
    ItemResult,
    PASSED,
    build_acceptance_material,
    utf16_len,
)
from app.engines.model import (
    HttpResponse,
    ModelRunner,
    build_chat_completions_url,
)
from app.engines.settings import EngineSettings
from app.engines.simulation import PlatformOutcome
from app.engines.smoke import run_smoke


def settings(**overrides: Any) -> EngineSettings:
    values: dict[str, Any] = {
        "model_api_base_url": "https://model.test.local/v1",
        "model_api_key": "secret-model-key",
        "model_name": "demo-model",
        "model_timeout_seconds": 30.0,
    }
    values.update(overrides)
    return EngineSettings(**values)


def valid_body(material: Any) -> str:
    index = material.text.index(material.marker)
    start = utf16_len(material.text[:index])
    end = start + utf16_len(material.marker)
    content = json.dumps(
        {
            "materialId": material.material_id,
            "summary": "社区图书角收到蓝色书箱。",
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
    return json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False)


class SequenceTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def __call__(
        self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> HttpResponse:
        self.requests.append((url, payload))
        if not self.responses:
            raise AssertionError("响应不足")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    responses: list[tuple[int, str]] = []
    requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        self.__class__.requests.append(
            {
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": json.loads(raw),
            }
        )
        status, body = self.__class__.responses.pop(0)
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_: object) -> None:
        return None


@contextlib.contextmanager
def local_model_server(responses: list[tuple[int, str]]):
    class Handler(RecordingHandler):
        pass

    Handler.responses = list(responses)
    Handler.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.dict(
            os.environ,
            {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
        ):
            yield Handler, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class UrlTests(unittest.TestCase):
    def test_base_with_v1_gets_single_endpoint(self) -> None:
        self.assertEqual(
            build_chat_completions_url("https://host.example/v1"),
            "https://host.example/v1/chat/completions",
        )

    def test_base_without_v1_gets_single_endpoint(self) -> None:
        self.assertEqual(
            build_chat_completions_url("https://host.example"),
            "https://host.example/chat/completions",
        )

    def test_existing_endpoint_is_not_duplicated(self) -> None:
        self.assertEqual(
            build_chat_completions_url("https://host.example/v1/chat/completions"),
            "https://host.example/v1/chat/completions",
        )


class ModelRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.material = build_acceptance_material("model-run")

    def test_go_session_header_is_scoped_to_the_subscription_endpoint(self) -> None:
        for base, expected in (
            ("https://opencode.ai/inference/go/openai/v1", f"ses_{self.material.run_id}"),
            ("https://opencode.ai/inference/openai/v1", None),
            ("https://model.test.local/v1", None),
        ):
            with self.subTest(base=base):
                sessions = []
                responses = iter([HttpResponse(429), HttpResponse(200, valid_body(self.material))])

                def record(url, headers, payload, timeout):
                    sessions.append(headers.get("x-opencode-session"))
                    return next(responses)

                result = ModelRunner(
                    settings(model_api_base_url=base), transport=record, sleep=lambda _: None
                ).run(self.material)
                self.assertEqual(result.status, "passed")
                self.assertEqual(sessions, [expected, expected])

    def test_first_request_uses_json_object_and_model(self) -> None:
        transport = SequenceTransport([HttpResponse(200, valid_body(self.material))])
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.status, "passed")
        url, payload = transport.requests[0]
        self.assertEqual(url, "https://model.test.local/v1/chat/completions")
        self.assertEqual(payload["model"], "demo-model")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("secret-model-key", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_default_transport_sends_user_agent_bearer_path_and_json(self) -> None:
        with local_model_server([(200, valid_body(self.material))]) as (handler, base_url):
            result = ModelRunner(
                settings(
                    model_api_base_url=base_url,
                    model_api_key="local-secret",
                    model_name="demo-model",
                )
            ).run(self.material)

        self.assertEqual(result.status, "passed")
        request = handler.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["headers"]["user-agent"], "EchoSim/0.1.0")
        self.assertEqual(request["headers"]["authorization"], "Bearer local-secret")
        self.assertEqual(request["body"]["model"], "demo-model")
        self.assertEqual(request["body"]["response_format"], {"type": "json_object"})

    def test_user_agent_is_kept_on_response_format_fallback(self) -> None:
        with local_model_server(
            [(400, "response_format is not supported"), (200, valid_body(self.material))]
        ) as (handler, base_url):
            result = ModelRunner(settings(model_api_base_url=base_url)).run(self.material)

        self.assertEqual(result.status, "passed")
        self.assertEqual(len(handler.requests), 2)
        self.assertTrue(
            all(request["headers"]["user-agent"] == "EchoSim/0.1.0" for request in handler.requests)
        )
        self.assertIn("response_format", handler.requests[0]["body"])
        self.assertNotIn("response_format", handler.requests[1]["body"])

    def test_user_agent_is_kept_on_retry(self) -> None:
        with local_model_server(
            [(429, "slow down"), (200, valid_body(self.material))]
        ) as (handler, base_url):
            result = ModelRunner(
                settings(model_api_base_url=base_url), sleep=lambda _: None
            ).run(self.material)

        self.assertEqual(result.status, "passed")
        self.assertEqual(len(handler.requests), 2)
        self.assertTrue(
            all(request["headers"]["user-agent"] == "EchoSim/0.1.0" for request in handler.requests)
        )

    def test_200_invalid_json_is_not_success(self) -> None:
        transport = SequenceTransport([HttpResponse(200, "not-json")])
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "invalid_output")

    def test_response_format_unsupported_retries_without_parameter(self) -> None:
        transport = SequenceTransport(
            [
                HttpResponse(400, "response_format is not supported"),
                HttpResponse(200, valid_body(self.material)),
            ]
        )
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.status, "passed")
        self.assertTrue(result.evidence["fallbackUsed"])
        self.assertEqual(result.calls.real, 2)
        self.assertIn("response_format", transport.requests[0][1])
        self.assertNotIn("response_format", transport.requests[1][1])

    def test_auth_failure_is_not_retried(self) -> None:
        transport = SequenceTransport([HttpResponse(401, "unauthorized")])
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "auth_failed")
        self.assertEqual(result.evidence["httpStatus"], 401)
        self.assertEqual(result.evidence["httpStatusClass"], "4xx")
        self.assertEqual(result.calls.real, 1)

    def test_402_is_billing_required_and_not_retried(self) -> None:
        body = json.dumps(
            {"error": {"code": "insufficient_quota", "message": "balance and payment method required"}}
        )
        transport = SequenceTransport([HttpResponse(402, body)])
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "billing_required")
        self.assertEqual(result.evidence["httpStatus"], 402)
        self.assertEqual(result.evidence["providerErrorCode"], "insufficient_quota")
        self.assertEqual(result.calls.real, 1)

    def test_403_cloudflare_1010_is_edge_blocked_and_not_retried(self) -> None:
        body = json.dumps({"error": {"code": 1010, "message": "cloudflare edge policy"}})
        transport = SequenceTransport([HttpResponse(403, body)])
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "edge_blocked")
        self.assertEqual(result.evidence["httpStatus"], 403)
        self.assertEqual(result.evidence["providerErrorCode"], "1010")
        self.assertEqual(result.calls.real, 1)

    def test_unknown_provider_error_code_is_not_recorded_or_serialized(self) -> None:
        fake_key = "sk-fake-echoed-provider-code-123"
        body = json.dumps({"error": {"code": fake_key, "message": "denied"}})
        transport = SequenceTransport([HttpResponse(401, body)])
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.reason, "auth_failed")
        self.assertIsNone(result.evidence["providerErrorCode"])
        serialized = json.dumps({"items": [result.to_dict()]}, ensure_ascii=False)
        self.assertNotIn(fake_key, serialized)

        def verified_baseline() -> DependencyBaseline:
            stages = {
                name: StageResult("passed", "offline", 0, {})
                for name in ("resolve", "install", "import", "minimalRun")
            }
            return DependencyBaseline("verified", stages, dict(PINS), "offline", "3.11")

        def graph_pass(_material: Any) -> ItemResult:
            return ItemResult(
                "graph.searchable",
                "graph",
                "episode-search",
                PASSED,
                None,
                0,
                CallCounts(double=1),
                {"accepted": True, "episodeId": "episode-test"},
            )

        def platform_pass(key: str, *_: Any) -> PlatformOutcome:
            return PlatformOutcome(
                ItemResult(
                    f"simulation.{key}",
                    "simulation",
                    "offline",
                    PASSED,
                    None,
                    0,
                    CallCounts(double=1),
                    {"cleanup": "passed"},
                ),
                "passed",
            )

        with tempfile.TemporaryDirectory() as tmp:
            outcome = run_smoke(
                settings=settings(model_api_key="fake-model-key"),
                report_dir=Path(tmp),
                tested_sha="offline-test",
                dirty=False,
                baseline_evaluator=verified_baseline,
                model_runner=lambda _material: result,
                graph_runner=graph_pass,
                platform_runner=platform_pass,
            )
        serialized_report = json.dumps(outcome.report, ensure_ascii=False)
        self.assertNotIn(fake_key, serialized_report)

    def test_403_with_1010_only_in_unrelated_metadata_stays_auth_failed(self) -> None:
        body = json.dumps(
            {
                "error": {"code": "forbidden", "message": "denied"},
                "metadata": {"code": 1010, "note": "1010 is unrelated metadata"},
            }
        )
        transport = SequenceTransport([HttpResponse(403, body)])
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "auth_failed")
        self.assertEqual(result.evidence["httpStatus"], 403)
        self.assertIsNone(result.evidence["providerErrorCode"])
        self.assertEqual(result.calls.real, 1)

    def test_403_postal_code_is_not_an_edge_error(self) -> None:
        for body in (
            "Forbidden. Postal code: 1010",
            json.dumps({
                "error": {"code": "forbidden"},
                "metadata": {"gateway": "Cloudflare", "note": "Postal code: 1010"},
            }),
        ):
            with self.subTest(body=body):
                transport = SequenceTransport([HttpResponse(403, body)])
                result = ModelRunner(settings(), transport=transport).run(self.material)
                self.assertEqual(result.reason, "auth_failed")
                self.assertEqual(result.evidence["httpStatus"], 403)
                self.assertIsNone(result.evidence["providerErrorCode"])
                self.assertEqual(len(transport.requests), 1)

    def test_403_explicit_text_error_code_matches_report(self) -> None:
        transport = SequenceTransport([HttpResponse(403, "error code: 1010")])
        result = ModelRunner(settings(), transport=transport).run(self.material)
        self.assertEqual(result.reason, "edge_blocked")
        self.assertEqual(result.evidence["providerErrorCode"], "1010")
        self.assertEqual(len(transport.requests), 1)

    def test_ordinary_403_keeps_auth_failed(self) -> None:
        transport = SequenceTransport([HttpResponse(403, "forbidden")])
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "auth_failed")
        self.assertEqual(result.evidence["httpStatus"], 403)
        self.assertEqual(result.calls.real, 1)

    def test_rate_limit_retries_once(self) -> None:
        transport = SequenceTransport(
            [HttpResponse(429, "slow down"), HttpResponse(200, valid_body(self.material))]
        )
        result = ModelRunner(
            settings(), transport=transport, sleep=lambda _: None
        ).run(self.material)

        self.assertEqual(result.status, "passed")
        self.assertTrue(result.evidence["retryUsed"])
        self.assertEqual(result.calls.real, 2)

    def test_timeout_is_failure(self) -> None:
        transport = SequenceTransport(
            [HttpResponse(0, "", "TimeoutError"), HttpResponse(0, "", "TimeoutError")]
        )
        result = ModelRunner(
            settings(), transport=transport, sleep=lambda _: None
        ).run(self.material)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "timeout")
        self.assertEqual(result.calls.real, 2)

    def test_missing_config_does_not_call(self) -> None:
        transport = SequenceTransport([])
        result = ModelRunner(settings(model_api_key=""), transport=transport).run(
            self.material
        )

        self.assertEqual(result.status, "not_run")
        self.assertEqual(result.reason, "missing_config")
        self.assertEqual(transport.requests, [])


if __name__ == "__main__":
    unittest.main()
