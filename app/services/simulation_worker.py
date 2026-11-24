"""双平台运行 worker：四步执行、按平台轮次推进、读回记账、停止与接手。

轮次进度以 `simulation_rounds` 为游标；每条轮次记录与动作、计数和事件同事务
提交。引擎写在同一 SQLite，worker 只按引擎记录读回，不采信子进程摘要。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..engines.duet_config import DuetConfig
from ..engines.duet_contracts import (
    PLATFORM_MAPPING,
    DuetContractError,
    KnownPost,
    plan_round,
)
from ..engines.duet_engine import native_counts, round_records
from ..errors import LeaseLost
from . import run_events
from . import simulation as simulation_store
from . import worker as worker_service

logger = logging.getLogger(__name__)

SIM_TOTAL_STEPS = simulation_store.SIMULATION_TOTAL_STEPS
SIM_STEP_KEYS = simulation_store.SIMULATION_STEP_KEYS
CHILD_MODULE = "app.engines.duet_runner"
MAX_ROUND_RESUMES = 2


class SimulationWorkError(Exception):
    """运行步骤失败；`code` 为稳定原因码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _runtime_root(config: DuetConfig, simulation_id: uuid.UUID) -> Path:
    return Path(config.data_dir) / str(simulation_id)


def _runtime_dir(config: DuetConfig, simulation_id: uuid.UUID, platform_key: str) -> Path:
    return _runtime_root(config, simulation_id) / platform_key


async def _sim_guard(
    db: AsyncSession, claim: worker_service.Claim, *, step: int
) -> tuple[str, object | None]:
    status, row = await worker_service._prep_guard(db, claim, step=step)
    if status != "ready":
        return status, None
    simulation = await simulation_store.get_simulation_for_run(
        db, run_id=claim.run_id
    )
    if simulation.phase in simulation_store.TERMINAL_PHASES:
        await db.rollback()
        return simulation.phase, None
    return "ready", row


