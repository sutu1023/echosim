"""worker 执行层：领取、续租、单步事务与终态写入。

所有写操作都限定 `jobs.id` 与本次领取的 `lease_token`，并用数据库 `now()` 判断
租约是否仍然有效；旧令牌不可能续活租约，也不可能写入任何进度或事件。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..engines.business_config import BusinessGraphConfig, BusinessGraphConfigError
from ..engines.business_contracts import GraphResult, SourceRef
from ..engines.business_graph import (
    BusinessGraphError,
    _classify_exception,
    _graph_uuid,
    _is_auth_error,
    _node_name,
    _node_uuid,
    assert_business_graph_scope,
    readback_graph,
    search_episode_once,
    write_episode,
)
from ..engines.business_model import (
    BusinessModelPlanner,
    ChunkInput,
    ExtractedBatch,
    extraction_to_payload,
    extractions_to_payload,
    plan_to_payload,
)
from ..errors import GraphBindingConflict, GraphLimitExceeded, LeaseLost
from . import document_extract
from . import document_storage
from . import evidence_graph as graph_store
from . import graph_source
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


class GraphWorkError(Exception):
    """建图 worker 步骤失败；`code` 为稳定原因码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GraphConfirmationAborted(Exception):
    """确认轮询因任务终态或租约失效停止。"""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


GRAPH_WRITE_MAX_ATTEMPTS = 2


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

GRAPH_STEP_KEYS = ("bind", "plan", "extract", "write", "readback", "publish")
GRAPH_BUILD_TOTAL_STEPS = graph_store.GRAPH_BUILD_TOTAL_STEPS


async def _graph_step_ready(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    expected_step: int,
) -> str:
    """外呼前复核：只有有效租约且 next_step 仍是前一步时才允许调用。"""
    async with session_factory() as db:
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
        if next_step >= expected_step:
            await db.rollback()
            return "duplicate"
        if next_step != expected_step - 1:
            await db.rollback()
            return "lost"
        await db.commit()
        return "ready"


async def _run_with_heartbeat(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    lease_seconds: float,
    heartbeat_seconds: float,
    func: Callable[..., Any],
    **kwargs: Any,
) -> Any:
    """同步 provider 调用放到线程中，期间独立续租。"""
    stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            claim,
            session_factory=session_factory,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            stop=stop,
        )
    )
    try:
        return await asyncio.to_thread(func, **kwargs)
    finally:
        stop.set()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


