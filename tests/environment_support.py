"""Change 08 真实 PostgreSQL 测试的原创图谱夹具（不读取任何私有样本）。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import evidence_graph as graph
from app.services import worker as worker_service
from tests.db_support import seed_original_material

QUESTION = "原创问题：雨天是否调整校园接驳班次？"

RICH_MATERIAL_TEXT = "\n".join(
    [
        "实体：张同学｜个人｜张同学是参与讨论的学生。",
        "实体：某校学生群体｜群体｜某校学生群体关注接驳班次。",
        "实体：某大学｜高校｜某大学位于东门附近。",
        "实体：某报社｜媒体机构｜某报社关注校园交通。",
        "实体：某争议事件｜事件｜某争议事件引发讨论。",
        "实体：某传闻｜待核实说法｜某传闻称班次将取消。",
        "实体：某社交平台｜平台｜某社交平台出现相关讨论。",
        "实体：调整接驳班次｜话题｜调整接驳班次是讨论焦点。",
        "实体：某地｜地点｜某地是事件发生地。",
        "实体：校友李某｜其他主体｜校友李某参与了讨论。",
        "关系：某报社｜报道｜某大学｜某报社报道某大学。",
        "关系：某争议事件｜发生于｜某地｜某争议事件发生于某地。",
        "关系：张同学｜主张｜调整接驳班次｜张同学主张调整接驳班次。",
        "关系：某校学生群体｜支持｜调整接驳班次｜某校学生群体支持调整接驳班次。",
        "关系：某社交平台｜传播｜某传闻｜某社交平台传播某传闻。",
        "关系：校友李某｜提及｜调整接驳班次｜校友李某被材料提及。",
    ]
)

SECOND_MATERIAL_TEXT = "\n".join(
    [
        "实体：李老师｜个人｜李老师是参与讨论的教师。",
        "实体：家长委员会｜群体｜家长委员会关注校车安排。",
        "实体：调整校车路线｜话题｜调整校车路线是讨论焦点。",
        "关系：李老师｜主张｜调整校车路线｜李老师主张调整校车路线。",
        "关系：家长委员会｜支持｜调整校车路线｜家长委员会支持调整校车路线。",
    ]
)


def utf16_len(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _positions(text_value: str, quote: str) -> tuple[int, int]:
    index = text_value.find(quote)
    if index < 0:
        raise AssertionError(f"夹具引文不在材料中：{quote}")
    start = utf16_len(text_value[:index])
    return start, start + utf16_len(quote)


@dataclass(frozen=True)
class SeededGraph:
    user_id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID
    build_id: uuid.UUID
    snapshot_id: uuid.UUID
    question: str
    text: str


NODE_SPECS = [
    ("p1", "个人", "张同学", "实体：张同学｜个人｜张同学是参与讨论的学生。"),
    ("g1", "群体", "某校学生群体", "实体：某校学生群体｜群体｜某校学生群体关注接驳班次。"),
    ("u1", "高校", "某大学", "实体：某大学｜高校｜某大学位于东门附近。"),
    ("m1", "媒体机构", "某报社", "实体：某报社｜媒体机构｜某报社关注校园交通。"),
    ("e1", "事件", "某争议事件", "实体：某争议事件｜事件｜某争议事件引发讨论。"),
    ("c1", "待核实说法", "某传闻", "实体：某传闻｜待核实说法｜某传闻称班次将取消。"),
    ("pl1", "平台", "某社交平台", "实体：某社交平台｜平台｜某社交平台出现相关讨论。"),
    ("t1", "话题", "调整接驳班次", "实体：调整接驳班次｜话题｜调整接驳班次是讨论焦点。"),
    ("l1", "地点", "某地", "实体：某地｜地点｜某地是事件发生地。"),
    ("o1", "其他主体", "校友李某", "实体：校友李某｜其他主体｜校友李某参与了讨论。"),
]

EDGE_SPECS = [
    ("r1", "报道", "某报社报道某大学。", "m1", "u1", "media_report"),
    ("r2", "发生于", "某争议事件发生于某地。", "e1", "l1", "other_material"),
    ("r3", "主张", "张同学主张调整接驳班次。", "p1", "t1", "party_claim"),
    ("r4", "支持", "某校学生群体支持调整接驳班次。", "g1", "t1", "party_claim"),
    ("r5", "传播", "某社交平台传播某传闻。", "pl1", "c1", "other_material"),
    ("r6", "提及", "校友李某被材料提及。", "o1", "t1", None),
]


def graph_objects(
    text_value: str, document_id: uuid.UUID
) -> tuple[list[graph.GraphNode], list[graph.GraphEdge], list[graph.GraphEvidence]]:
    nodes = [
        graph.GraphNode(key, node_type, label, "material", None)
        for key, node_type, label, _quote in NODE_SPECS
    ]
    edges = [
        graph.GraphEdge(
            key, edge_type, edge_type, "material", quote, source, target, claim
        )
        for key, edge_type, quote, source, target, claim in EDGE_SPECS
    ]
    evidence: list[graph.GraphEvidence] = []
    for key, _node_type, _label, quote in NODE_SPECS:
        start, end = _positions(text_value, quote)
        evidence.append(
            graph.GraphEvidence(
                target_kind="node",
                target_key=key,
                evidence_index=1,
                document_id=document_id,
                chunk_index=1,
                source_start=start,
                source_end=end,
                quote=quote,
            )
        )
    for index, (key, _edge_type, quote, _s, _t, _claim) in enumerate(EDGE_SPECS, start=1):
        start, end = _positions(text_value, quote)
        evidence.append(
            graph.GraphEvidence(
                target_kind="edge",
                target_key=key,
                evidence_index=1,
                document_id=document_id,
                chunk_index=1,
                source_start=start,
                source_end=end,
                quote=quote,
            )
        )
    return nodes, edges, evidence


async def seed_rich_graph(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    marker: str,
    question: str = QUESTION,
    material: str = RICH_MATERIAL_TEXT,
    build_can_continue: bool = True,
) -> SeededGraph:
    async with session_factory() as db:
        seed = await seed_original_material(db, marker=marker)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        await db.execute(
            text(
                "UPDATE documents SET display_name = :name, size_bytes = :size, "
                "sha256 = :sha, chunk_count = 1 WHERE id = :document_id"
            ),
            {
                "name": f"原创角色材料 {marker}.txt",
                "size": len(material.encode("utf-8")),
                "sha": digest,
                "document_id": seed.document_id,
            },
        )
        await db.execute(
            text(
                "UPDATE document_chunks SET text = :text, source_start = 0, "
                "source_end = :size, page_no = NULL, paragraph_start = 1, "
                "paragraph_end = 1 WHERE document_id = :document_id AND chunk_index = 1"
            ),
            {
                "text": material,
                "size": utf16_len(material),
                "document_id": seed.document_id,
            },
        )
        build = await graph.create_build(
            db,
            project_id=seed.project_id,
            actor_id=seed.user_id,
            question=question,
            document_ids=[seed.document_id],
            commit=False,
        )
        nodes, edges, evidence = graph_objects(material, seed.document_id)
        snapshot = await graph.create_snapshot(
            db,
            build_id=build.id,
            revision=1,
            nodes=nodes,
            edges=edges,
            evidence=evidence,
            complete=True,
            published=True,
            episode_count=1,
        )
        await db.execute(
            text(
                "UPDATE graph_builds SET phase = 'ready', can_continue = :can, "
                "planned_episodes = 1, accepted_episodes = 1, "
                "confirmed_episodes = 1, finished_at = now(), updated_at = now() "
                "WHERE id = :build_id"
            ),
            {"can": build_can_continue, "build_id": build.id},
        )
        await db.execute(
            text(
                "UPDATE jobs SET status = 'succeeded', next_step = :steps, "
                "finished_at = now(), lease_token = NULL, lease_expires_at = NULL "
                "WHERE id = :job_id"
            ),
            {"steps": 6, "job_id": build.job_id},
        )
        await db.commit()
    return SeededGraph(
        user_id=seed.user_id,
        project_id=seed.project_id,
        document_id=seed.document_id,
        build_id=build.id,
        snapshot_id=snapshot.id,
        question=question,
        text=material,
    )


async def claim_preparation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    lease_seconds: float = 60.0,
) -> worker_service.Claim:
    async with session_factory() as db:
        claim = await worker_service.claim_next(
            db,
            worker_id="environment-test-worker",
            lease_seconds=lease_seconds,
            job_id=job_id,
        )
    if claim is None:
        raise AssertionError("准备任务未被领取")
    return claim


async def scalar(session_factory: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    if isinstance(session_factory, AsyncSession):
        result = await session_factory.execute(text(sql), params or {})
        return result.scalar_one()
    async with session_factory() as db:
        result = await db.execute(text(sql), params or {})
        return result.scalar_one()
