"""worker 执行层：领取、续租、单步事务与终态写入。

所有写操作都限定 `jobs.id` 与本次领取的 `lease_token`，并用数据库 `now()` 判断
租约是否仍然有效；旧令牌不可能续活租约，也不可能写入任何进度或事件。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
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
from ..engines.business_contracts import (
    EntityType,
    GraphContractError,
    GraphResult,
    RelationType,
    SourceRef,
)
from ..engines.business_runtime import ProviderCallMetrics
from ..engines.business_graph import (
    BusinessGraphError,
    _classify_exception,
    _edge_label,
    _edge_source,
    _edge_target,
    _graph_uuid,
    _is_auth_error,
    _node_name,
    _node_uuid,
    assert_business_graph_scope,
    readback_graph,
    readback_raw_graph,
    search_episode_once,
    set_graph_ontology,
    write_episode,
)
from ..engines.business_model import (
    BusinessModelError,
    BusinessModelPlanner,
    ChunkInput,
    ExtractedBatch,
    extraction_to_payload,
    extractions_to_payload,
    manifest_to_payload,
    plan_to_payload,
)
from ..engines.business_semantics import (
    classify_claim_nature,
    consolidate,
    infer_relation_label,
    is_generic_relation,
    normalized_name,
    recommendation_action_label,
    select_mention_quote,
    select_support_quote,
)
from ..engines.environment_contracts import (
    EnvironmentContractError,
    validate_simulation,
)
from ..engines.environment_model import (
    EnvironmentCallMeter,
    EnvironmentProfileError,
    ProfileRole,
)
from ..errors import (
    EnvironmentConflict,
    GraphBindingConflict,
    GraphLimitExceeded,
    LeaseLost,
)
from . import document_extract
from . import document_storage
from . import environment as environment_store
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
    SELECT j.id FROM jobs j
      JOIN runs r ON r.id = j.run_id
      LEFT JOIN simulations s ON s.run_id = r.id
     WHERE (
            j.status = 'queued'
            OR (
                j.status = 'running'
                AND j.lease_expires_at IS NOT NULL
                AND j.lease_expires_at <= clock_timestamp()
            )
           )
       AND (CAST(:job_id AS uuid) IS NULL OR j.id = CAST(:job_id AS uuid))
       AND (
            (
                :run_kinds = ''
                OR r.kind = ANY(string_to_array(:run_kinds, ','))
            )
            AND (
                r.kind <> 'simulation.run'
                OR s.engine_mode = :simulation_engine
            )
           )
     ORDER BY j.created_at
     FOR UPDATE OF j SKIP LOCKED
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
    run_kinds: Sequence[str] | None = None,
    simulation_engine: str | None = None,
) -> Claim | None:
    """领取一个 `queued` 或租约已过期的 `running` 任务并生成新租约令牌。

    只领取本 worker 声明的 run 类型；`simulation.run` 还必须匹配引擎模式，
    使替身 worker 与真实本地 OASIS worker 各领各的任务。
    """
    try:
        result = await db.execute(
            text(CLAIM_SQL),
            {
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                "job_id": job_id,
                "run_kinds": ",".join(run_kinds or ()),
                "simulation_engine": simulation_engine or "double",
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
    # 只把图服务抽取需要的键、名称、端点与性质写入 episode；来源由清单保存。
    raw_nodes = batch_payload.get("nodes")
    raw_edges = batch_payload.get("edges")
    nodes = [
        {
            "key": item.get("key"),
            "nodeType": item.get("nodeType"),
            "label": item.get("label"),
            "origin": item.get("origin"),
        }
        for item in (raw_nodes if isinstance(raw_nodes, list) else [])
        if isinstance(item, dict)
    ]
    edges = [
        {
            "key": item.get("key"),
            "edgeType": item.get("edgeType"),
            "label": item.get("label"),
            "origin": item.get("origin"),
            "claimNature": item.get("claimNature"),
            "sourceKey": item.get("sourceKey"),
            "targetKey": item.get("targetKey"),
        }
        for item in (raw_edges if isinstance(raw_edges, list) else [])
        if isinstance(item, dict)
    ]
    search_text = " ".join(
        [
            *(str(item.get("label") or "") for item in nodes),
            *(str(item.get("label") or "") for item in edges),
            *(str(item.get("claimNature") or "") for item in edges),
        ]
    ).strip()
    return json.dumps(
        {
            "searchText": search_text[:500],
            "buildId": str(build_id),
            "batchKey": batch_key,
            "nodes": nodes,
            "edges": edges,
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
    real_graph = config.resolve_providers().real_graph
    if build.graph_id is not None:
        assert_business_graph_scope(str(build.graph_id), config=config)
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

    async with session_factory() as db:
        binding = (
            await graph_store.get_or_create_real_binding(
                db, project_id=build.project_id
            )
            if real_graph
            else await graph_store.get_or_create_binding(
                db, project_id=build.project_id
            )
        )
    if binding.graph_id is not None and not real_graph:
        assert_business_graph_scope(str(binding.graph_id), config=config)
        async with session_factory() as db:
            async def save_build_graph(session: AsyncSession) -> None:
                await graph_store.save_build_graph_id(
                    session,
                    build_id=build.id,
                    graph_id=binding.graph_id,
                    commit=False,
                )

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
                before_commit=save_build_graph,
                event_extra={
                    "phase": "preparing",
                    "plannedEpisodes": 0,
                    "acceptedEpisodes": 0,
                    "confirmedEpisodes": 0,
                },
                build_phase="preparing",
            )

    client = graph_client_factory()
    create_idempotency_key = (
        str(
            uuid.UUID(
                str(uuid.uuid5(binding.create_idempotency_key, str(build.id))),
                version=4,
            )
        )
        if real_graph
        else str(binding.create_idempotency_key)
    )
    create_name = (
        f"{binding.request_name}-{str(build.id)[:8]}"
        if real_graph
        else binding.request_name
    )
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
                name=create_name,
                description=binding.request_description,
                time_zone=binding.request_time_zone,
                idempotency_key=create_idempotency_key,
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
                if not real_graph:
                    await graph_store.save_binding_graph_id(
                        session,
                        project_id=build.project_id,
                        graph_id=graph_id,
                        commit=False,
                    )
                await graph_store.save_build_graph_id(
                    session,
                    build_id=build.id,
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


async def execute_graph_plan_ontology(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
    planner: BusinessModelPlanner,
) -> str:
    """plan：基于整份报告与问题规划实体/关系类型，不登记主体白名单。"""
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
        func=planner.plan_ontology,
        question=build.question,
        chunks=inputs,
        config=config,
    )
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    raw_plan = plan_to_payload(stage)
    payload = {
        "mode": "ontology",
        "entityTypes": [
            {**item, "displayName": stage.entity_types[index].display_name}
            for index, item in enumerate(raw_plan["entityTypes"])
        ],
        "relationTypes": [
            {**item, "displayName": stage.relation_types[index].display_name}
            for index, item in enumerate(raw_plan["relationTypes"])
        ],
    }
    async with session_factory() as db:
        return await _commit_graph_step(
            db,
            claim,
            step=2,
            step_key=GRAPH_STEP_KEYS[1],
            payload=payload,
            lease_seconds=lease_seconds,
            build_id=build.id,
            planned_episodes=0,
            event_extra={
                "phase": "preparing",
                "plannedEpisodes": 0,
                "acceptedEpisodes": 0,
                "confirmedEpisodes": 0,
            },
            build_phase="preparing",
        )


def _plan_types_from_payload(
    payload: dict[str, object],
) -> tuple[tuple[EntityType, ...], tuple[RelationType, ...]]:
    raw_entities = payload.get("entityTypes")
    raw_relations = payload.get("relationTypes")
    if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
        raise GraphWorkError("graph_incomplete", "规划缺少实体或关系类型")
    entities = tuple(
        EntityType(
            key=str(item.get("key") or ""),
            name=str(item.get("name") or ""),
            description=str(item.get("description") or ""),
            display_name=str(item.get("displayName") or item.get("name") or ""),
        )
        for item in raw_entities
        if isinstance(item, dict) and item.get("key") and item.get("name")
    )
    relations = tuple(
        RelationType(
            key=str(item.get("key") or ""),
            name=str(item.get("name") or ""),
            source_type=str(item.get("sourceType") or ""),
            target_type=str(item.get("targetType") or ""),
            description=str(item.get("description") or ""),
            display_name=str(item.get("displayName") or item.get("name") or ""),
        )
        for item in raw_relations
        if isinstance(item, dict) and item.get("key") and item.get("name")
    )
    if not entities or not relations:
        raise GraphWorkError("graph_incomplete", "规划类型为空")
    return entities, relations


def _build_graph_chunk_payloads(
    chunks: list[graph_store.BuildChunk],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    by_document: dict[uuid.UUID, list[graph_store.BuildChunk]] = {}
    order: list[uuid.UUID] = []
    for chunk in chunks:
        if chunk.document_id not in by_document:
            by_document[chunk.document_id] = []
            order.append(chunk.document_id)
        by_document[chunk.document_id].append(chunk)
    payloads: list[dict[str, object]] = []
    documents: list[dict[str, object]] = []
    for document_id in order:
        items = sorted(
            by_document[document_id], key=lambda item: item.chunk_index
        )
        text, positions = document_extract.reconstruct_graph_text(items)
        documents.append(
            {
                "documentId": str(document_id),
                "documentName": items[0].document_name,
                "text": text,
                "positions": [
                    None
                    if item is None
                    else [
                        item.page_no,
                        item.source_offset,
                        item.document_chunk_index,
                        item.paragraph_start,
                        item.paragraph_end,
                    ]
                    for item in positions
                ],
            }
        )
        for graph_chunk in document_extract.chunk_graph_text(
            text, positions, target=500, overlap=50
        ):
            payloads.append(
                {
                    "key": f"text-{len(payloads) + 1:04d}",
                    "ordinal": len(payloads) + 1,
                    "documentId": str(document_id),
                    "documentName": items[0].document_name,
                    "text": graph_chunk.text,
                    "positions": [
                        None
                        if item is None
                        else [
                            item.page_no,
                            item.source_offset,
                            item.document_chunk_index,
                            item.paragraph_start,
                            item.paragraph_end,
                        ]
                        for item in graph_chunk.positions
                    ],
                    "segments": [
                        {
                            "pageNo": segment.page_no,
                            "documentChunkIndex": segment.document_chunk_index,
                            "paragraphStart": segment.paragraph_start,
                            "paragraphEnd": segment.paragraph_end,
                            "sourceStart": segment.source_start,
                            "sourceEnd": segment.source_end,
                            "textStart": segment.text_start,
                            "textEnd": segment.text_end,
                        }
                        for segment in graph_chunk.segments
                    ],
                }
            )
    return payloads, documents


async def execute_graph_prepare_text(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: BusinessGraphConfig,
    graph_client_factory: Callable[[], Any],
) -> str:
    """extract：重建建图全文、按 500/50 切分并设置本次本体。"""
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
    entity_types, relation_types = _plan_types_from_payload(plan_payload)
    graph_id = str(build.graph_id) if build.graph_id is not None else ""
    if not graph_id:
        async with session_factory() as db:
            binding = await graph_store.require_binding(
                db, project_id=build.project_id
            )
        if binding.graph_id is None:
            raise GraphWorkError("graph_incomplete", "项目图谱绑定尚未完成")
        graph_id = str(binding.graph_id)
    assert_business_graph_scope(graph_id, config=config)
    chunk_payloads, document_payloads = _build_graph_chunk_payloads(chunks)
    if not chunk_payloads:
        raise GraphWorkError("invalid_graph_scope", "建图全文为空")
    if len(chunk_payloads) > config.max_nodes:
        raise GraphWorkError("graph_too_large", "建图分块数量超过上限")
    client = graph_client_factory()
    try:
        await _run_with_heartbeat(
            claim,
            session_factory=session_factory,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            func=set_graph_ontology,
            client=client,
            graph_id=graph_id,
            entity_types=entity_types,
            relation_types=relation_types,
            idempotency_key=str(
                uuid.UUID(
                    str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL, f"ontology:{build.id}"
                        )
                    ),
                    version=4,
                )
            ),
        )
    except BusinessGraphError as exc:
        raise GraphWorkError(exc.code, exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        raise GraphWorkError("graph_unavailable", "图谱本体设置失败") from exc
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    page_numbers = [
        int(segment["pageNo"])
        for item in chunk_payloads
        for segment in item["segments"]  # type: ignore[union-attr]
        if isinstance(segment, dict) and segment.get("pageNo") is not None
    ]
    async with session_factory() as db:
        return await _commit_graph_step(
            db,
            claim,
            step=3,
            step_key=GRAPH_STEP_KEYS[2],
            payload={
                "mode": "text",
                "chunks": chunk_payloads,
                "documents": document_payloads,
                "pageCount": max(page_numbers) if page_numbers else 0,
                "chunkCount": len(chunk_payloads),
            },
            lease_seconds=lease_seconds,
            build_id=build.id,
            planned_episodes=len(chunk_payloads),
            event_extra={
                "phase": "preparing",
                "plannedEpisodes": len(chunk_payloads),
                "acceptedEpisodes": 0,
                "confirmedEpisodes": 0,
                "pageCount": max(page_numbers) if page_numbers else 0,
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
            subjects=stage.subjects,
            batch=batch,
            chunks=inputs,
            config=config,
        )
        extracted_batches.append(extracted)
    if stage.subjects:
        try:
            consolidated = consolidate(
                subjects=stage.subjects,
                entity_types=stage.entity_types,
                extracted_batches=extracted_batches,
                max_sources_per_item=config.max_sources_per_item,
            )
        except GraphContractError as exc:
            raise GraphWorkError(exc.code, exc.message) from exc
        nodes = consolidated.nodes
        edges = consolidated.edges
    else:
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
        subjects=stage.subjects,
        nodes=nodes,
        edges=edges,
    )
    planner.validate_result(graph, config)
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    extraction_payload = extractions_to_payload(extracted_batches)
    payload: dict[str, object] = {
        "batches": extraction_payload["batches"],
    }
    if stage.subjects:
        payload["manifest"] = manifest_to_payload(nodes, edges)
    async with session_factory() as db:
        return await _commit_graph_step(
            db,
            claim,
            step=3,
            step_key=GRAPH_STEP_KEYS[2],
            payload=payload,
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


async def _prepare_graph_text_episodes(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    build_id: uuid.UUID,
    chunk_texts: list[tuple[str, str]],
    lease_seconds: float,
) -> tuple[str, list[graph_store.GraphEpisode]]:
    """先锁 job 校验租约与 next_step=3，再把原文分块写成计划 episode。"""
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
            for key, content in chunk_texts:
                if key in existing_by_key:
                    continue
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
        await db.commit()
    if extraction_payload is None:
        raise GraphWorkError("graph_incomplete", "缺少已提交的抽取清单")
    if build.graph_id is not None:
        graph_id = str(build.graph_id)
    else:
        async with session_factory() as db:
            binding = await graph_store.require_binding(
                db, project_id=build.project_id
            )
        if binding.graph_id is None:
            raise GraphWorkError("graph_incomplete", "项目图谱绑定尚未完成")
        graph_id = str(binding.graph_id)
    assert_business_graph_scope(graph_id, config=config)
    raw_chunks = extraction_payload.get("chunks")
    text_mode = extraction_payload.get("mode") == "text" or isinstance(
        raw_chunks, list
    )
    batch_payloads: list[tuple[str, dict[str, object]]] = []
    chunk_texts: list[tuple[str, str]] = []
    if text_mode:
        if not isinstance(raw_chunks, list) or not raw_chunks:
            raise GraphWorkError("graph_incomplete", "建图全文缺少分块")
        for raw in raw_chunks:
            if not isinstance(raw, dict):
                raise GraphWorkError("graph_incomplete", "建图分块不是对象")
            key = str(raw.get("key") or "")
            chunk_text = str(raw.get("text") or "")
            if not key or not chunk_text:
                raise GraphWorkError("graph_incomplete", "建图分块缺少键或正文")
            chunk_texts.append((key, chunk_text))
        episode_keys = [key for key, _ in chunk_texts]
    else:
        raw_batches = extraction_payload.get("batches")
        if not isinstance(raw_batches, list):
            raise GraphWorkError("graph_incomplete", "抽取清单缺少批次")
        for raw in raw_batches:
            if not isinstance(raw, dict):
                raise GraphWorkError("graph_incomplete", "抽取批次不是对象")
            key = str(raw.get("batchKey") or "")
            if not key:
                raise GraphWorkError("graph_incomplete", "抽取批次缺少 batchKey")
            batch_payloads.append((key, raw))
        if not batch_payloads:
            raise GraphWorkError("graph_incomplete", "抽取清单为空")
        episode_keys = [key for key, _ in batch_payloads]
    if before_prepare is not None:
        await before_prepare()
    if text_mode:
        status, episodes = await _prepare_graph_text_episodes(
            claim,
            session_factory=session_factory,
            build_id=build.id,
            chunk_texts=chunk_texts,
            lease_seconds=lease_seconds,
        )
    else:
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
    missing = [key for key in episode_keys if key not in episode_by_key]
    if missing:
        raise GraphWorkError("graph_incomplete", "episode 持久化不完整")

    client = graph_client_factory()
    accepted: dict[str, str] = {}
    async with _heartbeat_scope(
        claim,
        session_factory=session_factory,
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
    ):
        for key in episode_keys:
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
        try:
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
        except BusinessGraphError:
            raise
        except Exception as exc:  # noqa: BLE001 - 图谱检索异常按可重试分类
            error = _classify_exception(exc)
            if not error.retryable:
                raise GraphWorkError(
                    "graph_unavailable", "episode 检索失败"
                ) from exc
            now = clock()
            remaining = processing_deadline - now
            if remaining <= 0:
                raise GraphWorkError(
                    "processing_timeout", "episode 处理预算耗尽"
                ) from exc
            sleep(min(2.0, remaining))
            continue
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
    graph_id = (
        str(build.graph_id)
        if build.graph_id is not None
        else (str(binding.graph_id) if binding.graph_id is not None else "")
    )
    if not graph_id:
        raise GraphWorkError("graph_incomplete", "项目图谱绑定尚未完成")
    if write_payload is None:
        raise GraphWorkError("graph_incomplete", "缺少已提交的写入结果")
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
    node_type: str
    type_key: str
    origin: str
    summary: str
    subject_key: str
    aliases: tuple[str, ...]
    batch_keys: tuple[str, ...]
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class ManifestEdge:
    key: str
    label: str
    edge_type: str
    type_key: str
    origin: str
    summary: str
    claim_nature: str
    source_key: str
    target_key: str
    batch_keys: tuple[str, ...]
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


def _source_tuple(raw: object) -> tuple[SourceRef, ...]:
    return tuple(
        _source_ref(source)
        for source in (raw if isinstance(raw, list) else [])
    )


def _string_tuple(raw: object) -> tuple[str, ...]:
    return tuple(
        str(item)
        for item in (raw if isinstance(raw, list) else [])
        if str(item).strip()
    )


def _manifest_from_payload(
    payload: dict[str, object],
) -> tuple[list[ManifestNode], list[ManifestEdge]]:
    raw_manifest = payload.get("manifest")
    if isinstance(raw_manifest, dict):
        raw_nodes = raw_manifest.get("nodes")
        raw_edges = raw_manifest.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise GraphWorkError("graph_incomplete", "清单缺少节点或关系")
        nodes = [
            ManifestNode(
                key=str(item.get("key") or ""),
                label=str(item.get("label") or ""),
                node_type=str(item.get("nodeType") or ""),
                type_key=str(item.get("typeKey") or item.get("nodeType") or ""),
                origin=str(item.get("origin") or ""),
                summary=str(item.get("summary") or ""),
                subject_key=str(item.get("subjectKey") or ""),
                aliases=_string_tuple(item.get("aliases")),
                batch_keys=_string_tuple(item.get("batchKeys")),
                sources=_source_tuple(item.get("sources")),
            )
            for item in raw_nodes
            if isinstance(item, dict)
        ]
        edges = [
            ManifestEdge(
                key=str(item.get("key") or ""),
                label=str(item.get("label") or ""),
                edge_type=str(item.get("edgeType") or ""),
                type_key=str(item.get("typeKey") or item.get("edgeType") or ""),
                origin=str(item.get("origin") or ""),
                summary=str(item.get("summary") or ""),
                claim_nature=str(item.get("claimNature") or ""),
                source_key=str(item.get("sourceKey") or ""),
                target_key=str(item.get("targetKey") or ""),
                batch_keys=_string_tuple(item.get("batchKeys")),
                sources=_source_tuple(item.get("sources")),
            )
            for item in raw_edges
            if isinstance(item, dict)
        ]
        if any(not node.key or not node.label for node in nodes):
            raise GraphWorkError("graph_incomplete", "清单节点缺少键或名称")
        if any(not edge.key or not edge.label for edge in edges):
            raise GraphWorkError("graph_incomplete", "清单关系缺少键或名称")
        return nodes, edges

    # 升级前已提交的旧清单：保持旧扁平读取，供旧 build 接手。
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
            nodes.append(
                ManifestNode(
                    key=key,
                    label=str(item.get("label") or ""),
                    node_type="entity",
                    type_key="entity",
                    origin=str(item.get("origin") or ""),
                    summary=str(item.get("summary") or ""),
                    subject_key="",
                    aliases=(),
                    batch_keys=(),
                    sources=_source_tuple(item.get("sources")),
                )
            )
        for item in raw_edges:
            if not isinstance(item, dict):
                raise GraphWorkError("graph_incomplete", "关系不是对象")
            key = str(item.get("key") or "")
            if not key or key in seen_edges:
                raise GraphWorkError("graph_incomplete", "关系键重复或为空")
            seen_edges.add(key)
            edges.append(
                ManifestEdge(
                    key=key,
                    label=str(item.get("label") or ""),
                    edge_type="relation",
                    type_key="relation",
                    origin=str(item.get("origin") or ""),
                    summary=str(item.get("summary") or ""),
                    claim_nature="",
                    source_key=str(item.get("sourceKey") or ""),
                    target_key=str(item.get("targetKey") or ""),
                    batch_keys=(),
                    sources=_source_tuple(item.get("sources")),
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


def _chunk_payloads_by_key(
    extraction_payload: dict[str, object],
) -> dict[str, dict[str, object]]:
    raw_chunks = extraction_payload.get("chunks")
    if not isinstance(raw_chunks, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for raw in raw_chunks:
        if isinstance(raw, dict):
            key = str(raw.get("key") or "")
            if key:
                result[key] = raw
    return result


def _documents_by_id(
    extraction_payload: dict[str, object],
) -> dict[str, dict[str, object]]:
    raw_documents = extraction_payload.get("documents")
    if not isinstance(raw_documents, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for raw in raw_documents:
        if isinstance(raw, dict):
            document_id = str(raw.get("documentId") or "")
            if document_id:
                result[document_id] = raw
    return result


def _relation_label_map(
    plan_payload: dict[str, object] | None,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not plan_payload:
        return mapping
    raw_relations = plan_payload.get("relationTypes")
    if not isinstance(raw_relations, list):
        return mapping
    for raw in raw_relations:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "")
        name = str(raw.get("name") or "")
        display = str(raw.get("displayName") or name or key)
        if key:
            mapping[normalized_name(key)] = display
        if name:
            mapping[normalized_name(name)] = display
        if display:
            mapping[normalized_name(display)] = display
    return mapping


def _entity_label_map(plan_payload: dict[str, object] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not plan_payload:
        return mapping
    raw_entities = plan_payload.get("entityTypes")
    if not isinstance(raw_entities, list):
        return mapping
    for raw in raw_entities:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "")
        name = str(raw.get("name") or "")
        display = str(raw.get("displayName") or name or key)
        if key:
            mapping[normalized_name(key)] = display
        if name:
            mapping[normalized_name(name)] = display
    return mapping


def _relation_label(raw: str, mapping: dict[str, str]) -> str:
    value = raw.strip()
    if not value:
        return "关联"
    normalized = normalized_name(value)
    exact = mapping.get(normalized)
    if exact:
        return exact
    for key, label in mapping.items():
        if len(key) < 4 or len(normalized) < 4:
            continue
        if key in normalized or normalized in key:
            return label
    if all(ord(char) < 128 for char in value):
        return infer_relation_label(value)
    return value


def _source_from_positions(
    *,
    document_id: str,
    text: str,
    positions: object,
    start: int,
    end: int,
) -> SourceRef | None:
    if not isinstance(positions, list) or len(positions) != len(text):
        return None
    if start < 0 or end > len(text) or end <= start:
        return None
    raw = text[start:end]
    left = len(raw) - len(raw.lstrip())
    right = len(raw) - len(raw.rstrip())
    start += left
    end -= right
    if end <= start:
        return None
    quote = text[start:end]
    indexes = [
        index
        for index in range(start, end)
        if isinstance(positions[index], list)
    ]
    if not indexes:
        return None
    first = positions[indexes[0]]
    last = positions[indexes[-1]]
    if not isinstance(first, list) or not isinstance(last, list):
        return None
    if (first[0], first[2]) != (last[0], last[2]):
        # 跨页/跨入库分块的句子不发布，避免来源坐标错位。
        return None
    try:
        source_start = int(first[1])
        source_end = int(last[1]) + (
            2 if ord(text[indexes[-1]]) > 0xFFFF else 1
        )
        return SourceRef(
            document_id=document_id,
            chunk_index=int(first[2]),
            quote=quote,
            source_start=source_start,
            source_end=source_end,
            page_no=None if first[0] is None else int(first[0]),
            paragraph_start=None if first[3] is None else int(first[3]),
            paragraph_end=None if last[4] is None else int(last[4]),
        )
    except (TypeError, ValueError):
        return None


def _source_from_chunk_selection(
    chunk: dict[str, object],
    *,
    start: int,
    end: int,
) -> SourceRef | None:
    return _source_from_positions(
        document_id=str(chunk.get("documentId") or ""),
        text=str(chunk.get("text") or ""),
        positions=chunk.get("positions"),
        start=start,
        end=end,
    )


def _evidence_from_refs(
    target_kind: str,
    target_key: str,
    sources: Sequence[SourceRef],
) -> list[graph_store.GraphEvidence]:
    result: list[graph_store.GraphEvidence] = []
    for index, source in enumerate(sources, start=1):
        result.append(
            graph_store.GraphEvidence(
                target_kind=target_kind,
                target_key=target_key,
                evidence_index=index,
                document_id=uuid.UUID(str(source.document_id)),
                chunk_index=int(source.chunk_index),
                page_no=source.page_no,
                paragraph_start=source.paragraph_start,
                paragraph_end=source.paragraph_end,
                source_start=int(source.source_start),
                source_end=int(source.source_end),
                quote=source.quote,
            )
        )
    return result


_PLATFORM_TYPES = frozenset(
    {
        "媒体",
        "媒体机构",
        "媒体/平台",
        "媒体平台",
        "平台",
        "社交媒体",
        "传播平台",
    }
)
_ACTOR_TYPES = frozenset(
    {
        "个人",
        "人物",
        "群体",
        "高校",
        "机构",
        "机构组织",
        "部门",
        "校内部门",
        "政府机构",
        "法院",
        "医疗机构",
        "社会组织",
    }
)
_USER_ACTIONS = (
    "维权",
    "讨论",
    "发声",
    "避坑",
    "质疑",
    "投诉",
    "支持",
    "反对",
    "参与",
    "举报",
    "曝光",
    "转发",
    "评论",
)
_UNVERIFIED_CLAIM_MARKERS = (
    "不实信息",
    "不实",
    "谣言",
    "传闻",
    "据称",
    "被指",
    "涉嫌",
    "未经证实",
    "所谓",
    "报告称",
    "报告所称",
)
_PARTIAL_ACTOR_RE = re.compile(
    r"(?:部分|个别|一些|少数)[\u4e00-\u9fa5A-Za-z0-9]{0,8}"
    r"(?:账号|网民|用户|自媒体)"
)


def _node_is_platform(node_type: str) -> bool:
    return node_type in _PLATFORM_TYPES or "平台" in node_type or "媒体" in node_type


def _node_is_actor(node_type: str) -> bool:
    return node_type in _ACTOR_TYPES


def _is_user_action(label: str) -> bool:
    return any(action in label for action in _USER_ACTIONS)


def _is_unverified_claim(
    quote: str, fact: str, target_label: str, claim_nature: str
) -> bool:
    if claim_nature not in {"media_report", "party_claim"}:
        return False
    text = normalized_name(quote) + normalized_name(fact)
    markers = [
        marker for marker in _UNVERIFIED_CLAIM_MARKERS if marker in text
    ]
    if not markers:
        return False
    target = "".join(normalized_name(target_label).split())
    if not target:
        return False
    compact = "".join(text.split())
    for marker in markers:
        start = compact.find(marker)
        while start >= 0:
            window_start = max(0, start - 50)
            window_end = min(len(compact), start + len(marker) + 50)
            if target in compact[window_start:window_end]:
                return True
            start = compact.find(marker, start + len(marker))
    return False


def _claim_actor_label(quote: str) -> str:
    match = _PARTIAL_ACTOR_RE.search(quote)
    return match.group(0) if match else ""


def _item_episode_uuids(item: object) -> set[str]:
    if isinstance(item, dict):
        values = item.get("episode_uuids") or []
    else:
        values = getattr(item, "episode_uuids", None) or []
    return {str(value) for value in values if value}


def _item_labels(item: object) -> list[str]:
    if isinstance(item, dict):
        values = item.get("labels") or []
    else:
        values = getattr(item, "labels", None) or []
    return [str(value) for value in values]


def _item_name(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "")
    return str(getattr(item, "name", "") or "")


def _materialize_readback_text(
    *,
    readback_nodes: Sequence[object],
    readback_edges: Sequence[object],
    episode_uuids: Sequence[str],
    episodes: Sequence[graph_store.GraphEpisode],
    chunks_by_key: dict[str, dict[str, object]],
    documents_by_id: dict[str, dict[str, object]],
    relation_labels: dict[str, str],
    entity_labels: dict[str, str],
    max_sources_per_item: int,
) -> tuple[
    tuple[graph_store.GraphNode, ...],
    tuple[graph_store.GraphEdge, ...],
    tuple[graph_store.GraphEvidence, ...],
    dict[str, int],
]:
    """把读回节点/边归并成快照，并把关系定位到本次 build 的支持原句。"""
    current_episodes = {str(item) for item in episode_uuids}
    chunk_key_by_episode = {
        str(item.episode_uuid): item.episode_key
        for item in episodes
        if item.episode_uuid is not None
    }

    @dataclass
    class _NodeAggregate:
        key: str
        label: str
        node_type: str
        episode_uuids: set[str]
        readback_uuids: set[str]
        sources: list[SourceRef]

    @dataclass
    class _EdgeAggregate:
        key: str
        label: str
        edge_type: str
        fact: str
        source_key: str
        target_key: str
        claim_nature: str
        episode_uuids: set[str]
        sources: list[SourceRef]

    node_by_uuid: dict[str, str] = {}
    node_aggregates: dict[str, _NodeAggregate] = {}
    for item in readback_nodes:
        item_uuid = _node_uuid(item)
        if not item_uuid:
            continue
        item_episodes = _item_episode_uuids(item)
        if not item_episodes.intersection(current_episodes):
            continue
        label = _node_name(item).strip()
        if not label or len(label) < 2:
            continue
        raw_labels = _item_labels(item)
        raw_type = next(
            (
                str(value)
                for value in raw_labels
                if str(value) not in {"Entity", "Node", "entity", "node"}
            ),
            "entity",
        )
        node_type = entity_labels.get(normalized_name(raw_type), raw_type)
        if node_type in {"entity", "Entity", "Node"}:
            node_type = "其他主体"
        key = "n:" + hashlib.sha256(
            normalized_name(label).encode("utf-8")
        ).hexdigest()[:20]
        node_by_uuid[item_uuid] = key
        aggregate = node_aggregates.get(key)
        if aggregate is None:
            node_aggregates[key] = _NodeAggregate(
                key=key,
                label=label,
                node_type=node_type,
                episode_uuids=set(item_episodes & current_episodes),
                readback_uuids={item_uuid},
                sources=[],
            )
        else:
            aggregate.episode_uuids.update(item_episodes & current_episodes)
            aggregate.readback_uuids.add(item_uuid)
            if len(label) > len(aggregate.label):
                aggregate.label = label

    edge_aggregates: dict[str, _EdgeAggregate] = {}
    dropped_edges = 0
    for item in readback_edges:
        item_uuid = _node_uuid(item)
        if not item_uuid:
            continue
        item_episodes = _item_episode_uuids(item)
        current_item_episodes = item_episodes & current_episodes
        if not current_item_episodes:
            continue
        source_key = node_by_uuid.get(_edge_source(item))
        target_key = node_by_uuid.get(_edge_target(item))
        if not source_key or not target_key or source_key == target_key:
            dropped_edges += 1
            continue
        source_node = node_aggregates[source_key]
        target_node = node_aggregates[target_key]
        raw_name = _item_name(item)
        display = _relation_label(raw_name, relation_labels)
        fact = _edge_label(item).strip() or display
        if display == raw_name and raw_name and all(
            ord(char) < 128 for char in raw_name
        ):
            display = infer_relation_label(raw_name, fact)
        if is_generic_relation(display) or is_generic_relation(raw_name):
            dropped_edges += 1
            continue
        source_ref: SourceRef | None = None
        candidate_documents: set[str] = set()
        candidate_chunks: list[dict[str, object]] = []
        for episode_uuid in sorted(current_item_episodes):
            chunk = chunks_by_key.get(chunk_key_by_episode.get(episode_uuid, ""))
            if not chunk:
                continue
            candidate_chunks.append(chunk)
            document_id = str(chunk.get("documentId") or "")
            if document_id:
                candidate_documents.add(document_id)
        # 先在整份建图全文里找句子，覆盖被 500 字分块切断的跨句关系。
        for document_id in sorted(candidate_documents):
            document = documents_by_id.get(document_id)
            if not document:
                continue
            selected = select_support_quote(
                str(document.get("text") or ""),
                fact=fact,
                action=display,
                source_label=source_node.label,
                target_label=target_node.label,
            )
            if selected is None:
                continue
            source_ref = _source_from_positions(
                document_id=document_id,
                text=str(document.get("text") or ""),
                positions=document.get("positions"),
                start=selected[0],
                end=selected[1],
            )
            if source_ref is not None:
                break
        if source_ref is None:
            for chunk in candidate_chunks:
                selected = select_support_quote(
                    str(chunk.get("text") or ""),
                    fact=fact,
                    action=display,
                    source_label=source_node.label,
                    target_label=target_node.label,
                )
                if selected is None:
                    continue
                source_ref = _source_from_chunk_selection(
                    chunk, start=selected[0], end=selected[1]
                )
                if source_ref is not None:
                    break
        if source_ref is None:
            dropped_edges += 1
            continue
        claim_nature = classify_claim_nature(
            action=display,
            fact=fact,
            quote=source_ref.quote,
            source_label=source_node.label,
        )
        action_label = display
        edge_type = display

        # 平台上的用户行动：画成“用户 → 平台”，由界面读成“用户在平台上行动”。
        if (
            _node_is_platform(source_node.node_type)
            and _node_is_actor(target_node.node_type)
            and _is_user_action(display)
        ):
            source_key, target_key = target_key, source_key
            source_node, target_node = target_node, source_node
            edge_type = "platform_action"

        # 转述/不实信息：把被传播的说法建成待核实节点，不冒充已发生事件。
        if _is_unverified_claim(
            source_ref.quote, fact, target_node.label, claim_nature
        ):
            claim_key = f"claim:{target_key}"
            claim_label = target_node.label
            if "报告所称" not in claim_label and "未证实" not in claim_label:
                claim_label = f"{claim_label}（报告所称，未证实）"
            if claim_key not in node_aggregates:
                node_aggregates[claim_key] = _NodeAggregate(
                    key=claim_key,
                    label=claim_label,
                    node_type="待核实说法",
                    episode_uuids=set(current_item_episodes),
                    readback_uuids=set(),
                    sources=[],
                )
            target_key = claim_key
            target_node = node_aggregates[claim_key]
            edge_type = "attributed_claim"
            if "传播" in display or "传播" in fact:
                action_label = "传播不实信息"
            elif "报道" in display or "报道" in fact:
                action_label = "转述未经证实说法"
            else:
                action_label = f"转述{display}"
            if _node_is_platform(source_node.node_type):
                actor_label = _claim_actor_label(source_ref.quote)
                if actor_label:
                    actor_key = "actor:" + hashlib.sha256(
                        normalized_name(actor_label).encode("utf-8")
                    ).hexdigest()[:20]
                    if actor_key not in node_aggregates:
                        node_aggregates[actor_key] = _NodeAggregate(
                            key=actor_key,
                            label=actor_label,
                            node_type="群体",
                            episode_uuids=set(current_item_episodes),
                            readback_uuids=set(),
                            sources=[],
                        )
                    source_key = actor_key
                    source_node = node_aggregates[actor_key]

        if claim_nature == "author_recommendation":
            action_label = recommendation_action_label(action_label, fact)
            edge_type = "recommendation"

        key = "e:" + hashlib.sha256(
            "|".join(
                (
                    source_key,
                    target_key,
                    edge_type,
                    normalized_name(action_label),
                    normalized_name(fact),
                )
            ).encode("utf-8")
        ).hexdigest()[:20]
        aggregate = edge_aggregates.get(key)
        if aggregate is None:
            edge_aggregates[key] = _EdgeAggregate(
                key=key,
                label=action_label,
                edge_type=edge_type,
                fact=fact,
                source_key=source_key,
                target_key=target_key,
                claim_nature=claim_nature,
                episode_uuids=set(current_item_episodes),
                sources=[source_ref],
            )
        else:
            aggregate.episode_uuids.update(current_item_episodes)
            if (
                source_ref.source_start,
                source_ref.source_end,
                source_ref.document_id,
            ) not in {
                (
                    existing.source_start,
                    existing.source_end,
                    existing.document_id,
                )
                for existing in aggregate.sources
            }:
                aggregate.sources.append(source_ref)
            if aggregate.claim_nature != "official_decision":
                aggregate.claim_nature = classify_claim_nature(
                    action=aggregate.label,
                    fact=aggregate.fact,
                    quote=aggregate.sources[0].quote,
                    source_label=node_aggregates[aggregate.source_key].label,
                )

    incident: dict[str, list[SourceRef]] = {}
    for edge in edge_aggregates.values():
        incident.setdefault(edge.source_key, []).extend(edge.sources)
        incident.setdefault(edge.target_key, []).extend(edge.sources)

    included_node_keys: set[str] = set()
    nodes: list[graph_store.GraphNode] = []
    node_evidence: list[graph_store.GraphEvidence] = []
    for aggregate in node_aggregates.values():
        sources: list[SourceRef] = []
        candidate_documents: set[str] = set()
        candidate_chunks: list[dict[str, object]] = []
        for episode_uuid in sorted(aggregate.episode_uuids):
            chunk = chunks_by_key.get(chunk_key_by_episode.get(episode_uuid, ""))
            if not chunk:
                continue
            candidate_chunks.append(chunk)
            document_id = str(chunk.get("documentId") or "")
            if document_id:
                candidate_documents.add(document_id)
        for document_id in sorted(candidate_documents):
            document = documents_by_id.get(document_id)
            if not document:
                continue
            selected = select_mention_quote(
                str(document.get("text") or ""), label=aggregate.label
            )
            if selected is None:
                continue
            source = _source_from_positions(
                document_id=document_id,
                text=str(document.get("text") or ""),
                positions=document.get("positions"),
                start=selected[0],
                end=selected[1],
            )
            if source is not None:
                sources.append(source)
            if len(sources) >= max_sources_per_item:
                break
        if not sources:
            for chunk in candidate_chunks:
                selected = select_mention_quote(
                    str(chunk.get("text") or ""), label=aggregate.label
                )
                if selected is None:
                    continue
                source = _source_from_chunk_selection(
                    chunk, start=selected[0], end=selected[1]
                )
                if source is not None:
                    sources.append(source)
                if len(sources) >= max_sources_per_item:
                    break
        if not sources:
            sources = list(incident.get(aggregate.key, []))
        if not sources:
            continue
        # 去重并限制条数。
        unique: list[SourceRef] = []
        seen: set[tuple[str, int, int, int]] = set()
        for source in sources:
            marker = (
                source.document_id,
                source.chunk_index,
                source.source_start,
                source.source_end,
            )
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(source)
            if len(unique) >= max_sources_per_item:
                break
        included_node_keys.add(aggregate.key)
        nodes.append(
            graph_store.GraphNode(
                key=aggregate.key,
                node_type=aggregate.node_type,
                label=aggregate.label,
                origin="material",
                summary=None,
            )
        )
        node_evidence.extend(_evidence_from_refs("node", aggregate.key, unique))

    edges: list[graph_store.GraphEdge] = []
    edge_evidence: list[graph_store.GraphEvidence] = []
    for aggregate in edge_aggregates.values():
        if (
            aggregate.source_key not in included_node_keys
            or aggregate.target_key not in included_node_keys
        ):
            continue
        unique_sources: list[SourceRef] = []
        seen_sources: set[tuple[str, int, int, int]] = set()
        for source in aggregate.sources:
            marker = (
                source.document_id,
                source.chunk_index,
                source.source_start,
                source.source_end,
            )
            if marker in seen_sources:
                continue
            seen_sources.add(marker)
            unique_sources.append(source)
            if len(unique_sources) >= max_sources_per_item:
                break
        if not unique_sources:
            continue
        edges.append(
            graph_store.GraphEdge(
                key=aggregate.key,
                edge_type=aggregate.edge_type,
                label=aggregate.label,
                origin="material",
                summary=aggregate.fact,
                source_key=aggregate.source_key,
                target_key=aggregate.target_key,
                claim_nature=aggregate.claim_nature,
            )
        )
        edge_evidence.extend(
            _evidence_from_refs("edge", aggregate.key, unique_sources)
        )
    stats = {
        "readbackNodeCount": len(readback_nodes),
        "readbackEdgeCount": len(readback_edges),
        "mergedNodeCount": len(node_aggregates),
        "mergedEdgeCount": len(edge_aggregates),
        "publishedNodeCount": len(nodes),
        "publishedEdgeCount": len(edges),
        "droppedEdgeCount": dropped_edges + (len(edge_aggregates) - len(edges)),
    }
    return (
        tuple(nodes),
        tuple(edges),
        tuple(node_evidence + edge_evidence),
        stats,
    )


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
    readback_names = {
        _node_name(item) for item in readback_nodes if _node_uuid(item)
    }
    relation_keys = {
        str(getattr(item, "manifest_key", "")) for item in relations.values()
    }
    edge_keys = {key for key in relation_keys if key}
    nodes_by_key = {node.key: node for node in manifest_nodes}
    included_keys: set[str] = set()
    for edge in manifest_edges:
        if edge.key in edge_keys:
            included_keys.add(edge.source_key)
            included_keys.add(edge.target_key)
    for node in manifest_nodes:
        if node.origin == "hypothesis" and node.label in readback_names:
            included_keys.add(node.key)
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
            node_type=node.node_type,
            label=node.label,
            origin=node.origin,
            summary=node.summary or None,
        )
        for node in included_nodes
    )
    edges = tuple(
        graph_store.GraphEdge(
            key=edge.key,
            edge_type=edge.edge_type,
            label=edge.label,
            origin=edge.origin,
            summary=edge.summary or None,
            source_key=edge.source_key,
            target_key=edge.target_key,
            claim_nature=edge.claim_nature or None,
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


async def _resolve_evidence_coordinates(
    db: AsyncSession,
    *,
    build: graph_store.GraphBuild,
    chunks: list[graph_store.BuildChunk],
    evidence: Sequence[graph_store.GraphEvidence],
    storage_root: object,
    raw_cache: dict[uuid.UUID, object] | None = None,
) -> tuple[graph_store.GraphEvidence, ...]:
    """用入库分块与原件重新定位引文，修正建图全文映射的坐标误差。"""
    if not evidence:
        return ()
    sources = [
        SourceRef(
            document_id=str(item.document_id),
            chunk_index=item.chunk_index,
            quote=item.quote,
            source_start=item.source_start,
            source_end=item.source_end,
            page_no=item.page_no,
            paragraph_start=item.paragraph_start,
            paragraph_end=item.paragraph_end,
        )
        for item in evidence
    ]
    try:
        resolved = await graph_source.resolve_sources(
            db,
            build_id=build.id,
            project_id=build.project_id,
            chunks=chunks,
            sources=sources,
            storage_root=storage_root,
            raw_cache=raw_cache,
        )
    except graph_source.SourceMappingError as exc:
        raise GraphWorkError(exc.code, exc.message) from exc
    result: list[graph_store.GraphEvidence] = []
    for item in evidence:
        key = (
            item.document_id,
            item.chunk_index,
            item.source_start,
            item.source_end,
        )
        source = resolved.get(key)
        if source is None:
            raise GraphWorkError("source_mismatch", "来源未通过校验")
        result.append(
            graph_store.GraphEvidence(
                target_kind=item.target_kind,
                target_key=item.target_key,
                evidence_index=item.evidence_index,
                document_id=source.document_id,
                chunk_index=source.chunk_index,
                page_no=source.page_no,
                paragraph_start=source.paragraph_start,
                paragraph_end=source.paragraph_end,
                source_start=source.source_start,
                source_end=source.source_end,
                quote=source.quote,
            )
        )
    return tuple(result)


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
    if build.graph_id is not None:
        graph_id = str(build.graph_id)
    else:
        async with session_factory() as db:
            binding = await graph_store.require_binding(
                db, project_id=build.project_id
            )
        if binding.graph_id is None:
            raise GraphWorkError("graph_incomplete", "项目图谱绑定尚未完成")
        graph_id = str(binding.graph_id)
    if any(item.episode_uuid is None for item in episodes):
        raise GraphWorkError("graph_incomplete", "episode 缺少 UUID")
    episode_batch_keys = {
        str(item.episode_uuid): item.episode_key
        for item in episodes
        if item.episode_uuid is not None
    }
    text_mode = extraction_payload.get("mode") == "text" or isinstance(
        extraction_payload.get("chunks"), list
    )
    plan_payload: dict[str, object] | None = None
    chunks_by_key: dict[str, dict[str, object]] = {}
    relation_labels: dict[str, str] = {}
    entity_labels: dict[str, str] = {}
    documents_by_id: dict[str, dict[str, object]] = {}
    manifest_nodes: list[ManifestNode] = []
    manifest_edges: list[ManifestEdge] = []
    if text_mode:
        async with session_factory() as db:
            plan_payload = await _load_graph_step_payload(
                db, run_id=claim.run_id, step=2
            )
            await db.commit()
        chunks_by_key = _chunk_payloads_by_key(extraction_payload)
        relation_labels = _relation_label_map(plan_payload)
        entity_labels = _entity_label_map(plan_payload)
        documents_by_id = _documents_by_id(extraction_payload)
        if not chunks_by_key:
            raise GraphWorkError("graph_incomplete", "建图全文分块缺失")
    else:
        manifest_nodes, manifest_edges = _manifest_from_payload(extraction_payload)
    deadline = float(
        write_payload.get("processingDeadlineEpoch")
        or (time.time() + config.processing_timeout_seconds)
    )
    if deadline - time.time() <= 0:
        raise GraphWorkError("processing_timeout", "处理预算耗尽")
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    assert_business_graph_scope(graph_id, config=config)
    client = graph_client_factory()

    source_cache: dict[uuid.UUID, object] = {}
    last_revision: int | None = None
    last_signature: tuple[object, ...] | None = None
    last_attempted: frozenset[str] = frozenset()
    last_attempt_at = 0.0
    last_stats: dict[str, int] = {}

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
        nonlocal last_revision, last_signature, last_stats
        if not processed_uuids:
            return last_revision
        remaining = deadline - time.time()
        if remaining <= 0:
            raise GraphWorkError("processing_timeout", "处理预算耗尽")
        budget = remaining if final else min(
            remaining, max(1.0, min(8.0, remaining))
        )
        try:
            if text_mode:
                readback = await asyncio.to_thread(
                    readback_raw_graph,
                    client,
                    graph_id=graph_id,
                    episode_uuids=list(processed_uuids),
                    processing_timeout_seconds=budget,
                    page_size=100,
                )
            else:
                readback = await asyncio.to_thread(
                    readback_graph,
                    client,
                    graph_id=graph_id,
                    episode_uuids=list(processed_uuids),
                    processing_timeout_seconds=budget,
                    manifest_nodes=manifest_nodes,
                    manifest_edges=manifest_edges,
                    episode_batch_keys=episode_batch_keys,
                    page_size=100,
                )
        except BusinessGraphError as exc:
            if final:
                raise GraphWorkError(exc.code, exc.message) from exc
            return last_revision
        if text_mode:
            nodes, edges, evidence, stats = _materialize_readback_text(
                readback_nodes=readback.nodes,
                readback_edges=readback.edges,
                episode_uuids=list(processed_uuids),
                episodes=episodes,
                chunks_by_key=chunks_by_key,
                documents_by_id=documents_by_id,
                relation_labels=relation_labels,
                entity_labels=entity_labels,
                max_sources_per_item=config.max_sources_per_item,
            )
            async with session_factory() as session:
                evidence = await _resolve_evidence_coordinates(
                    session,
                    build=build,
                    chunks=chunks,
                    evidence=evidence,
                    storage_root=storage_root,
                    raw_cache=source_cache,
                )
                await session.commit()
        else:
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
            stats = {}
        last_stats = stats
        logger.info(
            "建图读回统计 build=%s raw_nodes=%s raw_edges=%s merged_nodes=%s "
            "merged_edges=%s published_nodes=%s published_edges=%s dropped_edges=%s",
            build.id,
            stats.get("readbackNodeCount", len(readback.nodes)),
            stats.get("readbackEdgeCount", len(readback.edges)),
            stats.get("mergedNodeCount", len(nodes)),
            stats.get("mergedEdgeCount", len(edges)),
            stats.get("publishedNodeCount", len(nodes)),
            stats.get("publishedEdgeCount", len(edges)),
            stats.get("droppedEdgeCount", 0),
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
                    **stats,
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
        confirm_semaphore = asyncio.Semaphore(5)

        async def confirm_one(item: graph_store.GraphEpisode):
            async with confirm_semaphore:
                return await _confirm_one_episode(
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
                    poll_interval_seconds=4.0,
                    clock=time.time,
                    sleep=time.sleep,
                )

        tasks: dict[asyncio.Task, graph_store.GraphEpisode] = {
            asyncio.create_task(confirm_one(item)): item for item in pending
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
                **last_stats,
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
                **last_stats,
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


async def _add_provider_calls(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    real_delta: int,
    double_delta: int,
) -> None:
    if real_delta <= 0 and double_delta <= 0:
        return
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE graph_builds SET "
                "provider_calls_real = provider_calls_real + :real_delta, "
                "provider_calls_double = provider_calls_double + :double_delta, "
                "updated_at = clock_timestamp() WHERE run_id = :run_id"
            ),
            {
                "real_delta": max(0, real_delta),
                "double_delta": max(0, double_delta),
                "run_id": claim.run_id,
            },
        )
        await db.commit()


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
    metrics: ProviderCallMetrics | None = None,
) -> str:
    """按当前步号执行六步业务建图；失败写稳定原因码，取消保留部分快照。"""
    planner: BusinessModelPlanner | None = None
    current = claim.next_step
    last_real = 0
    last_double = 0

    async def flush_provider_calls() -> None:
        nonlocal last_real, last_double
        if metrics is None:
            return
        real_delta = metrics.real - last_real
        double_delta = metrics.double - last_double
        await _add_provider_calls(
            claim,
            session_factory=session_factory,
            real_delta=real_delta,
            double_delta=double_delta,
        )
        last_real = metrics.real
        last_double = metrics.double

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
            elif step == 2:
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
                outcome = await execute_graph_plan_ontology(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                    planner=planner,
                )
            elif step == 3:
                outcome = await execute_graph_prepare_text(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                    graph_client_factory=graph_client_factory,
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
            await flush_provider_calls()
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
        try:
            await flush_provider_calls()
        except Exception:  # noqa: BLE001 - 计数失败不掩盖原始失败
            logger.warning("provider 调用计数写入失败 job_id=%s", claim.job_id)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message=exc.message,
        )
    except BusinessModelError as exc:
        logger.warning("建图模型失败 job_id=%s code=%s", claim.job_id, exc.code)
        try:
            await flush_provider_calls()
        except Exception:  # noqa: BLE001 - 计数失败不掩盖原始失败
            logger.warning("provider 调用计数写入失败 job_id=%s", claim.job_id)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message=exc.message,
        )
    except BusinessGraphError as exc:
        logger.warning("建图适配失败 job_id=%s code=%s", claim.job_id, exc.code)
        try:
            await flush_provider_calls()
        except Exception:  # noqa: BLE001 - 计数失败不掩盖原始失败
            logger.warning("provider 调用计数写入失败 job_id=%s", claim.job_id)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message=exc.message,
        )
    except BusinessGraphConfigError as exc:
        logger.warning("建图配置不可用 job_id=%s code=%s", claim.job_id, exc.code)
        try:
            await flush_provider_calls()
        except Exception:  # noqa: BLE001 - 计数失败不掩盖原始失败
            logger.warning("provider 调用计数写入失败 job_id=%s", claim.job_id)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message="建图配置不完整，请联系管理员",
        )
    except GraphBindingConflict as exc:
        logger.warning("图谱绑定冲突 job_id=%s code=%s", claim.job_id, exc.code)
        try:
            await flush_provider_calls()
        except Exception:  # noqa: BLE001 - 计数失败不掩盖原始失败
            logger.warning("provider 调用计数写入失败 job_id=%s", claim.job_id)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message="项目图谱绑定与返回结果不一致",
        )
    except GraphLimitExceeded as exc:
        logger.warning("建图规模超限 job_id=%s code=%s", claim.job_id, exc.code)
        try:
            await flush_provider_calls()
        except Exception:  # noqa: BLE001 - 计数失败不掩盖原始失败
            logger.warning("provider 调用计数写入失败 job_id=%s", claim.job_id)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message="构建结果超过规模上限，未发布截断结果",
        )
    except Exception as exc:  # noqa: BLE001 - 单任务失败写稳定终态
        logger.exception(
            "建图异常 job_id=%s: %s", claim.job_id, type(exc).__name__
        )
        try:
            await flush_provider_calls()
        except Exception:  # noqa: BLE001 - 计数失败不掩盖原始失败
            logger.warning("provider 调用计数写入失败 job_id=%s", claim.job_id)
        return await _fail_graph_build(
            claim,
            session_factory=session_factory,
            code="graph_unavailable",
            message="建图执行失败，请稍后重试",
        )


# ---------------------------------------------------------------- 角色与环境准备

PREPARE_STEP_KEYS = environment_store.PREPARE_STEP_KEYS
PREPARE_TOTAL_STEPS = environment_store.PREPARE_TOTAL_STEPS


class PreparationWorkError(Exception):
    """准备步骤失败；`code` 为稳定原因码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def _prep_guard(
    db: AsyncSession, claim: Claim, *, step: int
) -> tuple[str, object | None]:
    row = await _locked_job(db, claim)
    if row is None:
        await db.rollback()
        return "lost", None
    if row.status in TERMINAL_STATUSES:  # type: ignore[attr-defined]
        await db.rollback()
        return str(row.status), None  # type: ignore[attr-defined]
    db_now = await _db_now(db)
    if not _lease_valid(row, claim, db_now):
        await db.rollback()
        return "lost", None
    next_step = int(row.next_step)  # type: ignore[attr-defined]
    if next_step >= step:
        await db.rollback()
        return "duplicate", None
    if next_step != step - 1:
        await db.rollback()
        return "lost", None
    return "ready", row


