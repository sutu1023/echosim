from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from echosim.domain.accounts import Fault


class PreparationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idea: str
    base_version_id: str | None = None


class ConfirmationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str
    expected_candidate_id: str


def register_story(app, service, current, write_user):
    prefix = "/api/v1/works/{work_id}"

    @app.post(prefix + "/preparation-tasks")
    async def create(request: Request, work_id: str, body: PreparationInput):
        user = await write_user(request)
        row = await service.submit(
            user["user_id"],
            work_id,
            "preparation",
            request.headers.get("idempotency-key"),
            body.model_dump(),
        )
        return JSONResponse(jsonable_encoder(row), status_code=202)

    @app.get(prefix + "/preparations")
    async def history(request: Request, work_id: str):
        user = await current(request)
        if request.query_params:
            raise Fault("INVALID_REQUEST")
        return await service.history(user["user_id"], work_id)

    @app.post(prefix + "/preparation-confirmations")
    async def confirm(request: Request, work_id: str, body: ConfirmationInput):
        user = await write_user(request)
        return await service.confirm(
            user["user_id"], work_id, body.version_id, body.expected_candidate_id
        )

    @app.get(prefix + "/tasks/{task_id}")
    async def task(request: Request, work_id: str, task_id: str):
        user = await current(request)
        return await service.task(user["user_id"], work_id, task_id)
