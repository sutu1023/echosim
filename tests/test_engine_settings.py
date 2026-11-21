"""引擎配置：缺配置按依赖分别判定，不读取真实 .env。"""

from __future__ import annotations

import unittest

from app.engines.settings import (
    DEFAULT_GRAPH_INDEX_TIMEOUT_SECONDS,
    DEFAULT_MODEL_TIMEOUT_SECONDS,
    EngineSettings,
)


class EngineSettingsTests(unittest.TestCase):
    def test_all_missing(self) -> None:
        settings = EngineSettings.from_env({})

        self.assertEqual(
            settings.missing_model,
            ("MODEL_API_BASE_URL", "MODEL_API_KEY", "MODEL_NAME"),
        )
        self.assertEqual(
            settings.missing_graph,
            ("ZEP_API_KEY", "ZEP_ACCEPTANCE_GRAPH_ID"),
        )
        self.assertEqual(settings.model_timeout_seconds, DEFAULT_MODEL_TIMEOUT_SECONDS)
        self.assertEqual(
            settings.graph_index_timeout_seconds, DEFAULT_GRAPH_INDEX_TIMEOUT_SECONDS
        )

    def test_only_model_missing(self) -> None:
        settings = EngineSettings.from_env(
            {"ZEP_API_KEY": "z", "ZEP_ACCEPTANCE_GRAPH_ID": "g"}
        )

        self.assertTrue(settings.missing_model)
        self.assertFalse(settings.missing_graph)

    def test_only_graph_missing(self) -> None:
        settings = EngineSettings.from_env(
            {
                "MODEL_API_BASE_URL": "https://model.test.local/v1",
                "MODEL_API_KEY": "k",
                "MODEL_NAME": "m",
            }
        )

        self.assertFalse(settings.missing_model)
        self.assertTrue(settings.missing_graph)

    def test_empty_strings_are_missing(self) -> None:
        settings = EngineSettings.from_env(
            {
                "MODEL_API_BASE_URL": "",
                "MODEL_API_KEY": " ",
                "MODEL_NAME": "",
                "ZEP_API_KEY": "",
                "ZEP_ACCEPTANCE_GRAPH_ID": "",
            }
        )

        self.assertEqual(len(settings.missing_model), 3)
        self.assertEqual(len(settings.missing_graph), 2)

    def test_placeholders_are_treated_as_missing(self) -> None:
        settings = EngineSettings.from_env(
            {
                "MODEL_API_BASE_URL": "https://model.example.invalid/v1",
                "MODEL_API_KEY": "replace-with-model-key",
                "MODEL_NAME": "replace-with-model-name",
                "ZEP_API_KEY": "replace-with-zep-key",
                "ZEP_ACCEPTANCE_GRAPH_ID": "00000000-0000-0000-0000-000000000000",
            }
        )

        self.assertEqual(len(settings.missing_model), 3)
        self.assertEqual(len(settings.missing_graph), 2)

    def test_public_dict_excludes_secrets(self) -> None:
        settings = EngineSettings.from_env(
            {
                "MODEL_API_BASE_URL": "https://model.test.local/v1",
                "MODEL_API_KEY": "super-secret",
                "MODEL_NAME": "model",
                "ZEP_API_KEY": "zep-secret",
                "ZEP_ACCEPTANCE_GRAPH_ID": "graph",
            }
        )

        payload = settings.public_dict()

        self.assertNotIn("super-secret", str(payload))
        self.assertNotIn("zep-secret", str(payload))
        self.assertTrue(payload["modelConfigured"])
        self.assertTrue(payload["graphConfigured"])


if __name__ == "__main__":
    unittest.main()
