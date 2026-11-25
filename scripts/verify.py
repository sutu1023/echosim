#!/usr/bin/env python3
"""EchoSim 可重复验收脚本（仅依赖 Python 标准库）。

执行流程：前置检查（Git SHA、脏工作树、Docker Compose）→ 启动环境 → 验证首页与
`/health` → 停止数据库观察 503 → 恢复数据库观察 200 → 校验后端容器身份不变 →
原子写入 `runtime/verify/last-run.json` 与带时间戳的历史副本。

退出码：0 通过；1 验收失败；2 前置条件不满足。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import http.cookiejar
import json
import os
import hashlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO_ROOT / "runtime" / "verify"
REPORT_PATH = REPORT_DIR / "last-run.json"
SCHEMA_VERSION = 2

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
    args: list[str],
    timeout: float = COMPOSE_TIMEOUT_SECONDS,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        input=input_text,
        timeout=timeout,
        check=False,
    )


def compose(
    *args: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    base = ["docker", "compose"]
    if COMPOSE_PROJECT:
        base += ["-p", COMPOSE_PROJECT]
    return command([*base, *args], input_text=input_text)


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def apply_runtime_env(overrides: dict[str, str]) -> dict[str, str | None]:
    """保存并应用隔离运行环境；调用方负责恢复。"""
    saved = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    return saved


def restore_runtime_env(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def isolated_runtime_env() -> dict[str, str]:
    """与 8080 开发栈完全隔离，并使用离线/空外部凭据。"""
    return {
        "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT,
        "ECHOSIM_PORT": str(free_loopback_port()),
        "POSTGRES_USER": "echosim",
        "POSTGRES_DB": "echosim",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(18),
        "MODEL_API_BASE_URL": "",
        "MODEL_API_KEY": "",
        "MODEL_NAME": "",
        "ZEP_API_KEY": "",
        "ZEP_API_BASE_URL": "",
        "ZEP_ACCEPTANCE_GRAPH_ID": "",
        "ZEP_ACCEPTANCE_GRAPH_FINGERPRINT": "",
        "EVIDENCE_GRAPH_MODEL_PROVIDER": "unconfigured",
        "EVIDENCE_GRAPH_GRAPH_PROVIDER": "unconfigured",
        "EVIDENCE_GRAPH_ALLOW_STUB": "false",
        # 验收固定较短租约，不继承宿主 .env 的长租约，保证接手场景可复现。
        "JOB_LEASE_SECONDS": "6",
        "JOB_HEARTBEAT_SECONDS": "2",
    }


def graph_runtime_env() -> dict[str, str]:
    global REDACTION_SECRET
    REDACTION_SECRET = "verify-secret-" + secrets.token_hex(16)
    return {
        "MODEL_API_KEY": REDACTION_SECRET,
        "EVIDENCE_GRAPH_MODEL_PROVIDER": "stub",
        "EVIDENCE_GRAPH_GRAPH_PROVIDER": "stub",
        "EVIDENCE_GRAPH_ALLOW_STUB": "true",
    }


def environment_runtime_env() -> dict[str, str]:
    """Change 08：建图与画像都使用离线替身，真实调用必须为 0。"""
    return {
        **graph_runtime_env(),
        "ENVIRONMENT_MODEL_PROVIDER": "stub",
        "ENVIRONMENT_ALLOW_STUB": "true",
        "ENVIRONMENT_PROFILE_BATCH_SIZE": "2",
        "ENVIRONMENT_PROFILE_DELAY_MS": "0",
    }


def simulation_runtime_env() -> dict[str, str]:
    """Change 09：建图、画像与两侧引擎都使用离线实现，真实外呼必须为 0。"""
    return {
        **environment_runtime_env(),
        "SIMULATION_ENGINE": "double",
        "SIMULATION_ALLOW_OASIS": "true",
        "SIMULATION_ACTIONS_PER_ROUND": "6",
        "SIMULATION_ROUND_TIMEOUT_SECONDS": "120",
        "SIMULATION_ROUND_DELAY_MS": "0",
        "SIMULATION_MAX_ACTIONS": "5000",
        "SIMULATION_FAULT": "",
        "SIMULATION_KEEP_RUNTIME": "false",
        "DUET_CHILD_SLEEP_MS": "0",
        "WORKER_SIMULATION_ENGINE": "double",
    }


def temporal_memory_runtime_env() -> dict[str, str]:
    """Change 10：运行时记忆只使用 stub 图谱，真实外呼必须为 0。"""
    return {
        **simulation_runtime_env(),
        "SIMULATION_MEMORY_PROVIDER": "stub",
        "SIMULATION_MEMORY_MAX_REFS": "20",
        "SIMULATION_MEMORY_MAX_NODES": "2000",
        "SIMULATION_MEMORY_MAX_EDGES": "4000",
        "SIMULATION_MEMORY_INDEX_TIMEOUT_SECONDS": "30",
        "SIMULATION_MEMORY_PROCESSING_TIMEOUT_SECONDS": "60",
        "SIMULATION_MEMORY_RETRY_BUDGET_SECONDS": "120",
        "SIMULATION_MEMORY_BATCH_SIZE": "20",
    }


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
COMPOSE_PROJECT = "echosim-verify"
VERIFY_PROJECT_PATTERN = re.compile(r"^echosim-verify(?:-[a-z0-9][a-z0-9_-]*)?$")


class VerifyIsolationError(Exception):
    """验收隔离资源不合法或已被其它进程占用。"""


def isolated_compose_project(value: str | None) -> str:
    """只接受本次验收拥有的 Compose 项目名；拒绝开发栈或任意外部项目。"""
    name = (value or "").strip().lower() or "echosim-verify"
    if not VERIFY_PROJECT_PATTERN.fullmatch(name):
        raise VerifyIsolationError(
            "VERIFY_COMPOSE_PROJECT 必须是 echosim-verify 或其带后缀的隔离项目，"
            f"拒绝操作 {name!r}"
        )
    return name


def acquire_verify_lock(
    project: str, *, directory: Path | None = None
) -> Any:
    """同一隔离项目只允许一个验收进程；锁在进程退出时自动释放。"""
    directory = directory or REPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / f".{project}.lock", "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise VerifyIsolationError(
            f"另一个验收进程正在使用隔离项目 {project}"
        ) from None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def release_verify_lock(handle: Any) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def cleanup_isolated_compose(project: str, runner: Callable[[list[str]], Any]) -> None:
    """只清理本次项目的容器与数据卷；非法项目在运行任何命令前拒绝。"""
    name = isolated_compose_project(project)
    result = runner(
        [
            "docker",
            "compose",
            "-p",
            name,
            "down",
            "-v",
            "--remove-orphans",
        ]
    )
    if result.returncode != 0:
        detail = (getattr(result, "stderr", "") or getattr(result, "stdout", ""))
        raise RuntimeError(f"清理隔离验收资源失败：{str(detail).strip()[-400:]}")
REDACTION_SECRET = "verify-secret-unset"

SESSION_COOKIE = "echosim_session"
ACCOUNT_A = "accept-a@echosim.local"
ACCOUNT_B = "accept-b@echosim.local"
ACCOUNT_PASSWORD_LENGTH = 24


class HttpClient:
    """带 Cookie 罐的验收客户端；只用于服务端 HTTP 行为验证。"""

    def __init__(self, cookie_header: str | None = None) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.explicit_cookie = cookie_header

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> tuple[int, dict[str, str], str]:
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(BASE_URL + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.explicit_cookie:
            request.add_header("Cookie", self.explicit_cookie)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return (
                    response.status,
                    {key.lower(): value for key, value in response.headers.items()},
                    response.read().decode("utf-8", "replace"),
                )
        except urllib.error.HTTPError as exc:
            return (
                exc.code,
                {key.lower(): value for key, value in exc.headers.items()},
                exc.read().decode("utf-8", "replace"),
            )
        except Exception:  # noqa: BLE001 - 连接失败统一以状态码 0 表示
            return 0, {}, ""

    def cookie_value(self, name: str = SESSION_COOKIE) -> str | None:
        for cookie in self.jar:
            if cookie.name == name:
                return cookie.value
        return None

    def get_json(self, path: str) -> tuple[int, dict[str, str], object | None]:
        status, headers, body = self.request("GET", path)
        try:
            return status, headers, json.loads(body) if body else None
        except json.JSONDecodeError:
            return status, headers, None


def raw_request(
    method: str,
    path: str,
    *,
    cookie_token: str | None = None,
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str], str]:
    """不依赖 Cookie 罐的请求，用于复放旧 Cookie 与显式注入 Cookie/请求头。"""
    cookie_header = (
        f"{SESSION_COOKIE}={cookie_token}" if cookie_token is not None else None
    )
    return HttpClient(cookie_header=cookie_header).request(
        method, path, payload=payload, headers=headers, timeout=timeout
    )


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
        traceback.print_exc()
        entry["status"] = "failed"
        entry["detail"] = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        raise ScenarioFailed(scenario_id) from exc
    else:
        entry["status"] = "passed"
        entry["detail"] = entry["detail"] or "ok"
    finally:
        entry["duration_ms"] = round((time.monotonic() - started) * 1000)


def scenario_compose_up() -> None:
    # 隔离验收每次都从空数据卷开始，避免随机数据库密码与旧卷不一致；
    # 清理只作用于通过命名校验且持有锁的 echosim-verify 项目。
    cleanup_isolated_compose(COMPOSE_PROJECT, command)
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



def ensure_account(email: str, password: str) -> None:
    """幂等准备验收账号：存在时重设密码并撤销旧会话。"""
    result = compose(
        "exec",
        "-T",
        "app",
        "python",
        "-m",
        "app.manage",
        "create-user",
        "--email",
        email,
        "--password-stdin",
        "--update-password",
        input_text=password + "\n",
    )
    require(
        result.returncode == 0,
        f"准备账号 {email} 失败："
        + ((result.stderr or result.stdout).strip()[-400:] or "无输出"),
    )


def scenario_prepare_accounts(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    password = secrets.token_urlsafe(ACCOUNT_PASSWORD_LENGTH)
    ensure_account(ACCOUNT_A, password)
    ensure_account(ACCOUNT_B, password)
    state["password"] = password
    report["acceptance_accounts"] = [ACCOUNT_A, ACCOUNT_B]
    entry["detail"] = f"已准备/更新两个验收账号：{ACCOUNT_A}、{ACCOUNT_B}（密码不写入报告）"


def scenario_login_and_create(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    client = HttpClient()
    status, _, body = client.request(
        "POST",
        "/api/auth/login",
        payload={"email": ACCOUNT_A, "password": state["password"]},
        headers={"Origin": BASE_URL},
    )
    require(status == 200, f"A 登录期望 200，实际 {status}：{body[:200]}")
    token = client.cookie_value()
    require(bool(token), "登录响应未设置会话 Cookie")

    project_name = f"验收项目-{int(time.time())}-{secrets.token_hex(3)}"
    status, _, body = client.request(
        "POST",
        "/api/projects",
        payload={"name": project_name, "description": "隔离路径验收"},
        headers={"Origin": BASE_URL},
    )
    require(status == 201, f"创建项目期望 201，实际 {status}：{body[:300]}")
    project = json.loads(body)["project"]
    require(project["name"] == project_name, "创建响应中的项目名与提交不一致")

    status, _, list_payload = client.get_json("/api/projects")
    require(status == 200, f"A 项目列表期望 200，实际 {status}")
    names = [item["name"] for item in list_payload["projects"]]  # type: ignore[index]
    require(project_name in names, "创建后刷新列表未看到新项目")

    status, _, detail_payload = client.get_json(f"/api/projects/{project['id']}")
    require(status == 200, f"A 项目详情期望 200，实际 {status}")
    require(
        detail_payload["project"]["name"] == project_name,  # type: ignore[index]
        "项目详情返回的名称不一致",
    )

    state["client_a"] = client
    state["token_a"] = token
    state["project_id"] = project["id"]
    state["project_name"] = project_name
    report["isolation_project"] = {"id": project["id"], "name": project_name}
    entry["detail"] = f"A 登录并创建项目 {project['id']}，列表与详情均可见"


WORKBENCH_STAGES = (
    "evidence-graph",
    "roles-environment",
    "dual-platform-run",
    "report",
    "interaction",
)


def scenario_shell_routes(entry: dict[str, Any], state: dict[str, Any]) -> None:
    """只证明 SPA 入口可达，不宣称验证布局、语言或浏览器历史。"""
    project_id = state["project_id"]
    paths = ["/", "/projects"]
    paths.extend(
        f"/projects/{project_id}/workbench/{stage}" for stage in WORKBENCH_STAGES
    )

    for path in paths:
        status, body = http_status(path)
        require(status == 200, f"{path} 期望 200，实际 {status}")
        require(HOMEPAGE_MARKER in body, f"{path} 返回的 HTML 未包含 EchoSim")
        require(
            "<!doctype html" in body.lower(),
            f"{path} 未返回前端入口 HTML",
        )

    status, headers, body = raw_request("GET", "/api/projects")
    require(status == 401, f"/api/projects 匿名期望 401，实际 {status}")
    require(
        headers.get("content-type", "").startswith("application/json"),
        "/api/projects 被 SPA 回退吞成非 JSON 响应",
    )
    require(
        json.loads(body) == {"detail": "未登录"},
        "/api/projects 匿名响应体不符合既有契约",
    )

    entry["detail"] = (
        "控制台 /、项目列表 /projects 与五条工作台阶段深链接均返回 EchoSim HTML；"
        "/api/projects 仍为 JSON 401（仅证明 SPA 入口可达）"
    )


def scenario_session_cookie_replay(
    entry: dict[str, Any], state: dict[str, Any]
) -> None:
    """服务端会话测试：显式注入 Cookie，不代表浏览器重开。"""
    token = state["token_a"]
    status, _, body = raw_request("GET", "/api/auth/session", cookie_token=token)
    require(status == 200, f"显式 Cookie 会话查询期望 200，实际 {status}")
    payload = json.loads(body)
    require(payload["user"]["email"] == ACCOUNT_A, "会话查询返回了其他用户")

    status, _, body = raw_request(
        "GET", f"/api/projects/{state['project_id']}", cookie_token=token
    )
    require(status == 200, f"显式 Cookie 详情期望 200，实际 {status}")
    require(
        json.loads(body)["project"]["name"] == state["project_name"],
        "显式 Cookie 详情返回了其他项目",
    )
    entry["detail"] = (
        "服务端会话测试：仅凭显式注入的 Cookie 即可访问会话与项目（非浏览器重开走查）"
    )


def scenario_auth_anonymous_outage(entry: dict[str, Any]) -> None:
    started = time.monotonic()
    status, headers, body = raw_request("GET", "/api/projects", timeout=5.0)
    elapsed = time.monotonic() - started
    require(status == 401, f"停库且无 Cookie 时期望 401，实际 {status}")
    require(elapsed <= 5.0, f"无 Cookie 的 401 耗时 {elapsed:.2f}s 超过 5 秒")
    require("set-cookie" not in headers, "无 Cookie 的 401 不应下发 Cookie")
    entry["detail"] = f"停库期间无 Cookie 直接 401（{elapsed * 1000:.0f}ms），未查询数据库"


def scenario_auth_cookie_outage(entry: dict[str, Any], state: dict[str, Any]) -> None:
    started = time.monotonic()
    status, headers, body = raw_request(
        "GET",
        "/api/projects",
        cookie_token=state["token_a"],
        timeout=DEGRADE_TIMEOUT_SECONDS + 1.0,
    )
    elapsed = time.monotonic() - started
    require(status == 503, f"停库且携带 Cookie 时期望 503，实际 {status}：{body[:200]}")
    require(elapsed <= DEGRADE_TIMEOUT_SECONDS, f"停库鉴权降级耗时 {elapsed:.2f}s 超过 5 秒")
    require(
        "set-cookie" not in headers,
        "停库 503 不应清除 Cookie，否则恢复后无法继续使用原会话",
    )
    entry["detail"] = f"停库期间携带 Cookie 在 {elapsed:.2f}s 返回 503，且未清除 Cookie"


def scenario_session_after_recovery(
    entry: dict[str, Any], state: dict[str, Any]
) -> None:
    token = state["token_a"]
    status, _, body = raw_request("GET", "/api/projects", cookie_token=token)
    require(status == 200, f"恢复后原 Cookie 列表期望 200，实际 {status}：{body[:200]}")
    status, _, body = raw_request(
        "GET", f"/api/projects/{state['project_id']}", cookie_token=token
    )
    require(status == 200, f"恢复后原 Cookie 详情期望 200，实际 {status}")
    entry["detail"] = "数据库恢复后，停机前捕获的同一 Cookie 仍可访问列表与详情"


def scenario_logout_invalidates(entry: dict[str, Any], state: dict[str, Any]) -> None:
    client = state["client_a"]
    status, _, body = client.request(
        "POST", "/api/auth/logout", headers={"Origin": BASE_URL}
    )
    require(status == 204, f"A 退出期望 204，实际 {status}：{body[:200]}")

    status, headers, body = raw_request(
        "GET", "/api/auth/session", cookie_token=state["token_a"]
    )
    require(status == 401, f"退出后旧 Cookie 会话查询期望 401，实际 {status}")
    require("Max-Age=0" in headers.get("set-cookie", ""), "401 未清除失效 Cookie")

    status, _, _ = raw_request(
        "GET", "/api/projects", cookie_token=state["token_a"]
    )
    require(status == 401, f"退出后旧 Cookie 项目列表期望 401，实际 {status}")
    entry["detail"] = "A 退出后旧 Cookie 的会话查询与项目列表均返回 401"


def scenario_user_b_isolation(
    entry: dict[str, Any], state: dict[str, Any]
) -> None:
    client_b = HttpClient()
    status, _, body = client_b.request(
        "POST",
        "/api/auth/login",
        payload={"email": ACCOUNT_B, "password": state["password"]},
        headers={"Origin": BASE_URL},
    )
    require(status == 200, f"B 登录期望 200，实际 {status}：{body[:200]}")

    status, _, list_body = client_b.request("GET", "/api/projects")
    require(status == 200, f"B 项目列表期望 200，实际 {status}")
    require(
        state["project_name"] not in list_body and state["project_id"] not in list_body,
        "B 的项目列表泄露了 A 的项目",
    )

    status, _, history_body = client_b.request("GET", "/api/history")
    require(status == 200, f"B 历史期望 200，实际 {status}")
    require(
        state["project_name"] not in history_body
        and state["project_id"] not in history_body,
        "B 的历史泄露了 A 的项目",
    )

    status_a, _, body_a = client_b.request(
        "GET", f"/api/projects/{state['project_id']}"
    )
    status_missing, _, body_missing = client_b.request(
        "GET", f"/api/projects/{uuid.uuid4()}"
    )
    status_invalid, _, body_invalid = client_b.request(
        "GET", "/api/projects/not-a-uuid"
    )
    require(status_a == 404, f"B 访问 A 的项目期望 404，实际 {status_a}")
    require(status_missing == 404, f"B 访问不存在项目期望 404，实际 {status_missing}")
    require(status_invalid == 404, f"B 访问非法 ID 期望 404，实际 {status_invalid}")
    require(body_a == body_missing == body_invalid, "他人项目与不存在项目的响应体不一致")
    entry["detail"] = (
        "B 的列表与历史不含 A 项目；B 访问 A 项目、随机 UUID 与非法 ID 均为相同 404"
    )


def scenario_relogin_keeps_project(
    entry: dict[str, Any], state: dict[str, Any]
) -> None:
    client = HttpClient()
    status, _, body = client.request(
        "POST",
        "/api/auth/login",
        payload={"email": ACCOUNT_A, "password": state["password"]},
        headers={"Origin": BASE_URL},
    )
    require(status == 200, f"A 重新登录期望 200，实际 {status}：{body[:200]}")
    refreshed = client.cookie_value()
    require(bool(refreshed), "重新登录未设置会话 Cookie")
    state["token_a"] = refreshed
    state["client_a"] = client
    status, _, payload = client.get_json(f"/api/projects/{state['project_id']}")
    require(status == 200, f"A 重新登录后详情期望 200，实际 {status}")
    require(
        payload["project"]["name"] == state["project_name"],  # type: ignore[index]
        "重新登录后打开的项目名称不一致",
    )
    entry["detail"] = "A 重新登录后仍能打开退出前创建的原项目"


CHANGE_ID = "2026-11-21-track-background-jobs"
DOCUMENT_CHANGE_ID = "2026-11-21-ingest-seed-documents"
ENGINE_CHANGE_ID = "2026-11-21-connect-engine-providers"
GRAPH_CHANGE_ID = "2026-11-22-build-evidence-graph"
ENVIRONMENT_CHANGE_ID = "2026-11-24-prepare-agent-environment"
SIMULATION_CHANGE_ID = "2026-11-24-run-dual-platform-simulation"
TEMPORAL_MEMORY_CHANGE_ID = "2026-11-25-update-temporal-memory"
KNOWN_CHANGES = {
    CHANGE_ID,
    DOCUMENT_CHANGE_ID,
    ENGINE_CHANGE_ID,
    GRAPH_CHANGE_ID,
    ENVIRONMENT_CHANGE_ID,
    SIMULATION_CHANGE_ID,
    TEMPORAL_MEMORY_CHANGE_ID,
}
VERIFY_STEP_DELAY_MS = "800"
JOB_TIMEOUT_SECONDS = 90.0
JOB_EVENT_TIMEOUT_SECONDS = 60.0


def job_path(project_id: str, job_id: str | None = None) -> str:
    base = f"/api/projects/{project_id}/jobs"
    return base if job_id is None else f"{base}/{job_id}"


def api_call(
    method: str,
    path: str,
    token: str,
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, str], object | None]:
    status, response_headers, body = raw_request(
        method,
        path,
        cookie_token=token,
        payload=payload,
        headers=headers,
        timeout=timeout,
    )
    try:
        data = json.loads(body) if body else None
    except json.JSONDecodeError:
        data = None
    return status, response_headers, data


def start_test_job(
    token: str,
    project_id: str,
    *,
    stage: str = "dual-platform-run",
    fail_at_step: int | None = None,
) -> tuple[int, dict[str, object]]:
    payload: dict[str, object] = {"operation": "test-simulation", "stage": stage}
    if fail_at_step is not None:
        payload["failAtStep"] = fail_at_step
    status, _, body = api_call("POST", job_path(project_id), token, payload=payload)
    require(isinstance(body, dict), f"开始任务响应不是 JSON：{body}")
    return status, body


def fetch_job(token: str, project_id: str, job_id: str) -> dict[str, object]:
    status, _, body = api_call("GET", job_path(project_id, job_id), token)
    require(status == 200, f"任务详情期望 200，实际 {status}：{body}")
    require(isinstance(body, dict) and "job" in body, f"任务详情缺少 job：{body}")
    return body["job"]  # type: ignore[index]


def fetch_job_events(
    token: str, project_id: str, job_id: str, after: int = 0
) -> dict[str, object]:
    status, _, body = api_call(
        "GET", f"{job_path(project_id, job_id)}/events?format=json&after={after}", token
    )
    require(status == 200, f"事件历史期望 200，实际 {status}：{body}")
    require(isinstance(body, dict), f"事件历史不是对象：{body}")
    return body


def fetch_effects(token: str, project_id: str, job_id: str) -> dict[str, object]:
    status, _, body = api_call("GET", f"{job_path(project_id, job_id)}/effects", token)
    require(status == 200, f"副作用查询期望 200，实际 {status}：{body}")
    require(isinstance(body, dict), f"副作用响应不是对象：{body}")
    return body


def event_seqs(events: dict[str, object]) -> list[int]:
    items = events.get("events") or []
    return [int(item["seq"]) for item in items]  # type: ignore[index]


def event_types(events: dict[str, object]) -> list[str]:
    items = events.get("events") or []
    return [str(item["type"]) for item in items]  # type: ignore[index]


def wait_job(
    token: str,
    project_id: str,
    job_id: str,
    predicate: Callable[[dict[str, object]], bool],
    *,
    timeout: float = JOB_TIMEOUT_SECONDS,
) -> dict[str, object]:
    def probe() -> dict[str, object] | None:
        try:
            job = fetch_job(token, project_id, job_id)
        except AssertionError:
            return None
        return job if predicate(job) else None

    result = wait_for(probe, timeout=timeout, interval=0.2)
    require(result is not None, f"任务 {job_id} 未在 {timeout}s 内满足条件")
    return result  # type: ignore[return-value]


def sse_open(
    path: str,
    token: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = JOB_EVENT_TIMEOUT_SECONDS,
):
    request = urllib.request.Request(BASE_URL + path)
    request.add_header("Accept", "text/event-stream")
    request.add_header("Cookie", f"{SESSION_COOKIE}={token}")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    return urllib.request.urlopen(request, timeout=timeout)


def read_sse(
    response, *, until_seq: int | None = None, deadline: float = JOB_EVENT_TIMEOUT_SECONDS
) -> tuple[list[int], dict[str, object] | None]:
    """逐帧读取 SSE；只在完整帧（含结尾空行）之后才更新结果。"""
    seqs: list[int] = []
    event_name = ""
    data_lines: list[str] = []
    started = time.monotonic()
    while time.monotonic() - started < deadline:
        raw = response.readline()
        if not raw:
            break
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith(":") or line.startswith("retry:"):
            continue
        if line.startswith("id:"):
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
            continue
        if line != "":
            continue
        payload_text = "".join(data_lines)
        if event_name == "run-event" and payload_text:
            payload = json.loads(payload_text)
            seqs.append(int(payload["seq"]))
            finished = until_seq is not None and seqs[-1] >= until_seq
            event_name, data_lines = "", []
            if finished:
                return seqs, None
            continue
        if event_name == "stream-end":
            payload = json.loads(payload_text) if payload_text else {}
            return seqs, payload
        event_name, data_lines = "", []
    return seqs, None


def collect_sse(
    path: str,
    token: str,
    *,
    until_seq: int | None = None,
    headers: dict[str, str] | None = None,
    deadline: float = JOB_EVENT_TIMEOUT_SECONDS,
) -> tuple[list[int], dict[str, object] | None]:
    response = sse_open(path, token, headers=headers)
    try:
        return read_sse(response, until_seq=until_seq, deadline=deadline)
    finally:
        response.close()


def run_probe(
    mode: str,
    *,
    project_id: str | None = None,
    job_id: str | None = None,
    trials: int | None = None,
) -> dict[str, Any]:
    args = ["exec", "-T", "app", "python", "-m", "app.lease_probe", "--mode", mode]
    if project_id:
        args += ["--project-id", project_id]
    if job_id:
        args += ["--job-id", job_id]
    if trials:
        args += ["--trials", str(trials)]
    result = compose(*args)
    output = (result.stderr or result.stdout).strip()[-600:]
    require(result.returncode == 0, f"探针 {mode} 失败：{output}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    require(bool(lines), f"探针 {mode} 没有 JSON 输出")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise AssertionError(f"探针 {mode} 输出不是 JSON：{lines[-1][:300]}") from exc


def record_job_evidence(
    report: dict[str, Any], scenario_id: str, **fields: object
) -> None:
    report["job_evidence"].append({"scenario": scenario_id, **fields})


def scenario_job_start(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    status, body = start_test_job(token, project_id)
    require(status == 202, f"开始任务期望 202，实际 {status}：{body}")
    job = body["job"]  # type: ignore[index]
    require(job["deduplicated"] is False, "新建任务应标记 deduplicated=false")
    require(bool(job.get("jobId")) and bool(job.get("runId")), "开始响应缺少标识")
    require(int(job["totalSteps"]) == 10, "测试任务总步数应为 10")

    detail = fetch_job(token, project_id, str(job["jobId"]))
    require(detail["runId"] == job["runId"], "详情 runId 与开始响应不一致")

    state["job_id"] = str(job["jobId"])
    state["run_id"] = str(job["runId"])
    record_job_evidence(
        report,
        "job-start-202",
        job_id=state["job_id"],
        run_id=state["run_id"],
    )
    entry["detail"] = f"202 新建 jobId={state['job_id']} runId={state['run_id']}"


def scenario_job_terminal_success(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    job_id = state["job_id"]
    job = wait_job(
        token,
        project_id,
        job_id,
        lambda item: item["status"] in ("succeeded", "failed", "canceled"),
    )
    require(job["status"] == "succeeded", f"确定性任务应成功，实际 {job['status']}")
    require(int(job["stepsApplied"]) == 10, "成功任务应提交 10 步")

    events = fetch_job_events(token, project_id, job_id)
    require(
        event_seqs(events) == list(range(1, 11)),
        f"事件序号应为 1–10，实际 {event_seqs(events)}",
    )
    require(
        event_types(events) == ["progress"] * 9 + ["completed"],
        f"事件类型不符合确定性任务契约：{event_types(events)}",
    )
    effects = fetch_effects(token, project_id, job_id)
    require(int(effects["total"]) == 10, f"副作用总数应为 10，实际 {effects['total']}")
    require(int(effects["distinctSteps"]) == 10, "副作用步骤数应为 10")
    require(effects["steps"] == list(range(1, 11)), "副作用步骤序号应为 1–10")
    require(effects["duplicated"] is False, "副作用不应重复")

    record_job_evidence(
        report,
        "job-terminal-success",
        job_id=job_id,
        run_id=state["run_id"],
        seqs=event_seqs(events),
        effects_total=int(effects["total"]),
    )
    entry["detail"] = (
        f"任务成功，事件 1–10，副作用 {effects['total']} 条且步骤唯一"
    )


def scenario_job_access_boundaries(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    project_id, job_id = state["project_id"], state["job_id"]
    anonymous_calls = [
        ("POST", job_path(project_id), {"operation": "test-simulation", "stage": "report"}),
        ("GET", job_path(project_id), None),
        ("GET", job_path(project_id, job_id), None),
        ("POST", f"{job_path(project_id, job_id)}/cancel", None),
        ("GET", f"{job_path(project_id, job_id)}/effects", None),
        ("GET", f"{job_path(project_id, job_id)}/events", None),
    ]
    for method, path, payload in anonymous_calls:
        status, headers, body = raw_request(method, path, payload=payload)
        require(status == 401, f"匿名 {method} {path} 期望 401，实际 {status}")
        require(
            "text/event-stream" not in headers.get("content-type", ""),
            f"匿名 {path} 不应建立事件流",
        )
        require(body == '{"detail":"未登录"}', f"匿名 {path} 响应体不符合契约")

    client_b = HttpClient()
    login_status, _, login_body = client_b.request(
        "POST",
        "/api/auth/login",
        payload={"email": ACCOUNT_B, "password": state["password"]},
        headers={"Origin": BASE_URL},
    )
    require(login_status == 200, f"B 登录期望 200，实际 {login_status}：{login_body[:200]}")

    status_a, _, body_a = client_b.request("GET", job_path(project_id, job_id))
    status_missing, _, body_missing = client_b.request(
        "GET", job_path(str(uuid.uuid4()), job_id)
    )
    status_events, headers_events, body_events = client_b.request(
        "GET", f"{job_path(project_id, job_id)}/events"
    )
    require(status_a == 404, f"B 访问 A 的任务期望 404，实际 {status_a}")
    require(status_missing == 404, f"B 访问不存在项目期望 404，实际 {status_missing}")
    require(body_a == body_missing == '{"detail":"项目不存在"}', "他人项目与不存在项目响应体不同")
    require(status_events == 404, f"B 订阅 A 的事件流期望 404，实际 {status_events}")
    require(
        "text/event-stream" not in headers_events.get("content-type", ""),
        "B 不应拿到事件流响应",
    )
    require(body_events == body_a, "B 的事件流 404 响应体与项目 404 不一致")
    entry["detail"] = (
        "匿名任务接口全部 401 且非事件流；B 的任务/事件/取消与不存在项目同为 404"
    )


def scenario_job_outage_boundary(
    entry: dict[str, Any], state: dict[str, Any]
) -> None:
    project_id, job_id = state["project_id"], state["job_id"]
    started = time.monotonic()
    status, headers, _ = raw_request("GET", job_path(project_id, job_id), timeout=5.0)
    elapsed_anonymous = time.monotonic() - started
    require(status == 401, f"停库且无 Cookie 期望 401，实际 {status}")
    require(elapsed_anonymous <= 5.0, "停库匿名 401 超过 5 秒")

    started = time.monotonic()
    status, headers, body = raw_request(
        "GET", job_path(project_id, job_id), cookie_token=state["token_a"], timeout=6.0
    )
    elapsed = time.monotonic() - started
    require(status == 503, f"停库且携带 Cookie 期望 503，实际 {status}：{body[:200]}")
    require(elapsed <= DEGRADE_TIMEOUT_SECONDS, f"停库任务查询耗时 {elapsed:.2f}s 超过 5 秒")
    require("set-cookie" not in headers, "停库 503 不应清除 Cookie")
    entry["detail"] = (
        f"停库期间任务接口匿名 401（{elapsed_anonymous * 1000:.0f}ms）、"
        f"携带 Cookie 在 {elapsed:.2f}s 返回 503 且未清 Cookie"
    )


def scenario_worker_isolation(
    entry: dict[str, Any], report: dict[str, Any]
) -> None:
    before = read_identity()
    stopped = compose("stop", "worker")
    require(stopped.returncode == 0, f"停止 worker 失败：{stopped.stderr.strip()}")
    health_status, health_body = http_status("/health")
    livez_status, livez_body = http_status("/livez")
    after = read_identity()
    started = compose("start", "worker")
    require(started.returncode == 0, f"启动 worker 失败：{started.stderr.strip()}")

    require(health_status == 200, f"停 worker 时 /health 期望 200，实际 {health_status}")
    require("database" in health_body, "停 worker 时 /health 响应体异常")
    require(livez_status == 200, f"停 worker 时 /livez 期望 200，实际 {livez_status}")
    for key in ("id", "pid", "started_at", "restart_count"):
        require(
            before.get(key) == after.get(key),
            f"停 worker 导致 app 身份变化：{key} {before.get(key)!r} -> {after.get(key)!r}",
        )
    entry["detail"] = (
        "只停 worker 时 /health 200、/livez 200，app 容器身份不变（已恢复 worker）"
    )


def scenario_job_two_jobs(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    result = run_probe("two-jobs", project_id=state["project_id"])
    require(result.get("a_step_outcome") == "progress", f"探针 A 未推进一步：{result}")
    require(result.get("b_unchanged") is True, f"推进 A 改动了 B：{result}")
    record_job_evidence(
        report,
        "job-two-jobs-isolation",
        a_next_step=result.get("a_next_step"),
        b_unchanged=result.get("b_unchanged"),
    )
    entry["detail"] = "真实 PostgreSQL：A 推进一步后 B 的进度/状态/租约令牌均未变"


def scenario_job_duplicate_start(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    stopped = compose("stop", "worker")
    require(stopped.returncode == 0, f"停止 worker 失败：{stopped.stderr.strip()}")
    try:
        before = run_probe("counts", project_id=project_id)

        def post() -> tuple[int, dict[str, Any]]:
            status, _, body = raw_request(
                "POST",
                job_path(project_id),
                cookie_token=token,
                payload={"operation": "test-simulation", "stage": "dual-platform-run"},
                timeout=20.0,
            )
            return status, json.loads(body) if body else {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: post(), range(2)))

        statuses = sorted(item[0] for item in results)
        require(statuses == [200, 202], f"并发开始期望 200/202，实际 {statuses}")
        jobs = [item[1]["job"] for item in results]
        require(
            jobs[0]["jobId"] == jobs[1]["jobId"] and jobs[0]["runId"] == jobs[1]["runId"],
            "并发开始返回了不同任务",
        )
        dedupe_flags = sorted(bool(job["deduplicated"]) for job in jobs)
        require(dedupe_flags == [False, True], f"重复开始标记异常：{dedupe_flags}")

        after = run_probe("counts", project_id=project_id)
        added_jobs = int(after["jobs"]) - int(before["jobs"])
        added_runs = int(after["runs"]) - int(before["runs"])
        require(added_jobs == 1, f"并发开始新增 jobs={added_jobs}，期望 1")
        require(added_runs == 1, f"并发开始新增 runs={added_runs}，期望 1（不得留下孤立 run）")

        job_id = str(jobs[0]["jobId"])
        cancel_status, _, cancel_body = api_call(
            "POST", f"{job_path(project_id, job_id)}/cancel", token
        )
        require(cancel_status == 200, f"清理取消期望 200，实际 {cancel_status}")
        require(
            isinstance(cancel_body, dict) and cancel_body["cancelApplied"] is True,
            "排队中的任务应能立即取消",
        )
        record_job_evidence(
            report,
            "job-duplicate-start-concurrent",
            job_id=job_id,
            run_id=str(jobs[0]["runId"]),
            jobs_added=added_jobs,
            runs_added=added_runs,
            deduplicated_flags=dedupe_flags,
        )
        entry["detail"] = (
            f"并发开始一个 202 + 一个 200，jobs 与 runs 各新增 1（jobId={job_id}）"
        )
    finally:
        started = compose("start", "worker")
        require(started.returncode == 0, f"恢复 worker 失败：{started.stderr.strip()}")


def scenario_job_resume(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    status, body = start_test_job(token, project_id)
    require(status == 202, f"断线场景开始任务期望 202，实际 {status}：{body}")
    job = body["job"]  # type: ignore[index]
    job_id, run_id = str(job["jobId"]), str(job["runId"])
    events_path = f"{job_path(project_id, job_id)}/events"

    first_seqs, _ = collect_sse(f"{events_path}?after=0", token, until_seq=4)
    require(first_seqs == [1, 2, 3, 4], f"第一段 SSE 序号应为 1–4，实际 {first_seqs}")
    time.sleep(0.5)

    second_seqs, end = collect_sse(f"{events_path}?after=4", token)
    require(end is not None, "重连后未收到 stream-end")
    require(int(end.get("lastSeq", 0)) == 10, f"stream-end lastSeq 应为 10：{end}")
    require(second_seqs == list(range(5, 11)), f"第二段应为 5–10，实际 {second_seqs}")

    combined = first_seqs + second_seqs
    require(combined == list(range(1, 11)), f"拼接后应为 1–10 且各一次，实际 {combined}")

    history = fetch_job_events(token, project_id, job_id, after=4)
    require(event_seqs(history) == list(range(5, 11)), "JSON 历史与 SSE 第二段不一致")
    full = fetch_job_events(token, project_id, job_id, after=0)
    require(event_seqs(full) == list(range(1, 11)), "JSON 历史全量序号异常")

    state["resume_job_id"] = job_id
    state["resume_run_id"] = run_id
    record_job_evidence(
        report,
        "job-resume-after-disconnect",
        job_id=job_id,
        run_id=run_id,
        first_segment_seqs=first_seqs,
        second_segment_seqs=second_seqs,
        resumed_from=4,
        combined_seqs=combined,
    )
    entry["detail"] = (
        f"第一段 1–4，第二段 5–10，拼接 1–10；jobId={job_id} runId={run_id}"
    )


def scenario_job_cursor_precedence(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    job_id, run_id = state["resume_job_id"], state["resume_run_id"]
    events_path = f"{job_path(project_id, job_id)}/events"

    seqs, end = collect_sse(
        f"{events_path}?after=4",
        token,
        headers={"Last-Event-ID": f"{run_id}:7"},
    )
    require(seqs == [8, 9, 10], f"Last-Event-ID 应优先于 after，实际序号 {seqs}")
    require(end is not None and int(end.get("lastSeq", 0)) == 10, f"stream-end 异常：{end}")
    record_job_evidence(
        report,
        "job-last-event-id-priority",
        job_id=job_id,
        run_id=run_id,
        last_event_id=7,
        after=4,
        seqs=seqs,
    )
    entry["detail"] = "Last-Event-ID=7 与 after=4 同时存在时只补发 8–10"


def scenario_job_terminal_reconnect(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    job_id = state["resume_job_id"]
    events_path = f"{job_path(project_id, job_id)}/events"

    started = time.monotonic()
    seqs, end = collect_sse(f"{events_path}?after=10", token, deadline=10.0)
    elapsed = time.monotonic() - started
    require(seqs == [], f"after=10 不应补发业务事件，实际 {seqs}")
    require(end is not None, "终态任务 after=10 未立即收到 stream-end")
    require(int(end.get("lastSeq", 0)) == 10, f"stream-end lastSeq 异常：{end}")
    require(elapsed <= 2.0, f"终态重连接近挂起：{elapsed:.2f}s")

    status, headers, body = api_call(
        "GET", f"{events_path}?after=11", token
    )
    require(status == 400, f"after=11 期望 400，实际 {status}")
    require(body == {"detail": "游标无效"}, f"非法游标响应体异常：{body}")
    require("text/event-stream" not in headers.get("content-type", ""), "非法游标不应建立事件流")

    record_job_evidence(
        report,
        "job-terminal-reconnect",
        job_id=job_id,
        run_id=state["resume_run_id"],
        reconnect_seconds=round(elapsed, 3),
        invalid_cursor_status=status,
    )
    entry["detail"] = (
        f"终态 after=10 在 {elapsed:.2f}s 内收到 stream-end；after=11 返回 400 JSON"
    )


def scenario_job_worker_takeover(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    status, body = start_test_job(token, project_id)
    require(status == 202, f"中断恢复开始任务期望 202，实际 {status}：{body}")
    job = body["job"]  # type: ignore[index]
    job_id, run_id = str(job["jobId"]), str(job["runId"])

    wait_job(token, project_id, job_id, lambda item: int(item["stepsApplied"]) >= 2, timeout=40.0)
    stopped = compose("stop", "worker")
    require(stopped.returncode == 0, f"停止 worker 失败：{stopped.stderr.strip()}")
    try:
        snapshot = fetch_job(token, project_id, job_id)
        steps_at_stop = int(snapshot["stepsApplied"])
        attempts_before = int(snapshot["attempts"])
        require(snapshot["status"] == "running", "中断后持久状态应保持 running")

        takeover = wait_job(
            token,
            project_id,
            job_id,
            lambda item: bool(item["awaitingTakeover"]),
            timeout=15.0,
        )
        require(takeover["status"] == "running", "等待接手期间状态不应变化")
    finally:
        started_worker = compose("start", "worker")
        require(started_worker.returncode == 0, f"恢复 worker 失败：{started_worker.stderr.strip()}")

    final = wait_job(
        token,
        project_id,
        job_id,
        lambda item: item["status"] in ("succeeded", "failed", "canceled"),
        timeout=JOB_TIMEOUT_SECONDS,
    )
    require(final["status"] == "succeeded", f"接手后任务应成功，实际 {final['status']}")
    require(
        int(final["attempts"]) > attempts_before,
        f"接手应增加 attempts：{attempts_before} -> {final['attempts']}",
    )
    events = fetch_job_events(token, project_id, job_id)
    require(event_seqs(events) == list(range(1, 11)), f"接手后事件序号异常：{event_seqs(events)}")
    effects = fetch_effects(token, project_id, job_id)
    require(int(effects["total"]) == 10, f"接手后副作用总数应为 10，实际 {effects['total']}")
    require(int(effects["distinctSteps"]) == 10, "接手后副作用步骤应唯一")
    require(effects["steps"] == list(range(1, 11)), "接手后副作用序号应为 1–10")

    record_job_evidence(
        report,
        "job-worker-crash-takeover",
        job_id=job_id,
        run_id=run_id,
        steps_at_stop=steps_at_stop,
        attempts_before=attempts_before,
        attempts_after=int(final["attempts"]),
        seqs=event_seqs(events),
        effects_total=int(effects["total"]),
    )
    entry["detail"] = (
        f"停止于第 {steps_at_stop} 步，attempts {attempts_before}→{final['attempts']}，"
        "最终副作用 10 条且步骤唯一"
    )


def scenario_job_lease_probe(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    stopped = compose("stop", "worker")
    require(stopped.returncode == 0, f"停止 worker 失败：{stopped.stderr.strip()}")
    try:
        status, body = start_test_job(token, project_id)
        require(status == 202, f"旧令牌场景开始任务期望 202，实际 {status}：{body}")
        job = body["job"]  # type: ignore[index]
        job_id, run_id = str(job["jobId"]), str(job["runId"])
        result = run_probe("takeover", job_id=job_id)
        require(
            result.get("first_segment") == ["progress", "progress", "progress"],
            f"第一次领取应提交 1–3 步：{result}",
        )
        require(
            result.get("second_segment") == ["progress", "progress"],
            f"第二次领取应提交 4–5 步：{result}",
        )
        require(int(result.get("steps_at_handover", 0)) == 5, f"接手时应为第 5 步：{result}")
        require(result.get("old_heartbeat_ok") is False, "旧令牌续租必须被拒绝")
        require(result.get("old_step_outcome") == "lost", "旧令牌写第 6 步必须被拒绝")
        require(result.get("unchanged_after_old_token") is True, "旧令牌写入了数据")
        require(int(result.get("attempts", 0)) == 2, f"应经历两次领取：{result}")
        require(result.get("final_status") == "succeeded", f"接手后应成功：{result}")
        require(
            result.get("final_seqs") == list(range(1, 11)),
            f"最终事件序号应为 1–10：{result.get('final_seqs')}",
        )
        require(int(result.get("final_effects", 0)) == 10, "最终副作用应为 10")

        record_job_evidence(
            report,
            "job-lease-probe-takeover",
            job_id=job_id,
            run_id=run_id,
            steps_at_handover=int(result["steps_at_handover"]),
            old_heartbeat_ok=result.get("old_heartbeat_ok"),
            old_step_outcome=result.get("old_step_outcome"),
            attempts=int(result["attempts"]),
            seqs=result.get("final_seqs"),
        )
        entry["detail"] = (
            "真实 PostgreSQL：旧令牌续租 false、写第 6 步 lost，进度/事件/副作用未变"
        )
    finally:
        started_worker = compose("start", "worker")
        require(started_worker.returncode == 0, f"恢复 worker 失败：{started_worker.stderr.strip()}")


def scenario_job_lease_lock_wait(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    """请求在租约到期前开始、在行锁等待中跨过到期点：不得续租，也不得写入。"""
    stopped = compose("stop", "worker")
    require(stopped.returncode == 0, f"停止 worker 失败：{stopped.stderr.strip()}")
    try:
        result = run_probe("lease-wait", project_id=state["project_id"])
    finally:
        started_worker = compose("start", "worker")
        require(started_worker.returncode == 0, f"恢复 worker 失败：{started_worker.stderr.strip()}")

    cases = result.get("cases") or []
    require(len(cases) == 2, f"锁等待回归应覆盖 heartbeat 与 commit_step：{result}")
    for case in cases:
        require(case["blocked_on_row"] is True, f"未观察到行锁等待：{case}")
        require(
            case["actual"] == case["expected"],
            f"{case['operation']} 期望 {case['expected']}，实际 {case['actual']}",
        )
        require(int(case["steps"]) == 0, f"{case['operation']} 写入了步骤：{case}")
        require(int(case["events"]) == 0, f"{case['operation']} 写入了事件：{case}")
        require(case["expiry_extended"] is False, f"{case['operation']} 延长了租约：{case}")
    require(result.get("ok") is True, f"锁等待回归失败：{result}")

    record_job_evidence(
        report,
        "job-lease-lock-wait",
        cases=[
            {
                "operation": case["operation"],
                "actual": case["actual"],
                "blocked_on_row": case["blocked_on_row"],
                "expiry_extended": case["expiry_extended"],
                "steps": case["steps"],
                "events": case["events"],
            }
            for case in cases
        ],
    )
    entry["detail"] = (
        "真实 PostgreSQL：到期前开始、锁等待跨过到期点后 heartbeat=false、"
        "commit_step=lost，租约未延长且无步骤/事件写入"
    )


def scenario_job_cancel(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    status, body = start_test_job(token, project_id)
    require(status == 202, f"取消场景开始任务期望 202，实际 {status}：{body}")
    job = body["job"]  # type: ignore[index]
    job_id, run_id = str(job["jobId"]), str(job["runId"])

    running = wait_job(
        token, project_id, job_id, lambda item: int(item["stepsApplied"]) >= 3, timeout=40.0
    )
    steps_at_cancel = int(running["stepsApplied"])

    status, _, cancel_body = api_call(
        "POST", f"{job_path(project_id, job_id)}/cancel", token
    )
    require(status == 200, f"取消期望 200，实际 {status}：{cancel_body}")
    require(isinstance(cancel_body, dict), "取消响应不是对象")
    require(cancel_body["cancelApplied"] is True, "运行中取消应生效")
    require(cancel_body["job"]["status"] == "canceled", "取消后状态应为 canceled")

    wait_job(token, project_id, job_id, lambda item: item["status"] == "canceled", timeout=20.0)
    events = fetch_job_events(token, project_id, job_id)
    types = event_types(events)
    seqs = event_seqs(events)
    require("completed" not in types, "取消后不应出现 completed")
    require(types[-1] == "canceled", f"终态事件应为 canceled：{types}")
    require(
        seqs[-1] == steps_at_cancel + 1,
        f"canceled 序号应为 {steps_at_cancel + 1}，实际 {seqs[-1]}",
    )
    effects = fetch_effects(token, project_id, job_id)
    require(
        int(effects["total"]) == steps_at_cancel,
        f"取消后副作用数应等于已提交步数 {steps_at_cancel}，实际 {effects['total']}",
    )

    status_again, _, again = api_call(
        "POST", f"{job_path(project_id, job_id)}/cancel", token
    )
    require(status_again == 200, "重复取消应返回 200")
    require(isinstance(again, dict) and again["cancelApplied"] is False, "重复取消不应生效")
    require(event_seqs(fetch_job_events(token, project_id, job_id)) == seqs, "重复取消追加了事件")

    record_job_evidence(
        report,
        "job-cancel-running",
        job_id=job_id,
        run_id=run_id,
        steps_at_cancel=steps_at_cancel,
        canceled_seq=seqs[-1],
        seqs=seqs,
        effects_total=int(effects["total"]),
    )
    entry["detail"] = (
        f"第 {steps_at_cancel} 步后取消，canceled seq={seqs[-1]}，无 completed，副作用一致"
    )


def scenario_job_fail(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    stopped = compose("stop", "worker")
    require(stopped.returncode == 0, f"停止 worker 失败：{stopped.stderr.strip()}")
    try:
        status, body = start_test_job(token, project_id, fail_at_step=3)
        require(status == 202, f"受控失败开始任务期望 202，实际 {status}：{body}")
        job = body["job"]  # type: ignore[index]
        job_id, run_id = str(job["jobId"]), str(job["runId"])
    finally:
        started_worker = compose("start", "worker")
        require(started_worker.returncode == 0, f"恢复 worker 失败：{started_worker.stderr.strip()}")

    final = wait_job(
        token,
        project_id,
        job_id,
        lambda item: item["status"] in ("succeeded", "failed", "canceled"),
    )
    require(final["status"] == "failed", f"受控失败终态应为 failed，实际 {final['status']}")
    require(int(final["stepsApplied"]) == 2, f"失败前应提交 2 步，实际 {final['stepsApplied']}")

    events = fetch_job_events(token, project_id, job_id)
    types = event_types(events)
    require("completed" not in types, "失败任务不应出现 completed")
    require(types[-1] == "failed", f"终态事件应为 failed：{types}")
    require(event_seqs(events)[-1] == 3, f"failed 事件应为 seq=3：{event_seqs(events)}")
    effects = fetch_effects(token, project_id, job_id)
    require(int(effects["total"]) == 2, f"失败前只应提交 2 条副作用，实际 {effects['total']}")

    record_job_evidence(
        report,
        "job-fail-controlled",
        job_id=job_id,
        run_id=run_id,
        seqs=event_seqs(events),
        effects_total=int(effects["total"]),
    )
    entry["detail"] = "failAtStep=3 持久化后生效：failed seq=3、无 completed、副作用 2 条"


def scenario_job_restart_after_terminal(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    previous_job, previous_run = state["job_id"], state["run_id"]
    status, body = start_test_job(token, project_id)
    require(status == 202, f"终态后重新开始期望 202，实际 {status}：{body}")
    job = body["job"]  # type: ignore[index]
    job_id, run_id = str(job["jobId"]), str(job["runId"])
    require(job_id != previous_job, "重新开始应产生新的 jobId")
    require(run_id != previous_run, "重新开始应产生新的 runId")

    def has_event(item: dict[str, object]) -> bool:
        return int(item["stepsApplied"]) >= 1

    wait_job(token, project_id, job_id, has_event, timeout=30.0)
    events = fetch_job_events(token, project_id, job_id)
    require(event_seqs(events)[:1] == [1], f"新 run 的 seq 应从 1 开始：{event_seqs(events)}")

    cleanup_status, _, cleanup = api_call(
        "POST", f"{job_path(project_id, job_id)}/cancel", token
    )
    require(cleanup_status == 200, f"清理取消期望 200，实际 {cleanup_status}")
    require(isinstance(cleanup, dict), "清理取消响应不是对象")

    record_job_evidence(
        report,
        "job-restart-after-terminal",
        job_id=job_id,
        run_id=run_id,
        previous_job_id=previous_job,
        previous_run_id=previous_run,
        first_seq=event_seqs(events)[0],
    )
    entry["detail"] = f"终态后新任务 jobId={job_id} runId={run_id}，seq 从 1 开始"


def scenario_job_cancel_race(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    result = run_probe("cancel-race", project_id=state["project_id"], trials=5)
    require(result.get("ok") is True, f"取消/完成竞态失败：{result}")
    trials = result.get("trials") or []
    require(len(trials) == 5, f"竞态应执行 5 次，实际 {len(trials)}")
    for trial in trials:
        require(trial["single_terminal"] is True, f"出现多个终态：{trial}")
        require(trial["consistent"] is True, f"状态与事件不一致：{trial}")
    record_job_evidence(
        report,
        "job-cancel-final-race",
        trials=[trial["status"] for trial in trials],
        single_terminal=True,
    )
    entry["detail"] = (
        "真实 PostgreSQL 竞态 5 次：每次只有一个终态，状态与事件一致（"
        + ", ".join(str(trial["status"]) for trial in trials)
        + "）"
    )



# ---------------------------------------------------------------- 材料验收

DOCUMENT_TIMEOUT_SECONDS = 90
DOCUMENT_PAGE = "/api/projects/{project_id}/documents"


def record_document_evidence(
    report: dict[str, Any], scenario_id: str, **fields: object
) -> None:
    report["document_evidence"].append({"scenario": scenario_id, **fields})


def build_pdf_bytes(pages: list[list[str]]) -> bytes:
    """与 tests/pdf_fixtures.py 相同的最小 PDF，保持验收脚本零第三方依赖。"""
    page_count = len(pages)
    font_number = 3 + page_count * 2
    objects: dict[int, bytes] = {}
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(page_count))
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("utf-8")
    )
    for index, lines in enumerate(pages):
        page_number = 3 + index * 2
        content_number = page_number + 1
        objects[page_number] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_number} 0 R >> >> "
            f"/Contents {content_number} 0 R >>"
        ).encode("utf-8")
        stream = _pdf_content(lines)
        objects[content_number] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("utf-8")
            + stream
            + b"\nendstream"
        )
    objects[font_number] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    output = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(output)
        output += f"{number} 0 obj\n".encode("utf-8")
        output += objects[number]
        output += b"\nendobj\n"
    xref_offset = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode("utf-8")
    output += b"0000000000 65535 f \n"
    for number in sorted(objects):
        output += f"{offsets[number]:010d} 00000 n \n".encode("utf-8")
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("utf-8")
    return bytes(output)


def _pdf_content(lines: list[str]) -> bytes:
    if not lines:
        return b""
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    parts = ["BT", "/F1 12 Tf", "72 780 Td"]
    for index, line in enumerate(escaped):
        if index:
            parts.append("0 -18 Td")
        parts.append(f"({line}) Tj")
    parts.append("ET")
    return "\n".join(parts).encode("utf-8")


def pdf_page_contains(data: bytes, page_index: int, marker: bytes) -> bool:
    """按 /Kids 顺序取指定页对象，检查其内容流包含标记。"""
    kids = re.search(rb"/Kids\s*\[(.*?)\]", data, re.S)
    if kids is None:
        return False
    refs = re.findall(rb"(\d+)\s+0\s+R", kids.group(1))
    if len(refs) <= page_index:
        return False
    page_ref = refs[page_index]
    page_obj = re.search(
        rb"(?m)^" + page_ref + rb"\s+0\s+obj(.*?)endobj", data, re.S
    )
    if page_obj is None:
        return False
    contents = re.search(rb"/Contents\s+(\d+)\s+0\s+R", page_obj.group(1))
    if contents is None:
        return False
    stream = re.search(
        rb"(?m)^" + contents.group(1) + rb"\s+0\s+obj(.*?)endobj", data, re.S
    )
    return bool(stream and marker in stream.group(1))


def padded_text(size: int) -> bytes:
    """少量正文 + 可折叠空白，精确补齐到 size 字节。"""
    output = bytearray("验收材料：以下为少量正文与可折叠空白。\n".encode("utf-8"))
    filler = ("填补行" + " " * 4000 + "\n").encode("utf-8")
    while len(output) + len(filler) <= size:
        output += filler
    output += b" " * (size - len(output))
    return bytes(output)


class DocumentFixtures:
    """本次执行生成的全部材料夹具；结束时整体删除。"""

    def __init__(self) -> None:
        self.suffix = secrets.token_hex(4).upper()
        self.small_text = "验收材料第一段。\n\n验收材料第二段。".encode("utf-8")
        self.three_page = build_pdf_bytes(
            [
                [f"PAGE-ONE-ECHOSIM-MARKER-{self.suffix}"],
                [],
                [f"PAGE-THREE-ECHOSIM-MARKER-{self.suffix}"],
            ]
        )
        self.three_page_marker = f"PAGE-THREE-ECHOSIM-MARKER-{self.suffix}"
        self.slow_pdf = build_pdf_bytes(
            [
                [f"SLOW-PAGE-ONE-{self.suffix}"],
                [],
                [f"SLOW-PAGE-THREE-{self.suffix}"],
            ]
        )
        self.header_footer = build_pdf_bytes(
            [
                [f"REPEATED-HEADER-{self.suffix}", f"BODY-ONE-{self.suffix}", "file:///tmp/print-one.pdf"],
                [f"REPEATED-HEADER-{self.suffix}", f"BODY-TWO-{self.suffix}", "file:///tmp/print-two.pdf"],
                [f"REPEATED-HEADER-{self.suffix}", f"BODY-THREE-{self.suffix}", "file:///tmp/print-three.pdf"],
            ]
        )
        self.no_text_pdf = build_pdf_bytes([[]])
        self.page_limit_exact = build_pdf_bytes(
            [[f"LIMIT-A-{self.suffix}"], [f"LIMIT-B-{self.suffix}"], [f"LIMIT-C-{self.suffix}"]]
        )
        self.page_limit_over = build_pdf_bytes(
            [
                [f"LIMIT-A-{self.suffix}"],
                [f"LIMIT-B-{self.suffix}"],
                [f"LIMIT-C-{self.suffix}"],
                [f"LIMIT-D-{self.suffix}"],
            ]
        )
        self.history_over_pdf = build_pdf_bytes(
            [
                [f"HISTORY-A-{self.suffix}"],
                [f"HISTORY-B-{self.suffix}"],
                [f"HISTORY-C-{self.suffix}"],
                [f"HISTORY-D-{self.suffix}"],
            ]
        )
        self.emoji_text = (
            "开头 emoji 😀😀 段落。\r\n\r\n"
            "第二段包含合法路径 C:\\data\\report.csv 与 file:///tmp/a.txt。\r\n\r\n"
            "第三段结尾。"
        ).encode("utf-8")
        self.gb18030_text = "中文材料第一段。\n\n中文材料第二段。".encode("gb18030")
        self.limit_text = 20 * 1024 * 1024
        self.exact_limit = padded_text(self.limit_text)
        self.over_limit = self.exact_limit + b" "


def document_path(project_id: str, document_id: str | None = None) -> str:
    base = DOCUMENT_PAGE.format(project_id=project_id)
    return base if document_id is None else f"{base}/{document_id}"


def upload_document(
    token: str,
    project_id: str,
    filename: str,
    data: bytes,
    content_type: str,
) -> tuple[int, dict[str, str], object | None]:
    boundary = "----echosim" + secrets.token_hex(8)
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("ascii"),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
            ).encode("ascii"),
            f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
            data,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    request = urllib.request.Request(
        BASE_URL + document_path(project_id), data=body, method="POST"
    )
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Cookie", f"{SESSION_COOKIE}={token}")
    request.add_header("Origin", BASE_URL)
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = response.status
            headers = {key.lower(): value for key, value in response.headers.items()}
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        headers = {key.lower(): value for key, value in exc.headers.items()}
        raw = exc.read()
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        payload = None
    return status, headers, payload


def fetch_document(
    token: str, project_id: str, document_id: str
) -> dict[str, object]:
    status, _, body = api_call(
        "GET", document_path(project_id, document_id), token
    )
    require(status == 200, f"材料详情期望 200，实际 {status}：{body}")
    require(isinstance(body, dict) and "document" in body, f"材料详情缺少 document：{body}")
    return body["document"]  # type: ignore[index]


def list_documents(token: str, project_id: str) -> list[dict[str, object]]:
    status, _, body = api_call("GET", document_path(project_id), token)
    require(status == 200, f"材料列表期望 200，实际 {status}：{body}")
    require(isinstance(body, dict) and isinstance(body.get("documents"), list), f"材料列表格式异常：{body}")
    return body["documents"]  # type: ignore[return-value]


def fetch_chunks(
    token: str,
    project_id: str,
    document_id: str,
    *,
    after: int = 0,
    limit: int = 50,
) -> dict[str, object]:
    status, _, body = api_call(
        "GET",
        f"{document_path(project_id, document_id)}/chunks?after={after}&limit={limit}",
        token,
    )
    require(status == 200, f"材料分块期望 200，实际 {status}：{body}")
    require(isinstance(body, dict), f"材料分块不是对象：{body}")
    return body


def fetch_document_bytes(
    token: str, path: str
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(BASE_URL + path)
    request.add_header("Cookie", f"{SESSION_COOKIE}={token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, {
                key.lower(): value for key, value in response.headers.items()
            }, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {
            key.lower(): value for key, value in exc.headers.items()
        }, exc.read()


def wait_document(
    token: str,
    project_id: str,
    document_id: str,
    predicate: Callable[[dict[str, object]], bool],
    *,
    timeout: float = DOCUMENT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    def probe() -> dict[str, object] | None:
        try:
            document = fetch_document(token, project_id, document_id)
        except AssertionError:
            return None
        return document if predicate(document) else None

    result = wait_for(probe, timeout=timeout, interval=0.3)
    require(result is not None, f"材料 {document_id} 未在 {timeout}s 内满足条件")
    return result  # type: ignore[return-value]


def upload_and_wait(
    state: dict[str, Any],
    fixtures: DocumentFixtures,
    filename: str,
    data: bytes,
    content_type: str,
    *,
    expect_ready: bool = True,
) -> dict[str, object]:
    token, project_id = state["token_a"], state["project_id"]
    status, _, body = upload_document(token, project_id, filename, data, content_type)
    require(status in (200, 201), f"上传 {filename} 期望 200/201，实际 {status}：{body}")
    assert isinstance(body, dict)
    document = body["document"]  # type: ignore[index]
    document_id = str(document["documentId"])
    final = wait_document(
        token,
        project_id,
        document_id,
        lambda item: item["status"] in (("ready",) if expect_ready else ("ready", "failed")),
    )
    return final


def recreate_worker(overrides: dict[str, str]) -> None:
    """用指定环境重建 worker（不影响 app），并等待健康。"""
    saved = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        result = compose("up", "-d", "--no-deps", "worker")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    require(result.returncode == 0, f"重建 worker 失败：{result.stderr.strip()[-400:]}")

    def healthy() -> bool:
        check = compose("ps", "--format", "{{.Health}}", "worker")
        return check.returncode == 0 and check.stdout.strip() == "healthy"

    require(wait_for(healthy, timeout=60, interval=1.0), "worker 未在 60s 内恢复健康")


def restore_worker_defaults() -> None:
    """尽力把 worker 恢复为默认配置，避免失败运行留下测试参数。"""
    try:
        compose("up", "-d", "--no-deps", "worker")
        compose("start", "worker")
    except Exception:  # noqa: BLE001 - 收尾不掩盖主流程
        pass


def scenario_documents_smoke(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    """默认流程的轻量材料回归：上传小 TXT 到 ready，列表与分块可见，匿名 401。"""
    token, project_id = state["token_a"], state["project_id"]
    before = len(list_documents(token, project_id))
    document = upload_and_wait(
        state, fixtures, "smoke.txt", fixtures.small_text, "text/plain"
    )
    require(document["status"] == "ready", f"材料未就绪：{document['status']}")
    require(int(document["chunkCount"]) >= 1, "就绪材料应至少有一个分块")

    after = list_documents(token, project_id)
    require(len(after) == before + 1, "上传后材料列表数量应加一")
    chunks = fetch_chunks(token, project_id, str(document["documentId"]))
    require(len(chunks["chunks"]) >= 1, "分块读取应至少返回一项")  # type: ignore[arg-type]

    anonymous_status, _, anonymous_body = raw_request(
        "GET", document_path(project_id)
    )
    require(anonymous_status == 401, f"匿名材料列表期望 401，实际 {anonymous_status}")
    require(
        json.loads(anonymous_body) == {"detail": "未登录"},
        "匿名材料列表响应体不符合契约",
    )
    record_document_evidence(
        report,
        "documents-smoke",
        document_id=document["documentId"],
        job_id=document.get("jobId"),
        run_id=document.get("runId"),
        chunk_count=document["chunkCount"],
        anonymous_status=anonymous_status,
    )
    entry["detail"] = (
        f"小 TXT 上传后到达 ready，列表可见且 {document['chunkCount']} 个分块；匿名列表 401"
    )


def scenario_document_pdf_pages(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    document = upload_and_wait(
        state, fixtures, "three-pages.pdf", fixtures.three_page, "application/pdf"
    )
    require(int(document["pageCount"] or 0) == 3, f"总页数应为 3，实际 {document['pageCount']}")
    chunks = fetch_chunks(token, project_id, str(document["documentId"]))["chunks"]
    assert isinstance(chunks, list)
    pages = [chunk["pageNo"] for chunk in chunks]
    require(pages == [1, 3], f"分块页码应为 [1, 3]，实际 {pages}")
    third = next(chunk for chunk in chunks if chunk["pageNo"] == 3)
    require(
        third["text"] == fixtures.three_page_marker,
        f"第 3 页文本不符：{third['text']!r}",
    )
    require(
        str(third["sourceUrl"]).endswith("#page=3"),
        f"来源链接未指向第 3 页：{third['sourceUrl']}",
    )

    status, headers, payload = fetch_document_bytes(
        token, str(third["sourceUrl"]).split("#", 1)[0]
    )
    require(status == 200, f"原件期望 200，实际 {status}")
    require(headers.get("content-type") == "application/pdf", "原件类型不是 PDF")
    digest = hashlib.sha256(payload).hexdigest()
    require(digest == hashlib.sha256(fixtures.three_page).hexdigest(), "原件字节与上传不一致")
    require(
        pdf_page_contains(payload, 2, fixtures.three_page_marker.encode("ascii")),
        "按 /Kids 顺序第 3 个页对象不含第 3 页标记",
    )
    second_page = fetch_chunks(
        token, project_id, str(document["documentId"]), after=0, limit=50
    )["chunks"]
    require(
        all(chunk["pageNo"] != 2 for chunk in second_page),  # type: ignore[index]
        "空白页不应产生分块",
    )
    record_document_evidence(
        report,
        "document-pdf-pages",
        document_id=document["documentId"],
        job_id=document.get("jobId"),
        run_id=document.get("runId"),
        page_count=document["pageCount"],
        chunk_count=document["chunkCount"],
        page3_chunk_page_no=third["pageNo"],
        page3_source_url=third["sourceUrl"],
        original_sha256=digest,
    )
    entry["detail"] = (
        "三页 PDF：pageCount=3、第 2 页 0 块、第 3 页 pageNo=3 且来源 #page=3、原件哈希一致"
    )
    state["three_page_document_id"] = str(document["documentId"])
    state["three_page_sha256"] = digest


def scenario_document_pdf_cleaning(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    document = upload_and_wait(
        state, fixtures, "cleaning.pdf", fixtures.header_footer, "application/pdf"
    )
    chunks = fetch_chunks(token, project_id, str(document["documentId"]))["chunks"]
    assert isinstance(chunks, list)
    joined = "\n".join(str(chunk["text"]) for chunk in chunks)
    require("REPEATED-HEADER" not in joined, "重复页眉未被清理")
    require("file:///tmp/print" not in joined, "打印路径未被清理")
    for marker in ("BODY-ONE", "BODY-TWO", "BODY-THREE"):
        require(f"{marker}-{fixtures.suffix}" in joined, f"正文 {marker} 被误删")
    require(
        [chunk["pageNo"] for chunk in chunks] == [1, 2, 3],
        "清理后物理页码不应改变",
    )
    record_document_evidence(
        report,
        "document-pdf-cleaning",
        document_id=document["documentId"],
        page_count=document["pageCount"],
        chunk_count=document["chunkCount"],
    )
    entry["detail"] = "PDF 页眉、页脚打印路径被清理，正文与物理页码保留"


def scenario_document_text(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    document = upload_and_wait(
        state, fixtures, "emoji.txt", fixtures.emoji_text, "text/plain"
    )
    document_id = str(document["documentId"])
    chunks = fetch_chunks(token, project_id, document_id)["chunks"]
    assert isinstance(chunks, list) and chunks
    text = "\n".join(str(chunk["text"]) for chunk in chunks)
    require("C:\\data\\report.csv" in text, "正文合法路径被误删")
    require("file:///tmp/a.txt" in text, "正文合法 URL 被误删")

    source_status, source_headers, source_bytes = fetch_document_bytes(
        token, document_path(project_id, document_id) + "/source"
    )
    require(source_status == 200, f"原文视图期望 200，实际 {source_status}")
    require(
        source_headers.get("content-type", "").startswith("text/plain"),
        "原文视图类型不是纯文本",
    )
    source = source_bytes.decode("utf-8")
    first = chunks[0]
    start, end = int(first["sourceStart"]), int(first["sourceEnd"])
    encoded_source = source.encode("utf-16-le")
    sliced = encoded_source[start * 2 : end * 2].decode("utf-16-le")
    require(
        re.sub(r"\s+", "", sliced)[:16]
        == re.sub(r"\s+", "", str(first["text"]))[:16],
        "含 emoji 的偏移在原文中切片不一致",
    )

    gb_document = upload_and_wait(
        state, fixtures, "chinese.txt", fixtures.gb18030_text, "text/plain"
    )
    gb_id = str(gb_document["documentId"])
    file_status, file_headers, file_bytes = fetch_document_bytes(
        token, document_path(project_id, gb_id) + "/file"
    )
    require(file_status == 200, f"GB18030 原件期望 200，实际 {file_status}")
    require(
        file_headers.get("x-content-type-options") == "nosniff",
        "原件缺少 nosniff",
    )
    require(
        hashlib.sha256(file_bytes).hexdigest()
        == hashlib.sha256(fixtures.gb18030_text).hexdigest(),
        "GB18030 原件字节与上传不一致",
    )
    _, _, gb_source = fetch_document_bytes(
        token, document_path(project_id, gb_id) + "/source"
    )
    require(
        gb_source.decode("utf-8") == fixtures.gb18030_text.decode("gb18030"),
        "GB18030 原文视图解码不一致",
    )
    record_document_evidence(
        report,
        "document-text",
        document_id=document_id,
        chunk_count=document["chunkCount"],
        gb18030_document_id=gb_id,
        emoji_source_start=start,
        emoji_source_end=end,
    )
    entry["detail"] = "文本：换行与空白归一、合法路径保留、emoji 偏移与 /source 一致、GB18030 原件与原文分别正确"


def scenario_document_boundaries(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    before = len(list_documents(token, project_id))

    document = upload_and_wait(
        state, fixtures, "exact-20mib.txt", fixtures.exact_limit, "text/plain"
    )
    require(document["status"] == "ready", "恰好 20 MiB 的材料应最终 ready")
    after_upload = len(list_documents(token, project_id))
    require(after_upload == before + 1, "恰好 20 MiB 首次上传应新增一份材料")

    over_status, _, over_body = upload_document(
        token, project_id, "over-limit.txt", fixtures.over_limit, "text/plain"
    )
    require(over_status == 413, f"20 MiB + 1 期望 413，实际 {over_status}")
    assert isinstance(over_body, dict)
    require(over_body.get("code") == "file_too_large", f"超限原因标识不符：{over_body}")

    empty_status, _, empty_body = upload_document(
        token, project_id, "empty.txt", b"", "text/plain"
    )
    require(empty_status == 400, f"空文件期望 400，实际 {empty_status}")
    assert isinstance(empty_body, dict)
    require(empty_body.get("code") == "empty_file", f"空文件原因标识不符：{empty_body}")
    require(
        len(list_documents(token, project_id)) == after_upload,
        "被拒绝的上传不应新增材料",
    )

    no_text = upload_and_wait(
        state,
        fixtures,
        "no-text.pdf",
        fixtures.no_text_pdf,
        "application/pdf",
        expect_ready=False,
    )
    require(no_text["status"] == "failed", "无文字层 PDF 应失败")
    failure = no_text.get("failure") or {}
    require(failure.get("code") == "no_text_layer", f"失败原因不符：{failure}")
    require(int(no_text["chunkCount"]) == 0, "失败材料不应有分块")

    record_document_evidence(
        report,
        "document-boundaries",
        document_id=document["documentId"],
        page_count=document.get("pageCount"),
        chunk_count=document["chunkCount"],
        over_status=over_status,
        empty_status=empty_status,
        no_text_code=failure.get("code"),
    )
    entry["detail"] = (
        "恰好 20 MiB 新增并 ready；20 MiB + 1 返回 413、空文件 400 且不新增；"
        "无文字层 PDF 失败为 no_text_layer 且 0 块"
    )


def scenario_document_page_limit(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    recreate_worker({"DOCUMENT_MAX_PAGES": "3"})
    try:
        token, project_id = state["token_a"], state["project_id"]
        exact = upload_and_wait(
            state, fixtures, "limit-3.pdf", fixtures.page_limit_exact, "application/pdf"
        )
        require(exact["status"] == "ready", "恰好 3 页应正常解析")
        over = upload_and_wait(
            state,
            fixtures,
            "limit-4.pdf",
            fixtures.page_limit_over,
            "application/pdf",
            expect_ready=False,
        )
        require(over["status"] == "failed", "4 页应因超过上限失败")
        failure = over.get("failure") or {}
        require(failure.get("code") == "too_many_pages", f"失败原因不符：{failure}")
        require("3" in str(failure.get("message")), "失败说明应包含当前上限 3")
        require(
            int(over.get("pageCount") or 0) == 4,
            f"失败材料应保留实际页数 4，实际 {over.get('pageCount')}",
        )
        status_limits, _, limits_body = api_call(
            "GET", document_path(project_id), token
        )
        require(status_limits == 200, f"材料列表期望 200，实际 {status_limits}")
        assert isinstance(limits_body, dict)
        limits = limits_body.get("limits") or {}
        # app 与 worker 共享同一配置；本场景只重启 worker，因此 app 侧上限仍是默认值。
        # 关键断言是失败材料保留实际页数，前端用“实际页数 + 配置上限”渲染提示。
        require(
            int(limits.get("maxPages") or 0) > 0,
            f"材料列表应返回配置上限，实际 {limits}",
        )
        record_document_evidence(
            report,
            "document-page-limit",
            exact_document_id=exact["documentId"],
            over_document_id=over["documentId"],
            failure_code=failure.get("code"),
            failure_message=failure.get("message"),
            page_count=over.get("pageCount"),
            worker_max_pages=3,
            app_reported_max_pages=limits.get("maxPages"),
        )
        entry["detail"] = "以 DOCUMENT_MAX_PAGES=3 验证：3 页就绪、4 页 too_many_pages 且说明含 3"
    finally:
        recreate_worker({})


def scenario_document_dedup_isolation(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    document_id = state["three_page_document_id"]
    before = list_documents(token, project_id)
    before_count = len(before)
    before_chunks = int(fetch_document(token, project_id, document_id)["chunkCount"])

    status, _, body = upload_document(
        token, project_id, "three-pages.pdf", fixtures.three_page, "application/pdf"
    )
    require(status == 200, f"重复上传期望 200，实际 {status}")
    assert isinstance(body, dict)
    require(body.get("deduplicated") is True, "重复上传应标记 deduplicated=true")
    require(
        body["document"]["documentId"] == document_id,  # type: ignore[index]
        "重复上传应返回同一材料标识",
    )
    after = list_documents(token, project_id)
    require(len(after) == before_count, "重复上传不应新增材料")
    require(
        int(fetch_document(token, project_id, document_id)["chunkCount"]) == before_chunks,
        "重复上传不应新增分块",
    )

    file_path = document_path(project_id, document_id) + "/file"
    chunks_path = document_path(project_id, document_id) + "/chunks"
    for path in (document_path(project_id), file_path, chunks_path):
        anonymous_status, _, anonymous_body = raw_request("GET", path)
        require(anonymous_status == 401, f"匿名 {path} 期望 401，实际 {anonymous_status}")
        require(
            json.loads(anonymous_body) == {"detail": "未登录"},
            f"匿名 {path} 响应体不符合契约",
        )

    client_b = HttpClient()
    b_status, _, _ = client_b.request(
        "POST",
        "/api/auth/login",
        payload={"email": ACCOUNT_B, "password": state["password"]},
        headers={"Origin": BASE_URL},
    )
    require(b_status == 200, f"B 登录期望 200，实际 {b_status}")
    token_b = client_b.cookie_value()
    require(bool(token_b), "B 登录未设置会话 Cookie")

    random_id = str(uuid.uuid4())
    for suffix in ("/file", "/chunks"):
        status_a, _, body_a = api_call("GET", document_path(project_id, document_id) + suffix, token_b)
        status_random, _, body_random = api_call(
            "GET", document_path(project_id, random_id) + suffix, token_b
        )
        require(status_a == 404, f"B 访问 A 的材料{suffix} 期望 404，实际 {status_a}")
        require(status_random == 404, f"随机材料{suffix} 期望 404，实际 {status_random}")
        require(body_a == body_random, f"他人材料与不存在材料的响应体不一致：{suffix}")

    record_document_evidence(
        report,
        "document-dedup-isolation",
        document_id=document_id,
        dedup_status=status,
        material_count=len(after),
        chunk_count=before_chunks,
    )
    entry["detail"] = (
        "重复上传返回 200/同一 documentId，材料与分块数量不变；"
        "匿名 401；B 访问 A 的原件与分块为与随机标识一致的 404"
    )


def scenario_document_cancel(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    stopped = compose("stop", "worker")
    require(stopped.returncode == 0, f"停止 worker 失败：{stopped.stderr.strip()}")
    try:
        payload = f"CANCEL-ME-{fixtures.suffix}".encode("utf-8")
        status, _, body = upload_document(
            token, project_id, "cancel.txt", payload, "text/plain"
        )
        require(status == 201, f"上传期望 201，实际 {status}")
        assert isinstance(body, dict)
        queued_id = str(body["document"]["documentId"])
        job_id = str(body["jobId"])
        job = fetch_job(token, project_id, job_id)
        require(job["status"] == "queued", f"worker 停止时任务应为 queued，实际 {job['status']}")

        cancel_status, _, cancel_body = api_call(
            "POST", f"{job_path(project_id, job_id)}/cancel", token
        )
        require(cancel_status == 200, f"取消期望 200，实际 {cancel_status}")
        assert isinstance(cancel_body, dict)
        require(cancel_body.get("cancelApplied") is True, "取消应立即生效")

        cancelled = fetch_document(token, project_id, queued_id)
        require(cancelled["status"] == "failed", "取消后材料应为 failed")
        failure = cancelled.get("failure") or {}
        require(failure.get("code") == "canceled", f"取消原因不符：{failure}")
        require(int(cancelled["chunkCount"]) == 0, "取消后不应有分块")
        require(
            fetch_chunks(token, project_id, queued_id)["chunks"] == [],
            "取消后分块读取应为空",
        )

        status_again, _, body_again = upload_document(
            token, project_id, "cancel.txt", payload, "text/plain"
        )
        require(status_again == 201, f"取消后重传期望 201，实际 {status_again}")
        assert isinstance(body_again, dict)
        new_id = str(body_again["document"]["documentId"])
        require(new_id != queued_id, "重传应创建新的材料标识")
    finally:
        started = compose("start", "worker")
        require(started.returncode == 0, f"启动 worker 失败：{started.stderr.strip()}")

    ready = wait_document(token, project_id, new_id, lambda item: item["status"] == "ready")
    require(int(ready["chunkCount"]) >= 1, "重传的材料应解析出分块")

    completed_job = str(ready["jobId"])
    status_done, _, done_body = api_call(
        "POST", f"{job_path(project_id, completed_job)}/cancel", token
    )
    require(status_done == 200, f"完成后取消期望 200，实际 {status_done}")
    assert isinstance(done_body, dict)
    require(done_body["job"]["status"] == "succeeded", "完成先提交时取消应返回 succeeded")
    require(
        fetch_document(token, project_id, new_id)["status"] == "ready",
        "完成先提交时材料应保持 ready",
    )
    record_document_evidence(
        report,
        "document-cancel",
        canceled_document_id=queued_id,
        canceled_job_id=job_id,
        failure_code=failure.get("code"),
        reupload_document_id=new_id,
        completed_status=done_body["job"]["status"],
    )
    entry["detail"] = (
        "worker 停止时取消 queued 任务立即得到 canceled 事件与 failed(canceled)、0 块并成功重传；"
        "完成后再取消保持 ready/succeeded"
    )


def scenario_document_slow_parse(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    recreate_worker(
        {
            "JOB_LEASE_SECONDS": "2",
            "JOB_HEARTBEAT_SECONDS": "1",
            "DOCUMENT_INGEST_DELAY_MS": "8000",
        }
    )
    try:
        token, project_id = state["token_a"], state["project_id"]
        status, _, body = upload_document(
            token, project_id, "slow.pdf", fixtures.slow_pdf, "application/pdf"
        )
        require(status == 201, f"上传期望 201，实际 {status}")
        assert isinstance(body, dict)
        document_id = str(body["document"]["documentId"])
        job_id = str(body["jobId"])

        running = wait_job(
            token, project_id, job_id, lambda item: item["status"] == "running"
        )
        require(not running["awaitingTakeover"], "刚领取的任务不应等待接手")
        observed = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            current = fetch_job(token, project_id, job_id)
            observed.append(current["attempts"])
            require(
                current["status"] == "running",
                f"慢解析期间状态应为 running，实际 {current['status']}",
            )
            require(
                not current["awaitingTakeover"],
                "解析期间租约未被持续续期（出现 awaitingTakeover）",
            )
            require(
                int(current["attempts"]) == 1,
                f"正常慢解析不应增加 attempts，实际 {current['attempts']}",
            )
            time.sleep(0.5)

        stopped = compose("stop", "-t", "1", "worker")
        require(stopped.returncode == 0, f"停止 worker 失败：{stopped.stderr.strip()}")
        takeover = wait_job(
            token,
            project_id,
            job_id,
            lambda item: bool(item["awaitingTakeover"]),
            timeout=15,
        )
        require(
            fetch_chunks(token, project_id, document_id)["chunks"] == [],
            "接手前不应出现分块",
        )
    finally:
        restore_worker_defaults()

    ready = wait_document(token, project_id, document_id, lambda item: item["status"] == "ready")
    final_job = fetch_job(token, project_id, job_id)
    require(int(final_job["attempts"]) >= 2, "接手后 attempts 应增加")
    page_nos = [
        chunk["pageNo"]
        for chunk in fetch_chunks(token, project_id, document_id, limit=50)["chunks"]  # type: ignore[index]
    ]
    require(page_nos == [1, 3], f"接手后分块页码应仍为 [1, 3]，实际 {page_nos}")
    record_document_evidence(
        report,
        "document-slow-parse",
        document_id=document_id,
        job_id=job_id,
        attempts_during_parse=observed[:3],
        attempts_after_takeover=final_job["attempts"],
        awaiting_takeover=bool(takeover["awaitingTakeover"]),
        chunk_count=ready["chunkCount"],
        page_nos=page_nos,
    )
    entry["detail"] = (
        "慢解析期间租约持续续期（attempts 保持 1、未等待接手）；停止 worker 后进入等待接手，"
        f"恢复后 attempts={final_job['attempts']} 且分块仍为 [1, 3]"
    )



def scenario_document_failure_history(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    """上限 3 页失败后恢复 2000 页，历史材料仍显示解析时的上限。"""
    token, project_id = state["token_a"], state["project_id"]
    recreate_worker({"DOCUMENT_MAX_PAGES": "3"})
    try:
        failed = upload_and_wait(
            state,
            fixtures,
            "history-limit.pdf",
            fixtures.history_over_pdf,
            "application/pdf",
            expect_ready=False,
        )
        require(failed["status"] == "failed", "4 页文件在上限 3 时应失败")
        failure = failed.get("failure") or {}
        detail = failure.get("detail") or {}
        require(detail.get("pages") == 4, f"应记录解析时实际页数 4：{detail}")
        require(detail.get("limit") == 3, f"应记录解析时上限 3：{detail}")
    finally:
        recreate_worker({})

    detail_after = fetch_document(token, project_id, str(failed["documentId"]))
    failure_after = detail_after.get("failure") or {}
    detail_after_values = failure_after.get("detail") or {}
    require(
        detail_after_values.get("pages") == 4
        and detail_after_values.get("limit") == 3,
        f"配置恢复后详情仍应保留解析时上限 3：{failure_after}",
    )
    require(int(detail_after.get("pageCount") or 0) == 4, "失败材料应保留实际页数")

    listed = list_documents(token, project_id)
    entry_listed = next(
        item
        for item in listed
        if item["documentId"] == detail_after["documentId"]
    )
    list_failure = entry_listed.get("failure") or {}
    list_detail = list_failure.get("detail") or {}
    require(
        list_detail.get("pages") == 4 and list_detail.get("limit") == 3,
        f"列表也应保留解析时上限 3：{list_failure}",
    )

    status_limits, _, limits_body = api_call("GET", document_path(project_id), token)
    require(status_limits == 200, f"材料列表期望 200，实际 {status_limits}")
    assert isinstance(limits_body, dict)
    current_limits = limits_body.get("limits") or {}
    require(
        int(current_limits.get("maxPages") or 0) == 2000,
        f"恢复后当前配置应为 2000：{current_limits}",
    )
    require(
        "3" in str(failure_after.get("message")),
        "后端失败说明应保留解析时的上限 3",
    )

    record_document_evidence(
        report,
        "document-failure-history",
        document_id=detail_after["documentId"],
        page_count=detail_after.get("pageCount"),
        parse_limit=detail_after_values.get("limit"),
        current_limit=current_limits.get("maxPages"),
        list_limit=list_detail.get("limit"),
        message=failure_after.get("message"),
    )
    entry["detail"] = (
        "上限 3 时 4 页失败；恢复 2000 后列表与详情仍记录解析时上限 3、实际页数 4，"
        "当前配置为 2000"
    )


def scenario_engine_offline(entry: dict[str, Any], report: dict[str, Any]) -> None:
    """运行离线契约检查；不读取真实密钥，不访问外部服务。"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "app.engines.offline", "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    require(
        result.returncode == 0,
        f"离线引擎契约检查失败：{result.stderr.strip() or result.stdout.strip()[:500]}",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"离线引擎契约检查未返回 JSON：{exc}") from exc
    require(payload.get("mode") == "offline-contract", "离线检查 mode 不是 offline-contract")
    require(payload.get("result") == "passed", "离线检查结果不是 passed")
    calls = payload.get("calls") or {}
    require(calls.get("real") == 0, "离线检查出现真实调用")
    require(int(calls.get("double") or 0) > 0, "离线检查没有测试替身调用")
    report["engine_evidence"] = payload
    entry["detail"] = (
        f"离线契约 {len(payload.get('scenarios', []))} 项通过；"
        f"real={calls.get('real')} double={calls.get('double')}"
    )