async def _commit_step(
    db: AsyncSession,
    claim: worker_service.Claim,
    *,
    simulation: simulation_store.SimulationRow,
    step: int,
    step_key: str,
    lease_seconds: float,
    terminal: bool = False,
    status: str | None = None,
    reason: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
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
    payload: dict[str, Any] = {
        "simulationId": str(simulation.id),
        "selectionId": str(simulation.selection_id),
        "buildId": str(simulation.build_id),
        "snapshotId": str(simulation.snapshot_id),
        "step": step,
        "stepKey": step_key,
        "phase": status or simulation.phase,
        "rounds": simulation.rounds,
        "stepsApplied": step,
        "totalSteps": claim.total_steps,
    }
    if extra:
        payload.update({key: value for key, value in extra.items() if value is not None})
    await run_events.append_event(
        db,
        run_id=claim.run_id,
        seq=seq,
        type=(
            "completed"
            if status == "succeeded"
            else "failed"
            if status == "failed"
            else "canceled"
            if status == "canceled"
            else "progress"
        ),
        stage=claim.stage,
        simulated_at=run_events.simulated_time(claim.simulated_epoch, seq),
        payload=payload,
    )
    if terminal:
        await worker_service._finish(
            db,
            claim,
            step,
            status=status or "succeeded",
            reason=reason,
        )
    else:
        updated = await db.execute(
            text(
                "UPDATE jobs SET next_step = :step, lease_expires_at = "
                "clock_timestamp() + make_interval(secs => "
                + worker_service.LEASE_SECONDS_PARAM
                + "), updated_at = clock_timestamp() WHERE id = :job_id "
                "AND lease_token = :lease_token AND status = 'running'"
            ),
            {
                "step": step,
                "job_id": claim.job_id,
                "lease_token": claim.lease_token,
                "lease_seconds": lease_seconds,
            },
        )
        if updated.rowcount != 1:
            raise LeaseLost("运行任务的租约已失效")
    await db.execute(
        text(
            "UPDATE simulations SET phase = :phase, updated_at = clock_timestamp(), "
            "finished_at = CASE WHEN :terminal THEN clock_timestamp() "
            "ELSE finished_at END WHERE id = :simulation_id"
        ),
        {
            "phase": status or "running",
            "terminal": terminal,
            "simulation_id": simulation.id,
        },
    )
    await db.commit()
    return "succeeded" if terminal else "progress"


async def _bind(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    lease_seconds: float,
) -> str:
    async with session_factory() as db:
        status, _ = await _sim_guard(db, claim, step=1)
        if status != "ready":
            return status
        simulation = await simulation_store.get_simulation_for_run(
            db, run_id=claim.run_id
        )
        try:
            simulation.plan
        except DuetContractError as exc:
            await db.rollback()
            return await _fail(
                claim,
                session_factory=session_factory,
                config=config,
                code=exc.code,
                message=exc.message,
                failed_platforms=[],
            )
        return await _commit_step(
            db,
            claim,
            simulation=simulation,
            step=1,
            step_key=SIM_STEP_KEYS[0],
            lease_seconds=lease_seconds,
            extra={"rounds": simulation.rounds, "engine": simulation.engine_mode},
        )


async def _environment(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    lease_seconds: float,
    config: DuetConfig,
) -> str:
    async with session_factory() as db:
        status, _ = await _sim_guard(db, claim, step=2)
        if status != "ready":
            return status
        simulation = await simulation_store.get_simulation_for_run(
            db, run_id=claim.run_id
        )
        for platform_key in PLATFORM_MAPPING:
            _runtime_dir(config, simulation.id, platform_key).mkdir(
                parents=True, exist_ok=True
            )
        await db.execute(
            text(
                "UPDATE simulation_platforms SET status = 'running', "
                "updated_at = clock_timestamp() WHERE simulation_id = :id "
                "AND status = 'pending'"
            ),
            {"id": simulation.id},
        )
        return await _commit_step(
            db,
            claim,
            simulation=simulation,
            step=2,
            step_key=SIM_STEP_KEYS[1],
            lease_seconds=lease_seconds,
            extra={"platforms": list(PLATFORM_MAPPING)},
        )


async def _cancel_requested(db: AsyncSession, claim: worker_service.Claim) -> bool:
    result = await db.execute(
        text(
            "SELECT status, cancel_requested_at FROM jobs WHERE id = :job_id"
        ),
        {"job_id": claim.job_id},
    )
    row = result.first()
    if row is None:
        return False
    return str(row.status) == "canceled" or row.cancel_requested_at is not None


async def _round_plan(
    db: AsyncSession,
    *,
    simulation: simulation_store.SimulationRow,
    platform_key: str,
    round_index: int,
    actions_per_round: int,
) -> list[Any]:
    posts = await simulation_store.recent_posts(
        db, simulation_id=simulation.id, platform_key=platform_key
    )
    known = [
        KnownPost(
            action_key=f"known:{item['engineRecordId']}",
            engine_record_id=item["engineRecordId"],
            role_id=item["roleId"],
            topic=simulation.question,
        )
        for item in posts
    ]
    return plan_round(
        simulation.plan,
        platform_key=platform_key,
        round_index=round_index,
        run_marker=str(simulation.run_id),
        known_posts=known,
        actions_per_round=actions_per_round,
    )


async def _run_child(
    *,
    runtime_dir: Path,
    platform_key: str,
    mode: str,
    round_index: int,
    actions: Sequence[Any],
    fault: str,
    timeout_seconds: float,
    engine_command: str = "",
    engine_runtime: str = "/out",
) -> tuple[int, str]:
    request = {
        "mode": mode,
        "platformKey": platform_key,
        "runtimeDir": engine_runtime if engine_command else str(runtime_dir),
        "roundIndex": round_index,
        "fault": fault,
        "actions": [
            {
                "actionKey": action.action_key,
                "actionIndex": action.action_index,
                "nativeAction": action.native_action,
                "actorRoleId": action.actor_role_id,
                "actorAgentId": action.actor_agent_id,
                "actorName": action.actor_name,
                "content": action.content,
                "marker": action.marker,
                "targetActionKey": action.target_action_key,
                "targetEngineRecordId": action.target_engine_record_id,
            }
            for action in actions
        ],
    }
    request_path = runtime_dir / f"round-{round_index}-request.json"
    request_path.write_text(
        json.dumps(request, ensure_ascii=False), encoding="utf-8"
    )
    if engine_command:
        # 真实本地 OASIS 只在隔离引擎镜像里执行；worker 只负责租约与记账。
        command = [
            part.format(runtime=str(runtime_dir), request_name=request_path.name)
            for part in shlex.split(engine_command)
        ]
    else:
        command = [sys.executable, "-m", CHILD_MODULE, "--request", str(request_path)]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(process.pid), 9)
        except (ProcessLookupError, PermissionError):
            process.kill()
        await process.wait()
        return -9, ""
    finally:
        request_path.unlink(missing_ok=True)
    if process.returncode != 0:
        logger.warning(
            "引擎子进程失败 platform=%s round=%s code=%s stdout=%s stderr=%s",
            platform_key,
            round_index,
            process.returncode,
            stdout.decode("utf-8", "replace")[-300:],
            stderr.decode("utf-8", "replace")[-300:],
        )
    return process.returncode or 0, stdout.decode("utf-8", "replace")


