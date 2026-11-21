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
import secrets
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
KNOWN_CHANGES = {CHANGE_ID}
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


def run_acceptance(report: dict[str, Any], change: str | None = None) -> bool:
    state: dict[str, Any] = {}
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
            with scenario(report, "job-cancel-running") as entry:
                scenario_job_cancel(entry, state, report)
            with scenario(report, "job-fail-controlled") as entry:
                scenario_job_fail(entry, state, report)
            with scenario(report, "job-restart-after-terminal") as entry:
                scenario_job_restart_after_terminal(entry, state, report)
            with scenario(report, "job-cancel-final-race") as entry:
                scenario_job_cancel_race(entry, state, report)
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
