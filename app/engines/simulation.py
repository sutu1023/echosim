"""OASIS 双平台本地仿真：twitter/create_post 与 reddit/目标帖/create_comment。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts import (
    AcceptanceMaterial,
    CallCounts,
    ItemResult,
    PASSED,
    FAILED,
    build_action_content,
    build_target_post_content,
)
from .settings import EngineSettings

PLATFORM_CONFIG = {
    "platform_a": {
        "native_platform": "twitter",
        "recsys_type": "twitter",
        "role": "居民代表",
        "native_action": "create_post",
    },
    "platform_b": {
        "native_platform": "reddit",
        "recsys_type": "reddit",
        "role": "值班志愿者",
        "native_action": "create_comment",
    },
}


class SharedSetupError(Exception):
    """两个平台共用的导入或运行时前置失败。"""


class PlatformSetupError(Exception):
    """单个平台的环境或目标帖准备失败。"""


@dataclass(frozen=True)
class PlatformOutcome:
    item: ItemResult
    cleanup: str


def _default_platform_factory(
    platform_key: str, db_path: Path, work_root: Path
) -> Any:
    try:
        old_cwd = os.getcwd()
        try:
            os.chdir(work_root)
            from oasis.social_platform.platform import Platform
        finally:
            os.chdir(old_cwd)
    except Exception as exc:  # noqa: BLE001 - 共享依赖导入失败
        raise SharedSetupError(f"{type(exc).__name__}: {exc}") from exc
    try:
        return Platform(
            db_path=str(db_path),
            recsys_type=PLATFORM_CONFIG[platform_key]["recsys_type"],
        )
    except Exception as exc:  # noqa: BLE001 - 单平台构造失败
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc


def _close_platform(platform: object) -> None:
    close = getattr(platform, "close", None)
    if callable(close):
        close()
        return
    cursor = getattr(platform, "db_cursor", None)
    if cursor is not None:
        cursor.close()
    db = getattr(platform, "db", None)
    if db is not None:
        db.close()


def _find_post_id(platform: object, content: str) -> int | None:
    finder = getattr(platform, "find_post_id", None)
    if callable(finder):
        value = finder(content)
        return int(value) if value is not None else None
    cursor = getattr(platform, "db_cursor", None)
    if cursor is None:
        return None
    cursor.execute(
        "SELECT post_id FROM post WHERE content = ? ORDER BY post_id LIMIT 1",
        (content,),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None


async def _ensure_user(platform: object, role: str) -> None:
    sign_up = getattr(platform, "sign_up", None)
    if not callable(sign_up):
        return
    result = await sign_up(1, (role, role, "EchoSim 验收角色"))
    if isinstance(result, dict) and result.get("success"):
        return
    message = json.dumps(result, ensure_ascii=False).lower() if isinstance(result, dict) else str(result).lower()
    if "unique" not in message and "already" not in message and "exists" not in message:
        raise PlatformSetupError("角色创建失败")


async def _run_actions(
    platform: object, platform_key: str, material: AcceptanceMaterial
) -> dict[str, Any]:
    config = PLATFORM_CONFIG[platform_key]
    role = str(config["role"])
    await _ensure_user(platform, role)
    content = build_action_content(platform_key, material)

    if platform_key == "platform_a":
        existing = _find_post_id(platform, content)
        if existing is not None:
            return {"success": True, "post_id": existing, "targetPostId": None}
        result = await platform.create_post(1, content)  # type: ignore[attr-defined]
        if not isinstance(result, dict) or not result.get("success"):
            raise PlatformSetupError("create_post 未成功")
        return {
            "success": True,
            "post_id": result.get("post_id"),
            "targetPostId": None,
        }

    target_content = build_target_post_content(platform_key, material)
    target_post_id = _find_post_id(platform, target_content)
    if target_post_id is None:
        target_result = await platform.create_post(1, target_content)  # type: ignore[attr-defined]
        if not isinstance(target_result, dict) or not target_result.get("success"):
            raise PlatformSetupError("目标帖创建失败")
        target_post_id = int(target_result.get("post_id"))
    result = await platform.create_comment(  # type: ignore[attr-defined]
        1, (target_post_id, content)
    )
    if not isinstance(result, dict) or not result.get("success"):
        raise PlatformSetupError("create_comment 未成功")
    return {
        "success": True,
        "comment_id": result.get("comment_id"),
        "targetPostId": target_post_id,
    }


def _default_readback(
    db_path: Path,
    platform_key: str,
    material: AcceptanceMaterial,
) -> dict[str, Any] | None:
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(db_path)
    try:
        if platform_key == "platform_a":
            content = build_action_content(platform_key, material)
            row = conn.execute(
                "SELECT p.post_id, p.content, u.name FROM post p "
                "LEFT JOIN user u ON u.user_id = p.user_id "
                "WHERE p.content = ? ORDER BY p.post_id LIMIT 1",
                (content,),
            ).fetchone()
            if row is None:
                return None
            return {
                "platformKey": "platform_a",
                "nativePlatform": "twitter",
                "nativeAction": "create_post",
                "role": row[2],
                "content": row[1],
                "recordId": f"post:{row[0]}",
                "result": "success",
                "targetPostId": None,
            }

        target_content = build_target_post_content(platform_key, material)
        comment_content = build_action_content(platform_key, material)
        target = conn.execute(
            "SELECT post_id FROM post WHERE content = ? ORDER BY post_id LIMIT 1",
            (target_content,),
        ).fetchone()
        if target is None:
            return None
        target_post_id = int(target[0])
        row = conn.execute(
            "SELECT c.comment_id, c.post_id, c.content, u.name FROM comment c "
            "LEFT JOIN user u ON u.user_id = c.user_id "
            "WHERE c.post_id = ? AND c.content = ? ORDER BY c.comment_id LIMIT 1",
            (target_post_id, comment_content),
        ).fetchone()
        if row is None:
            return None
        return {
            "platformKey": "platform_b",
            "nativePlatform": "reddit",
            "nativeAction": "create_comment",
            "role": row[3],
            "content": row[2],
            "recordId": f"comment:{row[0]}",
            "result": "success",
            "targetPostId": target_post_id,
        }
    finally:
        conn.close()


def _validate_record(
    record: dict[str, Any] | None,
    platform_key: str,
    material: AcceptanceMaterial,
    *,
    expected_target_post_id: int | None = None,
) -> tuple[bool, str]:
    if not record:
        return False, "record"
    config = PLATFORM_CONFIG[platform_key]
    if record.get("platformKey") != platform_key:
        return False, "record"
    if record.get("nativePlatform") != config["native_platform"]:
        return False, "record"
    if record.get("nativeAction") != config["native_action"]:
        return False, "record"
    if record.get("role") != config["role"]:
        return False, "record"
    if record.get("result") != "success":
        return False, "record"
    content = str(record.get("content") or "")
    if material.run_id not in content and material.marker not in content:
        return False, "record"
    if platform_key == "platform_b":
        if not record.get("targetPostId"):
            return False, "record"
        if expected_target_post_id is not None and int(record["targetPostId"]) != int(
            expected_target_post_id
        ):
            return False, "record"
    return True, ""


def run_platform(
    platform_key: str,
    settings: EngineSettings,
    material: AcceptanceMaterial,
    work_root: Path,
    *,
    platform_factory: Callable[[str, Path, Path], Any] | None = None,
    readback: Callable[[Path, str, AcceptanceMaterial], dict[str, Any] | None] | None = None,
    call_kind: str = "real",
    clock: Callable[[], float] = time.monotonic,
) -> PlatformOutcome:
    config = PLATFORM_CONFIG[platform_key]
    factory = platform_factory or _default_platform_factory
    read = readback or _default_readback
    platform_dir = work_root / platform_key
    platform_dir.mkdir(parents=True, exist_ok=True)
    db_path = platform_dir / "social.db"
    started = clock()
    attempts = 0
    platform: object | None = None
    cleanup = "passed"
    outcome: dict[str, Any] | None = None
    action_result: dict[str, Any] | None = None
    result_item: ItemResult | None = None

    try:
        try:
            platform = factory(platform_key, db_path, work_root)
        except SharedSetupError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单平台前置失败
            result_item = ItemResult(
                f"simulation.{platform_key}",
                "simulation",
                config["native_action"],
                FAILED,
                f"{platform_key}_setup",
                int((clock() - started) * 1000),
                _calls(attempts, call_kind),
                {"detail": f"{type(exc).__name__}: {exc}"},
            )

        if platform is not None:
            for attempts in range(1, 3):
                try:
                    action_result = asyncio.run(
                        asyncio.wait_for(
                            _run_actions(platform, platform_key, material),
                            timeout=settings.simulation_timeout_seconds,
                        )
                    )
                    outcome = read(db_path, platform_key, material)
                    if outcome:
                        break
                except asyncio.TimeoutError:
                    outcome = read(db_path, platform_key, material)
                    if outcome:
                        break
                    if attempts == 2:
                        result_item = ItemResult(
                            f"simulation.{platform_key}",
                            "simulation",
                            config["native_action"],
                            FAILED,
                            "action_unconfirmed",
                            int((clock() - started) * 1000),
                            _calls(attempts, call_kind),
                            {"detail": "动作超时且无法确认"},
                        )
                        break
                except PlatformSetupError as exc:
                    result_item = ItemResult(
                        f"simulation.{platform_key}",
                        "simulation",
                        config["native_action"],
                        FAILED,
                        f"{platform_key}_setup",
                        int((clock() - started) * 1000),
                        _calls(attempts, call_kind),
                        {"detail": str(exc)},
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - 单平台动作失败
                    result_item = ItemResult(
                        f"simulation.{platform_key}",
                        "simulation",
                        config["native_action"],
                        FAILED,
                        f"{platform_key}_action",
                        int((clock() - started) * 1000),
                        _calls(attempts, call_kind),
                        {"detail": f"{type(exc).__name__}: {exc}"},
                    )
                    break
                outcome = read(db_path, platform_key, material)
                if outcome:
                    break

            if result_item is None and outcome is None:
                result_item = ItemResult(
                    f"simulation.{platform_key}",
                    "simulation",
                    config["native_action"],
                    FAILED,
                    f"{platform_key}_record",
                    int((clock() - started) * 1000),
                    _calls(attempts, call_kind),
                    {"detail": "引擎记录不存在"},
                )
            elif result_item is None and outcome is not None:
                expected_target = (
                    action_result.get("targetPostId")
                    if isinstance(action_result, dict)
                    else None
                )
                valid, failure = _validate_record(
                    outcome,
                    platform_key,
                    material,
                    expected_target_post_id=expected_target,
                )
                if not valid:
                    result_item = ItemResult(
                        f"simulation.{platform_key}",
                        "simulation",
                        config["native_action"],
                        FAILED,
                        f"{platform_key}_{failure}",
                        int((clock() - started) * 1000),
                        _calls(attempts, call_kind),
                        {"record": outcome},
                    )
                else:
                    result_item = ItemResult(
                        f"simulation.{platform_key}",
                        "simulation",
                        config["native_action"],
                        PASSED,
                        None,
                        int((clock() - started) * 1000),
                        _calls(attempts, call_kind),
                        {
                            "platformKey": platform_key,
                            "nativePlatform": config["native_platform"],
                            "nativeAction": config["native_action"],
                            "role": config["role"],
                            "recordId": outcome.get("recordId"),
                            "targetPostId": outcome.get("targetPostId"),
                            "result": outcome.get("result"),
                            "attempts": attempts,
                        },
                    )
    finally:
        if platform is not None:
            try:
                _close_platform(platform)
            except Exception:  # noqa: BLE001 - 清理失败在下面记录
                cleanup = "failed"
        try:
            shutil.rmtree(platform_dir)
        except OSError:
            cleanup = "failed"

    if result_item is None:
        result_item = ItemResult(
            f"simulation.{platform_key}",
            "simulation",
            config["native_action"],
            FAILED,
            f"{platform_key}_setup",
            int((clock() - started) * 1000),
            _calls(attempts, call_kind),
            {"detail": "平台未产生结果"},
        )
    result_item.evidence["cleanup"] = cleanup
    return PlatformOutcome(result_item, cleanup)


def _calls(count: int, call_kind: str) -> CallCounts:
    if call_kind == "double":
        return CallCounts(real=0, double=count)
    return CallCounts(real=count, double=0)


def minimal_simulation_check(work_root: Path) -> tuple[bool, str]:
    """四阶段基线的最小运行：只创建并关闭一个本地平台，不访问外部网络。"""
    work_root.mkdir(parents=True, exist_ok=True)
    platform_dir = work_root / "minimal"
    platform_dir.mkdir(parents=True, exist_ok=True)
    platform: object | None = None
    try:
        platform = _default_platform_factory(
            "platform_a", platform_dir / "minimal.db", work_root
        )
        return True, "已创建并关闭最小本地仿真环境"
    except Exception as exc:  # noqa: BLE001 - 最小运行失败记录类型
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if platform is not None:
            try:
                _close_platform(platform)
            except Exception:  # noqa: BLE001 - 关闭失败不改变最小运行结论
                pass
        shutil.rmtree(platform_dir, ignore_errors=True)