async def _prep_advance(
    db: AsyncSession,
    claim: Claim,
    *,
    step: int,
    lease_seconds: float,
    terminal: bool,
) -> None:
    if terminal:
        await _finish(db, claim, step, status="succeeded", reason=None)
        return
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
        raise LeaseLost("准备任务的租约已失效")


async def _prep_event_payload(
    preparation: environment_store.EnvironmentPreparation,
    *,
    step: int,
    step_key: str,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "preparationId": str(preparation.id),
        "buildId": str(preparation.build_id),
        "snapshotId": str(preparation.snapshot_id),
        "step": step,
        "stepKey": step_key,
        "phase": preparation.phase,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


async def _commit_prep_step(
    db: AsyncSession,
    claim: Claim,
    *,
    step: int,
    step_key: str,
    lease_seconds: float,
    terminal: bool = False,
    extra: dict[str, object] | None = None,
    before_commit: Callable[[AsyncSession], Any] | None = None,
) -> str:
    status, row = await _prep_guard(db, claim, step=step)
    if status != "ready":
        return status
    preparation = await environment_store.get_preparation_for_run(
        db, run_id=claim.run_id
    )
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
            "payload": json.dumps({"step": step}, ensure_ascii=False),
        },
    )
    if inserted.rowcount != 1:
        await db.rollback()
        return "duplicate"
    seq = await run_events.max_seq(db, run_id=claim.run_id) + 1
    payload = await _prep_event_payload(
        preparation,
        step=step,
        step_key=step_key,
        stepsApplied=step,
        totalSteps=claim.total_steps,
        **(extra or {}),
    )
    await run_events.append_event(
        db,
        run_id=claim.run_id,
        seq=seq,
        type="completed" if terminal else "progress",
        stage=claim.stage,
        simulated_at=run_events.simulated_time(claim.simulated_epoch, seq),
        payload=payload,
    )
    await _prep_advance(
        db, claim, step=step, lease_seconds=lease_seconds, terminal=terminal
    )
    await db.execute(
        text(
            "UPDATE environment_preparations SET updated_at = clock_timestamp(), "
            "finished_at = CASE WHEN :terminal THEN clock_timestamp() "
            "ELSE finished_at END WHERE id = :preparation_id"
        ),
        {"terminal": terminal, "preparation_id": preparation.id},
    )
    if before_commit is not None:
        await before_commit(db)
    await db.commit()
    return "succeeded" if terminal else "progress"


