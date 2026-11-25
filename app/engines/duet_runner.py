"""双平台引擎子进程入口：一轮一个进程，只写引擎数据库，不访问 PostgreSQL。

worker 通过请求文件下发本轮计划，子进程执行原生动作并把结果写进同一 SQLite；
随后由 worker 独立读回并记账，因此子进程中途退出也只留下可核对的原生记录。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .duet_contracts import DuetContractError
from .duet_engine import execute_round


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EchoSim 双平台引擎执行器")
    parser.add_argument("--request", required=True, help="本轮请求 JSON 文件")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
        result = execute_round(
            mode=str(payload["mode"]),
            platform_key=str(payload["platformKey"]),
            runtime_dir=Path(payload["runtimeDir"]),
            round_index=int(payload["roundIndex"]),
            actions=list(payload["actions"]),
            fault=str(payload.get("fault") or "none"),
        )
    except DuetContractError as exc:
        print(json.dumps({"status": "failed", "reason": exc.code}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001 - 子进程失败交给 worker 归因
        print(
            json.dumps(
                {"status": "failed", "reason": type(exc).__name__},
                ensure_ascii=False,
            )
        )
        return 1
    summary: dict[str, Any] = {
        "status": result.status,
        "roundIndex": result.round_index,
        "actionCount": result.action_count,
        "actions": list(result.actions),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
