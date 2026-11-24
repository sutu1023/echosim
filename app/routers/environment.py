"""角色与环境准备接口：上下文、开始/重试、详情、轮数与确认冻结。

顺序固定为“身份 → 项目归属 → 资源归属”：匿名请求在访问准备数据前 401，
他人项目与不存在项目统一 404，准备接口只返回中文说明与稳定原因码。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import require_user
from ..engines.environment_config import EnvironmentConfig
from ..schemas import (
    EnvironmentConfigurationRequest,
    EnvironmentConfirmRequest,
)
from ..services import environment as environment_service
from ..services import projects
from ..services.sessions import SessionUser

router = APIRouter(
    prefix="/api/projects/{project_id}/environment", tags=["environment"]
)


@router.get("")
async def get_environment_context(
    project_id: str,
    build: str | None = None,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    context = await environment_service.read_context(
        db, project_id=project.id, build_id=build
    )
    return environment_service.context_body(context)


async def _start(
    *,
    db: AsyncSession,
    project_id: uuid.UUID,
    user: SessionUser,
    build_id: uuid.UUID | str,
    retry: bool,
) -> JSONResponse:
    config = EnvironmentConfig.from_env()
    preparation, created = await environment_service.start_preparation(
        db,
        project_id=project_id,
        actor_id=user.id,
        build_id=build_id,
        config=config,
        retry=retry,
    )
    body = await environment_service.serialize_preparation(db, preparation)
    return JSONResponse(
        {"preparation": body}, status_code=202 if created else 200
    )


@router.post("/preparations")
async def start_preparation(
    project_id: str,
    build: str | None = None,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    context = await environment_service.read_context(
        db, project_id=project.id, build_id=build
    )
    return await _start(
        db=db,
        project_id=project.id,
        user=user,
        build_id=context.build.id,
        retry=False,
    )


@router.post("/preparations/{preparation_id}/retry")
async def retry_preparation(
    project_id: str,
    preparation_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    preparation = await environment_service.get_preparation(
        db, project_id=project.id, preparation_id=preparation_id
    )
    return await _start(
        db=db,
        project_id=project.id,
        user=user,
        build_id=preparation.build_id,
        retry=True,
    )


@router.get("/preparations/{preparation_id}")
async def preparation_detail(
    project_id: str,
    preparation_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    preparation = await environment_service.get_preparation(
        db, project_id=project.id, preparation_id=preparation_id
    )
    return {
        "preparation": await environment_service.serialize_preparation(
            db, preparation
        )
    }


@router.patch("/preparations/{preparation_id}/configuration")
async def save_configuration(
    project_id: str,
    preparation_id: str,
    payload: EnvironmentConfigurationRequest,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    preparation = await environment_service.update_configuration(
        db,
        project_id=project.id,
        preparation_id=preparation_id,
        settings_revision=payload.settingsRevision,
        duration_hours=payload.durationHours,
        minutes_per_round=payload.minutesPerRound,
        rounds_mode=payload.roundsMode,
        manual_rounds=payload.manualRounds,
        config=EnvironmentConfig.from_env(),
    )
    return {
        "preparation": await environment_service.serialize_preparation(
            db, preparation
        )
    }


@router.post("/preparations/{preparation_id}/confirm")
async def confirm_preparation(
    project_id: str,
    preparation_id: str,
    payload: EnvironmentConfirmRequest,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    preparation, frozen = await environment_service.confirm_preparation(
        db,
        project_id=project.id,
        preparation_id=preparation_id,
        settings_revision=payload.settingsRevision,
    )
    body = await environment_service.serialize_preparation(db, preparation)
    body["frozenSelection"] = frozen
    body["canContinue"] = True
    return {"preparation": body, "frozenSelection": frozen}
