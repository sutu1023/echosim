"""worker 进程入口：领取任务、逐步执行、SIGTERM 收尾。

与 `app` 进程完全独立：不监听端口、不修改 `/health` 语义，停止或崩溃只影响任务进度。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys
import uuid
from pathlib import Path

from .config import settings
from .db import SessionLocal, dispose_engine
from .engines.business_config import BusinessGraphConfig
from .engines.business_runtime import create_graph_client, create_planner
from .services import worker as worker_service

logger = logging.getLogger("echosim.worker")

# 供 Compose 探针判断 worker 进程是否存活；不暴露端口，也不代表任务进度。
HEARTBEAT_PATH = Path(os.environ.get("WORKER_HEARTBEAT_PATH", "/tmp/echosim-worker-heartbeat"))


def build_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def _write_heartbeat(stop_event: asyncio.Event) -> None:
    """每秒刷新一次心跳文件；停止或退出时让文件自然过期。"""
    while not stop_event.is_set():
        try:
            HEARTBEAT_PATH.touch()
        except OSError:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass


async def run(stop_event: asyncio.Event) -> None:
    worker_id = build_worker_id()
    lease_seconds = settings.job_lease_seconds
    heartbeat_seconds = settings.job_heartbeat_seconds
    poll_seconds = settings.worker_poll_interval_seconds
    step_delay = settings.test_task_step_delay_ms / 1000
    logger.info(
        "worker 启动 worker=%s lease=%.1fs heartbeat=%.1fs poll=%.2fs delay=%.2fs",
        worker_id,
        lease_seconds,
        heartbeat_seconds,
        poll_seconds,
        step_delay,
    )
    heartbeat_task = asyncio.create_task(_write_heartbeat(stop_event))

    while not stop_event.is_set():
        try:
            async with SessionLocal() as db:
                claim = await worker_service.claim_next(
                    db, worker_id=worker_id, lease_seconds=lease_seconds
                )
        except Exception as exc:  # noqa: BLE001 - 数据库故障时保持进程存活并重试
            logger.warning("领取任务异常，稍后重试: %s", type(exc).__name__)
            claim = None

        if claim is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            if claim.kind == "evidence.graph.build":
                business_config = BusinessGraphConfig.from_env()
                client_cache: dict[str, object] = {}

                def graph_client_factory():
                    if "client" not in client_cache:
                        client_cache["client"] = create_graph_client(
                            business_config,
                            zep_api_key=settings.zep_api_key,
                            zep_api_base_url=settings.zep_api_base_url,
                            timeout_seconds=settings.graph_write_timeout_seconds,
                        )
                    return client_cache["client"]

                outcome = await worker_service.execute_graph_build(
                    claim,
                    session_factory=SessionLocal,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=business_config,
                    planner_factory=lambda question, chunks: create_planner(
                        business_config,
                        model_api_base_url=settings.model_api_base_url,
                        model_api_key=settings.model_api_key,
                        model_name=settings.model_name,
                        timeout_seconds=settings.model_timeout_seconds,
                        question=question,
                        chunks=chunks,
                    ),
                    graph_client_factory=graph_client_factory,
                    storage_root=settings.document_storage_dir,
                )
            elif claim.kind == "document.ingest":
                outcome = await worker_service.execute_ingest(
                    claim,
                    session_factory=SessionLocal,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    storage_root=settings.document_storage_dir,
                    max_pages=settings.document_max_pages,
                    chunk_target=settings.document_chunk_target_chars,
                    chunk_overlap=settings.document_chunk_overlap_chars,
                    parse_delay_ms=settings.document_ingest_delay_ms,
                )
            else:
                outcome = await worker_service.execute_job(
                    claim,
                    session_factory=SessionLocal,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    step_delay_seconds=step_delay,
                )
            logger.info(
                "任务结局 job_id=%s run_id=%s kind=%s document_id=%s "
                "attempt=%s outcome=%s",
                claim.job_id,
                claim.run_id,
                claim.kind,
                claim.document_id,
                claim.attempt,
                outcome,
            )
        except Exception as exc:  # noqa: BLE001 - 单任务异常不应杀死 worker
            logger.warning(
                "任务执行异常 job_id=%s: %s", claim.job_id, type(exc).__name__
            )
            await asyncio.sleep(poll_seconds)

    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 收尾不掩盖主流程
        pass
    logger.info("worker 退出 worker=%s", worker_id)


async def _main() -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:  # pragma: no cover - 非类 Unix 平台
            pass
    try:
        await run(stop_event)
    finally:
        await dispose_engine()
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