async def _book_round(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    lease_seconds: float,
    simulation: simulation_store.SimulationRow,
    platform_key: str,
    round_index: int,
    records: Sequence[Mapping[str, Any]],
    round_outcome: str = "committed",
) -> str:
    simulated_at = simulation.simulated_start + timedelta(
        minutes=simulation.minutes_per_round * (round_index - 1)
    )
    async with session_factory() as db:
        row = await worker_service._locked_job(db, claim)
        if row is None:
            await db.rollback()
            return "lost"
        if row.status in worker_service.TERMINAL_STATUSES:  # type: ignore[attr-defined]
            await db.rollback()
            return str(row.status)  # type: ignore[attr-defined]
        db_now = await worker_service._db_now(db)
        if not worker_service._lease_valid(row, claim, db_now):
            await db.rollback()
            return "lost"
        current = await db.execute(
            text("SELECT phase FROM simulations WHERE id = :id"),
            {"id": simulation.id},
        )
        phase_row = current.first()
        if phase_row is None or str(phase_row.phase) in simulation_store.TERMINAL_PHASES:
            await db.rollback()
            return "terminal"
        written = await simulation_store.write_round(
            db,
            simulation=simulation,
            platform_key=platform_key,
            round_index=round_index,
            simulated_at=simulated_at,
            records=records,
            outcome=round_outcome,
        )
        if written is None:
            await db.rollback()
            return "duplicate"
        platform = await db.execute(
            text(
                "SELECT rounds_completed, action_count, by_action "
                "FROM simulation_platforms WHERE simulation_id = :id "
                "AND platform_key = :platform_key"
            ),
            {"id": simulation.id, "platform_key": platform_key},
        )
        platform_row = platform.first()
        seq = await run_events.max_seq(db, run_id=claim.run_id) + 1
        await run_events.append_event(
            db,
            run_id=claim.run_id,
            seq=seq,
            type="progress",
            stage=claim.stage,
            simulated_at=simulated_at,
            payload={
                "simulationId": str(simulation.id),
                "selectionId": str(simulation.selection_id),
                "buildId": str(simulation.build_id),
                "snapshotId": str(simulation.snapshot_id),
                "step": 3,
                "stepKey": SIM_STEP_KEYS[2],
                "phase": "running",
                "roundIndex": round_index,
                "totalRounds": simulation.rounds,
                "platformKey": platform_key,
                "nativePlatform": PLATFORM_MAPPING[platform_key],
                "roundsCompleted": int(platform_row.rounds_completed),  # type: ignore[union-attr]
                "actionCount": int(platform_row.action_count),  # type: ignore[union-attr]
                "nativeActionCounts": dict(platform_row.by_action or {}),  # type: ignore[union-attr]
                "actions": written,
            },
        )
        await db.commit()
    return "ok"