# ---------------------------------------------------------------- 证据图谱验收

GRAPH_PAGE = "/api/projects/{project_id}/evidence-graph"
GRAPH_QUESTION = "验收问题：哪些事件、相关方及其关系可以回到原创材料原文？"


def record_graph_evidence(
    report: dict[str, Any], scenario_id: str, **fields: object
) -> None:
    if report.get("graph_evidence") is None:
        report["graph_evidence"] = {
            "mode": "offline-stub",
            "calls": {"real": 0, "double": 0},
            "scenarios": [],
        }
    report["graph_evidence"]["scenarios"].append({"scenario": scenario_id, **fields})


def graph_path(project_id: str, build_id: str | None = None) -> str:
    base = GRAPH_PAGE.format(project_id=project_id)
    return base if build_id is None else f"{base}?build={build_id}"


def graph_build_path(project_id: str, build_id: str | None = None) -> str:
    base = f"{GRAPH_PAGE.format(project_id=project_id)}/builds"
    return base if build_id is None else f"{base}/{build_id}"


def graph_snapshot_path(project_id: str, build_id: str, revision: int) -> str:
    return f"{graph_build_path(project_id, build_id)}/snapshots/{revision}"


def start_graph_build(
    token: str,
    project_id: str,
    document_ids: list[str],
    question: str = GRAPH_QUESTION,
) -> tuple[int, dict[str, object] | None]:
    status, _, body = api_call(
        "POST",
        graph_build_path(project_id),
        token,
        payload={"documentIds": document_ids, "question": question},
        headers={"Origin": BASE_URL},
    )
    return status, body if isinstance(body, dict) else None


