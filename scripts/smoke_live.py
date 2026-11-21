#!/usr/bin/env python3
"""make smoke-live 的宿主预检：脏工作树拦截、SHA 绑定与一次性引擎容器。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO_ROOT / "runtime" / "smoke-live"
REPORT_PATH = REPORT_DIR / "last-run.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_output(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def write_refusal(tested_sha: str, dirty: bool) -> None:
    payload = {
        "schemaVersion": 1,
        "result": "failed",
        "mode": "live",
        "tested_sha": tested_sha,
        "dirty": dirty,
        "startedAt": utc_now(),
        "finishedAt": utc_now(),
        "items": [
            {
                "id": "worktree",
                "dependency": "repository",
                "stage": "preflight",
                "status": "not_run",
                "reason": "dirty_worktree",
                "durationMs": 0,
                "calls": {"real": 0, "double": 0},
                "evidence": {},
            }
        ],
        "calls": {"real": 0, "double": 0},
        "exitCode": 2,
        "reason": "dirty_worktree",
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_name(REPORT_PATH.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(REPORT_PATH)


def main() -> int:
    tested_sha = git_output(["rev-parse", "HEAD"]) or "unknown"
    dirty = bool(git_output(["status", "--porcelain"]))
    allow_dirty = os.environ.get("ALLOW_DIRTY", "").strip().lower() in {"1", "true", "yes"}
    if dirty and not allow_dirty:
        write_refusal(tested_sha, dirty)
        print(
            "[smoke-live] 工作树不干净：默认拒绝执行。"
            "请先提交，或使用 ALLOW_DIRTY=1 本地调试（报告会记录 dirty=true）。"
        )
        print(f"[smoke-live] 报告：{REPORT_PATH}")
        return 2

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    previous_mtime = REPORT_PATH.stat().st_mtime_ns if REPORT_PATH.exists() else None
    env = dict(os.environ)
    env["ECHOSIM_TESTED_SHA"] = tested_sha
    env["ECHOSIM_DIRTY"] = "true" if dirty else "false"
    command = [
        "docker",
        "compose",
        "--profile",
        "live",
        "run",
        "--rm",
        "--build",
        "-T",
        "engine",
    ]
    result = subprocess.run(command, cwd=REPO_ROOT, env=env, check=False)
    current_mtime = REPORT_PATH.stat().st_mtime_ns if REPORT_PATH.exists() else None
    if result.returncode != 0 and (current_mtime is None or current_mtime == previous_mtime):
        write_refusal(tested_sha, dirty)
        print("[smoke-live] 引擎容器未写出报告，已写入宿主侧失败报告。")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