async def _count_calls(
    session_factory: worker_service.SessionFactory,
    *,
    simulation_id: uuid.UUID,
    mode: str,
) -> None:
    async with session_factory() as db:
        await simulation_store.record_engine_calls(
            db, simulation_id=simulation_id, mode=mode
        )
        await db.commit()


async def _mark_platform_failed(
    session_factory: worker_service.SessionFactory,
    *,
    simulation_id: uuid.UUID,
    platform_key: str,
    code: str,
) -> None:
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE simulation_platforms SET status = 'failed', "
                "failure_code = :code, updated_at = clock_timestamp() "
                "WHERE simulation_id = :id AND platform_key = :platform_key"
            ),
            {"code": code, "id": simulation_id, "platform_key": platform_key},
        )
        await db.commit()


async def _run_round_actions(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    simulation: simulation_store.SimulationRow,
    platform_key: str,
    round_index: int,
    actions: Sequence[Any],
    config: DuetConfig,
    runtime_dir: Path,
) -> tuple[list[Mapping[str, Any]], str | None]:
    """调用引擎并独立读回；返回 (可补记动作, 失败原因码)。"""
    fault = config.fault_for(platform_key, round_index)
    readback: dict[str, Any] = {}
    for attempt in range(MAX_ROUND_RESUMES + 1):
        code, _ = await _run_child(
            runtime_dir=runtime_dir,
            platform_key=platform_key,
            mode=simulation.engine_mode,
            round_index=round_index,
            actions=actions,
            fault=fault if attempt == 0 else "none",
            timeout_seconds=config.round_timeout_seconds,
            engine_command=(
                config.engine_command if simulation.engine_mode == "oasis" else ""
            ),
            engine_runtime=config.engine_runtime,
        )
        await _count_calls(
            session_factory,
            simulation_id=simulation.id,
            mode=simulation.engine_mode,
        )
        if code == -9:
            # 超时也要先读回已写入的原生行，避免引擎有记录而业务缺账。
            readback = round_records(
                runtime_dir=runtime_dir,
                platform_key=platform_key,
                round_index=round_index,
                actions=[
                    {"action_key": action.action_key, "content": action.content}
                    for action in actions
                ],
            )
            if readback["unconfirmed"]:
                return list(readback["records"]), "action_unconfirmed"
            return list(readback.get("records") or []), "simulation_timeout"
        readback = round_records(
            runtime_dir=runtime_dir,
            platform_key=platform_key,
            round_index=round_index,
            actions=[
                {"action_key": action.action_key, "content": action.content}
                for action in actions
            ],
        )
        if readback["unconfirmed"]:
            return list(readback["records"]), "action_unconfirmed"
        if not readback["planned"]:
            return list(readback.get("records") or []), None
        fault = "none"
    return [], f"{platform_key}_record"


