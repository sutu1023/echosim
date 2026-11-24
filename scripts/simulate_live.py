"""真实本地 OASIS 双平台运行：开发者显式执行，复用工作台的普通启动链路。

前置：
- 一个已启动的 EchoSim 栈（`make up`），其建图/画像使用离线实现，避免真实外呼；
- 账号凭据 `ECHOSIM_LIVE_EMAIL` / `ECHOSIM_LIVE_PASSWORD`；
- 隔离引擎镜像与命令 `ECHOSIM_SIMULATION_ENGINE_COMMAND`（例如
  `docker run --rm --user {uid}:{gid} -w /out -v {runtime}:/out ... echosim-engine:dev
  python -m app.engines.duet_runner --request /out/{request_name}`）。

脚本会启动一个只领取 `simulation.run` 的本机 worker，用引擎镜像执行两个平台的
原生动作，读回引擎记录并把脱敏证据写入 `runtime/simulation-live/report.json`。
默认拒绝脏工作树（`ALLOW_DIRTY=1` 可显式允许，但报告会记录 `dirty=true`）。
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO_ROOT / "runtime" / "simulation-live"
REPORT_PATH = REPORT_DIR / "report.json"
RUN_ROOT = REPORT_DIR / "runs"

RESULT_QUESTION = "原创问题：雨天是否调整双平台的接驳班次？"
RESULT_TEXT = "\n".join(
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


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result.stdout.strip()


def wait_for(fn, *, timeout: float, interval: float = 0.4, label: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    raise SystemExit(f"等待 {label} 超时")


def main() -> int:
    dirty = bool(git("status", "--porcelain"))
    if dirty and os.environ.get("ALLOW_DIRTY") != "1":
        print("工作树不干净：请先提交，或用 ALLOW_DIRTY=1 显式允许。")
        return 2
    email = os.environ.get("ECHOSIM_LIVE_EMAIL")
    password = os.environ.get("ECHOSIM_LIVE_PASSWORD")
    mode = os.environ.get("ECHOSIM_LIVE_MODE", "command").strip() or "command"
    command = os.environ.get("ECHOSIM_SIMULATION_ENGINE_COMMAND", "")
    if not (email and password):
        print("缺少 ECHOSIM_LIVE_EMAIL / ECHOSIM_LIVE_PASSWORD")
        return 2
    if mode == "command" and not command:
        print(
            "command 模式需要 ECHOSIM_SIMULATION_ENGINE_COMMAND；"
            "使用专用 worker 时设置 ECHOSIM_LIVE_MODE=worker"
        )
        return 2
    command = command.replace("{uid}", str(os.getuid())).replace(
        "{gid}", str(os.getgid())
    )
    base_url = os.environ.get("ECHOSIM_LIVE_BASE_URL", "http://127.0.0.1:8080")
    client = httpx.Client(base_url=base_url, timeout=60.0)

    status = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    if status.status_code != 200:
        print(f"登录失败：{status.status_code}")
        return 1
    project = client.post(
        "/api/projects",
        json={"name": f"OASIS 双平台运行 {datetime.now(timezone.utc):%Y%m%d-%H%M%S}"},
    ).json()["project"]["id"]

    upload = client.post(
        f"/api/projects/{project}/documents",
        files={"file": ("原创材料.txt", RESULT_TEXT.encode("utf-8"), "text/plain")},
    )
    document = upload.json()["document"]["documentId"]
    wait_for(
        lambda: client.get(
            f"/api/projects/{project}/documents/{document}"
        ).json()["document"]["status"]
        == "ready",
        timeout=120,
        label="材料解析",
    )
    build = client.post(
        f"/api/projects/{project}/evidence-graph/builds",
        json={"documentIds": [document], "question": RESULT_QUESTION},
    ).json()["build"]["buildId"]
    def graph_ready():
        body = client.get(
            f"/api/projects/{project}/evidence-graph?build={build}"
        ).json()["build"]
        if body["phase"] in ("failed", "canceled"):
            raise SystemExit(f"建图未完成：{body['phase']} {body.get('failure')}")
        return body["canContinue"]

    wait_for(graph_ready, timeout=240, label="建图")
    preparation = client.post(
        f"/api/projects/{project}/environment/preparations?build={build}"
    ).json()["preparation"]["preparationId"]

    def ready_preparation():
        data = client.get(
            f"/api/projects/{project}/environment/preparations/{preparation}"
        ).json()["preparation"]
        if data["phase"] in ("failed", "canceled"):
            raise SystemExit(
                f"角色准备未完成：{data['phase']} {data.get('failure')}"
            )
        return data if data["phase"] == "ready" else None

    preparation_body = wait_for(ready_preparation, timeout=240, label="角色准备")
    revision = preparation_body["settings"]["settingsRevision"]
    configured = client.patch(
        f"/api/projects/{project}/environment/preparations/{preparation}/configuration",
        json={
            "settingsRevision": revision,
            "durationHours": 3,
            "minutesPerRound": 60,
            "roundsMode": "manual",
            "manualRounds": 3,
        },
    ).json()["preparation"]
    frozen = client.post(
        f"/api/projects/{project}/environment/preparations/{preparation}/confirm",
        json={"settingsRevision": configured["settings"]["settingsRevision"]},
    ).json()["frozenSelection"]

    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    runtime_root = RUN_ROOT / secrets.token_hex(6)
    worker_env = {
        **os.environ,
        "SIMULATION_ENGINE": "oasis",
        "SIMULATION_ALLOW_OASIS": "true",
        "SIMULATION_ENGINE_COMMAND": command,
        "SIMULATION_KEEP_RUNTIME": "true",
        "SIMULATION_DATA_DIR": str(runtime_root),
        "WORKER_SIMULATION_ENGINE": "oasis",
        "WORKER_RUN_KINDS": "simulation.run",
        "SYNC_WORKER": "1",
    }
    worker = None
    if mode == "command":
        worker = subprocess.Popen(
            [sys.executable, "-m", "app.worker"],
            cwd=str(REPO_ROOT),
            env=worker_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    try:
        started = client.post(
            f"/api/projects/{project}/simulations",
            json={"selectionId": frozen["selectionId"], "engineMode": "oasis"},
        )
        if started.status_code != 202:
            print(f"开始运行失败：{started.status_code} {started.text}")
            return 1
        run_id = started.json()["simulation"]["runId"]

        def terminal():
            data = client.get(
                f"/api/projects/{project}/simulations/{run_id}"
            ).json()["simulation"]
            return data if data["terminal"] else None

        detail = wait_for(terminal, timeout=600, label="双平台运行")
    finally:
        if worker is not None:
            worker.terminate()
            try:
                worker.wait(timeout=20)
            except subprocess.TimeoutExpired:
                worker.kill()

    platforms = {item["platformKey"]: item for item in detail["platforms"]}
    simulation_id = str(detail["simulationId"])
    engine_counts: dict[str, dict[str, int]] = {}
    if mode == "worker":
        engine_counts = fetch_engine_counts_in_worker(simulation_id)
    else:
        engine_counts = read_engine_counts_locally(runtime_root, simulation_id)
    errors = validate_run(detail, frozen, engine_counts)
    report = {
        "schema_version": 1,
        "mode": "live",
        "engine": "oasis",
        "result": "passed" if not errors else "failed",
        "tested_sha": git("rev-parse", "HEAD"),
        "dirty": dirty,
        "base_url": base_url,
        "workerMode": mode,
        "runId": run_id,
        "simulationId": simulation_id,
        "projectId": project,
        "selectionId": frozen["selectionId"],
        "buildId": frozen["buildId"],
        "snapshotId": frozen["snapshotId"],
        "roles": len(frozen["roles"]),
        "rounds": 3,
        "phase": detail["phase"],
        "platforms": {
            key: {
                "nativePlatform": value["nativePlatform"],
                "roundsCompleted": value["roundsCompleted"],
                "actionCount": value["actionCount"],
                "byAction": value["byAction"],
                "failure": value["failure"],
            }
            for key, value in platforms.items()
        },
        "engineCounts": engine_counts,
        "counts": detail["counts"],
        "calls": {
            "localOasisActions": detail["counts"]["total"],
            "externalModelCalls": 0,
        },
        "checks": errors,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "result": report["result"],
                "report": str(REPORT_PATH),
                "errors": errors,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["result"] == "passed" else 1


def read_engine_counts_locally(
    runtime_root: Path, simulation_id: str
) -> dict[str, dict[str, int]]:
    root = runtime_root / simulation_id
    if not root.is_dir():
        return {}
    counts: dict[str, dict[str, int]] = {}
    for platform in ("platform_a", "platform_b"):
        db_path = root / platform / "social.db"
        if not db_path.is_file():
            continue
        import sqlite3

        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            counts[platform] = {
                "create_post": db.execute(
                    "SELECT COUNT(*) FROM post WHERE original_post_id IS NULL"
                ).fetchone()[0],
                "repost": db.execute(
                    "SELECT COUNT(*) FROM post WHERE original_post_id IS NOT NULL"
                ).fetchone()[0],
                "create_comment": db.execute(
                    "SELECT COUNT(*) FROM comment"
                ).fetchone()[0],
                "like_post": db.execute("SELECT COUNT(*) FROM like").fetchone()[0],
                "dislike_post": db.execute(
                    "SELECT COUNT(*) FROM dislike"
                ).fetchone()[0],
            }
        finally:
            db.close()
    return counts


def fetch_engine_counts_in_worker(
    simulation_id: str,
) -> dict[str, dict[str, int]]:
    script = (REPO_ROOT / "scripts" / "engine_counts.py").read_text(encoding="utf-8")
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "worker-simulation", "python", "-", simulation_id],
        cwd=str(REPO_ROOT),
        input=script,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {}


def compare_platform_counts(
    engine_counts: dict[str, dict[str, int]],
    platforms: dict[str, dict[str, object]],
) -> list[str]:
    errors: list[str] = []
    for platform_key in ("platform_a", "platform_b"):
        native = {
            action: int(count)
            for action, count in (engine_counts.get(platform_key) or {}).items()
            if int(count) > 0
        }
        server = {
            action: int(count)
            for action, count in (
                (platforms.get(platform_key) or {}).get("byAction") or {}
            ).items()
            if int(count) > 0
        }
        if native != server:
            errors.append(
                f"{platform_key} 引擎计数 {native} 与服务端计数 {server} 不一致"
            )
    return errors


def validate_run(
    detail: dict,
    frozen: dict,
    engine_counts: dict[str, dict[str, int]],
) -> list[str]:
    errors: list[str] = []
    if len(frozen.get("roles") or []) != 6:
        errors.append(f"冻结角色数应为 6，实际 {len(frozen.get('roles') or [])}")
    if int(frozen.get("settings", {}).get("rounds", 0)) != 3:
        errors.append("冻结轮数应为 3")
    if detail.get("phase") != "succeeded":
        errors.append(f"运行未成功：{detail.get('phase')}")
    if detail.get("engine") != "oasis":
        errors.append(f"运行引擎应为 oasis，实际 {detail.get('engine')}")
    platforms = {item["platformKey"]: item for item in detail.get("platforms") or []}
    if set(platforms) != {"platform_a", "platform_b"}:
        errors.append("缺少平台记录")
    for platform_key in ("platform_a", "platform_b"):
        platform = platforms.get(platform_key)
        if platform is None:
            continue
        if int(platform.get("roundsCompleted", 0)) != 3:
            errors.append(
                f"{platform_key} 完成轮次应为 3，实际 {platform.get('roundsCompleted')}"
            )
        if int(platform.get("actionCount", 0)) <= 0:
            errors.append(f"{platform_key} 没有原生动作")
        if platform_key not in engine_counts:
            errors.append(f"缺少 {platform_key} 的原生数据库")
    if not errors:
        errors.extend(compare_platform_counts(engine_counts, platforms))
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
