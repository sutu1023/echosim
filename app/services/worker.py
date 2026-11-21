"""worker 执行层：领取、续租、单步事务与终态写入。

所有写操作都限定 `jobs.id` 与本次领取的 `lease_token`，并用数据库 `now()` 判断
租约是否仍然有效；旧令牌不可能续活租约，也不可能写入任何进度或事件。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import LeaseLost
from . import document_extract
from . import document_storage
from . import run_events
from .documents import INGEST_TOTAL_STEPS, failure_message

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)
TERMINAL_STATUSES = ("succeeded", "failed", "canceled")
LEASE_SECONDS_PARAM = "CAST(:lease_seconds AS double precision)"

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

CLAIM_SQL = """
WITH candidate AS (
    SELECT id FROM jobs
     WHERE (
            status = 'queued'
            OR (
                status = 'running'
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at <= clock_timestamp()
            )
           )
       AND (CAST(:job_id AS uuid) IS NULL OR id = CAST(:job_id AS uuid))
     ORDER BY created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1
)
UPDATE jobs j
   SET status = 'running',
       lease_owner = :worker_id,
       lease_token = gen_random_uuid(),
       lease_expires_at = clock_timestamp() + make_interval(secs => %s),
       attempts = attempts + 1,
       started_at = COALESCE(started_at, now()),
       updated_at = now()
  FROM candidate c, runs r
 WHERE j.id = c.id AND r.id = j.run_id
RETURNING j.id, j.run_id, j.next_step, j.attempts, j.lease_token,
          r.total_steps, r.stage, r.fail_at_step, r.simulated_epoch,
          r.kind, r.document_id
