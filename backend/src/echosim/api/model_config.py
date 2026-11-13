from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, SecretStr

from echosim.api.model_connection import MESSAGES
from echosim.domain.accounts import Fault
from echosim.domain.model_connection import ModelFailure


class ConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    base_url: str
    model: str
    api_key: SecretStr
    expected_version: str | None = None


def register_model_config(app, service, current, write_user):
    @app.exception_handler(ModelFailure)
    async def model_failure(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": MESSAGES.get(exc.code, "模型调用失败")},
                "request_id": request.state.request_id,
            },
        )

    @app.get("/api/v1/model-configurations")
    async def get_current(request: Request):
        user = await current(request)
        if request.query_params:
            raise Fault("INVALID_REQUEST")
        return {"configuration": await service.current(user["user_id"])}

    @app.get("/api/v1/model-configurations/{version}")
    async def get_version(request: Request, version: str):
        user = await current(request)
        return await service.get(user["user_id"], version)

    @app.post("/api/v1/model-configurations")
    async def save(request: Request, body: ConfigurationInput):
        user = await write_user(request)
        return await service.save(
            user["user_id"],
            body.base_url,
            body.model,
            body.api_key.get_secret_value(),
            body.expected_version,
        )

    @app.post("/api/v1/model-configuration-tests")
    async def test(request: Request, body: ConfigurationInput):
        user = await write_user(request)
        if body.expected_version:
            await service.get(user["user_id"], body.expected_version)
        return await service.test(body.base_url, body.model, body.api_key.get_secret_value())