@asynccontextmanager
async def _heartbeat_scope(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    lease_seconds: float,
    heartbeat_seconds: float,
):
    """在慢搜索/读回期间持续续租，退出时收敛后台任务。"""
    stop = asyncio.Event()
    task = asyncio.create_task(
        _heartbeat_loop(
            claim,
            session_factory=session_factory,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            stop=stop,
        )
    )
    try:
        yield
    finally:
        stop.set()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def _commit_graph_step(
    db: AsyncSession,
    claim: Claim,
    *,
    step: int,
    step_key: str,
    payload: dict[str, object],
    lease_seconds: float,
    build_id: uuid.UUID,
    planned_episodes: int | None = None,
    before_commit: Callable[[AsyncSession], Any] | None = None,
    event_extra: dict[str, object] | None = None,
    terminal: bool = False,
    build_phase: str | None = None,
    can_continue: bool | None = None,
    event_seq_from_max: bool = False,
) -> str:
    """提交一个建图步骤：步骤、事件、步号与业务状态同事务。"""
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
                "VALUES (:run_id, :step, :step_key, CAST(:payload AS jsonb)) "
                "ON CONFLICT (run_id, step_index) DO NOTHING"
            ),
            {
                "run_id": claim.run_id,
                "step": step,
                "step_key": step_key,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )
        if inserted.rowcount != 1:
            await db.rollback()
            return "duplicate"
        event_payload: dict[str, object] = {
            "step": step,
            "stepKey": step_key,
            "buildId": str(build_id),
            "stepsApplied": step,
            "totalSteps": claim.total_steps,
            "phase": build_phase,
        }
        if event_extra:
            event_payload.update(event_extra)
        event_seq = (
            await run_events.max_seq(db, run_id=claim.run_id) + 1
            if event_seq_from_max
            else step
        )
        await run_events.append_event(
            db,
            run_id=claim.run_id,
            seq=event_seq,
            type="completed" if terminal else "progress",
            stage=claim.stage,
            simulated_at=run_events.simulated_time(claim.simulated_epoch, step),
            payload=event_payload,
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
                return "lost"
        if planned_episodes is not None:
            await db.execute(
                text(
                    "UPDATE graph_builds SET planned_episodes = :planned, "
                    "updated_at = clock_timestamp() WHERE id = :build_id"
                ),
                {"planned": planned_episodes, "build_id": build_id},
            )
        if build_phase is not None or can_continue is not None or terminal:
            await db.execute(
                text(
                    "UPDATE graph_builds SET "
                    "phase = COALESCE(:phase, phase), "
                    "can_continue = COALESCE(:can_continue, can_continue), "
                    "finished_at = CASE WHEN :terminal THEN clock_timestamp() "
                    "ELSE finished_at END, updated_at = clock_timestamp() "
                    "WHERE id = :build_id"
                ),
                {
                    "phase": build_phase,
                    "can_continue": can_continue,
                    "terminal": terminal,
                    "build_id": build_id,
                },
            )
        if before_commit is not None:
            await before_commit(db)
        await db.commit()
        return "succeeded" if terminal else "progress"
    except DB_ERRORS:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


async def _load_graph_step_payload(
    db: AsyncSession, *, run_id: uuid.UUID, step: int
) -> dict[str, object] | None:
    result = await db.execute(
        text(
            "SELECT payload::text AS payload FROM run_steps "
            "WHERE run_id = :run_id AND step_index = :step"
        ),
        {"run_id": run_id, "step": step},
    )
    row = result.first()
    if row is None:
        return None
    try:
        payload = json.loads(row.payload)
    except (TypeError, json.JSONDecodeError):
        raise GraphWorkError("graph_incomplete", "已提交步骤载荷不可读取")
    return payload if isinstance(payload, dict) else None


def _chunk_inputs(chunks: list[graph_store.BuildChunk]) -> list[ChunkInput]:
    return [
        ChunkInput(
            document_id=str(chunk.document_id),
            document_name=chunk.document_name,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            source_start=chunk.source_start,
            source_end=chunk.source_end,
            page_no=chunk.page_no,
            paragraph_start=chunk.paragraph_start,
            paragraph_end=chunk.paragraph_end,
        )
        for chunk in chunks
    ]


def _graph_episode_content(
    build_id: uuid.UUID, batch_key: str, batch_payload: dict[str, object]
) -> str:
    return json.dumps(
        {
            "buildId": str(build_id),
            "batchKey": batch_key,
            "nodes": batch_payload.get("nodes") or [],
            "edges": batch_payload.get("edges") or [],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


async def _mark_episode_attempt(
    db: AsyncSession,
    claim: Claim,
    *,
    episode: graph_store.GraphEpisode,
    build_id: uuid.UUID,
) -> str:
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
        if next_step > 3:
            await db.rollback()
            return "duplicate"
        if next_step != 3:
            await db.rollback()
            return "lost"
        await graph_store.record_episode(
            db,
            build_id=build_id,
            episode_key=episode.episode_key,
            content=episode.content,
            content_sha256=episode.content_sha256,
            state="planned",
            episode_uuid=None,
            attempts_increment=1,
            write_outcome="in_flight",
        )
        await db.commit()
        return "progress"
    except DB_ERRORS:
        await db.rollback()
        raise


async def _mark_episode_rejected(
    db: AsyncSession,
    claim: Claim,
    *,
    episode: graph_store.GraphEpisode,
    build_id: uuid.UUID,
) -> str:
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
        if next_step > 3:
            await db.rollback()
            return "duplicate"
        if next_step != 3:
            await db.rollback()
            return "lost"
        await graph_store.record_episode(
            db,
            build_id=build_id,
            episode_key=episode.episode_key,
            content=episode.content,
            content_sha256=episode.content_sha256,
            state="planned",
            episode_uuid=None,
            write_outcome="rejected_retryable",
        )
        await db.commit()
        return "progress"
    except DB_ERRORS:
        await db.rollback()
        raise


async def _persist_episode_state(
    db: AsyncSession,
    claim: Claim,
    *,
    episode: graph_store.GraphEpisode,
    build_id: uuid.UUID,
    state: str,
    episode_uuid: str,
    expected_next_step: int,
) -> str:
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
        if next_step > expected_next_step:
            await db.rollback()
            return "duplicate"
        if next_step != expected_next_step:
            await db.rollback()
            return "lost"
        await graph_store.record_episode(
            db,
            build_id=build_id,
            episode_key=episode.episode_key,
            content=episode.content,
            content_sha256=episode.content_sha256,
            state=state,
            episode_uuid=uuid.UUID(str(episode_uuid)),
            write_outcome="accepted",
        )
        phase = "waiting_index" if state == "written" else "drawing"
        if state == "written":
            await db.execute(
                text(
                    "UPDATE graph_builds SET phase = CASE "
                    "WHEN phase IN ('preparing', 'waiting_index') "
                    "THEN 'waiting_index' ELSE phase END, "
                    "updated_at = clock_timestamp() WHERE id = :build_id"
                ),
                {"build_id": build_id},
            )
        else:
            await db.execute(
                text(
                    "UPDATE graph_builds SET phase = CASE "
                    "WHEN phase <> 'ready' THEN 'drawing' ELSE phase END, "
                    "updated_at = clock_timestamp() WHERE id = :build_id"
                ),
                {"build_id": build_id},
            )
        await db.commit()
        return "progress"
    except DB_ERRORS:
        await db.rollback()
        raise


async def _search_episode_guarded(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    expected_step: int,
    stop_event: asyncio.Event | None,
    client: object,
    graph_id: str,
    episode: graph_store.GraphEpisode,
    expected_uuid: str | None,
) -> tuple[str, object | None]:
    if stop_event is not None and stop_event.is_set():
        return "stopped", None
    state = await _graph_step_ready(
        claim, session_factory=session_factory, expected_step=expected_step
    )
    if state != "ready":
        return state, None
    try:
        evidence = await asyncio.to_thread(
            search_episode_once,
            client,
            graph_id=graph_id,
            episode=episode,
            expected_uuid=expected_uuid,
        )
    except BusinessGraphError as exc:
        raise GraphWorkError(exc.code, exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        raise GraphWorkError("graph_unavailable", "episode 检索失败") from exc
    return "progress", evidence


async def execute_graph_bind(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
    graph_client_factory: Callable[[], Any],
) -> str:
    """bind：校验材料并持久化图谱绑定；响应结果在有效租约下提交。"""
    async with session_factory() as db:
        build = await graph_store.get_build_for_run(db, run_id=claim.run_id)
        documents = await graph_store.list_build_documents(
            db, build_id=build.id, project_id=build.project_id
        )
        await db.commit()
    if not documents:
        raise GraphWorkError("invalid_graph_scope", "建图未选择材料")
    if len(documents) > config.max_documents:
        raise GraphWorkError("too_many_documents", "建图材料超过上限")
    if any(document.status != "ready" for document in documents):
        raise GraphWorkError("document_not_ready", "建图材料尚未就绪")
    if stop_event is not None and stop_event.is_set():
        return "stopped"

    state = await _graph_step_ready(
        claim, session_factory=session_factory, expected_step=1
    )
    if state != "ready":
        return state
    async with session_factory() as db:
        binding = await graph_store.get_or_create_binding(
            db, project_id=build.project_id
        )
    if binding.graph_id is not None:
        assert_business_graph_scope(str(binding.graph_id), config=config)
        async with session_factory() as db:
            return await _commit_graph_step(
                db,
                claim,
                step=1,
                step_key=GRAPH_STEP_KEYS[0],
                payload={
                    "graphBound": True,
                    "reused": True,
                    "createAttempts": 0,
                },
                lease_seconds=lease_seconds,
                build_id=build.id,
                event_extra={
                    "phase": "preparing",
                    "plannedEpisodes": 0,
                    "acceptedEpisodes": 0,
                    "confirmedEpisodes": 0,
                },
                build_phase="preparing",
            )

    client = graph_client_factory()
    last_error: Exception | None = None
    for attempt in range(1, 3):
        state = await _graph_step_ready(
            claim, session_factory=session_factory, expected_step=1
        )
        if state != "ready":
            return state
        try:
            graph = await _run_with_heartbeat(
                claim,
                session_factory=session_factory,
                lease_seconds=lease_seconds,
                heartbeat_seconds=heartbeat_seconds,
                func=client.graph.create,
                name=binding.request_name,
                description=binding.request_description,
                time_zone=binding.request_time_zone,
                idempotency_key=str(binding.create_idempotency_key),
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            error = _classify_exception(exc)
            if _is_auth_error(error):
                raise GraphWorkError("auth_failed", "图谱创建认证失败") from exc
            if (error.response_lost or error.retryable) and attempt < 2:
                time.sleep(0.05)
                continue
            raise GraphWorkError("graph_unavailable", "图谱创建失败") from exc
        graph_id = _graph_uuid(graph)
        if not graph_id:
            raise GraphWorkError(
                "invalid_graph_response", "图谱创建返回空 UUID"
            )
        assert_business_graph_scope(graph_id, config=config)
        async with session_factory() as db:
            async def save_binding(session: AsyncSession) -> None:
                await graph_store.save_binding_graph_id(
                    session,
                    project_id=build.project_id,
                    graph_id=graph_id,
                    commit=False,
                )

            return await _commit_graph_step(
                db,
                claim,
                step=1,
                step_key=GRAPH_STEP_KEYS[0],
                payload={
                    "graphBound": True,
                    "reused": False,
                    "createAttempts": attempt,
                },
                lease_seconds=lease_seconds,
                build_id=build.id,
                before_commit=save_binding,
                event_extra={
                    "phase": "preparing",
                    "plannedEpisodes": 0,
                    "acceptedEpisodes": 0,
                    "confirmedEpisodes": 0,
                },
                build_phase="preparing",
            )
    raise GraphWorkError(
        "graph_unavailable", "图谱创建失败"
    ) from last_error


async def execute_graph_plan(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
    planner: BusinessModelPlanner,
) -> str:
    """plan：单独调用模型规划并提交类型与批次。"""
    state = await _graph_step_ready(
        claim, session_factory=session_factory, expected_step=2
    )
    if state != "ready":
        return state
    async with session_factory() as db:
        build = await graph_store.get_build_for_run(db, run_id=claim.run_id)
        chunks = await graph_store.list_build_chunks(
            db, build_id=build.id, project_id=build.project_id
        )
        await db.commit()
    if not chunks:
        raise GraphWorkError("invalid_graph_scope", "建图材料没有可用分块")
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    state = await _graph_step_ready(
        claim, session_factory=session_factory, expected_step=2
    )
    if state != "ready":
        return state
    inputs = _chunk_inputs(chunks)
    stage = await _run_with_heartbeat(
        claim,
        session_factory=session_factory,
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
        func=planner.plan,
        question=build.question,
        chunks=inputs,
        config=config,
    )
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    async with session_factory() as db:
        return await _commit_graph_step(
            db,
            claim,
            step=2,
            step_key=GRAPH_STEP_KEYS[1],
            payload=plan_to_payload(stage),
            lease_seconds=lease_seconds,
            build_id=build.id,
            planned_episodes=len(stage.batches),
            event_extra={
                "phase": "preparing",
                "plannedEpisodes": len(stage.batches),
                "acceptedEpisodes": 0,
                "confirmedEpisodes": 0,
            },
            build_phase="preparing",
        )


async def execute_graph_extract(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
    planner: BusinessModelPlanner,
) -> str:
    """extract：从已提交计划按批抽取并提交带 batchKey 的清单。"""
    state = await _graph_step_ready(
        claim, session_factory=session_factory, expected_step=3
    )
    if state != "ready":
        return state
    async with session_factory() as db:
        build = await graph_store.get_build_for_run(db, run_id=claim.run_id)
        chunks = await graph_store.list_build_chunks(
            db, build_id=build.id, project_id=build.project_id
        )
        plan_payload = await _load_graph_step_payload(
            db, run_id=claim.run_id, step=2
        )
        await db.commit()
    if plan_payload is None:
        raise GraphWorkError("graph_incomplete", "缺少已提交的规划")
    if not chunks:
        raise GraphWorkError("invalid_graph_scope", "建图材料没有可用分块")
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    inputs = _chunk_inputs(chunks)
    stage = planner.restore_plan(plan_payload, chunks=inputs, config=config)
    extracted_batches: list[ExtractedBatch] = []
    for batch in stage.batches:
        state = await _graph_step_ready(
            claim, session_factory=session_factory, expected_step=3
        )
        if state != "ready":
            return state
        extracted = await _run_with_heartbeat(
            claim,
            session_factory=session_factory,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            func=planner.extract_batch,
            question=build.question,
            entity_types=stage.entity_types,
            relation_types=stage.relation_types,
            batch=batch,
            chunks=inputs,
            config=config,
        )
        extracted_batches.append(extracted)
    nodes = tuple(
        node for extracted in extracted_batches for node in extracted.nodes
    )
    edges = tuple(
        edge for extracted in extracted_batches for edge in extracted.edges
    )
    graph = GraphResult(
        entity_types=stage.entity_types,
        relation_types=stage.relation_types,
        batches=stage.batches,
        nodes=nodes,
        edges=edges,
    )
    planner.validate_result(graph, config)
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    async with session_factory() as db:
        return await _commit_graph_step(
            db,
            claim,
            step=3,
            step_key=GRAPH_STEP_KEYS[2],
            payload=extractions_to_payload(extracted_batches),
            lease_seconds=lease_seconds,
            build_id=build.id,
            event_extra={
                "phase": "preparing",
                "plannedEpisodes": len(stage.batches),
                "acceptedEpisodes": 0,
                "confirmedEpisodes": 0,
            },
            build_phase="preparing",
        )


async def _prepare_graph_episodes(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    build_id: uuid.UUID,
    batch_payloads: list[tuple[str, dict[str, object]]],
    lease_seconds: float,
) -> tuple[str, list[graph_store.GraphEpisode]]:
    """先锁 job 校验租约与 next_step=3，再写全部计划 episode 与汇总。"""
    try:
        async with session_factory() as db:
            row = await _locked_job(db, claim)
            if row is None:
                await db.rollback()
                return "lost", []
            if row.status in TERMINAL_STATUSES:  # type: ignore[attr-defined]
                await db.rollback()
                return str(row.status), []  # type: ignore[attr-defined]
            db_now = await _db_now(db)
            if not _lease_valid(row, claim, db_now):
                await db.rollback()
                return "lost", []
            next_step = int(row.next_step)  # type: ignore[attr-defined]
            if next_step > 3:
                await db.rollback()
                return "duplicate", []
            if next_step != 3:
                await db.rollback()
                return "lost", []
            existing = await graph_store.list_episodes(db, build_id=build_id)
            existing_by_key = {item.episode_key: item for item in existing}
            for key, batch_payload in batch_payloads:
                if key in existing_by_key:
                    continue
                content = _graph_episode_content(build_id, key, batch_payload)
                await graph_store.record_episode(
                    db,
                    build_id=build_id,
                    episode_key=key,
                    content=content,
                    content_sha256=hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest(),
                    state="planned",
                )
            await db.commit()
            episodes = await graph_store.list_episodes(db, build_id=build_id)
            await db.commit()
        return "progress", episodes
    except DB_ERRORS:
        raise


async def _confirm_write_episode_by_search(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    client: object,
    graph_id: str,
    episode: graph_store.GraphEpisode,
    build_id: uuid.UUID,
    index_timeout_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str | None]:
    """丢响应后检索确认；每轮搜索前复核停止信号与租约。"""
    deadline = time.monotonic() + max(0.001, index_timeout_seconds)
    seen: dict[str, bool] = {}
    wrong_uuid_seen = False
    while True:
        outcome, evidence = await _search_episode_guarded(
            claim,
            session_factory=session_factory,
            expected_step=4,
            stop_event=stop_event,
            client=client,
            graph_id=graph_id,
            episode=episode,
            expected_uuid=None,
        )
        if outcome != "progress":
            return outcome, None
        if evidence is None:
            return "lost", None
        matches = getattr(evidence, "matches", {})
        seen.update(matches)
        wrong_uuid_seen = wrong_uuid_seen or bool(
            getattr(evidence, "wrong_uuid_seen", False)
        )
        if len(seen) > 1:
            raise GraphWorkError(
                "write_unconfirmed", "同一正文命中多个 episode UUID"
            )
        if seen:
            # 检索迟到于预算：不得用迟到的成功响应推进 episode 状态。
            if time.monotonic() > deadline:
                code = (
                    "stale_or_foreign_hit" if wrong_uuid_seen else "write_unconfirmed"
                )
                raise GraphWorkError(code, "episode 写入结果未能确认")
            matched_uuid = next(iter(seen))
            async with session_factory() as db:
                persisted = await _persist_episode_state(
                    db,
                    claim,
                    episode=episode,
                    build_id=build_id,
                    state="matched",
                    episode_uuid=matched_uuid,
                    expected_next_step=3,
                )
            if persisted != "progress":
                return persisted, None
            return "progress", matched_uuid
        if time.monotonic() > deadline:
            code = "write_unconfirmed"
            if wrong_uuid_seen:
                code = "stale_or_foreign_hit"
            raise GraphWorkError(code, "episode 写入结果未能确认")
        sleep(0.05)


async def execute_graph_write(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
    graph_client_factory: Callable[[], Any],
    before_prepare: Callable[[], Any] | None = None,
) -> str:
    """write：先持久化全部 episode，再按写结果状态继续 add 或检索确认。"""
    state = await _graph_step_ready(
        claim, session_factory=session_factory, expected_step=4
    )
    if state != "ready":
        return state
    async with session_factory() as db:
        build = await graph_store.get_build_for_run(db, run_id=claim.run_id)
        extraction_payload = await _load_graph_step_payload(
            db, run_id=claim.run_id, step=3
        )
        binding = await graph_store.require_binding(
            db, project_id=build.project_id
        )
        await db.commit()
    if extraction_payload is None:
        raise GraphWorkError("graph_incomplete", "缺少已提交的抽取清单")
    if binding.graph_id is None:
        raise GraphWorkError("graph_incomplete", "项目图谱绑定尚未完成")
    graph_id = str(binding.graph_id)
    assert_business_graph_scope(graph_id, config=config)
    raw_batches = extraction_payload.get("batches")
    if not isinstance(raw_batches, list):
        raise GraphWorkError("graph_incomplete", "抽取清单缺少批次")
    batch_payloads: list[tuple[str, dict[str, object]]] = []
    for raw in raw_batches:
        if not isinstance(raw, dict):
            raise GraphWorkError("graph_incomplete", "抽取批次不是对象")
        key = str(raw.get("batchKey") or "")
        if not key:
            raise GraphWorkError("graph_incomplete", "抽取批次缺少 batchKey")
        batch_payloads.append((key, raw))
    if not batch_payloads:
        raise GraphWorkError("graph_incomplete", "抽取清单为空")
    if before_prepare is not None:
        await before_prepare()
    status, episodes = await _prepare_graph_episodes(
        claim,
        session_factory=session_factory,
        build_id=build.id,
        batch_payloads=batch_payloads,
        lease_seconds=lease_seconds,
    )
    if status != "progress":
        return status
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    episode_by_key = {item.episode_key: item for item in episodes}
    missing = [key for key, _ in batch_payloads if key not in episode_by_key]
    if missing:
        raise GraphWorkError("graph_incomplete", "episode 持久化不完整")

    client = graph_client_factory()
    accepted: dict[str, str] = {}
    for key, _ in batch_payloads:
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        episode = episode_by_key[key]
        if (
            episode.write_outcome == "accepted"
            or (
                episode.state in ("written", "matched", "processed")
                and episode.episode_uuid is not None
            )
        ):
            if episode.episode_uuid is not None:
                accepted[key] = str(episode.episode_uuid)
            continue
        state = await _graph_step_ready(
            claim, session_factory=session_factory, expected_step=4
        )
        if state != "ready":
            return state

        if episode.write_outcome == "in_flight":
            try:
                async with _heartbeat_scope(
                    claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                ):
                    outcome, confirmed = await _confirm_write_episode_by_search(
                        claim,
                        session_factory=session_factory,
                        stop_event=stop_event,
                        client=client,
                        graph_id=graph_id,
                        episode=episode,
                        build_id=build.id,
                        index_timeout_seconds=config.index_timeout_seconds,
                    )
            except BusinessGraphError as exc:
                raise GraphWorkError(exc.code, exc.message) from exc
            if outcome != "progress" or confirmed is None:
                return outcome
            accepted[key] = confirmed
            continue

        if episode.write_outcome == "rejected_retryable":
            if episode.attempts >= GRAPH_WRITE_MAX_ATTEMPTS:
                raise GraphWorkError("graph_unavailable", "episode 重试次数已耗尽")
            attempts_left = GRAPH_WRITE_MAX_ATTEMPTS - episode.attempts
        else:
            attempts_left = GRAPH_WRITE_MAX_ATTEMPTS

        for _ in range(max(1, attempts_left)):
            if stop_event is not None and stop_event.is_set():
                return "stopped"
            state = await _graph_step_ready(
                claim, session_factory=session_factory, expected_step=4
            )
            if state != "ready":
                return state
            async with session_factory() as db:
                outcome = await _mark_episode_attempt(
                    db, claim, episode=episode, build_id=build.id
                )
            if outcome != "progress":
                return outcome
            try:
                written = await _run_with_heartbeat(
                    claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    func=write_episode,
                    client=client,
                    graph_id=graph_id,
                    episode=episode,
                    config=config,
                    max_attempts=1,
                    sleep=time.sleep,
                )
            except BusinessGraphError as exc:
                raise GraphWorkError(exc.code, exc.message) from exc
            if written.episode_uuid:
                async with session_factory() as db:
                    persisted = await _persist_episode_state(
                        db,
                        claim,
                        episode=episode,
                        build_id=build.id,
                        state="written",
                        episode_uuid=written.episode_uuid,
                        expected_next_step=3,
                    )
                if persisted != "progress":
                    return persisted
                accepted[key] = written.episode_uuid
                break
            if written.response_lost:
                try:
                    async with _heartbeat_scope(
                        claim,
                        session_factory=session_factory,
                        lease_seconds=lease_seconds,
                        heartbeat_seconds=heartbeat_seconds,
                    ):
                        outcome, confirmed = await _confirm_write_episode_by_search(
                            claim,
                            session_factory=session_factory,
                            stop_event=stop_event,
                            client=client,
                            graph_id=graph_id,
                            episode=episode,
                            build_id=build.id,
                            index_timeout_seconds=config.index_timeout_seconds,
                        )
                except BusinessGraphError as exc:
                    raise GraphWorkError(exc.code, exc.message) from exc
                if outcome != "progress" or confirmed is None:
                    return outcome
                accepted[key] = confirmed
                break
            # provider 明确拒绝且可重试：先落库该状态，再在剩余次数内重发。
            async with session_factory() as db:
                rejected = await _mark_episode_rejected(
                    db, claim, episode=episode, build_id=build.id
                )
            if rejected != "progress":
                return rejected
            if _ == max(1, attempts_left) - 1:
                raise GraphWorkError("graph_unavailable", "episode 未受理")
        else:
            raise GraphWorkError("graph_unavailable", "episode 未受理")

    async with session_factory() as db:
        final_episodes = await graph_store.list_episodes(db, build_id=build.id)
        db_now_epoch = float(
            (
                await db.execute(
                    text("SELECT extract(epoch FROM clock_timestamp())")
                )
            ).scalar_one()
        )
        await db.commit()
        confirmed = sum(
            1
            for item in final_episodes
            if item.state in ("matched", "processed")
        )
        write_phase = "drawing" if confirmed else "waiting_index"
        payload = {
            "episodes": [
                {
                    "episodeKey": item.episode_key,
                    "episodeUuid": str(item.episode_uuid),
                }
                for item in final_episodes
                if item.episode_uuid is not None
            ],
            "acceptedEpisodes": len(accepted),
            "indexDeadlineEpoch": db_now_epoch + config.index_timeout_seconds,
            "processingDeadlineEpoch": (
                db_now_epoch + config.processing_timeout_seconds
            ),
        }
        return await _commit_graph_step(
            db,
            claim,
            step=4,
            step_key=GRAPH_STEP_KEYS[3],
            payload=payload,
            lease_seconds=lease_seconds,
            build_id=build.id,
            event_extra={
                "phase": write_phase,
                "plannedEpisodes": len(final_episodes),
                "acceptedEpisodes": len(accepted),
                "confirmedEpisodes": confirmed,
                "snapshotRevision": None,
            },
            build_phase=write_phase,
        )


async def _confirm_one_episode(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    client: object,
    graph_id: str,
    episode: graph_store.GraphEpisode,
    build_id: uuid.UUID,
    index_deadline: float,
    processing_deadline: float,
    poll_interval_seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> str:
    if episode.state == "processed":
        return "progress"
    if episode.episode_uuid is None:
        raise GraphWorkError("graph_incomplete", "episode 缺少已确认 UUID")
    expected_uuid = str(episode.episode_uuid)
    state = episode.state
    seen: dict[str, bool] = {}
    wrong_uuid_seen = False
    while True:
        if stop_event is not None and stop_event.is_set():
            raise GraphConfirmationAborted("stopped")
        now = clock()
        if state == "written" and now > index_deadline:
            code = "stale_or_foreign_hit" if wrong_uuid_seen else "index_timeout"
            raise GraphWorkError(code, "episode 索引预算耗尽")
        if state == "matched" and now > processing_deadline:
            raise GraphWorkError(
                "processing_timeout", "episode 处理预算耗尽"
            )
        outcome, evidence = await _search_episode_guarded(
            claim,
            session_factory=session_factory,
            expected_step=5,
            stop_event=stop_event,
            client=client,
            graph_id=graph_id,
            episode=episode,
            expected_uuid=expected_uuid,
        )
        if outcome != "progress":
            raise GraphConfirmationAborted(outcome)
        if evidence is None:
            raise GraphWorkError("graph_incomplete", "episode 检索无结果")
        seen.update(getattr(evidence, "matches", {}))
        wrong_uuid_seen = wrong_uuid_seen or bool(
            getattr(evidence, "wrong_uuid_seen", False)
        )
        if len(seen) > 1:
            raise GraphWorkError(
                "write_unconfirmed", "同一正文命中多个 episode UUID"
            )
        if seen:
            now = clock()
            # 索引与处理预算各自独立：迟到的搜索/处理结果只能失败，不能推进状态。
            if state == "written" and now > index_deadline:
                code = (
                    "stale_or_foreign_hit" if wrong_uuid_seen else "index_timeout"
                )
                raise GraphWorkError(code, "episode 索引预算耗尽")
            if now > processing_deadline:
                raise GraphWorkError("processing_timeout", "episode 处理预算耗尽")
            matched_uuid, processed = next(iter(seen.items()))
            if state == "written":
                async with session_factory() as db:
                    persisted = await _persist_episode_state(
                        db,
                        claim,
                        episode=episode,
                        build_id=build_id,
                        state="matched",
                        episode_uuid=matched_uuid,
                        expected_next_step=4,
                    )
                if persisted != "progress":
                    raise GraphConfirmationAborted(persisted)
                state = "matched"
            if processed:
                async with session_factory() as db:
                    persisted = await _persist_episode_state(
                        db,
                        claim,
                        episode=episode,
                        build_id=build_id,
                        state="processed",
                        episode_uuid=matched_uuid,
                        expected_next_step=4,
                    )
                if persisted != "progress":
                    raise GraphConfirmationAborted(persisted)
                return "progress"
        now = clock()
        if now > processing_deadline:
            raise GraphWorkError(
                "processing_timeout", "episode 处理预算耗尽"
            )
        if state == "written" and now > index_deadline:
            code = "stale_or_foreign_hit" if wrong_uuid_seen else "index_timeout"
            raise GraphWorkError(code, "episode 索引预算耗尽")
        sleep(min(max(0.0, poll_interval_seconds), max(0.0, processing_deadline - now)))


async def execute_graph_confirm_episodes(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
    graph_client_factory: Callable[[], Any],
    poll_interval_seconds: float = 0.05,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """第 5 步确认部分：并发保存 matched/processed 证据，不提交 readback。"""
    state = await _graph_step_ready(
        claim, session_factory=session_factory, expected_step=5
    )
    if state != "ready":
        return state
    async with session_factory() as db:
        build = await graph_store.get_build_for_run(db, run_id=claim.run_id)
        binding = await graph_store.require_binding(
            db, project_id=build.project_id
        )
        write_payload = await _load_graph_step_payload(
            db, run_id=claim.run_id, step=4
        )
        episodes = await graph_store.list_episodes(db, build_id=build.id)
        await db.commit()
    if binding.graph_id is None:
        raise GraphWorkError("graph_incomplete", "项目图谱绑定尚未完成")
    if write_payload is None:
        raise GraphWorkError("graph_incomplete", "缺少已提交的写入结果")
    graph_id = str(binding.graph_id)
    assert_business_graph_scope(graph_id, config=config)
    now = clock()
    index_deadline = float(
        write_payload.get("indexDeadlineEpoch")
        or (now + config.index_timeout_seconds)
    )
    processing_deadline = float(
        write_payload.get("processingDeadlineEpoch")
        or (now + config.processing_timeout_seconds)
    )
    pending = [
        episode
        for episode in episodes
        if episode.state != "processed"
    ]
    if any(episode.episode_uuid is None for episode in pending):
        raise GraphWorkError("graph_incomplete", "episode 尚未写入受理")
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    client = graph_client_factory()
    tasks = [
        asyncio.create_task(
            _confirm_one_episode(
                claim,
                session_factory=session_factory,
                stop_event=stop_event,
                client=client,
                graph_id=graph_id,
                episode=episode,
                build_id=build.id,
                index_deadline=index_deadline,
                processing_deadline=processing_deadline,
                poll_interval_seconds=poll_interval_seconds,
                clock=clock,
                sleep=sleep,
            )
        )
        for episode in pending
    ]
    async with _heartbeat_scope(
        claim,
        session_factory=session_factory,
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
    ):
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, GraphConfirmationAborted):
            return result.outcome
        if isinstance(result, Exception):
            raise result
    return "progress"



@dataclass(frozen=True)
class ManifestNode:
    key: str
    label: str
    origin: str
    summary: str
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class ManifestEdge:
    key: str
    label: str
    origin: str
    summary: str
    source_key: str
    target_key: str
    sources: tuple[SourceRef, ...]


def _source_ref(raw: object) -> SourceRef:
    if not isinstance(raw, dict):
        raise GraphWorkError("graph_incomplete", "来源不是对象")
    try:
        return SourceRef(
            document_id=str(raw.get("documentId") or ""),
            chunk_index=int(raw.get("chunkIndex") or 0),
            quote=str(raw.get("quote") or ""),
            source_start=int(raw.get("sourceStart") or 0),
            source_end=int(raw.get("sourceEnd") or 0),
            page_no=(
                None if raw.get("pageNo") is None else int(raw.get("pageNo"))
            ),
            paragraph_start=(
                None
                if raw.get("paragraphStart") is None
                else int(raw.get("paragraphStart"))
            ),
            paragraph_end=(
                None
                if raw.get("paragraphEnd") is None
                else int(raw.get("paragraphEnd"))
            ),
        )
    except (TypeError, ValueError):
        raise GraphWorkError("graph_incomplete", "来源字段不可读取") from None


def _manifest_from_payload(
    payload: dict[str, object],
) -> tuple[list[ManifestNode], list[ManifestEdge]]:
    raw_batches = payload.get("batches")
    if not isinstance(raw_batches, list):
        raise GraphWorkError("graph_incomplete", "抽取清单缺少批次")
    nodes: list[ManifestNode] = []
    edges: list[ManifestEdge] = []
    seen_nodes: set[str] = set()
    seen_edges: set[str] = set()
    for raw in raw_batches:
        if not isinstance(raw, dict):
            raise GraphWorkError("graph_incomplete", "抽取批次不是对象")
        raw_nodes = raw.get("nodes")
        raw_edges = raw.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise GraphWorkError("graph_incomplete", "抽取批次缺少节点或关系")
        for item in raw_nodes:
            if not isinstance(item, dict):
                raise GraphWorkError("graph_incomplete", "节点不是对象")
            key = str(item.get("key") or "")
            if not key or key in seen_nodes:
                raise GraphWorkError("graph_incomplete", "节点键重复或为空")
            seen_nodes.add(key)
            sources = item.get("sources")
            nodes.append(
                ManifestNode(
                    key=key,
                    label=str(item.get("label") or ""),
                    origin=str(item.get("origin") or ""),
                    summary=str(item.get("summary") or ""),
                    sources=tuple(
                        _source_ref(source)
                        for source in (sources if isinstance(sources, list) else [])
                    ),
                )
            )
        for item in raw_edges:
            if not isinstance(item, dict):
                raise GraphWorkError("graph_incomplete", "关系不是对象")
            key = str(item.get("key") or "")
            if not key or key in seen_edges:
                raise GraphWorkError("graph_incomplete", "关系键重复或为空")
            seen_edges.add(key)
            sources = item.get("sources")
            edges.append(
                ManifestEdge(
                    key=key,
                    label=str(item.get("label") or ""),
                    origin=str(item.get("origin") or ""),
                    summary=str(item.get("summary") or ""),
                    source_key=str(item.get("sourceKey") or ""),
                    target_key=str(item.get("targetKey") or ""),
                    sources=tuple(
                        _source_ref(source)
                        for source in (sources if isinstance(sources, list) else [])
                    ),
                )
            )
    return nodes, edges


def _evidence_for(
    target_kind: str,
    target_key: str,
    sources: Sequence[SourceRef],
    resolved: dict[tuple[uuid.UUID, int, int, int], graph_source.ResolvedSource],
) -> list[graph_store.GraphEvidence]:
    evidence: list[graph_store.GraphEvidence] = []
    for index, source in enumerate(sources, start=1):
        try:
            document_id = uuid.UUID(str(source.document_id))
        except (ValueError, TypeError, AttributeError):
            raise GraphWorkError("source_mismatch", "来源材料标识非法") from None
        item = resolved.get(
            (
                document_id,
                int(source.chunk_index),
                int(source.source_start),
                int(source.source_end),
            )
        )
        if item is None:
            raise GraphWorkError("source_mismatch", "来源未通过校验")
        evidence.append(
            graph_store.GraphEvidence(
                target_kind=target_kind,
                target_key=target_key,
                evidence_index=index,
                document_id=item.document_id,
                chunk_index=item.chunk_index,
                page_no=item.page_no,
                paragraph_start=item.paragraph_start,
                paragraph_end=item.paragraph_end,
                source_start=item.source_start,
                source_end=item.source_end,
                quote=item.quote,
            )
        )
    return evidence


async def _materialize_snapshot(
    db: AsyncSession,
    *,
    build: graph_store.GraphBuild,
    chunks: list[graph_store.BuildChunk],
    manifest_nodes: list[ManifestNode],
    manifest_edges: list[ManifestEdge],
    readback_nodes: Sequence[object],
    relations: dict[str, object],
    storage_root: object,
    source_cache: dict[uuid.UUID, object] | None = None,
) -> tuple[
    tuple[graph_store.GraphNode, ...],
    tuple[graph_store.GraphEdge, ...],
    tuple[graph_store.GraphEvidence, ...],
]:
    readback_names = {_node_name(item) for item in readback_nodes if _node_uuid(item)}
    relation_keys = {
        str(getattr(item, "manifest_key", "")) for item in relations.values()
    }
    edge_keys = {key for key in relation_keys if key}
    nodes_by_key = {node.key: node for node in manifest_nodes}
    included_keys: set[str] = set()
    for node in manifest_nodes:
        if node.label and node.label in readback_names:
            included_keys.add(node.key)
    for edge in manifest_edges:
        if edge.key in edge_keys:
            included_keys.add(edge.source_key)
            included_keys.add(edge.target_key)
    included_nodes = [
        node for node in manifest_nodes if node.key in included_keys
    ]
    included_edges = [edge for edge in manifest_edges if edge.key in edge_keys]
    if any(
        edge.source_key not in nodes_by_key or edge.target_key not in nodes_by_key
        for edge in included_edges
    ):
        raise GraphWorkError("graph_incomplete", "读回关系端点不在清单中")
    sources = graph_source.material_sources(included_nodes, included_edges)
    try:
        resolved = await graph_source.resolve_sources(
            db,
            build_id=build.id,
            project_id=build.project_id,
            chunks=chunks,
            sources=sources,
            storage_root=storage_root,
            raw_cache=source_cache,
        )
    except graph_source.SourceMappingError as exc:
        raise GraphWorkError(exc.code, exc.message) from exc
    nodes = tuple(
        graph_store.GraphNode(
            key=node.key,
            node_type="entity",
            label=node.label,
            origin=node.origin,
            summary=node.summary or None,
        )
        for node in included_nodes
    )
    edges = tuple(
        graph_store.GraphEdge(
            key=edge.key,
            edge_type="relation",
            label=edge.label,
            origin=edge.origin,
            summary=edge.summary or None,
            source_key=edge.source_key,
            target_key=edge.target_key,
        )
        for edge in included_edges
    )
    evidence: list[graph_store.GraphEvidence] = []
    for node in included_nodes:
        evidence.extend(
            _evidence_for("node", node.key, node.sources, resolved)
        )
    for edge in included_edges:
        evidence.extend(
            _evidence_for("edge", edge.key, edge.sources, resolved)
        )
    return nodes, edges, tuple(evidence)


async def _next_snapshot_revision(
    db: AsyncSession, *, build_id: uuid.UUID
) -> int:
    result = await db.execute(
        text(
            "SELECT COALESCE(MAX(revision), 0) + 1 FROM graph_snapshots "
            "WHERE build_id = :build_id"
        ),
        {"build_id": build_id},
    )
    return int(result.scalar_one())


def _final_graph_reason(
    nodes: Sequence[graph_store.GraphNode],
    edges: Sequence[graph_store.GraphEdge],
    evidence: Sequence[graph_store.GraphEvidence],
) -> str | None:
    material_nodes = {node.key for node in nodes if node.origin == "material"}
    material_edges = {edge.key for edge in edges if edge.origin == "material"}
    if not nodes or not edges or not material_nodes or not material_edges:
        return "insufficient_graph"
    evidence_nodes = {item.target_key for item in evidence if item.target_kind == "node"}
    evidence_edges = {item.target_key for item in evidence if item.target_kind == "edge"}
    if not material_nodes.issubset(evidence_nodes):
        return "graph_incomplete"
    if not material_edges.issubset(evidence_edges):
        return "graph_incomplete"
    return None


def _readback_signature(
    nodes: Sequence[graph_store.GraphNode],
    edges: Sequence[graph_store.GraphEdge],
) -> tuple[object, ...]:
    return (
        tuple(
            sorted((node.key, node.label, node.origin) for node in nodes)
        ),
        tuple(
            sorted(
                (
                    edge.key,
                    edge.label,
                    edge.source_key,
                    edge.target_key,
                    edge.origin,
                )
                for edge in edges
            )
        ),
    )


async def execute_graph_readback(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
    graph_client_factory: Callable[[], Any],
    storage_root: object,
) -> str:
    """readback：已处理批次稳定后立即发布部分快照，全部完成后再提交第 5 步。"""
    state = await _graph_step_ready(
        claim, session_factory=session_factory, expected_step=5
    )
    if state != "ready":
        return state
    async with session_factory() as db:
        build = await graph_store.get_build_for_run(db, run_id=claim.run_id)
        binding = await graph_store.require_binding(
            db, project_id=build.project_id
        )
        extraction_payload = await _load_graph_step_payload(
            db, run_id=claim.run_id, step=3
        )
        write_payload = await _load_graph_step_payload(
            db, run_id=claim.run_id, step=4
        )
        episodes = await graph_store.list_episodes(db, build_id=build.id)
        chunks = await graph_store.list_build_chunks(
            db, build_id=build.id, project_id=build.project_id
        )
        await db.commit()
    if extraction_payload is None or write_payload is None:
        raise GraphWorkError("graph_incomplete", "缺少已提交的抽取或写入结果")
    if binding.graph_id is None:
        raise GraphWorkError("graph_incomplete", "项目图谱绑定尚未完成")
    if any(item.episode_uuid is None for item in episodes):
        raise GraphWorkError("graph_incomplete", "episode 缺少 UUID")
    manifest_nodes, manifest_edges = _manifest_from_payload(extraction_payload)
    deadline = float(
        write_payload.get("processingDeadlineEpoch")
        or (time.time() + config.processing_timeout_seconds)
    )
    if deadline - time.time() <= 0:
        raise GraphWorkError("processing_timeout", "处理预算耗尽")
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    graph_id = str(binding.graph_id)
    assert_business_graph_scope(graph_id, config=config)
    client = graph_client_factory()

    source_cache: dict[uuid.UUID, object] = {}
    last_revision: int | None = None
    last_signature: tuple[object, ...] | None = None
    last_attempted: frozenset[str] = frozenset()
    last_attempt_at = 0.0

    async def read_episodes() -> list[graph_store.GraphEpisode]:
        async with session_factory() as session:
            values = await graph_store.list_episodes(
                session, build_id=build.id
            )
            await session.commit()
            return values

    async def publish_partial(
        processed_uuids: Sequence[str],
        *,
        final: bool,
    ) -> int | None:
        nonlocal last_revision, last_signature
        if not processed_uuids:
            return last_revision
        remaining = deadline - time.time()
        if remaining <= 0:
            raise GraphWorkError("processing_timeout", "处理预算耗尽")
        budget = remaining if final else min(
            remaining, max(1.0, min(8.0, remaining))
        )
        try:
            readback = await asyncio.to_thread(
                readback_graph,
                client,
                graph_id=graph_id,
                episode_uuids=list(processed_uuids),
                processing_timeout_seconds=budget,
                manifest_nodes=manifest_nodes,
                manifest_edges=manifest_edges,
                page_size=100,
            )
        except BusinessGraphError as exc:
            if final:
                raise GraphWorkError(exc.code, exc.message) from exc
            return last_revision
        async with session_factory() as session:
            nodes, edges, evidence = await _materialize_snapshot(
                session,
                build=build,
                chunks=chunks,
                manifest_nodes=manifest_nodes,
                manifest_edges=manifest_edges,
                readback_nodes=readback.nodes,
                relations=readback.relations,
                storage_root=storage_root,
                source_cache=source_cache,
            )
        if not nodes or not edges:
            if final:
                raise GraphWorkError(
                    "graph_incomplete", "读回未形成材料节点与材料关系"
                )
            return last_revision
        signature = _readback_signature(nodes, edges)
        if signature == last_signature:
            return last_revision
        async with session_factory() as session:
            row = await _locked_job(session, claim)
            if row is None:
                await session.rollback()
                raise GraphConfirmationAborted("lost")
            if row.status in TERMINAL_STATUSES:  # type: ignore[attr-defined]
                await session.rollback()
                raise GraphConfirmationAborted(str(row.status))  # type: ignore[attr-defined]
            db_now = await _db_now(session)
            if not _lease_valid(row, claim, db_now):
                await session.rollback()
                raise GraphConfirmationAborted("lost")
            if int(row.next_step) >= 5:  # type: ignore[attr-defined]
                await session.rollback()
                raise GraphConfirmationAborted("duplicate")
            revision = await _next_snapshot_revision(
                session, build_id=build.id
            )
            await graph_store.create_snapshot(
                session,
                build_id=build.id,
                revision=revision,
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                complete=False,
                published=True,
                episode_count=len(processed_uuids),
            )
            await session.execute(
                text(
                    "UPDATE graph_builds SET phase = CASE "
                    "WHEN phase IN ('preparing', 'waiting_index') "
                    "THEN 'drawing' ELSE phase END, "
                    "updated_at = clock_timestamp() WHERE id = :build_id"
                ),
                {"build_id": build.id},
            )
            seq = await run_events.max_seq(session, run_id=claim.run_id) + 1
            await run_events.append_event(
                session,
                run_id=claim.run_id,
                seq=seq,
                type="progress",
                stage=claim.stage,
                simulated_at=run_events.simulated_time(
                    claim.simulated_epoch, seq
                ),
                payload={
                    "step": 5,
                    "stepKey": GRAPH_STEP_KEYS[4],
                    "buildId": str(build.id),
                    "phase": "drawing",
                    "partial": True,
                    "stepsApplied": int(row.next_step),  # type: ignore[attr-defined]
                    "totalSteps": claim.total_steps,
                    "plannedEpisodes": len(episodes),
                    "acceptedEpisodes": sum(
                        1
                        for item in episodes
                        if item.episode_uuid is not None
                    ),
                    "confirmedEpisodes": len(processed_uuids),
                    "snapshotRevision": revision,
                    "nodeCount": len(nodes),
                    "edgeCount": len(edges),
                },
            )
            await session.commit()
        last_revision = revision
        last_signature = signature
        return revision

    async with _heartbeat_scope(
        claim,
        session_factory=session_factory,
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
    ):
        processed_initial = [
            str(item.episode_uuid)
            for item in episodes
            if item.state == "processed" and item.episode_uuid is not None
        ]
        if processed_initial:
            await publish_partial(processed_initial, final=False)
            last_attempted = frozenset(processed_initial)
            last_attempt_at = time.time()

        pending = [item for item in episodes if item.state != "processed"]
        tasks: dict[asyncio.Task, graph_store.GraphEpisode] = {
            asyncio.create_task(
                _confirm_one_episode(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    client=client,
                    graph_id=graph_id,
                    episode=item,
                    build_id=build.id,
                    index_deadline=float(
                        write_payload.get("indexDeadlineEpoch")
                        or (time.time() + config.index_timeout_seconds)
                    ),
                    processing_deadline=deadline,
                    poll_interval_seconds=0.05,
                    clock=time.time,
                    sleep=time.sleep,
                )
            ): item
            for item in pending
        }
        try:
            while tasks:
                if stop_event is not None and stop_event.is_set():
                    for task in tasks:
                        task.cancel()
                    return "stopped"
                done, _ = await asyncio.wait(
                    set(tasks), timeout=0.15, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    tasks.pop(task, None)
                    result = task.result()
                    if isinstance(result, GraphConfirmationAborted):
                        for other in tasks:
                            other.cancel()
                        return result.outcome
                current = await read_episodes()
                processed_now = [
                    str(item.episode_uuid)
                    for item in current
                    if item.state == "processed" and item.episode_uuid is not None
                ]
                now = time.time()
                current_set = frozenset(processed_now)
                if processed_now and (
                    current_set != last_attempted
                    or now - last_attempt_at >= 1.0
                ):
                    last_attempted = current_set
                    last_attempt_at = now
                    await publish_partial(processed_now, final=False)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        final_episodes = await read_episodes()
        if any(item.state != "processed" for item in final_episodes):
            raise GraphWorkError("processing_timeout", "episode 尚未全部处理完成")
        all_uuids = [
            str(item.episode_uuid)
            for item in final_episodes
            if item.episode_uuid is not None
        ]
        await publish_partial(all_uuids, final=True)

    async with session_factory() as db:
        latest = await graph_store.get_published_snapshot(db, build_id=build.id)
        return await _commit_graph_step(
            db,
            claim,
            step=5,
            step_key=GRAPH_STEP_KEYS[4],
            payload={
                "snapshotRevision": latest.revision,
                "nodeCount": latest.node_count,
                "edgeCount": latest.edge_count,
                "episodeCount": len(final_episodes),
            },
            lease_seconds=lease_seconds,
            build_id=build.id,
            event_extra={
                "buildId": str(build.id),
                "phase": "drawing",
                "plannedEpisodes": len(final_episodes),
                "acceptedEpisodes": sum(
                    1
                    for item in final_episodes
                    if item.episode_uuid is not None
                ),
                "confirmedEpisodes": len(final_episodes),
                "snapshotRevision": latest.revision,
                "nodeCount": latest.node_count,
                "edgeCount": latest.edge_count,
            },
            build_phase="drawing",
            event_seq_from_max=True,
        )


async def execute_graph_publish(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
) -> str:
    """publish：最终快照、ready、canContinue、完成事件与任务成功同一事务。"""
    del heartbeat_seconds
    state = await _graph_step_ready(
        claim, session_factory=session_factory, expected_step=6
    )
    if state != "ready":
        return state
    async with session_factory() as db:
        build = await graph_store.get_build_for_run(db, run_id=claim.run_id)
        episodes = await graph_store.list_episodes(db, build_id=build.id)
        await db.commit()
    if any(item.state != "processed" for item in episodes):
        raise GraphWorkError("processing_timeout", "episode 尚未全部处理完成")
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    async with session_factory() as db:
        try:
            partial = await graph_store.get_published_snapshot(
                db, build_id=build.id
            )
        except graph_store.GraphSnapshotNotFound:
            raise GraphWorkError("graph_incomplete", "缺少可发布的部分快照")
        reason = _final_graph_reason(
            partial.nodes, partial.edges, partial.evidence
        )
        if reason is not None:
            raise GraphWorkError(reason, "读回结果不足以构成完成图谱")
        revision = await _next_snapshot_revision(db, build_id=build.id)

        async def publish_final(session: AsyncSession) -> None:
            await graph_store.create_snapshot(
                session,
                build_id=build.id,
                revision=revision,
                nodes=partial.nodes,
                edges=partial.edges,
                evidence=partial.evidence,
                complete=True,
                published=True,
                episode_count=len(episodes),
            )
            await graph_store.prune_partial_snapshots(
                session, build_id=build.id
            )

        return await _commit_graph_step(
            db,
            claim,
            step=6,
            step_key=GRAPH_STEP_KEYS[5],
            payload={
                "snapshotRevision": revision,
                "nodeCount": len(partial.nodes),
                "edgeCount": len(partial.edges),
                "episodeCount": len(episodes),
            },
            lease_seconds=lease_seconds,
            build_id=build.id,
            before_commit=publish_final,
            event_extra={
                "buildId": str(build.id),
                "phase": "ready",
                "plannedEpisodes": len(episodes),
                "acceptedEpisodes": len(episodes),
                "confirmedEpisodes": len(episodes),
                "snapshotRevision": revision,
                "nodeCount": len(partial.nodes),
                "edgeCount": len(partial.edges),
                "canContinue": True,
            },
            terminal=True,
            build_phase="ready",
            can_continue=True,
            event_seq_from_max=True,
        )


async def _fail_graph_build(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    code: str,
    message: str,
) -> str:
    """在仍持有租约时写失败终态；已取消或失去租约时不改写任务。"""
    async with session_factory() as db:
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
            build = await graph_store.get_build_for_run(db, run_id=claim.run_id)
            seq = await run_events.max_seq(db, run_id=claim.run_id) + 1
            await run_events.append_event(
                db,
                run_id=claim.run_id,
                seq=seq,
                type="failed",
                stage=claim.stage,
                simulated_at=run_events.simulated_time(
                    claim.simulated_epoch, seq
                ),
                payload={
                    "step": int(row.next_step),  # type: ignore[attr-defined]
                    "buildId": str(build.id),
                    "phase": "failed",
                    "reason": code,
                    "stepsApplied": int(row.next_step),  # type: ignore[attr-defined]
                    "totalSteps": claim.total_steps,
                    "plannedEpisodes": build.planned_episodes,
                    "acceptedEpisodes": build.accepted_episodes,
                    "confirmedEpisodes": build.confirmed_episodes,
                },
            )
            await db.execute(
                text(
                    "UPDATE graph_builds SET phase = 'failed', "
                    "failure_code = :code, failure_message = :message, "
                    "can_continue = false, finished_at = clock_timestamp(), "
                    "updated_at = clock_timestamp() WHERE id = :build_id"
                ),
                {"code": code, "message": message, "build_id": build.id},
            )
            await _finish(
                db,
                claim,
                int(row.next_step),  # type: ignore[attr-defined]
                status="failed",
                reason=code,
            )
            await db.commit()
            return "failed"
        except LeaseLost:
            await db.rollback()
            return "lost"
        except DB_ERRORS:
            await db.rollback()
            raise


async def execute_graph_build(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
    planner_factory: Callable[[str, Sequence[ChunkInput]], BusinessModelPlanner],
    graph_client_factory: Callable[[], Any],
    storage_root: object,
) -> str:
    """按当前步号执行六步业务建图；失败写稳定原因码，取消保留部分快照。"""
    planner: BusinessModelPlanner | None = None
    current = claim.next_step
    try:
        for step in range(current + 1, GRAPH_BUILD_TOTAL_STEPS + 1):
            if stop_event is not None and stop_event.is_set():
                return "stopped"
            if step == 1:
                outcome = await execute_graph_bind(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                    graph_client_factory=graph_client_factory,
                )
            elif step in (2, 3):
                if planner is None:
                    async with session_factory() as db:
                        build = await graph_store.get_build_for_run(
                            db, run_id=claim.run_id
                        )
                        chunks = await graph_store.list_build_chunks(
                            db, build_id=build.id, project_id=build.project_id
                        )
                        await db.commit()
                    planner = planner_factory(
                        build.question, _chunk_inputs(chunks)
                    )
                function = (
                    execute_graph_plan if step == 2 else execute_graph_extract
                )
                outcome = await function(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                    planner=planner,
                )
            elif step == 4:
                outcome = await execute_graph_write(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                    graph_client_factory=graph_client_factory,
                )
            elif step == 5:
                outcome = await execute_graph_readback(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                    graph_client_factory=graph_client_factory,
                    storage_root=storage_root,
                )
            else:
                outcome = await execute_graph_publish(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                )
            if outcome in ("succeeded", "completed"):
                return "succeeded"
            if outcome == "duplicate":
                current = step
                continue
            if outcome != "progress":
                return outcome
            current = step
        return "succeeded"
    except GraphWorkError as exc:
        logger.warning(
            "建图失败 job_id=%s code=%s", claim.job_id, exc.code
        )
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message=exc.message,
        )
    except BusinessGraphError as exc:
        logger.warning("建图适配失败 job_id=%s code=%s", claim.job_id, exc.code)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message=exc.message,
        )
    except BusinessGraphConfigError as exc:
        logger.warning("建图配置不可用 job_id=%s code=%s", claim.job_id, exc.code)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message="建图配置不完整，请联系管理员",
        )
    except GraphBindingConflict as exc:
        logger.warning("图谱绑定冲突 job_id=%s code=%s", claim.job_id, exc.code)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message="项目图谱绑定与返回结果不一致",
        )
    except GraphLimitExceeded as exc:
        logger.warning("建图规模超限 job_id=%s code=%s", claim.job_id, exc.code)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message="构建结果超过规模上限，未发布截断结果",
        )
    except Exception as exc:  # noqa: BLE001 - 单任务失败写稳定终态
        logger.warning(
            "建图异常 job_id=%s: %s", claim.job_id, type(exc).__name__
        )
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code="graph_unavailable",
            message="建图执行失败，请稍后重试",
        )


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
