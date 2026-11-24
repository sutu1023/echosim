"""角色画像 provider 配置：只决定“用真实模型、替身还是未配置”。

与业务建图复用同一套 provider 语义：`auto` 只在凭据完整时解析为真实 provider，
显式 `stub` 必须同时打开允许开关，未配置时不得静默回退到替身。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Mapping

from .environment_contracts import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_MAX_MANUAL_ROUNDS,
    DEFAULT_MINUTES_PER_ROUND,
    DEFAULT_MODEL_TIMEOUT_SECONDS,
    DEFAULT_PROFILE_BATCH_SIZE,
)
from .settings import is_configured

DEFAULT_ENVIRONMENT_MODEL_PROVIDER = "auto"
DEFAULT_ENVIRONMENT_ALLOW_STUB = False

ENVIRONMENT_ENV_VARS = (
    "ENVIRONMENT_MODEL_PROVIDER",
    "ENVIRONMENT_ALLOW_STUB",
    "ENVIRONMENT_DEFAULT_DURATION_HOURS",
    "ENVIRONMENT_DEFAULT_MINUTES_PER_ROUND",
    "ENVIRONMENT_MAX_MANUAL_ROUNDS",
    "ENVIRONMENT_PROFILE_BATCH_SIZE",
    "ENVIRONMENT_MODEL_TIMEOUT_SECONDS",
    "ENVIRONMENT_PROFILE_DELAY_MS",
)
MODEL_PROVIDER_VALUES = ("auto", "unconfigured", "http", "stub")


class EnvironmentConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _text(env: Mapping[str, str], key: str, default: str = "") -> str:
    value = env.get(key)
    return default if value is None else str(value).strip()


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _text(env, key).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _text(env, key)
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _text(env, key)
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


@dataclass(frozen=True)
class EnvironmentConfig:
    model_provider: str = DEFAULT_ENVIRONMENT_MODEL_PROVIDER
    allow_stub: bool = DEFAULT_ENVIRONMENT_ALLOW_STUB
    default_duration_hours: int = DEFAULT_DURATION_HOURS
    default_minutes_per_round: int = DEFAULT_MINUTES_PER_ROUND
    max_manual_rounds: int = DEFAULT_MAX_MANUAL_ROUNDS
    profile_batch_size: int = DEFAULT_PROFILE_BATCH_SIZE
    model_timeout_seconds: float = DEFAULT_MODEL_TIMEOUT_SECONDS
    profile_delay_ms: int = 0
    model_api_base_url: str = ""
    model_api_key: str = ""
    model_name: str = ""
    model_configured: bool = False

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "EnvironmentConfig":
        env = os.environ if environ is None else environ
        base_url = _text(env, "MODEL_API_BASE_URL")
        api_key = _text(env, "MODEL_API_KEY")
        model_name = _text(env, "MODEL_NAME")
        return cls(
            model_provider=_text(
                env,
                "ENVIRONMENT_MODEL_PROVIDER",
                DEFAULT_ENVIRONMENT_MODEL_PROVIDER,
            ),
            allow_stub=_bool(
                env,
                "ENVIRONMENT_ALLOW_STUB",
                DEFAULT_ENVIRONMENT_ALLOW_STUB,
            ),
            default_duration_hours=_int(
                env, "ENVIRONMENT_DEFAULT_DURATION_HOURS", DEFAULT_DURATION_HOURS
            ),
            default_minutes_per_round=_int(
                env,
                "ENVIRONMENT_DEFAULT_MINUTES_PER_ROUND",
                DEFAULT_MINUTES_PER_ROUND,
            ),
            max_manual_rounds=_int(
                env, "ENVIRONMENT_MAX_MANUAL_ROUNDS", DEFAULT_MAX_MANUAL_ROUNDS
            ),
            profile_batch_size=_int(
                env, "ENVIRONMENT_PROFILE_BATCH_SIZE", DEFAULT_PROFILE_BATCH_SIZE
            ),
            model_timeout_seconds=_float(
                env,
                "ENVIRONMENT_MODEL_TIMEOUT_SECONDS",
                DEFAULT_MODEL_TIMEOUT_SECONDS,
            ),
            profile_delay_ms=_int(
                env, "ENVIRONMENT_PROFILE_DELAY_MS", 0
            )
            if _text(env, "ENVIRONMENT_PROFILE_DELAY_MS")
            else 0,
            model_api_base_url=base_url,
            model_api_key=api_key,
            model_name=model_name,
            model_configured=all(
                is_configured(value) for value in (base_url, api_key, model_name)
            ),
        )

    def resolve_provider(self) -> str:
        requested = (self.model_provider or "auto").strip().lower()
        if requested not in MODEL_PROVIDER_VALUES:
            raise EnvironmentConfigError("invalid_provider", "未知画像 provider")
        if requested == "auto":
            return "http" if self.model_configured else "unconfigured"
        if requested == "unconfigured":
            return "unconfigured"
        if requested == "stub":
            if not self.allow_stub:
                raise EnvironmentConfigError(
                    "stub_not_allowed", "测试替身未显式允许，拒绝启用"
                )
            return "stub"
        return "http" if self.model_configured else "unconfigured"

    def public_dict(self) -> dict[str, object]:
        return {
            "modelProvider": self.model_provider,
            "allowStub": self.allow_stub,
            "defaultDurationHours": self.default_duration_hours,
            "defaultMinutesPerRound": self.default_minutes_per_round,
            "maxManualRounds": self.max_manual_rounds,
            "profileBatchSize": self.profile_batch_size,
            "modelTimeoutSeconds": self.model_timeout_seconds,
            "profileDelayMs": self.profile_delay_ms,
            "modelConfigured": self.model_configured,
        }
