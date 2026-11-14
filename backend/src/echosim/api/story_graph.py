from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def register_graph(app, service, current, write_user):
    prefix = "/api/v1/works/{work_id}/story-graph"

    @app.get(prefix)
    async def read(request: Request, work_id: str):
        user = await current(request)
        return JSONResponse(jsonable_encoder(await service.read(user["user_id"], work_id)))

    @app.post(prefix + "/builds", status_code=202)
    async def build(request: Request, work_id: str):
        user = await write_user(request)
        return await service.build(user["user_id"], work_id)
