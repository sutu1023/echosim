import hmac
import json

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from echosim.domain.accounts import Fault

COOKIE = "echosim_session"
MESSAGES = {
    "CHAPTER_STALE": "草稿版本已变化，请重新加载并核对",
    "CHAPTER_REVIEW_REQUIRED": "请先核对章节修改稿",
    "GRAPH_NOT_READY": "初始图谱尚未就绪，请等待构建或重试",
    "RESULT_PENDING": "请先采纳或放弃本次推演",
    "RESULT_INELIGIBLE": "本次结果不可采纳或已处理",
    "CANON_CONFLICT": "故事主线已变化，请刷新后操作",
    "PREPARATION_FROZEN": "已有正式剧情，故事设定已锁定",
    "STORY_CAPACITY": "故事内容超过当前验证容量，未继续调用模型",
    "MODEL_CONFIGURATION_REQUIRED": "请先在模型设置中保存有效配置",
    "MODEL_CREDENTIAL_UNAVAILABLE": "模型凭据不可用，请重新保存配置",
    "CONFIGURATION_CONFLICT": "配置已更新，请重新加载后操作",
    "CONFIRMATION_REQUIRED": "请先确认故事设定与角色",
    "WORK_BUSY": "作品已有任务正在执行，请稍后重试",
    "CANDIDATE_CONFLICT": "候选版本已变化，请重新加载后确认",
    "INVALID_REQUEST": "请求参数无效",
    "UNAUTHENTICATED": "请重新登录",
    "AUTHENTICATION_FAILED": "用户名或密码错误",
    "RATE_LIMITED": "尝试过于频繁，请稍后重试",
    "CSRF_REJECTED": "请求来源或会话校验失败，请刷新重试",
    "NOT_FOUND": "资源不存在",
    "IDEMPOTENCY_CONFLICT": "本次创建内容已改变，请重新发起创建",
    "DEMO_UNAVAILABLE": "体验账号尚未准备好",
    "SERVICE_UNAVAILABLE": "服务暂时不可用，请稍后重试",
}


def register_private(app, accounts, works, *, env, quick_enabled, origin):
    quick = env == "development" and quick_enabled

    @app.exception_handler(Fault)
    async def fault(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": MESSAGES.get(exc.code, "请求无法完成")},
                "request_id": request.state.request_id,
            },
            headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else {},
        )

    def token(request):
        return request.cookies.get(COOKIE)

    def source_check(request):
        if not origin or request.headers.get("origin") != origin:
            raise Fault("CSRF_REJECTED", 403)

    async def current(request):
        if not token(request):
            raise Fault("UNAUTHENTICATED", 401)
        if accounts is None:
            raise Fault("SERVICE_UNAVAILABLE", 503)
        return await accounts.current(token(request))

    async def write_user(request):
        user = await current(request)
        source_check(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), user["csrf"]):
            raise Fault("CSRF_REJECTED", 403)
        return user

    async def body(request, fields):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise Fault("INVALID_REQUEST")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 4096:
                raise Fault("INVALID_REQUEST")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise Fault("INVALID_REQUEST") from None
        if not isinstance(value, dict) or set(value) != fields:
            raise Fault("INVALID_REQUEST")
        if any(not isinstance(v, str) or len(v) > 256 for v in value.values()):
            raise Fault("INVALID_REQUEST")
        return value

    def signed(result):
        response = JSONResponse({k: v for k, v in result.items() if k != "token"})
        response.set_cookie(
            COOKIE,
            result["token"],
            max_age=604800,
            httponly=True,
            secure=env == "production",
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/api/v1/capabilities")
    async def capabilities():
        return {"quick_login": quick}

    @app.post("/api/v1/sessions")
    async def login(request: Request):
        source_check(request)
        data = await body(request, {"username", "password"})
        if not 1 <= len(data["password"]) <= 128:
            raise Fault("INVALID_REQUEST")
        if accounts is None:
            raise Fault("SERVICE_UNAVAILABLE", 503)
        return signed(
            await accounts.login(
                data["username"],
                data["password"],
                request.client.host if request.client else "unknown",
            )
        )

    @app.get("/api/v1/sessions/current")
    async def identity(request: Request):
        user = await current(request)
        return {"username": user["username"], "csrf": user["csrf"]}

    @app.delete("/api/v1/sessions/current")
    async def logout(request: Request):
        source_check(request)
        try:
            await write_user(request)
        except Fault as exc:
            if exc.code != "UNAUTHENTICATED":
                raise
        if accounts:
            await accounts.logout(token(request))
        response = Response(status_code=204)
        response.delete_cookie(
            COOKIE, path="/", httponly=True, secure=env == "production", samesite="lax"
        )
        return response

    if quick:

        @app.post("/api/v1/dev-sessions")
        async def quick_login(request: Request):
            source_check(request)
            data = await body(request, {"target"})
            if accounts is None:
                raise Fault("SERVICE_UNAVAILABLE", 503)
            return signed(await accounts.quick(data["target"], token(request)))

    @app.post("/api/v1/works")
    async def create(request: Request):
        user = await write_user(request)
        data = await body(request, {"title"})
        result = await works.create(
            user["user_id"], data["title"], request.headers.get("idempotency-key")
        )
        return JSONResponse(jsonable_encoder(result), status_code=201)

    @app.get("/api/v1/works")
    async def listing(request: Request):
        user = await current(request)
        if set(request.query_params) - {"limit", "cursor"} or len(
            request.query_params.multi_items()
        ) != len(request.query_params):
            raise Fault("INVALID_REQUEST")
        try:
            limit = int(request.query_params.get("limit", "20"))
        except ValueError:
            raise Fault("INVALID_REQUEST") from None
        return await works.list(user["user_id"], limit, request.query_params.get("cursor"))

    @app.get("/api/v1/works/{work_id}")
    async def detail(request: Request, work_id: str):
        user = await current(request)
        if request.query_params:
            raise Fault("INVALID_REQUEST")
        return await works.get(user["user_id"], work_id)

    return current, write_user
