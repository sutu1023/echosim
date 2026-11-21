"""Chat Completions 模型适配器：路径、json_object、预算与失败分类。"""

from __future__ import annotations

import json
import unittest
from typing import Any

from app.engines.contracts import build_acceptance_material, utf16_len
from app.engines.model import (
    HttpResponse,
    ModelRunner,
    build_chat_completions_url,
)
from app.engines.settings import EngineSettings


def settings(**overrides: Any) -> EngineSettings:
    values: dict[str, Any] = {
        "model_api_base_url": "https://model.example.invalid/v1",
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

    def test_first_request_uses_json_object_and_model(self) -> None:
        transport = SequenceTransport([HttpResponse(200, valid_body(self.material))])
        result = ModelRunner(settings(), transport=transport).run(self.material)

        self.assertEqual(result.status, "passed")
        url, payload = transport.requests[0]
        self.assertEqual(url, "https://model.example.invalid/v1/chat/completions")
        self.assertEqual(payload["model"], "demo-model")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("secret-model-key", json.dumps(result.to_dict(), ensure_ascii=False))

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
