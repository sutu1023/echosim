"""证据图谱接口：开始建图、读取默认/旧 build 与已发布快照来源定位。"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..deps import require_user
from ..errors import (
    GraphBuildConflict,
    GraphBuildNotFound,
    GraphSnapshotNotFound,
    GraphSnapshotInconsistent,
    GraphLimitExceeded,
    InvalidSnapshotRevision,
)
from ..schemas import EvidenceGraphStartRequest
from ..services import evidence_graph as graph_store
from ..services import projects
from ..services.sessions import SessionUser

router = APIRouter(
    prefix="/api/projects/{project_id}/evidence-graph", tags=["evidence-graph"]
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _build_body(build: graph_store.GraphBuild) -> dict[str, object]:
    failure = None
    if build.failure_code is not None:
        failure = {
            "code": build.failure_code,
            "message": build.failure_message or "建图未完成",
        }
    return {
        "buildId": str(build.id),
        "projectId": str(build.project_id),
        "runId": str(build.run_id),
        "jobId": str(build.job_id),
        "question": build.question,
        "phase": build.phase,
        "status": build.job_status,
        "plannedEpisodes": build.planned_episodes,
        "acceptedEpisodes": build.accepted_episodes,
        "confirmedEpisodes": build.confirmed_episodes,
        "canContinue": build.can_continue,
        "failure": failure,
        "createdAt": _iso(build.created_at),
        "updatedAt": _iso(build.updated_at),
        "finishedAt": _iso(build.finished_at),
    }


def _source_body(
    item: graph_store.GraphEvidence,
    *,
    project_id: uuid.UUID,
    documents: dict[uuid.UUID, tuple[str, str]],
) -> dict[str, object]:
    kind, name = documents.get(item.document_id, ("", "材料"))
    document_path = (
        f"/api/projects/{project_id}/documents/{item.document_id}"
    )
    body: dict[str, object] = {
        "documentId": str(item.document_id),
        "documentName": name,
        "kind": kind,
        "chunkIndex": item.chunk_index,
        "pageNo": item.page_no,
        "paragraphStart": item.paragraph_start,
        "paragraphEnd": item.paragraph_end,
        "sourceStart": item.source_start,
        "sourceEnd": item.source_end,
        "quote": item.quote,
    }
    if kind == "pdf":
        page = item.page_no or 1
        body["locator"] = {
            "kind": "pdf",
            "pageNo": page,
            "url": f"{document_path}/file#page={page}",
        }
    else:
        body["locator"] = {
            "kind": "text",
            "url": f"{document_path}/source",
            "sourceStart": item.source_start,
            "sourceEnd": item.source_end,
        }
    return body


def _snapshot_body(
    snapshot: graph_store.GraphSnapshot,
    *,
    project_id: uuid.UUID,
    documents: dict[uuid.UUID, tuple[str, str]],
) -> dict[str, object]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for item in snapshot.evidence:
        grouped.setdefault((item.target_kind, item.target_key), []).append(
            _source_body(item, project_id=project_id, documents=documents)
        )
    return {
        "snapshotId": str(snapshot.id),
        "buildId": str(snapshot.build_id),
        "revision": snapshot.revision,
        "complete": snapshot.complete,
        "published": snapshot.published,
        "nodeCount": snapshot.node_count,
        "edgeCount": snapshot.edge_count,
        "episodeCount": snapshot.episode_count,
        "createdAt": _iso(snapshot.created_at),
        "publishedAt": _iso(snapshot.published_at),
        "nodes": [
            {
                "key": node.key,
                "nodeType": node.node_type,
                "label": node.label,
                "origin": node.origin,
                "summary": node.summary,
                "sources": grouped.get(("node", node.key), []),
            }
            for node in snapshot.nodes
        ],
        "edges": [
            {
                "key": edge.key,
                "edgeType": edge.edge_type,
                "label": edge.label,
                "origin": edge.origin,
                "summary": edge.summary,
                "sourceKey": edge.source_key,
                "targetKey": edge.target_key,
                "sources": grouped.get(("edge", edge.key), []),
            }
            for edge in snapshot.edges
        ],
    }


async def _snapshot_payload(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    snapshot: graph_store.GraphSnapshot | None,
) -> dict[str, object] | None:
    if snapshot is None:
        return None
    document_ids = [item.document_id for item in snapshot.evidence]
    documents = await graph_store.document_kinds(
        db, project_id=project_id, document_ids=document_ids
    )
    return _snapshot_body(
        snapshot, project_id=project_id, documents=documents
    )


@router.post("/builds")
async def start_build(
    project_id: str,
    payload: EvidenceGraphStartRequest,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    build, created = await graph_store.start_graph_build(
        db,
        project_id=project.id,
        actor_id=user.id,
        question=payload.question,
        document_ids=payload.documentIds,
        max_documents=settings.evidence_graph_max_documents,
    )
    return JSONResponse(
        {"build": _build_body(build)},
        status_code=202 if created else 200,
    )


async def _read_graph(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    build_id: uuid.UUID | str | None,
) -> dict[str, object]:
    build, snapshot = await graph_store.read_build_graph(
        db, project_id=project_id, build_id=build_id
    )
    return {
        "build": _build_body(build),
        "snapshot": await _snapshot_payload(
            db, project_id=project_id, snapshot=snapshot
        ),
    }


@router.get("")
async def get_default_build(
    project_id: str,
    build: str | None = None,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    return await _read_graph(db, project_id=project.id, build_id=build)


@router.get("/builds/{build_id}")
async def get_build(
    project_id: str,
    build_id: str,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    return await _read_graph(db, project_id=project.id, build_id=build_id)


@router.get("/builds/{build_id}/snapshots/{revision}")
async def get_snapshot(
    project_id: str,
    build_id: str,
    revision: int,
    user: SessionUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    project = await projects.resolve_owned_project(
        db, actor_id=user.id, project_id=project_id
    )
    build = await graph_store.get_build(
        db, project_id=project.id, build_id=build_id
    )
    latest = await graph_store.max_snapshot_revision(db, build_id=build.id)
    if revision < 1 or revision > latest:
        raise InvalidSnapshotRevision
    snapshot = await graph_store.get_published_snapshot(
        db, build_id=build.id, revision=revision
    )
    return {
        "build": _build_body(build),
        "snapshot": await _snapshot_payload(
            db, project_id=project.id, snapshot=snapshot
        ),
    }


# 保留显式引用，避免路由被静态回退吞掉时缺少可诊断的异常类型。
__all__ = [
    "router",
    "GraphBuildConflict",
    "GraphBuildNotFound",
    "GraphLimitExceeded",
    "GraphSnapshotInconsistent",
    "InvalidSnapshotRevision",
]
