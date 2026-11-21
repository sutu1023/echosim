"""引擎配置在 app/config.py、.env.example、Compose 与 README 中保持一致。"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.config import Settings
from app.engines.settings import (
    DEFAULT_GRAPH_INDEX_TIMEOUT_SECONDS,
    DEFAULT_MODEL_TIMEOUT_SECONDS,
    DEFAULT_SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS,
    EngineSettings,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_VARS = (
    "MODEL_API_BASE_URL",
    "MODEL_API_KEY",
    "MODEL_NAME",
    "MODEL_TIMEOUT_SECONDS",
    "ZEP_API_KEY",
    "ZEP_API_BASE_URL",
    "ZEP_ACCEPTANCE_GRAPH_ID",
    "GRAPH_WRITE_TIMEOUT_SECONDS",
    "GRAPH_STATUS_TIMEOUT_SECONDS",
    "GRAPH_INDEX_TIMEOUT_SECONDS",
    "GRAPH_POLL_INTERVAL_SECONDS",
    "SIMULATION_TIMEOUT_SECONDS",
    "SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS",
)


class EngineConfigConsistencyTests(unittest.TestCase):
    def test_all_vars_exist_in_env_example_and_compose(self) -> None:
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        for name in ENGINE_VARS:
            with self.subTest(name=name):
                self.assertIn(f"{name}=", env_example)
                self.assertIn(f"{name}: ${{{name}:-", compose)
                self.assertIn(f"`{name}`", readme)

    def test_defaults_match_engine_settings(self) -> None:
        app_settings = Settings()
        engine_settings = EngineSettings.from_env({})

        self.assertEqual(
            app_settings.model_timeout_seconds, engine_settings.model_timeout_seconds
        )
        self.assertEqual(
            app_settings.graph_index_timeout_seconds,
            engine_settings.graph_index_timeout_seconds,
        )
        self.assertEqual(
            app_settings.smoke_live_total_timeout_seconds,
            engine_settings.smoke_live_total_timeout_seconds,
        )
        self.assertEqual(
            engine_settings.model_timeout_seconds, DEFAULT_MODEL_TIMEOUT_SECONDS
        )
        self.assertEqual(
            engine_settings.graph_index_timeout_seconds,
            DEFAULT_GRAPH_INDEX_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            engine_settings.smoke_live_total_timeout_seconds,
            DEFAULT_SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
