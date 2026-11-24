"""双平台运行配置：引擎模式、运行目录、轮次预算与验收故障注入。

默认使用确定性替身引擎，真实本地 OASIS 必须显式开启；配置不读密钥，
也不在缺少引擎依赖时静默回退。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .duet_contracts import DEFAULT_ACTIONS_PER_ROUND, DEFAULT_MAX_ACTIONS

DEFAULT_SIMULATION_ENGINE = "double"
DEFAULT_SIMULATION_ALLOW_OASIS = False
DEFAULT_SIMULATION_DATA_DIR = "data/simulations"
DEFAULT_ROUND_TIMEOUT_SECONDS = 120.0
DEFAULT_ROUND_DELAY_MS = 0

DUET_ENV_VARS = (
    "SIMULATION_ENGINE",
    "SIMULATION_ALLOW_OASIS",
    "SIMULATION_DATA_DIR",
    "SIMULATION_ACTIONS_PER_ROUND",
    "SIMULATION_ROUND_TIMEOUT_SECONDS",
    "SIMULATION_ROUND_DELAY_MS",
    "SIMULATION_MAX_ACTIONS",
    "SIMULATION_FAULT",
    "SIMULATION_ENGINE_COMMAND",
    "SIMULATION_ENGINE_RUNTIME",
    "SIMULATION_KEEP_RUNTIME",
    "SIMULATION_PLATFORM_DELAY_MS",
    "SIMULATION_CLEANUP_FAIL",
    "WORKER_RUN_KINDS",
    "WORKER_SIMULATION_ENGINE",
)

SIMULATION_ENGINES = ("double", "oasis")


class DuetConfigError(ValueError):
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
    return raw in {"1", "true", "yes", "on"}


def _int(env: Mapping[str, str], key: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(_text(env, key))
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        value = float(_text(env, key))
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def parse_platform_delays(raw: str) -> dict[str, int]:
    """`platform_a:1500,platform_b:0` → {platformKey: 毫秒}；仅验收使用。"""
    delays: dict[str, int] = {}
    for item in (raw or "").split(","):
        parts = [part.strip() for part in item.split(":")]
        if len(parts) != 2 or not parts[0] or not parts[1].isdigit():
            continue
        delays[parts[0]] = min(int(parts[1]), 600000)
    return delays


def parse_fault(raw: str) -> dict[str, tuple[str, str]]:
    """`platform_a:1:after_first_action` → {platform: (round, mode)}。"""
    faults: dict[str, tuple[str, str]] = {}
    for item in (raw or "").split(","):
        parts = [part.strip() for part in item.split(":")]
        if len(parts) != 3 or not parts[0]:
            continue
        if not parts[1].isdigit():
            continue
        faults[parts[0]] = (parts[1], parts[2] or "none")
    return faults


@dataclass(frozen=True)
class DuetConfig:
    engine: str = DEFAULT_SIMULATION_ENGINE
    allow_oasis: bool = DEFAULT_SIMULATION_ALLOW_OASIS
    data_dir: Path = Path(DEFAULT_SIMULATION_DATA_DIR)
    actions_per_round: int = DEFAULT_ACTIONS_PER_ROUND
    round_timeout_seconds: float = DEFAULT_ROUND_TIMEOUT_SECONDS
    round_delay_ms: int = DEFAULT_ROUND_DELAY_MS
    max_actions: int = DEFAULT_MAX_ACTIONS
    keep_runtime: bool = False
    platform_delays: Mapping[str, int] | None = None
    cleanup_fail: tuple[str, ...] = ()
    engine_command: str = ""
    engine_runtime: str = "/out"
    fault: Mapping[str, tuple[str, str]] | None = None
    run_kinds: tuple[str, ...] | None = None
    worker_engine: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "DuetConfig":
        env = os.environ if environ is None else environ
        engine = _text(env, "SIMULATION_ENGINE", DEFAULT_SIMULATION_ENGINE).lower()
        if engine not in SIMULATION_ENGINES:
            engine = DEFAULT_SIMULATION_ENGINE
        worker_engine = _text(env, "WORKER_SIMULATION_ENGINE", engine).lower()
        if worker_engine not in SIMULATION_ENGINES:
            worker_engine = engine
        kinds_raw = _text(env, "WORKER_RUN_KINDS")
        kinds = (
            tuple(item.strip() for item in kinds_raw.split(",") if item.strip())
            if kinds_raw
            else None
        )
        return cls(
            engine=engine,
            allow_oasis=_bool(env, "SIMULATION_ALLOW_OASIS", DEFAULT_SIMULATION_ALLOW_OASIS),
            data_dir=Path(_text(env, "SIMULATION_DATA_DIR", DEFAULT_SIMULATION_DATA_DIR)),
            actions_per_round=_int(
                env,
                "SIMULATION_ACTIONS_PER_ROUND",
                DEFAULT_ACTIONS_PER_ROUND,
                minimum=1,
                maximum=50,
            ),
            round_timeout_seconds=_float(
                env, "SIMULATION_ROUND_TIMEOUT_SECONDS", DEFAULT_ROUND_TIMEOUT_SECONDS
            ),
            round_delay_ms=_int(
                env,
                "SIMULATION_ROUND_DELAY_MS",
                DEFAULT_ROUND_DELAY_MS,
                minimum=0,
                maximum=600000,
            ),
            max_actions=_int(
                env,
                "SIMULATION_MAX_ACTIONS",
                DEFAULT_MAX_ACTIONS,
                minimum=1,
                maximum=100000,
            ),
            keep_runtime=_bool(env, "SIMULATION_KEEP_RUNTIME", False),
            platform_delays=parse_platform_delays(
                _text(env, "SIMULATION_PLATFORM_DELAY_MS")
            ),
            cleanup_fail=tuple(
                item.strip()
                for item in _text(env, "SIMULATION_CLEANUP_FAIL").split(",")
                if item.strip()
            ),
            engine_command=_text(env, "SIMULATION_ENGINE_COMMAND"),
            engine_runtime=_text(env, "SIMULATION_ENGINE_RUNTIME", "/out"),
            fault=parse_fault(_text(env, "SIMULATION_FAULT")),
            run_kinds=kinds,
            worker_engine=worker_engine,
        )

    def delay_for(self, platform_key: str) -> int:
        if not self.platform_delays:
            return 0
        return int(self.platform_delays.get(platform_key, 0))

    def fault_for(self, platform_key: str, round_index: int) -> str:
        if not self.fault:
            return "none"
        entry = self.fault.get(platform_key)
        if entry is None:
            return "none"
        raw_round, mode = entry
        return mode if int(raw_round) == round_index else "none"

    def resolve_engine(self) -> str:
        if self.engine == "oasis" and not self.allow_oasis:
            raise DuetConfigError("engine_not_enabled", "真实本地 OASIS 运行未启用")
        return self.engine

    def public_dict(self) -> dict[str, object]:
        return {
            "engine": self.engine,
            "allowOasis": self.allow_oasis,
            "dataDir": str(self.data_dir),
            "actionsPerRound": self.actions_per_round,
            "roundTimeoutSeconds": self.round_timeout_seconds,
            "roundDelayMs": self.round_delay_ms,
            "maxActions": self.max_actions,
            "keepRuntime": self.keep_runtime,
            "engineCommandConfigured": bool(self.engine_command),
            "runKinds": list(self.run_kinds or ()),
            "workerEngine": self.worker_engine,
        }