""" % LEASE_SECONDS_PARAM


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    run_id: uuid.UUID
    attempt: int
    lease_token: uuid.UUID
    next_step: int
    total_steps: int
    stage: str
    fail_at_step: int | None
    simulated_epoch: datetime
    kind: str
    document_id: uuid.UUID | None


@dataclass(frozen=True)
class StepOutcome:
    kind: str
    next_step: int


def _as_claim(row: object) -> Claim:
    return Claim(
        job_id=row.id,  # type: ignore[attr-defined]
        run_id=row.run_id,  # type: ignore[attr-defined]
        attempt=int(row.attempts),  # type: ignore[attr-defined]
        lease_token=row.lease_token,  # type: ignore[attr-defined]
        next_step=int(row.next_step),  # type: ignore[attr-defined]
        total_steps=int(row.total_steps),  # type: ignore[attr-defined]
        stage=row.stage,  # type: ignore[attr-defined]
        fail_at_step=row.fail_at_step,  # type: ignore[attr-defined]
        simulated_epoch=row.simulated_epoch,  # type: ignore[attr-defined]
        kind=str(getattr(row, "kind", "test.simulation")),
        document_id=getattr(row, "document_id", None),
    )


async def claim_next(
    db: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: float,
    job_id: uuid.UUID | None = None,
) -> Claim | None:
    """领取一个 `queued` 或租约已过期的 `running` 任务并生成新租约令牌。"""
    try:
        result = await db.execute(
            text(CLAIM_SQL),
            {
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                "job_id": job_id,
            },
        )
        row = result.first()
        if row is None:
            await db.commit()
            return None
        claim = _as_claim(row)
        await db.commit()
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("领取任务失败: %s", type(exc).__name__)
        return None
    logger.info(
        "领取任务 job_id=%s run_id=%s attempt=%s next_step=%s worker=%s",
        claim.job_id,
        claim.run_id,
        claim.attempt,
        claim.next_step,
        worker_id,
    )
    return claim


async def heartbeat(db: AsyncSession, claim: Claim, *, lease_seconds: float) -> bool:
    """续租；租约已过期或令牌不符时返回 False（过期心跳不能续活）。

    必须先锁住任务行，再用单独的语句读取 ``clock_timestamp()``：事务内的 ``now()``
    固定为事务开始时间，若请求在到期前开始、在持锁等待中跨过到期点，``now()`` 会让
    过期心跳错误地续租。读取失败时整事务回滚，租约不被延长。
    """
    try:
        locked = await db.execute(
            text(
                "SELECT status, lease_token, lease_expires_at FROM jobs "
                "WHERE id = :job_id FOR UPDATE"
            ),
            {"job_id": claim.job_id},
        )
        row = locked.first()
        db_now = await _db_now(db)
        if row is None or not _lease_valid(row, claim, db_now):
            await db.rollback()
            logger.warning(
                "租约续期被拒绝 job_id=%s attempt=%s", claim.job_id, claim.attempt
            )
            return False
        updated = await db.execute(
            text(
                "UPDATE jobs SET lease_expires_at = clock_timestamp() + make_interval("
                "secs => " + LEASE_SECONDS_PARAM + "), updated_at = clock_timestamp() "
                "WHERE id = :job_id AND lease_token = :lease_token "
                "AND status = 'running' RETURNING id"
            ),
            {
                "job_id": claim.job_id,
                "lease_token": claim.lease_token,
                "lease_seconds": lease_seconds,
            },
        )
        alive = updated.first() is not None
        if not alive:
            await db.rollback()
            return False
        await db.commit()
        return True
    except DB_ERRORS:
        await db.rollback()
        raise


async def _locked_job(db: AsyncSession, claim: Claim) -> object | None:
    """锁住目标任务行；租约是否有效留到持锁后单独读时钟再判断。"""
    result = await db.execute(
        text(
            "SELECT j.status, j.lease_token, j.lease_expires_at, "
            "j.next_step, r.total_steps, r.stage, r.fail_at_step, r.simulated_epoch "
            "FROM jobs j JOIN runs r ON r.id = j.run_id "
            "WHERE j.id = :job_id FOR UPDATE OF j"
        ),
        {"job_id": claim.job_id},
    )
    return result.first()


async def _db_now(db: AsyncSession) -> datetime:
    """持锁之后读取真实数据库时钟；`now()` 是事务开始时间，不能用于本次判断。"""
    result = await db.execute(text("SELECT clock_timestamp() AS db_now"))
    return result.scalar_one()


def _lease_valid(row: object, claim: Claim, db_now: datetime) -> bool:
    """令牌、状态与到期时间三者同时满足才算持有租约。"""
    if row.status in TERMINAL_STATUSES:  # type: ignore[attr-defined]
        return False
    expires_at = row.lease_expires_at  # type: ignore[attr-defined]
    return (
        row.status == "running"  # type: ignore[attr-defined]
        and row.lease_token == claim.lease_token  # type: ignore[attr-defined]
        and expires_at is not None
        and expires_at > db_now
    )


async def commit_step(
    db: AsyncSession, claim: Claim, *, step: int, lease_seconds: float
) -> StepOutcome:
    """提交一步：守卫 → 副作用 → 事件 → 推进步号，全部在同一事务内。"""
    try:
        row = await _locked_job(db, claim)
        if row is None:
            await db.rollback()
            return StepOutcome("lost", claim.next_step)
        if row.status in TERMINAL_STATUSES:  # type: ignore[attr-defined]
            await db.rollback()
            return StepOutcome(str(row.status), int(row.next_step))  # type: ignore[attr-defined]
        # 先锁定、再读实际时钟：跨过到期点的等待不能让旧租约继续写入。
        db_now = await _db_now(db)
        if not _lease_valid(row, claim, db_now):
            await db.rollback()
            return StepOutcome("lost", claim.next_step)

        next_step = int(row.next_step)  # type: ignore[attr-defined]
        if next_step >= step:
            await db.rollback()
            return StepOutcome("duplicate", next_step)
        if next_step != step - 1:
            await db.rollback()
            return StepOutcome("lost", next_step)

        total_steps = int(row.total_steps)  # type: ignore[attr-defined]
        fail_at_step = row.fail_at_step  # type: ignore[attr-defined]
        if fail_at_step is not None and step == int(fail_at_step):
            seq = step
            await run_events.append_event(
                db,
                run_id=claim.run_id,
                seq=seq,
                type="failed",
                stage=str(row.stage),  # type: ignore[attr-defined]
                simulated_at=run_events.simulated_time(row.simulated_epoch, seq),  # type: ignore[attr-defined]
                payload={
                    "step": step,
                    "reason": "controlled_failure",
                    "stepsApplied": next_step,
                    "totalSteps": total_steps,
                },
            )
            await _finish(db, claim, next_step, status="failed", reason="controlled_failure")
            await db.commit()
            return StepOutcome("failed", next_step)

        inserted = await db.execute(
            text(
                "INSERT INTO run_steps (run_id, step_index, step_key, payload) "
                "VALUES (:run_id, :step, :step_key, CAST(:payload AS jsonb)) "
                "ON CONFLICT (run_id, step_index) DO NOTHING"
            ),
            {
                "run_id": claim.run_id,
                "step": step,
                "step_key": f"step-{step:02d}",
                "payload": json.dumps({"step": step}, ensure_ascii=False),
            },
        )
        if inserted.rowcount != 1:
            await db.rollback()
            return StepOutcome("duplicate", next_step)

        terminal = step == total_steps
        event_type = "completed" if terminal else "progress"
        await run_events.append_event(
            db,
            run_id=claim.run_id,
            seq=step,
            type=event_type,
            stage=str(row.stage),  # type: ignore[attr-defined]
            simulated_at=run_events.simulated_time(row.simulated_epoch, step),  # type: ignore[attr-defined]
            payload={
                "step": step,
                "labels": f"步骤 {step}/{total_steps}",
                "stepsApplied": step,
                "totalSteps": total_steps,
            },
        )
        if terminal:
            await _finish(db, claim, step, status="succeeded", reason=None)
        else:
            updated = await db.execute(
                text(
                    "UPDATE jobs SET next_step = :step, "
                    "lease_expires_at = clock_timestamp() + make_interval(secs => "
                    + LEASE_SECONDS_PARAM
                    + "), updated_at = clock_timestamp() "
                    "WHERE id = :job_id AND lease_token = :lease_token "
                    "AND status = 'running'"
                ),
                {
                    "step": step,
                    "job_id": claim.job_id,
                    "lease_token": claim.lease_token,
                    "lease_seconds": lease_seconds,
                },
            )
            if updated.rowcount != 1:
                await db.rollback()
                return StepOutcome("lost", next_step)
        await db.commit()
        return StepOutcome(event_type, step)
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning(
            "步骤提交失败 job_id=%s step=%s: %s", claim.job_id, step, type(exc).__name__
        )
        raise


async def _finish(
    db: AsyncSession,
    claim: Claim,
    next_step: int,
    *,
    status: str,
    reason: str | None,
) -> None:
    result = await db.execute(
        text(
            "UPDATE jobs SET status = :status, next_step = :next_step, "
            "finished_at = clock_timestamp(), terminal_reason = :reason, "
            "lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL, "
            "updated_at = clock_timestamp() "
            "WHERE id = :job_id AND lease_token = :lease_token AND status = 'running'"
        ),
        {
            "status": status,
            "next_step": next_step,
            "reason": reason,
            "job_id": claim.job_id,
            "lease_token": claim.lease_token,
        },
    )
    if result.rowcount != 1:
        raise LeaseLost(f"任务 {claim.job_id} 的租约已失效")




# ---------------------------------------------------------------- 材料解析

INGEST_STEP_KEYS = ("read", "clean", "index")


@dataclass(frozen=True)
class IngestArtifacts:
    chunks: tuple[document_extract.Chunk, ...]
    page_count: int | None
    text_encoding: str | None


def _compute_artifacts(
    *,
    kind: str,
    data: bytes,
    max_pages: int,
    chunk_target: int,
    chunk_overlap: int,
    delay_ms: int,
) -> IngestArtifacts:
    """在工作线程里完成同步提取；delay 用于验收放大慢解析窗口。"""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)
    if kind == "pdf":
        pdf = document_extract.parse_pdf(data, max_pages=max_pages)
        chunks = document_extract.chunk_pdf(
            pdf, target=chunk_target, overlap=chunk_overlap
        )
        return IngestArtifacts(tuple(chunks), len(pdf.pages), None)
    text = document_extract.parse_text(data)
    chunks = document_extract.chunk_text(
        text, target=chunk_target, overlap=chunk_overlap
    )
    return IngestArtifacts(tuple(chunks), None, text.encoding)


def ingest_failure(exc: BaseException) -> tuple[str, str]:
    """把提取异常映射为稳定的失败码与面向用户的说明。"""
    if isinstance(exc, document_extract.DecodeFailed):
        return "decode_failed", failure_message("decode_failed")
    if isinstance(exc, document_extract.NoTextLayer):
        return "no_text_layer", failure_message("no_text_layer")
    if isinstance(exc, document_extract.TooManyPages):
        return "too_many_pages", failure_message(
            "too_many_pages", pages=exc.pages, limit=exc.limit
        )
    return "parse_error", failure_message("parse_error")


async def _load_document_meta(db: AsyncSession, claim: Claim) -> object | None:
    result = await db.execute(
        text("SELECT id, kind, storage_path FROM documents WHERE id = :document_id"),
        {"document_id": claim.document_id},
    )
    return result.first()


async def _mark_parsing(db: AsyncSession, claim: Claim) -> None:
    await db.execute(
        text(
            "UPDATE documents SET status = 'parsing', updated_at = now() "
            "WHERE id = :document_id AND status IN ('queued', 'parsing')"
        ),
        {"document_id": claim.document_id},
    )
    await db.commit()


async def _heartbeat_loop(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    lease_seconds: float,
    heartbeat_seconds: float,
    stop: asyncio.Event,
) -> None:
    """计算期间独立续租；失去租约或数据库故障时安静退出。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=heartbeat_seconds)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            return
        try:
            async with session_factory() as db:
                if not await heartbeat(db, claim, lease_seconds=lease_seconds):
                    return
        except DB_ERRORS:
            return