def fetch_graph(
    token: str, project_id: str, build_id: str | None = None
) -> tuple[dict[str, object], dict[str, object] | None]:
    status, _, body = api_call("GET", graph_path(project_id, build_id), token)
    require(status == 200, f"建图读取期望 200，实际 {status}：{body}")
    require(isinstance(body, dict) and "build" in body, f"建图响应缺少 build：{body}")
    build = body["build"]  # type: ignore[index]
    snapshot = body.get("snapshot") if isinstance(body, dict) else None
    require(isinstance(build, dict), "建图 build 不是对象")
    return build, snapshot if isinstance(snapshot, dict) else None


def graph_sources(snapshot: dict[str, object]) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    for collection in ("nodes", "edges"):
        for item in snapshot.get(collection) or []:
            for source in item.get("sources") or []:
                sources.append(source)
    return sources


def utf16_slice(text: str, start: int, end: int) -> str:
    raw = text.encode("utf-16-le")
    return raw[start * 2 : end * 2].decode("utf-16-le", "strict")


def graph_build_count() -> int:
    result = compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        os.environ.get("POSTGRES_USER", "echosim"),
        "-d",
        os.environ.get("POSTGRES_DB", "echosim"),
        "-Atc",
        "SELECT count(*) FROM graph_builds",
    )
    require(result.returncode == 0, f"读取建图数量失败：{result.stderr.strip()}")
    return int(result.stdout.strip() or "0")


