"""同域静态托管：构建产物优先，未知路径回退 `index.html`。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def resolve_within(dist_dir: Path, relative_path: str) -> Path | None:
    """把请求路径解析到 `dist_dir` 之内；越界或非法路径返回 None。"""
    dist_root = dist_dir.resolve()
    candidate = (dist_root / relative_path.lstrip("/")).resolve()
    if candidate != dist_root and not candidate.is_relative_to(dist_root):
        return None
    return candidate


def register_static_routes(app: FastAPI, dist_dir: Path) -> None:
    """注册静态资源挂载与 SPA 回退路由。

    必须在 API 路由之后调用：`/health`、`/livez` 先匹配，`/assets` 由
    `StaticFiles` 处理，最后的 catch-all 只负责静态文件与 `index.html`。
    """
    dist_dir = dist_dir.resolve()
    app.mount(
        "/assets",
        StaticFiles(directory=dist_dir / "assets", check_dir=False),
        name="assets",
    )

    index_file = dist_dir / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        """存在的静态文件直接返回，否则回退到前端入口。"""
        candidate = resolve_within(dist_dir, full_path)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        if not index_file.is_file():
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(index_file)