async def _publish_chunks(
    db: AsyncSession, claim: Claim, artifacts: IngestArtifacts
) -> None:
    await db.execute(
        text("DELETE FROM document_chunks WHERE document_id = :document_id"),
        {"document_id": claim.document_id},
    )
    if artifacts.chunks:
        await db.execute(
            text(
                "INSERT INTO document_chunks (document_id, chunk_index, page_no, "
                "paragraph_start, paragraph_end, source_start, source_end, text) "
                "VALUES (:document_id, :chunk_index, :page_no, :paragraph_start, "
                ":paragraph_end, :source_start, :source_end, :text)"
            ),
            [
                {
                    "document_id": claim.document_id,
                    "chunk_index": chunk.index,
                    "page_no": chunk.page_no,
                    "paragraph_start": chunk.paragraph_start,
                    "paragraph_end": chunk.paragraph_end,
                    "source_start": chunk.source_start,
                    "source_end": chunk.source_end,
                    "text": chunk.text,
                }
                for chunk in artifacts.chunks
            ],
        )
    updated = await db.execute(
        text(
            "UPDATE documents SET status = 'ready', page_count = :page_count, "
            "chunk_count = :chunk_count, text_encoding = :text_encoding, "
            "failure_code = NULL, failure_message = NULL, "
            "ready_at = clock_timestamp(), updated_at = clock_timestamp() "
            "WHERE id = :document_id AND status IN ('queued', 'parsing')"
        ),
        {
            "document_id": claim.document_id,
            "page_count": artifacts.page_count,
            "chunk_count": len(artifacts.chunks),
            "text_encoding": artifacts.text_encoding,
        },
    )
    if updated.rowcount != 1:
        raise LeaseLost(f"材料 {claim.document_id} 已不在可发布状态")


