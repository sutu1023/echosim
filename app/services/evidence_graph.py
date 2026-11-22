"""证据图谱持久化服务：建图任务、快照读模型与项目图谱绑定。

本模块只处理数据库事实，不调用模型或 Zep。建图任务复用现有 `runs`/`jobs` 队列与
事务约定；快照只有在 `published=true` 时才允许通过面向业务的读取方法取得。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..engines.business_config import (
    DEFAULT_EVIDENCE_GRAPH_MAX_EDGES,
    DEFAULT_EVIDENCE_GRAPH_MAX_NODES,
    DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM,
)
from ..engines.business_contracts import GRAPH_BUILD_STEPS
from ..errors import (
    DocumentNotFound,
    GraphBindingConflict,
    GraphBindingNotFound,
    GraphBuildConflict,
    GraphBuildNotFound,
    GraphEpisodeConflict,
    GraphEpisodeNotFound,
    GraphLimitExceeded,
    GraphSnapshotInconsistent,
    GraphSnapshotNotFound,
    ProjectNotFound,
    ServiceUnavailable,
)

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)

GRAPH_BUILD_KIND = "evidence.graph.build"
GRAPH_BUILD_STAGE = "evidence-graph"
GRAPH_BUILD_OPERATION = "evidence-graph-build"
GRAPH_BUILD_OPERATION_KEY = "evidence-graph-build:v1"
GRAPH_BUILD_TOTAL_STEPS = 6
GRAPH_BUILD_STEP_KEYS = GRAPH_BUILD_STEPS

MAX_GRAPH_NODES = DEFAULT_EVIDENCE_GRAPH_MAX_NODES
MAX_GRAPH_EDGES = DEFAULT_EVIDENCE_GRAPH_MAX_EDGES
MAX_EVIDENCE_PER_TARGET = DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM
PARTIAL_SNAPSHOT_RETENTION = 3

ACTIVE_JOB_STATUSES = ("queued", "running")
EPISODE_STATES = ("planned", "written", "matched", "processed", "failed")
MATCHED_EPISODE_STATES = ("matched", "processed")
WRITE_OUTCOMES = ("not_attempted", "in_flight", "rejected_retryable", "accepted")

BINDING_NAME_PREFIX = "echosim-project-"
BINDING_DESCRIPTION = "EchoSim 项目证据图谱（自动创建，请勿手工修改）"
BINDING_TIME_ZONE = "UTC"

BINDING_COLUMNS = (
    "project_id, create_idempotency_key, request_name, request_description, "
    "request_time_zone, graph_id, state, created_at, updated_at, bound_at"
)
BUILD_COLUMNS = (
    "b.id, b.project_id, b.run_id, b.question, b.phase, b.planned_episodes, "
    "b.accepted_episodes, b.confirmed_episodes, b.can_continue, b.failure_code, "
    "b.failure_message, b.created_at, b.updated_at, b.finished_at, "
    "j.id AS job_id, j.status AS job_status"
)
BUILD_SOURCE = "FROM graph_builds b JOIN jobs j ON j.run_id = b.run_id"

READ_BUILD_GRAPH_SQL = """
WITH selected AS (
    SELECT b.id, b.project_id, b.run_id, b.question, b.phase,
           b.planned_episodes, b.accepted_episodes, b.confirmed_episodes,
           b.can_continue, b.failure_code, b.failure_message,
           b.created_at, b.updated_at, b.finished_at,
           j.id AS job_id, j.status AS job_status
      FROM graph_builds b
      JOIN jobs j ON j.run_id = b.run_id
     WHERE b.project_id = :project_id
       AND (CAST(:build_id AS uuid) IS NULL OR b.id = CAST(:build_id AS uuid))
     ORDER BY CASE WHEN j.status IN ('queued', 'running') THEN 0 ELSE 1 END,
              b.created_at DESC, b.id DESC
     LIMIT 1
), chosen AS (
    SELECT s.id AS snapshot_id, s.revision, s.complete, s.published,
           s.node_count, s.edge_count, s.episode_count,
           s.created_at AS snapshot_created_at, s.published_at
      FROM graph_snapshots s
     WHERE s.build_id = (SELECT id FROM selected)
       AND s.published = true
     ORDER BY s.revision DESC
     LIMIT 1
)
SELECT selected.*, chosen.*,
       COALESCE((SELECT jsonb_agg(jsonb_build_object(
           'key', n.node_key, 'nodeType', n.node_type, 'label', n.label,
           'origin', n.origin, 'summary', n.summary) ORDER BY n.node_key)
         FROM graph_nodes n WHERE n.snapshot_id = chosen.snapshot_id),
         '[]'::jsonb)::text AS nodes_json,
       COALESCE((SELECT jsonb_agg(jsonb_build_object(
           'key', e.edge_key, 'edgeType', e.edge_type, 'label', e.label,
           'origin', e.origin, 'summary', e.summary,
           'sourceKey', e.source_node_key, 'targetKey', e.target_node_key)
           ORDER BY e.edge_key)
         FROM graph_edges e WHERE e.snapshot_id = chosen.snapshot_id),
         '[]'::jsonb)::text AS edges_json,
       COALESCE((SELECT jsonb_agg(jsonb_build_object(
           'targetKind', v.target_kind, 'targetKey', v.target_key,
           'evidenceIndex', v.evidence_index, 'documentId', v.document_id,
           'chunkIndex', v.chunk_index, 'pageNo', v.page_no,
           'paragraphStart', v.paragraph_start, 'paragraphEnd', v.paragraph_end,
           'sourceStart', v.source_start, 'sourceEnd', v.source_end,
           'quote', v.quote)
           ORDER BY v.target_kind, v.target_key, v.evidence_index)
         FROM graph_evidence v WHERE v.snapshot_id = chosen.snapshot_id),
         '[]'::jsonb)::text AS evidence_json
  FROM selected
  LEFT JOIN chosen ON true
