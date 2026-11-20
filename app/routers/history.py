"""历史路由：跨项目的自有事件入口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import require_user
from ..services import history
from ..services.sessions import SessionUser

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
async def list_history(
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    events = await history.list_owned_history(db, owner_id=user.id)
    return {
        "events": [
            {
                "id": str(event.id),
                "project_id": str(event.project_id),
                "project_name": event.project_name,
                "type": event.type,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in events
        ]
    }