async def _commit_ingest_step(
    db: AsyncSession,
    claim: Claim,
    *,
    step: int,
    step_key: str,
    artifacts: IngestArtifacts,
    lease_seconds: float,
) -> str:
    """提交一个解析步骤；只有最后一步会原子发布分块与 ready。"""
    try:
        row = await _locked_job(db, claim)
        if row is None:
            await db.rollback()
            return "lost"
        if row.status in TERMINAL_STATUSES:  # type: ignore[attr-defined]
            await db.rollback()
            return str(row.status)  # type: ignore[attr-defined]
        db_now = await _db_now(db)
        if not _lease_valid(row, claim, db_now):
            await db.rollback()
            return "lost"
        next_step = int(row.next_step)  # type: ignore[attr-defined]
        if next_step >= step:
            await db.rollback()
            return "duplicate"
        if next_step != step - 1:
            await db.rollback()
            return "lost"
        inserted = await db.execute(
            text(
                "INSERT INTO run_steps (run_id, step_index, step_key, payload) "
                "VALUES (:run_id, :step, :step_key, '{}'::jsonb) "
                "ON CONFLICT (run_id, step_index) DO NOTHING"
            ),
            {"run_id": claim.run_id, "step": step, "step_key": f"ingest-{step_key}"},
        )
        if inserted.rowcount != 1:
            await db.rollback()
            return "duplicate"

        terminal = step >= claim.total_steps
        event_type = "completed" if terminal else "progress"
        await run_events.append_event(
            db,
            run_id=claim.run_id,
            seq=step,
            type=event_type,
            stage=claim.stage,
            simulated_at=run_events.simulated_time(claim.simulated_epoch, step),
            payload={
                "step": step,
                "stepKey": step_key,
                "chunks": len(artifacts.chunks),
                "totalSteps": claim.total_steps,
            },
        )
        if terminal:
            await _publish_chunks(db, claim, artifacts)
            await _finish(db, claim, step, status="succeeded", reason=None)
        else:
            updated = await db.execute(
                text(
                    "UPDATE jobs SET next_step = :step, "
                    "lease_expires_at = clock_timestamp() + make_interval(secs => "
                    + LEASE_SECONDS_PARAM
                    + "), updated_at = clock_timestamp() "
                    "WHERE id = :job_id AND lease_token = :lease_token "
                    "AND status = 'running'"
                ),
                {
                    "step": step,
                    "job_id": claim.job_id,
                    "lease_token": claim.lease_token,
                    "lease_seconds": lease_seconds,
                },
            )
            if updated.rowcount != 1:
                await db.rollback()
                return "lost"
        await db.commit()
        return event_type
    except DB_ERRORS:
        await db.rollback()
        raise