async def _prep_bind(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: object,
) -> str:
    del heartbeat_seconds
    if stop_event is not None and stop_event.is_set():
        return "stopped"
    async with session_factory() as db:
        preparation = await environment_store.get_preparation_for_run(
            db, run_id=claim.run_id
        )
        if preparation.phase in ("failed", "canceled"):
            await db.rollback()
            return preparation.phase
        if preparation.phase == "ready":
            await db.rollback()
            return "duplicate"
        build, snapshot = await graph_store.read_build_graph(
            db,
            project_id=preparation.project_id,
            build_id=preparation.build_id,
        )
        del build
        if snapshot is None or snapshot.id != preparation.snapshot_id:
            raise PreparationWorkError(
                "graph_snapshot_missing", "绑定快照不可用"
            )
        if not snapshot.complete or not snapshot.published:
            raise PreparationWorkError("graph_not_ready", "绑定快照未完成")
        selection = environment_store.select_for_snapshot(snapshot)
        if not selection.roles:
            raise PreparationWorkError(
                "no_role_candidates", "材料中没有可作为角色的主体"
            )
        extra = {
            "eligibleRoles": len(selection.roles),
            "totalRoles": len(selection.roles),
            "profiledRoles": 0,
            "previewRoleCount": 0,
        }
        return await _commit_prep_step(
            db,
            claim,
            step=1,
            step_key="bind",
            lease_seconds=lease_seconds,
            extra=extra,
        )


async def _prep_select(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: object,
) -> str:
    del heartbeat_seconds
    batch_size = int(getattr(config, "profile_batch_size", 2))
    async with session_factory() as db:
        preparation = await environment_store.get_preparation_for_run(
            db, run_id=claim.run_id
        )
        snapshot = await environment_store.load_bound_snapshot(db, preparation)
        selection = environment_store.select_for_snapshot(snapshot)
        if not selection.roles:
            raise PreparationWorkError(
                "no_role_candidates", "材料中没有可作为角色的主体"
            )
        ordered = sorted(selection.roles, key=lambda item: item.node_key)
        batch_index_by_key = {
            role.node_key: index // max(1, batch_size)
            for index, role in enumerate(ordered)
        }
        for role in ordered:
            role_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO environment_roles (role_id, preparation_id, "
                    "snapshot_id, node_key, display_name, role_type, batch_index, "
                    "mention, background, expressed_topics, involved_topics) "
                    "VALUES (:role_id, :preparation_id, :snapshot_id, :node_key, "
                    ":display_name, :role_type, :batch_index, CAST(:mention AS jsonb), "
                    "CAST(:background AS jsonb), CAST(:expressed AS jsonb), "
                    "CAST(:involved AS jsonb)) "
                    "ON CONFLICT (preparation_id, node_key) DO UPDATE SET "
                    "display_name = EXCLUDED.display_name, "
                    "role_type = EXCLUDED.role_type, "
                    "batch_index = EXCLUDED.batch_index, "
                    "mention = EXCLUDED.mention, "
                    "background = EXCLUDED.background, "
                    "expressed_topics = EXCLUDED.expressed_topics, "
                    "involved_topics = EXCLUDED.involved_topics, "
                    "updated_at = clock_timestamp() "
                    "WHERE environment_roles.published_at IS NULL"
                ),
                {
                    "role_id": role_id,
                    "preparation_id": preparation.id,
                    "snapshot_id": preparation.snapshot_id,
                    "node_key": role.node_key,
                    "display_name": role.display_name,
                    "role_type": role.role_type,
                    "batch_index": batch_index_by_key[role.node_key],
                    "mention": json.dumps(role.mention, ensure_ascii=False),
                    "background": json.dumps(role.background, ensure_ascii=False),
                    "expressed": json.dumps(
                        list(role.expressed_topics), ensure_ascii=False
                    ),
                    "involved": json.dumps(
                        list(role.involved_topics), ensure_ascii=False
                    ),
                },
            )
            stored = await db.execute(
                text(
                    "SELECT role_id FROM environment_roles "
                    "WHERE preparation_id = :preparation_id AND node_key = :node_key"
                ),
                {
                    "preparation_id": preparation.id,
                    "node_key": role.node_key,
                },
            )
            stored_id = stored.scalar_one()
            if role.sources:
                await db.execute(
                    text(
                        "INSERT INTO environment_role_sources (preparation_id, "
                        "snapshot_id, role_id, material_field, field_index, "
                        "source_index, target_kind, target_key, evidence_index, "
                        "claim_nature) VALUES (:preparation_id, :snapshot_id, "
                        ":role_id, :material_field, :field_index, :source_index, "
                        ":target_kind, :target_key, :evidence_index, :claim_nature) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    [
                        {
                            "preparation_id": preparation.id,
                            "snapshot_id": preparation.snapshot_id,
                            "role_id": stored_id,
                            "material_field": ref.material_field,
                            "field_index": ref.field_index,
                            "source_index": ref.source_index,
                            "target_kind": ref.target_kind,
                            "target_key": ref.target_key,
                            "evidence_index": ref.evidence_index,
                            "claim_nature": ref.claim_nature,
                        }
                        for ref in role.sources
                    ],
                )
        batch_count = len({item for item in batch_index_by_key.values()})
        extra = {
            "eligibleRoles": len(ordered),
            "totalRoles": len(ordered),
            "profiledRoles": 0,
            "previewRoleCount": 0,
            "batchCount": batch_count,
        }
        return await _commit_prep_step(
            db,
            claim,
            step=2,
            step_key="select",
            lease_seconds=lease_seconds,
            extra=extra,
        )


