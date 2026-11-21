"""材料路由：上传、列表、详情、分块、原文视图与原件。

顺序固定为“身份 → 项目归属 → 项目内材料”；匿名请求在访问数据库前返回 401，
他人项目与他人材料都不泄露任何元数据。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterator
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..deps import require_user
from ..errors import DocumentNotText
from ..services import document_storage
from ..services import documents as document_service
from ..services import projects
from ..services.sessions import SessionUser

router = APIRouter(prefix="/api/projects/{project_id}/documents", tags=["documents"])

READ_CHUNK_SIZE = 64 * 1024
DISPLAY_NAME_MAX_LENGTH = 200
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
KIND_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "markdown": "text/markdown",
    "text": "text/plain",
}


def detect_kind(filename: str, head: bytes) -> str | None:
    """按扩展名与 PDF 魔数判定类型，不信任客户端 Content-Type。"""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return "pdf" if head.startswith(b"%PDF-") else None
    if lower.endswith((".md", ".markdown")):
        return "markdown"
    if lower.endswith(".txt"):
        return "text"
    return None


def sanitize_display_name(filename: str) -> str:
    """只保留名称本身：去掉路径、控制字符与首尾空白。"""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = CONTROL_RE.sub("", name).strip()
    name = "".join(
        ch for ch in name if unicodedata.category(ch) not in {"Cc", "Cf"}
    )
    if len(name) > DISPLAY_NAME_MAX_LENGTH:
        name = name[:DISPLAY_NAME_MAX_LENGTH]
    return name or "材料"


def content_disposition(name: str) -> str:
    encoded = quote(name, safe="")
    return f"inline; filename*=UTF-8''{encoded}"


async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while True:
        chunk = await upload.read(READ_CHUNK_SIZE)
        if not chunk:
            return
        yield chunk


@router.post("")
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    filename = sanitize_display_name(file.filename or "")
    head = await file.read(5)
    kind = detect_kind(filename, head)
    if kind is None:
        return JSONResponse(
            {
                "detail": "仅支持 PDF、Markdown 与 TXT",
                "code": "unsupported_type",
            },
            status_code=415,
        )

    async def chunks() -> AsyncIterator[bytes]:
        if head:
            yield head
        async for chunk in _upload_chunks(file):
            yield chunk

    try:
        spooled = await document_storage.spool_upload(
            settings.document_storage_dir, chunks(), settings.document_max_bytes
        )
    except document_storage.FileTooLarge:
        return JSONResponse(
            {
                "detail": "文件超过 20 MiB 上限",
                "code": "file_too_large",
            },
            status_code=413,
        )
    except document_storage.EmptyUpload:
        return JSONResponse(
            {"detail": "文件为空，请选择有内容的文件", "code": "empty_file"},
            status_code=400,
        )

    document, deduplicated = await document_service.register_upload(
        db,
        project_id=project.id,
        actor_id=user.id,
        spooled=spooled,
        display_name=filename,
        kind=kind,
        content_type=KIND_CONTENT_TYPES[kind],
    )
    body: dict[str, object] = {
        "document": document_service.document_body(document),
        "deduplicated": deduplicated,
    }
    if document.job_id is not None:
        body["jobId"] = str(document.job_id)
        body["runId"] = str(document.run_id)
    return JSONResponse(body, status_code=200 if deduplicated else 201)


@router.get("")
async def list_documents(
    project_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    items = await document_service.list_documents(db, project_id=project.id)
    return {
        "documents": [document_service.document_body(item) for item in items],
        "limits": {
            "maxBytes": settings.document_max_bytes,
            "maxPages": settings.document_max_pages,
        },
    }


@router.get("/{document_id}")
async def document_detail(
    project_id: str,
    document_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    document = await document_service.get_document(
        db, project_id=project.id, document_id=document_id
    )
    return {"document": document_service.document_body(document)}


@router.get("/{document_id}/chunks")
async def document_chunks(
    project_id: str,
    document_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    document, chunks = await document_service.list_chunks(
        db,
        project_id=project.id,
        document_id=document_id,
        after=after,
        limit=limit,
    )
    return {
        "documentId": str(document.id),
        "ready": document.ready,
        "total": document.chunk_count,
        "chunks": chunks,
    }


@router.get("/{document_id}/source")
async def document_source(
    project_id: str,
    document_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    document = await document_service.get_document(
        db, project_id=project.id, document_id=document_id
    )
    if document.kind == "pdf":
        raise DocumentNotText
    text = document_service.source_text(document)
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@router.get("/{document_id}/file")
async def document_file(
    project_id: str,
    document_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    document = await document_service.get_document(
        db, project_id=project.id, document_id=document_id
    )
    payload = document_service.original_bytes(document)
    media_type = (
        "application/pdf"
        if document.kind == "pdf"
        else "text/plain; charset=utf-8"
    )
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(document.display_name),
            "X-Content-Type-Options": "nosniff",
        },
    )
