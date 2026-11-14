from typing import Literal

from fastapi import Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from echosim.domain.accounts import Fault


class ChapterAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["generate", "review", "save", "replace"]
    version: int = Field(ge=0)
    title: str = Field(default="", max_length=160)
    body: str = Field(default="", max_length=30000)
    candidate: str | None = None


def register_chapters(app, service, current, write_user):
    path = "/api/v1/works/{work_id}/simulations/{task_id}/chapters"

    @app.get(path)
    async def read(request: Request, work_id: str, task_id: str):
        user = await current(request)
        return await service.read(user["user_id"], work_id, task_id)

    @app.post(path)
    async def change(
        request: Request, work_id: str, task_id: str, body: ChapterAction, response: Response
    ):
        user = await write_user(request)
        result = await service.change(
            user["user_id"],
            work_id,
            task_id,
            request.headers.get("idempotency-key"),
            **body.model_dump(),
        )
        response.status_code = 200 if result.get("saved") else 202
        return result

    @app.get(path + "/markdown")
    async def export(request: Request, work_id: str, task_id: str):
        user = await current(request)
        draft = (await service.read(user["user_id"], work_id, task_id))["draft"]
        if not draft:
            raise Fault("NOT_FOUND", 404)
        return Response(
            f"# {draft['title']}\n\n{draft['body']}\n",
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="chapter.md"'},
        )