def _profile_inputs(
    role: environment_store.EnvironmentRoleRow,
) -> ProfileRole:
    expressed = [
        str(item.get("text") or "") for item in role.expressed_topics
    ]
    involved = [str(item.get("text") or "") for item in role.involved_topics]
    return ProfileRole(
        role_key=role.node_key,
        display_name=role.display_name,
        role_type=role.role_type,
        mention=str(role.mention.get("text") or ""),
        background=str(role.background.get("text") or ""),
        expressed_topics=tuple(item for item in expressed if item),
        involved_topics=tuple(item for item in involved if item),
    )


async def _commit_prep_batch(
    db: AsyncSession,
    claim: Claim,
    *,
    preparation: environment_store.EnvironmentPreparation,
    batch_index: int,
    batch_roles: Sequence[environment_store.EnvironmentRoleRow],
    profile: object,
    is_last: bool,
    lease_seconds: float,
    total_roles: int,
    batch_count: int,
) -> str:
    status, _row = await _prep_guard(db, claim, step=3)
    if status != "ready":
        return status
    role_map = getattr(profile, "roles")
    for role in batch_roles:
        simulation = role_map.get(role.node_key)
        if simulation is None:
            await db.rollback()
            raise PreparationWorkError(
                "invalid_model_output", "画像输出缺少角色"
            )
        validate_simulation(simulation)
        await db.execute(
            text(
                "UPDATE environment_roles SET simulation = CAST(:simulation AS jsonb), "
                "preview_ready_at = COALESCE(preview_ready_at, clock_timestamp()), "
                "updated_at = clock_timestamp() "
                "WHERE preparation_id = :preparation_id AND role_id = :role_id "
                "AND published_at IS NULL"
            ),
            {
                "simulation": json.dumps(simulation, ensure_ascii=False),
                "preparation_id": preparation.id,
                "role_id": role.role_id,
            },
        )
    counts = await db.execute(
        text(
            "SELECT count(*) FILTER (WHERE preview_ready_at IS NOT NULL) "
            "AS preview_count, count(DISTINCT batch_index) FILTER "
            "(WHERE preview_ready_at IS NOT NULL) AS preview_batches "
            "FROM environment_roles WHERE preparation_id = :preparation_id"
        ),
        {"preparation_id": preparation.id},
    )
    row = counts.one()
    preview_count = int(row.preview_count)
    preview_batches = int(row.preview_batches)
    await db.execute(
        text(
            "UPDATE environment_preparations SET "
            "preview_role_count = :preview_count, "
            "profile_batch_count = :preview_batches, "
            "updated_at = clock_timestamp() WHERE id = :preparation_id"
        ),
        {
            "preview_count": preview_count,
            "preview_batches": preview_batches,
            "preparation_id": preparation.id,
        },
    )
    batch_topics = [
        {
            "text": text,
            "origin": "simulation",
            "claimNature": None,
            "claimNatureKnown": False,
            "sources": [],
        }
        for text in getattr(profile, "initial_topics", ())
        if str(text).strip()
    ]
    if batch_topics:
        await db.execute(
            text(
                "UPDATE environment_preparations SET profile_topics = ("
                "SELECT COALESCE(jsonb_agg(DISTINCT item), '[]'::jsonb) "
                "FROM jsonb_array_elements(profile_topics || CAST(:new AS jsonb)) "
                "AS item) WHERE id = :preparation_id"
            ),
            {
                "new": json.dumps(batch_topics, ensure_ascii=False),
                "preparation_id": preparation.id,
            },
        )
    if is_last:
        inserted = await db.execute(
            text(
                "INSERT INTO run_steps (run_id, step_index, step_key, payload) "
                "VALUES (:run_id, 3, 'profile', CAST(:payload AS jsonb)) "
                "ON CONFLICT (run_id, step_index) DO NOTHING"
            ),
            {
                "run_id": claim.run_id,
                "payload": json.dumps(
                    {
                        "step": 3,
                        "batchCount": batch_count,
                        "previewRoleCount": preview_count,
                    },
                    ensure_ascii=False,
                ),
            },
        )
        if inserted.rowcount != 1:
            await db.rollback()
            return "duplicate"
    seq = await run_events.max_seq(db, run_id=claim.run_id) + 1
    payload = await _prep_event_payload(
        preparation,
        step=3,
        step_key="profile",
        stepsApplied=3 if is_last else 2,
        totalSteps=claim.total_steps,
        batchIndex=batch_index,
        batchRoleIds=[str(role.role_id) for role in batch_roles],
        eligibleRoles=total_roles,
        totalRoles=total_roles,
        profiledRoles=preview_count,
        previewRoleCount=preview_count,
        batchCount=batch_count,
        batchCommitted=is_last,
    )
    await run_events.append_event(
        db,
        run_id=claim.run_id,
        seq=seq,
        type="progress",
        stage=claim.stage,
        simulated_at=run_events.simulated_time(claim.simulated_epoch, seq),
        payload=payload,
    )
    if is_last:
        await _prep_advance(
            db, claim, step=3, lease_seconds=lease_seconds, terminal=False
        )
    else:
        updated = await db.execute(
            text(
                "UPDATE jobs SET lease_expires_at = clock_timestamp() + "
                "make_interval(secs => " + LEASE_SECONDS_PARAM + "), "
                "updated_at = clock_timestamp() WHERE id = :job_id "
                "AND lease_token = :lease_token AND status = 'running'"
            ),
            {
                "job_id": claim.job_id,
                "lease_token": claim.lease_token,
                "lease_seconds": lease_seconds,
            },
        )
        if updated.rowcount != 1:
            await db.rollback()
            return "lost"
    await db.commit()
    return "progress"


