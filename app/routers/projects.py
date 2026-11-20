"""项目路由：创建、列表与详情；归属校验全部委托服务层。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import require_user
from ..schemas import ProjectCreateRequest
from ..services import projects
from ..services.projects import Project
from ..services.sessions import SessionUser

router = APIRouter(prefix="/api/projects", tags=["projects"])


def project_body(project: Project) -> dict[str, object]:
    return {
        "id": str(project.id),
        "name": project.name,
        "description": project.description,
        "created_at": project.created_at.isoformat(),
    }


@router.get("")
async def list_projects(
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    owned = await projects.list_owned_projects(db, owner_id=user.id)
    return {"projects": [project_body(item) for item in owned]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.create_project(
        db,
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
    )
    return {"project": project_body(project)}


@router.get("/{project_id}")
async def project_detail(
    project_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    return {"project": project_body(project)}