async def _advance_platform(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    lease_seconds: float,
    simulation: simulation_store.SimulationRow,
    platform_key: str,
    config: DuetConfig,
    stop_event: asyncio.Event | None,
    stop_signal: asyncio.Event,
) -> str:
    """单个平台独立推进：慢平台不阻塞另一平台的下一轮。"""
    runtime_dir = _runtime_dir(config, simulation.id, platform_key)
    rounds_so_far = simulation_store.platform_rounds
    async with session_factory() as db:
        completed = await rounds_so_far(
            db, simulation_id=simulation.id, platform_key=platform_key
        )
    while completed < simulation.rounds:
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        if stop_signal.is_set():
            return "canceled"
        async with session_factory() as db:
            canceled = await _cancel_requested(db, claim)
            await db.commit()
        if canceled:
            stop_signal.set()
            return "canceled"
        pause = config.delay_for(platform_key) or config.round_delay_ms
        if pause:
            await asyncio.sleep(pause / 1000)
        round_index = completed + 1
        async with session_factory() as db:
            actions = await _round_plan(
                db,
                simulation=simulation,
                platform_key=platform_key,
                round_index=round_index,
                actions_per_round=config.actions_per_round,
            )
            await db.commit()
        reserved = len(actions)
        try:
            if reserved:
                async with session_factory() as db:
                    allowed = await simulation_store.reserve_actions(
                        db,
                        simulation_id=simulation.id,
                        count=reserved,
                        max_actions=config.max_actions,
                    )
                    await db.commit()
                if not allowed:
                    reserved = 0
                    return await _fail_platform(
                        session_factory,
                        simulation=simulation,
                        platform_key=platform_key,
                        code="simulation_too_large",
                    )
            if actions:
                records, failure = await _run_round_actions(
                    claim,
                    session_factory=session_factory,
                    simulation=simulation,
                    platform_key=platform_key,
                    round_index=round_index,
                    actions=actions,
                    config=config,
                    runtime_dir=runtime_dir,
                )
            else:
                records, failure = [], None
            outcome = await _book_round(
                claim,
                session_factory=session_factory,
                lease_seconds=lease_seconds,
                simulation=simulation,
                platform_key=platform_key,
                round_index=round_index,
                records=records,
                round_outcome="unconfirmed" if failure else "committed",
            )
            if outcome in ("lost", "terminal"):
                return outcome
            if outcome == "ok":
                completed = round_index
            else:
                async with session_factory() as db:
                    completed = await rounds_so_far(
                        db, simulation_id=simulation.id, platform_key=platform_key
                    )
            if failure:
                return await _fail_platform(
                    session_factory,
                    simulation=simulation,
                    platform_key=platform_key,
                    code=failure,
                )
        finally:
            if reserved:
                async with session_factory() as db:
                    await simulation_store.release_actions(
                        db, simulation_id=simulation.id, count=reserved
                    )
                    await db.commit()
        # 本轮已读回并记账；若此刻收到停止请求，不再开始下一轮。
        async with session_factory() as db:
            canceled = await _cancel_requested(db, claim)
            await db.commit()
        if canceled:
            stop_signal.set()
            return "canceled"
    return "ok"


async def _fail_platform(
    session_factory: worker_service.SessionFactory,
    *,
    simulation: simulation_store.SimulationRow,
    platform_key: str,
    code: str,
) -> str:
    logger.warning(
        "运行平台失败 simulation=%s platform=%s code=%s",
        simulation.id,
        platform_key,
        code,
    )
    await _mark_platform_failed(
        session_factory,
        simulation_id=simulation.id,
        platform_key=platform_key,
        code=code,
    )
    return f"platform_failed:{platform_key}:{code}"


async def _all_platforms_complete(
    session_factory: worker_service.SessionFactory,
    *,
    simulation: simulation_store.SimulationRow,
) -> bool:
    async with session_factory() as db:
        result = await db.execute(
            text(
                "SELECT platform_key, rounds_completed FROM simulation_platforms "
                "WHERE simulation_id = :id"
            ),
            {"id": simulation.id},
        )
        rows = result.fetchall()
    if len(rows) != len(PLATFORM_MAPPING):
        return False
    return all(int(row.rounds_completed) >= simulation.rounds for row in rows)


