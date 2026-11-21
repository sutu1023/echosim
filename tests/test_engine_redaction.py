"""引擎报告脱敏：密钥、Authorization 与 URL 凭据不进入输出。"""

from __future__ import annotations

import unittest

from app.engines.redaction import (
    contains_secret,
    redact_text,
    redact_url,
    sanitize_mapping,
)


class RedactionTests(unittest.TestCase):
    def test_text_and_mapping_redact_secret(self) -> None:
        payload = {
            "model_api_key": "unique-secret",
            "authorization": "Bearer unique-secret",
            "nested": {"value": "prefix unique-secret suffix"},
        }

        cleaned = sanitize_mapping(payload, ["unique-secret"])

        self.assertFalse(contains_secret(cleaned, "unique-secret"))
        self.assertEqual(cleaned["model_api_key"], "[redacted]")
        self.assertEqual(cleaned["authorization"], "[redacted]")

    def test_url_drops_query_and_userinfo(self) -> None:
        self.assertEqual(
            redact_url("https://user:pass@example.invalid/v1?api_key=secret#frag"),
            "https://example.invalid/v1",
        )

    def test_redact_text_is_idempotent(self) -> None:
        self.assertEqual(redact_text("a-secret-b", ["secret"]), "a-[redacted]-b")


if __name__ == "__main__":
    unittest.main()
