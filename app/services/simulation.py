"""双平台运行持久化服务：开始、读取、停止、轮次记账与终态。

运行只从冻结选择创建；所有读取先按项目归属过滤。计数以 `simulation_actions`
聚合为权威，平台行上的 `action_count`/`by_action` 只是同事务冗余值。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..engines.duet_contracts import (
    PLATFORM_MAPPING,
    DuetContractError,
    parse_selection,
    SelectionPlan,
)
from ..errors import (
    SelectionNotFound,
    ServiceUnavailable,
    SimulationConflict,
    SimulationNotFound,
)
from . import jobs as job_service
from . import run_events

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)

SIMULATION_KIND = "simulation.run"
SIMULATION_STAGE = "dual-platform-run"
SIMULATION_TOTAL_STEPS = 4
SIMULATION_STEP_KEYS = ("bind", "environment", "advance", "publish")
SIMULATION_OPERATION_KEY_PREFIX = "dual-platform-run:v1:"

ACTIVE_PHASES = ("queued", "running")
TERMINAL_PHASES = ("succeeded", "failed", "canceled")
ENGINE_MODES = ("double", "oasis")

SIM_COLUMNS = (
    "s.id, s.project_id, s.selection_id, s.preparation_id, s.build_id, "
    "s.snapshot_id, s.run_id, s.question, s.rounds, s.minutes_per_round, "
    "s.duration_hours, s.engine_mode, s.selection_payload, s.phase, "
    "s.failure_code, s.failed_platforms, s.simulated_start, "
    "s.provider_calls_real, s.provider_calls_double, s.reserved_actions, "
    "s.cleanup_code, s.created_at, s.updated_at, s.finished_at, "
    "j.id AS job_id, j.status AS job_status, j.cancel_requested_at"
)
SIM_SOURCE = "FROM simulations s JOIN jobs j ON j.run_id = s.run_id"


@dataclass(frozen=True)
class SimulationRow:
    id: uuid.UUID
    project_id: uuid.UUID
    selection_id: uuid.UUID
    preparation_id: uuid.UUID
    build_id: uuid.UUID
    snapshot_id: uuid.UUID
    run_id: uuid.UUID
    question: str
    rounds: int
    minutes_per_round: int
    duration_hours: int
    engine_mode: str
    selection_payload: Mapping[str, Any]
    phase: str
    failure_code: str | None
    failed_platforms: list[str]
    simulated_start: datetime
    provider_calls_real: int
    provider_calls_double: int
    reserved_actions: int
    cleanup_code: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    job_id: uuid.UUID
    job_status: str
    stop_requested_at: datetime | None

    @property
    def terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    @property
    def plan(self) -> SelectionPlan:
        return parse_selection(self.selection_payload)


def _as_row(row: object) -> SimulationRow:
    platforms = row.failed_platforms or []  # type: ignore[attr-defined]
    return SimulationRow(
        id=row.id,  # type: ignore[attr-defined]
        project_id=row.project_id,  # type: ignore[attr-defined]
        selection_id=row.selection_id,  # type: ignore[attr-defined]
        preparation_id=row.preparation_id,  # type: ignore[attr-defined]
        build_id=row.build_id,  # type: ignore[attr-defined]
        snapshot_id=row.snapshot_id,  # type: ignore[attr-defined]
        run_id=row.run_id,  # type: ignore[attr-defined]
        question=row.question,  # type: ignore[attr-defined]
        rounds=int(row.rounds),  # type: ignore[attr-defined]
        minutes_per_round=int(row.minutes_per_round),  # type: ignore[attr-defined]
        duration_hours=int(row.duration_hours),  # type: ignore[attr-defined]
        engine_mode=str(row.engine_mode),  # type: ignore[attr-defined]
        selection_payload=row.selection_payload,  # type: ignore[attr-defined]
        phase=str(row.phase),  # type: ignore[attr-defined]
        failure_code=row.failure_code,  # type: ignore[attr-defined]
        failed_platforms=list(platforms),
        simulated_start=row.simulated_start,  # type: ignore[attr-defined]
        provider_calls_real=int(row.provider_calls_real),  # type: ignore[attr-defined]
        provider_calls_double=int(row.provider_calls_double),  # type: ignore[attr-defined]
        reserved_actions=int(getattr(row, "reserved_actions", 0) or 0),
        cleanup_code=getattr(row, "cleanup_code", None),
        created_at=row.created_at,  # type: ignore[attr-defined]
        updated_at=row.updated_at,  # type: ignore[attr-defined]
        finished_at=row.finished_at,  # type: ignore[attr-defined]
        job_id=row.job_id,  # type: ignore[attr-defined]
        job_status=str(row.job_status),  # type: ignore[attr-defined]
        stop_requested_at=getattr(row, "cancel_requested_at", None),
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_uuid(raw: str | uuid.UUID, error: type[Exception]) -> uuid.UUID:
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        raise error from None


async def _fetch(
    db: AsyncSession, *, project_id: uuid.UUID, run_id: uuid.UUID
) -> SimulationRow | None:
    result = await db.execute(
        text(
            f"SELECT {SIM_COLUMNS} {SIM_SOURCE} "
            "WHERE s.project_id = :project_id AND s.run_id = :run_id"
        ),
        {"project_id": project_id, "run_id": run_id},
    )
    row = result.first()
    return None if row is None else _as_row(row)


async def get_simulation(
    db: AsyncSession, *, project_id: uuid.UUID, run_id: str | uuid.UUID
) -> SimulationRow:
    parsed = _parse_uuid(run_id, SimulationNotFound)
    try:
        simulation = await _fetch(db, project_id=project_id, run_id=parsed)
    except DB_ERRORS as exc:
        logger.warning("运行详情查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    if simulation is None:
        raise SimulationNotFound
    return simulation


async def get_simulation_for_run(
    db: AsyncSession, *, run_id: uuid.UUID
) -> SimulationRow:
    result = await db.execute(
        text(f"SELECT {SIM_COLUMNS} {SIM_SOURCE} WHERE s.run_id = :run_id"),
        {"run_id": run_id},
    )
    row = result.first()
    if row is None:
        raise SimulationNotFound
    return _as_row(row)


async def list_simulations(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    selection_id: str | None = None,
    limit: int = 20,
) -> list[SimulationRow]:
    params: dict[str, Any] = {
        "project_id": project_id,
        "limit": max(1, min(limit, 50)),
    }
    clause = ""
    if selection_id:
        try:
            params["selection_id"] = uuid.UUID(selection_id)
        except (ValueError, AttributeError):
            raise SimulationConflict("selection_invalid", "选择标识非法") from None
        clause = " AND s.selection_id = :selection_id"
    try:
        result = await db.execute(
            text(
                f"SELECT {SIM_COLUMNS} {SIM_SOURCE} WHERE s.project_id = :project_id"
                f"{clause} ORDER BY s.created_at DESC, s.id DESC LIMIT :limit"
            ),
            params,
        )
        return [_as_row(row) for row in result.fetchall()]
    except DB_ERRORS as exc:
        logger.warning("运行列表查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc


async def _latest(
    db: AsyncSession, *, project_id: uuid.UUID
) -> SimulationRow | None:
    result = await db.execute(
        text(
            f"SELECT {SIM_COLUMNS} {SIM_SOURCE} WHERE s.project_id = :project_id "
            "ORDER BY (s.phase IN ('queued', 'running')) DESC, "
            "s.created_at DESC, s.id DESC LIMIT 1"
        ),
        {"project_id": project_id},
    )
    row = result.first()
    return None if row is None else _as_row(row)


async def _active_for_selection(
    db: AsyncSession, *, project_id: uuid.UUID, selection_id: uuid.UUID
) -> SimulationRow | None:
    result = await db.execute(
        text(
            f"SELECT {SIM_COLUMNS} {SIM_SOURCE} "
            "WHERE s.project_id = :project_id AND s.selection_id = :selection_id "
            "AND j.status IN ('queued', 'running') "
            "ORDER BY s.created_at DESC LIMIT 1"
        ),
        {"project_id": project_id, "selection_id": selection_id},
    )
    row = result.first()
    return None if row is None else _as_row(row)


async def start_simulation(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    selection_id: str | uuid.UUID,
    engine_mode: str | None,
    allow_oasis: bool,
    default_engine: str,
) -> tuple[SimulationRow, bool]:
    """从冻结选择开始运行；返回 (运行, 是否新建)。"""
    parsed = _parse_uuid(selection_id, SelectionNotFound)
    requested = (engine_mode or default_engine or "double").strip().lower()
    if requested not in ENGINE_MODES:
        raise SimulationConflict("selection_invalid", "引擎模式非法")
    if requested == "oasis" and not allow_oasis:
        raise SimulationConflict("engine_not_enabled", "真实本地 OASIS 运行未启用")

    try:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{project_id}:simulation:{parsed}"},
        )
        selection = await db.execute(
            text(
                "SELECT selection_id, preparation_id, build_id, snapshot_id, payload "
                "FROM environment_frozen_selections "
                "WHERE project_id = :project_id AND selection_id = :selection_id"
            ),
            {"project_id": project_id, "selection_id": parsed},
        )
        row = selection.first()
        if row is None:
            raise SelectionNotFound
        try:
            plan = parse_selection(row.payload)
        except DuetContractError as exc:
            raise SimulationConflict(exc.code, exc.message) from None

        active = await _active_for_selection(
            db, project_id=project_id, selection_id=parsed
        )
        if active is not None:
            await db.commit()
            return active, False

        simulation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        job_id = uuid.uuid4()
        # 推演时钟从首个首发角色的最早活跃小时开始；真实创建时间只用于审计。
        simulated_start = datetime.now(timezone.utc).replace(
            hour=plan.start_hour, minute=0, second=0, microsecond=0
        )
        await db.execute(
            text(
                "INSERT INTO runs (id, project_id, created_by, kind, stage, "
                "total_steps, fail_at_step, simulated_epoch) "
                "VALUES (:id, :project_id, :created_by, :kind, :stage, :steps, "
                "NULL, :simulated_epoch)"
            ),
            {
                "id": run_id,
                "project_id": project_id,
                "created_by": actor_id,
                "kind": SIMULATION_KIND,
                "stage": SIMULATION_STAGE,
                "steps": SIMULATION_TOTAL_STEPS,
                "simulated_epoch": simulated_start,
            },
        )
        await db.execute(
            text(
                "INSERT INTO jobs (id, run_id, project_id, operation_key, status, "
                "next_step, attempts, lease_token) VALUES (:id, :run_id, "
                ":project_id, :operation_key, 'queued', 0, 0, NULL)"
            ),
            {
                "id": job_id,
                "run_id": run_id,
                "project_id": project_id,
                "operation_key": f"{SIMULATION_OPERATION_KEY_PREFIX}{parsed}",
            },
        )
        await db.execute(
            text(
                "INSERT INTO simulations (id, project_id, selection_id, "
                "preparation_id, build_id, snapshot_id, run_id, question, rounds, "
                "minutes_per_round, duration_hours, engine_mode, selection_payload, "
                "phase, simulated_start) VALUES (:id, :project_id, :selection_id, "
                ":preparation_id, :build_id, :snapshot_id, :run_id, :question, "
                ":rounds, :minutes, :hours, :engine_mode, CAST(:payload AS jsonb), "
                "'queued', :simulated_start)"
            ),
            {
                "id": simulation_id,
                "project_id": project_id,
                "selection_id": parsed,
                "preparation_id": plan.preparation_id,
                "build_id": plan.build_id,
                "snapshot_id": plan.snapshot_id,
                "run_id": run_id,
                "question": plan.question,
                "rounds": plan.rounds,
                "minutes": plan.minutes_per_round,
                "hours": plan.duration_hours,
                "engine_mode": requested,
                "payload": json.dumps(plan_payload(row.payload), ensure_ascii=False),
                "simulated_start": simulated_start,
            },
        )
        for platform_key, native_platform in PLATFORM_MAPPING.items():
            await db.execute(
                text(
                    "INSERT INTO simulation_platforms (simulation_id, platform_key, "
                    "native_platform, status, engine_rel_dir) VALUES "
                    "(:simulation_id, :platform_key, :native_platform, 'pending', "
                    ":engine_rel_dir)"
                ),
                {
                    "simulation_id": simulation_id,
                    "platform_key": platform_key,
                    "native_platform": native_platform,
                    "engine_rel_dir": f"{simulation_id}/{platform_key}",
                },
            )
        await db.commit()
    except (SelectionNotFound, SimulationConflict):
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("开始运行冲突 project=%s: %s", project_id, type(exc).__name__)
        active = await _active_for_selection(
            db, project_id=project_id, selection_id=parsed
        )
        if active is not None:
            return active, False
        raise SimulationConflict(
            "simulation_conflict", "运行状态已变化，请刷新后重试"
        ) from exc
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("开始运行失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc

    return (
        await get_simulation(db, project_id=project_id, run_id=run_id),
        True,
    )


def plan_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """只保留运行需要的冻结字段，避免把整份准备详情复制进运行。"""
    if not isinstance(payload, Mapping):
        return {}
    allowed = {
        "selectionId",
        "preparationId",
        "projectId",
        "buildId",
        "snapshotId",
        "question",
        "settingsRevision",
        "settings",
        "roles",
        "platforms",
        "initialTopics",
        "activation",
    }
    return {key: value for key, value in payload.items() if key in allowed}


# ---------------------------------------------------------------- 读取视图


async def load_platforms(
    db: AsyncSession, *, simulation_id: uuid.UUID
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT platform_key, native_platform, status, rounds_completed, "
            "action_count, by_action, failure_code FROM simulation_platforms "
            "WHERE simulation_id = :simulation_id ORDER BY platform_key"
        ),
        {"simulation_id": simulation_id},
    )
    return [
        {
            "platformKey": row.platform_key,
            "nativePlatform": row.native_platform,
            "status": row.status,
            "roundsCompleted": int(row.rounds_completed),
            "actionCount": int(row.action_count),
            "byAction": dict(row.by_action or {}),
            "failure": (
                None
                if row.failure_code is None
                else {"code": row.failure_code}
            ),
        }
        for row in result.fetchall()
    ]


async def aggregate_counts(
    db: AsyncSession, *, simulation_id: uuid.UUID
) -> tuple[int, dict[str, int], dict[str, dict[str, int]]]:
    result = await db.execute(
        text(
            "SELECT platform_key, native_action, COUNT(*) AS total "
            "FROM simulation_actions WHERE simulation_id = :simulation_id "
            "GROUP BY platform_key, native_action"
        ),
        {"simulation_id": simulation_id},
    )
    overall: dict[str, int] = {}
    per_platform: dict[str, dict[str, int]] = {}
    total = 0
    for row in result.fetchall():
        count = int(row.total)
        total += count
        overall[row.native_action] = overall.get(row.native_action, 0) + count
        bucket = per_platform.setdefault(row.platform_key, {})
        bucket[row.native_action] = bucket.get(row.native_action, 0) + count
    return total, overall, per_platform


async def action_page(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    platform_key: str | None = None,
    after: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """按 action_seq 升序分页；目标摘要与目标动作一起返回。"""
    params: dict[str, Any] = {
        "simulation_id": simulation_id,
        "after": max(0, after),
        "limit": max(1, min(limit, 200)),
    }
    clause = ""
    if platform_key:
        if platform_key not in PLATFORM_MAPPING:
            raise SimulationConflict("selection_invalid", "平台非法")
        params["platform_key"] = platform_key
        clause = " AND a.platform_key = :platform_key"
    result = await db.execute(
        text(
            "SELECT a.id, a.platform_key, a.native_platform, a.native_action, "
            "a.round_index, a.action_seq, a.actor_role_id, a.actor_display_name, "
            "a.content, a.engine_record_id, a.engine_sequence, "
            "a.target_engine_record_id, a.target_action_id, a.target_platform_key, "
            "a.target_native_action, a.target_resolved, a.simulated_at, "
            "t.content AS target_content, t.actor_display_name AS target_actor_name, "
            "t.round_index AS target_round_index, t.simulated_at AS target_simulated_at "
            "FROM simulation_actions a LEFT JOIN simulation_actions t "
            "ON t.id = a.target_action_id "
            "WHERE a.simulation_id = :simulation_id AND a.action_seq > :after"
            f"{clause} ORDER BY a.action_seq LIMIT :limit"
        ),
        params,
    )
    items: list[dict[str, Any]] = []
    for row in result.fetchall():
        target = None
        if row.target_resolved and row.target_action_id is not None:
            target = {
                "actionId": str(row.target_action_id),
                "content": row.target_content or "",
                "actorRoleName": row.target_actor_name or "",
                "roundIndex": int(row.target_round_index or 0),
                "simulatedAt": _iso(row.target_simulated_at),
            }
        items.append(
            {
                "actionId": str(row.id),
                "platformKey": row.platform_key,
                "nativePlatform": row.native_platform,
                "nativeAction": row.native_action,
                "roundIndex": int(row.round_index),
                "actionSeq": int(row.action_seq),
                "actorRoleId": str(row.actor_role_id),
                "actorRoleName": row.actor_display_name,
                "content": row.content,
                "engineRecordId": row.engine_record_id,
                "engineSequence": row.engine_sequence,
                "targetEngineRecordId": row.target_engine_record_id,
                "targetActionId": (
                    str(row.target_action_id)
                    if row.target_action_id is not None
                    else None
                ),
                "targetPlatformKey": row.target_platform_key,
                "targetNativeAction": row.target_native_action,
                "targetResolved": bool(row.target_resolved),
                "target": target,
                "simulatedAt": _iso(row.simulated_at),
            }
        )
    next_after = items[-1]["actionSeq"] if items else max(0, after)
    return {"items": items, "nextAfter": next_after}


async def serialize_simulation(
    db: AsyncSession, simulation: SimulationRow, *, action_limit: int = 50
) -> dict[str, Any]:
    platforms = await load_platforms(db, simulation_id=simulation.id)
    total, overall, per_platform = await aggregate_counts(
        db, simulation_id=simulation.id
    )
    for platform in platforms:
        platform["byAction"] = per_platform.get(platform["platformKey"], {})
        platform["actionCount"] = sum(platform["byAction"].values())
        platform["roundsTotal"] = simulation.rounds
        platform["simulatedAt"] = _iso(
            simulation.simulated_start
            + timedelta(
                minutes=simulation.minutes_per_round * platform["roundsCompleted"]
            )
        )
    clock_rounds = max(
        [platform["roundsCompleted"] for platform in platforms] or [0]
    )
    return {
        "runId": str(simulation.run_id),
        "simulationId": str(simulation.id),
        "jobId": str(simulation.job_id),
        "selectionId": str(simulation.selection_id),
        "preparationId": str(simulation.preparation_id),
        "buildId": str(simulation.build_id),
        "snapshotId": str(simulation.snapshot_id),
        "question": simulation.question,
        "engine": simulation.engine_mode,
        "phase": simulation.phase,
        "terminal": simulation.terminal,
        "partial": bool(simulation.failed_platforms),
        "failedPlatforms": simulation.failed_platforms,
        "failure": (
            None
            if simulation.failure_code is None
            else {"code": simulation.failure_code}
        ),
        "rounds": simulation.rounds,
        "minutesPerRound": simulation.minutes_per_round,
        "durationHours": simulation.duration_hours,
        "simulatedStart": _iso(simulation.simulated_start),
        "simulatedAt": _iso(
            simulation.simulated_start
            + timedelta(
                minutes=simulation.minutes_per_round * clock_rounds
            )
        ),
        "platforms": platforms,
        "counts": {"total": total, "byAction": overall},
        "actions": await action_page(
            db, simulation_id=simulation.id, limit=action_limit
        ),
        "canStop": not simulation.terminal and simulation.stop_requested_at is None,
        "stopRequested": simulation.stop_requested_at is not None,
        "cleanup": (
            None
            if simulation.cleanup_code is None
            else {"code": simulation.cleanup_code}
        ),
        "createdAt": _iso(simulation.created_at),
        "updatedAt": _iso(simulation.updated_at),
        "finishedAt": _iso(simulation.finished_at),
    }


async def serialize_list(
    db: AsyncSession, simulations: Sequence[SimulationRow]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for simulation in simulations:
        items.append(
            {
                "runId": str(simulation.run_id),
                "selectionId": str(simulation.selection_id),
                "engine": simulation.engine_mode,
                "phase": simulation.phase,
                "rounds": simulation.rounds,
                "terminal": simulation.terminal,
                "failedPlatforms": simulation.failed_platforms,
                "createdAt": _iso(simulation.created_at),
                "finishedAt": _iso(simulation.finished_at),
            }
        )
    return items


async def default_simulation(
    db: AsyncSession, *, project_id: uuid.UUID
) -> SimulationRow | None:
    try:
        return await _latest(db, project_id=project_id)
    except DB_ERRORS as exc:
        logger.warning("最近运行查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc


async def stop_simulation(
    db: AsyncSession, *, project_id: uuid.UUID, run_id: str | uuid.UUID
) -> tuple[SimulationRow, bool]:
    simulation = await get_simulation(db, project_id=project_id, run_id=run_id)
    job, applied = await job_service.cancel_job(
        db, project_id=project_id, job_id=simulation.job_id
    )
    del job
    refreshed = await get_simulation(db, project_id=project_id, run_id=run_id)
    return refreshed, applied


async def cancel_for_run(db: AsyncSession, *, run_id: uuid.UUID) -> None:
    """在取消事务内冻结运行；不删除已有轮次、动作或计数。"""
    await db.execute(
        text(
            "UPDATE simulations SET phase = 'canceled', "
            "failure_code = COALESCE(failure_code, 'canceled'), "
            "finished_at = clock_timestamp(), updated_at = clock_timestamp() "
            "WHERE run_id = :run_id AND phase IN ('queued', 'running')"
        ),
        {"run_id": run_id},
    )
    await db.execute(
        text(
            "UPDATE simulation_platforms SET status = 'canceled', "
            "failure_code = COALESCE(failure_code, 'canceled'), "
            "updated_at = clock_timestamp() WHERE simulation_id = "
            "(SELECT id FROM simulations WHERE run_id = :run_id) "
            "AND status IN ('pending', 'running')"
        ),
        {"run_id": run_id},
    )


# ---------------------------------------------------------------- 预算与终态


async def reserve_actions(
    db: AsyncSession, *, simulation_id: uuid.UUID, count: int, max_actions: int
) -> bool:
    """调用引擎前预留整次运行的动作预算；两个平台共享同一上限。"""
    if count <= 0:
        return True
    result = await db.execute(
        text(
            "UPDATE simulations s SET reserved_actions = s.reserved_actions + :count, "
            "updated_at = clock_timestamp() WHERE s.id = :id AND ("
            "SELECT COUNT(*) FROM simulation_actions a WHERE a.simulation_id = s.id"
            ") + s.reserved_actions + :count <= :max_actions RETURNING s.reserved_actions"
        ),
        {
            "count": count,
            "id": simulation_id,
            "max_actions": max_actions,
        },
    )
    return result.first() is not None


async def release_actions(
    db: AsyncSession, *, simulation_id: uuid.UUID, count: int
) -> None:
    if count <= 0:
        return
    await db.execute(
        text(
            "UPDATE simulations SET reserved_actions = GREATEST(0, "
            "reserved_actions - :count), updated_at = clock_timestamp() "
            "WHERE id = :id"
        ),
        {"count": count, "id": simulation_id},
    )


async def terminate(
    db: AsyncSession,
    *,
    simulation: SimulationRow,
    status: str,
    reason: str | None,
    failed_platforms: Sequence[str],
    stage: str,
    simulated_epoch: datetime,
    simulated_at: datetime | None = None,
) -> tuple[bool, str]:
    """在锁定的运行行上写终态；并发时只有先提交的一方生效。"""
    locked = await db.execute(
        text("SELECT phase FROM simulations WHERE id = :id FOR UPDATE"),
        {"id": simulation.id},
    )
    row = locked.first()
    if row is None:
        return False, simulation.phase
    current = str(row.phase)
    if current in TERMINAL_PHASES:
        return False, current
    total, overall, _ = await aggregate_counts(db, simulation_id=simulation.id)
    await db.execute(
        text(
            "UPDATE simulations SET phase = :status, failure_code = :reason, "
            "failed_platforms = :platforms, reserved_actions = 0, "
            "updated_at = clock_timestamp(), finished_at = clock_timestamp() "
            "WHERE id = :id"
        ),
        {
            "status": status,
            "reason": reason,
            "platforms": list(failed_platforms),
            "id": simulation.id,
        },
    )
    seq = await run_events.max_seq(db, run_id=simulation.run_id) + 1
    event_type = {
        "succeeded": "completed",
        "failed": "failed",
        "canceled": "canceled",
    }.get(status, "failed")
    await run_events.append_event(
        db,
        run_id=simulation.run_id,
        seq=seq,
        type=event_type,
        stage=stage,
        simulated_at=(
            simulated_at
            if simulated_at is not None
            else run_events.simulated_time(simulated_epoch, seq)
        ),
        payload={
            "simulationId": str(simulation.id),
            "selectionId": str(simulation.selection_id),
            "buildId": str(simulation.build_id),
            "snapshotId": str(simulation.snapshot_id),
            "step": 4,
            "stepKey": "publish",
            "phase": status,
            "reason": reason,
            "failedPlatforms": list(failed_platforms),
            "counts": {"total": total, "byAction": overall},
        },
    )
    return True, status


async def record_cleanup(
    db: AsyncSession, *, simulation_id: uuid.UUID, code: str | None
) -> None:
    """终态提交后记录清理结果；不改变业务终态、计数或已有结果。"""
    await db.execute(
        text(
            "UPDATE simulations SET cleanup_code = :code, "
            "updated_at = clock_timestamp() WHERE id = :id"
        ),
        {"code": code, "id": simulation_id},
    )


# ---------------------------------------------------------------- 轮次记账


async def write_round(
    db: AsyncSession,
    *,
    simulation: SimulationRow,
    platform_key: str,
    round_index: int,
    simulated_at: datetime,
    records: Sequence[Mapping[str, Any]],
    outcome: str = "committed",
) -> int | None:
    """写入一轮：轮次记录、动作、平台计数；调用方负责租约、事件与提交。

    `outcome=unconfirmed` 时保留该轮已确认动作和轮次记录，但不推进
    `rounds_completed`，表示“这一轮没有正常完成”。
    """
    if outcome not in ("committed", "unconfirmed"):
        raise ValueError("outcome 只能是 committed 或 unconfirmed")
    inserted = await db.execute(
        text(
            "INSERT INTO simulation_rounds (simulation_id, platform_key, "
            "round_index, simulated_at, action_count, engine_write_outcome) "
            "VALUES (:simulation_id, :platform_key, :round_index, :simulated_at, "
            ":action_count, :outcome) ON CONFLICT DO NOTHING "
            "RETURNING round_index"
        ),
        {
            "simulation_id": simulation.id,
            "platform_key": platform_key,
            "round_index": round_index,
            "simulated_at": simulated_at,
            "action_count": len(records),
            "outcome": outcome,
        },
    )
    if inserted.first() is None:
        return None
    current = await db.execute(
        text(
            "SELECT COALESCE(MAX(action_seq), 0) AS seq FROM simulation_actions "
            "WHERE simulation_id = :simulation_id"
        ),
        {"simulation_id": simulation.id},
    )
    seq = int(current.scalar_one())
    for record in records:
        seq += 1
        target_action_id = None
        target_engine_record_id = record.get("targetEngineRecordId")
        if target_engine_record_id:
            target = await db.execute(
                text(
                    "SELECT id FROM simulation_actions WHERE simulation_id = "
                    ":simulation_id AND platform_key = :platform_key "
                    "AND engine_record_id = :engine_record_id"
                ),
                {
                    "simulation_id": simulation.id,
                    "platform_key": platform_key,
                    "engine_record_id": target_engine_record_id,
                },
            )
            target_row = target.first()
            target_action_id = target_row.id if target_row is not None else None
        await db.execute(
            text(
                "INSERT INTO simulation_actions (id, simulation_id, "
                "preparation_id, platform_key, native_platform, round_index, "
                "action_seq, native_action, actor_role_id, actor_display_name, "
                "content, engine_record_id, engine_sequence, "
                "target_engine_record_id, target_action_id, target_platform_key, "
                "target_native_action, target_resolved, simulated_at) VALUES "
                "(:id, :simulation_id, :preparation_id, :platform_key, "
                ":native_platform, :round_index, :action_seq, :native_action, "
                ":actor_role_id, :actor_name, :content, :engine_record_id, "
                ":engine_sequence, :target_engine_record_id, :target_action_id, "
                ":target_platform_key, :target_native_action, :target_resolved, "
                ":simulated_at) ON CONFLICT (simulation_id, platform_key, "
                "engine_record_id) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "simulation_id": simulation.id,
                "preparation_id": simulation.preparation_id,
                "platform_key": platform_key,
                "native_platform": PLATFORM_MAPPING[platform_key],
                "round_index": round_index,
                "action_seq": seq,
                "native_action": record["nativeAction"],
                "actor_role_id": record["actorRoleId"],
                "actor_name": record["actorName"],
                "content": record.get("content") or "",
                "engine_record_id": record["engineRecordId"],
                "engine_sequence": record.get("engineSequence"),
                "target_engine_record_id": target_engine_record_id,
                "target_action_id": target_action_id,
                "target_platform_key": (
                    platform_key if target_engine_record_id else None
                ),
                "target_native_action": record.get("targetNativeAction"),
                "target_resolved": target_action_id is not None,
                "simulated_at": simulated_at,
            },
        )
    counts = await aggregate_counts(db, simulation_id=simulation.id)
    per_platform = counts[2].get(platform_key, {})
    await db.execute(
        text(
            "UPDATE simulation_platforms SET status = 'running', "
            "rounds_completed = CASE WHEN :advance THEN "
            "GREATEST(rounds_completed, :round_index) ELSE rounds_completed END, "
            "action_count = :action_count, "
            "by_action = CAST(:by_action AS jsonb), updated_at = clock_timestamp() "
            "WHERE simulation_id = :simulation_id AND platform_key = :platform_key"
        ),
        {
            "round_index": round_index,
            "action_count": sum(per_platform.values()),
            "by_action": json.dumps(per_platform, ensure_ascii=False),
            "simulation_id": simulation.id,
            "platform_key": platform_key,
            "advance": outcome == "committed",
        },
    )
    return len(records)


async def recent_posts(
    db: AsyncSession, *, simulation_id: uuid.UUID, platform_key: str
) -> list[dict[str, Any]]:
    """已提交的帖子动作，供下一轮规划目标原帖。"""
    result = await db.execute(
        text(
            "SELECT engine_record_id, actor_role_id FROM simulation_actions "
            "WHERE simulation_id = :simulation_id AND platform_key = :platform_key "
            "AND native_action = 'create_post' ORDER BY action_seq"
        ),
        {"simulation_id": simulation_id, "platform_key": platform_key},
    )
    return [
        {
            "engineRecordId": row.engine_record_id,
            "roleId": str(row.actor_role_id),
        }
        for row in result.fetchall()
    ]


async def platform_rounds(
    db: AsyncSession, *, simulation_id: uuid.UUID, platform_key: str
) -> int:
    """已正常完成的轮次；未确认或超时的轮次记录存在但不推进游标。"""
    result = await db.execute(
        text(
            "SELECT COALESCE(rounds_completed, 0) FROM simulation_platforms "
            "WHERE simulation_id = :simulation_id AND platform_key = :platform_key"
        ),
        {"simulation_id": simulation_id, "platform_key": platform_key},
    )
    return int(result.scalar_one_or_none() or 0)


async def record_engine_calls(
    db: AsyncSession, *, simulation_id: uuid.UUID, mode: str, calls: int = 1
) -> None:
    column = "provider_calls_real" if mode == "oasis" else "provider_calls_double"
    await db.execute(
        text(
            f"UPDATE simulations SET {column} = {column} + :calls, "
            "updated_at = clock_timestamp() WHERE id = :simulation_id"
        ),
        {"calls": calls, "simulation_id": simulation_id},
    )