async def _flush_preparation_calls(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    preparation_id: uuid.UUID,
    real_delta: int,
    double_delta: int,
) -> None:
    """在 provider 调用边界持久化实际尝试，不依赖批次是否提交成功。"""
    if real_delta <= 0 and double_delta <= 0:
        return
    try:
        async with session_factory() as db:
            await db.execute(
                text(
                    "UPDATE environment_preparations SET "
                    "provider_calls_real = provider_calls_real + :real_delta, "
                    "provider_calls_double = provider_calls_double + :double_delta, "
                    "updated_at = clock_timestamp() "
                    "WHERE id = :preparation_id AND run_id = :run_id"
                ),
                {
                    "real_delta": max(0, real_delta),
                    "double_delta": max(0, double_delta),
                    "preparation_id": preparation_id,
                    "run_id": claim.run_id,
                },
            )
            await db.commit()
    except DB_ERRORS:
        logger.warning(
            "准备 provider 计数写入失败 job_id=%s", claim.job_id
        )


async def _prep_profile(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: object,
    profiler_factory: Callable[[EnvironmentCallMeter], Any],
) -> str:
    batch_size = int(getattr(config, "profile_batch_size", 2))
    async with session_factory() as db:
        preparation = await environment_store.get_preparation_for_run(
            db, run_id=claim.run_id
        )
        roles = await environment_store.load_roles(db, preparation=preparation)
        await db.commit()
    if not roles:
        raise PreparationWorkError("no_role_candidates", "准备没有角色")
    batches: dict[int, list[environment_store.EnvironmentRoleRow]] = {}
    for role in roles:
        batches.setdefault(role.batch_index, []).append(role)
    ordered_batches = sorted(batches)
    first_batch = ordered_batches[0]
    total_roles = len(roles)
    batch_count = len(ordered_batches)
    meter = EnvironmentCallMeter()
    flushed_real = 0
    flushed_double = 0
    profiler = profiler_factory(meter)
    for batch_index in ordered_batches:
        batch_roles = sorted(
            batches[batch_index], key=lambda item: item.node_key
        )
        if all(role.preview_ready_at is not None for role in batch_roles):
            continue
        ready = await _graph_step_ready(
            claim, session_factory=session_factory, expected_step=3
        )
        if ready != "ready":
            return ready
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        try:
            outcome = await _run_with_heartbeat(
                claim,
                session_factory=session_factory,
                lease_seconds=lease_seconds,
                heartbeat_seconds=heartbeat_seconds,
                func=profiler.profile,
                question=preparation.question,
                roles=[_profile_inputs(role) for role in batch_roles],
                require_first_speaker=batch_index == first_batch,
            )
        finally:
            await _flush_preparation_calls(
                claim,
                session_factory=session_factory,
                preparation_id=preparation.id,
                real_delta=meter.real - flushed_real,
                double_delta=meter.double - flushed_double,
            )
            flushed_real = meter.real
            flushed_double = meter.double
        is_last = batch_index == ordered_batches[-1]
        async with session_factory() as db:
            status = await _commit_prep_batch(
                db,
                claim,
                preparation=preparation,
                batch_index=batch_index,
                batch_roles=batch_roles,
                profile=outcome.profile,
                is_last=is_last,
                lease_seconds=lease_seconds,
                total_roles=total_roles,
                batch_count=batch_count,
            )
        if status != "progress":
            return status
        delay = int(getattr(config, "profile_delay_ms", 0)) / 1000
        if is_last is False and delay > 0:
            # 仅用于验收放大“第一批已提交、第二批生成中”的窗口。
            await asyncio.sleep(delay)
    # 所有批次都已预览但第 3 步尚未提交时（例如上轮最后一批提交后接管），补齐步骤。
    async with session_factory() as db:
        preparation = await environment_store.get_preparation_for_run(
            db, run_id=claim.run_id
        )
        roles = await environment_store.load_roles(db, preparation=preparation)
        preview_count = len(
            [role for role in roles if role.preview_ready_at is not None]
        )
        result = await _commit_prep_step(
            db,
            claim,
            step=3,
            step_key="profile",
            lease_seconds=lease_seconds,
            extra={
                "eligibleRoles": total_roles,
                "totalRoles": total_roles,
                "profiledRoles": preview_count,
                "previewRoleCount": preview_count,
                "batchCount": batch_count,
                "batchCommitted": True,
            },
        )
        return "progress" if result == "duplicate" else result


