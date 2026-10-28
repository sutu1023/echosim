from fastapi import APIRouter, Request

from app.api.contracts import respond

router = APIRouter()


@router.get("/livez")
async def livez(request: Request):
    return respond(
        request, data={"status": "alive", "version": request.app.state.settings.app_version}
    )


@router.get("/health")
async def health(request: Request):
    ready, data = await request.app.state.health.inspect()
    return respond(request, "OK" if ready else "DEPENDENCY_UNAVAILABLE", data)
