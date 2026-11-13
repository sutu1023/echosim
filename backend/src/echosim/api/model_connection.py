import time

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from echosim.domain.model_connection import MODEL, ModelFailure
from echosim.logging import emit

MESSAGES = {
    "MODEL_AUTH_FAILED": "供应商鉴权失败，请检查 KEY 与订阅权限",
    "MODEL_RATE_LIMITED": "供应商额度不足或请求受限，请稍后手动重试",
    "MODEL_TIMEOUT": "模型调用超过 60 秒，请稍后手动重试",
    "MODEL_UPSTREAM_ERROR": "无法完成供应商请求，请检查网络或稍后重试",
    "MODEL_INVALID_RESPONSE": "供应商未返回有效的文本回答",
}


class TestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        raw = value.get_secret_value()
        if not raw.strip() or len(raw) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
            raise ValueError("Invalid credential")
        return value


def register_model_test(app, service):
    @app.get("/api/v1/model-connection-tests")
    async def available():
        return JSONResponse({"enabled": True}, headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/model-connection-tests")
    async def test_model(body: TestInput, request: Request):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {"code": "ORIGIN_FORBIDDEN", "message": "不允许跨站请求"},
                    "request_id": request.state.request_id,
                },
            )
        started = time.monotonic()
        try:
            result = await service.test(body.api_key.get_secret_value())
        except ModelFailure as exc:
            emit(
                "model_connection_test_failed",
                service="api",
                request_id=request.state.request_id,
                model=MODEL,
                configuration_version="opencode-go-test-v1",
                prompt_version="v1",
                timeout_seconds=60,
                max_tokens=256,
                temperature="provider-default",
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                usage=None,
                error_code=exc.code,
            )
            return JSONResponse(
                status_code=exc.status,
                content={
                    "error": {"code": exc.code, "message": MESSAGES[exc.code]},
                    "request_id": request.state.request_id,
                },
            )
        emit(
            "model_connection_test",
            service="api",
            request_id=request.state.request_id,
            model=result["model"],
            configuration_version="opencode-go-test-v1",
            prompt_version="v1",
            timeout_seconds=60,
            max_tokens=256,
            temperature="provider-default",
            duration_ms=result["duration_ms"],
            usage=result["usage"],
        )
        return {**result, "request_id": request.state.request_id}
