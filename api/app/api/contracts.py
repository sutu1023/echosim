from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

ERRORS = {
    "INVALID_INPUT": (422, "请求参数不符合要求，请检查标记字段后重试。"),
    "NOT_FOUND": (404, "请求的资源不可用，请检查地址或返回首页。"),
    "ACCESS_DENIED": (403, "此入口尚未开放，请返回已登记的页面。"),
    "METHOD_NOT_ALLOWED": (405, "此入口不支持该操作，请检查请求方法。"),
    "DEPENDENCY_UNAVAILABLE": (503, "服务尚未就绪，请检查数据库连接后重试。"),
    "INTERNAL_ERROR": (500, "本次请求未能完成，请稍后重试并提供请求编号。"),
}


def respond(request: Request, code: str = "OK", data: Any = None) -> JSONResponse:
    status, message = (200, "操作成功") if code == "OK" else ERRORS[code]
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "data": data,
            "request_id": request.state.request_id,
        },
    )
