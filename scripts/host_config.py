#!/usr/bin/env python3
"""宿主侧从 Compose 最终解析结果派生 worker 专用验收图谱指纹。"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.engines.business_config import graph_id_fingerprint  # noqa: E402


def compose_config(
    env_file: Path, environ: Mapping[str, str] | None = None
) -> dict[str, object]:
    """调用 Compose 自己的 .env/shell 优先级解析，不读取文件内容到宿主逻辑。"""
    process_env = dict(os.environ)
    if environ:
        process_env.update({key: str(value) for key, value in environ.items()})
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "live",
            "--env-file",
            str(env_file),
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "compose config 失败")
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise RuntimeError("compose config 不是对象")
    return parsed


def _service_environment(
    config: Mapping[str, object], service: str
) -> dict[str, str]:
    services = config.get("services")
    if not isinstance(services, dict):
        raise RuntimeError("Compose 缺少 services")
    target = services.get(service)
    if not isinstance(target, dict):
        raise RuntimeError(f"Compose 缺少服务 {service}")
    environment = target.get("environment") or {}
    if not isinstance(environment, dict):
        raise RuntimeError(f"Compose 服务 {service} 环境不是对象")
    return {str(key): str(value or "") for key, value in environment.items()}


def engine_acceptance_graph_id(config: Mapping[str, object]) -> str:
    return _service_environment(config, "engine").get(
        "ZEP_ACCEPTANCE_GRAPH_ID", ""
    )


def worker_fingerprint(config: Mapping[str, object]) -> str:
    return graph_id_fingerprint(engine_acceptance_graph_id(config)) or ""


def worker_environment(config: Mapping[str, object]) -> dict[str, str]:
    return _service_environment(config, "worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fingerprint", "export"))
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        config = compose_config(args.env_file)
        fingerprint = worker_fingerprint(config)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"无法解析 Compose 配置：{type(exc).__name__}", file=sys.stderr)
        return 1
    if args.command == "fingerprint":
        print(fingerprint)
    else:
        print(f"export ZEP_ACCEPTANCE_GRAPH_FINGERPRINT={shlex.quote(fingerprint)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
