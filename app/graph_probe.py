"""Change 07 验收专用图谱探针：绑定窗口、失败分类与发布竞态。

只在 `make verify CHANGE=2026-11-22-build-evidence-graph` 中由隔离 Compose 项目调用。
复用真实数据库、worker 步骤与业务图谱函数；不访问网络，不读取私有材料。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SessionLocal, dispose_engine
from .engines.business_config import BusinessGraphConfig
from .engines.business_graph import (
    BusinessGraphError,
    confirm_episode,
    readback_graph,
)
from .engines.business_stub import StubGraphClient, StubPager
from .errors import GraphSnapshotNotFound
from .services import evidence_graph as graph_store
from .services import jobs as job_service
from .services import worker as worker_service


def _config() -> BusinessGraphConfig:
    return BusinessGraphConfig.from_env(
        {
            "EVIDENCE_GRAPH_MODEL_PROVIDER": "stub",
            "EVIDENCE_GRAPH_GRAPH_PROVIDER": "stub",
            "EVIDENCE_GRAPH_ALLOW_STUB": "true",
        }
    )


async def _owner_id(db: AsyncSession, project_id: uuid.UUID) -> uuid.UUID:
    return (
        await db.execute(
            text("SELECT owner_id FROM projects WHERE id = :project_id"),
            {"project_id": project_id},
        )
    ).scalar_one()


async def _create_build(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    document_ids: list[uuid.UUID],
    question: str,
) -> graph_store.GraphBuild:
    owner = await _owner_id(db, project_id)
    return await graph_store.create_build(
        db,
        project_id=project_id,
        actor_id=owner,
        question=question,
        document_ids=document_ids,
    )


async def _cleanup_build(db: AsyncSession, build: graph_store.GraphBuild) -> None:
    await db.execute(
        text("DELETE FROM runs WHERE id = :run_id"), {"run_id": build.run_id}
    )
    await db.commit()


async def _clear_binding(db: AsyncSession, project_id: uuid.UUID) -> None:
    await db.execute(
        text("DELETE FROM project_graph_bindings WHERE project_id = :project_id"),
        {"project_id": project_id},
    )
    await db.commit()


async def _binding_facts(
    db: AsyncSession, project_id: uuid.UUID
) -> dict[str, int]:
    row = (
        await db.execute(
            text(
                "SELECT count(*) AS rows, "
                "count(DISTINCT graph_id) AS graphs, "
                "count(graph_id) AS bound FROM project_graph_bindings "
                "WHERE project_id = :project_id"
            ),
            {"project_id": project_id},
        )
    ).one()
    return {
        "bindingRows": int(row.rows),
        "distinctGraphIds": int(row.graphs),
        "boundRows": int(row.bound),
    }


async def _claim(
    db: AsyncSession, build: graph_store.GraphBuild, worker_id: str
) -> worker_service.Claim:
    claim = await worker_service.claim_next(
        db,
        worker_id=worker_id,
        lease_seconds=30.0,
        job_id=build.job_id,
    )
    if claim is None:
        raise RuntimeError("图谱探针任务未被领取")
    return claim


async def _expire_and_reclaim(
    db: AsyncSession, build: graph_store.GraphBuild, worker_id: str
) -> worker_service.Claim:
    await db.execute(
        text(
            "UPDATE jobs SET lease_expires_at = clock_timestamp() "
            "- interval '1 second' WHERE id = :job_id"
        ),
        {"job_id": build.job_id},
    )
    await db.commit()
    return await _claim(db, build, worker_id)


async def mode_bind_windows(project_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
    config = _config()
    cases: list[dict[str, Any]] = []
    async with SessionLocal() as db:
        for name, client, takeover in (
            ("response_lost", StubGraphClient(), False),
            ("response_arrived_db_not_committed", StubGraphClient(), False),
            ("takeover_pending_binding", StubGraphClient(), True),
        ):
            if name == "response_lost":
                client.lose_create_response = True
            if name == "response_arrived_db_not_committed":
                client.fail_after_create = True
            await _clear_binding(db, project_id)
            build = await _create_build(
                db,
                project_id=project_id,
                document_ids=[document_id],
                question="验收探针：项目图谱绑定窗口",
            )
            try:
                claim = await _claim(db, build, f"graph-probe-{name}")
                if takeover:
                    binding = await graph_store.get_or_create_binding(
                        db, project_id=project_id
                    )
                    manual = client.graph.create(
                        name=binding.request_name,
                        description=binding.request_description,
                        time_zone=binding.request_time_zone,
                        idempotency_key=str(binding.create_idempotency_key),
                    )
                    if not manual.get("uuid"):
                        raise RuntimeError("探针预置图谱创建未返回 UUID")
                    claim = await _expire_and_reclaim(
                        db, build, f"graph-probe-{name}-takeover"
                    )
                outcome = await worker_service.execute_graph_bind(
                    claim,
                    session_factory=SessionLocal,
                    stop_event=None,
                    lease_seconds=30.0,
                    heartbeat_seconds=1.0,
                    config=config,
                    graph_client_factory=lambda client=client: client,
                )
                if outcome != "progress":
                    raise RuntimeError(f"绑定窗口 {name} 未提交：{outcome}")
                facts = await _binding_facts(db, project_id)
                keys = list(client.create_keys)
                cases.append(
                    {
                        "case": name,
                        "createAttempts": len(keys),
                        "sameIdempotencyKey": len(set(keys)) == 1,
                        "singleGraphId": facts["distinctGraphIds"] == 1,
                        "bindingRows": facts["bindingRows"],
                        "boundRows": facts["boundRows"],
                        "takeoverAttempts": claim.attempt,
                    }
                )
            finally:
                await _cleanup_build(db, build)
                await _clear_binding(db, project_id)
    return {"mode": "bind-windows", "cases": cases}


class _SearchClient:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.graph = self
        self.items = items

    def search_episodes(self, graph_uuid: str, *, query: str, page_size: int = 50):
        del graph_uuid, query, page_size
        return StubPager(self.items)


class _PageNamespace:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    def list(self, graph_uuid: str, *, page_size: int, filters: object, **kwargs: Any):
        del graph_uuid, page_size, filters, kwargs
        return StubPager(self.items)


class _ReadbackClient:
    def __init__(self, *, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]):
        self.graph = self
        self.node = _PageNamespace(nodes)
        self.edge = _PageNamespace(edges)

    def search_episodes(self, graph_uuid: str, *, query: str, page_size: int = 50):
        del graph_uuid, query, page_size
        return StubPager([])


def _episode_fixture() -> graph_store.GraphEpisode:
    content = "验收探针 episode"
    return graph_store.GraphEpisode(
        build_id=uuid.uuid4(),
        episode_key="probe-episode",
        idempotency_key=uuid.uuid4(),
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        episode_uuid=uuid.uuid4(),
        state="written",
        attempts=1,
    )


def _matched_item(episode: graph_store.GraphEpisode, *, processed: bool) -> dict[str, Any]:
    return {
        "uuid": str(episode.episode_uuid),
        "content": episode.content,
        "metadata": {
            "buildId": str(episode.build_id),
            "episodeKey": episode.episode_key,
            "contentSha256": episode.content_sha256,
        },
        "processed": processed,
    }


def _failure_code(call) -> str:
    try:
        call()
    except BusinessGraphError as exc:
        return exc.code
    return "unexpected_success"


async def _limit_codes(project_id: uuid.UUID) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    async with SessionLocal() as db:
        build = await _create_build(
            db,
            project_id=project_id,
            document_ids=[],
            question="验收探针：规模上限",
        )
        try:
            before = int(
                (
                    await db.execute(
                        text("SELECT count(*) FROM graph_snapshots WHERE build_id = :b"),
                        {"b": build.id},
                    )
                ).scalar_one()
            )
            for name, nodes, edges, evidence in (
                (
                    "graph_too_large_nodes",
                    [
                        graph_store.GraphNode(
                            key=f"n{i}", node_type="entity", label=f"n{i}", origin="material"
                        )
                        for i in range(graph_store.MAX_GRAPH_NODES + 1)
                    ],
                    [],
                    [],
                ),
                (
                    "graph_too_large_edges",
                    [
                        graph_store.GraphNode(
                            key="n1", node_type="entity", label="n1", origin="material"
                        )
                    ],
                    [
                        graph_store.GraphEdge(
                            key=f"e{i}",
                            edge_type="relation",
                            label=f"e{i}",
                            origin="material",
                            source_key="n1",
                            target_key="n1",
                        )
                        for i in range(graph_store.MAX_GRAPH_EDGES + 1)
                    ],
                    [],
                ),
                (
                    "too_many_sources",
                    [
                        graph_store.GraphNode(
                            key="n1", node_type="entity", label="n1", origin="material"
                        )
                    ],
                    [],
                    [
                        graph_store.GraphEvidence(
                            target_kind="node",
                            target_key="n1",
                            evidence_index=i + 1,
                            document_id=uuid.uuid4(),
                            chunk_index=1,
                            source_start=0,
                            source_end=1,
                            quote="p",
                        )
                        for i in range(graph_store.MAX_EVIDENCE_PER_TARGET + 1)
                    ],
                ),
            ):
                code = "unexpected_success"
                try:
                    await graph_store.create_snapshot(
                        db,
                        build_id=build.id,
                        revision=1,
                        nodes=nodes,
                        edges=edges,
                        evidence=evidence,
                        published=True,
                    )
                except Exception as exc:  # noqa: BLE001 - 只记录稳定错误码
                    code = getattr(exc, "code", type(exc).__name__)
                await db.rollback()
                results.append({"case": name, "code": code})
            after = int(
                (
                    await db.execute(
                        text("SELECT count(*) FROM graph_snapshots WHERE build_id = :b"),
                        {"b": build.id},
                    )
                ).scalar_one()
            )
            results.append(
                {"case": "no_snapshot_published", "before": before, "after": after}
            )
        finally:
            await _cleanup_build(db, build)
    return results


async def mode_failure_codes(project_id: uuid.UUID) -> dict[str, Any]:
    episode = _episode_fixture()
    cases = [
        {
            "case": "index_timeout",
            "code": _failure_code(
                lambda: confirm_episode(
                    _SearchClient([]),
                    graph_id=str(uuid.uuid4()),
                    episode=episode,
                    expected_uuid=str(episode.episode_uuid),
                    index_timeout_seconds=0.01,
                    processing_timeout_seconds=0.02,
                    poll_interval_seconds=0.001,
                )
            ),
        },
        {
            "case": "write_unconfirmed",
            "code": _failure_code(
                lambda: confirm_episode(
                    _SearchClient([]),
                    graph_id=str(uuid.uuid4()),
                    episode=episode,
                    expected_uuid=None,
                    index_timeout_seconds=0.01,
                    processing_timeout_seconds=0.02,
                    poll_interval_seconds=0.001,
                )
            ),
        },
        {
            "case": "processing_timeout",
            "code": _failure_code(
                lambda: confirm_episode(
                    _SearchClient([_matched_item(episode, processed=False)]),
                    graph_id=str(uuid.uuid4()),
                    episode=episode,
                    expected_uuid=str(episode.episode_uuid),
                    index_timeout_seconds=0.05,
                    processing_timeout_seconds=0.01,
                    poll_interval_seconds=0.001,
                )
            ),
        },
        {
            "case": "graph_incomplete",
            "code": _failure_code(
                lambda: readback_graph(
                    _ReadbackClient(
                        nodes=[
                            {
                                "uuid": str(uuid.uuid4()),
                                "name": "probe-node",
                                "episode_uuids": [str(episode.episode_uuid)],
                            }
                        ],
                        edges=[],
                    ),
                    graph_id=str(uuid.uuid4()),
                    episode_uuids=[str(episode.episode_uuid)],
                    processing_timeout_seconds=0.02,
                    poll_interval_seconds=0.001,
                )
            ),
        },
    ]
    async with SessionLocal() as db:
        limits = await _limit_codes(project_id)
    return {"mode": "failure-codes", "cases": cases, "limits": limits}


def _nodes() -> tuple[graph_store.GraphNode, graph_store.GraphEdge]:
    return (
        graph_store.GraphNode(
            key="probe-node", node_type="entity", label="probe", origin="material"
        ),
        graph_store.GraphEdge(
            key="probe-edge",
            edge_type="relation",
            label="probe",
            origin="material",
            source_key="probe-node",
            target_key="probe-node",
        ),
    )


async def _partial_snapshot(
    db: AsyncSession,
    build: graph_store.GraphBuild,
    document_id: uuid.UUID,
    *,
    published: bool,
    complete: bool,
) -> graph_store.GraphSnapshot:
    row = (
        await db.execute(
            text(
                "SELECT chunk_index, page_no FROM document_chunks "
                "WHERE document_id = :document_id ORDER BY chunk_index LIMIT 1"
            ),
            {"document_id": document_id},
        )
    ).first()
    if row is None:
        raise RuntimeError("探针材料没有分块")
    node, edge = _nodes()
    evidence = [
        graph_store.GraphEvidence(
            target_kind="node",
            target_key=node.key,
            evidence_index=1,
            document_id=document_id,
            chunk_index=int(row.chunk_index),
            page_no=None if row.page_no is None else int(row.page_no),
            source_start=0,
            source_end=1,
            quote="p",
        ),
        graph_store.GraphEvidence(
            target_kind="edge",
            target_key=edge.key,
            evidence_index=1,
            document_id=document_id,
            chunk_index=int(row.chunk_index),
            page_no=None if row.page_no is None else int(row.page_no),
            source_start=0,
            source_end=1,
            quote="p",
        ),
    ]
    snapshot = await graph_store.create_snapshot(
        db,
        build_id=build.id,
        revision=1,
        nodes=[node],
        edges=[edge],
        evidence=evidence,
        complete=complete,
        published=published,
        episode_count=1,
    )
    return snapshot


async def _prepare_ready_job(
    db: AsyncSession, build: graph_store.GraphBuild, worker_id: str
) -> worker_service.Claim:
    claim = await _claim(db, build, worker_id)
    await db.execute(
        text("UPDATE jobs SET next_step = 5 WHERE id = :job_id"),
        {"job_id": build.job_id},
    )
    await graph_store.record_episode(
        db,
        build_id=build.id,
        episode_key="probe-episode",
        content="验收探针 episode",
        content_sha256=hashlib.sha256("验收探针 episode".encode()).hexdigest(),
        state="processed",
        episode_uuid=uuid.uuid4(),
    )
    await db.commit()
    return claim


async def mode_publish_race(project_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
    config = _config()
    results: dict[str, Any] = {}
    async with SessionLocal() as db:
        # 发布先于取消
        build = await _create_build(
            db,
            project_id=project_id,
            document_ids=[document_id],
            question="验收探针：发布先于取消",
        )
        try:
            claim = await _prepare_ready_job(db, build, "graph-probe-publish")
            await _partial_snapshot(db, build, document_id, published=True, complete=False)
            await db.commit()
            outcome = await worker_service.execute_graph_publish(
                claim,
                session_factory=SessionLocal,
                stop_event=None,
                lease_seconds=30.0,
                heartbeat_seconds=1.0,
                config=config,
            )
            _, applied = await job_service.cancel_job(
                db, project_id=project_id, job_id=build.job_id
            )
            snapshot = await graph_store.get_published_snapshot(db, build_id=build.id)
            completed = int(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM run_events WHERE run_id = :run_id "
                            "AND type = 'completed'"
                        ),
                        {"run_id": build.run_id},
                    )
                ).scalar_one()
            )
            phase = (
                await db.execute(
                    text("SELECT phase FROM graph_builds WHERE id = :build_id"),
                    {"build_id": build.id},
                )
            ).scalar_one()
            results["publish_before_cancel"] = {
                "outcome": outcome,
                "cancelApplied": bool(applied),
                "phase": phase,
                "finalComplete": snapshot.complete,
                "completedEvents": completed,
            }
        finally:
            await _cleanup_build(db, build)

        # 取消先于发布：候选最终快照不可见，发布步骤不写完成事件
        build = await _create_build(
            db,
            project_id=project_id,
            document_ids=[document_id],
            question="验收探针：取消先于发布",
        )
        try:
            claim = await _prepare_ready_job(db, build, "graph-probe-cancel")
            candidate = await _partial_snapshot(
                db, build, document_id, published=False, complete=True
            )
            await db.commit()
            _, applied = await job_service.cancel_job(
                db, project_id=project_id, job_id=build.job_id
            )
            outcome = await worker_service.execute_graph_publish(
                claim,
                session_factory=SessionLocal,
                stop_event=None,
                lease_seconds=30.0,
                heartbeat_seconds=1.0,
                config=config,
            )
            candidate_visible = True
            try:
                await graph_store.get_published_snapshot(
                    db, build_id=build.id, revision=candidate.revision
                )
            except GraphSnapshotNotFound:
                candidate_visible = False
            completed = int(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM run_events WHERE run_id = :run_id "
                            "AND type = 'completed'"
                        ),
                        {"run_id": build.run_id},
                    )
                ).scalar_one()
            )
            results["cancel_before_publish"] = {
                "outcome": outcome,
                "cancelApplied": bool(applied),
                "candidateVisible": candidate_visible,
                "completedEvents": completed,
            }
        finally:
            await _cleanup_build(db, build)
    return {"mode": "publish-race", **results}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    project_id = uuid.UUID(args.project_id)
    if args.mode == "bind-windows":
        return await mode_bind_windows(project_id, uuid.UUID(args.document_id))
    if args.mode == "failure-codes":
        return await mode_failure_codes(project_id)
    if args.mode == "publish-race":
        return await mode_publish_race(project_id, uuid.UUID(args.document_id))
    raise ValueError(f"未知探针模式：{args.mode}")


async def _main_async(args: argparse.Namespace) -> dict[str, Any]:
    try:
        return await _run(args)
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="Change 07 图谱验收探针")
    parser.add_argument(
        "--mode", required=True, choices=["bind-windows", "failure-codes", "publish-race"]
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--document-id", default="")
    args = parser.parse_args()
    result = asyncio.run(_main_async(args))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
