"""任务服务：开始、列表、详情、取消与测试副作用查询。

同一个服务函数既被 HTTP 路由使用，也被验收探针使用。开始请求在事务内用 advisory
lock 串行化同一 `(project, operation_key)`，因此并发请求只会产生一对 job/run。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import JobNotFound, ServiceUnavailable
from . import documents, evidence_graph, run_events

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)

TEST_OPERATION = "test-simulation"
TEST_OPERATION_KEY = "test-simulation:v1"
TEST_KIND = "test.simulation"
TEST_TOTAL_STEPS = 10
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "canceled")
CANCEL_REASON = "canceled_by_user"

JOB_COLUMNS = (
    "j.id, j.run_id, j.project_id, j.status, j.next_step, j.attempts, "
    "j.lease_expires_at, j.terminal_reason, j.created_at, j.started_at, "
    "j.finished_at, j.cancel_requested_at, r.total_steps, r.stage, "
    "(j.status = 'running' AND j.lease_expires_at IS NOT NULL "
    " AND j.lease_expires_at <= now()) AS awaiting_takeover, "
    "COALESCE(GREATEST(0, EXTRACT(EPOCH FROM (now() - j.lease_expires_at))), 0)"
    " AS lease_expired_seconds"
)
JOB_SOURCE = "FROM jobs j JOIN runs r ON r.id = j.run_id"


@dataclass(frozen=True)
class Job:
    id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    next_step: int
    attempts: int
    total_steps: int
    stage: str
    lease_expires_at: datetime | None
    terminal_reason: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    awaiting_takeover: bool
    lease_expired_seconds: float

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def _as_job(row: object) -> Job:
    return Job(
        id=row.id,  # type: ignore[attr-defined]
        run_id=row.run_id,  # type: ignore[attr-defined]
        project_id=row.project_id,  # type: ignore[attr-defined]
        status=row.status,  # type: ignore[attr-defined]
        next_step=int(row.next_step),  # type: ignore[attr-defined]
        attempts=int(row.attempts),  # type: ignore[attr-defined]
        total_steps=int(row.total_steps),  # type: ignore[attr-defined]
        stage=row.stage,  # type: ignore[attr-defined]
        lease_expires_at=row.lease_expires_at,  # type: ignore[attr-defined]
        terminal_reason=row.terminal_reason,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        started_at=row.started_at,  # type: ignore[attr-defined]
        finished_at=row.finished_at,  # type: ignore[attr-defined]
        cancel_requested_at=row.cancel_requested_at,  # type: ignore[attr-defined]
        awaiting_takeover=bool(row.awaiting_takeover),  # type: ignore[attr-defined]
        lease_expired_seconds=round(float(row.lease_expired_seconds or 0), 3),  # type: ignore[attr-defined]
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def job_body(job: Job, *, deduplicated: bool | None = None) -> dict[str, object]:
    body: dict[str, object] = {
        "jobId": str(job.id),
        "runId": str(job.run_id),
        "projectId": str(job.project_id),
        "status": job.status,
        "stage": job.stage,
        "stepsApplied": job.next_step,
        "totalSteps": job.total_steps,
        "attempts": job.attempts,
        "awaitingTakeover": job.awaiting_takeover,
        "leaseExpiredSeconds": job.lease_expired_seconds,
        "terminalReason": job.terminal_reason,
        "createdAt": _iso(job.created_at),
        "startedAt": _iso(job.started_at),
        "finishedAt": _iso(job.finished_at),
        "cancelRequestedAt": _iso(job.cancel_requested_at),
    }
    if deduplicated is not None:
        body["deduplicated"] = deduplicated
    return body


def _parse_job_id(job_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(job_id)
    except (ValueError, AttributeError, TypeError):
        raise JobNotFound from None


async def _fetch_job(
    db: AsyncSession, *, project_id: uuid.UUID, job_id: uuid.UUID
) -> Job | None:
    result = await db.execute(
        text(
            f"SELECT {JOB_COLUMNS} {JOB_SOURCE} "
            "WHERE j.id = :job_id AND j.project_id = :project_id"
        ),
        {"job_id": job_id, "project_id": project_id},
    )
    row = result.first()
    return _as_job(row) if row is not None else None


async def _find_active(db: AsyncSession, *, project_id: uuid.UUID) -> Job | None:
    result = await db.execute(
        text(
            f"SELECT {JOB_COLUMNS} {JOB_SOURCE} "
            "WHERE j.project_id = :project_id AND j.operation_key = :operation_key "
            "AND j.status IN ('queued', 'running') "
            "ORDER BY j.created_at DESC LIMIT 1"
        ),
        {"project_id": project_id, "operation_key": TEST_OPERATION_KEY},
    )
    row = result.first()
    return _as_job(row) if row is not None else None


async def start_job(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    stage: str,
    fail_at_step: int | None,
) -> tuple[Job, bool]:
    """开始测试任务；返回 (任务, 是否新建)。

    同一 `(project, operation_key)` 在 advisory lock 内“先查后插”。命中活动任务时
    直接返回 200 语义且不插入任何行；冲突路径整体回滚，不会留下孤立 run。
    """
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    lock_key = f"{project_id}:{TEST_OPERATION_KEY}"
    try:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )
        existing = await _find_active(db, project_id=project_id)
        if existing is not None:
            await db.commit()
            return existing, False

        await db.execute(
            text(
                "INSERT INTO runs (id, project_id, created_by, kind, stage, "
                "total_steps, fail_at_step, simulated_epoch) "
                "VALUES (:id, :project_id, :created_by, :kind, :stage, "
                ":total_steps, :fail_at_step, now())"
            ),
            {
                "id": run_id,
                "project_id": project_id,
                "created_by": actor_id,
                "kind": TEST_KIND,
                "stage": stage,
                "total_steps": TEST_TOTAL_STEPS,
                "fail_at_step": fail_at_step,
            },
        )
        await db.execute(
            text(
                "INSERT INTO jobs (id, run_id, project_id, operation_key, status, "
                "next_step, attempts, lease_token) "
                "VALUES (:id, :run_id, :project_id, :operation_key, 'queued', 0, 0, NULL)"
            ),
            {
                "id": job_id,
                "run_id": run_id,
                "project_id": project_id,
                "operation_key": TEST_OPERATION_KEY,
            },
        )
        await db.commit()
    except IntegrityError as exc:
        # 理论上是 advisory lock 的最后防线：整体回滚，避免留下孤立 run。
        await db.rollback()
        logger.warning("开始任务冲突 project=%s: %s", project_id, type(exc).__name__)
        existing = await _find_active(db, project_id=project_id)
        if existing is None:
            raise ServiceUnavailable from exc
        return existing, False
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("开始任务失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc

    created = await _fetch_job(db, project_id=project_id, job_id=job_id)
    if created is None:  # pragma: no cover - 刚提交即消失只可能是并发删除
        raise ServiceUnavailable
    return created, True


async def list_jobs(
    db: AsyncSession, *, project_id: uuid.UUID, limit: int = 20
) -> list[Job]:
    try:
        result = await db.execute(
            text(
                f"SELECT {JOB_COLUMNS} {JOB_SOURCE} "
                "WHERE j.project_id = :project_id AND r.kind = 'test.simulation' "
                "ORDER BY j.created_at DESC, j.id DESC LIMIT :limit"
            ),
            {"project_id": project_id, "limit": limit},
        )
        rows = result.fetchall()
    except DB_ERRORS as exc:
        logger.warning("任务列表查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    return [_as_job(row) for row in rows]


async def get_job(
    db: AsyncSession, *, project_id: uuid.UUID, job_id: str | uuid.UUID
) -> Job:
    parsed = job_id if isinstance(job_id, uuid.UUID) else _parse_job_id(job_id)
    try:
        job = await _fetch_job(db, project_id=project_id, job_id=parsed)
    except DB_ERRORS as exc:
        logger.warning("任务详情查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    if job is None:
        raise JobNotFound
    return job


async def cancel_job(
    db: AsyncSession, *, project_id: uuid.UUID, job_id: str | uuid.UUID
) -> tuple[Job, bool]:
    """请求取消；返回 (任务, 本次取消是否生效)。终态任务只返回现状。"""
    parsed = job_id if isinstance(job_id, uuid.UUID) else _parse_job_id(job_id)
    try:
        result = await db.execute(
            text(
                "SELECT j.status, j.run_id, j.next_step, r.total_steps, r.stage, "
                "r.simulated_epoch, r.kind, r.document_id "
                "FROM jobs j JOIN runs r ON r.id = j.run_id "
                "WHERE j.id = :job_id AND j.project_id = :project_id "
                "FOR UPDATE OF j"
            ),
            {"job_id": parsed, "project_id": project_id},
        )
        row = result.first()
        if row is None:
            await db.rollback()
            raise JobNotFound
        if row.status in TERMINAL_STATUSES:
            await db.commit()
            return await get_job(db, project_id=project_id, job_id=parsed), False

        seq = await run_events.max_seq(db, run_id=row.run_id) + 1
        await run_events.append_event(
            db,
            run_id=row.run_id,
            seq=seq,
            type="canceled",
            stage=row.stage,
            simulated_at=run_events.simulated_time(row.simulated_epoch, seq),
            payload={
                "step": int(row.next_step),
                "reason": CANCEL_REASON,
                "stepsApplied": int(row.next_step),
                "totalSteps": int(row.total_steps),
            },
        )
        updated = await db.execute(
            text(
                "UPDATE jobs SET status = 'canceled', cancel_requested_at = now(), "
                "finished_at = now(), terminal_reason = :reason, lease_owner = NULL, "
                "lease_token = NULL, lease_expires_at = NULL, updated_at = now() "
                "WHERE id = :job_id AND status IN ('queued', 'running')"
            ),
            {"job_id": parsed, "reason": CANCEL_REASON},
        )
        if updated.rowcount != 1:
            # 极少数竞态：回滚连同刚追加的事件一起撤销，保持单一终态。
            await db.rollback()
            return await get_job(db, project_id=project_id, job_id=parsed), False
        if row.kind == "document.ingest" and row.document_id is not None:
            await documents.cancel_for_run(db, run_id=row.run_id)
        if row.kind == "evidence.graph.build":
            await evidence_graph.cancel_for_run(db, run_id=row.run_id)
        if row.kind == "agent.environment.prepare":
            from . import environment

            await environment.cancel_for_run(db, run_id=row.run_id)
        await db.commit()
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("取消任务失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc

    return await get_job(db, project_id=project_id, job_id=parsed), True


async def get_effects(
    db: AsyncSession, *, project_id: uuid.UUID, job_id: str | uuid.UUID
) -> dict[str, object]:
    """测试副作用的可查询、可计数视图。"""
    job = await get_job(db, project_id=project_id, job_id=job_id)
    try:
        result = await db.execute(
            text(
                "SELECT step_index FROM run_steps WHERE run_id = :run_id "
                "ORDER BY step_index"
            ),
            {"run_id": job.run_id},
        )
        steps = [int(row.step_index) for row in result.fetchall()]
    except DB_ERRORS as exc:
        logger.warning("副作用查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    distinct = sorted(set(steps))
    return {
        "runId": str(job.run_id),
        "jobId": str(job.id),
        "total": len(steps),
        "distinctSteps": len(distinct),
        "steps": distinct,
        "totalSteps": job.total_steps,
        "duplicated": len(steps) != len(distinct),
    }
