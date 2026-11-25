"""worker-simulation 镜像的四阶段基线：解析 → 安装 → 导入 → 最小运行。

在镜像内执行，输出单行 JSON。只使用镜像内的依赖与一个临时 SQLite，
不访问网络、不读取项目材料，也不连接 PostgreSQL。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from importlib import metadata
from pathlib import Path


def phase(name: str):
    def decorator(func):
        def wrapper(result: dict) -> None:
            entry: dict[str, object] = {"status": "passed"}
            try:
                entry["detail"] = func()
            except Exception as exc:  # noqa: BLE001 - 失败必须记录阶段与类型
                entry["status"] = "failed"
                entry["reason"] = type(exc).__name__
                entry["detail"] = str(exc)[:200]
                result["result"] = "failed"
            result["phases"][name] = entry

        return wrapper

    return decorator


@phase("resolve")
def resolve() -> dict:
    requirements = Path("/app/requirements.txt")
    pins = {}
    if requirements.is_file():
        for line in requirements.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([a-zA-Z0-9_.-]+)==([^\s#]+)", line.strip())
            if match:
                pins[match.group(1).lower()] = match.group(2)
    return {
        "requirements": str(requirements),
        "pins": pins,
        "engineRequirements": metadata.version("camel-oasis"),
    }


@phase("install")
def install() -> dict:
    versions = {
        name: metadata.version(name)
        for name in (
            "camel-oasis",
            "camel-ai",
            "pydantic",
            "sqlalchemy",
            "asyncpg",
            "pydantic-settings",
        )
    }
    return {"versions": versions}


@phase("import")
def importer() -> dict:
    workdir = tempfile.mkdtemp(prefix="simulation-worker-import-")
    os.chdir(workdir)
    import oasis  # noqa: F401
    from oasis.social_platform.platform import Platform  # noqa: F401
    from app.simulation_worker import run  # noqa: F401
    from app.services import simulation_worker  # noqa: F401

    return {"workdir": workdir}


@phase("minimal_run")
def minimal_run() -> dict:
    import asyncio

    workdir = tempfile.mkdtemp(prefix="simulation-worker-run-")
    os.chdir(workdir)
    from oasis.social_platform.platform import Platform

    db_path = Path(workdir) / "social.db"

    async def execute() -> dict:
        platform = Platform(db_path=str(db_path), recsys_type="twitter")
        try:
            signup = await platform.sign_up(1, ("基线角色", "baseline", "基线"))
            post = await platform.create_post(1, "worker-simulation 最小运行 [[baseline]]")
            cursor = platform.db_cursor
            row = cursor.execute(
                "SELECT post_id, content FROM post ORDER BY post_id LIMIT 1"
            ).fetchone()
        finally:
            platform.db_cursor.close()
            platform.db.close()
        if not signup.get("success") or not post.get("success") or row is None:
            raise RuntimeError("最小运行未写入帖子")
        return {"postId": int(row[0]), "content": str(row[1])[:60]}

    return asyncio.run(execute())


def main() -> int:
    result: dict[str, object] = {
        "schema_version": 1,
        "result": "passed",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "machine": os.uname().machine,
        "phases": {},
    }
    resolve(result)
    install(result)
    importer(result)
    if result["result"] == "passed":
        minimal_run(result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
