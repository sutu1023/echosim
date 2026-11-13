import asyncio
import json

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from echosim.domain.accounts import Fault


class StartSimulation(BaseModel):
    model_config = ConfigDict(extra="forbid")


def register_simulation(app, service, current, write_user):
    prefix = "/api/v1/works/{work_id}/simulations"

    @app.post(prefix)
    async def start(request: Request, work_id: str, body: StartSimulation):
        user = await write_user(request)
        row = await service.stories.submit(
            user["user_id"], work_id, "simulation", request.headers.get("idempotency-key"), {}
        )
        return JSONResponse(jsonable_encoder(row), status_code=202)

    def cursor(request):
        if set(request.query_params) - {"after"} or len(request.query_params.multi_items()) != len(
            request.query_params
        ):
            raise Fault("INVALID_REQUEST")
        try:
            return int(request.query_params.get("after", request.headers.get("last-event-id", "0")))
        except ValueError:
            raise Fault("INVALID_REQUEST") from None

    @app.get(prefix + "/{task_id}")
    @app.get(prefix + "/{task_id}/events")
    async def read(request: Request, work_id: str, task_id: str):
        user = await current(request)
        return await service.read(user["user_id"], work_id, task_id, cursor(request))

    @app.post(prefix + "/{task_id}/stop")
    async def stop(request: Request, work_id: str, task_id: str):
        user = await write_user(request)
        return await service.stop(user["user_id"], work_id, task_id)

    @app.get(prefix + "/{task_id}/stream")
    async def stream(request: Request, work_id: str, task_id: str):
        user = await current(request)
        after = cursor(request)
        await service.read(user["user_id"], work_id, task_id, after)

        async def events():
            position = after
            while not await request.is_disconnected():
                try:
                    user = await current(request)
                    data = await service.read(user["user_id"], work_id, task_id, position)
                    for event in data["events"]:
                        await current(request)
                        position = event["sequence"]
                        body = json.dumps(jsonable_encoder(event), ensure_ascii=False)
                        yield f"id: {position}\ndata: {body}\n\n"
                    yield "event: status\ndata: " + json.dumps({"state": data["state"]}) + "\n\n"
                    if data["state"] in {"succeeded", "failed", "stopped"}:
                        return
                    await asyncio.sleep(1)
                except Fault:
                    return

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )
