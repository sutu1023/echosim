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
import http.cookiejar
import json
import os
import hashlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
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
    return command(["docker", "compose", *args], input_text=input_text)


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
KNOWN_CHANGES = {CHANGE_ID, DOCUMENT_CHANGE_ID}
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
        record_document_evidence(
            report,
            "document-page-limit",
            exact_document_id=exact["documentId"],
            over_document_id=over["documentId"],
            failure_code=failure.get("code"),
            failure_message=failure.get("message"),
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
    }


def run(report: dict[str, Any], args: argparse.Namespace) -> int:
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

    if change == CHANGE_ID:
        # 放大步骤间隔，让“读到第 4 条后断开”、worker 中断等窗口足够宽。
        os.environ["TEST_TASK_STEP_DELAY_MS"] = VERIFY_STEP_DELAY_MS

    try:
        passed = run_acceptance(report, change)
    finally:
        restore_database()
        restore_worker_defaults()

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
