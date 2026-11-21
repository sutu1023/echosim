"""材料服务：上传登记、去重、列表、详情、分块与原件读取。

项目归属一律由调用方先经 `resolve_owned_project` 解析，本模块只接受已解析的
`project_id`，并按项目过滤材料查询。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..errors import DocumentNotFound, DocumentNotText, ServiceUnavailable
from . import document_storage

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)

INGEST_KIND = "document.ingest"
INGEST_STAGE = "evidence-graph"
INGEST_TOTAL_STEPS = 3
INGEST_OPERATION_PREFIX = "document-ingest:v1:"
ACTIVE_STATUSES = ("queued", "parsing")
READY_STATUS = "ready"

DOCUMENT_COLUMNS = (
    "id, project_id, uploaded_by, display_name, kind, content_type, size_bytes, "
    "sha256, storage_path, text_encoding, status, page_count, chunk_count, "
    "failure_code, failure_message, created_at, ready_at"
)

FAILURE_MESSAGES = {
    "empty_file": "文件为空，请选择有内容的文件",
    "file_too_large": "文件超过 20 MiB 上限",
    "unsupported_type": "仅支持 PDF、Markdown 与 TXT",
    "decode_failed": "无法以 UTF-8 或可识别的中文编码解码",
    "no_text_layer": "该 PDF 没有文字层，请上传文本型 PDF",
    "original_missing": "原件不可读取，请重新上传",
    "too_many_pages": "页数超过当前上限",
    "parse_error": "解析失败，请重试或更换文件",
    "canceled": "入库已取消",
}


def failure_message(code: str, **details: object) -> str:
    message = FAILURE_MESSAGES.get(code, "解析失败，请重试或更换文件")
    if code == "too_many_pages":
        pages = details.get("pages")
        limit = details.get("limit")
        message = f"该 PDF 共 {pages} 页，{message}" if pages is not None else message
        if limit is not None:
            message = f"{message}（上限 {limit} 页）"
    return message


@dataclass(frozen=True)
class Document:
    id: uuid.UUID
    project_id: uuid.UUID
    uploaded_by: uuid.UUID
    display_name: str
    kind: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_path: str
    text_encoding: str | None
    status: str
    page_count: int | None
    chunk_count: int
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    ready_at: datetime | None
    job_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None

    @property
    def ready(self) -> bool:
        return self.status == READY_STATUS


def _as_document(row: object) -> Document:
    return Document(
        id=row.id,  # type: ignore[attr-defined]
        project_id=row.project_id,  # type: ignore[attr-defined]
        uploaded_by=row.uploaded_by,  # type: ignore[attr-defined]
        display_name=row.display_name,  # type: ignore[attr-defined]
        kind=row.kind,  # type: ignore[attr-defined]
        content_type=row.content_type,  # type: ignore[attr-defined]
        size_bytes=int(row.size_bytes),  # type: ignore[attr-defined]
        sha256=row.sha256.strip(),  # type: ignore[attr-defined]
        storage_path=row.storage_path,  # type: ignore[attr-defined]
        text_encoding=row.text_encoding,  # type: ignore[attr-defined]
        status=row.status,  # type: ignore[attr-defined]
        page_count=None if row.page_count is None else int(row.page_count),  # type: ignore[attr-defined]
        chunk_count=int(row.chunk_count),  # type: ignore[attr-defined]
        failure_code=row.failure_code,  # type: ignore[attr-defined]
        failure_message=row.failure_message,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        ready_at=row.ready_at,  # type: ignore[attr-defined]
        job_id=getattr(row, "job_id", None),
        run_id=getattr(row, "job_run_id", None),
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def document_body(document: Document) -> dict[str, object]:
    failure = None
    if document.failure_code is not None:
        failure = {
            "code": document.failure_code,
            "message": document.failure_message or failure_message(document.failure_code),
        }
    return {
        "documentId": str(document.id),
        "name": document.display_name,
        "kind": document.kind,
        "contentType": document.content_type,
        "sizeBytes": document.size_bytes,
        "sha256": document.sha256,
        "status": document.status,
        "pageCount": document.page_count,
        "chunkCount": document.chunk_count,
        "failure": failure,
        "createdAt": _iso(document.created_at),
        "readyAt": _iso(document.ready_at),
        "jobId": str(document.job_id) if document.job_id else None,
        "runId": str(document.run_id) if document.run_id else None,
    }


def _parse_document_id(document_id: str | uuid.UUID) -> uuid.UUID:
    if isinstance(document_id, uuid.UUID):
        return document_id
    try:
        return uuid.UUID(document_id)
    except (ValueError, AttributeError, TypeError):
        raise DocumentNotFound from None


def _storage_root() -> Path:
    return settings.document_storage_dir


async def _find_active_by_hash(
    db: AsyncSession, *, project_id: uuid.UUID, sha256: str
) -> Document | None:
    result = await db.execute(
        text(
            f"SELECT {DOCUMENT_COLUMNS} FROM documents "
            "WHERE project_id = :project_id AND sha256 = :sha256 "
            "AND status IN ('queued', 'parsing', 'ready')"
        ),
        {"project_id": project_id, "sha256": sha256},
    )
    row = result.first()
    return _as_document(row) if row is not None else None


async def _latest_job(
    db: AsyncSession, *, project_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID] | None:
    result = await db.execute(
        text(
            "SELECT id, run_id FROM jobs "
            "WHERE project_id = :project_id AND operation_key = :operation_key "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {
            "project_id": project_id,
            "operation_key": f"{INGEST_OPERATION_PREFIX}{document_id}",
        },
    )
    row = result.first()
    return (row.id, row.run_id) if row is not None else None


def _with_job(document: Document, job: tuple[uuid.UUID, uuid.UUID] | None) -> Document:
    if job is None:
        return document
    return Document(
        **{
            **document.__dict__,
            "job_id": job[0],
            "run_id": job[1],
        }
    )


async def register_upload(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    spooled: document_storage.SpooledUpload,
    display_name: str,
    kind: str,
    content_type: str,
) -> tuple[Document, bool]:
    """登记材料并创建解析任务；相同内容返回既有材料（不去重分块）。"""
    document_id = uuid.uuid4()
    relative = document_storage.relative_path(project_id, document_id, kind)
    try:
        result = await db.execute(
            text(
                "INSERT INTO documents (id, project_id, uploaded_by, display_name, "
                "kind, content_type, size_bytes, sha256, storage_path, status) "
                "VALUES (:id, :project_id, :uploaded_by, :display_name, :kind, "
                ":content_type, :size_bytes, :sha256, :storage_path, 'queued') "
                "ON CONFLICT (project_id, sha256) "
                "WHERE status IN ('queued', 'parsing', 'ready') DO NOTHING "
                f"RETURNING {DOCUMENT_COLUMNS}"
            ),
            {
                "id": document_id,
                "project_id": project_id,
                "uploaded_by": actor_id,
                "display_name": display_name,
                "kind": kind,
                "content_type": content_type,
                "size_bytes": spooled.size_bytes,
                "sha256": spooled.sha256,
                "storage_path": relative,
            },
        )
        row = result.first()
        if row is None:
            await db.commit()
            document_storage.discard_spool(spooled)
            existing = await _find_active_by_hash(
                db, project_id=project_id, sha256=spooled.sha256
            )
            if existing is None:  # pragma: no cover - 冲突行随后消失属于异常竞态
                raise ServiceUnavailable
            job = await _latest_job(
                db, project_id=project_id, document_id=existing.id
            )
            return _with_job(existing, job), True

        document_storage.commit_spool(_storage_root(), spooled, relative)
        run_id = uuid.uuid4()
        job_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO runs (id, project_id, created_by, kind, stage, "
                "total_steps, fail_at_step, simulated_epoch, document_id) "
                "VALUES (:id, :project_id, :created_by, :kind, :stage, "
                ":total_steps, NULL, now(), :document_id)"
            ),
            {
                "id": run_id,
                "project_id": project_id,
                "created_by": actor_id,
                "kind": INGEST_KIND,
                "stage": INGEST_STAGE,
                "total_steps": INGEST_TOTAL_STEPS,
                "document_id": document_id,
            },
        )
        await db.execute(
            text(
                "INSERT INTO jobs (id, run_id, project_id, operation_key, status, "
                "next_step, attempts, lease_token) "
                "VALUES (:id, :run_id, :project_id, :operation_key, 'queued', 0, 0, NULL)"
            ),
            {
                "id": job_id,
                "run_id": run_id,
                "project_id": project_id,
                "operation_key": f"{INGEST_OPERATION_PREFIX}{document_id}",
            },
        )
        await db.commit()
    except DB_ERRORS as exc:
        await db.rollback()
        document_storage.discard_spool(spooled)
        logger.warning("材料登记失败 document=%s: %s", document_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    except BaseException:
        await db.rollback()
        document_storage.discard_spool(spooled)
        raise
    document = _as_document(row)
    return _with_job(document, (job_id, run_id)), False


async def list_documents(db: AsyncSession, *, project_id: uuid.UUID) -> list[Document]:
    try:
        result = await db.execute(
            text(
                f"SELECT {', '.join('d.' + column.strip() for column in DOCUMENT_COLUMNS.split(','))}, "
                "j.id AS job_id, j.run_id AS job_run_id "
                "FROM documents d "
                "LEFT JOIN LATERAL ("
                "  SELECT id, run_id FROM jobs "
                "  WHERE project_id = d.project_id "
                "  AND operation_key = 'document-ingest:v1:' || d.id::text "
                "  ORDER BY created_at DESC LIMIT 1"
                ") j ON true "
                "WHERE d.project_id = :project_id "
                "ORDER BY d.created_at DESC, d.id DESC"
            ),
            {"project_id": project_id},
        )
        rows = result.fetchall()
    except DB_ERRORS as exc:
        logger.warning("材料列表查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    return [_as_document(row) for row in rows]


async def get_document(
    db: AsyncSession, *, project_id: uuid.UUID, document_id: str | uuid.UUID
) -> Document:
    parsed = _parse_document_id(document_id)
    try:
        result = await db.execute(
            text(
                f"SELECT {', '.join('d.' + column.strip() for column in DOCUMENT_COLUMNS.split(','))}, "
                "j.id AS job_id, j.run_id AS job_run_id "
                "FROM documents d "
                "LEFT JOIN LATERAL ("
                "  SELECT id, run_id FROM jobs "
                "  WHERE project_id = d.project_id "
                "  AND operation_key = 'document-ingest:v1:' || d.id::text "
                "  ORDER BY created_at DESC LIMIT 1"
                ") j ON true "
                "WHERE d.id = :document_id AND d.project_id = :project_id"
            ),
            {"document_id": parsed, "project_id": project_id},
        )
        row = result.first()
    except DB_ERRORS as exc:
        logger.warning("材料详情查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    if row is None:
        raise DocumentNotFound
    return _as_document(row)


async def list_chunks(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    document_id: str | uuid.UUID,
    after: int = 0,
    limit: int = 20,
) -> tuple[Document, list[dict[str, object]]]:
    document = await get_document(
        db, project_id=project_id, document_id=document_id
    )
    if not document.ready:
        return document, []
    try:
        result = await db.execute(
            text(
                "SELECT chunk_index, page_no, paragraph_start, paragraph_end, "
                "source_start, source_end, text FROM document_chunks "
                "WHERE document_id = :document_id AND chunk_index > :after "
                "ORDER BY chunk_index LIMIT :limit"
            ),
            {"document_id": document.id, "after": after, "limit": limit},
        )
        rows = result.fetchall()
    except DB_ERRORS as exc:
        logger.warning("材料分块查询失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    chunks = [
        {
            "index": int(row.chunk_index),
            "pageNo": None if row.page_no is None else int(row.page_no),
            "paragraphStart": (
                None if row.paragraph_start is None else int(row.paragraph_start)
            ),
            "paragraphEnd": (
                None if row.paragraph_end is None else int(row.paragraph_end)
            ),
            "sourceStart": int(row.source_start),
            "sourceEnd": int(row.source_end),
            "text": row.text,
            "sourceUrl": (
                f"/api/projects/{document.project_id}/documents/{document.id}/file"
                f"#page={int(row.page_no)}"
                if row.page_no is not None
                else None
            ),
        }
        for row in rows
    ]
    return document, chunks


def original_bytes(document: Document) -> bytes:
    try:
        return document_storage.read_bytes(_storage_root(), document.storage_path)
    except (OSError, ValueError) as exc:
        logger.warning("原件读取失败 document=%s: %s", document.id, type(exc).__name__)
        raise DocumentNotFound from exc


def source_text(document: Document) -> str:
    if document.kind == "pdf":
        raise DocumentNotText
    data = original_bytes(document)
    encoding = document.text_encoding or "utf-8"
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError):  # pragma: no cover - 记录已校验
        raise DocumentNotText from None


async def cancel_for_run(db: AsyncSession, *, run_id: uuid.UUID) -> None:
    """取消事务内同步把材料标记为失败；只影响未就绪的材料。"""
    await db.execute(
        text(
            "UPDATE documents SET status = 'failed', failure_code = 'canceled', "
            "failure_message = :message, chunk_count = 0, page_count = NULL, "
            "updated_at = now() "
            "WHERE id = (SELECT document_id FROM runs WHERE id = :run_id) "
            "AND status IN ('queued', 'parsing')"
        ),
        {"run_id": run_id, "message": FAILURE_MESSAGES["canceled"]},
    )
