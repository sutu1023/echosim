"""只读统计隔离运行目录中的原生动作数（验收三方核对用）。

用法：在 worker 容器内执行 `python - <simulationId>`，只读取 SQLite，
不写数据库、不访问网络。输出为 {platformKey: {nativeAction: count}}。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

QUERIES = {
    "create_post": "SELECT COUNT(*) FROM post WHERE original_post_id IS NULL",
    "repost": "SELECT COUNT(*) FROM post WHERE original_post_id IS NOT NULL",
    "create_comment": "SELECT COUNT(*) FROM comment",
    "like_post": "SELECT COUNT(*) FROM like",
    "dislike_post": "SELECT COUNT(*) FROM dislike",
}


def main() -> int:
    simulation_id = sys.argv[1]
    root = Path(os.environ.get("SIMULATION_DATA_DIR", "/app/data/simulations")) / simulation_id
    counts: dict[str, dict[str, int]] = {}
    for platform in ("platform_a", "platform_b"):
        db_path = root / platform / "social.db"
        if not db_path.is_file():
            counts[platform] = {}
            continue
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            result: dict[str, int] = {}
            for action, sql in QUERIES.items():
                result[action] = int(db.execute(sql).fetchone()[0])
            counts[platform] = result
        finally:
            db.close()
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