"""


@dataclass(frozen=True)
class GraphBinding:
    project_id: uuid.UUID
    create_idempotency_key: uuid.UUID
    request_name: str
    request_description: str
    request_time_zone: str
    graph_id: uuid.UUID | None
    state: str
    created_at: datetime
    updated_at: datetime
    bound_at: datetime | None

    @property
    def bound(self) -> bool:
        return self.graph_id is not None and self.state == "bound"

    def create_params(self) -> dict[str, str]:
        """返回 `graph.create` 的稳定请求参数，不含任何用户正文。"""
        return {
            "name": self.request_name,
            "description": self.request_description,
            "time_zone": self.request_time_zone,
        }


@dataclass(frozen=True)
class GraphBuild:
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    question: str
    phase: str
    planned_episodes: int
    accepted_episodes: int
    confirmed_episodes: int
    can_continue: bool
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    job_id: uuid.UUID
    job_status: str


@dataclass(frozen=True)
class GraphEpisode:
    build_id: uuid.UUID
    episode_key: str
    idempotency_key: uuid.UUID
    content: str
    content_sha256: str
    episode_uuid: uuid.UUID | None
    state: str
    attempts: int
    write_outcome: str = "not_attempted"


@dataclass(frozen=True)
class BuildDocument:
    document_id: uuid.UUID
    display_name: str
    kind: str
    status: str
    sha256: str
    chunk_count: int
    ordinal: int


@dataclass(frozen=True)
class BuildChunk:
    document_id: uuid.UUID
    document_name: str
    chunk_index: int
    text: str
    source_start: int
    source_end: int
    page_no: int | None
    paragraph_start: int | None
    paragraph_end: int | None


@dataclass(frozen=True)
class GraphNode:
    key: str
    node_type: str
    label: str
    origin: str
    summary: str | None = None


@dataclass(frozen=True)
class GraphEdge:
    key: str
    edge_type: str
    label: str
    origin: str
    summary: str | None = None
    source_key: str = ""
    target_key: str = ""


@dataclass(frozen=True)
class GraphEvidence:
    target_kind: str
    target_key: str
    evidence_index: int
    document_id: uuid.UUID
    chunk_index: int
    source_start: int
    source_end: int
    quote: str
    page_no: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None


@dataclass(frozen=True)
class GraphSnapshot:
    id: uuid.UUID
    build_id: uuid.UUID
    revision: int
    complete: bool
    published: bool
    node_count: int
    edge_count: int
    episode_count: int
    created_at: datetime
    published_at: datetime | None
    nodes: tuple[GraphNode, ...] = field(default_factory=tuple)
    edges: tuple[GraphEdge, ...] = field(default_factory=tuple)
    evidence: tuple[GraphEvidence, ...] = field(default_factory=tuple)


def _as_binding(row: object) -> GraphBinding:
    return GraphBinding(
        project_id=row.project_id,  # type: ignore[attr-defined]
        create_idempotency_key=row.create_idempotency_key,  # type: ignore[attr-defined]
        request_name=row.request_name,  # type: ignore[attr-defined]
        request_description=row.request_description,  # type: ignore[attr-defined]
        request_time_zone=row.request_time_zone,  # type: ignore[attr-defined]
        graph_id=row.graph_id,  # type: ignore[attr-defined]
        state=row.state,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        updated_at=row.updated_at,  # type: ignore[attr-defined]
        bound_at=row.bound_at,  # type: ignore[attr-defined]
    )


def _as_build(row: object) -> GraphBuild:
    return GraphBuild(
        id=row.id,  # type: ignore[attr-defined]
        project_id=row.project_id,  # type: ignore[attr-defined]
        run_id=row.run_id,  # type: ignore[attr-defined]
        question=row.question,  # type: ignore[attr-defined]
        phase=row.phase,  # type: ignore[attr-defined]
        planned_episodes=int(row.planned_episodes),  # type: ignore[attr-defined]
        accepted_episodes=int(row.accepted_episodes),  # type: ignore[attr-defined]
        confirmed_episodes=int(row.confirmed_episodes),  # type: ignore[attr-defined]
        can_continue=bool(row.can_continue),  # type: ignore[attr-defined]
        failure_code=row.failure_code,  # type: ignore[attr-defined]
        failure_message=row.failure_message,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        updated_at=row.updated_at,  # type: ignore[attr-defined]
        finished_at=row.finished_at,  # type: ignore[attr-defined]
        job_id=row.job_id,  # type: ignore[attr-defined]
        job_status=row.job_status,  # type: ignore[attr-defined]
    )


def _as_episode(row: object) -> GraphEpisode:
    return GraphEpisode(
        build_id=row.build_id,  # type: ignore[attr-defined]
        episode_key=row.episode_key,  # type: ignore[attr-defined]
        idempotency_key=row.idempotency_key,  # type: ignore[attr-defined]
        content=row.content,  # type: ignore[attr-defined]
        content_sha256=row.content_sha256.strip(),  # type: ignore[attr-defined]
        episode_uuid=row.episode_uuid,  # type: ignore[attr-defined]
        state=row.state,  # type: ignore[attr-defined]
        attempts=int(row.attempts),  # type: ignore[attr-defined]
        write_outcome=row.write_outcome,  # type: ignore[attr-defined]
    )


def _parse_build_id(build_id: str | uuid.UUID) -> uuid.UUID:
    if isinstance(build_id, uuid.UUID):
        return build_id
    try:
        return uuid.UUID(build_id)
    except (ValueError, AttributeError, TypeError):
        raise GraphBuildNotFound from None


def _coerce_graph_id(graph_id: str | uuid.UUID | None) -> uuid.UUID:
    if graph_id is None:
        raise GraphBindingConflict("invalid_graph_response")
    if isinstance(graph_id, uuid.UUID):
        parsed = graph_id
    else:
        value = str(graph_id).strip()
        if not value:
            raise GraphBindingConflict("invalid_graph_response")
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError, TypeError):
            raise GraphBindingConflict("invalid_graph_response") from None
    if parsed.int == 0:
        raise GraphBindingConflict("invalid_graph_response")
    return parsed


def _binding_params(project_id: uuid.UUID) -> tuple[str, str, str]:
    """项目专用、稳定且不含用户正文的图谱创建参数。"""
    return (
        f"{BINDING_NAME_PREFIX}{project_id}",
        BINDING_DESCRIPTION,
        BINDING_TIME_ZONE,
    )


async def create_build(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    question: str,
    document_ids: Sequence[uuid.UUID],
    commit: bool = True,
) -> GraphBuild:
    """创建一条 build、一条 run、一条 job 与材料快照，全部在同一事务提交。

    本函数只做持久化；开始接口的材料状态校验、advisory lock 与 202/200 语义由后续
    建图开始服务负责。活动任务唯一性仍由 `jobs_active_operation_idx` 兜底。
    """
    ordered = list(document_ids)
    build_id = uuid.uuid4()
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    try:
        if ordered:
            statement = text(
                "SELECT id, sha256, chunk_count, status FROM documents "
                "WHERE project_id = :project_id AND id IN :document_ids"
            ).bindparams(bindparam("document_ids", expanding=True))
            result = await db.execute(
                statement,
                {"project_id": project_id, "document_ids": ordered},
            )
            rows = {row.id: row for row in result.fetchall()}
        else:
            rows = {}
        if len(rows) != len(set(ordered)):
            raise DocumentNotFound

        await db.execute(
            text(
                "INSERT INTO runs (id, project_id, created_by, kind, stage, "
                "total_steps, fail_at_step, simulated_epoch) "
                "VALUES (:id, :project_id, :created_by, :kind, :stage, "
                ":total_steps, NULL, now())"
            ),
            {
                "id": run_id,
                "project_id": project_id,
                "created_by": actor_id,
                "kind": GRAPH_BUILD_KIND,
                "stage": GRAPH_BUILD_STAGE,
                "total_steps": GRAPH_BUILD_TOTAL_STEPS,
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
                "operation_key": GRAPH_BUILD_OPERATION_KEY,
            },
        )
        await db.execute(
            text(
                "INSERT INTO graph_builds (id, project_id, run_id, question, phase) "
                "VALUES (:id, :project_id, :run_id, :question, 'preparing')"
            ),
            {
                "id": build_id,
                "project_id": project_id,
                "run_id": run_id,
                "question": question,
            },
        )
        if ordered:
            await db.execute(
                text(
                    "INSERT INTO graph_build_documents (build_id, document_id, "
                    "document_sha256, chunk_count, ordinal) "
                    "VALUES (:build_id, :document_id, :document_sha256, "
                    ":chunk_count, :ordinal)"
                ),
                [
                    {
                        "build_id": build_id,
                        "document_id": document_id,
                        "document_sha256": rows[document_id].sha256.strip(),
                        "chunk_count": int(rows[document_id].chunk_count),
                        "ordinal": ordinal,
                    }
                    for ordinal, document_id in enumerate(ordered, start=1)
                ],
            )
        if commit:
            await db.commit()
    except DocumentNotFound:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("创建建图任务冲突 project=%s: %s", project_id, type(exc).__name__)
        raise GraphBuildConflict from exc
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("创建建图任务失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc

    created = await get_build(db, project_id=project_id, build_id=build_id)
    return created


async def _query_build(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    build_id: str | uuid.UUID | None,
) -> GraphBuild:
    """读取 build；默认活动 build 优先，否则最近创建的 build。"""
    parsed = None if build_id is None else _parse_build_id(build_id)
    try:
        if parsed is None:
            sql = (
                f"SELECT {BUILD_COLUMNS} {BUILD_SOURCE} "
                "WHERE b.project_id = :project_id "
                "ORDER BY CASE WHEN j.status IN ('queued', 'running') "
                "THEN 0 ELSE 1 END, b.created_at DESC, b.id DESC LIMIT 1"
            )
            params: dict[str, object] = {"project_id": project_id}
        else:
            sql = (
                f"SELECT {BUILD_COLUMNS} {BUILD_SOURCE} "
                "WHERE b.project_id = :project_id AND b.id = :build_id"
            )
            params = {"project_id": project_id, "build_id": parsed}
        result = await db.execute(text(sql), params)
        row = result.first()
    except GraphBuildNotFound:
        raise
    except DB_ERRORS as exc:
        logger.warning("建图读取失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    if row is None:
        raise GraphBuildNotFound
    return _as_build(row)


async def get_build(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    build_id: str | uuid.UUID | None = None,
) -> GraphBuild:
    """按项目读取建图：无 ID 时活动 build 优先，否则最近创建的 build。"""
    return await _query_build(db, project_id=project_id, build_id=build_id)


async def _snapshot_from_build_graph_row(row: object) -> GraphSnapshot | None:
    """把单语句读取的 build 行转换为同一语句快照中的快照对象。"""
    if row.snapshot_id is None:  # type: ignore[attr-defined]
        return None
    nodes = tuple(
        GraphNode(
            key=item["key"],
            node_type=item["nodeType"],
            label=item["label"],
            origin=item["origin"],
            summary=item.get("summary"),
        )
        for item in json.loads(row.nodes_json or "[]")  # type: ignore[attr-defined]
    )
    edges = tuple(
        GraphEdge(
            key=item["key"],
            edge_type=item["edgeType"],
            label=item["label"],
            origin=item["origin"],
            summary=item.get("summary"),
            source_key=item["sourceKey"],
            target_key=item["targetKey"],
        )
        for item in json.loads(row.edges_json or "[]")  # type: ignore[attr-defined]
    )
    evidence = tuple(
        GraphEvidence(
            target_kind=item["targetKind"],
            target_key=item["targetKey"],
            evidence_index=int(item["evidenceIndex"]),
            document_id=uuid.UUID(item["documentId"]),
            chunk_index=int(item["chunkIndex"]),
            page_no=None if item["pageNo"] is None else int(item["pageNo"]),
            paragraph_start=(
                None
                if item["paragraphStart"] is None
                else int(item["paragraphStart"])
            ),
            paragraph_end=(
                None if item["paragraphEnd"] is None else int(item["paragraphEnd"])
            ),
            source_start=int(item["sourceStart"]),
            source_end=int(item["sourceEnd"]),
            quote=item["quote"],
        )
        for item in json.loads(row.evidence_json or "[]")  # type: ignore[attr-defined]
    )
    node_count = int(row.node_count)  # type: ignore[attr-defined]
    edge_count = int(row.edge_count)  # type: ignore[attr-defined]
    if len(nodes) != node_count or len(edges) != edge_count:
        logger.error(
            "单语句快照计数与明细不一致 build=%s nodes=%s/%s edges=%s/%s",
            row.id,  # type: ignore[attr-defined]
            len(nodes),
            node_count,
            len(edges),
            edge_count,
        )
        raise GraphSnapshotInconsistent
    return GraphSnapshot(
        id=row.snapshot_id,  # type: ignore[attr-defined]
        build_id=row.id,  # type: ignore[attr-defined]
        revision=int(row.revision),  # type: ignore[attr-defined]
        complete=bool(row.complete),  # type: ignore[attr-defined]
        published=bool(row.published),  # type: ignore[attr-defined]
        node_count=node_count,
        edge_count=edge_count,
        episode_count=int(row.episode_count),  # type: ignore[attr-defined]
        created_at=row.snapshot_created_at,  # type: ignore[attr-defined]
        published_at=row.published_at,  # type: ignore[attr-defined]
        nodes=nodes,
        edges=edges,
        evidence=evidence,
    )


async def read_build_graph(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    build_id: str | uuid.UUID | None = None,
) -> tuple[GraphBuild, GraphSnapshot | None]:
    """用一条 SQL 返回同一数据库视图中的 build 与最近已发布快照。

    读取不获取 build/job 行锁，因此不会与 worker 的 job → build 写入顺序形成
    环；build 状态、快照头、节点、边与来源由同一语句快照决定。
    """
    parsed = None if build_id is None else _parse_build_id(build_id)
    try:
        result = await db.execute(
            text(READ_BUILD_GRAPH_SQL),
            {"project_id": project_id, "build_id": parsed},
        )
        row = result.first()
    except GraphBuildNotFound:
        raise
    except DB_ERRORS as exc:
        logger.warning("建图读取失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    if row is None:
        raise GraphBuildNotFound
    snapshot = await _snapshot_from_build_graph_row(row)
    return _as_build(row), snapshot


def _validate_snapshot_limits(
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
    evidence: Sequence[GraphEvidence],
) -> None:
    if len(nodes) > MAX_GRAPH_NODES:
        raise GraphLimitExceeded(
            code="graph_too_large",
            limit=MAX_GRAPH_NODES,
            actual=len(nodes),
            target="nodes",
        )
    if len(edges) > MAX_GRAPH_EDGES:
        raise GraphLimitExceeded(
            code="graph_too_large",
            limit=MAX_GRAPH_EDGES,
            actual=len(edges),
            target="edges",
        )
    per_target: dict[tuple[str, str], int] = {}
    for item in evidence:
        key = (item.target_kind, item.target_key)
        per_target[key] = per_target.get(key, 0) + 1
        if per_target[key] > MAX_EVIDENCE_PER_TARGET:
            raise GraphLimitExceeded(
                code="too_many_sources",
                limit=MAX_EVIDENCE_PER_TARGET,
                actual=per_target[key],
                target=f"{item.target_kind}:{item.target_key}",
            )


async def create_snapshot(
    db: AsyncSession,
    *,
    build_id: uuid.UUID,
    revision: int,
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
    evidence: Sequence[GraphEvidence],
    complete: bool = False,
    published: bool = False,
    episode_count: int = 0,
) -> GraphSnapshot:
    """写入一份完整物化快照；默认是候选快照，不可通过业务读取取得。

    本方法不提交也不回滚：调用方负责把快照、build 状态、job 终态与完成事件放在
    同一个事务内提交，或整体回滚。`complete=True, published=True` 同样遵守该边界。
    """
    if revision < 1:
        raise ValueError("快照版本必须从 1 开始")
    _validate_snapshot_limits(nodes, edges, evidence)
    snapshot_id = uuid.uuid4()
    try:
        if published:
            # 已发布快照与 build 状态共享同一锁顺序：先 build，再写快照。
            locked = await db.execute(
                text(
                    "SELECT id FROM graph_builds WHERE id = :build_id FOR UPDATE"
                ),
                {"build_id": build_id},
            )
            if locked.first() is None:
                raise GraphBuildNotFound
        header = (
            await db.execute(
                text(
                    "INSERT INTO graph_snapshots (id, build_id, revision, complete, "
                    "published, node_count, edge_count, episode_count, published_at) "
                    "VALUES (:id, :build_id, :revision, :complete, :published, "
                    ":node_count, :edge_count, :episode_count, "
                    "CASE WHEN :published THEN now() ELSE NULL END) "
                    "RETURNING created_at, published_at"
                ),
                {
                    "id": snapshot_id,
                    "build_id": build_id,
                    "revision": revision,
                    "complete": complete,
                    "published": published,
                    "node_count": len(nodes),
                    "edge_count": len(edges),
                    "episode_count": episode_count,
                },
            )
        ).one()
        if nodes:
            await db.execute(
                text(
                    "INSERT INTO graph_nodes (snapshot_id, node_key, node_type, "
                    "label, origin, summary) VALUES (:snapshot_id, :node_key, "
                    ":node_type, :label, :origin, :summary)"
                ),
                [
                    {
                        "snapshot_id": snapshot_id,
                        "node_key": node.key,
                        "node_type": node.node_type,
                        "label": node.label,
                        "origin": node.origin,
                        "summary": node.summary,
                    }
                    for node in nodes
                ],
            )
        if edges:
            await db.execute(
                text(
                    "INSERT INTO graph_edges (snapshot_id, edge_key, edge_type, "
                    "label, origin, summary, source_node_key, target_node_key) "
                    "VALUES (:snapshot_id, :edge_key, :edge_type, :label, :origin, "
                    ":summary, :source_node_key, :target_node_key)"
                ),
                [
                    {
                        "snapshot_id": snapshot_id,
                        "edge_key": edge.key,
                        "edge_type": edge.edge_type,
                        "label": edge.label,
                        "origin": edge.origin,
                        "summary": edge.summary,
                        "source_node_key": edge.source_key,
                        "target_node_key": edge.target_key,
                    }
                    for edge in edges
                ],
            )
        if evidence:
            await db.execute(
                text(
                    "INSERT INTO graph_evidence (snapshot_id, target_kind, "
                    "target_key, evidence_index, document_id, chunk_index, page_no, "
                    "paragraph_start, paragraph_end, source_start, source_end, quote) "
                    "VALUES (:snapshot_id, :target_kind, :target_key, "
                    ":evidence_index, :document_id, :chunk_index, :page_no, "
                    ":paragraph_start, :paragraph_end, :source_start, :source_end, "
                    ":quote)"
                ),
                [
                    {
                        "snapshot_id": snapshot_id,
                        "target_kind": item.target_kind,
                        "target_key": item.target_key,
                        "evidence_index": item.evidence_index,
                        "document_id": item.document_id,
                        "chunk_index": item.chunk_index,
                        "page_no": item.page_no,
                        "paragraph_start": item.paragraph_start,
                        "paragraph_end": item.paragraph_end,
                        "source_start": item.source_start,
                        "source_end": item.source_end,
                        "quote": item.quote,
                    }
                    for item in evidence
                ],
            )
    except GraphBuildNotFound:
        raise
    except DB_ERRORS as exc:
        logger.warning("快照写入失败 build=%s: %s", build_id, type(exc).__name__)
        raise ServiceUnavailable from exc

    return GraphSnapshot(
        id=snapshot_id,
        build_id=build_id,
        revision=revision,
        complete=complete,
        published=published,
        node_count=len(nodes),
        edge_count=len(edges),
        episode_count=episode_count,
        created_at=header.created_at,
        published_at=header.published_at,
        nodes=tuple(sorted(nodes, key=lambda item: item.key)),
        edges=tuple(sorted(edges, key=lambda item: item.key)),
        evidence=tuple(
            sorted(
                evidence,
                key=lambda item: (
                    item.target_kind,
                    item.target_key,
                    item.evidence_index,
                ),
            )
        ),
    )


async def _read_snapshot_header(
    db: AsyncSession,
    *,
    build_id: uuid.UUID,
    revision: int | None,
) -> object:
    """读取快照头并加共享锁；锁定行在本次事务结束前不会被清理或改写。"""
    if revision is not None and revision < 1:
        raise GraphSnapshotNotFound
    try:
        if revision is None:
            result = await db.execute(
                text(
                    "SELECT id, build_id, revision, complete, published, node_count, "
                    "edge_count, episode_count, created_at, published_at "
                    "FROM graph_snapshots WHERE build_id = :build_id "
                    "AND published = true ORDER BY revision DESC LIMIT 1 "
                    "FOR SHARE"
                ),
                {"build_id": build_id},
            )
        else:
            result = await db.execute(
                text(
                    "SELECT id, build_id, revision, complete, published, node_count, "
                    "edge_count, episode_count, created_at, published_at "
                    "FROM graph_snapshots WHERE build_id = :build_id "
                    "AND revision = :revision AND published = true FOR SHARE"
                ),
                {"build_id": build_id, "revision": revision},
            )
        row = result.first()
    except DB_ERRORS as exc:
        logger.warning("快照头读取失败 build=%s: %s", build_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    if row is None:
        raise GraphSnapshotNotFound
    return row


async def _read_snapshot_details(
    db: AsyncSession, row: object
) -> GraphSnapshot:
    """在快照头锁保护下读取明细，并拒绝计数与数组长度不一致的结果。"""
    snapshot_id = row.id  # type: ignore[attr-defined]
    try:
        node_rows = (
            await db.execute(
                text(
                    "SELECT node_key, node_type, label, origin, summary "
                    "FROM graph_nodes WHERE snapshot_id = :snapshot_id "
                    "ORDER BY node_key"
                ),
                {"snapshot_id": snapshot_id},
            )
        ).fetchall()
        edge_rows = (
            await db.execute(
                text(
                    "SELECT edge_key, edge_type, label, origin, summary, "
                    "source_node_key, target_node_key FROM graph_edges "
                    "WHERE snapshot_id = :snapshot_id ORDER BY edge_key"
                ),
                {"snapshot_id": snapshot_id},
            )
        ).fetchall()
        evidence_rows = (
            await db.execute(
                text(
                    "SELECT target_kind, target_key, evidence_index, document_id, "
                    "chunk_index, page_no, paragraph_start, paragraph_end, "
                    "source_start, source_end, quote FROM graph_evidence "
                    "WHERE snapshot_id = :snapshot_id "
                    "ORDER BY target_kind, target_key, evidence_index"
                ),
                {"snapshot_id": snapshot_id},
            )
        ).fetchall()
    except DB_ERRORS as exc:
        logger.warning("快照明细读取失败 snapshot=%s: %s", snapshot_id, type(exc).__name__)
        raise ServiceUnavailable from exc

    nodes = tuple(
        GraphNode(
            key=item.node_key,
            node_type=item.node_type,
            label=item.label,
            origin=item.origin,
            summary=item.summary,
        )
        for item in node_rows
    )
    edges = tuple(
        GraphEdge(
            key=item.edge_key,
            edge_type=item.edge_type,
            label=item.label,
            origin=item.origin,
            summary=item.summary,
            source_key=item.source_node_key,
            target_key=item.target_node_key,
        )
        for item in edge_rows
    )
    evidence = tuple(
        GraphEvidence(
            target_kind=item.target_kind,
            target_key=item.target_key,
            evidence_index=int(item.evidence_index),
            document_id=item.document_id,
            chunk_index=int(item.chunk_index),
            page_no=None if item.page_no is None else int(item.page_no),
            paragraph_start=(
                None if item.paragraph_start is None else int(item.paragraph_start)
            ),
            paragraph_end=(
                None if item.paragraph_end is None else int(item.paragraph_end)
            ),
            source_start=int(item.source_start),
            source_end=int(item.source_end),
            quote=item.quote,
        )
        for item in evidence_rows
    )
    node_count = int(row.node_count)  # type: ignore[attr-defined]
    edge_count = int(row.edge_count)  # type: ignore[attr-defined]
    if len(nodes) != node_count or len(edges) != edge_count:
        logger.error(
            "快照计数与明细不一致 snapshot=%s nodes=%s/%s edges=%s/%s",
            snapshot_id,
            len(nodes),
            node_count,
            len(edges),
            edge_count,
        )
        raise GraphSnapshotInconsistent
    return GraphSnapshot(
        id=row.id,  # type: ignore[attr-defined]
        build_id=row.build_id,  # type: ignore[attr-defined]
        revision=int(row.revision),  # type: ignore[attr-defined]
        complete=bool(row.complete),  # type: ignore[attr-defined]
        published=bool(row.published),  # type: ignore[attr-defined]
        node_count=node_count,
        edge_count=edge_count,
        episode_count=int(row.episode_count),  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        published_at=row.published_at,  # type: ignore[attr-defined]
        nodes=nodes,
        edges=edges,
        evidence=evidence,
    )


async def get_published_snapshot(
    db: AsyncSession,
    *,
    build_id: uuid.UUID,
    revision: int | None = None,
) -> GraphSnapshot:
    """按版本读取已发布快照；候选快照与未发布版本一律不可见。

    快照头以共享锁固定后再读明细，因此同一次返回的头、节点、边与来源来自同一
    个已发布版本；清理事务若试图删除该行会等待本次读取结束。
    """
    header = await _read_snapshot_header(
        db, build_id=build_id, revision=revision
    )
    return await _read_snapshot_details(db, header)


async def _prune_partial_snapshots(
    db: AsyncSession, *, build_id: uuid.UUID, keep: int
) -> list[int]:
    result = await db.execute(
        text(
            "WITH kept AS ("
            "  SELECT id FROM graph_snapshots "
            "  WHERE build_id = :build_id AND published = true AND complete = false "
            "  ORDER BY revision DESC, id DESC LIMIT :keep"
            ") "
            "DELETE FROM graph_snapshots s "
            "WHERE s.build_id = :build_id AND s.published = true "
            "AND s.complete = false AND s.id NOT IN (SELECT id FROM kept) "
            "RETURNING s.revision"
        ),
        {"build_id": build_id, "keep": max(0, keep)},
    )
    return [int(row.revision) for row in result.fetchall()]


async def prune_partial_snapshots(
    db: AsyncSession,
    *,
    build_id: uuid.UUID,
    keep: int = PARTIAL_SNAPSHOT_RETENTION,
) -> list[int]:
    """保留最近 `keep` 份已发布部分快照，删除更旧的部分快照。

    本方法不提交：调用方决定与发布事务一起提交或回滚。
    """
    try:
        return await _prune_partial_snapshots(
            db, build_id=build_id, keep=keep
        )
    except DB_ERRORS as exc:
        logger.warning("部分快照清理失败 build=%s: %s", build_id, type(exc).__name__)
        raise ServiceUnavailable from exc


async def publish_snapshot(
    db: AsyncSession,
    *,
    snapshot_id: uuid.UUID,
    complete: bool = False,
    keep_partials: int = PARTIAL_SNAPSHOT_RETENTION,
) -> GraphSnapshot:
    """发布一份快照；标记最终快照时同时清理过旧的部分快照。

    本方法不提交也不回滚。调用方可以把发布、build 状态、job 终态与完成事件放在
    同一个事务内提交；共享锁顺序固定为 build 行在前、快照行在后，与业务读取一致。
    """
    try:
        lookup = await db.execute(
            text(
                "SELECT build_id, revision FROM graph_snapshots "
                "WHERE id = :snapshot_id"
            ),
            {"snapshot_id": snapshot_id},
        )
        lookup_row = lookup.first()
        if lookup_row is None:
            raise GraphSnapshotNotFound
        build_id = lookup_row.build_id
        revision = int(lookup_row.revision)

        locked_build = await db.execute(
            text("SELECT id FROM graph_builds WHERE id = :build_id FOR UPDATE"),
            {"build_id": build_id},
        )
        if locked_build.first() is None:
            raise GraphSnapshotNotFound
        locked_snapshot = await db.execute(
            text(
                "SELECT id FROM graph_snapshots WHERE id = :snapshot_id "
                "FOR UPDATE"
            ),
            {"snapshot_id": snapshot_id},
        )
        if locked_snapshot.first() is None:
            raise GraphSnapshotNotFound

        await db.execute(
            text(
                "UPDATE graph_snapshots SET published = true, "
                "complete = (complete OR :complete), "
                "published_at = COALESCE(published_at, now()) "
                "WHERE id = :snapshot_id"
            ),
            {"snapshot_id": snapshot_id, "complete": complete},
        )
        if complete:
            await _prune_partial_snapshots(
                db, build_id=build_id, keep=keep_partials
            )
    except GraphSnapshotNotFound:
        raise
    except DB_ERRORS as exc:
        logger.warning("快照发布失败 snapshot=%s: %s", snapshot_id, type(exc).__name__)
        raise ServiceUnavailable from exc

    header = await _read_snapshot_header(
        db, build_id=build_id, revision=revision
    )
    return await _read_snapshot_details(db, header)


async def record_episode(
    db: AsyncSession,
    *,
    build_id: uuid.UUID,
    episode_key: str,
    content: str,
    content_sha256: str,
    state: str = "planned",
    episode_uuid: uuid.UUID | None = None,
    attempts_increment: int = 0,
    write_outcome: str | None = None,
) -> None:
    """记录 episode 状态并重算 build 的 planned/accepted/confirmed 计数。

    本方法不提交也不回滚：调用方负责提交。先锁 build 行再写 episode，使同一
    build 的并发 episode 写入串行重算计数，汇总始终与已提交明细一致。已持久化的
    内容、内容哈希与幂等键不会因重试被改写；已绑定 UUID 出现冲突值时明确失败。
    """
    if state not in EPISODE_STATES:
        raise ValueError(f"非法 episode 状态: {state}")
    if write_outcome is not None and write_outcome not in WRITE_OUTCOMES:
        raise ValueError(f"非法写结果: {write_outcome}")
    try:
        locked = await db.execute(
            text("SELECT id FROM graph_builds WHERE id = :build_id FOR UPDATE"),
            {"build_id": build_id},
        )
        if locked.first() is None:
            raise GraphBuildNotFound
        written = await db.execute(
            text(
                "INSERT INTO graph_episodes (build_id, episode_key, "
                "idempotency_key, content, content_sha256, episode_uuid, state, "
                "attempts, write_outcome) VALUES (:build_id, :episode_key, "
                ":idempotency_key, :content, :content_sha256, :episode_uuid, "
                ":state, :attempts, COALESCE(:write_outcome, 'not_attempted')) "
                "ON CONFLICT (build_id, episode_key) DO UPDATE SET "
                "episode_uuid = COALESCE(graph_episodes.episode_uuid, "
                "EXCLUDED.episode_uuid), "
                "state = CASE "
                "WHEN graph_episodes.state = 'failed' THEN 'failed' "
                "WHEN graph_episodes.state = 'processed' THEN 'processed' "
                "WHEN graph_episodes.state = 'matched' "
                "AND EXCLUDED.state <> 'processed' THEN 'matched' "
                "WHEN graph_episodes.state = 'written' "
                "AND EXCLUDED.state = 'planned' THEN 'written' "
                "ELSE EXCLUDED.state END, "
                "attempts = graph_episodes.attempts + :attempts_increment, "
                "write_outcome = CASE "
                "WHEN graph_episodes.write_outcome = 'accepted' THEN 'accepted' "
                "WHEN :write_outcome IS NULL THEN graph_episodes.write_outcome "
                "ELSE :write_outcome END, "
                "updated_at = now() "
                "WHERE graph_episodes.episode_uuid IS NULL "
                "OR EXCLUDED.episode_uuid IS NULL "
                "OR graph_episodes.episode_uuid = EXCLUDED.episode_uuid "
                "RETURNING graph_episodes.episode_key"
            ),
            {
                "build_id": build_id,
                "episode_key": episode_key,
                "idempotency_key": uuid.uuid4(),
                "content": content,
                "content_sha256": content_sha256,
                "episode_uuid": episode_uuid,
                "state": state,
                "attempts": max(0, attempts_increment),
                "attempts_increment": max(0, attempts_increment),
                "write_outcome": write_outcome,
            },
        )
        if written.first() is None:
            raise GraphEpisodeConflict
        counts = await db.execute(
            text(
                "SELECT count(*) AS planned, "
                "count(*) FILTER (WHERE episode_uuid IS NOT NULL) AS accepted, "
                "count(*) FILTER (WHERE state IN ('matched', 'processed')) "
                "AS confirmed FROM graph_episodes WHERE build_id = :build_id"
            ),
            {"build_id": build_id},
        )
        row = counts.one()
        await db.execute(
            text(
                "UPDATE graph_builds SET planned_episodes = :planned, "
                "accepted_episodes = :accepted, confirmed_episodes = :confirmed, "
                "updated_at = now() WHERE id = :build_id"
            ),
            {
                "build_id": build_id,
                "planned": int(row.planned),
                "accepted": int(row.accepted),
                "confirmed": int(row.confirmed),
            },
        )
    except (GraphBuildNotFound, GraphEpisodeConflict):
        raise
    except DB_ERRORS as exc:
        logger.warning("episode 记录失败 build=%s: %s", build_id, type(exc).__name__)
        raise ServiceUnavailable from exc


async def get_build_for_run(
    db: AsyncSession, *, run_id: uuid.UUID
) -> GraphBuild:
    try:
        result = await db.execute(
            text(
                f"SELECT {BUILD_COLUMNS} {BUILD_SOURCE} WHERE b.run_id = :run_id"
            ),
            {"run_id": run_id},
        )
        row = result.first()
    except DB_ERRORS as exc:
        logger.warning("run 建图读取失败 run=%s: %s", run_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    if row is None:
        raise GraphBuildNotFound
    return _as_build(row)


async def list_build_documents(
    db: AsyncSession, *, build_id: uuid.UUID, project_id: uuid.UUID
) -> list[BuildDocument]:
    try:
        result = await db.execute(
            text(
                "SELECT d.id, d.display_name, d.kind, d.status, d.sha256, "
                "d.chunk_count, b.ordinal FROM graph_build_documents b "
                "JOIN graph_builds g ON g.id = b.build_id "
                "JOIN documents d ON d.id = b.document_id "
                "WHERE b.build_id = :build_id AND g.project_id = :project_id "
                "ORDER BY b.ordinal"
            ),
            {"build_id": build_id, "project_id": project_id},
        )
        rows = result.fetchall()
    except DB_ERRORS as exc:
        logger.warning("建图材料读取失败 build=%s: %s", build_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    return [
        BuildDocument(
            document_id=row.id,
            display_name=row.display_name,
            kind=row.kind,
            status=row.status,
            sha256=row.sha256.strip(),
            chunk_count=int(row.chunk_count),
            ordinal=int(row.ordinal),
        )
        for row in rows
    ]


async def list_build_chunks(
    db: AsyncSession, *, build_id: uuid.UUID, project_id: uuid.UUID
) -> list[BuildChunk]:
    try:
        result = await db.execute(
            text(
                "SELECT c.document_id, d.display_name, c.chunk_index, c.text, "
                "c.source_start, c.source_end, c.page_no, c.paragraph_start, "
                "c.paragraph_end FROM graph_build_documents b "
                "JOIN graph_builds g ON g.id = b.build_id "
                "JOIN documents d ON d.id = b.document_id "
                "JOIN document_chunks c ON c.document_id = d.id "
                "WHERE b.build_id = :build_id AND g.project_id = :project_id "
                "AND d.status = 'ready' "
                "ORDER BY b.ordinal, c.chunk_index"
            ),
            {"build_id": build_id, "project_id": project_id},
        )
        rows = result.fetchall()
    except DB_ERRORS as exc:
        logger.warning("建图分块读取失败 build=%s: %s", build_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    return [
        BuildChunk(
            document_id=row.document_id,
            document_name=row.display_name,
            chunk_index=int(row.chunk_index),
            text=row.text,
            source_start=int(row.source_start),
            source_end=int(row.source_end),
            page_no=None if row.page_no is None else int(row.page_no),
            paragraph_start=(
                None if row.paragraph_start is None else int(row.paragraph_start)
            ),
            paragraph_end=(
                None if row.paragraph_end is None else int(row.paragraph_end)
            ),
        )
        for row in rows
    ]


async def list_episodes(
    db: AsyncSession, *, build_id: uuid.UUID
) -> list[GraphEpisode]:
    """读取 build 已持久化的 episode 键、内容哈希、UUID 与状态。"""
    try:
        result = await db.execute(
            text(
                "SELECT build_id, episode_key, idempotency_key, content, "
                "content_sha256, episode_uuid, state, attempts, write_outcome "
                "FROM graph_episodes WHERE build_id = :build_id "
                "ORDER BY episode_key"
            ),
            {"build_id": build_id},
        )
        rows = result.fetchall()
    except DB_ERRORS as exc:
        logger.warning("episode 列表读取失败 build=%s: %s", build_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    return [_as_episode(row) for row in rows]


async def get_episode(
    db: AsyncSession, *, build_id: uuid.UUID, episode_key: str
) -> GraphEpisode:
    try:
        result = await db.execute(
            text(
                "SELECT build_id, episode_key, idempotency_key, content, "
                "content_sha256, episode_uuid, state, attempts, write_outcome "
                "FROM graph_episodes WHERE build_id = :build_id "
                "AND episode_key = :episode_key"
            ),
            {"build_id": build_id, "episode_key": episode_key},
        )
        row = result.first()
    except DB_ERRORS as exc:
        logger.warning("episode 读取失败 build=%s: %s", build_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    if row is None:
        raise GraphEpisodeNotFound
    return _as_episode(row)


async def get_or_create_binding(
    db: AsyncSession, *, project_id: uuid.UUID
) -> GraphBinding:
    """取得项目绑定；首次创建时先提交幂等键与稳定参数，再返回给调用方。"""
    request_name, request_description, request_time_zone = _binding_params(project_id)
    try:
        result = await db.execute(
            text(
                "INSERT INTO project_graph_bindings (project_id, "
                "create_idempotency_key, request_name, request_description, "
                "request_time_zone, state) "
                "VALUES (:project_id, :create_idempotency_key, :request_name, "
                ":request_description, :request_time_zone, 'pending') "
                "ON CONFLICT (project_id) DO NOTHING "
                f"RETURNING {BINDING_COLUMNS}"
            ),
            {
                "project_id": project_id,
                "create_idempotency_key": uuid.uuid4(),
                "request_name": request_name,
                "request_description": request_description,
                "request_time_zone": request_time_zone,
            },
        )
        row = result.first()
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("项目图谱绑定创建失败 project=%s: %s", project_id, type(exc).__name__)
        raise ProjectNotFound from exc
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("项目图谱绑定读写失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc

    if row is not None:
        return _as_binding(row)
    return await require_binding(db, project_id=project_id)


async def get_binding(
    db: AsyncSession, *, project_id: uuid.UUID
) -> GraphBinding | None:
    try:
        result = await db.execute(
            text(
                f"SELECT {BINDING_COLUMNS} FROM project_graph_bindings "
                "WHERE project_id = :project_id"
            ),
            {"project_id": project_id},
        )
        row = result.first()
    except DB_ERRORS as exc:
        logger.warning("项目图谱绑定读取失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    return _as_binding(row) if row is not None else None


async def require_binding(
    db: AsyncSession, *, project_id: uuid.UUID
) -> GraphBinding:
    """只接受当前项目的绑定记录；缺失时不回退到验收图谱或其它项目。"""
    binding = await get_binding(db, project_id=project_id)
    if binding is None:
        raise GraphBindingNotFound
    return binding


async def save_binding_graph_id(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    graph_id: str | uuid.UUID | None,
    commit: bool = True,
) -> GraphBinding:
    """保存服务端返回的图谱 UUID；空值或与已有值不一致时拒绝覆盖。

    `commit=False` 时由调用方在同一事务内提交，用于把绑定 UUID 与 step/事件原子写入。
    """
    parsed = _coerce_graph_id(graph_id)
    try:
        result = await db.execute(
            text(
                f"SELECT {BINDING_COLUMNS} FROM project_graph_bindings "
                "WHERE project_id = :project_id FOR UPDATE"
            ),
            {"project_id": project_id},
        )
        row = result.first()
        if row is None:
            await db.rollback()
            raise GraphBindingNotFound
        existing = row.graph_id
        if existing is not None:
            if existing == parsed:
                if commit:
                    await db.commit()
                return _as_binding(row)
            await db.rollback()
            raise GraphBindingConflict("invalid_graph_scope")

        updated = await db.execute(
            text(
                "UPDATE project_graph_bindings SET graph_id = :graph_id, "
                "state = 'bound', bound_at = now(), updated_at = now() "
                "WHERE project_id = :project_id AND graph_id IS NULL "
                f"RETURNING {BINDING_COLUMNS}"
            ),
            {"project_id": project_id, "graph_id": parsed},
        )
        saved = updated.first()
        if saved is None:
            await db.rollback()
            raise GraphBindingConflict("invalid_graph_scope")
        if commit:
            await db.commit()
        return _as_binding(saved)
    except IntegrityError as exc:
        # 另一项目已保存同一图谱 UUID：唯一约束裁决，当前项目不得复用。
        await db.rollback()
        logger.warning("图谱绑定 UUID 冲突 project=%s: %s", project_id, type(exc).__name__)
        raise GraphBindingConflict("invalid_graph_scope") from exc
    except (GraphBindingConflict, GraphBindingNotFound):
        raise
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("图谱绑定保存失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc


async def start_graph_build(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    question: str,
    document_ids: Sequence[uuid.UUID],
    max_documents: int,
) -> tuple[GraphBuild, bool]:
    """开始业务建图；同一项目同一操作只允许一个活动任务。"""
    ordered = list(dict.fromkeys(document_ids))
    if not ordered:
        raise GraphBuildConflict("invalid_graph_scope")
    if len(ordered) > max_documents:
        raise GraphBuildConflict("too_many_documents")
    lock_key = f"{project_id}:{GRAPH_BUILD_OPERATION_KEY}"
    try:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )
        existing = await db.execute(
            text(
                "SELECT b.id FROM graph_builds b JOIN jobs j ON j.run_id = b.run_id "
                "WHERE b.project_id = :project_id AND j.operation_key = :operation "
                "AND j.status IN ('queued', 'running') "
                "ORDER BY b.created_at DESC, b.id DESC LIMIT 1"
            ),
            {
                "project_id": project_id,
                "operation": GRAPH_BUILD_OPERATION_KEY,
            },
        )
        row = existing.first()
        if row is not None:
            build = await get_build(
                db, project_id=project_id, build_id=row.id
            )
            await db.commit()
            return build, False

        statement = text(
            "SELECT id, status, chunk_count FROM documents "
            "WHERE project_id = :project_id AND id IN :document_ids"
        ).bindparams(bindparam("document_ids", expanding=True))
        documents = (
            await db.execute(
                statement,
                {"project_id": project_id, "document_ids": ordered},
            )
        ).fetchall()
        if len(documents) != len(ordered):
            raise DocumentNotFound
        if any(document.status != "ready" for document in documents):
            raise GraphBuildConflict("document_not_ready")
        build = await create_build(
            db,
            project_id=project_id,
            actor_id=actor_id,
            question=question,
            document_ids=ordered,
            commit=False,
        )
        await db.commit()
        return build, True
    except (DocumentNotFound, GraphBuildConflict):
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("开始建图冲突 project=%s: %s", project_id, type(exc).__name__)
        raise GraphBuildConflict from exc
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("开始建图失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc


def snapshot_can_continue(snapshot: GraphSnapshot) -> bool:
    """只有完整、已发布且材料陈述均带有效来源的快照才能进入下一阶段。"""
    if not snapshot.complete or not snapshot.published:
        return False
    material_nodes = {
        node.key for node in snapshot.nodes if node.origin == "material"
    }
    material_edges = {
        edge.key for edge in snapshot.edges if edge.origin == "material"
    }
    if not material_nodes or not material_edges:
        return False
    evidence_nodes = {
        item.target_key
        for item in snapshot.evidence
        if item.target_kind == "node" and item.quote and item.source_end > item.source_start
    }
    evidence_edges = {
        item.target_key
        for item in snapshot.evidence
        if item.target_kind == "edge" and item.quote and item.source_end > item.source_start
    }
    return material_nodes.issubset(evidence_nodes) and material_edges.issubset(
        evidence_edges
    )


async def cancel_for_run(db: AsyncSession, *, run_id: uuid.UUID) -> None:
    """取消事务内标记 build 取消；已发布部分快照保留可读。"""
    await db.execute(
        text(
            "UPDATE graph_builds SET phase = 'canceled', "
            "failure_code = 'canceled', failure_message = '建图已取消', "
            "can_continue = false, finished_at = now(), updated_at = now() "
            "WHERE run_id = :run_id AND phase NOT IN ('ready', 'failed', 'canceled')"
        ),
        {"run_id": run_id},
    )


async def max_snapshot_revision(
    db: AsyncSession, *, build_id: uuid.UUID
) -> int:
    try:
        result = await db.execute(
            text(
                "SELECT COALESCE(MAX(revision), 0) FROM graph_snapshots "
                "WHERE build_id = :build_id"
            ),
            {"build_id": build_id},
        )
        return int(result.scalar_one())
    except DB_ERRORS as exc:
        logger.warning("快照版本读取失败 build=%s: %s", build_id, type(exc).__name__)
        raise ServiceUnavailable from exc


async def document_kinds(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    document_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, tuple[str, str]]:
    """返回来源材料的类型与显示名，用于生成 PDF/文本定位地址。"""
    if not document_ids:
        return {}
    try:
        statement = text(
            "SELECT id, kind, display_name FROM documents "
            "WHERE project_id = :project_id AND id IN :document_ids"
        ).bindparams(bindparam("document_ids", expanding=True))
        rows = (
            await db.execute(
                statement,
                {
                    "project_id": project_id,
                    "document_ids": list(dict.fromkeys(document_ids)),
                },
            )
        ).fetchall()
    except DB_ERRORS as exc:
        logger.warning("来源材料读取失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    return {row.id: (row.kind, row.display_name) for row in rows}
