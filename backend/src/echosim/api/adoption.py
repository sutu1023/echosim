from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt


class Adopt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str | None = None
    expected_canon_revision: StrictInt
    acknowledge_irreversible: StrictBool


class Discard(BaseModel):
    model_config = ConfigDict(extra="forbid")


def register_adoption(app, service, write_user):
    prefix = "/api/v1/works/{work_id}/simulations/{task_id}"

    @app.post(prefix + "/adoptions")
    async def adopt(request: Request, work_id: str, task_id: str, body: Adopt):
        user = await write_user(request)
        row = await service.choose(
            user["user_id"],
            work_id,
            task_id,
            request.headers.get("idempotency-key"),
            body.model_dump(exclude_none=True),
        )
        return JSONResponse(jsonable_encoder(row), status_code=202)

    @app.post(prefix + "/discards")
    async def discard(request: Request, work_id: str, task_id: str, body: Discard):
        user = await write_user(request)
        row = await service.choose(
            user["user_id"],
            work_id,
            task_id,
            request.headers.get("idempotency-key"),
            {},
            discard=True,
        )
        return JSONResponse(jsonable_encoder(row))
