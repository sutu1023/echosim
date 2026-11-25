"""双平台引擎实现：确定性替身与真实本地 OASIS 共用同一引擎数据库协议。

两类引擎都写同样的原生表（post/comment/like/dislike/trace/user）与同样的
记账副表（echosim_round/echosim_action）。读回只看引擎数据库，不看动作调用
返回值；这也是“引擎已执行、worker 尚未记账”能被接手补记的前提。
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .duet_contracts import (
    ACTION_CYCLE,
    CONTENT_ACTIONS,
    DuetContractError,
    NATIVE_ACTIONS,
    TARGETED_ACTIONS,
    action_record_id,
    marker_present,
)

NATIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS user (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER,
    user_name TEXT,
    name TEXT,
    bio TEXT,
    created_at DATETIME,
    num_followings INTEGER DEFAULT 0,
    num_followers INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS post (
    post_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    original_post_id INTEGER,
    content TEXT DEFAULT '',
    quote_content TEXT,
    created_at DATETIME,
    num_likes INTEGER DEFAULT 0,
    num_dislikes INTEGER DEFAULT 0,
    num_shares INTEGER DEFAULT 0,
    num_reports INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS comment (
    comment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER,
    user_id INTEGER,
    content TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    num_likes INTEGER DEFAULT 0,
    num_dislikes INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS like (
    like_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    post_id INTEGER,
    created_at DATETIME
);
CREATE TABLE IF NOT EXISTS dislike (
    dislike_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    post_id INTEGER,
    created_at DATETIME
);
CREATE TABLE IF NOT EXISTS trace (
    user_id INTEGER,
    created_at DATETIME,
    action TEXT,
    info TEXT,
    PRIMARY KEY (user_id, created_at, action, info)
);
"""

SIDECAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS echosim_round (
    round_index INTEGER PRIMARY KEY,
    marker TEXT NOT NULL,
    status TEXT NOT NULL,
    action_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    committed_at TEXT
);
CREATE TABLE IF NOT EXISTS echosim_action (
    action_key TEXT PRIMARY KEY,
    round_index INTEGER NOT NULL,
    action_index INTEGER NOT NULL,
    native_action TEXT NOT NULL,
    actor_role_id TEXT NOT NULL,
    actor_agent_id INTEGER NOT NULL,
    actor_name TEXT NOT NULL,
    target_engine_record_id TEXT,
    target_action_key TEXT,
    content_marker TEXT NOT NULL DEFAULT '',
    native_table TEXT,
    native_baseline INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    engine_record_id TEXT,
    engine_sequence INTEGER,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS echosim_action_round_idx
    ON echosim_action (round_index, action_index);
"""

NATIVE_TABLE = {
    "create_post": "post",
    "repost": "post",
    "create_comment": "comment",
    "like_post": "like",
    "dislike_post": "dislike",
}

ADOPT_QUERIES = {
    "create_post": (
        "SELECT rowid, post_id FROM post WHERE rowid > ? AND user_id = ? "
        "AND content LIKE ? ORDER BY rowid LIMIT 1"
    ),
    "repost": (
        "SELECT rowid, post_id FROM post WHERE rowid > ? AND user_id = ? "
        "AND original_post_id = ? ORDER BY rowid LIMIT 1"
    ),
    "create_comment": (
        "SELECT rowid, comment_id FROM comment WHERE rowid > ? AND user_id = ? "
        "AND content LIKE ? ORDER BY rowid LIMIT 1"
    ),
    "like_post": (
        "SELECT rowid, like_id FROM like WHERE rowid > ? AND user_id = ? "
        "AND post_id = ? ORDER BY rowid LIMIT 1"
    ),
    "dislike_post": (
        "SELECT rowid, dislike_id FROM dislike WHERE rowid > ? AND user_id = ? "
        "AND post_id = ? ORDER BY rowid LIMIT 1"
    ),
}

VERIFY_QUERIES = {
    "create_post": "SELECT rowid, post_id, content FROM post WHERE post_id = ?",
    "repost": "SELECT rowid, post_id, original_post_id FROM post WHERE post_id = ?",
    "create_comment": (
        "SELECT rowid, comment_id, post_id, content FROM comment WHERE comment_id = ?"
    ),
    "like_post": "SELECT rowid, like_id, post_id FROM like WHERE like_id = ?",
    "dislike_post": "SELECT rowid, dislike_id, post_id FROM dislike WHERE dislike_id = ?",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execute(db: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
    return db.execute(sql, tuple(params))


def _max_rowid(db: sqlite3.Connection, table: str) -> int:
    row = db.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def _split_record_id(engine_record_id: str) -> int:
    _, _, raw = engine_record_id.partition(":")
    return int(raw)


def _record_native(
    db: sqlite3.Connection,
    *,
    native_action: str,
    agent_id: int,
    content: str,
    target_id: int | None,
    platform_key: str,
    step: int,
) -> tuple[int, int]:
    """执行一次原生动作并返回 (原生记录 id, 原生 rowid)；不回读、不记账。"""
    created = step if platform_key == "platform_a" else datetime.now(timezone.utc).isoformat()
    if native_action == "create_post":
        cursor = _execute(
            db,
            "INSERT INTO post (user_id, original_post_id, content, created_at, "
            "num_likes, num_dislikes, num_shares, num_reports) "
            "VALUES (?, NULL, ?, ?, 0, 0, 0, 0)",
            (agent_id, content, created),
        )
        native_id = int(cursor.lastrowid)
    elif native_action == "repost":
        cursor = _execute(
            db,
            "INSERT INTO post (user_id, original_post_id, content, created_at, "
            "num_likes, num_dislikes, num_shares, num_reports) "
            "VALUES (?, ?, '', ?, 0, 0, 0, 0)",
            (agent_id, target_id, created),
        )
        native_id = int(cursor.lastrowid)
        _execute(
            db,
            "UPDATE post SET num_shares = num_shares + 1 WHERE post_id = ?",
            (target_id,),
        )
    elif native_action == "create_comment":
        cursor = _execute(
            db,
            "INSERT INTO comment (post_id, user_id, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (target_id, agent_id, content, created),
        )
        native_id = int(cursor.lastrowid)
    elif native_action == "like_post":
        cursor = _execute(
            db,
            "INSERT INTO like (user_id, post_id, created_at) VALUES (?, ?, ?)",
            (agent_id, target_id, created),
        )
        native_id = int(cursor.lastrowid)
        _execute(
            db,
            "UPDATE post SET num_likes = num_likes + 1 WHERE post_id = ?",
            (target_id,),
        )
    elif native_action == "dislike_post":
        cursor = _execute(
            db,
            "INSERT INTO dislike (user_id, post_id, created_at) VALUES (?, ?, ?)",
            (agent_id, target_id, created),
        )
        native_id = int(cursor.lastrowid)
        _execute(
            db,
            "UPDATE post SET num_dislikes = num_dislikes + 1 WHERE post_id = ?",
            (target_id,),
        )
    else:  # pragma: no cover - 契约已限定动作集合
        raise DuetContractError("platform_a_action", "未知原生动作")
    rowid = int(_execute(db, f"SELECT rowid FROM {NATIVE_TABLE[native_action]} "
                             "WHERE rowid = last_insert_rowid()").fetchone()[0])
    return native_id, rowid


def _record_trace(
    db: sqlite3.Connection,
    *,
    agent_id: int,
    action: str,
    info: Mapping[str, Any],
    created: int | str,
) -> None:
    try:
        _execute(
            db,
            "INSERT INTO trace (user_id, created_at, action, info) VALUES (?, ?, ?, ?)",
            (agent_id, created, action, json.dumps(info, ensure_ascii=False)),
        )
    except sqlite3.IntegrityError:
        # 与 OASIS trace 主键一致：完全相同的动作在同一时间步只留一条。
        pass


def _sign_up(db: sqlite3.Connection, *, agent_id: int, name: str, platform_key: str) -> None:
    row = _execute(db, "SELECT user_id FROM user WHERE agent_id = ?", (agent_id,)).fetchone()
    if row is not None:
        return
    created = 0 if platform_key == "platform_a" else datetime.now(timezone.utc).isoformat()
    _execute(
        db,
        "INSERT INTO user (user_id, agent_id, user_name, name, bio, created_at, "
        "num_followings, num_followers) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        (agent_id, agent_id, f"role{agent_id}", name, "EchoSim 模拟角色", created),
    )
    _record_trace(
        db,
        agent_id=agent_id,
        action="sign_up",
        info={"name": name, "user_name": f"role{agent_id}"},
        created=created,
    )


class DoubleEngine:
    """确定性替身引擎：不访问网络、不调用模型，写入与 OASIS 同构的原生表。"""

    mode = "double"

    def __init__(self, db: sqlite3.Connection, *, platform_key: str, step: int) -> None:
        self.db = db
        self.platform_key = platform_key
        self.step = step

    def sign_up(self, agent_id: int, name: str) -> None:
        _sign_up(self.db, agent_id=agent_id, name=name, platform_key=self.platform_key)

    def execute(
        self,
        *,
        native_action: str,
        agent_id: int,
        content: str,
        target_id: int | None,
    ) -> tuple[int, int]:
        native_id, rowid = _record_native(
            self.db,
            native_action=native_action,
            agent_id=agent_id,
            content=content,
            target_id=target_id,
            platform_key=self.platform_key,
            step=self.step,
        )
        created = self.step if self.platform_key == "platform_a" else datetime.now(timezone.utc).isoformat()
        info: dict[str, Any] = {"content": content}
        if native_action == "create_post":
            info = {"content": content, "post_id": native_id}
        elif native_action == "repost":
            info = {"reposted_id": target_id, "new_post_id": native_id}
        elif native_action == "create_comment":
            info = {"content": content, "comment_id": native_id}
        elif native_action == "like_post":
            info = {"post_id": target_id, "like_id": native_id}
        elif native_action == "dislike_post":
            info = {"post_id": target_id, "dislike_id": native_id}
        _record_trace(
            self.db,
            agent_id=agent_id,
            action=native_action,
            info=info,
            created=created,
        )
        return native_id, rowid


class OasisEngine:
    """真实本地 OASIS 平台：调用原生 create_post/create_comment/like/repost。"""

    mode = "oasis"

    def __init__(self, platform: Any, *, platform_key: str) -> None:
        self.platform = platform
        self.platform_key = platform_key
        self._signed: set[int] = set()

    def sign_up(self, agent_id: int, name: str) -> None:
        if agent_id in self._signed:
            return
        import asyncio

        result = asyncio.run(
            self.platform.sign_up(
                agent_id, (name, f"role{agent_id}", "EchoSim 模拟角色")
            )
        )
        if not isinstance(result, dict) or not result.get("success"):
            message = json.dumps(result, ensure_ascii=False).lower() if isinstance(result, dict) else str(result).lower()
            if "unique" not in message and "already" not in message and "exist" not in message:
                raise DuetContractError("platform_b_setup", "角色创建失败")
        self._signed.add(agent_id)

    def execute(
        self,
        *,
        native_action: str,
        agent_id: int,
        content: str,
        target_id: int | None,
    ) -> tuple[int, int]:
        import asyncio

        if native_action == "create_post":
            result = asyncio.run(self.platform.create_post(agent_id, content))
        elif native_action == "repost":
            result = asyncio.run(self.platform.repost(agent_id, target_id))
        elif native_action == "create_comment":
            result = asyncio.run(self.platform.create_comment(agent_id, (target_id, content)))
        elif native_action == "like_post":
            result = asyncio.run(self.platform.like_post(agent_id, target_id))
        elif native_action == "dislike_post":
            result = asyncio.run(self.platform.dislike_post(agent_id, target_id))
        else:  # pragma: no cover - 契约已限定动作集合
            raise DuetContractError("platform_a_action", "未知原生动作")
        if not isinstance(result, dict) or not result.get("success"):
            raise DuetContractError(
                f"{self.platform_key}_action", "原生动作未成功"
            )
        native_id = int(
            result.get("post_id")
            or result.get("comment_id")
            or result.get("like_id")
            or result.get("dislike_id")
            or 0
        )
        if native_id <= 0:
            raise DuetContractError(f"{self.platform_key}_action", "原生动作缺少标识")
        table = NATIVE_TABLE[native_action]
        row = self.platform.db_cursor.execute(
            f"SELECT rowid FROM {table} WHERE rowid = last_insert_rowid()"
        ).fetchone()
        rowid = int(row[0]) if row else 0
        return native_id, rowid


def open_engine(
    *,
    mode: str,
    platform_key: str,
    db_path: Path,
    step: int,
    connection: sqlite3.Connection | None = None,
) -> tuple[Any, Any]:
    """打开引擎；返回 (engine, closer)。缺少依赖时抛 `engine_unavailable`。

    替身引擎复用调用方的连接，避免同一 SQLite 文件上出现两个写事务。
    """
    if mode == "double":
        db = connection if connection is not None else sqlite3.connect(str(db_path))
        engine = DoubleEngine(db, platform_key=platform_key, step=step)
        return engine, None

    try:
        from oasis.social_platform.platform import Platform
    except Exception as exc:  # noqa: BLE001 - 缺少引擎依赖
        raise DuetContractError(
            "engine_unavailable", f"本地 OASIS 引擎不可用（{type(exc).__name__}）"
        ) from None
    import contextlib
    import io

    try:
        # OASIS 建表时会向 stdout 打印 db_path；runner 的 stdout 是协议通道。
        with contextlib.redirect_stdout(io.StringIO()):
            platform = Platform(
                db_path=str(db_path),
                recsys_type="twitter" if platform_key == "platform_a" else "reddit",
            )
    except Exception as exc:  # noqa: BLE001 - 单平台环境失败
        raise DuetContractError(
            f"{platform_key}_setup", f"本地平台创建失败（{type(exc).__name__}）"
        ) from None
    engine = OasisEngine(platform, platform_key=platform_key)
    return engine, platform


def close_engine(closer: Any) -> None:
    if isinstance(closer, sqlite3.Connection):
        try:
            closer.close()
        except sqlite3.Error:
            pass
        return
    for attribute in ("db_cursor", "db"):
        handle = getattr(closer, attribute, None)
        if handle is None:
            continue
        try:
            handle.close()
        except (sqlite3.Error, Exception):  # noqa: BLE001 - 清理失败不影响已写入记录
            pass


# ---------------------------------------------------------------- 轮次执行


@dataclass(frozen=True)
class RoundExecution:
    round_index: int
    status: str
    action_count: int
    actions: tuple[dict[str, Any], ...]


def _action_row(db: sqlite3.Connection, action_key: str) -> sqlite3.Row | None:
    db.row_factory = sqlite3.Row
    try:
        return db.execute(
            "SELECT * FROM echosim_action WHERE action_key = ?", (action_key,)
        ).fetchone()
    finally:
        db.row_factory = None


def _normalized(action: Mapping[str, Any]) -> dict[str, Any]:
    """请求文件用 camelCase，内部统一成 snake_case。"""
    def pick(*names: str) -> Any:
        for name in names:
            if name in action and action[name] is not None:
                return action[name]
        return None

    return {
        "action_key": str(pick("actionKey", "action_key")),
        "action_index": int(pick("actionIndex", "action_index") or 0),
        "native_action": str(pick("nativeAction", "native_action")),
        "actor_role_id": str(pick("actorRoleId", "actor_role_id")),
        "actor_agent_id": int(pick("actorAgentId", "actor_agent_id") or 0),
        "actor_name": str(pick("actorName", "actor_name") or ""),
        "target_engine_record_id": pick("targetEngineRecordId", "target_engine_record_id"),
        "target_action_key": pick("targetActionKey", "target_action_key"),
        "content": str(pick("content") or ""),
        "marker": str(pick("marker") or ""),
    }


def _upsert_action(
    db: sqlite3.Connection,
    *,
    action: Mapping[str, Any],
    round_index: int,
    status: str,
    native_table: str | None,
    baseline: int,
    engine_record_id: str | None,
    engine_sequence: int | None,
) -> None:
    db.execute(
        "INSERT INTO echosim_action (action_key, round_index, action_index, "
        "native_action, actor_role_id, actor_agent_id, actor_name, "
        "target_engine_record_id, target_action_key, content_marker, "
        "native_table, native_baseline, status, engine_record_id, "
        "engine_sequence, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(action_key) DO UPDATE SET status=excluded.status, "
        "native_table=excluded.native_table, native_baseline=excluded.native_baseline, "
        "engine_record_id=excluded.engine_record_id, "
        "engine_sequence=excluded.engine_sequence, updated_at=excluded.updated_at",
        (
            action["action_key"],
            round_index,
            int(action["action_index"]),
            action["native_action"],
            action["actor_role_id"],
            int(action["actor_agent_id"]),
            action["actor_name"],
            action.get("target_engine_record_id"),
            action.get("target_action_key"),
            action.get("marker") or "",
            native_table,
            baseline,
            status,
            engine_record_id,
            engine_sequence,
            _now(),
        ),
    )


def execute_round(
    *,
    mode: str,
    platform_key: str,
    runtime_dir: Path,
    round_index: int,
    actions: Sequence[Mapping[str, Any]],
    fault: str = "none",
) -> RoundExecution:
    """执行一轮：先写意图，再执行原生动作，最后写轮次标记。

    `fault` 只用于验收复现中断：`after_plan`（未执行任何原生动作）、
    `after_first_action`（原生已落库但副表仍是 in_flight）、`before_native`
    （已标记 in_flight 但未调用原生方法）。
    """
    runtime_dir.mkdir(parents=True, exist_ok=True)
    # 同一平台目录只允许一个子进程写入：接手前旧进程若仍持有锁，新进程必须等待。
    lock_handle = open(runtime_dir / "platform.lock", "w")
    try:
        import fcntl

        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    except OSError:
        pass
    db_path = runtime_dir / "social.db"
    if mode == "double":
        db = sqlite3.connect(str(db_path), isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.executescript(NATIVE_SCHEMA)
        db.executescript(SIDECAR_SCHEMA)
        engine, closer = open_engine(
            mode=mode,
            platform_key=platform_key,
            db_path=db_path,
            step=round_index,
            connection=db,
        )
    else:
        # 真实 OASIS 自己管理连接；副表与原生动作共用同一连接，避免写锁冲突。
        engine, closer = open_engine(
            mode=mode,
            platform_key=platform_key,
            db_path=db_path,
            step=round_index,
        )
        db = engine.platform.db
        db.execute("PRAGMA busy_timeout=5000")
        db.executescript(SIDECAR_SCHEMA)
    try:
        try:
            db.execute(
                "INSERT INTO echosim_round (round_index, marker, status, action_count, "
                "started_at) VALUES (?, ?, 'open', 0, ?) "
                "ON CONFLICT(round_index) DO UPDATE SET status='open'",
                (round_index, f"{platform_key}:r{round_index}", _now()),
            )
            db.commit()

            if fault == "after_plan":
                # 只写计划、不执行任何原生动作：接手可证明“尚未开始”并安全重发。
                for action in [_normalized(item) for item in actions]:
                    _upsert_action(
                        db,
                        action=action,
                        round_index=round_index,
                        status="planned",
                        native_table=NATIVE_TABLE[str(action["native_action"])],
                        baseline=0,
                        engine_record_id=None,
                        engine_sequence=None,
                    )
                db.commit()
                return _current_round(db, round_index)

            for action in [_normalized(item) for item in actions]:
                row = _action_row(db, str(action["action_key"]))
                if row is not None and row["status"] == "committed":
                    continue
                table = NATIVE_TABLE[str(action["native_action"])]
                if row is not None and row["status"] == "in_flight":
                    # 上次中断可能已写入原生行：先尝试按基线认领，认领不到才重发。
                    adopted = adopt_native(
                        db,
                        native_action=str(row["native_action"]),
                        agent_id=int(row["actor_agent_id"]),
                        marker=str(row["content_marker"] or ""),
                        target_engine_record_id=row["target_engine_record_id"],
                        baseline=int(row["native_baseline"]),
                    )
                    if adopted is not None:
                        _upsert_action(
                            db,
                            action=action,
                            round_index=round_index,
                            status="committed",
                            native_table=table,
                            baseline=int(row["native_baseline"]),
                            engine_record_id=adopted.engine_record_id,
                            engine_sequence=adopted.engine_sequence,
                        )
                        db.commit()
                        continue
                target = action.get("target_engine_record_id")
                if not target and action.get("target_action_key"):
                    # 同一轮内先执行的帖子：按动作键解析出引擎记录 id。
                    target_row = _action_row(db, str(action["target_action_key"]))
                    if target_row is not None and target_row["engine_record_id"]:
                        target = target_row["engine_record_id"]
                if action["native_action"] in TARGETED_ACTIONS and not target:
                    raise DuetContractError(
                        f"{platform_key}_record", "目标原帖未能在本运行中定位"
                    )
                action["target_engine_record_id"] = target
                engine.sign_up(int(action["actor_agent_id"]), str(action["actor_name"]))
                baseline = _max_rowid(db, table)
                _upsert_action(
                    db,
                    action=action,
                    round_index=round_index,
                    status="in_flight",
                    native_table=table,
                    baseline=baseline,
                    engine_record_id=None,
                    engine_sequence=None,
                )
                db.commit()
                if fault == "before_native":
                    return _current_round(db, round_index)

                target = action.get("target_engine_record_id")
                target_id = _split_record_id(str(target)) if target else None
                native_id, native_seq = engine.execute(
                    native_action=str(action["native_action"]),
                    agent_id=int(action["actor_agent_id"]),
                    content=str(action.get("content") or ""),
                    target_id=target_id,
                )
                _upsert_action(
                    db,
                    action=action,
                    round_index=round_index,
                    status="committed",
                    native_table=table,
                    baseline=baseline,
                    engine_record_id=action_record_id(
                        str(action["native_action"]), native_id
                    ),
                    engine_sequence=native_seq,
                )
                db.commit()
                if fault in ("after_first_action", "strip_marker"):
                    # 原生行已落库，但副表仍是 in_flight、轮次标记仍未写。
                    db.execute(
                        "UPDATE echosim_action SET status='in_flight', "
                        "engine_record_id=NULL, engine_sequence=NULL "
                        "WHERE action_key = ?",
                        (action["action_key"],),
                    )
                    if fault == "strip_marker":
                        # 模拟平台改写正文或写错目标：原生行存在但无法归属。
                        content = str(action.get("content") or "")
                        stripped = content.replace(str(action.get("marker") or ""), "")
                        if str(action["native_action"]) == "create_post":
                            db.execute(
                                "UPDATE post SET content = ? WHERE content = ?",
                                (stripped, content),
                            )
                        elif str(action["native_action"]) == "create_comment":
                            db.execute(
                                "UPDATE comment SET content = ? WHERE content = ?",
                                (stripped, content),
                            )
                        else:
                            db.execute(
                                "UPDATE echosim_action SET "
                                "target_engine_record_id = 'post:999999' "
                                "WHERE action_key = ?",
                                (action["action_key"],),
                            )
                    db.commit()
                    _sleep_for_verification()
                    return _current_round(db, round_index)

            committed = _count_committed(db, round_index)
            db.execute(
                "UPDATE echosim_round SET status='committed', action_count=?, "
                "committed_at=? WHERE round_index = ?",
                (committed, _now(), round_index),
            )
            db.commit()
            _sleep_for_verification()
            return _current_round(db, round_index)
        finally:
            db.commit()
            db.close()
    finally:
        if closer is not None:
            close_engine(closer)
        try:
            lock_handle.close()
        except OSError:
            pass


def _sleep_for_verification() -> None:
    """仅验收用：放大“引擎已写入、worker 尚未记账”的窗口。"""
    sleep_ms = int(os.environ.get("DUET_CHILD_SLEEP_MS") or 0)
    if sleep_ms > 0:
        import time

        time.sleep(min(sleep_ms, 30000) / 1000)


def _count_committed(db: sqlite3.Connection, round_index: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM echosim_action WHERE round_index = ? AND status='committed'",
        (round_index,),
    ).fetchone()
    return int(row[0]) if row else 0


def _current_round(db: sqlite3.Connection, round_index: int) -> RoundExecution:
    db.row_factory = sqlite3.Row
    try:
        round_row = db.execute(
            "SELECT * FROM echosim_round WHERE round_index = ?", (round_index,)
        ).fetchone()
        rows = db.execute(
            "SELECT * FROM echosim_action WHERE round_index = ? ORDER BY action_index",
            (round_index,),
        ).fetchall()
    finally:
        db.row_factory = None
    actions = tuple(
        {
            "actionKey": row["action_key"],
            "nativeAction": row["native_action"],
            "status": row["status"],
            "engineRecordId": row["engine_record_id"],
        }
        for row in rows
    )
    return RoundExecution(
        round_index=round_index,
        status=str(round_row["status"]) if round_row else "missing",
        action_count=int(round_row["action_count"]) if round_row else 0,
        actions=actions,
    )


# ---------------------------------------------------------------- 读回


@dataclass(frozen=True)
class NativeMatch:
    engine_record_id: str
    engine_sequence: int


def verify_native(
    db: sqlite3.Connection,
    *,
    native_action: str,
    agent_id: int,
    content: str,
    marker: str,
    target_engine_record_id: str | None,
    engine_record_id: str,
) -> NativeMatch | None:
    """按原生记录 id 读回并核对目标；只有带正文的动作才校验正文标记。"""
    query = VERIFY_QUERIES.get(native_action)
    if query is None:
        return None
    try:
        row = db.execute(query, (_split_record_id(engine_record_id),)).fetchone()
    except (ValueError, sqlite3.DatabaseError):
        return None
    if row is None:
        return None
    if native_action == "create_post":
        if row[2] is not None and marker and not marker_present(str(row[2]), marker):
            return None
    elif native_action == "create_comment":
        if target_engine_record_id and int(row[2]) != _split_record_id(target_engine_record_id):
            return None
        if marker and not marker_present(str(row[3] or ""), marker):
            return None
    elif native_action == "repost":
        if target_engine_record_id and int(row[2] or 0) != _split_record_id(target_engine_record_id):
            return None
    elif native_action in ("like_post", "dislike_post"):
        if target_engine_record_id and int(row[2]) != _split_record_id(target_engine_record_id):
            return None
    return NativeMatch(engine_record_id=engine_record_id, engine_sequence=int(row[0]))


def adopt_native(    db: sqlite3.Connection,
    *,
    native_action: str,
    agent_id: int,
    marker: str,
    target_engine_record_id: str | None,
    baseline: int,
) -> NativeMatch | None:
    """没有副表记录 id 时，用“基线之后的新行”证明动作已执行。"""
    query = ADOPT_QUERIES.get(native_action)
    if query is None:
        return None
    try:
        if native_action in CONTENT_ACTIONS:
            row = db.execute(query, (baseline, agent_id, f"%{marker}%")).fetchone()
        elif native_action == "repost":
            target = _split_record_id(target_engine_record_id or "")
            row = db.execute(query, (baseline, agent_id, target)).fetchone()
        else:
            target = _split_record_id(target_engine_record_id or "")
            row = db.execute(query, (baseline, agent_id, target)).fetchone()
    except (ValueError, sqlite3.DatabaseError):
        return None
    if row is None:
        return None
    return NativeMatch(
        engine_record_id=action_record_id(native_action, int(row[1])),
        engine_sequence=int(row[0]),
    )


def _ambiguous_native(
    db: sqlite3.Connection,
    *,
    native_action: str,
    agent_id: int,
    baseline: int,
) -> bool:
    """基线之后是否已有本角色的原生行：有但无法归属时不得重发。"""
    table = NATIVE_TABLE.get(native_action)
    if table is None:
        return True
    try:
        row = db.execute(
            f"SELECT 1 FROM {table} WHERE rowid > ? AND user_id = ? LIMIT 1",
            (baseline, agent_id),
        ).fetchone()
    except sqlite3.DatabaseError:
        return True
    return row is not None


def read_sidecar(runtime_dir: Path) -> tuple[dict[int, sqlite3.Row], list[sqlite3.Row]]:
    db_path = runtime_dir / "social.db"
    if not db_path.is_file():
        return {}, []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        try:
            rounds = {
                int(row["round_index"]): row
                for row in db.execute("SELECT * FROM echosim_round")
            }
            actions = list(
                db.execute("SELECT * FROM echosim_action ORDER BY action_index")
            )
        except sqlite3.DatabaseError:
            return {}, []
    finally:
        db.close()
    return rounds, actions


def round_records(
    *,
    runtime_dir: Path,
    platform_key: str,
    round_index: int,
    actions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """独立读回一轮：区分已提交、已执行可补记、未执行与不可确认。"""
    db_path = runtime_dir / "social.db"
    rounds, rows = read_sidecar(runtime_dir)
    round_row = rounds.get(round_index)
    by_key = {str(row["action_key"]): row for row in rows}
    db: sqlite3.Connection | None = None
    if db_path.is_file():
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        records: list[dict[str, Any]] = []
        planned: list[str] = []
        unconfirmed: list[str] = []
        for action in actions:
            key = str(action["action_key"])
            row = by_key.get(key)
            if row is None:
                planned.append(key)
                continue
            status = str(row["status"])
            native_action = str(row["native_action"])
            target = row["target_engine_record_id"]
            match: NativeMatch | None = None
            if db is not None:
                if status == "committed" and row["engine_record_id"]:
                    match = verify_native(
                        db,
                        native_action=native_action,
                        agent_id=int(row["actor_agent_id"]),
                        content=str(action.get("content") or ""),
                        marker=str(row["content_marker"] or ""),
                        target_engine_record_id=target,
                        engine_record_id=str(row["engine_record_id"]),
                    )
                elif status == "in_flight":
                    match = adopt_native(
                        db,
                        native_action=native_action,
                        agent_id=int(row["actor_agent_id"]),
                        marker=str(row["content_marker"] or ""),
                        target_engine_record_id=target,
                        baseline=int(row["native_baseline"]),
                    )
                    if match is None:
                        if _ambiguous_native(
                            db,
                            native_action=native_action,
                            agent_id=int(row["actor_agent_id"]),
                            baseline=int(row["native_baseline"]),
                        ):
                            # 有本角色在该基线之后的新行但无法归属：保留证据、不重发。
                            unconfirmed.append(key)
                            continue
                        # 基线之后没有任何本角色原生行：可证明尚未执行，允许重发。
                        planned.append(key)
                        continue
                elif status == "planned":
                    planned.append(key)
                    continue
            if match is None:
                if status == "in_flight":
                    unconfirmed.append(key)
                else:
                    planned.append(key)
                continue
            records.append(
                {
                    "actionKey": key,
                    "nativeAction": native_action,
                    "actorRoleId": str(row["actor_role_id"]),
                    "actorAgentId": int(row["actor_agent_id"]),
                    "actorName": str(row["actor_name"]),
                    "content": str(action.get("content") or ""),
                    "marker": str(row["content_marker"] or ""),
                    "engineRecordId": match.engine_record_id,
                    "engineSequence": match.engine_sequence,
                    "targetEngineRecordId": target,
                    "targetActionKey": row["target_action_key"],
                    "roundIndex": round_index,
                }
            )
        return {
            "roundIndex": round_index,
            "status": str(round_row["status"]) if round_row else "missing",
            "roundCommitted": bool(round_row and round_row["status"] == "committed"),
            "records": records,
            "planned": planned,
            "unconfirmed": unconfirmed,
            "actionCount": len(actions),
        }
    finally:
        if db is not None:
            db.close()


def native_counts(runtime_dir: Path, platform_key: str) -> dict[str, int]:
    """直接从原生表统计，用于“页面 / 服务端 / 引擎”三方核对。"""
    db_path = runtime_dir / "social.db"
    if not db_path.is_file():
        return {}
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        counts: dict[str, int] = {}
        queries = {
            "create_post": "SELECT COUNT(*) FROM post WHERE original_post_id IS NULL",
            "repost": "SELECT COUNT(*) FROM post WHERE original_post_id IS NOT NULL",
            "create_comment": "SELECT COUNT(*) FROM comment",
            "like_post": "SELECT COUNT(*) FROM like",
            "dislike_post": "SELECT COUNT(*) FROM dislike",
        }
        for action, sql in queries.items():
            if action not in NATIVE_ACTIONS[platform_key]:
                continue
            try:
                counts[action] = int(db.execute(sql).fetchone()[0])
            except sqlite3.DatabaseError:
                counts[action] = 0
        return counts
    finally:
        db.close()


def iter_rounds(rounds: Iterable[sqlite3.Row]) -> Iterable[int]:
    for row in rounds:
        yield int(row["round_index"])