async def _commit_ingest_failure(
    db: AsyncSession,
    claim: Claim,
    *,
    code: str,
    message: str,
    page_count: int | None = None,
    failure_detail: dict[str, object] | None = None,
) -> str:
    """失败、终态事件与材料失败同事务写入。"""
    try:
        row = await _locked_job(db, claim)
        if row is None:
            await db.rollback()
            return "lost"
        if row.status in TERMINAL_STATUSES:  # type: ignore[attr-defined]
            await db.rollback()
            return str(row.status)  # type: ignore[attr-defined]
        db_now = await _db_now(db)
        if not _lease_valid(row, claim, db_now):
            await db.rollback()
            return "lost"
        next_step = int(row.next_step)  # type: ignore[attr-defined]
        seq = next_step + 1
        await run_events.append_event(
            db,
            run_id=claim.run_id,
            seq=seq,
            type="failed",
            stage=claim.stage,
            simulated_at=run_events.simulated_time(claim.simulated_epoch, seq),
            payload={
                "reason": code,
                "stepsApplied": next_step,
                "totalSteps": claim.total_steps,
            },
        )
        await db.execute(
            text(
                "UPDATE documents SET status = 'failed', failure_code = :code, "
                "failure_message = :message, chunk_count = 0, "
                "page_count = :page_count, "
                "failure_detail = CAST(:failure_detail AS jsonb), "
                "ready_at = NULL, updated_at = now() "
                "WHERE id = :document_id AND status IN ('queued', 'parsing')"
            ),
            {
                "document_id": claim.document_id,
                "code": code,
                "message": message,
                "page_count": page_count,
                "failure_detail": json.dumps(
                    failure_detail or {}, ensure_ascii=False
                ),
            },
        )
        await _finish(db, claim, next_step, status="failed", reason=code)
        await db.commit()
        return "failed"
    except DB_ERRORS:
        await db.rollback()
        raise


