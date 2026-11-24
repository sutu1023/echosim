"""引擎运行配置：仅使用标准库，供隔离引擎镜像读取。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

# 这些默认值同时被 app/config.py 引用，避免应用配置与引擎配置漂移。
DEFAULT_MODEL_API_BASE_URL = ""
DEFAULT_MODEL_API_KEY = ""
DEFAULT_MODEL_NAME = ""
DEFAULT_MODEL_TIMEOUT_SECONDS = 30.0
DEFAULT_ZEP_API_KEY = ""
DEFAULT_ZEP_API_BASE_URL = ""
DEFAULT_ZEP_ACCEPTANCE_GRAPH_ID = ""
DEFAULT_GRAPH_WRITE_TIMEOUT_SECONDS = 20.0
DEFAULT_GRAPH_STATUS_TIMEOUT_SECONDS = 10.0
DEFAULT_GRAPH_INDEX_TIMEOUT_SECONDS = 60.0
DEFAULT_GRAPH_POLL_INTERVAL_SECONDS = 3.0
DEFAULT_SIMULATION_TIMEOUT_SECONDS = 60.0
DEFAULT_SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS = 300.0

MODEL_ENV_VARS = (
    "MODEL_API_BASE_URL",
    "MODEL_API_KEY",
    "MODEL_NAME",
)
GRAPH_ENV_VARS = (
    "ZEP_API_KEY",
    "ZEP_ACCEPTANCE_GRAPH_ID",
)
ENGINE_ENV_VARS = MODEL_ENV_VARS + GRAPH_ENV_VARS + (
    "ZEP_API_BASE_URL",
    "MODEL_TIMEOUT_SECONDS",
    "GRAPH_WRITE_TIMEOUT_SECONDS",
    "GRAPH_STATUS_TIMEOUT_SECONDS",
    "GRAPH_INDEX_TIMEOUT_SECONDS",
    "GRAPH_POLL_INTERVAL_SECONDS",
    "SIMULATION_TIMEOUT_SECONDS",
    "SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS",
)


def _text(env: Mapping[str, str], key: str, default: str = "") -> str:
    value = env.get(key)
    if value is None:
        return default
    return str(value).strip()


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return False
    return (
        lowered.startswith("replace-with-")
        or "example.invalid" in lowered
        or lowered == "00000000-0000-0000-0000-000000000000"
    )


def _configured(value: str) -> bool:
    return bool(value) and not _is_placeholder(value)


def is_placeholder(value: str) -> bool:
    """公开的占位值判断，供业务建图配置复用。"""
    return _is_placeholder(value)


def is_configured(value: str) -> bool:
    """公开的“非空且非占位”判断，供业务建图配置复用。"""
    return _configured(value)


def _number(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _text(env, key)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class EngineSettings:
    """引擎运行所需配置；密钥字段不进入 repr。"""

    model_api_base_url: str = DEFAULT_MODEL_API_BASE_URL
    model_api_key: str = field(default=DEFAULT_MODEL_API_KEY, repr=False)
    model_name: str = DEFAULT_MODEL_NAME
    model_timeout_seconds: float = DEFAULT_MODEL_TIMEOUT_SECONDS
    zep_api_key: str = field(default=DEFAULT_ZEP_API_KEY, repr=False)
    zep_api_base_url: str = DEFAULT_ZEP_API_BASE_URL
    zep_acceptance_graph_id: str = DEFAULT_ZEP_ACCEPTANCE_GRAPH_ID
    graph_write_timeout_seconds: float = DEFAULT_GRAPH_WRITE_TIMEOUT_SECONDS
    graph_status_timeout_seconds: float = DEFAULT_GRAPH_STATUS_TIMEOUT_SECONDS
    graph_index_timeout_seconds: float = DEFAULT_GRAPH_INDEX_TIMEOUT_SECONDS
    graph_poll_interval_seconds: float = DEFAULT_GRAPH_POLL_INTERVAL_SECONDS
    simulation_timeout_seconds: float = DEFAULT_SIMULATION_TIMEOUT_SECONDS
    smoke_live_total_timeout_seconds: float = DEFAULT_SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "EngineSettings":
        env = os.environ if environ is None else environ
        return cls(
            model_api_base_url=_text(env, "MODEL_API_BASE_URL"),
            model_api_key=_text(env, "MODEL_API_KEY"),
            model_name=_text(env, "MODEL_NAME"),
            model_timeout_seconds=_number(
                env, "MODEL_TIMEOUT_SECONDS", DEFAULT_MODEL_TIMEOUT_SECONDS
            ),
            zep_api_key=_text(env, "ZEP_API_KEY"),
            zep_api_base_url=_text(env, "ZEP_API_BASE_URL"),
            zep_acceptance_graph_id=_text(env, "ZEP_ACCEPTANCE_GRAPH_ID"),
            graph_write_timeout_seconds=_number(
                env, "GRAPH_WRITE_TIMEOUT_SECONDS", DEFAULT_GRAPH_WRITE_TIMEOUT_SECONDS
            ),
            graph_status_timeout_seconds=_number(
                env,
                "GRAPH_STATUS_TIMEOUT_SECONDS",
                DEFAULT_GRAPH_STATUS_TIMEOUT_SECONDS,
            ),
            graph_index_timeout_seconds=_number(
                env, "GRAPH_INDEX_TIMEOUT_SECONDS", DEFAULT_GRAPH_INDEX_TIMEOUT_SECONDS
            ),
            graph_poll_interval_seconds=_number(
                env, "GRAPH_POLL_INTERVAL_SECONDS", DEFAULT_GRAPH_POLL_INTERVAL_SECONDS
            ),
            simulation_timeout_seconds=_number(
                env, "SIMULATION_TIMEOUT_SECONDS", DEFAULT_SIMULATION_TIMEOUT_SECONDS
            ),
            smoke_live_total_timeout_seconds=_number(
                env,
                "SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS",
                DEFAULT_SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS,
            ),
        )

    @property
    def missing_model(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not _configured(self.model_api_base_url):
            missing.append("MODEL_API_BASE_URL")
        if not _configured(self.model_api_key):
            missing.append("MODEL_API_KEY")
        if not _configured(self.model_name):
            missing.append("MODEL_NAME")
        return tuple(missing)

    @property
    def missing_graph(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not _configured(self.zep_api_key):
            missing.append("ZEP_API_KEY")
        if not _configured(self.zep_acceptance_graph_id):
            missing.append("ZEP_ACCEPTANCE_GRAPH_ID")
        return tuple(missing)

    def missing_for(self, dependency: str) -> tuple[str, ...]:
        if dependency == "model":
            return self.missing_model
        if dependency == "graph":
            return self.missing_graph
        return ()

    def public_dict(self) -> dict[str, object]:
        """只包含可写入报告的非敏感字段。"""
        return {
            "modelConfigured": not self.missing_model,
            "graphConfigured": not self.missing_graph,
            "modelTimeoutSeconds": self.model_timeout_seconds,
            "graphWriteTimeoutSeconds": self.graph_write_timeout_seconds,
            "graphStatusTimeoutSeconds": self.graph_status_timeout_seconds,
            "graphIndexTimeoutSeconds": self.graph_index_timeout_seconds,
            "graphPollIntervalSeconds": self.graph_poll_interval_seconds,
            "simulationTimeoutSeconds": self.simulation_timeout_seconds,
            "smokeLiveTotalTimeoutSeconds": self.smoke_live_total_timeout_seconds,
        }