async def _advance(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: DuetConfig,
) -> str:
    scope = worker_service._heartbeat_scope(
        claim,
        session_factory=session_factory,
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )
    async with scope:
        async with session_factory() as db:
            simulation = await simulation_store.get_simulation_for_run(
                db, run_id=claim.run_id
            )
            await db.commit()
        if simulation.terminal:
            return simulation.phase
        stop_signal = asyncio.Event()
        tasks = {
            platform_key: asyncio.create_task(
                _advance_platform(
                    claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                    simulation=simulation,
                    platform_key=platform_key,
                    config=config,
                    stop_event=stop_event,
                    stop_signal=stop_signal,
                )
            )
            for platform_key in PLATFORM_MAPPING
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        failures: list[tuple[str, str]] = []
        canceled = False
        stopped = False
        for platform_key, result in zip(tasks, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "运行平台异常 simulation=%s platform=%s: %s",
                    simulation.id,
                    platform_key,
                    type(result).__name__,
                )
                failures.append((platform_key, f"{platform_key}_action"))
                continue
            if result == "canceled":
                canceled = True
            elif result == "stopped":
                stopped = True
            elif result in ("lost", "terminal"):
                return result
            elif result.startswith("platform_failed:"):
                _, failed_platform, code = result.split(":", 2)
                failures.append((failed_platform, code))
        if stopped:
            return "stopped"
        async with session_factory() as db:
            requested = await _cancel_requested(db, claim)
            await db.commit()
        if canceled or requested:
            return await _finish_run(
                claim,
                session_factory=session_factory,
                lease_seconds=lease_seconds,
                config=config,
                status="canceled",
                failures=failures,
                reason="canceled",
            )
        if failures:
            return await _finish_run(
                claim,
                session_factory=session_factory,
                lease_seconds=lease_seconds,
                config=config,
                status="failed",
                failures=failures,
                reason=failures[0][1],
            )
        if not await _all_platforms_complete(
            session_factory, simulation=simulation
        ):
            failures = [
                (platform_key, f"{platform_key}_record")
                for platform_key in PLATFORM_MAPPING
            ]
            return await _finish_run(
                claim,
                session_factory=session_factory,
                lease_seconds=lease_seconds,
                config=config,
                status="failed",
                failures=failures,
                reason=failures[0][1],
            )
    async with session_factory() as db:
        status, _ = await _sim_guard(db, claim, step=3)
        if status != "ready":
            return status
        simulation = await simulation_store.get_simulation_for_run(
            db, run_id=claim.run_id
        )
        return await _commit_step(
            db,
            claim,
            simulation=simulation,
            step=3,
            step_key=SIM_STEP_KEYS[2],
            lease_seconds=lease_seconds,
            extra={"rounds": simulation.rounds},
        )


async def _cleanup_runtime(
    config: DuetConfig,
    simulation_id: uuid.UUID,
    failures: Sequence[tuple[str, str]],
) -> str | None:
    """终态提交后再清理运行目录；失败只记录原因码，不改变业务终态。"""
    if config.keep_runtime:
        return None
    del failures
    codes: list[str] = []
    for platform_key in PLATFORM_MAPPING:
        if platform_key in config.cleanup_fail:
            codes.append(f"{platform_key}_cleanup")
            continue
        directory = _runtime_dir(config, simulation_id, platform_key)
        try:
            if directory.exists():
                shutil.rmtree(directory)
        except OSError:
            codes.append(f"{platform_key}_cleanup")
    root = _runtime_root(config, simulation_id)
    try:
        if root.exists() and not any(root.iterdir()):
            root.rmdir()
    except OSError:
        codes.append("cleanup_failed")
    return ",".join(dict.fromkeys(codes)) or None


async def _finish_run(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    lease_seconds: float,
    config: DuetConfig,
    status: str,
    failures: Sequence[tuple[str, str]],
    reason: str | None,
) -> str:
    """写终态：步骤、平台状态、终态事件与 job 同事务；清理在提交之后。"""
    async with session_factory() as db:
        row = await worker_service._locked_job(db, claim)
        if row is None:
            await db.rollback()
            return "lost"
        if row.status in worker_service.TERMINAL_STATUSES:  # type: ignore[attr-defined]
            await db.rollback()
            return str(row.status)  # type: ignore[attr-defined]
        db_now = await worker_service._db_now(db)
        if not worker_service._lease_valid(row, claim, db_now):
            await db.rollback()
            return "lost"
        simulation = await simulation_store.get_simulation_for_run(
            db, run_id=claim.run_id
        )
        failed_platforms = [item[0] for item in failures]
        rounds_result = await db.execute(
            text(
                "SELECT platform_key, rounds_completed FROM simulation_platforms "
                "WHERE simulation_id = :id"
            ),
            {"id": simulation.id},
        )
        platform_rounds_completed = {
            row.platform_key: int(row.rounds_completed)
            for row in rounds_result.fetchall()
        }
        for platform_key, _ in failures:
            await db.execute(
                text(
                    "UPDATE simulation_platforms SET status = 'failed', "
                    "failure_code = COALESCE(failure_code, :code), "
                    "updated_at = clock_timestamp() WHERE simulation_id = :id "
                    "AND platform_key = :platform_key"
                ),
                {
                    "code": f"{platform_key}_record",
                    "id": simulation.id,
                    "platform_key": platform_key,
                },
            )
        await db.execute(
            text(
                "UPDATE simulation_platforms SET status = 'succeeded', "
                "updated_at = clock_timestamp() WHERE simulation_id = :id "
                "AND status = 'running' AND rounds_completed >= :rounds"
            ),
            {"id": simulation.id, "rounds": simulation.rounds},
        )
        total, overall, per_platform = await simulation_store.aggregate_counts(
            db, simulation_id=simulation.id
        )
        del total, overall
        for platform_key, counts in per_platform.items():
            await db.execute(
                text(
                    "UPDATE simulation_platforms SET action_count = :count, "
                    "by_action = CAST(:by_action AS jsonb) WHERE simulation_id = :id "
                    "AND platform_key = :platform_key"
                ),
                {
                    "count": sum(counts.values()),
                    "by_action": json.dumps(counts, ensure_ascii=False),
                    "id": simulation.id,
                    "platform_key": platform_key,
                },
            )
        inserted = await db.execute(
            text(
                "INSERT INTO run_steps (run_id, step_index, step_key, payload) "
                "VALUES (:run_id, 4, :step_key, '{}'::jsonb) "
                "ON CONFLICT (run_id, step_index) DO NOTHING"
            ),
            {"run_id": claim.run_id, "step_key": SIM_STEP_KEYS[3]},
        )
        if inserted.rowcount != 1:
            await db.rollback()
            return "duplicate"
        rounds_done = max(
            [platform_rounds_completed.get(platform_key, 0) for platform_key in PLATFORM_MAPPING]
            or [0]
        )
        end_clock = simulation.simulated_start + timedelta(
            minutes=simulation.minutes_per_round
            * (simulation.rounds if status == "succeeded" else rounds_done)
        )
        applied, final = await simulation_store.terminate(
            db,
            simulation=simulation,
            status=status,
            reason=reason,
            failed_platforms=failed_platforms,
            stage=claim.stage,
            simulated_epoch=claim.simulated_epoch,
            simulated_at=end_clock,
        )
        if not applied:
            await db.rollback()
            return final
        await worker_service._finish(db, claim, 4, status=status, reason=reason)
        await db.commit()
    cleanup_code = await _cleanup_runtime(config, simulation.id, failures)
    if cleanup_code:
        async with session_factory() as db:
            await simulation_store.record_cleanup(
                db, simulation_id=simulation.id, code=cleanup_code
            )
            await db.commit()
    return status


async def _publish(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    lease_seconds: float,
    config: DuetConfig,
) -> str:
    async with session_factory() as db:
        status, _ = await _sim_guard(db, claim, step=4)
        if status != "ready":
            return status
        simulation = await simulation_store.get_simulation_for_run(
            db, run_id=claim.run_id
        )
        await db.rollback()
    failures: list[tuple[str, str]] = []
    for platform_key in PLATFORM_MAPPING:
        async with session_factory() as db:
            result = await db.execute(
                text(
                    "SELECT rounds_completed, failure_code FROM simulation_platforms "
                    "WHERE simulation_id = :id AND platform_key = :platform_key"
                ),
                {"id": simulation.id, "platform_key": platform_key},
            )
            row = result.first()
            await db.commit()
        if row is None:
            failures.append((platform_key, f"{platform_key}_setup"))
            continue
        if row.failure_code:
            failures.append((platform_key, row.failure_code))
        elif int(row.rounds_completed) < simulation.rounds:
            failures.append((platform_key, f"{platform_key}_record"))
    return await _finish_run(
        claim,
        session_factory=session_factory,
        lease_seconds=lease_seconds,
        config=config,
        status="succeeded" if not failures else "failed",
        failures=failures,
        reason=None if not failures else failures[0][1],
    )


async def _fail(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    config: DuetConfig,
    code: str,
    message: str,
    failed_platforms: Sequence[str],
) -> str:
    logger.warning("运行失败 job_id=%s code=%s", claim.job_id, code)
    del message
    async with session_factory() as db:
        row = await worker_service._locked_job(db, claim)
        if row is None:
            await db.rollback()
            return "lost"
        if row.status in worker_service.TERMINAL_STATUSES:  # type: ignore[attr-defined]
            await db.rollback()
            return str(row.status)  # type: ignore[attr-defined]
        db_now = await worker_service._db_now(db)
        if not worker_service._lease_valid(row, claim, db_now):
            await db.rollback()
            return "lost"
        simulation = await simulation_store.get_simulation_for_run(
            db, run_id=claim.run_id
        )
        applied, final = await simulation_store.terminate(
            db,
            simulation=simulation,
            status="failed",
            reason=code,
            failed_platforms=list(failed_platforms),
            stage=claim.stage,
            simulated_epoch=claim.simulated_epoch,
            simulated_at=simulation.simulated_start,
        )
        if not applied:
            await db.rollback()
            return final
        await worker_service._finish(
            db,
            claim,
            int(row.next_step),  # type: ignore[attr-defined]
            status="failed",
            reason=code,
        )
        await db.commit()
    cleanup_code = await _cleanup_runtime(config, simulation.id, [])
    if cleanup_code:
        async with session_factory() as db:
            await simulation_store.record_cleanup(
                db, simulation_id=simulation.id, code=cleanup_code
            )
            await db.commit()
    return "failed"


async def execute_simulation(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: DuetConfig,
) -> str:
    """按四步执行双平台运行；失败写稳定原因码，不删除已有动作与计数。"""
    try:
        for step in range(claim.next_step + 1, SIM_TOTAL_STEPS + 1):
            if stop_event is not None and stop_event.is_set():
                return "stopped"
            if step == 1:
                outcome = await _bind(
                    claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                )
            elif step == 2:
                outcome = await _environment(
                    claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                    config=config,
                )
            elif step == 3:
                outcome = await _advance(
                    claim,
                    session_factory=session_factory,
                    stop_event=stop_event,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                )
            else:
                outcome = await _publish(
                    claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                    config=config,
                )
            if outcome in ("succeeded", "failed", "canceled"):
                return outcome
            if outcome in ("progress", "duplicate"):
                continue
            return outcome
        return "succeeded"
    except DuetContractError as exc:
        return await _fail(
            claim,
            session_factory=session_factory,
            config=config,
            code=exc.code,
            message=exc.message,
            failed_platforms=[],
        )
    except LeaseLost:
        return "lost"
    except Exception as exc:  # noqa: BLE001 - 单任务异常写稳定终态
        logger.exception("运行异常 job_id=%s: %s", claim.job_id, type(exc).__name__)
        return await _fail(
            claim,
            session_factory=session_factory,
            config=config,
            code="simulation_failed",
            message="运行执行失败",
            failed_platforms=[],
        )


async def engine_counts(
    *, simulation_id: uuid.UUID, config: DuetConfig
) -> dict[str, dict[str, int]]:
    """从引擎数据库统计各平台原生动作数，供验收三方核对。"""
    return {
        platform_key: native_counts(
            _runtime_dir(config, simulation_id, platform_key), platform_key
        )
        for platform_key in PLATFORM_MAPPING
    }