async def execute_ingest(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    storage_root: Path,
    max_pages: int,
    chunk_target: int,
    chunk_overlap: int,
    parse_delay_ms: int,
) -> str:
    """解析材料：读取、清洗、分块都在内存完成，只有最后一步发布分块。"""
    stop_heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            claim,
            session_factory=session_factory,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            stop=stop_heartbeat,
        )
    )
    try:
        async with session_factory() as db:
            meta = await _load_document_meta(db, claim)
            await db.commit()
            if meta is not None:
                await _mark_parsing(db, claim)
        if meta is None:
            async with session_factory() as db:
                return await _commit_ingest_failure(
                    db,
                    claim,
                    code="original_missing",
                    message=failure_message("original_missing"),
                )
        try:
            data = await asyncio.to_thread(
                document_storage.read_bytes, storage_root, meta.storage_path
            )
        except (OSError, ValueError):
            async with session_factory() as db:
                return await _commit_ingest_failure(
                    db,
                    claim,
                    code="original_missing",
                    message=failure_message("original_missing"),
                )
        try:
            artifacts = await asyncio.to_thread(
                _compute_artifacts,
                kind=str(meta.kind),
                data=data,
                max_pages=max_pages,
                chunk_target=chunk_target,
                chunk_overlap=chunk_overlap,
                delay_ms=parse_delay_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 解析失败是业务终态
            code, message = ingest_failure(exc)
            page_count = (
                exc.pages
                if isinstance(exc, document_extract.TooManyPages)
                else None
            )
            failure_detail = (
                {"pages": exc.pages, "limit": exc.limit}
                if isinstance(exc, document_extract.TooManyPages)
                else {}
            )
            async with session_factory() as db:
                return await _commit_ingest_failure(
                    db,
                    claim,
                    code=code,
                    message=message,
                    page_count=page_count,
                    failure_detail=failure_detail,
                )

        if stop_event is not None and stop_event.is_set():
            return "stopped"
        outcome = "succeeded"
        for step in range(claim.next_step + 1, claim.total_steps + 1):
            async with session_factory() as db:
                outcome = await _commit_ingest_step(
                    db,
                    claim,
                    step=step,
                    step_key=INGEST_STEP_KEYS[step - 1],
                    artifacts=artifacts,
                    lease_seconds=lease_seconds,
                )
            if outcome not in ("progress", "completed"):
                return outcome
        return "succeeded"
    finally:
        stop_heartbeat.set()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 收尾不掩盖主流程
            pass

async def execute_job(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None = None,
    lease_seconds: float,
    heartbeat_seconds: float,
    step_delay_seconds: float,
) -> str:
    """从 `next_step+1` 开始逐步执行，返回最终结局标识。"""
    step = claim.next_step + 1
    last_heartbeat = asyncio.get_running_loop().time()
    while step <= claim.total_steps:
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        if step_delay_seconds > 0:
            await asyncio.sleep(step_delay_seconds)
        async with session_factory() as db:
            outcome = await commit_step(
                db, claim, step=step, lease_seconds=lease_seconds
            )
        if outcome.kind in ("progress", "duplicate"):
            step = outcome.next_step + 1
        else:
            logger.info(
                "任务结束 job_id=%s attempt=%s outcome=%s steps=%s",
                claim.job_id,
                claim.attempt,
                outcome.kind,
                outcome.next_step,
            )
            return outcome.kind

        now = asyncio.get_running_loop().time()
        if now - last_heartbeat >= heartbeat_seconds:
            async with session_factory() as db:
                if not await heartbeat(db, claim, lease_seconds=lease_seconds):
                    return "lost"
            last_heartbeat = now
    return "succeeded"
