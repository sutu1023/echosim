"""双平台模拟 worker 入口：只领取 `simulation.run`，不加载建图与图谱客户端。

真实本地 OASIS 需要 `camel-oasis`，该依赖只安装在 `worker-simulation` 镜像；
默认 worker 镜像不安装，也不会领取真实引擎任务（领取条件同时匹配 run 类型与
`simulations.engine_mode`）。
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
from .engines.duet_config import DuetConfig
from .services import simulation_worker
from .services import worker as worker_service

logger = logging.getLogger("echosim.simulation_worker")

HEARTBEAT_PATH = Path(
    os.environ.get("WORKER_HEARTBEAT_PATH", "/tmp/echosim-worker-heartbeat")
)


def build_worker_id() -> str:
    return f"{socket.gethostname()}-sim-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def _write_heartbeat(stop_event: asyncio.Event) -> None:
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
    config = DuetConfig.from_env()
    worker_id = build_worker_id()
    lease_seconds = settings.job_lease_seconds
    heartbeat_seconds = settings.job_heartbeat_seconds
    poll_seconds = settings.worker_poll_interval_seconds
    logger.info(
        "模拟 worker 启动 worker=%s engine=%s lease=%.1fs heartbeat=%.1fs",
        worker_id,
        config.worker_engine,
        lease_seconds,
        heartbeat_seconds,
    )
    heartbeat_task = asyncio.create_task(_write_heartbeat(stop_event))
    while not stop_event.is_set():
        try:
            async with SessionLocal() as db:
                claim = await worker_service.claim_next(
                    db,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                    run_kinds=("simulation.run",),
                    simulation_engine=config.worker_engine,
                )
        except Exception as exc:  # noqa: BLE001 - 数据库故障时保持进程存活
            logger.warning("领取任务异常，稍后重试: %s", type(exc).__name__)
            claim = None
        if claim is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            outcome = await simulation_worker.execute_simulation(
                claim,
                session_factory=SessionLocal,
                stop_event=stop_event,
                lease_seconds=lease_seconds,
                heartbeat_seconds=heartbeat_seconds,
                config=config,
            )
            logger.info(
                "模拟任务结局 job_id=%s run_id=%s attempt=%s outcome=%s",
                claim.job_id,
                claim.run_id,
                claim.attempt,
                outcome,
            )
        except Exception as exc:  # noqa: BLE001 - 单任务异常不杀死 worker
            logger.warning(
                "模拟任务异常 job_id=%s: %s", claim.job_id, type(exc).__name__
            )
            await asyncio.sleep(poll_seconds)
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    logger.info("模拟 worker 退出 worker=%s", worker_id)


async def _main() -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:  # pragma: no cover
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
