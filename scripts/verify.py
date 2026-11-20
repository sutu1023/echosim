#!/usr/bin/env python3
"""EchoSim 可重复验收脚本（仅依赖 Python 标准库）。

执行流程：前置检查（Git SHA、脏工作树、Docker Compose）→ 启动环境 → 验证首页与
`/health` → 停止数据库观察 503 → 恢复数据库观察 200 → 校验后端容器身份不变 →
原子写入 `runtime/verify/last-run.json` 与带时间戳的历史副本。

退出码：0 通过；1 验收失败；2 前置条件不满足。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO_ROOT / "runtime" / "verify"
REPORT_PATH = REPORT_DIR / "last-run.json"
SCHEMA_VERSION = 1

COMPOSE_TIMEOUT_SECONDS = 900
RECOVERY_TIMEOUT_SECONDS = 60
DEGRADE_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.1

HOMEPAGE_MARKER = "EchoSim"


class ScenarioFailed(Exception):
    """单个场景失败，后续场景不再执行。"""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def command(
    args: list[str], timeout: float = COMPOSE_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    return command(["docker", "compose", *args])


def git_head() -> str:
    result = command(["git", "rev-parse", "HEAD"], timeout=30)
    require(result.returncode == 0, f"无法读取 Git HEAD：{result.stderr.strip()}")
    return result.stdout.strip()


def worktree_is_dirty() -> bool:
    result = command(["git", "status", "--porcelain"], timeout=30)
    require(result.returncode == 0, f"无法检查工作树：{result.stderr.strip()}")
    return bool(result.stdout.strip())


def http_status(path: str, timeout: float = 5.0) -> tuple[int, str]:
    """返回 (状态码, 响应正文)；连接失败以状态码 0 表示。"""
    url = BASE_URL + path
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - 连接失败/超时统一视为不可达
        return 0, ""


BASE_URL = "http://127.0.0.1:8080"


def set_loopback_port() -> str:
    """从 Compose 读取发布端口，并断言只绑定回环地址。"""
    global BASE_URL
    result = compose("port", "app", "8080")
    require(result.returncode == 0, f"无法读取端口映射：{result.stderr.strip()}")
    mapping = result.stdout.strip()
    require(":" in mapping, f"端口映射格式异常：{mapping!r}")
    host, port = mapping.rsplit(":", 1)
    require(host == "127.0.0.1", f"端口未绑定回环地址：{mapping}")
    BASE_URL = f"http://127.0.0.1:{port}"
    return mapping


def wait_for(
    probe: Callable[[], Any],
    timeout: float,
    interval: float = POLL_INTERVAL_SECONDS,
) -> Any:
    """轮询直到 probe 返回真值；超时返回最后一次结果。"""
    deadline = time.monotonic() + timeout
    last: Any = None
    while True:
        last = probe()
        if last:
            return last
        if time.monotonic() >= deadline:
            return last
        time.sleep(interval)


def app_container_id() -> str:
    result = compose("ps", "-q", "app")
    require(result.returncode == 0, f"无法查询 app 容器：{result.stderr.strip()}")
    container_id = result.stdout.strip()
    require(bool(container_id), "app 容器未运行")
    return container_id


def read_identity() -> dict[str, Any]:
    container_id = app_container_id()
    result = command(["docker", "inspect", container_id], timeout=60)
    require(result.returncode == 0, f"无法检查 app 容器：{result.stderr.strip()}")
    payload = json.loads(result.stdout)[0]
    labels = payload.get("Config", {}).get("Labels", {}) or {}
    return {
        "id": payload.get("Id", ""),
        "pid": payload.get("State", {}).get("Pid"),
        "started_at": payload.get("State", {}).get("StartedAt"),
        "restart_count": payload.get("RestartCount"),
        "compose_project": labels.get("com.docker.compose.project"),
    }


@contextmanager
def scenario(report: dict[str, Any], scenario_id: str) -> Iterator[dict[str, Any]]:
    entry: dict[str, Any] = {
        "id": scenario_id,
        "status": "failed",
        "duration_ms": 0,
        "detail": "",
    }
    report["scenarios"].append(entry)
    started = time.monotonic()
    try:
        yield entry
    except Exception as exc:  # noqa: BLE001 - 任意失败都要落进报告
        entry["status"] = "failed"
        entry["detail"] = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        raise ScenarioFailed(scenario_id) from exc
    else:
        entry["status"] = "passed"
        entry["detail"] = entry["detail"] or "ok"
    finally:
        entry["duration_ms"] = round((time.monotonic() - started) * 1000)


def scenario_compose_up() -> None:
    result = compose("up", "-d", "--build", "--wait", "--wait-timeout", "240")
    require(
        result.returncode == 0,
        "docker compose up 失败："
        + (result.stderr.strip() or result.stdout.strip())[-1500:],
    )


def scenario_homepage(entry: dict[str, Any]) -> None:
    def probe() -> tuple[int, str] | None:
        status, body = http_status("/")
        if status == 200 and HOMEPAGE_MARKER in body:
            return status, body
        return None

    result = wait_for(probe, timeout=60)
    require(result is not None, "首页在 60 秒内未返回包含 EchoSim 的 200 响应")
    entry["detail"] = "首页 200 且包含 EchoSim"


def scenario_health_ok() -> None:
    status, body = http_status("/health")
    require(status == 200, f"/health 期望 200，实际 {status}：{body[:200]}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"/health 响应不是 JSON：{body[:200]}") from exc
    require(
        payload == {"status": "ok", "database": "ok"},
        f"/health 响应体不符合契约：{payload}",
    )


def scenario_identity(entry: dict[str, Any], report: dict[str, Any]) -> None:
    identity = read_identity()
    report["app"] = {key: identity[key] for key in ("id", "pid", "started_at", "restart_count")}
    report["compose_project"] = identity.get("compose_project")
    entry["detail"] = (
        f"id={identity['id'][:12]} pid={identity['pid']} "
        f"started_at={identity['started_at']} restart_count={identity['restart_count']}"
    )


def scenario_db_degrade(entry: dict[str, Any], report: dict[str, Any]) -> None:
    stopped = compose("stop", "db")
    require(stopped.returncode == 0, f"停止 db 失败：{stopped.stderr.strip()}")

    def probe_degraded() -> tuple[int, str] | None:
        status, body = http_status("/health")
        return (status, body) if status == 503 else None

    started = time.monotonic()
    result = wait_for(probe_degraded, timeout=DEGRADE_TIMEOUT_SECONDS + 1.0)
    elapsed = time.monotonic() - started
    report["degrade_seconds"] = round(elapsed, 3)

    require(result is not None, f"/health 在 {DEGRADE_TIMEOUT_SECONDS} 秒内未返回 503")
    require(elapsed <= DEGRADE_TIMEOUT_SECONDS, f"停库到 503 耗时 {elapsed:.2f}s 超过 5 秒")
    status, body = result
    require(status == 503, f"/health 期望 503，实际 {status}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"/health 降级响应不是 JSON：{body[:200]}") from exc
    require(
        payload == {"status": "degraded", "database": "unavailable"},
        f"/health 降级响应体不符合契约：{payload}",
    )

    livez_status, livez_body = http_status("/livez")
    require(livez_status == 200, f"停库期间 /livez 期望 200，实际 {livez_status}")
    entry["detail"] = f"停库后 {elapsed:.2f}s 返回 503，/livez 仍为 200（{livez_body[:80]}）"


def scenario_db_recover(entry: dict[str, Any]) -> None:
    started_db = compose("start", "db")
    require(started_db.returncode == 0, f"启动 db 失败：{started_db.stderr.strip()}")

    def probe_ready() -> tuple[int, str] | None:
        status, body = http_status("/health")
        return (status, body) if status == 200 else None

    started = time.monotonic()
    result = wait_for(probe_ready, timeout=RECOVERY_TIMEOUT_SECONDS)
    elapsed = time.monotonic() - started
    require(result is not None, f"/health 在 {RECOVERY_TIMEOUT_SECONDS} 秒内未恢复 200")
    status, body = result
    payload = json.loads(body)
    require(
        payload == {"status": "ok", "database": "ok"},
        f"/health 恢复后的响应体不符合契约：{payload}",
    )
    entry["detail"] = f"恢复数据库后 {elapsed:.2f}s 返回 200"


def scenario_identity_stable(entry: dict[str, Any], report: dict[str, Any]) -> None:
    current = read_identity()
    before = report.get("app") or {}
    for key in ("id", "pid", "started_at", "restart_count"):
        require(
            current.get(key) == before.get(key),
            f"后端容器身份发生变化：{key} {before.get(key)!r} -> {current.get(key)!r}",
        )
    entry["detail"] = "容器 ID/PID/StartedAt/RestartCount 均未变化"


def scenario_homepage_after_recovery(entry: dict[str, Any]) -> None:
    status, body = http_status("/")
    require(status == 200, f"恢复后首页期望 200，实际 {status}")
    require(HOMEPAGE_MARKER in body, "恢复后首页未包含 EchoSim")
    entry["detail"] = "恢复后首页仍为 200 且包含 EchoSim"


def run_acceptance(report: dict[str, Any]) -> bool:
    try:
        with scenario(report, "compose-up"):
            scenario_compose_up()
        with scenario(report, "loopback-port"):
            mapping = set_loopback_port()
            report["base_url"] = BASE_URL
            report["port_mapping"] = mapping
        with scenario(report, "homepage") as entry:
            scenario_homepage(entry)
        with scenario(report, "health-ok"):
            scenario_health_ok()
        with scenario(report, "container-identity") as entry:
            scenario_identity(entry, report)
        with scenario(report, "database-stop-degrades") as entry:
            scenario_db_degrade(entry, report)
        with scenario(report, "database-recover-ready") as entry:
            scenario_db_recover(entry)
        with scenario(report, "container-identity-stable") as entry:
            scenario_identity_stable(entry, report)
        with scenario(report, "homepage-after-recovery") as entry:
            scenario_homepage_after_recovery(entry)
    except ScenarioFailed:
        return False
    return True


def restore_database() -> None:
    """尽力把数据库恢复到运行状态，保证下一次验收可执行。"""
    result = compose("start", "db")
    status = "ok" if result.returncode == 0 else f"失败：{result.stderr.strip()}"
    print(f"[verify] 恢复数据库：{status}")


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
    )
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def write_report(report: dict[str, Any]) -> None:
    atomic_write(REPORT_PATH, report)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    atomic_write(REPORT_DIR / f"run-{stamp}.json", report)


def finalize(report: dict[str, Any], exit_code: int) -> int:
    report["finished_at"] = utc_now()
    report["exit_code"] = exit_code
    report["result"] = "passed" if exit_code == 0 else "failed"
    write_report(report)
    print("\n[verify] 场景结果：")
    for entry in report["scenarios"]:
        print(
            f"  - {entry['id']}: {entry['status']} ({entry['duration_ms']}ms) "
            f"{entry['detail']}"
        )
    print(f"[verify] result={report['result']} tested_sha={report['tested_sha']}")
    print(f"[verify] 报告：{REPORT_PATH.relative_to(REPO_ROOT)}")
    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EchoSim 可重复验收：启动环境、验证健康检查故障与恢复、写入绑定 SHA 的报告。",
        epilog=(
            "环境变量：EXPECTED_SHA 指定期望被测 SHA（等价于 --expect-sha）；"
            "ALLOW_DIRTY=1 显式允许脏工作树继续执行（报告记录 dirty=true）。"
        ),
    )
    parser.add_argument(
        "--expect-sha",
        default=os.environ.get("EXPECTED_SHA") or None,
        help="期望的被测 Git SHA；与 HEAD 不一致时验收失败（默认读取 EXPECTED_SHA）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    allow_dirty = os.environ.get("ALLOW_DIRTY", "").strip().lower() in {"1", "true", "yes"}

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "result": "failed",
        "tested_sha": "",
        "dirty": False,
        "expected_sha": args.expect_sha,
        "started_at": utc_now(),
        "finished_at": None,
        "exit_code": None,
        "compose_project": None,
        "base_url": None,
        "app": None,
        "degrade_seconds": None,
        "scenarios": [],
    }

    try:
        head = git_head()
    except AssertionError as exc:
        print(f"[verify] {exc}")
        report["tested_sha"] = "unknown"
        report["scenarios"].append(
            {
                "id": "git-head",
                "status": "failed",
                "duration_ms": 0,
                "detail": str(exc),
            }
        )
        return finalize(report, 2)

    report["tested_sha"] = head
    report["dirty"] = worktree_is_dirty()

    print(f"[verify] HEAD={head}")
    print(f"[verify] dirty={report['dirty']}")

    if report["dirty"] and not allow_dirty:
        message = (
            "工作树不干净：存在未提交或未跟踪的改动。请先提交后再验收，"
            "或使用 ALLOW_DIRTY=1 显式允许（报告将记录 dirty=true，不能作为干净克隆证据）。"
        )
        print(f"[verify] {message}")
        report["scenarios"].append(
            {
                "id": "worktree-clean",
                "status": "failed",
                "duration_ms": 0,
                "detail": message,
            }
        )
        return finalize(report, 2)

    if args.expect_sha and args.expect_sha != head:
        message = f"被测 SHA 不一致：期望 {args.expect_sha}，实际 HEAD {head}"
        print(f"[verify] {message}")
        report["scenarios"].append(
            {
                "id": "sha-match",
                "status": "failed",
                "duration_ms": 0,
                "detail": message,
            }
        )
        return finalize(report, 1)

    if args.expect_sha:
        report["scenarios"].append(
            {
                "id": "sha-match",
                "status": "passed",
                "duration_ms": 0,
                "detail": f"HEAD 与期望 SHA 一致：{head}",
            }
        )

    try:
        passed = run_acceptance(report)
    finally:
        restore_database()

    return finalize(report, 0 if passed else 1)


if __name__ == "__main__":
    sys.exit(main())
