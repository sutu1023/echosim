#!/usr/bin/env python3
"""EchoSim 可重复验收脚本（仅依赖 Python 标准库）。

执行流程：前置检查（Git SHA、脏工作树、Docker Compose）→ 启动环境 → 验证首页与
`/health` → 停止数据库观察 503 → 恢复数据库观察 200 → 校验后端容器身份不变 →
原子写入 `runtime/verify/last-run.json` 与带时间戳的历史副本。

退出码：0 通过；1 验收失败；2 前置条件不满足。
"""

from __future__ import annotations

import argparse
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
    timeout: float = 10.0,
) -> tuple[int, dict[str, str], str]:
    """不依赖 Cookie 罐的请求，用于复放旧 Cookie 与显式注入 Cookie。"""
    cookie_header = (
        f"{SESSION_COOKIE}={cookie_token}" if cookie_token is not None else None
    )
    return HttpClient(cookie_header=cookie_header).request(
        method, path, payload=payload, timeout=timeout
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
    status, _, payload = client.get_json(f"/api/projects/{state['project_id']}")
    require(status == 200, f"A 重新登录后详情期望 200，实际 {status}")
    require(
        payload["project"]["name"] == state["project_name"],  # type: ignore[index]
        "重新登录后打开的项目名称不一致",
    )
    entry["detail"] = "A 重新登录后仍能打开退出前创建的原项目"


def run_acceptance(report: dict[str, Any]) -> bool:
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
        with scenario(report, "session-cookie-replay") as entry:
            scenario_session_cookie_replay(entry, state)
        with scenario(report, "database-stop-degrades") as entry:
            scenario_db_degrade(entry, report)
        with scenario(report, "auth-anonymous-during-outage") as entry:
            scenario_auth_anonymous_outage(entry)
        with scenario(report, "auth-cookie-during-outage") as entry:
            scenario_auth_cookie_outage(entry, state)
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

    try:
        passed = run_acceptance(report)
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