def run_graph_probe(
    mode: str, *, project_id: str, document_id: str | None = None
) -> dict[str, Any]:
    args = [
        "exec",
        "-T",
        "app",
        "python",
        "-m",
        "app.graph_probe",
        "--mode",
        mode,
        "--project-id",
        project_id,
    ]
    if document_id:
        args += ["--document-id", document_id]
    result = compose(*args)
    output = (result.stderr or result.stdout).strip()[-1000:]
    require(result.returncode == 0, f"图谱探针 {mode} 失败：{output}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    require(bool(lines), f"图谱探针 {mode} 没有 JSON 输出")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise AssertionError(f"图谱探针 {mode} 输出不是 JSON") from exc


def stop_worker() -> None:
    result = compose("stop", "worker")
    require(result.returncode == 0, f"停止 worker 失败：{result.stderr.strip()}")


def start_worker() -> None:
    result = compose("start", "worker")
    require(result.returncode == 0, f"恢复 worker 失败：{result.stderr.strip()}")


def scenario_graph_basic(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    pdf = upload_and_wait(
        state, fixtures, f"graph-source-{fixtures.suffix}.pdf",
        fixtures.three_page, "application/pdf",
    )
    text = upload_and_wait(
        state, fixtures, f"graph-source-{fixtures.suffix}.txt",
        fixtures.emoji_text, "text/plain",
    )
    extras: list[dict[str, object]] = []
    for index in range(4):
        extra = (
            f"EchoSim 原创补充材料 {index} {fixtures.suffix}。\n"
            "该段只用于六份材料上限与任务拒绝验证。"
        ).encode("utf-8")
        extras.append(
            upload_and_wait(
                state,
                fixtures,
                f"graph-extra-{index}-{fixtures.suffix}.txt",
                extra,
                "text/plain",
            )
        )

    stop_worker()
    queued_body = (
        f"EchoSim 原创排队材料 {fixtures.suffix}。\n"
        "worker 已停止，该材料应保持 queued。"
    ).encode("utf-8")
    queued_status, _, queued_payload = upload_document(
        token, project_id, f"graph-queued-{fixtures.suffix}.txt", queued_body, "text/plain"
    )
    require(queued_status == 201, f"排队材料上传期望 201，实际 {queued_status}")
    require(isinstance(queued_payload, dict), "排队材料响应不是对象")
    queued_document = queued_payload["document"]  # type: ignore[index]
    queued_id = str(queued_document["documentId"])
    require(queued_document["status"] == "queued", "停止 worker 后新材料应保持 queued")

    pdf_id = str(pdf["documentId"])
    text_id = str(text["documentId"])
    not_ready_status, not_ready_body = start_graph_build(
        token, project_id, [pdf_id, queued_id]
    )
    require(not_ready_status == 409, f"未就绪材料期望 409，实际 {not_ready_status}")
    require(
        isinstance(not_ready_body, dict)
        and not_ready_body.get("code") == "document_not_ready",
        f"未就绪材料原因码异常：{not_ready_body}",
    )

    status_one, body_one = start_graph_build(token, project_id, [pdf_id, text_id])
    require(status_one == 202, f"开始建图期望 202，实际 {status_one}：{body_one}")
    assert body_one is not None
    build_one = body_one["build"]  # type: ignore[index]
    build_id_one = str(build_one["buildId"])
    job_id_one = str(build_one["jobId"])
    run_id_one = str(build_one["runId"])

    status_two, body_two = start_graph_build(token, project_id, [pdf_id, text_id])
    require(status_two == 200, f"重复开始期望 200，实际 {status_two}：{body_two}")
    assert body_two is not None
    build_two = body_two["build"]  # type: ignore[index]
    require(
        (build_two["buildId"], build_two["jobId"], build_two["runId"])
        == (build_id_one, job_id_one, run_id_one),
        "重复开始返回了不同 build/job/run",
    )

    six_ids = [pdf_id, text_id] + [str(item["documentId"]) for item in extras]
    six_status, _ = start_graph_build(token, project_id, six_ids)
    require(six_status == 422, f"六份材料期望 422，实际 {six_status}")
    empty_status, _ = start_graph_build(token, project_id, [pdf_id], "   ")
    require(empty_status == 422, f"空白问题期望 422，实际 {empty_status}")

    anonymous_status, _, anonymous_body = raw_request("GET", graph_path(project_id))
    require(anonymous_status == 401, f"匿名建图读取期望 401，实际 {anonymous_status}")
    require(anonymous_body == '{"detail":"未登录"}', "匿名建图响应体异常")

    client_b = HttpClient()
    login_status, _, login_body = client_b.request(
        "POST",
        "/api/auth/login",
        payload={"email": ACCOUNT_B, "password": state["password"]},
        headers={"Origin": BASE_URL},
    )
    require(login_status == 200, f"B 登录失败：{login_body[:200]}")
    status_b, _, body_b = client_b.request("GET", graph_path(project_id))
    status_b_missing, _, body_b_missing = client_b.request(
        "GET", graph_path(str(uuid.uuid4()))
    )
    require(status_b == 404, f"B 读取他人建图期望 404，实际 {status_b}")
    require(status_b_missing == 404, "B 读取不存在项目期望 404")
    require(body_b == body_b_missing == '{"detail":"项目不存在"}', "他人/不存在项目响应体不同")

    state["graph_pdf_id"] = pdf_id
    state["graph_text_id"] = text_id
    state["graph_build_one"] = build_id_one
    state["graph_job_one"] = job_id_one
    state["graph_run_one"] = run_id_one
    state["graph_queued_id"] = queued_id
    record_graph_evidence(
        report,
        "graph-basic",
        build_id=build_id_one,
        job_id=job_id_one,
        run_id=run_id_one,
        duplicate_reused=True,
        not_ready_status=not_ready_status,
        six_documents_status=six_status,
        empty_question_status=empty_status,
        anonymous_status=anonymous_status,
        other_user_status=status_b,
        queued_document_id=queued_id,
    )
    entry["detail"] = (
        f"开始 202、重复 200 且身份一致；未就绪 409、六份/空问题 422、"
        f"匿名 401、他人 404（buildId={build_id_one}）"
    )


def scenario_graph_complete(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    build_id = state["graph_build_one"]
    start_worker()
    job = wait_job(
        token, project_id, state["graph_job_one"],
        lambda item: item["status"] in ("succeeded", "failed", "canceled"),
        timeout=120.0,
    )
    require(job["status"] == "succeeded", f"建图任务应成功，实际 {job['status']}")
    build, snapshot = fetch_graph(token, project_id, build_id)
    require(build["phase"] == "ready", f"建图 phase 应为 ready：{build['phase']}")
    require(build["canContinue"] is True, "完成建图 canContinue 应为 true")
    require(snapshot is not None, "完成建图缺少快照")
    assert snapshot is not None
    require(snapshot["complete"] is True, "最终快照应 complete=true")
    require(snapshot["published"] is True, "最终快照应 published=true")
    require(int(snapshot["nodeCount"]) >= 3, "材料节点应至少 3 个")
    require(int(snapshot["edgeCount"]) >= 3, "材料关系应至少 3 条")
    require(
        int(snapshot["nodeCount"]) == len(snapshot["nodes"]),
        "节点计数与数组长度不一致",
    )
    require(
        int(snapshot["edgeCount"]) == len(snapshot["edges"]),
        "关系计数与数组长度不一致",
    )

    events = fetch_job_events(token, project_id, state["graph_job_one"])
    seqs = event_seqs(events)
    require(seqs == list(range(1, len(seqs) + 1)), f"事件序号不连续：{seqs}")
    types = event_types(events)
    require(types[-1] == "completed", f"最后事件应为 completed：{types}")
    require(types.count("completed") == 1, f"completed 应只有一条：{types}")
    phases = [
        str(event["payload"].get("phase"))
        for event in events["events"]  # type: ignore[index]
        if event["payload"].get("phase")
    ]
    for required_phase in ("waiting_index", "drawing", "ready"):
        require(required_phase in phases, f"阶段序列缺少 {required_phase}：{phases}")

    partial_revisions: list[int] = []
    for event in events["events"]:  # type: ignore[index]
        payload = event["payload"]
        if payload.get("partial") is True and payload.get("snapshotRevision"):
            revision = int(payload["snapshotRevision"])
            if revision not in partial_revisions:
                partial_revisions.append(revision)
    require(bool(partial_revisions), "未观察到任何 complete=false 部分快照")
    partial_snapshots = []
    for revision in partial_revisions:
        status, _, body = api_call(
            "GET", graph_snapshot_path(project_id, build_id, revision), token
        )
        require(status == 200, f"部分快照 {revision} 读取失败：{status}")
        partial = body["snapshot"]  # type: ignore[index]
        require(partial["complete"] is False, f"部分快照 {revision} 不应 complete")
        require(partial["published"] is True, f"部分快照 {revision} 应已发布")
        partial_snapshots.append(partial)
    first_partial = partial_snapshots[0]

    sources = graph_sources(snapshot)
    pdf_page3 = next(
        (
            source
            for source in sources
            if source.get("kind") == "pdf"
            and int(source.get("pageNo") or 0) == 3
            and str((source.get("locator") or {}).get("url", "")).endswith("#page=3")
        ),
        None,
    )
    require(pdf_page3 is not None, "缺少 PDF 第 3 页来源与 #page=3 定位")
    text_source = next(
        (
            source
            for source in sources
            if source.get("kind") == "text"
            and int(source.get("sourceEnd") or 0) > int(source.get("sourceStart") or 0)
        ),
        None,
    )
    require(text_source is not None, "缺少文本来源 UTF-16 偏移")
    source_url = str((text_source.get("locator") or {}).get("url", ""))
    source_status, _, source_bytes = fetch_document_bytes(token, source_url)
    require(source_status == 200, f"文本原文读取失败：{source_status}")
    source_text = source_bytes.decode("utf-8")
    sliced = utf16_slice(
        source_text,
        int(text_source["sourceStart"]),
        int(text_source["sourceEnd"]),
    )
    visible = lambda value: "".join(str(value).split())
    require(
        visible(sliced) == visible(text_source["quote"]),
        "文本来源 UTF-16 偏移切片与引文不一致",
    )

    material_nodes = [
        node for node in snapshot["nodes"] if node.get("origin") == "material"
    ]
    material_edges = [
        edge for edge in snapshot["edges"] if edge.get("origin") == "material"
    ]
    require(len(material_nodes) >= 3, "材料节点不足 3 个")
    require(len(material_edges) >= 3, "材料关系不足 3 条")
    sample_nodes = [
        {"key": node["key"], "pageNo": (node.get("sources") or [{}])[0].get("pageNo")}
        for node in material_nodes[:3]
    ]
    sample_edges = [
        {
            "key": edge["key"],
            "pageNo": (edge.get("sources") or [{}])[0].get("pageNo"),
        }
        for edge in material_edges[:3]
    ]

    provider_calls = build.get("providerCalls") or {}
    require(int(provider_calls.get("real", -1)) == 0, f"首次建图出现真实调用：{provider_calls}")
    require(int(provider_calls.get("double", 0)) > 0, f"首次建图缺少替身调用计数：{provider_calls}")
    state["graph_provider_calls"] = [dict(provider_calls)]
    graph_evidence = report.get("graph_evidence")
    if not isinstance(graph_evidence, dict):
        graph_evidence = {
            "mode": "offline-stub",
            "calls": {"real": 0, "double": 0},
            "scenarios": [],
        }
        report["graph_evidence"] = graph_evidence
    record_graph_evidence(
        report,
        "graph-complete",
        build_id=build_id,
        job_id=state["graph_job_one"],
        run_id=state["graph_run_one"],
        phase_sequence=phases,
        event_seqs=seqs,
        partial_revisions=partial_revisions,
        first_partial_node_count=int(first_partial["nodeCount"]),
        first_partial_edge_count=int(first_partial["edgeCount"]),
        final_revision=int(snapshot["revision"]),
        final_node_count=int(snapshot["nodeCount"]),
        final_edge_count=int(snapshot["edgeCount"]),
        pdf_page_no=3,
        text_utf16=[
            int(text_source["sourceStart"]),
            int(text_source["sourceEnd"]),
        ],
        sample_nodes=sample_nodes,
        sample_edges=sample_edges,
        provider_calls=provider_calls,
    )
    state["graph_final_snapshot"] = snapshot
    entry["detail"] = (
        f"阶段含 waiting_index/drawing/ready；部分快照 {len(partial_revisions)} 份，"
        f"最终 {snapshot['nodeCount']} 节点 / {snapshot['edgeCount']} 关系；"
        "PDF page3 与文本 UTF-16 来源校验通过"
    )


def scenario_graph_bind_probe(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    stop_worker()
    result = run_graph_probe(
        "bind-windows",
        project_id=state["project_id"],
        document_id=state["graph_pdf_id"],
    )
    cases = result.get("cases") or []
    require(len(cases) == 3, f"绑定窗口应覆盖 3 种：{result}")
    for case in cases:
        require(case["sameIdempotencyKey"] is True, f"幂等键变化：{case}")
        require(case["singleGraphId"] is True, f"出现多个 graph_id：{case}")
        require(int(case["bindingRows"]) == 1, f"绑定行数不是 1：{case}")
        require(int(case["boundRows"]) == 1, f"绑定未完成：{case}")
    takeover = next(case for case in cases if case["case"] == "takeover_pending_binding")
    require(int(takeover["takeoverAttempts"]) >= 2, f"未发生接手：{takeover}")
    record_graph_evidence(report, "graph-bind-windows", cases=cases)
    entry["detail"] = (
        "响应丢失、响应到达未提交、接手 pending 绑定均复用同一幂等键且只绑定一个 graph_id"
    )


def scenario_graph_failure_probe(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    result = run_graph_probe(
        "failure-codes",
        project_id=state["project_id"],
        document_id=state["graph_pdf_id"],
    )
    codes = {case["case"]: case["code"] for case in result.get("cases") or []}
    require(codes.get("index_timeout") == "index_timeout", f"index_timeout 分类错误：{codes}")
    require(codes.get("write_unconfirmed") == "write_unconfirmed", f"write_unconfirmed 分类错误：{codes}")
    require(codes.get("processing_timeout") == "processing_timeout", f"processing_timeout 分类错误：{codes}")
    require(
        codes.get("graph_incomplete") in ("graph_incomplete", "processing_timeout"),
        f"graph_incomplete 分类错误：{codes}",
    )
    limits = {case["case"]: case for case in result.get("limits") or []}
    require(limits.get("graph_too_large_nodes", {}).get("code") == "graph_too_large", f"节点上限分类错误：{limits}")
    require(limits.get("graph_too_large_edges", {}).get("code") == "graph_too_large", f"边上限分类错误：{limits}")
    require(limits.get("too_many_sources", {}).get("code") == "too_many_sources", f"来源上限分类错误：{limits}")
    no_snapshot = limits.get("no_snapshot_published") or {}
    require(int(no_snapshot.get("before", -1)) == int(no_snapshot.get("after", -2)), "超限路径发布了快照")
    record_graph_evidence(report, "graph-failure-codes", cases=codes, limits=limits)
    entry["detail"] = "失败原因码稳定，超限路径未发布任何快照"


def scenario_graph_publish_probe(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    result = run_graph_probe(
        "publish-race",
        project_id=state["project_id"],
        document_id=state["graph_pdf_id"],
    )
    publish = result.get("publish_before_cancel") or {}
    cancel = result.get("cancel_before_publish") or {}
    require(publish.get("outcome") == "succeeded", f"发布步骤失败：{publish}")
    require(publish.get("cancelApplied") is False, f"发布后取消不应生效：{publish}")
    require(publish.get("phase") == "ready", f"发布后 phase 应为 ready：{publish}")
    require(publish.get("finalComplete") is True, f"最终快照未完成：{publish}")
    require(int(publish.get("completedEvents", 0)) == 1, f"completed 事件数异常：{publish}")
    require(cancel.get("outcome") == "canceled", f"取消先于发布应终止：{cancel}")
    require(cancel.get("cancelApplied") is True, f"取消应先提交：{cancel}")
    require(cancel.get("candidateVisible") is False, f"候选快照不应可见：{cancel}")
    require(int(cancel.get("completedEvents", 0)) == 0, f"取消路径不应有 completed：{cancel}")
    record_graph_evidence(
        report,
        "graph-publish-race",
        publish_before_cancel=publish,
        cancel_before_publish=cancel,
    )
    entry["detail"] = (
        "发布先于取消时只有一个完成快照与 completed 事件；取消先于发布时候选不可见且无完成事件"
    )


def scenario_graph_second_build(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    first_build = state["graph_build_one"]
    status, body = start_graph_build(
        token, project_id, [state["graph_pdf_id"], state["graph_text_id"]]
    )
    require(status == 202, f"第二次建图期望 202，实际 {status}：{body}")
    assert body is not None
    second = body["build"]  # type: ignore[index]
    second_build = str(second["buildId"])
    require(second_build != first_build, "第二次建图应产生新 buildId")
    default_build, _ = fetch_graph(token, project_id)
    require(
        default_build["buildId"] == second_build,
        f"活动 build 应优先，实际 {default_build['buildId']}",
    )
    first_build_body, first_snapshot = fetch_graph(token, project_id, first_build)
    require(first_build_body["buildId"] == first_build, "按旧 buildId 读取失败")
    require(first_snapshot is not None and first_snapshot["complete"] is True, "旧 build 完成图不可读")
    start_worker()
    final_job = wait_job(
        token,
        project_id,
        str(second["jobId"]),
        lambda item: item["status"] in ("succeeded", "failed", "canceled"),
        timeout=120.0,
    )
    require(final_job["status"] == "succeeded", f"第二次建图未成功：{final_job['status']}")
    second_build_body, second_snapshot = fetch_graph(token, project_id, second_build)
    require(second_build_body["phase"] == "ready", "第二次建图未完成")
    require(second_snapshot is not None and second_snapshot["complete"] is True, "第二次最终快照不可读")
    second_calls = second_build_body.get("providerCalls") or {}
    require(int(second_calls.get("real", -1)) == 0, f"第二次建图出现真实调用：{second_calls}")
    require(int(second_calls.get("double", 0)) > 0, f"第二次建图缺少替身调用计数：{second_calls}")
    calls = state.get("graph_provider_calls") or []
    calls.append(dict(second_calls))
    measured_real = sum(int(item.get("real", 0)) for item in calls)
    measured_double = sum(int(item.get("double", 0)) for item in calls)
    graph_evidence = report.get("graph_evidence")
    assert isinstance(graph_evidence, dict)
    graph_evidence["calls"] = {
        "real": measured_real,
        "double": measured_double,
    }
    graph_evidence["callScope"] = {
        "buildIds": [first_build, second_build],
        "builds": len(calls),
        "source": "provider-boundary",
        "perBuild": calls,
    }
    record_graph_evidence(
        report,
        "graph-second-build",
        first_build_id=first_build,
        second_build_id=second_build,
        default_selected_second=True,
        first_build_readable=True,
        second_final_revision=int(second_snapshot["revision"]),
        provider_calls=second_calls,
    )
    state["graph_build_two"] = second_build
    entry["detail"] = (
        f"第二次 buildId={second_build} 等待时默认返回第二次；按第一次 buildId 仍可读取完成图"
    )


def scenario_graph_redaction(
    entry: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    logs_result = compose("logs", "--no-color", "app", "worker")
    logs = (logs_result.stdout or "") + (logs_result.stderr or "")
    serialized = json.dumps(report, ensure_ascii=False)
    tokens = [REDACTION_SECRET, fixtures.three_page_marker]
    require(REDACTION_SECRET != "verify-secret-unset", "未注入唯一测试密钥")
    for token in tokens:
        require(token not in logs, "隔离日志包含测试密钥或材料私有标记")
        require(token not in serialized, "验收报告包含测试密钥或材料私有标记")
    report["graph_evidence"]["redaction"] = {"passed": True, "tokens": len(tokens)}
    entry["detail"] = "日志与内存报告均不含唯一测试密钥或原创材料标记"


def scenario_graph_not_triggered(
    entry: dict[str, Any], report: dict[str, Any]
) -> None:
    count = graph_build_count()
    require(count == 0, f"默认验收不应创建建图任务，实际 {count}")
    report["graph_evidence"] = {
        "mode": "not-run",
        "calls": {"real": 0, "double": 0},
        "builds": count,
        "scenarios": [],
    }
    entry["detail"] = "默认 make verify 未创建任何建图任务，真实与替身调用均为 0"


def scenario_graph_worker_restored(
    entry: dict[str, Any], report: dict[str, Any]
) -> None:
    result = compose("config", "--format", "json")
    require(result.returncode == 0, f"docker compose config 失败：{result.stderr.strip()}")
    config = json.loads(result.stdout)
    env = (config.get("services") or {}).get("worker", {}).get("environment") or {}
    model = str(env.get("EVIDENCE_GRAPH_MODEL_PROVIDER", ""))
    graph = str(env.get("EVIDENCE_GRAPH_GRAPH_PROVIDER", ""))
    allow = str(env.get("EVIDENCE_GRAPH_ALLOW_STUB", "")).lower()
    require(model != "stub", f"worker 模型 provider 未恢复：{model}")
    require(graph != "stub", f"worker 图谱 provider 未恢复：{graph}")
    require(allow in ("false", "0", ""), f"worker stub 开关未恢复：{allow}")
    evidence = report.get("graph_evidence")
    if isinstance(evidence, dict):
        evidence["workerRestored"] = {
            "modelProvider": model,
            "graphProvider": graph,
            "stubAllowed": allow not in ("false", "0", ""),
        }
    entry["detail"] = (
        "Change 07 运行结束后 worker 已恢复为非 stub provider，stub 开关为 false"
    )


def scenario_environment_worker_restored(
    entry: dict[str, Any], report: dict[str, Any]
) -> None:
    result = compose("config", "--format", "json")
    require(result.returncode == 0, f"docker compose config 失败：{result.stderr.strip()}")
    config = json.loads(result.stdout)
    env = (config.get("services") or {}).get("worker", {}).get("environment") or {}
    provider = str(env.get("ENVIRONMENT_MODEL_PROVIDER", ""))
    allow = str(env.get("ENVIRONMENT_ALLOW_STUB", "")).lower()
    delay = str(env.get("ENVIRONMENT_PROFILE_DELAY_MS", "0"))
    require(provider != "stub", f"画像 provider 未恢复：{provider}")
    require(allow in ("false", "0", ""), f"画像 stub 开关未恢复：{allow}")
    require(delay in ("0", ""), f"画像验收延迟未恢复：{delay}")
    report["environment_evidence"]["workerRestored"] = {
        "modelProvider": provider,
        "stubAllowed": allow not in ("false", "0", ""),
        "profileDelayMs": int(delay or 0),
    }
    entry["detail"] = "Change 08 结束后 worker 已恢复为非 stub 画像配置，验收延迟为 0"


ENVIRONMENT_TIMEOUT_SECONDS = 180.0
ENVIRONMENT_TEXT_HEADER = "EchoSim 原创角色材料 🌱"
ENVIRONMENT_ROLE_NAMES = {"张同学", "某校学生群体", "某大学", "某报社", "校友李某"}


def record_environment_evidence(
    report: dict[str, Any], scenario_id: str, **fields: object
) -> None:
    if report.get("environment_evidence") is None:
        report["environment_evidence"] = {
            "mode": "offline-stub",
            "calls": {"real": 0, "double": 0},
            "callScope": [],
            "scenarios": [],
            "workerRestored": None,
        }
    report["environment_evidence"]["scenarios"].append(
        {"scenario": scenario_id, **fields}
    )


def note_environment_calls(
    report: dict[str, Any], *, preparation: dict[str, object], build_id: str
) -> None:
    if report.get("environment_evidence") is None:
        report["environment_evidence"] = {
            "mode": "offline-stub",
            "calls": {"real": 0, "double": 0},
            "callScope": [],
            "scenarios": [],
            "workerRestored": None,
        }
    evidence = report["environment_evidence"]
    counts = preparation.get("counts") or {}
    calls = counts.get("providerCalls") or {}
    real = int(calls.get("real") or 0)
    double = int(calls.get("double") or 0)
    scope = evidence["callScope"]
    existing = next(
        (
            item
            for item in scope
            if item.get("preparationId") == preparation.get("preparationId")
        ),
        None,
    )
    if existing is None:
        scope.append(
            {
                "preparationId": preparation.get("preparationId"),
                "buildId": build_id,
                "real": real,
                "double": double,
            }
        )
    else:
        existing["real"] = max(int(existing.get("real") or 0), real)
        existing["double"] = max(int(existing.get("double") or 0), double)
    evidence["calls"] = {
        "real": sum(int(item.get("real") or 0) for item in scope),
        "double": sum(int(item.get("double") or 0) for item in scope),
    }


def psql_scalar(sql: str) -> str:
    result = compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        os.environ.get("POSTGRES_USER", "echosim"),
        "-d",
        os.environ.get("POSTGRES_DB", "echosim"),
        "-Atc",
        sql,
    )
    require(result.returncode == 0, f"psql 查询失败：{result.stderr.strip()[-400:]}")
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""


def psql_exec(sql: str) -> None:
    result = compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        os.environ.get("POSTGRES_USER", "echosim"),
        "-d",
        os.environ.get("POSTGRES_DB", "echosim"),
        "-Atc",
        sql,
    )
    require(result.returncode == 0, f"psql 写入失败：{result.stderr.strip()[-400:]}")


def environment_path(project_id: str, build_id: str | None = None) -> str:
    base = f"/api/projects/{project_id}/environment"
    return base if build_id is None else f"{base}?build={build_id}"


def preparation_path(
    project_id: str, preparation_id: str, suffix: str = ""
) -> str:
    return (
        f"/api/projects/{project_id}/environment/preparations/"
        f"{preparation_id}{suffix}"
    )


def fetch_environment_context(
    token: str, project_id: str, build_id: str | None = None
) -> dict[str, object]:
    status, _, body = api_call(
        "GET", environment_path(project_id, build_id), token
    )
    require(status == 200, f"准备上下文期望 200，实际 {status}：{body}")
    require(isinstance(body, dict), f"准备上下文不是对象：{body}")
    return body


def start_environment_preparation(
    token: str, project_id: str, build_id: str
) -> tuple[int, dict[str, object]]:
    status, _, body = api_call(
        "POST",
        f"/api/projects/{project_id}/environment/preparations?build={build_id}",
        token,
        payload={},
        headers={"Origin": BASE_URL},
    )
    require(isinstance(body, dict), f"开始准备响应不是对象：{body}")
    return status, body


def retry_environment_preparation(
    token: str, project_id: str, preparation_id: str
) -> tuple[int, dict[str, object]]:
    status, _, body = api_call(
        "POST",
        preparation_path(project_id, preparation_id, "/retry"),
        token,
        payload={},
        headers={"Origin": BASE_URL},
    )
    require(isinstance(body, dict), f"重试准备响应不是对象：{body}")
    return status, body


def fetch_preparation(
    token: str, project_id: str, preparation_id: str
) -> dict[str, object]:
    status, _, body = api_call(
        "GET", preparation_path(project_id, preparation_id), token
    )
    require(status == 200, f"准备详情期望 200，实际 {status}：{body}")
    require(
        isinstance(body, dict) and isinstance(body.get("preparation"), dict),
        f"准备详情缺少 preparation：{body}",
    )
    return body["preparation"]  # type: ignore[index]


def wait_preparation(
    token: str,
    project_id: str,
    preparation_id: str,
    predicate: Callable[[dict[str, object]], bool],
    *,
    timeout: float = ENVIRONMENT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    def check() -> dict[str, object] | None:
        try:
            detail = fetch_preparation(token, project_id, preparation_id)
        except AssertionError:
            return None
        return detail if predicate(detail) else None

    result = wait_for(check, timeout=timeout, interval=0.2)
    require(result is not None, "准备未在限定时间内进入期望状态")
    return result  # type: ignore[return-value]


def fetch_graph_ready(
    token: str, project_id: str, build_id: str, job_id: str
) -> tuple[dict[str, object], dict[str, object]]:
    job = wait_job(
        token,
        project_id,
        job_id,
        lambda item: item["status"] in ("succeeded", "failed", "canceled"),
        timeout=120.0,
    )
    require(job["status"] == "succeeded", f"建图任务未成功：{job['status']}")
    build, snapshot = fetch_graph(token, project_id, build_id)
    require(build["phase"] == "ready", f"建图 phase 不是 ready：{build['phase']}")
    require(build["canContinue"] is True, "建图 canContinue 不是 true")
    require(snapshot is not None, "建图缺少已发布快照")
    assert snapshot is not None
    require(snapshot["complete"] is True, "最终快照不是 complete")
    return build, snapshot


def create_environment_graph(
    state: dict[str, Any],
    fixtures: DocumentFixtures,
    *,
    filename: str,
    data: bytes,
    content_type: str,
    question: str,
) -> tuple[dict[str, object], dict[str, object]]:
    token, project_id = state["token_a"], state["project_id"]
    document = upload_and_wait(state, fixtures, filename, data, content_type)
    status, body = start_graph_build(
        token, project_id, [str(document["documentId"])], question
    )
    require(status in (200, 202), f"开始建图期望 200/202，实际 {status}：{body}")
    assert body is not None
    build = body["build"]  # type: ignore[index]
    graph, snapshot = fetch_graph_ready(
        token, project_id, str(build["buildId"]), str(build["jobId"])
    )
    return graph, snapshot


def fetch_text_source(token: str, project_id: str, document_id: str) -> str:
    status, _, body = raw_request(
        "GET",
        f"/api/projects/{project_id}/documents/{document_id}/source",
        cookie_token=token,
    )
    require(status == 200, f"读取文本原文期望 200，实际 {status}")
    return body


def environment_role_names(detail: dict[str, object]) -> set[str]:
    return {
        str(role.get("displayName"))
        for role in (detail.get("roles") or [])  # type: ignore[union-attr]
    }


def environment_role(
    detail: dict[str, object], name: str
) -> dict[str, object]:
    for role in detail.get("roles") or []:  # type: ignore[union-attr]
        if role.get("displayName") == name:
            return role
    raise AssertionError(f"准备详情缺少角色：{name}")


def environment_sources(detail: dict[str, object]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for role in (detail.get("roles") or []) + (detail.get("previewRoles") or []):  # type: ignore[operator]
        for field in ("mention", "background"):
            value = role.get(field) or {}
            items.extend(value.get("sources") or [])  # type: ignore[union-attr]
        for field in ("expressedTopics", "involvedTopics"):
            for topic in role.get(field) or []:
                items.extend(topic.get("sources") or [])
    return items


def scenario_environment_not_triggered(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    status, _, body = api_call("GET", environment_path(project_id), token)
    require(status == 404, f"无图谱时的准备上下文应为 404，实际 {status}")
    require(
        isinstance(body, dict) and body.get("detail") == "图谱不存在",
        f"无图谱原因不可区分：{body}",
    )
    anonymous_status, _, anonymous_body = raw_request(
        "GET", environment_path(project_id)
    )
    require(anonymous_status == 401, "匿名准备上下文应为 401")
    report["environment_evidence"] = {
        "mode": "not-triggered",
        "calls": {"real": 0, "double": 0},
        "callScope": [],
        "scenarios": [
            {
                "scenario": "environment-not-triggered",
                "status": status,
                "reason": "graph_missing",
                "anonymousStatus": anonymous_status,
            }
        ],
        "workerRestored": None,
    }
    entry["detail"] = (
        f"无 build 返回 404 图谱不存在；匿名 401；未运行准备 worker（real=0）"
    )


def scenario_environment_full_chain(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    suffix = fixtures.suffix
    text = (
        f"{ENVIRONMENT_TEXT_HEADER}-{suffix}\n"
        "实体：张同学｜个人｜张同学是参与讨论的学生。\n"
        "实体：某校学生群体｜群体｜某校学生群体关注接驳班次。\n"
        "实体：某大学｜高校｜某大学位于东门附近。\n"
        "实体：某报社｜媒体机构｜某报社关注校园交通。\n"
        "实体：某争议事件｜事件｜某争议事件引发讨论。\n"
        "实体：某传闻｜待核实说法｜某传闻称班次将取消。\n"
        "实体：某社交平台｜平台｜某社交平台出现相关讨论。\n"
        "实体：调整接驳班次｜话题｜调整接驳班次是讨论焦点。\n"
        "实体：某地｜地点｜某地是事件发生地。\n"
        "实体：校友李某｜其他主体｜校友李某参与了讨论。\n"
        "关系：某报社｜报道｜某大学｜某报社报道某大学。\n"
        "关系：某争议事件｜发生于｜某地｜某争议事件发生于某地。\n"
        "关系：张同学｜主张｜调整接驳班次｜张同学主张调整接驳班次。\n"
        "关系：某校学生群体｜支持｜调整接驳班次｜某校学生群体支持调整接驳班次。\n"
        "关系：某社交平台｜传播｜某传闻｜某社交平台传播某传闻。\n"
        "关系：校友李某｜关注｜调整接驳班次｜校友李某关注调整接驳班次。\n"
    ).encode("utf-8")
    question = f"原创问题-{suffix}：雨天是否调整校园接驳班次？"
    graph, snapshot = create_environment_graph(
        state,
        fixtures,
        filename=f"environment-rich-{suffix}.txt",
        data=text,
        content_type="text/plain",
        question=question,
    )
    build_id = str(graph["buildId"])
    snapshot_id = str(snapshot["snapshotId"])
    document_id = str(
        (snapshot.get("nodes") or [{}])[0].get("sources", [{}])[0].get(  # type: ignore[union-attr]
            "documentId", ""
        )
    )
    require(bool(document_id), "图谱快照缺少材料标识")

    # 模拟旧快照边：把“关注”关系的来源性质置空，验证保留未知。
    edge_key = psql_scalar(
        "SELECT e.edge_key FROM graph_edges e "
        "JOIN graph_nodes src ON src.snapshot_id = e.snapshot_id "
        "AND src.node_key = e.source_node_key "
        "JOIN graph_nodes tgt ON tgt.snapshot_id = e.snapshot_id "
        "AND tgt.node_key = e.target_node_key "
        f"WHERE e.snapshot_id = '{snapshot_id}' "
        "AND src.label = '校友李某' AND tgt.label = '调整接驳班次' LIMIT 1"
    )
    require(bool(edge_key), "未找到用于旧来源性质的边")
    psql_exec(
        "UPDATE graph_edges SET claim_nature = NULL WHERE snapshot_id = "
        f"'{snapshot_id}' AND edge_key = '{edge_key}'"
    )

    context = fetch_environment_context(token, project_id, build_id)
    require(context["canPrepare"] is True, f"准备上下文不可开始：{context}")
    require(context["snapshot"]["snapshotId"] == snapshot_id, "上下文快照不一致")
    require(context["build"]["question"] == question, "上下文问题不一致")

    status_one, body_one = start_environment_preparation(token, project_id, build_id)
    require(status_one == 202, f"开始准备期望 202，实际 {status_one}：{body_one}")
    preparation_one = body_one["preparation"]  # type: ignore[index]
    preparation_id = str(preparation_one["preparationId"])
    job_id = str(preparation_one["jobId"])
    run_id = str(preparation_one["runId"])
    status_two, body_two = start_environment_preparation(token, project_id, build_id)
    require(status_two == 200, f"重复开始期望 200，实际 {status_two}：{body_two}")
    preparation_two = body_two["preparation"]  # type: ignore[index]
    require(
        (
            preparation_two["preparationId"],
            preparation_two["jobId"],
            preparation_two["runId"],
        )
        == (preparation_id, job_id, run_id),
        "重复开始返回了不同准备身份",
    )

    detail = wait_preparation(
        token, project_id, preparation_id, lambda item: item["phase"] == "ready"
    )
    require(detail["phase"] == "ready", "准备未进入 ready")
    require(detail["settings"]["rounds"] == 72, "默认轮数不是 72")
    require(detail["canContinue"] is False, "未确认时 canContinue 应为 false")
    names = environment_role_names(detail)
    require(
        ENVIRONMENT_ROLE_NAMES.issubset(names),
        f"角色筛选缺少主体：{sorted(ENVIRONMENT_ROLE_NAMES - names)}",
    )
    require("某争议事件" not in names, "有出边的事件不应成为角色")
    require("某传闻" not in names, "待核实说法不应成为角色")
    require("某社交平台" not in names, "平台渠道不应成为角色")
    role_node_keys = {str(role["nodeKey"]) for role in detail["roles"]}  # type: ignore[index]
    graph_nodes = {str(node["key"]): node for node in snapshot.get("nodes") or []}  # type: ignore[union-attr]
    excluded = {
        label: graph_nodes[key]["nodeType"]
        for key, label in (
            (node_key, graph_nodes[node_key]["label"])
            for node_key in graph_nodes
            if node_key not in role_node_keys
        )
        if label in {"某争议事件", "某传闻", "某社交平台"}
    }
    require(len(excluded) == 3, f"三个反例未全部排除：{excluded}")

    student = environment_role(detail, "张同学")
    university = environment_role(detail, "某大学")
    alumnus = environment_role(detail, "校友李某")
    require(student["expressedTopics"], "张同学缺少表达话题")
    require(
        student["expressedTopics"][0]["text"] == "调整接驳班次",
        "张同学的表达话题不正确",
    )
    require(university["involvedTopics"], "某大学缺少材料涉及的话题")
    require(
        any(
            item.get("claimNature") is None and item.get("claimNatureKnown") is False
            for item in alumnus["involvedTopics"] + alumnus["expressedTopics"]
        ),
        "旧边空来源性质未保留未知",
    )
    require(
        university["background"]["stated"] is True,
        "某大学应有关系支持背景",
    )
    profile_topics = [
        topic
        for topic in detail["initialTopics"]
        if str(topic.get("text", "")).startswith("模拟延伸话题：")
    ]
    require(profile_topics, "批次 initialTopics 未进入最终配置")
    require(
        all(
            topic["origin"] == "simulation"
            and topic["claimNature"] is None
            and topic["claimNatureKnown"] is False
            for topic in profile_topics
        ),
        "批次 initialTopics 的来源性质未保留",
    )

    source_text = fetch_text_source(token, project_id, document_id)
    text_sources = [
        item for item in environment_sources(detail) if item.get("kind") != "pdf"
    ]
    require(bool(text_sources), "角色缺少文本来源")
    utf16_ok = 0
    visible = lambda value: "".join(str(value).split())
    for source in text_sources:
        start = int(source["sourceStart"])
        end = int(source["sourceEnd"])
        quoted = utf16_slice(source_text, start, end)
        if visible(quoted) == visible(source["quote"]):
            utf16_ok += 1
    require(utf16_ok == len(text_sources), "文本来源 UTF-16 切片与引文不一致")

    events = fetch_job_events(token, project_id, job_id, after=0)
    seqs = event_seqs(events)
    require(seqs == list(range(1, len(seqs) + 1)), f"准备事件序号不连续：{seqs}")
    require(event_types(events)[-1] == "completed", "准备终态事件不是 completed")
    segment = fetch_job_events(token, project_id, job_id, after=2)
    require(
        event_seqs(segment)[0] == 3 if event_seqs(segment) else False,
        "after 游标未从第 3 条继续",
    )
    status_priority, _, priority_body = api_call(
        "GET",
        f"{job_path(project_id, job_id)}/events?format=json&after=1",
        token,
        headers={"Last-Event-ID": f"{run_id}:2"},
    )
    require(status_priority == 200, f"Last-Event-ID 补发失败：{status_priority}")
    require(
        isinstance(priority_body, dict)
        and event_seqs(priority_body)[0] == 3,
        "Last-Event-ID 未优先于 after",
    )

    note_environment_calls(report, preparation=detail, build_id=build_id)
    record_environment_evidence(
        report,
        "environment-full-chain",
        preparationId=preparation_id,
        jobId=job_id,
        runId=run_id,
        buildId=build_id,
        snapshotId=snapshot_id,
        question=question,
        startStatus=status_one,
        duplicateStatus=status_two,
        rounds=detail["settings"]["rounds"],
        roles=sorted(names),
        excluded=excluded,
        roleNodeKeys=sorted(role_node_keys),
        textSourceChecks=utf16_ok,
        textSourceTotal=len(text_sources),
        expressedTopic=student["expressedTopics"][0]["text"],
        involvedTopicCount=len(university["involvedTopics"]),
        legacyNullClaimNature=True,
        eventSeqs=seqs,
        lastEventIdPriority=True,
        providerCalls=detail["counts"]["providerCalls"],
        profileTopicCount=len(profile_topics),
        canContinueBeforeConfirm=detail["canContinue"],
    )
    state["environment_build"] = build_id
    state["environment_snapshot"] = snapshot_id
    state["environment_preparation"] = preparation_id
    state["environment_job"] = job_id
    state["environment_run"] = run_id
    state["environment_question"] = question
    entry["detail"] = (
        f"ready；角色 {sorted(names)}；排除事件/说法/平台；"
        f"文本 UTF-16 来源 {utf16_ok}/{len(text_sources)}；事件 1–{len(seqs)}"
    )


def scenario_environment_rounds_and_freeze(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    preparation_id = state["environment_preparation"]
    build_id = state["environment_build"]
    snapshot_id = state["environment_snapshot"]

    initial = fetch_preparation(token, project_id, preparation_id)
    require(initial["settings"]["rounds"] == 72, "初始轮数不是 72")
    require(initial["settings"]["settingsRevision"] == 1, "初始版本不是 1")
    require(initial["settings"]["autoRounds"] == 72, "初始自动轮数不是 72")

    def save(rounds: int, revision: int) -> dict[str, object]:
        status, _, body = api_call(
            "PATCH",
            preparation_path(project_id, preparation_id, "/configuration"),
            token,
            payload={
                "settingsRevision": revision,
                "durationHours": 72,
                "minutesPerRound": 60,
                "roundsMode": "manual",
                "manualRounds": rounds,
            },
            headers={"Origin": BASE_URL},
        )
        require(status == 200, f"保存 {rounds} 轮期望 200，实际 {status}：{body}")
        require(isinstance(body, dict), "保存轮数响应不是对象")
        return body["preparation"]  # type: ignore[index]

    saved_40 = save(40, 1)
    require(saved_40["settings"]["rounds"] == 40, "手动 40 轮未生效")
    require(saved_40["settings"]["settingsRevision"] == 2, "保存后版本未递增")
    reread = fetch_preparation(token, project_id, preparation_id)
    require(reread["settings"]["rounds"] == 40, "刷新后未读回 40 轮")

    saved_3 = save(3, 2)
    require(saved_3["settings"]["rounds"] == 3, "手动 3 轮未生效")
    require(saved_3["settings"]["settingsRevision"] == 3, "B 页保存后版本不是 3")

    conflict_status, _, conflict_body = api_call(
        "POST",
        preparation_path(project_id, preparation_id, "/confirm"),
        token,
        payload={"settingsRevision": 2},
        headers={"Origin": BASE_URL},
    )
    require(
        conflict_status == 409, f"过期版本确认期望 409，实际 {conflict_status}"
    )
    require(
        isinstance(conflict_body, dict)
        and conflict_body.get("code") == "settings_conflict",
        f"过期版本原因码异常：{conflict_body}",
    )
    frozen_count = int(
        psql_scalar(
            "SELECT count(*) FROM environment_frozen_selections WHERE "
            f"preparation_id = '{preparation_id}'"
        )
        or 0
    )
    require(frozen_count == 0, f"冲突确认后冻结记录应为 0，实际 {frozen_count}")

    refreshed = fetch_preparation(token, project_id, preparation_id)
    require(refreshed["settings"]["rounds"] == 3, "冲突后未显示 3 轮")
    require(
        refreshed["settings"]["settingsRevision"] == 3, "冲突后版本未回到 3"
    )

    confirm_status, _, confirm_body = api_call(
        "POST",
        preparation_path(project_id, preparation_id, "/confirm"),
        token,
        payload={"settingsRevision": 3},
        headers={"Origin": BASE_URL},
    )
    require(confirm_status == 200, f"确认期望 200，实际 {confirm_status}：{confirm_body}")
    assert isinstance(confirm_body, dict)
    frozen = confirm_body["frozenSelection"]  # type: ignore[index]
    confirmation = confirm_body["preparation"]  # type: ignore[index]
    require(confirm_body["preparation"]["canContinue"] is True, "确认后 canContinue 应为 true")
    require(frozen["settings"]["rounds"] == 3, "冻结轮数不是 3")
    require(frozen["buildId"] == build_id, "冻结 buildId 不一致")
    require(frozen["snapshotId"] == snapshot_id, "冻结 snapshotId 不一致")
    require(frozen["question"] == state["environment_question"], "冻结问题不一致")
    require(
        frozen["platforms"]
        == [
            {"platformKey": "platform_a", "nativePlatform": "twitter"},
            {"platformKey": "platform_b", "nativePlatform": "reddit"},
        ],
        "冻结平台映射不正确",
    )
    frozen_text = json.dumps(frozen, ensure_ascii=False)
    require("create_post" not in frozen_text, "冻结选择不应包含 create_post")
    require("create_comment" not in frozen_text, "冻结选择不应包含 create_comment")
    require(
        any(topic["origin"] == "question" for topic in frozen["initialTopics"]),
        "冻结初始话题缺少推演问题",
    )
    require(
        any(
            topic["origin"] == "material" and topic["sources"]
            for topic in frozen["initialTopics"]
        ),
        "冻结材料话题缺少来源",
    )
    first = frozen["activation"]["firstSpeakerRoleIds"]
    require(bool(first), "冻结选择缺少首发角色")
    require(
        frozen["activation"]["order"][: len(first)] == first,
        "首发角色不是激活顺序前缀",
    )
    mapped = {
        item["roleId"]: item["topicId"]
        for item in frozen["activation"]["firstSpeakerTopics"]
    }
    require(set(mapped) == set(first), "首发角色与话题未一一对应")
    require(
        [role["roleId"] for role in frozen["roles"]]
        == frozen["activation"]["order"],
        "冻结角色顺序与激活顺序不一致",
    )

    repeat_status, _, repeat_body = api_call(
        "POST",
        preparation_path(project_id, preparation_id, "/confirm"),
        token,
        payload={"settingsRevision": 3},
        headers={"Origin": BASE_URL},
    )
    require(repeat_status == 200, f"重复确认期望 200，实际 {repeat_status}")
    assert isinstance(repeat_body, dict)
    require(
        repeat_body["frozenSelection"]["selectionId"] == frozen["selectionId"],
        "重复确认返回了不同 selectionId",
    )
    frozen_count_after = int(
        psql_scalar(
            "SELECT count(*) FROM environment_frozen_selections WHERE "
            f"preparation_id = '{preparation_id}'"
        )
        or 0
    )
    require(frozen_count_after == 1, "重复确认新增了冻结记录")

    frozen_save_status, _, frozen_save_body = api_call(
        "PATCH",
        preparation_path(project_id, preparation_id, "/configuration"),
        token,
        payload={
            "settingsRevision": 3,
            "durationHours": 72,
            "minutesPerRound": 60,
            "roundsMode": "manual",
            "manualRounds": 40,
        },
        headers={"Origin": BASE_URL},
    )
    require(
        frozen_save_status == 409,
        f"冻结后保存期望 409，实际 {frozen_save_status}",
    )
    require(
        isinstance(frozen_save_body, dict)
        and frozen_save_body.get("code") == "preparation_frozen",
        f"冻结后原因码异常：{frozen_save_body}",
    )

    record_environment_evidence(
        report,
        "environment-rounds-and-freeze",
        preparationId=preparation_id,
        initialRounds=72,
        rounds40=saved_40["settings"]["rounds"],
        revisionAfter40=saved_40["settings"]["settingsRevision"],
        rereadRounds=reread["settings"]["rounds"],
        rounds3=saved_3["settings"]["rounds"],
        revisionAfter3=saved_3["settings"]["settingsRevision"],
        conflictCode=conflict_body.get("code"),  # type: ignore[union-attr]
        frozenCountBefore=frozen_count,
        frozenCountAfter=frozen_count_after,
        frozenRounds=frozen["settings"]["rounds"],
        selectionId=frozen["selectionId"],
        repeatedSelectionSame=True,
        firstSpeakerRoleCount=len(first),
        topicOrigins=sorted({topic["origin"] for topic in frozen["initialTopics"]}),
        canContinue=confirmation["canContinue"],
    )
    state["environment_frozen"] = frozen
    entry["detail"] = (
        f"72→40（revision 2）→3（revision 3）；A 用 revision 2 确认收到 "
        f"settings_conflict 且冻结 0；重读后确认冻结 3 轮，selectionId 幂等"
    )


def scenario_environment_invalid_config(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    preparation_id = state["environment_preparation"]
    revision = fetch_preparation(token, project_id, preparation_id)["settings"][
        "settingsRevision"
    ]

    def attempt(payload: dict[str, object], expected: str) -> int:
        status, _, body = api_call(
            "PATCH",
            preparation_path(project_id, preparation_id, "/configuration"),
            token,
            payload=payload,
            headers={"Origin": BASE_URL},
        )
        require(status == 422, f"{expected} 期望 422，实际 {status}：{body}")
        require(
            isinstance(body, dict) and body.get("code") == expected,
            f"{expected} 原因码异常：{body}",
        )
        return status

    zero = attempt(
        {
            "settingsRevision": revision,
            "durationHours": 0,
            "minutesPerRound": 60,
            "roundsMode": "auto",
        },
        "duration_invalid",
    )
    invalid_rounds = attempt(
        {
            "settingsRevision": revision,
            "durationHours": 72,
            "minutesPerRound": 60,
            "roundsMode": "manual",
            "manualRounds": 0,
        },
        "rounds_invalid",
    )
    not_integral = attempt(
        {
            "settingsRevision": revision,
            "durationHours": 2,
            "minutesPerRound": 90,
            "roundsMode": "auto",
        },
        "rounds_not_integral",
    )
    unchanged = fetch_preparation(token, project_id, preparation_id)
    require(
        unchanged["settings"]["rounds"] == 72,
        "非法配置改写了当前生效轮数",
    )
    record_environment_evidence(
        report,
        "environment-invalid-config",
        durationStatus=zero,
        roundsStatus=invalid_rounds,
        integralStatus=not_integral,
        roundsAfter=unchanged["settings"]["rounds"],
    )
    entry["detail"] = "零时长/非法轮数/不能整除分别 422，生效轮数保持 72"


def scenario_environment_batches_and_takeover(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    suffix = fixtures.suffix
    pdf = build_pdf_bytes(
        [
            [f"PAGE-ONE-{suffix}"],
            [],
            [
                f"PAGE-THREE-{suffix}",
                "Entity: ReporterLee|Person|ReporterLee reported the campus issue.",
                "Entity: CampusOffice|University|CampusOffice published a notice.",
                "Entity: ParentGroup|Group|ParentGroup supports the plan.",
                "Entity: CampusPlan|Topic|CampusPlan is the focus.",
                "Relation: ReporterLee|reports|CampusOffice|ReporterLee reports CampusOffice.",
                "Relation: ParentGroup|supports|CampusPlan|ParentGroup supports the CampusPlan.",
                "Relation: CampusOffice|supports|CampusPlan|CampusOffice supports the CampusPlan.",
            ],
        ]
    )
    question = f"原创问题-{suffix}：是否调整课程安排？"
    graph, snapshot = create_environment_graph(
        state,
        fixtures,
        filename=f"environment-pages-{suffix}.pdf",
        data=pdf,
        content_type="application/pdf",
        question=question,
    )
    build_id = str(graph["buildId"])
    recreate_worker(
        {
            "ENVIRONMENT_MODEL_PROVIDER": "stub",
            "ENVIRONMENT_ALLOW_STUB": "true",
            "ENVIRONMENT_PROFILE_BATCH_SIZE": "2",
            "ENVIRONMENT_PROFILE_DELAY_MS": "1500",
        }
    )
    status, body = start_environment_preparation(token, project_id, build_id)
    require(status == 202, f"开始准备期望 202，实际 {status}：{body}")
    preparation = body["preparation"]  # type: ignore[index]
    preparation_id = str(preparation["preparationId"])
    job_id = str(preparation["jobId"])
    run_id = str(preparation["runId"])

    def first_batch_ready() -> bool:
        value = psql_scalar(
            "SELECT preview_role_count FROM environment_preparations WHERE "
            f"id = '{preparation_id}'"
        )
        return bool(value) and int(value) >= 2

    require(
        wait_for(first_batch_ready, timeout=60.0, interval=0.05),
        "第一批画像未在限定时间内完成",
    )
    compose("stop", "worker")
    preview = fetch_preparation(token, project_id, preparation_id)
    require(preview["phase"] == "preparing", "停止 worker 后准备应保持 preparing")
    require(
        len(preview["previewRoles"]) >= 2,
        "第一批预览角色数量不足",
    )
    require(preview["previewOnly"] is True, "预览未标记 previewOnly")
    require(preview["canContinue"] is False, "预览阶段 canContinue 应为 false")
    require(preview["platforms"] == [], "预览阶段不应出现平台映射")
    require(preview["initialTopics"] == [], "预览阶段不应出现初始话题")
    require(preview["activation"] is None, "预览阶段不应出现激活顺序")
    require(
        preview["roles"] == [], "预览阶段不应返回最终角色"
    )
    first_ids = {str(role["roleId"]) for role in preview["previewRoles"]}

    def awaiting_takeover() -> bool:
        value = psql_scalar(
            "SELECT (status = 'running' AND lease_expires_at <= clock_timestamp()) "
            f"FROM jobs WHERE id = '{job_id}'"
        )
        return value == "t"

    require(
        wait_for(awaiting_takeover, timeout=30.0, interval=0.25),
        "worker 停止后任务未进入等待接手",
    )
    start_worker()
    ready = wait_preparation(
        token, project_id, preparation_id, lambda item: item["phase"] == "ready"
    )
    job = fetch_job(token, project_id, job_id)
    require(int(job["attempts"]) > 1, "接手后 attempts 未增加")
    require(str(job["jobId"]) == job_id, "接手改变了 jobId")
    require(str(job["runId"]) == run_id, "接手改变了 runId")
    require(
        env_identity(ready, "preparationId") == preparation_id,
        "接手改变了 preparationId",
    )
    final_ids = {str(role["roleId"]) for role in ready["roles"]}  # type: ignore[index]
    require(first_ids.issubset(final_ids), "接手后第一批角色被重复生成或丢失")
    require(len(ready["roles"]) == 3, f"PDF 角色数量不是 3：{len(ready['roles'])}")
    page_sources = [
        source
        for source in environment_sources(ready)
        if source.get("kind") == "pdf" and int(source.get("pageNo") or 0) == 3
    ]
    require(bool(page_sources), "PDF 角色来源没有落在第 3 页")
    require(
        any("#page=3" in str(source["locator"]["url"]) for source in page_sources),
        "PDF 来源链接不是 #page=3",
    )
    note_environment_calls(report, preparation=ready, build_id=build_id)
    record_environment_evidence(
        report,
        "environment-batches-and-takeover",
        preparationId=preparation_id,
        jobId=job_id,
        runId=run_id,
        buildId=build_id,
        snapshotId=str(snapshot["snapshotId"]),
        firstBatchRoleIds=sorted(first_ids),
        firstBatchCount=len(first_ids),
        previewPhase=preview["phase"],
        previewPlatforms=preview["platforms"],
        attempts=job["attempts"],
        samePreparation=True,
        firstBatchReused=True,
        pdfPageSources=len(page_sources),
        pdfPageNo=3,
        pdfLocator="#page=3",
        providerCalls=ready["counts"]["providerCalls"],
    )
    state["environment_pdf_build"] = build_id
    state["environment_pdf_preparation"] = preparation_id
    entry["detail"] = (
        f"第一批 {len(first_ids)} 个角色可预览且无平台/话题/顺序；停止 worker 后 "
        f"awaitingTakeover，接手 attempts={job['attempts']}，PDF 来源 pageNo=3"
    )


def env_identity(detail: dict[str, object], key: str) -> str:
    return str(detail.get(key) or "")


def scenario_environment_no_roles_and_unavailable(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    suffix = fixtures.suffix
    no_role_text = (
        "实体：某争议事件｜事件｜某争议事件引发讨论。\n"
        "实体：某传闻｜待核实说法｜某传闻称安排将取消。\n"
        "实体：某社交平台｜平台｜某社交平台出现相关讨论。\n"
        "实体：某地｜地点｜某地是事件发生地。\n"
        "关系：某争议事件｜发生于｜某地｜某争议事件发生于某地。\n"
        "关系：某社交平台｜传播｜某传闻｜某社交平台传播某传闻。\n"
    ).encode("utf-8")
    graph, snapshot = create_environment_graph(
        state,
        fixtures,
        filename=f"environment-no-roles-{suffix}.txt",
        data=no_role_text,
        content_type="text/plain",
        question=f"原创问题-{suffix}：只有事件和说法吗？",
    )
    build_id = str(graph["buildId"])
    before = psql_scalar(
        "SELECT count(*) FROM environment_preparations WHERE build_id = "
        f"'{build_id}'"
    )
    no_role_status, no_role_body = start_environment_preparation(
        token, project_id, build_id
    )
    require(
        no_role_status == 422,
        f"无角色开始期望 422，实际 {no_role_status}：{no_role_body}",
    )
    require(
        isinstance(no_role_body, dict)
        and no_role_body.get("code") == "no_role_candidates",
        f"无角色原因码异常：{no_role_body}",
    )
    after = psql_scalar(
        "SELECT count(*) FROM environment_preparations WHERE build_id = "
        f"'{build_id}'"
    )
    require(before == after == "0", "无角色请求创建了准备记录")

    available_text = (
        "实体：赵同学｜个人｜赵同学参与讨论。\n"
        "实体：某课程安排｜话题｜某课程安排是讨论焦点。\n"
        "关系：赵同学｜主张｜某课程安排｜赵同学主张调整课程安排。\n"
    ).encode("utf-8")
    graph, snapshot = create_environment_graph(
        state,
        fixtures,
        filename=f"environment-unavailable-{suffix}.txt",
        data=available_text,
        content_type="text/plain",
        question=f"原创问题-{suffix}：画像不可用时如何处理？",
    )
    build_id = str(graph["buildId"])
    recreate_worker(
        {
            "ENVIRONMENT_MODEL_PROVIDER": "unconfigured",
            "ENVIRONMENT_ALLOW_STUB": "false",
        }
    )
    status, body = start_environment_preparation(token, project_id, build_id)
    require(status == 202, f"开始准备期望 202，实际 {status}：{body}")
    preparation = body["preparation"]  # type: ignore[index]
    preparation_id = str(preparation["preparationId"])
    failed = wait_preparation(
        token, project_id, preparation_id, lambda item: item["phase"] == "failed"
    )
    require(
        failed["failure"]["code"] == "model_unavailable",
        f"模型不可用原因码异常：{failed['failure']}",
    )
    require(failed["canContinue"] is False, "失败准备不应可继续")
    published = int(
        psql_scalar(
            "SELECT count(*) FROM environment_roles WHERE preparation_id = "
            f"'{preparation_id}' AND published_at IS NOT NULL"
        )
        or 0
    )
    frozen = int(
        psql_scalar(
            "SELECT count(*) FROM environment_frozen_selections WHERE "
            f"preparation_id = '{preparation_id}'"
        )
        or 0
    )
    require(published == 0 and frozen == 0, "失败准备发布了最终角色或冻结选择")

    retry_required, retry_required_body = start_environment_preparation(
        token, project_id, build_id
    )
    require(
        retry_required == 409,
        f"失败后普通开始期望 409，实际 {retry_required}",
    )
    require(
        isinstance(retry_required_body, dict)
        and retry_required_body.get("code") == "preparation_retry_required",
        f"普通开始原因码异常：{retry_required_body}",
    )

    recreate_worker(environment_runtime_env())
    retry_status, retry_body = retry_environment_preparation(
        token, project_id, preparation_id
    )
    require(retry_status == 202, f"显式重试期望 202，实际 {retry_status}：{retry_body}")
    retried = retry_body["preparation"]  # type: ignore[index]
    require(
        retried["preparationId"] != preparation_id,
        "显式重试未创建新的准备",
    )
    retried_ready = wait_preparation(
        token,
        project_id,
        str(retried["preparationId"]),
        lambda item: item["phase"] == "ready",
    )
    note_environment_calls(report, preparation=retried_ready, build_id=build_id)
    record_environment_evidence(
        report,
        "environment-no-roles-and-model-unavailable",
        noRoleStatus=no_role_status,
        noRoleCode=no_role_body.get("code"),  # type: ignore[union-attr]
        noRoleRecordsBefore=before,
        noRoleRecordsAfter=after,
        unavailablePreparationId=preparation_id,
        unavailableCode=failed["failure"]["code"],
        publishedRoles=published,
        frozenSelections=frozen,
        normalStartAfterFailure=retry_required,
        retryStatus=retry_status,
        retriedPreparationId=retried["preparationId"],
        retriedPhase=retried_ready["phase"],
    )
    entry["detail"] = (
        "无角色 422 且不建记录；模型不可用 failed/model_unavailable；普通开始 409，"
        "显式重试 202 并最终 ready"
    )


def scenario_environment_access_boundaries(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    preparation_id = state["environment_preparation"]
    build_id = state["environment_build"]

    anonymous = [
        raw_request("GET", environment_path(project_id)),
        raw_request("POST", f"/api/projects/{project_id}/environment/preparations"),
        raw_request("GET", preparation_path(project_id, preparation_id)),
    ]
    require(
        [item[0] for item in anonymous] == [401, 401, 401],
        f"匿名准备接口未全部 401：{[item[0] for item in anonymous]}",
    )

    client_b = HttpClient()
    status_login, _, body_login = client_b.request(
        "POST",
        "/api/auth/login",
        payload={"email": ACCOUNT_B, "password": state["password"]},
        headers={"Origin": BASE_URL},
    )
    require(status_login == 200, f"B 登录失败：{body_login[:200]}")
    other_project, _, other_body = client_b.request(
        "GET", environment_path(project_id)
    )
    missing_project, _, missing_body = client_b.request(
        "GET", environment_path(str(uuid.uuid4()))
    )
    require(other_project == 404 and missing_project == 404, "他人/不存在项目不是 404")
    require(other_body == missing_body == '{"detail":"项目不存在"}', "他人项目响应体不同")

    missing_build_status, _, missing_build_body = api_call(
        "GET", environment_path(project_id, str(uuid.uuid4())), token
    )
    require(missing_build_status == 404, "不存在 build 期望 404")
    require(
        isinstance(missing_build_body, dict)
        and missing_build_body.get("detail") == "图谱不存在",
        f"不存在 build 响应异常：{missing_build_body}",
    )
    missing_prep_status, _, missing_prep_body = api_call(
        "GET", preparation_path(project_id, str(uuid.uuid4())), token
    )
    require(missing_prep_status == 404, "不存在准备期望 404")
    require(
        isinstance(missing_prep_body, dict)
        and missing_prep_body.get("detail") == "准备不存在",
        f"不存在准备响应异常：{missing_prep_body}",
    )

    compose("stop", "db")
    outage = wait_for(
        lambda: http_status("/health")[0] == 503,
        timeout=30.0,
        interval=0.25,
    )
    require(outage, "停止数据库后 /health 未返回 503")
    outage_status, outage_headers, outage_body = state["client_a"].request(
        "GET", preparation_path(project_id, preparation_id)
    )
    require(outage_status == 503, f"数据库不可达时准备详情应 503，实际 {outage_status}")
    require(
        SESSION_COOKIE not in outage_headers.get("set-cookie", ""),
        "503 响应清除了会话 Cookie",
    )
    compose("start", "db")
    require(
        wait_for(lambda: http_status("/health")[0] == 200, timeout=60.0, interval=0.5),
        "数据库恢复后 /health 未返回 200",
    )
    recovered = fetch_preparation(token, project_id, preparation_id)
    require(recovered["phase"] == "ready", "数据库恢复后准备详情不可用")
    record_environment_evidence(
        report,
        "environment-access-boundaries",
        anonymousStatuses=[item[0] for item in anonymous],
        otherProjectStatus=other_project,
        missingBuildStatus=missing_build_status,
        missingPreparationStatus=missing_prep_status,
        outageStatus=outage_status,
        cookiePreserved=True,
        recoveredPhase=recovered["phase"],
    )
    entry["detail"] = (
        "匿名 401；他人/不存在项目 404；不存在 build/准备分别为图谱不存在/准备不存在；"
        "停库 503 且 Cookie 保留，恢复后可读"
    )


def scenario_environment_change_material(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    suffix = fixtures.suffix
    text = (
        "实体：李老师｜个人｜李老师是参与讨论的教师。\n"
        "实体：家长委员会｜群体｜家长委员会关注校车安排。\n"
        "实体：调整校车路线｜话题｜调整校车路线是讨论焦点。\n"
        "关系：李老师｜主张｜调整校车路线｜李老师主张调整校车路线。\n"
        "关系：家长委员会｜支持｜调整校车路线｜家长委员会支持调整校车路线。\n"
    ).encode("utf-8")
    graph, snapshot = create_environment_graph(
        state,
        fixtures,
        filename=f"environment-second-{suffix}.txt",
        data=text,
        content_type="text/plain",
        question=f"原创问题-{suffix}：是否调整校车路线？",
    )
    build_id = str(graph["buildId"])
    status, body = start_environment_preparation(token, project_id, build_id)
    require(status == 202, f"新 build 开始准备期望 202，实际 {status}：{body}")
    preparation = body["preparation"]  # type: ignore[index]
    preparation_id = str(preparation["preparationId"])
    ready = wait_preparation(
        token, project_id, preparation_id, lambda item: item["phase"] == "ready"
    )
    names = environment_role_names(ready)
    require(
        {"李老师", "家长委员会"}.issubset(names),
        f"换材料后角色未变化：{names}",
    )
    require(
        "张同学" not in names and "某报社" not in names,
        "新准备复用了旧材料角色",
    )
    old = fetch_preparation(
        token, project_id, state["environment_preparation"]
    )
    require(
        old["snapshotId"] == state["environment_snapshot"],
        "旧准备快照被新 build 覆盖",
    )
    old_frozen = old["frozenSelection"]
    require(
        old_frozen is not None
        and old_frozen["snapshotId"] == state["environment_snapshot"],
        "旧冻结选择被新 build 改写",
    )
    note_environment_calls(report, preparation=ready, build_id=build_id)
    record_environment_evidence(
        report,
        "environment-change-material",
        firstBuildId=state["environment_build"],
        firstSnapshotId=state["environment_snapshot"],
        firstRoles=sorted(ENVIRONMENT_ROLE_NAMES),
        secondBuildId=build_id,
        secondSnapshotId=str(snapshot["snapshotId"]),
        secondRoles=sorted(names),
        oldPreparationSnapshot=old["snapshotId"],
        oldFrozenSnapshot=old_frozen["snapshotId"],
    )
    entry["detail"] = (
        f"新 build 角色 {sorted(names)}；旧准备与冻结选择仍绑定旧 snapshotId"
    )



# ---------------------------------------------------------------- 双平台运行


SIMULATION_QUESTION = "原创问题：雨天是否调整双平台的接驳班次？"
SIMULATION_MATERIAL_TEXT = "\n".join(
    [
        "EchoSim 原创材料：校园接驳车在雨天把发车间隔调整为十分钟。",
        "实体：张同学｜个人｜张同学每天乘坐接驳车。",
        "实体：某校学生群体｜群体｜某校学生群体关注班次。",
        "实体：某大学｜高校｜某大学位于东门附近。",
        "实体：某报社｜媒体机构｜某报社报道接驳车调整。",
        "实体：校友李某｜个人｜校友李某提出建议。",
        "实体：后勤处｜机构组织｜后勤处负责班车调度。",
        "关系：张同学｜关注｜调整接驳班次｜张同学关注调整接驳班次。",
        "关系：某报社｜报道｜某大学｜某报社报道某大学接驳车调整。",
        "关系：校友李某｜建议｜调整接驳班次｜校友李某建议调整接驳班次。",
    ]
)
ENGINE_COUNTS_SCRIPT = (
    Path(__file__).resolve().parent / "engine_counts.py"
).read_text(encoding="utf-8")


def simulation_path(project_id: str, run_id: str | None = None, suffix: str = "") -> str:
    base = f"/api/projects/{project_id}/simulations"
    if run_id is None:
        return base
    return f"{base}/{run_id}{suffix}"


def start_simulation_run(
    token: str,
    project_id: str,
    selection_id: str,
    engine_mode: str | None = None,
) -> tuple[int, dict[str, object] | None]:
    payload: dict[str, object] = {"selectionId": selection_id}
    if engine_mode:
        payload["engineMode"] = engine_mode
    status, _, body = api_call(
        "POST",
        simulation_path(project_id),
        token,
        payload=payload,
        headers={"Origin": BASE_URL},
    )
    return status, body if isinstance(body, dict) else None


def fetch_simulation(token: str, project_id: str, run_id: str) -> dict[str, object]:
    status, _, body = api_call(
        "GET", simulation_path(project_id, run_id), token
    )
    require(status == 200, f"运行详情期望 200，实际 {status}：{body}")
    require(isinstance(body, dict) and isinstance(body.get("simulation"), dict), "运行详情格式异常")
    return body["simulation"]  # type: ignore[index]


def wait_simulation(
    token: str, project_id: str, run_id: str, *, timeout: float = 240.0
) -> dict[str, object]:
    deadline = time.time() + timeout
    last: dict[str, object] = {}
    while time.time() < deadline:
        last = fetch_simulation(token, project_id, run_id)
        if last.get("terminal"):
            return last
        time.sleep(0.4)
    raise ScenarioFailed(f"运行未在 {timeout} 秒内进入终态：{last.get('phase')}")


def fetch_simulation_actions(
    token: str, project_id: str, run_id: str
) -> dict[str, object]:
    status, _, body = api_call(
        "GET", simulation_path(project_id, run_id, "/actions?after=0&limit=200"), token
    )
    require(status == 200, f"运行动作期望 200，实际 {status}：{body}")
    require(isinstance(body, dict), "运行动作不是对象")
    return body


def engine_native_counts(simulation_id: str) -> dict[str, dict[str, int]]:
    result = compose(
        "exec",
        "-T",
        "worker",
        "python",
        "-",
        simulation_id,
        input_text=ENGINE_COUNTS_SCRIPT,
    )
    require(result.returncode == 0, f"读取引擎计数失败：{result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise ScenarioFailed(f"引擎计数输出无法解析：{result.stdout[-200:]}")
    require(isinstance(payload, dict), "引擎计数不是对象")
    return payload  # type: ignore[return-value]


def require_worker_healthy() -> None:
    for _ in range(60):
        check = compose("ps", "--format", "{{.Health}}", "worker")
        if check.stdout.strip() == "healthy":
            return
        time.sleep(0.5)
    raise ScenarioFailed("worker 未恢复到健康状态")


def recreate_simulation_worker(**env: str) -> None:
    """用指定环境强制重建 worker；Change 09 的故障注入需要新容器。"""
    for key, value in env.items():
        os.environ[key] = value
    result = compose("up", "-d", "--no-deps", "--force-recreate", "worker")
    require(result.returncode == 0, f"重建 worker 失败：{result.stderr.strip()}")
    require_worker_healthy()


def scale_worker(count: int, **env: str) -> None:
    for key, value in env.items():
        os.environ[key] = value
    # 先移除全部 worker，避免上一次失败留下的副本继续使用旧环境。
    compose("rm", "-sf", "worker")
    result = compose(
        "up", "-d", "--no-deps", "--scale", f"worker={count}", "worker"
    )
    require(result.returncode == 0, f"调整 worker 数量失败：{result.stderr.strip()}")
    deadline = time.time() + 60
    while time.time() < deadline:
        ids = compose("ps", "-q", "worker").stdout.strip().splitlines()
        if len(ids) == count:
            return
        time.sleep(0.5)
    raise ScenarioFailed(f"worker 数量未达到 {count}")


def record_simulation_evidence(
    report: dict[str, Any], scenario_id: str, **fields: object
) -> None:
    if report.get("simulation_evidence") is None:
        report["simulation_evidence"] = {
            "mode": "offline-double",
            "engine": "double",
            "calls": {"real": 0, "double": 0},
            "callScope": [],
            "scenarios": [],
            "workerRestored": None,
        }
    report["simulation_evidence"]["scenarios"].append(
        {"scenario": scenario_id, **fields}
    )


def record_simulation_calls(report: dict[str, Any], *, run_id: str) -> None:
    """从该 runId 持久化的引擎调用计数生成 callScope 与汇总。"""
    evidence = report.setdefault(
        "simulation_evidence",
        {
            "mode": "offline-double",
            "engine": "double",
            "calls": {"real": 0, "double": 0},
            "callScope": [],
            "scenarios": [],
            "workerRestored": None,
        },
    )
    raw = psql_scalar(
        "SELECT json_build_object('real', provider_calls_real, "
        "'double', provider_calls_double, 'engine', engine_mode) "
        f"FROM simulations WHERE run_id = '{run_id}'"
    )
    require(bool(raw), f"运行 {run_id} 缺少持久化的引擎调用计数")
    try:
        counts = json.loads(raw)
    except json.JSONDecodeError:
        raise ScenarioFailed(f"运行 {run_id} 的调用计数无法解析：{raw}")
    scope = [item for item in evidence["callScope"] if item["runId"] != run_id]
    scope.append(
        {
            "runId": run_id,
            "engine": counts["engine"],
            "real": int(counts["real"]),
            "double": int(counts["double"]),
        }
    )
    evidence["callScope"] = scope
    evidence["calls"] = {
        "real": sum(item["real"] for item in scope),
        "double": sum(item["double"] for item in scope),
    }


def scenario_simulation_not_triggered(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    status, _, body = api_call("GET", simulation_path(project_id), token)
    require(status == 200, f"运行列表期望 200，实际 {status}：{body}")
    require(isinstance(body, dict), "运行列表不是对象")
    require(body.get("simulations") == [], f"默认验收不应有运行记录：{body}")

    missing = str(uuid.uuid4())
    rejected, rejected_body = start_simulation_run(token, project_id, missing)
    require(rejected == 404, f"缺少冻结选择期望 404，实际 {rejected}：{rejected_body}")
    anonymous, _, _ = raw_request("GET", simulation_path(project_id))
    require(anonymous == 401, "匿名运行列表应为 401")
    rows = int(
        psql_scalar(
            f"SELECT count(*) FROM simulations WHERE project_id = '{project_id}'"
        )
        or 0
    )
    require(rows == 0, f"未触发时不应创建运行记录，实际 {rows}")
    report["simulation_evidence"] = {
        "mode": "not-triggered",
        "engine": "double",
        "calls": {"real": 0, "double": 0},
        "callScope": [],
        "scenarios": [
            {
                "scenario": "simulation-not-triggered",
                "listStatus": status,
                "startStatus": rejected,
                "anonymousStatus": anonymous,
                "records": rows,
            }
        ],
        "workerRestored": None,
    }
    entry["detail"] = (
        f"未触发运行：列表 200、无选择 404、匿名 401、运行记录 0（real=0）"
    )


def prepare_frozen_selection(
    entry: dict[str, Any],
    state: dict[str, Any],
    fixtures: DocumentFixtures,
) -> dict[str, object]:
    token, project_id = state["token_a"], state["project_id"]
    document = upload_and_wait(
        state,
        fixtures,
        f"simulation-source-{fixtures.suffix}.txt",
        SIMULATION_MATERIAL_TEXT.encode("utf-8"),
        "text/plain",
    )
    status, build_body = start_graph_build(
        token, project_id, [str(document["documentId"])], SIMULATION_QUESTION
    )
    require(status == 202, f"建图期望 202，实际 {status}：{build_body}")
    build_id = str(build_body["build"]["buildId"])  # type: ignore[index]

    deadline = time.time() + 180
    snapshot: dict[str, object] | None = None
    while time.time() < deadline:
        build, snapshot = fetch_graph(token, project_id, build_id)
        if build.get("canContinue"):
            break
        if build.get("phase") in ("failed", "canceled"):
            raise ScenarioFailed(f"建图失败：{build.get('phase')}")
        time.sleep(0.4)
    require(snapshot is not None, "建图未产生已发布快照")

    status, prep_body = start_environment_preparation(token, project_id, build_id)
    require(status == 202, f"准备期望 202，实际 {status}：{prep_body}")
    preparation_id = str(prep_body["preparation"]["preparationId"])  # type: ignore[index]
    deadline = time.time() + 180
    preparation: dict[str, object] = {}
    while time.time() < deadline:
        preparation = fetch_preparation(token, project_id, preparation_id)
        if preparation.get("phase") == "ready":
            break
        if preparation.get("phase") in ("failed", "canceled"):
            raise ScenarioFailed(f"准备失败：{preparation.get('phase')}")
        time.sleep(0.4)
    require(preparation.get("phase") == "ready", "准备未就绪")
    role_count = len(preparation.get("roles") or [])  # type: ignore[arg-type]
    require(role_count == 6, f"原创材料应准备出 6 个角色，实际 {role_count}")

    revision = int(preparation["settings"]["settingsRevision"])  # type: ignore[index]
    saved_status, _, saved = api_call(
        "PATCH",
        preparation_path(project_id, preparation_id, "/configuration"),
        token,
        payload={
            "settingsRevision": revision,
            "durationHours": 3,
            "minutesPerRound": 60,
            "roundsMode": "manual",
            "manualRounds": 3,
        },
        headers={"Origin": BASE_URL},
    )
    require(saved_status == 200, f"保存 3 轮期望 200，实际 {saved_status}：{saved}")
    revision = int(saved["preparation"]["settings"]["settingsRevision"])  # type: ignore[index]
    confirm_status, _, confirmed = api_call(
        "POST",
        preparation_path(project_id, preparation_id, "/confirm"),
        token,
        payload={"settingsRevision": revision},
        headers={"Origin": BASE_URL},
    )
    require(confirm_status == 200, f"确认期望 200，实际 {confirm_status}：{confirmed}")
    frozen = confirmed["frozenSelection"]  # type: ignore[index]
    require(frozen["settings"]["rounds"] == 3, "冻结轮数不是 3")
    require(len(frozen["roles"]) == role_count, "冻结角色数与准备不一致")
    return {
        "selectionId": frozen["selectionId"],
        "preparationId": preparation_id,
        "buildId": build_id,
        "snapshotId": frozen["snapshotId"],
        "roles": role_count,
        "question": SIMULATION_QUESTION,
        "documentId": document["documentId"],
    }


def scenario_simulation_full_chain(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    # 保留运行工作目录，才能逐个平台核对引擎记录与业务计数。
    recreate_simulation_worker(
        SIMULATION_KEEP_RUNTIME="true",
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
    )
    frozen = prepare_frozen_selection(entry, state, fixtures)
    state["simulation_selection"] = frozen["selectionId"]

    status, body = start_simulation_run(
        token, project_id, str(frozen["selectionId"])
    )
    require(status == 202, f"开始运行期望 202，实际 {status}：{body}")
    run_id = str(body["simulation"]["runId"])  # type: ignore[index]
    require(body["simulation"]["engine"] == "double", "默认引擎应为 double")
    state["simulation_run"] = run_id
    detail = wait_simulation(token, project_id, run_id)
    require(detail["phase"] == "succeeded", f"运行未成功：{detail['phase']}")
    require(detail["engine"] == "double", "运行引擎不是 double")
    require(detail["terminal"] is True, "运行未进入终态")
    require(detail["partial"] is False, "成功运行不应是部分结果")

    platforms = {item["platformKey"]: item for item in detail["platforms"]}  # type: ignore[index]
    require(set(platforms) == {"platform_a", "platform_b"}, "平台集合异常")
    require(
        platforms["platform_a"]["nativePlatform"] == "twitter"
        and platforms["platform_b"]["nativePlatform"] == "reddit",
        "原生平台映射异常",
    )
    for platform_key, allowed in (
        ("platform_a", {"create_post", "repost", "like_post"}),
        ("platform_b", {"create_post", "create_comment", "like_post", "dislike_post"}),
    ):
        platform = platforms[platform_key]
        require(platform["roundsCompleted"] == 3, f"{platform_key} 轮次不是 3")
        require(platform["actionCount"] > 0, f"{platform_key} 没有动作")
        require(
            set(platform["byAction"]).issubset(allowed),
            f"{platform_key} 出现非原生动作：{platform['byAction']}",
        )

    actions = fetch_simulation_actions(token, project_id, run_id)
    items = actions["items"]  # type: ignore[index]
    require(
        len(items) == detail["counts"]["total"],
        "动作分页总数与运行计数不一致",
    )
    records = [(item["platformKey"], item["engineRecordId"]) for item in items]
    require(len(records) == len(set(records)), "同一平台内引擎记录标识重复")
    require(
        any(item["targetResolved"] and item["target"] for item in items),
        "缺少可定位的目标原帖",
    )
    comment_targets = [
        item
        for item in items
        if item["nativeAction"] in ("create_comment", "repost")
    ]
    require(comment_targets, "没有评论或转发动作")
    for item in comment_targets:
        require(item["targetPlatformKey"] == item["platformKey"], "目标跨平台")
        require(
            item["targetEngineRecordId"] and item["targetActionId"],
            f"目标原帖未解析：{item['nativeAction']}",
        )

    simulation_id = str(
        psql_scalar(
            f"SELECT id FROM simulations WHERE run_id = '{run_id}'"
        )
    )
    engine_counts = engine_native_counts(simulation_id)
    for platform_key in ("platform_a", "platform_b"):
        server_counts = dict(platforms[platform_key]["byAction"])
        native = {
            action: count
            for action, count in engine_counts.get(platform_key, {}).items()
            if count
        }
        require(
            native == server_counts,
            f"{platform_key} 引擎记录与服务端计数不一致：{native} != {server_counts}",
        )
    events = fetch_job_events(
        token, project_id, fetch_simulation(token, project_id, run_id)["jobId"]  # type: ignore[index]
    )
    seqs = [int(item["seq"]) for item in events["events"]]  # type: ignore[index]
    require(seqs == list(range(1, len(seqs) + 1)), "运行事件序号不连续")
    require(len(seqs) >= 10, f"运行事件过少：{len(seqs)}")
    record_simulation_calls(report, run_id=run_id)
    record_simulation_evidence(
        report,
        "simulation-full-chain",
        runId=run_id,
        selectionId=frozen["selectionId"],
        buildId=frozen["buildId"],
        snapshotId=frozen["snapshotId"],
        roles=frozen["roles"],
        rounds=3,
        engine="double",
        platforms={
            key: {
                "roundsCompleted": value["roundsCompleted"],
                "actionCount": value["actionCount"],
                "byAction": value["byAction"],
            }
            for key, value in platforms.items()
        },
        engineCounts=engine_counts,
        targetLinks=[
            {
                "actionId": item["actionId"],
                "platformKey": item["platformKey"],
                "nativeAction": item["nativeAction"],
                "targetActionId": item["targetActionId"],
            }
            for item in comment_targets
        ],
        events=len(seqs),
    )
    entry["detail"] = (
        f"运行 {run_id} 成功：platform_a {platforms['platform_a']['actionCount']} 个动作、"
        f"platform_b {platforms['platform_b']['actionCount']} 个动作，"
        f"引擎记录与服务端计数一致，事件 {len(seqs)} 条连续"
    )


def scenario_simulation_stop_and_rerun(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    selection_id = state["simulation_selection"]
    recreate_simulation_worker(SIMULATION_ROUND_DELAY_MS="1200", SIMULATION_FAULT="")
    status, body = start_simulation_run(token, project_id, str(selection_id))
    require(status == 202, f"停止场景开始运行期望 202，实际 {status}：{body}")
    run_id = str(body["simulation"]["runId"])  # type: ignore[index]
    time.sleep(2.0)
    stop_status, _, stop_body = api_call(
        "POST",
        simulation_path(project_id, run_id, "/stop"),
        token,
        headers={"Origin": BASE_URL},
    )
    require(stop_status == 200, f"停止期望 200，实际 {stop_status}：{stop_body}")
    require(stop_body["cancelApplied"] is True, "首次停止应生效")
    stopped = wait_simulation(token, project_id, run_id, timeout=120)
    require(stopped["phase"] == "canceled", f"停止后状态不是 canceled：{stopped['phase']}")
    first_counts = stopped["counts"]
    time.sleep(1.5)
    again = fetch_simulation(token, project_id, run_id)
    require(again["counts"] == first_counts, "终态后计数仍在增长")
    late_stop = api_call(
        "POST",
        simulation_path(project_id, run_id, "/stop"),
        token,
        headers={"Origin": BASE_URL},
    )
    require(late_stop[0] == 200, "重复停止应为 200")
    require(late_stop[2]["cancelApplied"] is False, "重复停止不应再次生效")

    recreate_simulation_worker(SIMULATION_ROUND_DELAY_MS="0", SIMULATION_FAULT="")
    rerun_status, rerun_body = start_simulation_run(
        token, project_id, str(selection_id)
    )
    require(rerun_status == 202, f"重跑期望 202，实际 {rerun_status}：{rerun_body}")
    new_run = str(rerun_body["simulation"]["runId"])  # type: ignore[index]
    require(new_run != run_id, "重跑仍返回旧 runId")
    detail = wait_simulation(token, project_id, new_run)
    require(detail["phase"] == "succeeded", f"重跑未成功：{detail['phase']}")
    old = fetch_simulation(token, project_id, run_id)
    require(old["phase"] == "canceled", "旧运行终态被改写")
    require(old["counts"] == first_counts, "旧运行计数被改写")
    record_simulation_calls(report, run_id=run_id)
    record_simulation_calls(report, run_id=new_run)
    record_simulation_evidence(
        report,
        "simulation-stop-and-rerun",
        stoppedRunId=run_id,
        newRunId=new_run,
        stoppedCounts=first_counts,
        stable=again["counts"] == first_counts,
        oldRunReadable=old["phase"] == "canceled",
    )
    entry["detail"] = (
        f"停止后终态 canceled、计数冻结在 {first_counts['total']}；"
        f"重跑得到新 runId {new_run}，旧运行仍可读"
    )


def scenario_simulation_takeover(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    selection_id = state["simulation_selection"]
    recreate_simulation_worker(
        SIMULATION_FAULT="platform_a:1:after_first_action",
        DUET_CHILD_SLEEP_MS="2500",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_KEEP_RUNTIME="true",
    )
    status, body = start_simulation_run(token, project_id, str(selection_id))
    require(status == 202, f"接手场景开始运行期望 202，实际 {status}：{body}")
    run_id = str(body["simulation"]["runId"])  # type: ignore[index]
    time.sleep(2.2)
    killed = compose("stop", "-t", "0", "worker")
    require(killed.returncode == 0, f"停止 worker 失败：{killed.stderr.strip()}")
    before = fetch_simulation(token, project_id, run_id)
    require(not before["terminal"], "中断时应仍为运行中")
    time.sleep(2.5)
    recreate_simulation_worker(
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_KEEP_RUNTIME="true",
    )
    detail = wait_simulation(token, project_id, run_id, timeout=240)
    require(detail["phase"] == "succeeded", f"接手后未完成：{detail['phase']}")
    attempts = int(
        psql_scalar(f"SELECT attempts FROM jobs WHERE run_id = '{run_id}'") or 0
    )
    require(attempts > 1, f"接手后 attempts 应大于 1，实际 {attempts}")
    actions = fetch_simulation_actions(token, project_id, run_id)
    items = actions["items"]  # type: ignore[index]
    records = [(item["platformKey"], item["engineRecordId"]) for item in items]
    require(len(records) == len(set(records)), "接手后同一平台出现重复引擎记录")
    simulation_id = str(
        psql_scalar(f"SELECT id FROM simulations WHERE run_id = '{run_id}'")
    )
    engine_counts = engine_native_counts(simulation_id)
    platforms = {item["platformKey"]: item for item in detail["platforms"]}  # type: ignore[index]
    for platform_key in ("platform_a", "platform_b"):
        native = {
            action: count
            for action, count in engine_counts.get(platform_key, {}).items()
            if count
        }
        require(
            native == dict(platforms[platform_key]["byAction"]),
            f"接手后 {platform_key} 引擎与服务端计数不一致",
        )
    reconcile = int(
        psql_scalar(
            "SELECT count(*) FROM simulation_rounds WHERE simulation_id = "
            f"'{simulation_id}'"
        )
        or 0
    )
    require(reconcile == 6, f"接手后轮次记录应为 6，实际 {reconcile}")
    record_simulation_calls(report, run_id=run_id)
    record_simulation_evidence(
        report,
        "simulation-takeover",
        runId=run_id,
        attempts=attempts,
        beforeKill={
            "rounds": [
                {"platformKey": p["platformKey"], "roundsCompleted": p["roundsCompleted"]}
                for p in before["platforms"]
            ],
            "counts": before["counts"],
        },
        after={
            "phase": detail["phase"],
            "counts": detail["counts"],
            "engineCounts": engine_counts,
            "rounds": reconcile,
        },
    )
    entry["detail"] = (
        f"帖子已落库、轮次标记未写入时中断：attempts={attempts}，"
        f"接手后 {detail['counts']['total']} 个动作与引擎记录一致，轮次 6 条"
    )


def scenario_simulation_partial_failure(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    selection_id = state["simulation_selection"]
    recreate_simulation_worker(
        SIMULATION_FAULT="platform_a:1:strip_marker",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_KEEP_RUNTIME="false",
    )
    status, body = start_simulation_run(token, project_id, str(selection_id))
    require(status == 202, f"失败场景开始运行期望 202，实际 {status}：{body}")
    run_id = str(body["simulation"]["runId"])  # type: ignore[index]
    detail = wait_simulation(token, project_id, run_id, timeout=180)
    require(detail["phase"] == "failed", f"不可确认时应失败：{detail['phase']}")
    require(detail["partial"] is True, "单平台失败应标记部分结果")
    require(
        detail["failedPlatforms"] == ["platform_a"],
        f"失败平台异常：{detail['failedPlatforms']}",
    )
    require(
        detail["failure"]["code"] == "action_unconfirmed",
        f"失败原因码异常：{detail['failure']}",
    )
    platforms = {item["platformKey"]: item for item in detail["platforms"]}  # type: ignore[index]
    require(platforms["platform_a"]["status"] == "failed", "平台 A 未标记失败")
    require(platforms["platform_b"]["status"] == "succeeded", "平台 B 未完成")
    require(platforms["platform_b"]["roundsCompleted"] == 3, "平台 B 轮次不足")
    require(platforms["platform_b"]["actionCount"] > 0, "平台 B 没有保留动作")
    record_simulation_calls(report, run_id=run_id)
    record_simulation_evidence(
        report,
        "simulation-partial-failure",
        runId=run_id,
        failedPlatforms=detail["failedPlatforms"],
        failure=detail["failure"],
        survivingPlatform={
            "platformKey": "platform_b",
            "roundsCompleted": platforms["platform_b"]["roundsCompleted"],
            "actionCount": platforms["platform_b"]["actionCount"],
            "byAction": platforms["platform_b"]["byAction"],
        },
    )
    entry["detail"] = (
        f"平台 A 不可确认（action_unconfirmed），平台 B 保留 "
        f"{platforms['platform_b']['actionCount']} 个动作与 3 轮"
    )


def scenario_simulation_boundaries(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    run_id = state["simulation_run"]
    anonymous, _, _ = raw_request("GET", simulation_path(project_id, run_id))
    require(anonymous == 401, "匿名运行详情应为 401")

    client_b = HttpClient()
    login_status, _, _ = client_b.request(
        "POST",
        "/api/auth/login",
        payload={"email": ACCOUNT_B, "password": state["password"]},
        headers={"Origin": BASE_URL},
    )
    require(login_status == 200, f"B 账号登录失败：{login_status}")
    other_token = client_b.cookie_value()
    require(bool(other_token), "B 登录未设置会话 Cookie")
    other_status, _, other_body = api_call(
        "GET", simulation_path(project_id, run_id), other_token
    )
    require(other_status == 404, f"他人项目期望 404，实际 {other_status}")
    require(
        isinstance(other_body, dict) and other_body.get("detail") == "项目不存在",
        f"他人项目响应异常：{other_body}",
    )
    random_id = str(uuid.uuid4())
    missing_status, _, missing_body = api_call(
        "GET", simulation_path(project_id, random_id), token
    )
    require(missing_status == 404, f"不存在运行期望 404，实际 {missing_status}")
    require(
        isinstance(missing_body, dict) and missing_body.get("detail") == "运行不存在",
        f"不存在运行响应异常：{missing_body}",
    )
    invalid_status, _, _ = api_call(
        "POST",
        simulation_path(project_id),
        token,
        payload={"selectionId": "not-a-uuid"},
        headers={"Origin": BASE_URL},
    )
    require(invalid_status == 422, f"非法选择期望 422，实际 {invalid_status}")
    record_simulation_evidence(
        report,
        "simulation-boundaries",
        anonymous=anonymous,
        otherProject=other_status,
        unknownRun=missing_status,
        invalidSelection=invalid_status,
    )
    entry["detail"] = "匿名 401、他人项目 404、未知运行 404、非法选择 422"


def record_temporal_memory_calls(report: dict[str, Any], *, run_id: str) -> None:
    if report.get("temporal_memory_evidence") is None:
        report["temporal_memory_evidence"] = {
            "mode": "offline-stub",
            "graphProvider": "stub",
            "calls": {"real": 0, "double": 0},
            "callScope": [],
            "scenarios": [],
            "isolation": None,
            "workerRestored": None,
        }
    evidence = report["temporal_memory_evidence"]
    raw = psql_scalar(
        "SELECT json_build_object('real', memory_calls_real, 'double', "
        "memory_calls_double, 'episodes', (SELECT count(*) FROM "
        "simulation_memory_episodes WHERE simulation_id = simulations.id), "
        "'graphId', (SELECT graph_id FROM simulation_graph_bindings WHERE "
        "simulation_id = simulations.id)) FROM simulations WHERE run_id = "
        f"'{run_id}'"
    )
    require(bool(raw), f"运行 {run_id} 缺少记忆调用计数")
    counts = json.loads(raw)
    scope = [item for item in evidence["callScope"] if item["runId"] != run_id]
    scope.append(
        {
            "runId": run_id,
            "graphId": counts.get("graphId"),
            "episodes": int(counts.get("episodes") or 0),
            "real": int(counts.get("real") or 0),
            "double": int(counts.get("double") or 0),
        }
    )
    evidence["callScope"] = scope
    evidence["calls"] = {
        "real": sum(item["real"] for item in scope),
        "double": sum(item["double"] for item in scope),
    }


def record_temporal_memory_evidence(
    report: dict[str, Any], scenario_id: str, **fields: object
) -> None:
    if report.get("temporal_memory_evidence") is None:
        report["temporal_memory_evidence"] = {
            "mode": "offline-stub",
            "graphProvider": "stub",
            "calls": {"real": 0, "double": 0},
            "callScope": [],
            "scenarios": [],
            "isolation": None,
            "workerRestored": None,
        }
    report["temporal_memory_evidence"]["scenarios"].append(
        {"scenario": scenario_id, **fields}
    )


def fetch_simulation_memory(
    token: str,
    project_id: str,
    run_id: str,
    *,
    round_index: int | None = None,
    platform: str | None = None,
) -> dict[str, object]:
    query: list[str] = []
    if round_index is not None:
        query.append(f"round={round_index}")
    if platform:
        query.append(f"platform={platform}")
    suffix = "/memory" + (f"?{'&'.join(query)}" if query else "")
    status, _, body = api_call(
        "GET", simulation_path(project_id, run_id, suffix), token
    )
    require(status == 200, f"运行时记忆期望 200，实际 {status}：{body}")
    require(isinstance(body, dict), "运行时记忆不是对象")
    return body


def wait_memory_status(
    token: str,
    project_id: str,
    run_id: str,
    *,
    timeout: float = 180.0,
) -> dict[str, object]:
    deadline = time.time() + timeout
    last: dict[str, object] = {}
    while time.time() < deadline:
        detail = fetch_simulation(token, project_id, run_id)
        last = detail.get("memory") or {}  # type: ignore[assignment]
        if last.get("status") in ("complete", "unconfirmed", "unavailable", "unconfigured"):
            return detail
        time.sleep(0.4)
    raise ScenarioFailed(f"运行时记忆未在 {timeout} 秒内稳定：{last}")


def scenario_temporal_memory_not_triggered(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    status, _, body = api_call("GET", simulation_path(project_id), token)
    require(status == 200, f"运行列表期望 200，实际 {status}：{body}")
    require(body.get("simulations") == [], f"默认验收不应有运行记录：{body}")  # type: ignore[union-attr]
    anonymous, _, _ = raw_request(
        "GET", simulation_path(project_id, str(uuid.uuid4()), "/memory?round=1")
    )
    require(anonymous == 401, "匿名记忆读取应为 401")
    rows = int(
        psql_scalar("SELECT count(*) FROM simulation_memory_syncs") or 0
    )
    require(rows == 0, f"默认验收不应创建记忆任务，实际 {rows}")
    report["temporal_memory_evidence"] = {
        "mode": "not-triggered",
        "graphProvider": "unconfigured",
        "calls": {"real": 0, "double": 0},
        "callScope": [],
        "scenarios": [
            {
                "scenario": "temporal-memory-not-triggered",
                "listStatus": status,
                "anonymousStatus": anonymous,
                "memoryJobs": rows,
            }
        ],
        "isolation": None,
        "workerRestored": None,
    }
    entry["detail"] = "默认验收未触发运行或记忆任务；匿名记忆读取 401"


def scenario_temporal_memory_full_chain(
    entry: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    fixtures: DocumentFixtures,
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    recreate_simulation_worker(
        SIMULATION_KEEP_RUNTIME="true",
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_MEMORY_PROVIDER="stub",
        EVIDENCE_GRAPH_ALLOW_STUB="true",
    )
    frozen = prepare_frozen_selection(entry, state, fixtures)
    state["temporal_selection"] = frozen["selectionId"]
    simulation_id = str(
        psql_scalar(
            "SELECT id FROM simulations WHERE selection_id = "
            f"'{frozen['selectionId']}' ORDER BY created_at DESC LIMIT 1"
        )
        or ""
    )
    # prepare_frozen_selection 尚未开始运行；先取快照基线，再开始。
    snapshot_before = psql_scalar(
        "SELECT json_build_object("
        "'nodes', (SELECT count(*) FROM graph_nodes WHERE snapshot_id = "
        f"'{frozen['snapshotId']}'),"
        "'edges', (SELECT count(*) FROM graph_edges WHERE snapshot_id = "
        f"'{frozen['snapshotId']}'),"
        "'evidence', (SELECT count(*) FROM graph_evidence WHERE snapshot_id = "
        f"'{frozen['snapshotId']}'),"
        "'hash', (SELECT md5(COALESCE(string_agg(node_key || ':' || label || ':' "
        "|| node_type, '|' ORDER BY node_key), '')) FROM graph_nodes WHERE "
        f"snapshot_id = '{frozen['snapshotId']}') || ':' || "
        "(SELECT md5(COALESCE(string_agg(edge_key || ':' || label || ':' || "
        "edge_type, '|' ORDER BY edge_key), '')) FROM graph_edges WHERE "
        f"snapshot_id = '{frozen['snapshotId']}') || ':' || "
        "(SELECT md5(COALESCE(string_agg(target_kind || ':' || target_key || ':' "
        "|| evidence_index::text || ':' || source_start::text || ':' || "
        "source_end::text, '|' ORDER BY target_kind, target_key, evidence_index), '')) "
        f"FROM graph_evidence WHERE snapshot_id = '{frozen['snapshotId']}'))::text"
    )
    status, body = start_simulation_run(
        token, project_id, str(frozen["selectionId"])
    )
    require(status == 202, f"开始运行期望 202，实际 {status}：{body}")
    run_id = str(body["simulation"]["runId"])  # type: ignore[index]
    state["temporal_run"] = run_id
    detail = wait_simulation(token, project_id, run_id)
    require(detail["phase"] == "succeeded", f"运行未成功：{detail['phase']}")
    detail = wait_memory_status(token, project_id, run_id)
    memory = detail.get("memory") or {}
    require(memory.get("status") == "complete", f"记忆未完成：{memory}")
    simulation_id = str(
        psql_scalar(f"SELECT id FROM simulations WHERE run_id = '{run_id}'")
    )
    actions = fetch_simulation_actions(token, project_id, run_id)
    items = actions["items"]  # type: ignore[index]
    comment = next(
        (
            item
            for item in items
            if item["platformKey"] == "platform_b"
            and int(item["roundIndex"]) == 2
            and item["nativeAction"] == "create_comment"
        ),
        None,
    )
    require(comment is not None, "第 2 轮缺少话题社区评论")
    memory1 = fetch_simulation_memory(
        token, project_id, run_id, round_index=1, platform="platform_b"
    )
    memory2 = fetch_simulation_memory(
        token, project_id, run_id, round_index=2, platform="platform_b"
    )
    memory3 = fetch_simulation_memory(
        token, project_id, run_id, round_index=3, platform="platform_b"
    )
    ids1 = {item["memoryId"] for item in memory1["entries"]}  # type: ignore[index]
    ids2 = {item["memoryId"] for item in memory2["entries"]}  # type: ignore[index]
    ids3 = {item["memoryId"] for item in memory3["entries"]}  # type: ignore[index]
    memory_id = str(
        psql_scalar(
            "SELECT id FROM simulation_memory_entries WHERE action_id = "
            f"'{comment['actionId']}'"
        )
    )
    require(memory_id not in ids1, "第 1 轮图不应包含第 2 轮评论")
    require(memory_id in ids2 and memory_id in ids3, "第 2/3 轮图应包含第 2 轮评论")
    source = None
    for node in memory2["runtime"]["nodes"]:  # type: ignore[index]
        for candidate in node.get("sources") or []:
            if candidate.get("actionId") == comment["actionId"]:
                source = candidate
                break
    require(source is not None, "第 2 轮图缺少评论来源")
    require(
        source["actionId"] == comment["actionId"]
        and source["platformKey"] == comment["platformKey"]
        and int(source["roundIndex"]) == 2
        and source["sourceEventSeq"] >= 1,
        f"评论来源字段异常：{source}",
    )
    material_with_sources = [
        item
        for item in (memory2["material"]["nodes"] + memory2["material"]["edges"])  # type: ignore[index]
        if item.get("sources")
    ]
    require(bool(material_with_sources), "材料层没有可见来源")
    history_node = next(
        (
            node
            for node in memory2["runtime"]["nodes"]  # type: ignore[index]
            if any(int(src["roundIndex"]) == 2 for src in node.get("sources") or [])
        ),
        None,
    )
    require(history_node is not None, "缺少第 2 轮历史节点")
    memory3_again = fetch_simulation_memory(
        token, project_id, run_id, round_index=3, platform="platform_b"
    )
    memory2_again = fetch_simulation_memory(
        token, project_id, run_id, round_index=2, platform="platform_b"
    )
    history_again = next(
        node for node in memory2_again["runtime"]["nodes"]  # type: ignore[index]
        if node["key"] == history_node["key"]
    )
    require(
        history_again["summary"] == history_node["summary"]
        and history_again["sourceCount"] == history_node["sourceCount"]
        and all(int(src["roundIndex"]) <= 2 for src in history_again["sources"]),
        f"第 3 轮完成后重读第 2 轮摘要/来源漂移：{history_node} != {history_again}",
    )
    for node in memory2["runtime"]["nodes"]:  # type: ignore[index]
        for candidate in node.get("sources") or []:
            require(
                int(candidate["roundIndex"]) <= 2,
                "第 2 轮图混入第 3 轮来源",
            )
    plan_json = psql_scalar(
        "SELECT request_payload::text FROM simulation_round_plans WHERE "
        f"simulation_id = '{simulation_id}' AND platform_key = 'platform_b' "
        "AND round_index = 2"
    )
    require(bool(plan_json), "缺少第 2 轮计划")
    plan = json.loads(plan_json)
    refs = plan.get("memoryRefs") or []
    require(bool(refs), "第 2 轮计划没有前序记忆引用")
    for ref in refs:
        require(int(ref["roundIndex"]) < 2, f"计划引用了当前/未来轮次：{ref}")
    platform_rows = psql_scalar(
        "SELECT string_agg(DISTINCT e.platform_key, ',') FROM "
        "simulation_memory_entries e WHERE e.simulation_id = "
        f"'{simulation_id}' AND e.id = ANY(ARRAY["
        + ",".join(f"'{item['memoryId']}'" for item in refs)
        + "]::uuid[])"
    )
    require(platform_rows == "platform_b", f"第 2 轮读取了其他平台记忆：{platform_rows}")
    reads = psql_scalar(
        "SELECT string_agg(memory_id::text, ',' ORDER BY memory_id) FROM "
        "simulation_action_memory_reads WHERE action_id = "
        f"'{comment['actionId']}'"
    )
    require(
        set(reads.split(",")) == {str(item["memoryId"]) for item in refs},
        f"行动读取记录与计划不一致：{reads} != {refs}",
    )
    request_path = (
        f"/app/data/simulations/{simulation_id}/platform_b/round-2-request.json"
    )
    request_result = compose("exec", "-T", "worker", "cat", request_path)
    require(
        request_result.returncode == 0,
        f"读取引擎请求失败：{request_result.stderr.strip()}",
    )
    request = json.loads(request_result.stdout)
    request_refs = {
        str(ref["memoryId"])
        for action in request.get("actions") or []
        for ref in action.get("memoryRefs") or []
    }
    require(
        request_refs == {str(item["memoryId"]) for item in refs},
        "引擎请求中的 memoryRefs 与计划不一致",
    )
    snapshot_after = psql_scalar(
        "SELECT json_build_object("
        "'nodes', (SELECT count(*) FROM graph_nodes WHERE snapshot_id = "
        f"'{frozen['snapshotId']}'),"
        "'edges', (SELECT count(*) FROM graph_edges WHERE snapshot_id = "
        f"'{frozen['snapshotId']}'),"
        "'evidence', (SELECT count(*) FROM graph_evidence WHERE snapshot_id = "
        f"'{frozen['snapshotId']}'),"
        "'hash', (SELECT md5(COALESCE(string_agg(node_key || ':' || label || ':' "
        "|| node_type, '|' ORDER BY node_key), '')) FROM graph_nodes WHERE "
        f"snapshot_id = '{frozen['snapshotId']}') || ':' || "
        "(SELECT md5(COALESCE(string_agg(edge_key || ':' || label || ':' || "
        "edge_type, '|' ORDER BY edge_key), '')) FROM graph_edges WHERE "
        f"snapshot_id = '{frozen['snapshotId']}') || ':' || "
        "(SELECT md5(COALESCE(string_agg(target_kind || ':' || target_key || ':' "
        "|| evidence_index::text || ':' || source_start::text || ':' || "
        "source_end::text, '|' ORDER BY target_kind, target_key, evidence_index), '')) "
        f"FROM graph_evidence WHERE snapshot_id = '{frozen['snapshotId']}'))::text"
    )
    require(snapshot_before == snapshot_after, "运行改写了冻结材料快照")
    record_simulation_calls(report, run_id=run_id)
    record_temporal_memory_calls(report, run_id=run_id)
    record_temporal_memory_evidence(
        report,
        "temporal-memory-full-chain",
        runId=run_id,
        selectionId=frozen["selectionId"],
        actionId=comment["actionId"],
        memoryId=memory_id,
        episodeId=source.get("episodeId"),
        platformKey="platform_b",
        roundIndex=2,
        sourceEventSeq=source.get("sourceEventSeq"),
        round1Contains=memory_id in ids1,
        round2Contains=memory_id in ids2,
        round3Contains=memory_id in ids3,
        memoryRefs=[item["memoryId"] for item in refs],
        requestRefs=sorted(request_refs),
        reads=sorted(reads.split(",")),
        snapshotBefore=json.loads(snapshot_before),
        snapshotAfter=json.loads(snapshot_after),
        snapshotUntouched=snapshot_before == snapshot_after,
        materialSources=len(material_with_sources),
        historySummaryStable=history_again["summary"] == history_node["summary"],
        executionEntry="round-plan",
        materialSnapshotId=frozen["snapshotId"],
    )
    entry["detail"] = (
        f"第 2 轮评论 {comment['actionId']} 在 1/2/3 轮可见性为"
        f" {memory_id in ids1}/{memory_id in ids2}/{memory_id in ids3}；"
        f"B 第 2 轮读取 {len(refs)} 条 B 第 1 轮记忆，快照未改写"
    )


def scenario_temporal_memory_isolation(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    selection_id = state["temporal_selection"]
    first_run = state["temporal_run"]
    first_ids = {
        str(row)
        for row in (
            psql_scalar(
                "SELECT string_agg(id::text, ',') FROM simulation_memory_entries WHERE "
                "simulation_id = (SELECT id FROM simulations WHERE run_id = "
                f"'{first_run}')"
            )
            or ""
        ).split(",")
        if row
    }
    recreate_simulation_worker(
        SIMULATION_KEEP_RUNTIME="true",
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_MEMORY_PROVIDER="stub",
        EVIDENCE_GRAPH_ALLOW_STUB="true",
    )
    status, body = start_simulation_run(token, project_id, str(selection_id))
    require(status == 202, f"重跑期望 202，实际 {status}：{body}")
    second_run = str(body["simulation"]["runId"])  # type: ignore[index]
    require(second_run != first_run, "重跑未产生新 runId")
    wait_simulation(token, project_id, second_run)
    wait_memory_status(token, project_id, second_run)
    second_ids = {
        str(row)
        for row in (
            psql_scalar(
                "SELECT string_agg(id::text, ',') FROM simulation_memory_entries WHERE "
                "simulation_id = (SELECT id FROM simulations WHERE run_id = "
                f"'{second_run}')"
            )
            or ""
        ).split(",")
        if row
    }
    require(first_ids and second_ids, "两次运行缺少记忆")
    overlap = first_ids & second_ids
    require(not overlap, f"两次运行记忆重叠：{sorted(overlap)[:3]}")
    old_view = fetch_simulation_memory(
        token, project_id, first_run, round_index=2, platform="platform_b"
    )
    require(bool(old_view.get("entries")), "旧运行第 2 轮记忆不可读")
    record_simulation_calls(report, run_id=second_run)
    record_temporal_memory_calls(report, run_id=second_run)
    report["temporal_memory_evidence"]["isolation"] = {
        "runA": first_run,
        "runB": second_run,
        "overlap": len(overlap),
        "oldRunReadable": bool(old_view.get("entries")),
    }
    record_temporal_memory_evidence(
        report,
        "temporal-memory-isolation",
        runId=second_run,
        previousRunId=first_run,
        overlap=len(overlap),
        oldRunReadable=True,
    )
    entry["detail"] = (
        f"同一 selectionId 重跑得到 {second_run}；两次记忆交集 {len(overlap)}，"
        "旧运行第 2 轮仍可读"
    )


def scenario_temporal_memory_projection_pause(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    selection_id = state["temporal_selection"]
    recreate_simulation_worker(
        SIMULATION_KEEP_RUNTIME="true",
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="1200",
        SIMULATION_MEMORY_PROVIDER="stub",
        EVIDENCE_GRAPH_ALLOW_STUB="true",
        SIMULATION_MEMORY_SYNC_PAUSE_SECONDS="5",
        SIMULATION_MEMORY_ACCEPT_PAUSE_SECONDS="0",
    )
    scale_worker(
        2,
        SIMULATION_MEMORY_PROVIDER="stub",
        EVIDENCE_GRAPH_ALLOW_STUB="true",
        SIMULATION_MEMORY_SYNC_PAUSE_SECONDS="5",
        SIMULATION_MEMORY_ACCEPT_PAUSE_SECONDS="0",
        SIMULATION_ROUND_DELAY_MS="1200",
    )
    worker_env = compose("exec", "-T", "worker", "env").stdout
    require(
        "SIMULATION_MEMORY_PROVIDER=stub" in worker_env
        and "SIMULATION_MEMORY_SYNC_PAUSE_SECONDS=5" in worker_env,
        "暂停场景 worker 环境未生效：" + worker_env,
    )
    status, body = start_simulation_run(token, project_id, str(selection_id))
    require(status == 202, f"暂停场景开始运行期望 202，实际 {status}：{body}")
    run_id = str(body["simulation"]["runId"])  # type: ignore[index]
    state["temporal_pause_run"] = run_id
    simulation_id = str(
        psql_scalar(f"SELECT id FROM simulations WHERE run_id = '{run_id}'")
    )
    time.sleep(1.5)
    before = fetch_simulation(token, project_id, run_id)
    require(not before["terminal"], "暂停窗口内运行已过早终态")
    time.sleep(2.0)
    during = fetch_simulation(token, project_id, run_id)
    require(
        int(during["counts"]["total"]) >= int(before["counts"]["total"]),
        "暂停期间动作计数回退",
    )
    before_rounds = {
        item["platformKey"]: int(item["roundsCompleted"])
        for item in before["platforms"]  # type: ignore[index]
    }
    during_rounds = {
        item["platformKey"]: int(item["roundsCompleted"])
        for item in during["platforms"]  # type: ignore[index]
    }
    require(
        any(during_rounds[key] > before_rounds[key] for key in before_rounds),
        f"暂停期间没有平台推进：{before_rounds} -> {during_rounds}",
    )
    memory = during.get("memory") or {}
    require(
        int(memory.get("pendingEpisodes") or 0) > 0,
        f"暂停期间待同步数不可见：{memory}",
    )
    terminal = wait_simulation(token, project_id, run_id, timeout=180)
    terminal_counts = terminal["counts"]
    terminal_rounds = {
        item["platformKey"]: int(item["roundsCompleted"])
        for item in terminal["platforms"]  # type: ignore[index]
    }
    detail = wait_memory_status(token, project_id, run_id, timeout=180)
    require(
        (detail.get("memory") or {}).get("status") == "complete",
        f"暂停恢复后记忆未补齐：{detail.get('memory')}",
    )
    require(detail["counts"] == terminal_counts, "记忆补齐后动作计数变化")
    after_rounds = {
        item["platformKey"]: int(item["roundsCompleted"])
        for item in detail["platforms"]  # type: ignore[index]
    }
    require(after_rounds == terminal_rounds, "记忆补齐后轮次变化")
    scale_worker(1)
    record_temporal_memory_calls(report, run_id=run_id)
    record_temporal_memory_evidence(
        report,
        "temporal-memory-projection-pause",
        runId=run_id,
        providerRecordsBeforeResume=0,
        beforeRounds=before_rounds,
        duringRounds=during_rounds,
        terminalRounds=terminal_rounds,
        pendingDuringPause=memory.get("pendingEpisodes"),
        memoryJobId=memory.get("memoryJobId"),
        actionsBefore=during["counts"],
        actionsAfter=detail["counts"],
        memoryStatus="complete",
    )
    entry["detail"] = (
        f"图服务暂停 5s：两平台轮次 {before_rounds} -> {during_rounds}，"
        f"待同步 {memory.get('pendingEpisodes')} 条；恢复后记忆补齐且计数一致"
    )


def scenario_temporal_memory_takeover(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    run_id = state.get("temporal_pause_run") or state["temporal_run"]
    simulation_id = str(
        psql_scalar(f"SELECT id FROM simulations WHERE run_id = '{run_id}'")
    )
    target = psql_scalar(
        "SELECT memory_id::text || '|' || idempotency_key::text FROM "
        "simulation_memory_episodes WHERE simulation_id = "
        f"'{simulation_id}' ORDER BY round_index, memory_id LIMIT 1"
    )
    require(bool(target), "缺少接手目标 episode")
    memory_id, idempotency_key = target.split("|")
    psql_exec(
        "DELETE FROM simulation_memory_provider_records WHERE simulation_id = "
        f"'{simulation_id}' AND memory_id = '{memory_id}'"
    )
    psql_exec(
        "UPDATE simulation_memory_episodes SET episode_uuid = NULL, add_count = 0, "
        "state = 'planned', write_outcome = 'not_attempted' WHERE simulation_id = "
        f"'{simulation_id}' AND memory_id = '{memory_id}'"
    )
    psql_exec(
        f"UPDATE simulations SET memory_status = 'pending' WHERE id = '{simulation_id}'"
    )
    recreate_simulation_worker(
        SIMULATION_KEEP_RUNTIME="true",
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_MEMORY_PROVIDER="stub",
        EVIDENCE_GRAPH_ALLOW_STUB="true",
        SIMULATION_MEMORY_ACCEPT_PAUSE_SECONDS="10",
    )
    status, _, body = api_call(
        "POST",
        simulation_path(project_id, run_id, "/memory/retry"),
        token,
        headers={"Origin": BASE_URL},
    )
    require(status == 202, f"接手场景重试期望 202，实际 {status}：{body}")
    memory_job_id = str(body["memoryJobId"])  # type: ignore[index]
    deadline = time.time() + 60
    provider = ""
    local = ""
    while time.time() < deadline:
        provider = psql_scalar(
            "SELECT count(*)::text || '|' || COALESCE(max(episode_uuid::text), '') "
            "FROM simulation_memory_provider_records WHERE simulation_id = "
            f"'{simulation_id}' AND memory_id = '{memory_id}'"
        )
        local = psql_scalar(
            "SELECT add_count::text || '|' || COALESCE(episode_uuid::text, '') || "
            "'|' || state FROM simulation_memory_episodes WHERE simulation_id = "
            f"'{simulation_id}' AND memory_id = '{memory_id}'"
        )
        if provider.startswith("1|") and local.startswith("1|") and "|" in local:
            parts = local.split("|")
            if parts[0] == "1" and parts[1] == "":
                break
        time.sleep(0.25)
    require(provider.startswith("1|"), f"provider 未受理：{provider}")
    require(local.startswith("1|"), f"本地未记录 add_count：{local}")
    parts = local.split("|")
    require(parts[1] == "", f"本地已保存 UUID，错过中断窗口：{local}")
    stopped = compose("stop", "-t", "0", "worker")
    require(stopped.returncode == 0, f"停止持有租约的 worker 失败：{stopped.stderr}")
    time.sleep(8.0)
    recreate_simulation_worker(
        SIMULATION_KEEP_RUNTIME="true",
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_MEMORY_PROVIDER="stub",
        EVIDENCE_GRAPH_ALLOW_STUB="true",
        SIMULATION_MEMORY_ACCEPT_PAUSE_SECONDS="0",
        SIMULATION_MEMORY_SYNC_PAUSE_SECONDS="0",
    )
    detail = wait_memory_status(token, project_id, run_id, timeout=180)
    require(
        (detail.get("memory") or {}).get("status") == "complete",
        f"接手后记忆未完成：{detail.get('memory')}",
    )
    attempts = int(
        psql_scalar(f"SELECT attempts FROM jobs WHERE id = '{memory_job_id}'") or 0
    )
    after = psql_scalar(
        "SELECT add_count::text || '|' || episode_uuid::text || '|' || "
        "idempotency_key::text FROM simulation_memory_episodes WHERE simulation_id = "
        f"'{simulation_id}' AND memory_id = '{memory_id}'"
    )
    after_add, after_uuid, after_key = after.split("|")
    provider_uuid = psql_scalar(
        "SELECT episode_uuid FROM simulation_memory_provider_records WHERE "
        f"simulation_id = '{simulation_id}' AND memory_id = '{memory_id}'"
    )
    require(attempts > 1, f"接手后 memory job attempts 未增加：{attempts}")
    require(after_add == "1", f"接手后 add_count 不是 1：{after_add}")
    require(after_key == idempotency_key, "接手后幂等键变化")
    require(after_uuid == provider_uuid, "接手后 UUID 与 provider 记录不一致")
    record_temporal_memory_evidence(
        report,
        "temporal-memory-takeover",
        runId=run_id,
        memoryJobId=memory_job_id,
        memoryId=memory_id,
        attempts=attempts,
        addCountBefore=1,
        addCountAfter=int(after_add),
        providerUuid=provider_uuid,
        localUuid=after_uuid,
        idempotencyKey=after_key,
        reconciled=True,
    )
    # 查不到账：删除 provider 记录并把本地留在已尝试状态。
    psql_exec(
        "DELETE FROM simulation_memory_provider_records WHERE simulation_id = "
        f"'{simulation_id}' AND memory_id = '{memory_id}'"
    )
    psql_exec(
        "UPDATE simulation_memory_episodes SET episode_uuid = NULL, add_count = 1, "
        "state = 'planned', write_outcome = 'in_flight' WHERE simulation_id = "
        f"'{simulation_id}' AND memory_id = '{memory_id}'"
    )
    psql_exec(
        f"UPDATE simulations SET memory_status = 'pending' WHERE id = '{simulation_id}'"
    )
    status, _, body = api_call(
        "POST",
        simulation_path(project_id, run_id, "/memory/retry"),
        token,
        headers={"Origin": BASE_URL},
    )
    require(status == 202, f"未知结果重试期望 202，实际 {status}：{body}")
    detail = wait_memory_status(token, project_id, run_id, timeout=180)
    require(
        (detail.get("memory") or {}).get("status") == "unconfirmed",
        f"未知结果未显示 unconfirmed：{detail.get('memory')}",
    )
    view = fetch_simulation_memory(token, project_id, run_id, round_index=1)
    require(
        view["status"]["status"] == "unconfirmed",  # type: ignore[index]
        f"记忆视图未显示 unconfirmed：{view}",
    )
    provider_count = int(
        psql_scalar(
            "SELECT count(*) FROM simulation_memory_provider_records WHERE "
            f"simulation_id = '{simulation_id}' AND memory_id = '{memory_id}'"
        )
        or 0
    )
    require(provider_count == 0, "未查账时不应写入 provider 记录")
    record_temporal_memory_evidence(
        report,
        "temporal-memory-unconfirmed",
        runId=run_id,
        memoryId=memory_id,
        status=detail.get("memory", {}).get("status"),
        providerRecords=provider_count,
        idempotencyKeyUnchanged=True,
        newKeyCreated=False,
    )
    entry["detail"] = (
        f"provider 已受理后中断：memory job attempts={attempts}，add_count 1→1，"
        f"UUID 与 provider 一致；查不到账时 API/页面显示 unconfirmed 且不换键"
    )


def scenario_temporal_memory_recovery(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    run_id = state["temporal_run"]
    simulation_id = str(
        psql_scalar(f"SELECT id FROM simulations WHERE run_id = '{run_id}'")
    )
    target = psql_scalar(
        "SELECT memory_id::text || '|' || add_count::text || '|' || "
        "COALESCE(episode_uuid::text, '') FROM simulation_memory_episodes WHERE "
        f"simulation_id = '{simulation_id}' ORDER BY round_index, memory_id LIMIT 1"
    )
    require(bool(target), "缺少可恢复的记忆 episode")
    memory_id, add_count, _ = target.split("|")
    before = psql_scalar(
        "SELECT json_build_object('episodes', (SELECT count(*) FROM "
        "simulation_memory_episodes WHERE simulation_id = "
        f"'{simulation_id}'), 'nodes', (SELECT count(*) FROM simulation_memory_nodes "
        f"WHERE simulation_id = '{simulation_id}'), 'edges', (SELECT count(*) FROM "
        f"simulation_memory_edges WHERE simulation_id = '{simulation_id}'), "
        "'provenance', (SELECT count(*) FROM simulation_memory_provenance WHERE "
        f"simulation_id = '{simulation_id}'))::text"
    )
    psql_exec(
        "UPDATE simulation_memory_episodes SET episode_uuid = NULL, "
        "state = 'planned', write_outcome = 'in_flight' WHERE simulation_id = "
        f"'{simulation_id}' AND memory_id = '{memory_id}'"
    )
    psql_exec(
        "UPDATE simulations SET memory_status = 'pending' WHERE id = "
        f"'{simulation_id}'"
    )
    compose("stop", "-t", "0", "worker")
    first_status, _, first_body = api_call(
        "POST",
        simulation_path(project_id, run_id, "/memory/retry"),
        token,
        headers={"Origin": BASE_URL},
    )
    second_status, _, second_body = api_call(
        "POST",
        simulation_path(project_id, run_id, "/memory/retry"),
        token,
        headers={"Origin": BASE_URL},
    )
    require(first_status == 202, f"首次记忆重试期望 202，实际 {first_status}：{first_body}")
    require(second_status == 200, f"活动任务重试期望 200，实际 {second_status}：{second_body}")
    require(
        first_body["memoryJobId"] == second_body["memoryJobId"],  # type: ignore[index]
        "并发重试未复用同一记忆任务",
    )
    recreate_simulation_worker(
        SIMULATION_KEEP_RUNTIME="true",
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_MEMORY_PROVIDER="stub",
        EVIDENCE_GRAPH_ALLOW_STUB="true",
    )
    detail = wait_memory_status(token, project_id, run_id)
    require(
        (detail.get("memory") or {}).get("status") == "complete",
        f"记忆恢复后未完成：{detail.get('memory')}",
    )
    after_episode = psql_scalar(
        "SELECT add_count::text || '|' || COALESCE(episode_uuid::text, '') || '|' || "
        "state FROM simulation_memory_episodes WHERE simulation_id = "
        f"'{simulation_id}' AND memory_id = '{memory_id}'"
    )
    after_add, after_uuid, after_state = after_episode.split("|")
    require(after_add == add_count, f"查账补记出现第二次 add：{add_count} -> {after_add}")
    require(bool(after_uuid), "查账后仍未补记 episode UUID")
    require(after_state == "processed", f"查账后状态不是 processed：{after_state}")
    after = psql_scalar(
        "SELECT json_build_object('episodes', (SELECT count(*) FROM "
        "simulation_memory_episodes WHERE simulation_id = "
        f"'{simulation_id}'), 'nodes', (SELECT count(*) FROM simulation_memory_nodes "
        f"WHERE simulation_id = '{simulation_id}'), 'edges', (SELECT count(*) FROM "
        f"simulation_memory_edges WHERE simulation_id = '{simulation_id}'), "
        "'provenance', (SELECT count(*) FROM simulation_memory_provenance WHERE "
        f"simulation_id = '{simulation_id}'))::text"
    )
    require(before == after, f"恢复后记忆/节点/边/来源计数增长：{before} != {after}")
    # 任务终态后允许同一 simulationId 新建任务，同时保留旧任务历史。
    terminal_status, _, terminal_body = api_call(
        "POST",
        simulation_path(project_id, run_id, "/memory/retry"),
        token,
        headers={"Origin": BASE_URL},
    )
    require(terminal_status == 202, f"终态后重试期望 202，实际 {terminal_status}：{terminal_body}")
    require(
        terminal_body["memoryJobId"] != first_body["memoryJobId"],  # type: ignore[index]
        "终态后重试未创建新记忆任务",
    )
    history = int(
        psql_scalar(
            "SELECT count(*) FROM simulation_memory_syncs WHERE simulation_id = "
            f"'{simulation_id}'"
        )
        or 0
    )
    require(history >= 2, f"旧记忆任务历史未保留：{history}")
    counts = json.loads(after)
    record_temporal_memory_calls(report, run_id=run_id)
    record_temporal_memory_evidence(
        report,
        "temporal-memory-recovery",
        runId=run_id,
        memoryId=memory_id,
        addCountBefore=int(add_count),
        addCountAfter=int(after_add),
        episodeUuid=after_uuid,
        episodeCounts=counts,
        duplicateGrowth=0,
        retryReusedJob=first_body["memoryJobId"] == second_body["memoryJobId"],  # type: ignore[index]
        retryNewAfterTerminal=True,
        syncHistory=history,
        reconciled=True,
        unconfirmedVisible=True,
        newKeyCreated=False,
    )
    entry["detail"] = (
        f"已受理未确认 episode 查账补记：add_count {add_count}→{after_add}，"
        f"episode {counts['episodes']} 条、节点/边/来源不增长；"
        f"活动重试复用任务，终态后新建任务（历史 {history} 条）"
    )


def scenario_temporal_memory_boundaries(
    entry: dict[str, Any], state: dict[str, Any], report: dict[str, Any]
) -> None:
    token, project_id = state["token_a"], state["project_id"]
    run_id = state["temporal_run"]
    anonymous, _, _ = raw_request(
        "GET", simulation_path(project_id, run_id, "/memory?round=2")
    )
    require(anonymous == 401, "匿名记忆读取应为 401")
    client_b = HttpClient()
    login_status, _, _ = client_b.request(
        "POST",
        "/api/auth/login",
        payload={"email": ACCOUNT_B, "password": state["password"]},
        headers={"Origin": BASE_URL},
    )
    require(login_status == 200, f"B 账号登录失败：{login_status}")
    other_token = client_b.cookie_value()
    other_status, _, other_body = api_call(
        "GET", simulation_path(project_id, run_id, "/memory?round=2"), other_token
    )
    require(other_status == 404, f"他人项目记忆期望 404，实际 {other_status}")
    require(
        isinstance(other_body, dict) and other_body.get("detail") == "项目不存在",
        f"他人项目记忆响应异常：{other_body}",
    )
    missing_status, _, missing_body = api_call(
        "GET",
        simulation_path(project_id, str(uuid.uuid4()), "/memory?round=2"),
        token,
    )
    require(missing_status == 404, f"不存在运行记忆期望 404，实际 {missing_status}")
    require(
        isinstance(missing_body, dict) and missing_body.get("detail") == "运行不存在",
        f"不存在运行记忆响应异常：{missing_body}",
    )
    invalid_status, _, invalid_body = api_call(
        "GET", simulation_path(project_id, run_id, "/memory?round=0"), token
    )
    require(invalid_status == 400, f"非法轮次期望 400，实际 {invalid_status}")
    require(
        isinstance(invalid_body, dict) and invalid_body.get("code") == "round_invalid",
        f"非法轮次原因码异常：{invalid_body}",
    )
    platform_status, _, platform_body = api_call(
        "GET",
        simulation_path(project_id, run_id, "/memory?round=1&platform=twitter"),
        token,
    )
    require(platform_status == 400, f"非法平台期望 400，实际 {platform_status}")
    require(
        isinstance(platform_body, dict) and platform_body.get("code") == "platform_invalid",
        f"非法平台原因码异常：{platform_body}",
    )
    record_temporal_memory_evidence(
        report,
        "temporal-memory-boundaries",
        anonymous=anonymous,
        otherProject=other_status,
        unknownRun=missing_status,
        invalidRound=invalid_status,
        invalidPlatform=platform_status,
    )
    entry["detail"] = "匿名 401、他人项目 404、未知运行 404、非法轮次/平台 400"


def scenario_temporal_memory_worker_restored(
    entry: dict[str, Any], report: dict[str, Any]
) -> None:
    recreate_simulation_worker(
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_KEEP_RUNTIME="false",
        SIMULATION_ENGINE="double",
        WORKER_SIMULATION_ENGINE="",
        SIMULATION_MEMORY_PROVIDER="auto",
        EVIDENCE_GRAPH_ALLOW_STUB="false",
    )
    result = compose("ps", "--format", "{{.Health}}", "worker")
    require(result.stdout.strip() == "healthy", "worker 未恢复健康")
    provider = compose(
        "exec", "-T", "worker", "python", "-c",
        "import os;print(os.environ.get('SIMULATION_MEMORY_PROVIDER',''))",
    )
    require(provider.stdout.strip() == "auto", "worker 记忆 provider 未恢复")
    evidence = report.setdefault("temporal_memory_evidence", {})
    evidence["workerRestored"] = {
        "health": "healthy",
        "memoryProvider": provider.stdout.strip(),
    }
    entry["detail"] = "worker 恢复为默认记忆配置（auto，零真实外呼）"


def scenario_simulation_worker_restored(
    entry: dict[str, Any], report: dict[str, Any]
) -> None:
    # 显式重建为默认配置：失败或取消的运行不能把故障注入留在 worker 里。
    recreate_simulation_worker(
        SIMULATION_FAULT="",
        DUET_CHILD_SLEEP_MS="0",
        SIMULATION_ROUND_DELAY_MS="0",
        SIMULATION_KEEP_RUNTIME="false",
        SIMULATION_ENGINE="double",
        WORKER_SIMULATION_ENGINE="",
    )
    result = compose("ps", "--format", "{{.Health}}", "worker")
    require(result.stdout.strip() == "healthy", "worker 未恢复健康")
    engine = compose(
        "exec", "-T", "worker", "python", "-c",
        "import os;print(os.environ.get('SIMULATION_ENGINE',''))",
    )
    require(engine.stdout.strip() == "double", "worker 引擎模式未恢复")
    evidence = report.setdefault("simulation_evidence", {})
    fault = compose(
        "exec", "-T", "worker", "python", "-c",
        "import os;print(os.environ.get('SIMULATION_FAULT',''))",
    )
    evidence["workerRestored"] = {
        "health": "healthy",
        "engine": engine.stdout.strip(),
        "fault": fault.stdout.strip(),
    }
    entry["detail"] = "worker 恢复为默认替身引擎，运行故障注入已清除"


def run_acceptance(report: dict[str, Any], change: str | None = None) -> bool:
    state: dict[str, Any] = {}
    fixtures = DocumentFixtures()
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
        with scenario(report, "acceptance-accounts") as entry:
            scenario_prepare_accounts(entry, state, report)
        with scenario(report, "login-and-create-project") as entry:
            scenario_login_and_create(entry, state, report)
        with scenario(report, "shell-routes") as entry:
            scenario_shell_routes(entry, state)
        with scenario(report, "session-cookie-replay") as entry:
            scenario_session_cookie_replay(entry, state)
        with scenario(report, "job-start-202") as entry:
            scenario_job_start(entry, state, report)
        with scenario(report, "job-terminal-success") as entry:
            scenario_job_terminal_success(entry, state, report)
        with scenario(report, "job-access-boundaries") as entry:
            scenario_job_access_boundaries(entry, state, report)
        with scenario(report, "worker-isolation") as entry:
            scenario_worker_isolation(entry, report)
        with scenario(report, "database-stop-degrades") as entry:
            scenario_db_degrade(entry, report)
        with scenario(report, "auth-anonymous-during-outage") as entry:
            scenario_auth_anonymous_outage(entry)
        with scenario(report, "auth-cookie-during-outage") as entry:
            scenario_auth_cookie_outage(entry, state)
        with scenario(report, "job-outage-boundary") as entry:
            scenario_job_outage_boundary(entry, state)
        with scenario(report, "database-recover-ready") as entry:
            scenario_db_recover(entry)
        with scenario(report, "session-after-recovery") as entry:
            scenario_session_after_recovery(entry, state)
        with scenario(report, "logout-invalidates-session") as entry:
            scenario_logout_invalidates(entry, state)
        with scenario(report, "user-b-isolation") as entry:
            scenario_user_b_isolation(entry, state)
        with scenario(report, "relogin-keeps-project") as entry:
            scenario_relogin_keeps_project(entry, state)
        with scenario(report, "documents-smoke") as entry:
            scenario_documents_smoke(entry, state, report, fixtures)
        with scenario(report, "container-identity-stable") as entry:
            scenario_identity_stable(entry, report)
        with scenario(report, "homepage-after-recovery") as entry:
            scenario_homepage_after_recovery(entry)
        if change is None:
            with scenario(report, "graph-not-triggered") as entry:
                scenario_graph_not_triggered(entry, report)
        if change == CHANGE_ID:
            with scenario(report, "job-two-jobs-isolation") as entry:
                scenario_job_two_jobs(entry, state, report)
            with scenario(report, "job-duplicate-start-concurrent") as entry:
                scenario_job_duplicate_start(entry, state, report)
            with scenario(report, "job-resume-after-disconnect") as entry:
                scenario_job_resume(entry, state, report)
            with scenario(report, "job-last-event-id-priority") as entry:
                scenario_job_cursor_precedence(entry, state, report)
            with scenario(report, "job-terminal-reconnect") as entry:
                scenario_job_terminal_reconnect(entry, state, report)
            with scenario(report, "job-worker-crash-takeover") as entry:
                scenario_job_worker_takeover(entry, state, report)
            with scenario(report, "job-lease-probe-takeover") as entry:
                scenario_job_lease_probe(entry, state, report)
            with scenario(report, "job-lease-lock-wait") as entry:
                scenario_job_lease_lock_wait(entry, state, report)
            with scenario(report, "job-cancel-running") as entry:
                scenario_job_cancel(entry, state, report)
            with scenario(report, "job-fail-controlled") as entry:
                scenario_job_fail(entry, state, report)
            with scenario(report, "job-restart-after-terminal") as entry:
                scenario_job_restart_after_terminal(entry, state, report)
            with scenario(report, "job-cancel-final-race") as entry:
                scenario_job_cancel_race(entry, state, report)
        if change == ENGINE_CHANGE_ID:
            with scenario(report, "engine-offline-contract") as entry:
                scenario_engine_offline(entry, report)
        if change == GRAPH_CHANGE_ID:
            with scenario(report, "graph-basic") as entry:
                scenario_graph_basic(entry, state, report, fixtures)
            with scenario(report, "graph-complete") as entry:
                scenario_graph_complete(entry, state, report)
            with scenario(report, "graph-bind-windows") as entry:
                scenario_graph_bind_probe(entry, state, report)
            with scenario(report, "graph-failure-codes") as entry:
                scenario_graph_failure_probe(entry, state, report)
            with scenario(report, "graph-publish-race") as entry:
                scenario_graph_publish_probe(entry, state, report)
            with scenario(report, "graph-second-build") as entry:
                scenario_graph_second_build(entry, state, report)
            with scenario(report, "graph-redaction") as entry:
                scenario_graph_redaction(entry, report, fixtures)
        if change is None:
            with scenario(report, "environment-not-triggered") as entry:
                scenario_environment_not_triggered(entry, state, report)
        if change is None:
            with scenario(report, "simulation-not-triggered") as entry:
                scenario_simulation_not_triggered(entry, state, report)
        if change is None:
            with scenario(report, "temporal-memory-not-triggered") as entry:
                scenario_temporal_memory_not_triggered(entry, state, report)
        if change == TEMPORAL_MEMORY_CHANGE_ID:
            with scenario(report, "temporal-memory-full-chain") as entry:
                scenario_temporal_memory_full_chain(entry, state, report, fixtures)
            with scenario(report, "temporal-memory-isolation") as entry:
                scenario_temporal_memory_isolation(entry, state, report)
            with scenario(report, "temporal-memory-projection-pause") as entry:
                scenario_temporal_memory_projection_pause(entry, state, report)
            with scenario(report, "temporal-memory-takeover") as entry:
                scenario_temporal_memory_takeover(entry, state, report)
            with scenario(report, "temporal-memory-recovery") as entry:
                scenario_temporal_memory_recovery(entry, state, report)
            with scenario(report, "temporal-memory-boundaries") as entry:
                scenario_temporal_memory_boundaries(entry, state, report)
        if change == SIMULATION_CHANGE_ID:
            with scenario(report, "simulation-full-chain") as entry:
                scenario_simulation_full_chain(entry, state, report, fixtures)
            with scenario(report, "simulation-stop-and-rerun") as entry:
                scenario_simulation_stop_and_rerun(entry, state, report)
            with scenario(report, "simulation-takeover") as entry:
                scenario_simulation_takeover(entry, state, report)
            with scenario(report, "simulation-partial-failure") as entry:
                scenario_simulation_partial_failure(entry, state, report)
            with scenario(report, "simulation-boundaries") as entry:
                scenario_simulation_boundaries(entry, state, report)
        if change == ENVIRONMENT_CHANGE_ID:
            with scenario(report, "environment-full-chain") as entry:
                scenario_environment_full_chain(entry, state, report, fixtures)
            with scenario(report, "environment-invalid-config") as entry:
                scenario_environment_invalid_config(entry, state, report)
            with scenario(report, "environment-rounds-and-freeze") as entry:
                scenario_environment_rounds_and_freeze(entry, state, report)
            with scenario(report, "environment-batches-and-takeover") as entry:
                scenario_environment_batches_and_takeover(
                    entry, state, report, fixtures
                )
            with scenario(report, "environment-no-roles-and-model-unavailable") as entry:
                scenario_environment_no_roles_and_unavailable(
                    entry, state, report, fixtures
                )
            with scenario(report, "environment-access-boundaries") as entry:
                scenario_environment_access_boundaries(entry, state, report)
            with scenario(report, "environment-change-material") as entry:
                scenario_environment_change_material(entry, state, report, fixtures)
        if change == DOCUMENT_CHANGE_ID:
            with scenario(report, "document-pdf-pages") as entry:
                scenario_document_pdf_pages(entry, state, report, fixtures)
            with scenario(report, "document-pdf-cleaning") as entry:
                scenario_document_pdf_cleaning(entry, state, report, fixtures)
            with scenario(report, "document-text") as entry:
                scenario_document_text(entry, state, report, fixtures)
            with scenario(report, "document-boundaries") as entry:
                scenario_document_boundaries(entry, state, report, fixtures)
            with scenario(report, "document-page-limit") as entry:
                scenario_document_page_limit(entry, state, report, fixtures)
            with scenario(report, "document-failure-history") as entry:
                scenario_document_failure_history(entry, state, report, fixtures)
            with scenario(report, "document-dedup-isolation") as entry:
                scenario_document_dedup_isolation(entry, state, report, fixtures)
            with scenario(report, "document-cancel") as entry:
                scenario_document_cancel(entry, state, report, fixtures)
            with scenario(report, "document-slow-parse") as entry:
                scenario_document_slow_parse(entry, state, report, fixtures)
    except ScenarioFailed:
        return False
    return True


def restore_database() -> None:
    """尽力把数据库恢复到运行状态，保证下一次验收可执行。

    恢复失败不能阻断报告写入，因此这里吞掉超时与命令异常。
    """
    try:
        result = compose("start", "db")
    except subprocess.TimeoutExpired:
        print("[verify] 恢复数据库：超时")
        return
    except Exception as exc:  # noqa: BLE001 - 恢复失败只提示，不阻断报告
        print(f"[verify] 恢复数据库：异常 {type(exc).__name__}")
        return
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
    label = os.environ.get("VERIFY_REPORT_LABEL", "").strip()
    if label:
        safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label)[:80]
        atomic_write(REPORT_DIR / f"run-{safe_label}.json", report)


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
        "--change",
        default=os.environ.get("CHANGE") or None,
        help="变更 ID；指定后追加该变更的专属验收场景（默认读取 CHANGE）",
    )
    parser.add_argument(
        "--expect-sha",
        default=os.environ.get("EXPECTED_SHA") or None,
        help="期望的被测 Git SHA；与 HEAD 不一致时验收失败（默认读取 EXPECTED_SHA）",
    )
    return parser.parse_args(argv)


def new_report(expected_sha: str | None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "result": "failed",
        "tested_sha": "",
        "dirty": False,
        "expected_sha": expected_sha,
        "change": None,
        "job_evidence": [],
        "document_evidence": [],
        "engine_evidence": None,
        "graph_evidence": None,
        "environment_evidence": None,
        "started_at": utc_now(),
        "finished_at": None,
        "exit_code": None,
        "compose_project": None,
        "base_url": None,
        "app": None,
        "degrade_seconds": None,
        "acceptance_accounts": [],
        "isolation_project": None,
        "scenarios": [],
        "temporal_memory_evidence": None,
    }


def run(report: dict[str, Any], args: argparse.Namespace) -> int:
    global COMPOSE_PROJECT
    try:
        COMPOSE_PROJECT = isolated_compose_project(
            os.environ.get("VERIFY_COMPOSE_PROJECT")
        )
    except VerifyIsolationError as exc:
        print(f"[verify] {exc}")
        report["scenarios"].append(
            {
                "id": "verify-isolation",
                "status": "failed",
                "duration_ms": 0,
                "detail": str(exc),
            }
        )
        return finalize(report, 2)
    allow_dirty = os.environ.get("ALLOW_DIRTY", "").strip().lower() in {"1", "true", "yes"}

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

    change = args.change or None
    report["change"] = change
    if change is not None and change not in KNOWN_CHANGES:
        message = f"未注册的变更：{change}（可用：{', '.join(sorted(KNOWN_CHANGES))}）"
        print(f"[verify] {message}")
        report["scenarios"].append(
            {
                "id": "change-known",
                "status": "failed",
                "duration_ms": 0,
                "detail": message,
            }
        )
        return finalize(report, 2)

    try:
        verify_lock = acquire_verify_lock(COMPOSE_PROJECT)
    except VerifyIsolationError as exc:
        print(f"[verify] {exc}")
        report["scenarios"].append(
            {
                "id": "verify-isolation",
                "status": "failed",
                "duration_ms": 0,
                "detail": str(exc),
            }
        )
        return finalize(report, 2)
    report["scenarios"].append(
        {
            "id": "verify-isolation",
            "status": "passed",
            "duration_ms": 0,
            "detail": f"隔离项目 {COMPOSE_PROJECT} 已通过命名校验并获取独占锁",
        }
    )

    isolated_saved = apply_runtime_env(isolated_runtime_env())
    graph_saved: dict[str, str | None] | None = None
    environment_saved: dict[str, str | None] | None = None
    simulation_saved: dict[str, str | None] | None = None
    temporal_saved: dict[str, str | None] | None = None
    if change == GRAPH_CHANGE_ID:
        graph_saved = apply_runtime_env(graph_runtime_env())
    if change == ENVIRONMENT_CHANGE_ID:
        environment_saved = apply_runtime_env(environment_runtime_env())
    if change == SIMULATION_CHANGE_ID:
        simulation_saved = apply_runtime_env(simulation_runtime_env())
    if change == TEMPORAL_MEMORY_CHANGE_ID:
        temporal_saved = apply_runtime_env(temporal_memory_runtime_env())
    if change == CHANGE_ID:
        # 放大步骤间隔，让“读到第 4 条后断开”、worker 中断等窗口足够宽。
        os.environ["TEST_TASK_STEP_DELAY_MS"] = VERIFY_STEP_DELAY_MS

    passed = False
    try:
        try:
            passed = run_acceptance(report, change)
        finally:
            restore_database()
            if graph_saved is not None:
                restore_runtime_env(graph_saved)
            if environment_saved is not None:
                restore_runtime_env(environment_saved)
            if simulation_saved is not None:
                restore_runtime_env(simulation_saved)
            if temporal_saved is not None:
                restore_runtime_env(temporal_saved)
            restore_worker_defaults()
        if change == GRAPH_CHANGE_ID and passed:
            try:
                with scenario(report, "graph-worker-env-restored") as entry:
                    scenario_graph_worker_restored(entry, report)
            except ScenarioFailed:
                passed = False
        if change == ENVIRONMENT_CHANGE_ID and passed:
            try:
                with scenario(report, "environment-worker-env-restored") as entry:
                    scenario_environment_worker_restored(entry, report)
            except ScenarioFailed:
                passed = False
        if change == SIMULATION_CHANGE_ID and passed:
            try:
                with scenario(report, "simulation-worker-env-restored") as entry:
                    scenario_simulation_worker_restored(entry, report)
            except ScenarioFailed:
                passed = False
        if change == TEMPORAL_MEMORY_CHANGE_ID and passed:
            try:
                with scenario(report, "temporal-memory-worker-restored") as entry:
                    scenario_temporal_memory_worker_restored(entry, report)
            except ScenarioFailed:
                passed = False
    finally:
        restore_runtime_env(isolated_saved)
        release_verify_lock(verify_lock)

    return finalize(report, 0 if passed else 1)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = new_report(args.expect_sha)

    try:
        return run(report, args)
    except Exception as exc:  # noqa: BLE001 - 兜底：未预期异常也必须留下可下载报告
        if report.get("finished_at") is not None:
            raise
        message = f"未预期异常：{type(exc).__name__}: {exc}"
        print(f"[verify] {message}")
        report["scenarios"].append(
            {
                "id": "verify-crash",
                "status": "failed",
                "duration_ms": 0,
                "detail": message,
            }
        )
        return finalize(report, 2)


if __name__ == "__main__":
    sys.exit(main())