async def _compose_environment(
    db: AsyncSession,
    *,
    preparation: environment_store.EnvironmentPreparation,
    snapshot: graph_store.GraphSnapshot,
) -> dict[str, object]:
    selection = environment_store.select_for_snapshot(snapshot)
    roles = await environment_store.load_roles(db, preparation=preparation)
    if not roles:
        raise PreparationWorkError("no_role_candidates", "准备没有角色")
    for role in roles:
        if role.simulation is None:
            raise PreparationWorkError(
                "preparation_not_ready", "角色画像尚未完成"
            )
        try:
            validate_simulation(role.simulation)
        except EnvironmentContractError as exc:
            raise PreparationWorkError(exc.code, exc.message) from None
    ordered = sorted(
        roles,
        key=lambda item: (
            item.batch_index,
            int((item.simulation or {}).get("orderIndex", 0)),
            str(item.role_id),
        ),
    )
    first = [role for role in ordered if bool((role.simulation or {}).get("firstSpeaker"))]
    if not first:
        raise PreparationWorkError(
            "activation_order_invalid", "缺少首发角色"
        )
    if ordered[: len(first)] != first:
        raise PreparationWorkError(
            "activation_order_invalid", "首发角色必须是激活顺序前缀"
        )

    topics: list[dict[str, object]] = [
        {
            "topicId": "t1",
            "text": preparation.question,
            "origin": "question",
            "claimNature": None,
            "claimNatureKnown": False,
            "sources": [],
        }
    ]
    seen = {preparation.question.strip()}
    topic_id_by_text = {preparation.question.strip(): "t1"}

    async def add_topic(
        text: str,
        origin: str,
        claim_nature: str | None,
        sources: list[dict[str, object]],
    ) -> None:
        value = text.strip()
        if not value:
            return
        if value in seen:
            return
        seen.add(value)
        topic_id = f"t{len(topics) + 1}"
        topic_id_by_text[value] = topic_id
        topics.append(
            {
                "topicId": topic_id,
                "text": value,
                "origin": origin,
                "claimNature": claim_nature,
                "claimNatureKnown": claim_nature is not None,
                "sources": sources,
            }
        )

    for draft in selection.material_topics:
        bodies = await environment_store.source_bodies_for_refs(
            db, preparation=preparation, refs=draft.sources
        )
        await add_topic(
            draft.text, draft.origin, draft.claim_nature, bodies
        )
    for topic in preparation.profile_topics:
        if not isinstance(topic, dict):
            continue
        await add_topic(
            str(topic.get("text") or ""),
            str(topic.get("origin") or "simulation"),
            topic.get("claimNature"),
            list(topic.get("sources") or []),
        )
    for role in ordered:
        for text in (role.simulation or {}).get("topics", []):
            await add_topic(str(text), "simulation", None, [])
    for role in first:
        text = str((role.simulation or {}).get("firstTopic") or "").strip()
        if not text:
            raise PreparationWorkError(
                "first_topic_missing", "首发角色缺少首发话题"
            )
        await add_topic(text, "simulation", None, [])

    activation = []
    for position, role in enumerate(ordered, start=1):
        simulation = role.simulation or {}
        first_topic = (
            str(simulation.get("firstTopic") or "").strip()
            if role in first
            else ""
        )
        topic_id = topic_id_by_text.get(first_topic) if first_topic else None
        if role in first and topic_id is None:
            raise PreparationWorkError(
                "first_topic_missing", "首发话题不在初始话题中"
            )
        activation.append(
            {
                "position": position,
                "roleId": str(role.role_id),
                "isFirstSpeaker": role in first,
                "firstTopicId": topic_id,
            }
        )
    return {
        "platforms": [
            {"platformKey": key, "nativePlatform": native}
            for key, native in environment_store.PLATFORM_MAPPING
        ],
        "topics": topics,
        "activation": activation,
    }


