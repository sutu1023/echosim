"""验收专用探针：在真实 PostgreSQL 上回放租约与终态竞态。

只在 `make verify CHANGE=...` 中通过 `docker compose exec` 调用，不在 `app` 或
`worker` 的正常请求路径上；它复用生产代码的领取与步骤提交函数，因此结论对应真实
事务语义，而不是测试替身。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import SessionLocal, dispose_engine
from .services import jobs as job_service
from .services import worker as worker_service

logger = logging.getLogger("echosim.lease_probe")
PROBE_STAGE = "dual-platform-run"


async def _create_probe_job(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    operation_key: str,
    total_steps: int = 10,
) -> tuple[uuid.UUID, uuid.UUID]:
    owner = (
        await db.execute(
            text("SELECT owner_id FROM projects WHERE id = :project_id"),
            {"project_id": project_id},
        )
    ).scalar_one()
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO runs (id, project_id, created_by, kind, stage, total_steps, "
            "fail_at_step, simulated_epoch) "
            "VALUES (:id, :project_id, :created_by, 'test.simulation', :stage, "
            ":total_steps, NULL, now())"
        ),
        {
            "id": run_id,
            "project_id": project_id,
            "created_by": owner,
            "stage": PROBE_STAGE,
            "total_steps": total_steps,
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
            "operation_key": operation_key,
        },
    )
    await db.commit()
    return job_id, run_id


async def _drop_probe_job(
    db: AsyncSession, job_ids: list[uuid.UUID], run_ids: list[uuid.UUID]
) -> None:
    if job_ids:
        await db.execute(
            text("DELETE FROM jobs WHERE id = ANY(:ids)"), {"ids": job_ids}
        )
    if run_ids:
        await db.execute(
            text("DELETE FROM runs WHERE id = ANY(:ids)"), {"ids": run_ids}
        )
    await db.commit()


async def _snapshot(db: AsyncSession, job_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT j.status, j.next_step, j.attempts, j.lease_token::text AS token, "
                "j.lease_expires_at, j.run_id "
                "FROM jobs j WHERE j.id = :job_id"
            ),
            {"job_id": job_id},
        )
    ).first()
    seqs = [
        int(item.seq)
        for item in (
            await db.execute(
                text(
                    "SELECT seq FROM run_events WHERE run_id = :run_id ORDER BY seq"
                ),
                {"run_id": row.run_id},
            )
        ).fetchall()
    ]
    effects = int(
        (
            await db.execute(
                text("SELECT COUNT(*) FROM run_steps WHERE run_id = :run_id"),
                {"run_id": row.run_id},
            )
        ).scalar_one()
    )
    return {
        "status": row.status,
        "next_step": int(row.next_step),
        "attempts": int(row.attempts),
        "lease_token": row.token,
        "lease_expires_at": row.lease_expires_at.isoformat()
        if row.lease_expires_at
        else None,
        "seqs": seqs,
        "effects": effects,
    }


async def _commit_steps(
    claim: worker_service.Claim, steps: range, *, lease_seconds: float
) -> list[str]:
    outcomes: list[str] = []
    for step in steps:
        async with SessionLocal() as db:
            outcome = await worker_service.commit_step(
                db, claim, step=step, lease_seconds=lease_seconds
            )
        outcomes.append(outcome.kind)
        if outcome.kind not in ("progress", "completed"):
            break
    return outcomes


async def mode_two_jobs(project_id: uuid.UUID) -> dict[str, Any]:
    """A 推进一步时 B 的进度、状态与租约必须完全不变。"""
    lease = settings.job_lease_seconds
    async with SessionLocal() as db:
        job_a, run_a = await _create_probe_job(
            db, project_id=project_id, operation_key="lease-probe-a"
        )
        job_b, run_b = await _create_probe_job(
            db, project_id=project_id, operation_key="lease-probe-b"
        )
        claim_a = await worker_service.claim_next(
            db, worker_id="probe-a", lease_seconds=lease, job_id=job_a
        )
        claim_b = await worker_service.claim_next(
            db, worker_id="probe-b", lease_seconds=lease, job_id=job_b
        )
        if claim_a is None or claim_b is None:
            raise RuntimeError("两个探针任务未能同时领取")
        before = await _snapshot(db, job_b)
        async with SessionLocal() as other:
            outcome = await worker_service.commit_step(
                other, claim_a, step=1, lease_seconds=lease
            )
        after = await _snapshot(db, job_b)
        unchanged = before == after
        result = {
            "mode": "two-jobs",
            "a_step_outcome": outcome.kind,
            "a_next_step": 1,
            "b_unchanged": unchanged,
            "b_before": before,
            "b_after": after,
        }
        await _drop_probe_job(db, [job_a, job_b], [run_a, run_b])
    return result


async def mode_counts(project_id: uuid.UUID) -> dict[str, Any]:
    async with SessionLocal() as db:
        jobs = int(
            (
                await db.execute(
                    text("SELECT COUNT(*) FROM jobs WHERE project_id = :p"),
                    {"p": project_id},
                )
            ).scalar_one()
        )
        runs = int(
            (
                await db.execute(
                    text("SELECT COUNT(*) FROM runs WHERE project_id = :p"),
                    {"p": project_id},
                )
            ).scalar_one()
        )
    return {"mode": "counts", "jobs": jobs, "runs": runs}


async def mode_takeover(job_id: uuid.UUID) -> dict[str, Any]:
    """旧令牌在接手后不能续租，也不能写第 6 步。"""
    lease = settings.job_lease_seconds
    async with SessionLocal() as db:
        claim_old = await worker_service.claim_next(
            db, worker_id="probe-old", lease_seconds=lease, job_id=job_id
        )
    if claim_old is None:
        raise RuntimeError("探针任务未被第一次领取")

    first = await _commit_steps(claim_old, range(1, 4), lease_seconds=lease)

    async with SessionLocal() as db:
        await db.execute(
            text(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :job_id"
            ),
            {"job_id": job_id},
        )
        await db.commit()
        claim_new = await worker_service.claim_next(
            db, worker_id="probe-new", lease_seconds=lease, job_id=job_id
        )
        if claim_new is None:
            raise RuntimeError("过期租约未被第二次领取")

    second = await _commit_steps(claim_new, range(4, 6), lease_seconds=lease)

    async with SessionLocal() as db:
        settled = await _snapshot(db, job_id)
        heartbeat_ok = await worker_service.heartbeat(
            db, claim_old, lease_seconds=lease
        )
        stale = await worker_service.commit_step(
            db, claim_old, step=6, lease_seconds=lease
        )
        after_stale = await _snapshot(db, job_id)

    third = await _commit_steps(claim_new, range(6, 11), lease_seconds=lease)

    async with SessionLocal() as db:
        final = await _snapshot(db, job_id)

    return {
        "mode": "takeover",
        "first_segment": first,
        "second_segment": second,
        "third_segment": third,
        "steps_at_handover": settled["next_step"],
        "old_heartbeat_ok": heartbeat_ok,
        "old_step_outcome": stale.kind,
        "unchanged_after_old_token": settled == after_stale,
        "attempts": final["attempts"],
        "final_status": final["status"],
        "final_seqs": final["seqs"],
        "final_effects": final["effects"],
    }


async def mode_cancel_race(project_id: uuid.UUID, trials: int = 5) -> dict[str, Any]:
    """最后一步提交与取消竞争：每次只能留下一个终态事件。"""
    lease = settings.job_lease_seconds
    results: list[dict[str, Any]] = []
    for _ in range(trials):
        async with SessionLocal() as db:
            job_id, run_id = await _create_probe_job(
                db, project_id=project_id, operation_key=f"cancel-race-{uuid.uuid4().hex[:8]}", total_steps=1
            )
            claim = await worker_service.claim_next(
                db, worker_id="probe-race", lease_seconds=lease, job_id=job_id
            )
        if claim is None:
            raise RuntimeError("竞态探针任务未被领取")

        async def commit() -> str:
            async with SessionLocal() as db:
                outcome = await worker_service.commit_step(
                    db, claim, step=1, lease_seconds=lease
                )
            return outcome.kind

        async def cancel() -> bool:
            async with SessionLocal() as db:
                _, applied = await job_service.cancel_job(
                    db, project_id=project_id, job_id=job_id
                )
            return applied

        commit_result, cancel_result = await asyncio.gather(
            commit(), cancel(), return_exceptions=True
        )

        async with SessionLocal() as db:
            status = (
                await db.execute(
                    text("SELECT status FROM jobs WHERE id = :job_id"),
                    {"job_id": job_id},
                )
            ).scalar_one()
            types = [
                item.type
                for item in (
                    await db.execute(
                        text(
                            "SELECT type FROM run_events WHERE run_id = :run_id "
                            "ORDER BY seq"
                        ),
                        {"run_id": run_id},
                    )
                ).fetchall()
            ]
            await _drop_probe_job(db, [job_id], [run_id])

        terminals = [item for item in types if item in ("completed", "failed", "canceled")]
        consistent = (
            len(terminals) == 1
            and (
                (status == "succeeded" and "completed" in types and "canceled" not in types)
                or (status == "canceled" and "canceled" in types and "completed" not in types)
            )
        )
        results.append(
            {
                "status": status,
                "types": types,
                "commit": commit_result
                if isinstance(commit_result, str)
                else type(commit_result).__name__,
                "cancel_applied": cancel_result
                if isinstance(cancel_result, bool)
                else type(cancel_result).__name__,
                "single_terminal": len(terminals) == 1,
                "consistent": consistent,
            }
        )
    return {
        "mode": "cancel-race",
        "trials": results,
        "ok": all(item["consistent"] for item in results),
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.mode == "counts":
        return await mode_counts(uuid.UUID(args.project_id))
    if args.mode == "two-jobs":
        return await mode_two_jobs(uuid.UUID(args.project_id))
    if args.mode == "takeover":
        return await mode_takeover(uuid.UUID(args.job_id))
    if args.mode == "cancel-race":
        return await mode_cancel_race(uuid.UUID(args.project_id), trials=args.trials)
    raise SystemExit(f"未知模式：{args.mode}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EchoSim 租约与终态竞态验收探针")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("two-jobs", "counts", "takeover", "cancel-race"),
    )
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args(argv)
    if args.mode in ("counts", "two-jobs", "cancel-race") and not args.project_id:
        parser.error("该模式需要 --project-id")
    if args.mode == "takeover" and not args.job_id:
        parser.error("takeover 模式需要 --job-id")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    args = parse_args(argv)

    async def runner() -> dict[str, Any]:
        try:
            return await _run(args)
        finally:
            await dispose_engine()

    try:
        payload = asyncio.run(runner())
    except Exception as exc:  # noqa: BLE001 - 探针失败需要结构化输出
        print(json.dumps({"mode": args.mode, "error": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
