"""run 事件：持久化、游标解析、历史补发与 SSE 帧。

事件先与业务变更同事务写入 `run_events`，推送阶段只读取已提交行。游标统一表示
“客户端已完整处理的最后一个连续 seq”：缺省为 0，合法范围为 ``0..MAX(seq)``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import InvalidCursor

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)

TERMINAL_TYPES = frozenset({"failed", "canceled", "completed"})
EVENT_TYPES = frozenset({"progress"}) | TERMINAL_TYPES
SIMULATED_STEP = timedelta(minutes=1)
DEFAULT_POLL_SECONDS = 0.5
DEFAULT_HEARTBEAT_SECONDS = 15.0

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class RunEvent:
    run_id: uuid.UUID
    seq: int
    type: str
    stage: str
    simulated_at: datetime
    payload: dict[str, object]


def simulated_time(epoch: datetime, seq: int) -> datetime:
    """模拟时钟：run 起点加 (seq-1) 分钟，与墙上时间无关。"""
    return epoch + SIMULATED_STEP * (seq - 1)


def _parse_last_event_id(value: str) -> tuple[uuid.UUID, int]:
    raw_run, separator, raw_seq = value.partition(":")
    if not separator:
        raise InvalidCursor("游标格式无效")
    try:
        parsed_run = uuid.UUID(raw_run)
        parsed_seq = int(raw_seq)
    except (ValueError, AttributeError, TypeError):
        raise InvalidCursor("游标格式无效") from None
    if parsed_seq < 0:
        raise InvalidCursor("游标不能为负数")
    return parsed_run, parsed_seq


def parse_cursor(
    last_event_id: str | None,
    after: str | None,
    *,
    run_id: uuid.UUID,
    last_seq: int,
) -> int:
    """按 `Last-Event-ID` > `after` > 0 的优先级确定补发起点。"""
    cursor = 0
    if last_event_id is not None and last_event_id.strip() != "":
        parsed_run, cursor = _parse_last_event_id(last_event_id)
        if parsed_run != run_id:
            raise InvalidCursor("游标属于其它 run")
    elif after is not None:
        if not after.isdigit():
            raise InvalidCursor("游标不是整数")
        cursor = int(after)
    if cursor > last_seq:
        raise InvalidCursor("游标超出当前最大序号")
    return cursor


def serialize(event: RunEvent) -> dict[str, object]:
    """对外事件字段固定为六项。"""
    return {
        "runId": str(event.run_id),
        "seq": event.seq,
        "type": event.type,
        "stage": event.stage,
        "simulatedAt": event.simulated_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "payload": event.payload,
    }


def _decode_payload(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _row_to_event(row: object) -> RunEvent:
    return RunEvent(
        run_id=row.run_id,  # type: ignore[attr-defined]
        seq=row.seq,  # type: ignore[attr-defined]
        type=row.type,  # type: ignore[attr-defined]
        stage=row.stage,  # type: ignore[attr-defined]
        simulated_at=row.simulated_at,  # type: ignore[attr-defined]
        payload=_decode_payload(row.payload),  # type: ignore[attr-defined]
    )


async def max_seq(db: AsyncSession, *, run_id: uuid.UUID) -> int:
    result = await db.execute(
        text("SELECT COALESCE(MAX(seq), 0) AS last_seq FROM run_events WHERE run_id = :run_id"),
        {"run_id": run_id},
    )
    return int(result.scalar_one() or 0)


async def terminal_seq(db: AsyncSession, *, run_id: uuid.UUID) -> int | None:
    result = await db.execute(
        text(
            "SELECT MAX(seq) AS seq FROM run_events "
            "WHERE run_id = :run_id AND type IN ('failed', 'canceled', 'completed')"
        ),
        {"run_id": run_id},
    )
    value = result.scalar_one()
    return int(value) if value is not None else None


async def list_events_after(
    db: AsyncSession, *, run_id: uuid.UUID, cursor: int
) -> list[RunEvent]:
    result = await db.execute(
        text(
            "SELECT run_id, seq, type, stage, simulated_at, payload::text AS payload "
            "FROM run_events WHERE run_id = :run_id AND seq > :cursor ORDER BY seq"
        ),
        {"run_id": run_id, "cursor": cursor},
    )
    return [_row_to_event(row) for row in result.fetchall()]


async def append_event(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    seq: int,
    type: str,
    stage: str,
    simulated_at: datetime,
    payload: dict[str, object],
) -> None:
    """在调用方事务内追加一条事件；`(run_id, seq)` 主键兜底防重。"""
    if type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型：{type}")
    await db.execute(
        text(
            "INSERT INTO run_events (run_id, seq, type, stage, simulated_at, payload) "
            "VALUES (:run_id, :seq, :type, :stage, :simulated_at, CAST(:payload AS jsonb))"
        ),
        {
            "run_id": run_id,
            "seq": seq,
            "type": type,
            "stage": stage,
            "simulated_at": simulated_at,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_frame(event: RunEvent) -> str:
    body = json.dumps(serialize(event), ensure_ascii=False)
    return f"id: {event.run_id}:{event.seq}\nevent: run-event\ndata: {body}\n\n"


def stream_end_frame(run_id: uuid.UUID, last_seq: int) -> str:
    body = json.dumps(
        {"runId": str(run_id), "lastSeq": last_seq, "terminal": True},
        ensure_ascii=False,
    )
    return f"event: stream-end\ndata: {body}\n\n"


def stream_error_frame() -> str:
    body = json.dumps({"message": "服务暂时不可用"}, ensure_ascii=False)
    return f"event: stream-error\ndata: {body}\n\n"


async def event_stream(
    *,
    session_factory: SessionFactory,
    run_id: uuid.UUID,
    cursor: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    poll_interval: float = DEFAULT_POLL_SECONDS,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """先补发游标之后的已提交事件，再按 0.5 秒轮询实时事件。

    数据库是唯一事实来源：轮询只负责唤醒，不存在“通知丢失即漏事件”的窗口。
    读到终态事件（或终态已在此前提交）后发送 `stream-end` 并结束。
    """
    last = cursor
    idle_seconds = 0.0
    yield "retry: 1000\n\n"
    while True:
        try:
            async with session_factory() as session:
                rows = await list_events_after(session, run_id=run_id, cursor=last)
                terminal = await terminal_seq(session, run_id=run_id)
        except DB_ERRORS as exc:
            logger.warning(
                "事件流读取失败 run_id=%s: %s", run_id, type(exc).__name__
            )
            yield stream_error_frame()
            return

        for event in rows:
            yield sse_frame(event)
            last = event.seq
            idle_seconds = 0.0
            if event.type in TERMINAL_TYPES:
                yield stream_end_frame(run_id, last)
                return

        if terminal is not None and last >= terminal:
            yield stream_end_frame(run_id, last)
            return

        if await is_disconnected():
            return

        if idle_seconds >= heartbeat_seconds:
            yield ": keep-alive\n\n"
            idle_seconds = 0.0
        await asyncio.sleep(poll_interval)
        idle_seconds += poll_interval