async def _prep_compose(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: object,
) -> str:
    del heartbeat_seconds
    async with session_factory() as db:
        preparation = await environment_store.get_preparation_for_run(
            db, run_id=claim.run_id
        )
        snapshot = await environment_store.load_bound_snapshot(db, preparation)
        composed = await _compose_environment(
            db, preparation=preparation, snapshot=snapshot
        )
        extra = {
            "eligibleRoles": len(composed["activation"]),  # type: ignore[arg-type]
            "totalRoles": len(composed["activation"]),  # type: ignore[arg-type]
            "profiledRoles": preparation.preview_role_count,
            "previewRoleCount": preparation.preview_role_count,
            "rounds": preparation.rounds,
            "topicCount": len(composed["topics"]),  # type: ignore[arg-type]
        }
        return await _commit_prep_step(
            db,
            claim,
            step=4,
            step_key="compose",
            lease_seconds=lease_seconds,
            extra=extra,
            before_commit=None,
        )


async def _read_compose_payload(
    db: AsyncSession, *, run_id: uuid.UUID
) -> dict[str, object] | None:
    result = await db.execute(
        text(
            "SELECT payload::text AS payload FROM run_steps "
            "WHERE run_id = :run_id AND step_index = 4"
        ),
        {"run_id": run_id},
    )
    row = result.first()
    if row is None:
        return None
    try:
        payload = json.loads(row.payload)
    except (TypeError, json.JSONDecodeError):
        return None
    composed = payload.get("composed") if isinstance(payload, dict) else None
    return composed if isinstance(composed, dict) else None


async def _prep_publish(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: object,
) -> str:
    del heartbeat_seconds
    async with session_factory() as db:
        preparation = await environment_store.get_preparation_for_run(
            db, run_id=claim.run_id
        )
        snapshot = await environment_store.load_bound_snapshot(db, preparation)
        composed = await _compose_environment(
            db, preparation=preparation, snapshot=snapshot
        )
        roles = await environment_store.load_roles(db, preparation=preparation)
        role_ids = {str(role.role_id) for role in roles}
        order = [item["roleId"] for item in composed["activation"]]  # type: ignore[index]
        if len(order) != len(role_ids) or set(order) != role_ids:
            raise PreparationWorkError(
                "activation_order_invalid", "激活顺序必须覆盖全部角色"
            )
        first = [
            item
            for item in composed["activation"]  # type: ignore[index]
            if item["isFirstSpeaker"]
        ]
        if not first or order[: len(first)] != [item["roleId"] for item in first]:
            raise PreparationWorkError(
                "activation_order_invalid", "首发角色必须是激活顺序前缀"
            )
        status, _row = await _prep_guard(db, claim, step=5)
        if status != "ready":
            return status
        await db.execute(
            text(
                "DELETE FROM environment_activation WHERE preparation_id = :prep"
            ),
            {"prep": preparation.id},
        )
        await db.execute(
            text(
                "DELETE FROM environment_platforms WHERE preparation_id = :prep"
            ),
            {"prep": preparation.id},
        )
        await db.execute(
            text("DELETE FROM environment_topics WHERE preparation_id = :prep"),
            {"prep": preparation.id},
        )
        for platform in composed["platforms"]:  # type: ignore[index]
            await db.execute(
                text(
                    "INSERT INTO environment_platforms (preparation_id, "
                    "platform_key, native_platform, enabled) "
                    "VALUES (:prep, :key, :native, true)"
                ),
                {
                    "prep": preparation.id,
                    "key": platform["platformKey"],
                    "native": platform["nativePlatform"],
                },
            )
        for topic in composed["topics"]:  # type: ignore[index]
            await db.execute(
                text(
                    "INSERT INTO environment_topics (preparation_id, topic_id, "
                    "topic_index, text, origin, claim_nature, claim_nature_known, "
                    "sources) VALUES (:prep, :topic_id, :topic_index, :text, "
                    ":origin, :claim, :known, CAST(:sources AS jsonb))"
                ),
                {
                    "prep": preparation.id,
                    "topic_id": topic["topicId"],
                    "topic_index": int(str(topic["topicId"])[1:]) - 1,
                    "text": topic["text"],
                    "origin": topic["origin"],
                    "claim": topic["claimNature"],
                    "known": topic["claimNatureKnown"],
                    "sources": json.dumps(topic["sources"], ensure_ascii=False),
                },
            )
        for item in composed["activation"]:  # type: ignore[index]
            await db.execute(
                text(
                    "INSERT INTO environment_activation (preparation_id, "
                    "position, role_id, is_first_speaker, first_topic_id) "
                    "VALUES (:prep, :position, :role_id, :first, :topic)"
                ),
                {
                    "prep": preparation.id,
                    "position": item["position"],
                    "role_id": item["roleId"],
                    "first": item["isFirstSpeaker"],
                    "topic": item["firstTopicId"],
                },
            )
        await db.execute(
            text(
                "UPDATE environment_roles SET published_at = clock_timestamp(), "
                "updated_at = clock_timestamp() WHERE preparation_id = :prep "
                "AND preview_ready_at IS NOT NULL"
            ),
            {"prep": preparation.id},
        )
        await db.execute(
            text(
                "UPDATE environment_preparations SET phase = 'ready', "
                "failure_code = NULL, failure_message = NULL, "
                "finished_at = clock_timestamp(), updated_at = clock_timestamp() "
                "WHERE id = :prep"
            ),
            {"prep": preparation.id},
        )
        extra = {
            "eligibleRoles": len(roles),
            "totalRoles": len(roles),
            "profiledRoles": len(roles),
            "previewRoleCount": len(roles),
            "rounds": preparation.rounds,
            "topicCount": len(composed["topics"]),  # type: ignore[arg-type]
            "phase": "ready",
        }
        return await _commit_prep_step(
            db,
            claim,
            step=5,
            step_key="publish",
            lease_seconds=lease_seconds,
            terminal=True,
            extra=extra,
        )


async def _fail_preparation(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    code: str,
    message: str,
) -> str:
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
            preparation = await environment_store.get_preparation_for_run(
                db, run_id=claim.run_id
            )
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
                    "preparationId": str(preparation.id),
                    "buildId": str(preparation.build_id),
                    "snapshotId": str(preparation.snapshot_id),
                    "step": int(row.next_step),  # type: ignore[attr-defined]
                    "stepKey": (
                        PREPARE_STEP_KEYS[min(int(row.next_step), 4)]  # type: ignore[attr-defined]
                    ),
                    "phase": "failed",
                    "reason": code,
                    "stepsApplied": int(row.next_step),  # type: ignore[attr-defined]
                    "totalSteps": claim.total_steps,
                    "previewRoleCount": preparation.preview_role_count,
                },
            )
            await db.execute(
                text(
                    "UPDATE environment_preparations SET phase = 'failed', "
                    "failure_code = :code, failure_message = :message, "
                    "finished_at = clock_timestamp(), updated_at = clock_timestamp() "
                    "WHERE id = :prep"
                ),
                {"code": code, "message": message, "prep": preparation.id},
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


async def execute_preparation(
    claim: Claim,
    *,
    session_factory: SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: object,
    profiler_factory: Callable[[EnvironmentCallMeter], Any],
) -> str:
    """按五步执行角色与环境准备；失败写稳定原因码，不发布半成品。"""
    try:
        for step in range(claim.next_step + 1, PREPARE_TOTAL_STEPS + 1):
            if stop_event is not None and stop_event.is_set():
                return "stopped"
            if step == 1:
                outcome = await _prep_bind(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                )
            elif step == 2:
                outcome = await _prep_select(
                    claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                )
            elif step == 3:
                outcome = await _prep_profile(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                    profiler_factory=profiler_factory,
                )
            elif step == 4:
                outcome = await _prep_compose(
                    claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                )
            else:
                outcome = await _prep_publish(
                    claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                )
            if outcome in ("succeeded", "completed"):
                return "succeeded"
            if outcome in ("progress", "duplicate"):
                continue
            return outcome
        return "succeeded"
    except PreparationWorkError as exc:
        logger.warning("准备失败 job_id=%s code=%s", claim.job_id, exc.code)
        return await _fail_preparation(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message=exc.message,
        )
    except EnvironmentProfileError as exc:
        code = (
            exc.code
            if exc.code in ("invalid_model_output", "no_role_candidates")
            else "model_unavailable"
        )
        logger.warning("准备画像失败 job_id=%s code=%s", claim.job_id, code)
        return await _fail_preparation(
            claim,
            session_factory=session_factory,
            code=code,
            message=(
                "画像输出不合法"
                if code == "invalid_model_output"
                else "画像服务暂时不可用"
            ),
        )
    except EnvironmentContractError as exc:
        logger.warning("准备契约失败 job_id=%s code=%s", claim.job_id, exc.code)
        return await _fail_preparation(
            claim,
            session_factory=session_factory,
            code="invalid_model_output",
            message="画像输出不合法",
        )
    except EnvironmentConflict as exc:
        logger.warning("准备状态冲突 job_id=%s code=%s", claim.job_id, exc.code)
        return await _fail_preparation(
            claim,
            session_factory=session_factory,
            code=exc.code,
            message=exc.message,
        )
    except Exception as exc:  # noqa: BLE001 - 单任务失败写稳定终态
        logger.exception(
            "准备异常 job_id=%s: %s", claim.job_id, type(exc).__name__
        )
        return await _fail_preparation(
            claim,
            session_factory=session_factory,
            code="preparation_failed",
            message="准备执行失败，请稍后重试",
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
