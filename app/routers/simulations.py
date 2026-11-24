"""双平台运行接口：开始、列表、详情、动作分页与停止。

顺序固定为“身份 → 项目归属 → 运行归属”：匿名请求在访问运行数据前 401，
他人项目与不存在项目统一 404，运行事件复用项目内任务的事件流。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import require_user
from ..engines.duet_config import DuetConfig
from ..errors import InvalidCursor
from ..schemas import SimulationStartRequest
from ..services import projects
from ..services import simulation as simulation_service
from ..services.sessions import SessionUser

router = APIRouter(
    prefix="/api/projects/{project_id}/simulations", tags=["simulations"]
)


@router.post("")
async def start_simulation(
    project_id: str,
    payload: SimulationStartRequest,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    config = DuetConfig.from_env()
    simulation, created = await simulation_service.start_simulation(
        db,
        project_id=project.id,
        actor_id=user.id,
        selection_id=payload.selectionId,
        engine_mode=payload.engineMode,
        allow_oasis=config.allow_oasis,
        default_engine=config.engine,
    )
    body = {
        "simulation": await simulation_service.serialize_simulation(db, simulation),
        "deduplicated": not created,
    }
    return JSONResponse(body, status_code=202 if created else 200)


@router.get("")
async def list_simulations(
    project_id: str,
    selection: str | None = None,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    simulations = await simulation_service.list_simulations(
        db, project_id=project.id, selection_id=selection
    )
    current = await simulation_service.default_simulation(
        db, project_id=project.id
    )
    return {
        "simulations": await simulation_service.serialize_list(db, simulations),
        "simulation": (
            None
            if current is None
            else await simulation_service.serialize_simulation(db, current)
        ),
    }


@router.get("/{run_id}")
async def simulation_detail(
    project_id: str,
    run_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    simulation = await simulation_service.get_simulation(
        db, project_id=project.id, run_id=run_id
    )
    return {
        "simulation": await simulation_service.serialize_simulation(
            db, simulation
        )
    }


@router.get("/{run_id}/actions")
async def simulation_actions(
    project_id: str,
    run_id: str,
    platform: str | None = None,
    after: int = 0,
    limit: int = 50,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    simulation = await simulation_service.get_simulation(
        db, project_id=project.id, run_id=run_id
    )
    if after < 0:
        raise InvalidCursor("游标不能为负数")
    page = await simulation_service.action_page(
        db,
        simulation_id=simulation.id,
        platform_key=platform,
        after=after,
        limit=limit,
    )
    return {"runId": str(simulation.run_id), **page}


@router.post("/{run_id}/stop")
async def stop_simulation(
    project_id: str,
    run_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    simulation, applied = await simulation_service.stop_simulation(
        db, project_id=project.id, run_id=run_id
    )
    return {
        "cancelApplied": applied,
        "simulation": await simulation_service.serialize_simulation(db, simulation),
    }
