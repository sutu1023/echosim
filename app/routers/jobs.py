"""任务路由：开始、列表、详情、取消、事件订阅与测试副作用。

顺序固定为“身份 → 项目归属 → 项目内任务 → 游标”，因此匿名与越权请求在建立
SSE 响应之前就得到 JSON 错误，不会泄露任何事件内容。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal, get_db
from ..deps import require_user
from ..schemas import JobStartRequest
from ..services import jobs as job_service
from ..services import run_events
from ..services import projects
from ..services.sessions import SessionUser

router = APIRouter(prefix="/api/projects/{project_id}/jobs", tags=["jobs"])


@router.post("")
async def start_job(
    project_id: str,
    payload: JobStartRequest,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    job, created = await job_service.start_job(
        db,
        project_id=project.id,
        actor_id=user.id,
        stage=payload.stage,
        fail_at_step=payload.failAtStep,
    )
    body = {"job": job_service.job_body(job, deduplicated=not created)}
    return JSONResponse(body, status_code=202 if created else 200)


@router.get("")
async def list_jobs(
    project_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    items = await job_service.list_jobs(db, project_id=project.id)
    return {"jobs": [job_service.job_body(item) for item in items]}


@router.get("/{job_id}")
async def job_detail(
    project_id: str,
    job_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    job = await job_service.get_job(db, project_id=project.id, job_id=job_id)
    return {"job": job_service.job_body(job)}


@router.post("/{job_id}/cancel")
async def cancel_job(
    project_id: str,
    job_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    job, applied = await job_service.cancel_job(
        db, project_id=project.id, job_id=job_id
    )
    return {"job": job_service.job_body(job), "cancelApplied": applied}


@router.get("/{job_id}/effects")
async def job_effects(
    project_id: str,
    job_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    return await job_service.get_effects(
        db, project_id=project.id, job_id=job_id
    )


@router.get("/{job_id}/events")
async def job_events(
    project_id: str,
    job_id: str,
    request: Request,
    after: str | None = None,
    format: Literal["sse", "json"] = "sse",
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """事件订阅：默认 SSE，`format=json` 返回同一游标的补发结果。"""
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    job = await job_service.get_job(db, project_id=project.id, job_id=job_id)
    last_seq = await run_events.max_seq(db, run_id=job.run_id)
    cursor = run_events.parse_cursor(
        request.headers.get("last-event-id"),
        after,
        run_id=job.run_id,
        last_seq=last_seq,
    )

    if format == "json":
        events = await run_events.list_events_after(
            db, run_id=job.run_id, cursor=cursor
        )
        events_last = events[-1].seq if events else cursor
        terminal = await run_events.terminal_seq(db, run_id=job.run_id)
        return {
            "runId": str(job.run_id),
            "jobId": str(job.id),
            "after": cursor,
            "lastSeq": max(last_seq, events_last),
            "terminal": terminal is not None and events_last >= terminal,
            "events": [run_events.serialize(event) for event in events],
        }

    async def generate():
        async for chunk in run_events.event_stream(
            session_factory=SessionLocal,
            run_id=job.run_id,
            cursor=cursor,
            is_disconnected=request.is_disconnected,
        ):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers=run_events.SSE_HEADERS,
    )
