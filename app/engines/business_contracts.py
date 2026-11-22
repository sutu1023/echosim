"""业务建图的纯数据契约：节点、边、来源、批次、阶段与结构校验。

本模块只使用标准库，不导入模型或 Zep SDK；数据库与外部适配器复用同一组定义。
"""

from __future__ import annotations

from dataclasses import dataclass

from .business_config import (
    DEFAULT_EVIDENCE_GRAPH_MAX_EDGES,
    DEFAULT_EVIDENCE_GRAPH_MAX_NODES,
    DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM,
)

ORIGIN_MATERIAL = "material"
ORIGIN_HYPOTHESIS = "hypothesis"
ORIGINS = (ORIGIN_MATERIAL, ORIGIN_HYPOTHESIS)

BUILD_PHASES = (
    "preparing",
    "waiting_index",
    "drawing",
    "ready",
    "failed",
    "canceled",
)
GRAPH_BUILD_STEPS = ("bind", "plan", "extract", "write", "readback", "publish")

REASON_CODES = frozenset(
    {
        "missing_config",
        "too_many_documents",
        "document_not_ready",
        "invalid_graph_scope",
        "invalid_graph_response",
        "invalid_model_output",
        "source_mismatch",
        "ambiguous_source",
        "source_unavailable",
        "model_unavailable",
        "graph_unavailable",
        "auth_failed",
        "write_unconfirmed",
        "stale_or_foreign_hit",
        "index_timeout",
        "processing_timeout",
        "graph_incomplete",
        "graph_too_large",
        "too_many_sources",
        "insufficient_graph",
    }
)


class GraphContractError(Exception):
    """建图结果不满足契约；`code` 是稳定原因标识。"""

    def __init__(self, code: str, message: str, target: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.target = target


@dataclass(frozen=True)
class EntityType:
    key: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class RelationType:
    key: str
    name: str
    source_type: str
    target_type: str
    description: str = ""


@dataclass(frozen=True)
class ChunkRef:
    document_id: str
    chunk_index: int


@dataclass(frozen=True)
class ExtractionBatch:
    key: str
    ordinal: int
    document_ids: tuple[str, ...]
    chunk_refs: tuple[ChunkRef, ...] = ()
    node_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRef:
    document_id: str
    chunk_index: int
    quote: str
    source_start: int
    source_end: int
    page_no: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None


@dataclass(frozen=True)
class GraphNode:
    key: str
    node_type: str
    label: str
    origin: str
    summary: str = ""
    sources: tuple[SourceRef, ...] = ()


@dataclass(frozen=True)
class GraphEdge:
    key: str
    edge_type: str
    label: str
    origin: str
    source_key: str
    target_key: str
    summary: str = ""
    sources: tuple[SourceRef, ...] = ()


@dataclass(frozen=True)
class GraphResult:
    entity_types: tuple[EntityType, ...] = ()
    relation_types: tuple[RelationType, ...] = ()
    batches: tuple[ExtractionBatch, ...] = ()
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()


def _non_empty(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_source(source: SourceRef, *, target: str) -> None:
    if not _non_empty(source.document_id):
        raise GraphContractError(
            "source_mismatch", "来源缺少 documentId", target
        )
    if source.chunk_index < 1:
        raise GraphContractError(
            "source_mismatch", "来源 chunkIndex 必须从 1 开始", target
        )
    if not _non_empty(source.quote):
        raise GraphContractError("source_mismatch", "来源引文为空", target)
    if source.source_start < 0 or source.source_end <= source.source_start:
        raise GraphContractError(
            "source_mismatch", "来源 UTF-16 偏移非法", target
        )
    if source.page_no is not None and source.page_no < 1:
        raise GraphContractError(
            "source_mismatch", "来源物理页码必须从 1 开始", target
        )
    if (source.paragraph_start is None) != (source.paragraph_end is None):
        raise GraphContractError(
            "source_mismatch", "来源段落区间不完整", target
        )
    if source.paragraph_start is not None:
        if (
            source.paragraph_start < 1
            or source.paragraph_end is None
            or source.paragraph_end < source.paragraph_start
        ):
            raise GraphContractError(
                "source_mismatch", "来源段落区间非法", target
            )


def _validate_sources(
    *,
    target: str,
    origin: str,
    sources: tuple[SourceRef, ...],
    max_sources: int,
) -> None:
    if len(sources) > max_sources:
        raise GraphContractError(
            "too_many_sources",
            f"单项来源超过上限 {max_sources}",
            target,
        )
    if origin == ORIGIN_MATERIAL and not sources:
        raise GraphContractError(
            "source_mismatch", "材料陈述必须至少有一条来源", target
        )
    for source in sources:
        _validate_source(source, target=target)


def _validate_type_keys(
    values: tuple[EntityType | RelationType, ...],
) -> None:
    seen: set[str] = set()
    for value in values:
        if not _non_empty(value.key) or not _non_empty(value.name):
            raise GraphContractError(
                "invalid_graph_response", "实体或关系类型缺少 key/name", value.key
            )
        if value.key in seen:
            raise GraphContractError(
                "invalid_graph_response", "实体或关系类型 key 重复", value.key
            )
        seen.add(value.key)


def validate_graph_result(
    result: GraphResult,
    *,
    max_nodes: int = DEFAULT_EVIDENCE_GRAPH_MAX_NODES,
    max_edges: int = DEFAULT_EVIDENCE_GRAPH_MAX_EDGES,
    max_sources_per_item: int = DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM,
    require_material: bool = True,
) -> None:
    """校验一份建图结果；结构、来源或规模不合法时抛出稳定错误。"""
    if not result.nodes and not result.edges:
        raise GraphContractError("insufficient_graph", "建图结果为空")
    if len(result.nodes) > max_nodes:
        raise GraphContractError(
            "graph_too_large",
            f"节点数量超过上限 {max_nodes}",
            "nodes",
        )
    if len(result.edges) > max_edges:
        raise GraphContractError(
            "graph_too_large",
            f"边数量超过上限 {max_edges}",
            "edges",
        )

    _validate_type_keys(result.entity_types)
    _validate_type_keys(result.relation_types)
    batch_keys: set[str] = set()
    for batch in result.batches:
        if not _non_empty(batch.key) or batch.ordinal < 1:
            raise GraphContractError(
                "invalid_graph_response", "抽取批次缺少 key 或 ordinal", batch.key
            )
        if batch.key in batch_keys:
            raise GraphContractError(
                "invalid_graph_response", "抽取批次 key 重复", batch.key
            )
        if not batch.document_ids:
            raise GraphContractError(
                "invalid_graph_response", "抽取批次缺少材料", batch.key
            )
        for chunk in batch.chunk_refs:
            if not _non_empty(chunk.document_id) or chunk.chunk_index < 1:
                raise GraphContractError(
                    "invalid_graph_response", "抽取批次分块非法", batch.key
                )
        if batch.chunk_refs and any(
            chunk.document_id not in batch.document_ids
            for chunk in batch.chunk_refs
        ):
            raise GraphContractError(
                "invalid_graph_response", "抽取批次分块不属于批次材料", batch.key
            )
        batch_keys.add(batch.key)

    node_keys: set[str] = set()
    for node in result.nodes:
        if not _non_empty(node.key) or not _non_empty(node.label):
            raise GraphContractError(
                "invalid_graph_response", "节点缺少 key/label", node.key
            )
        if not _non_empty(node.node_type):
            raise GraphContractError(
                "invalid_graph_response", "节点缺少类型", node.key
            )
        if node.origin not in ORIGINS:
            raise GraphContractError(
                "invalid_graph_response", "节点 origin 非法", node.key
            )
        if node.key in node_keys:
            raise GraphContractError(
                "invalid_graph_response", "节点 key 重复", node.key
            )
        node_keys.add(node.key)
        _validate_sources(
            target=f"node:{node.key}",
            origin=node.origin,
            sources=node.sources,
            max_sources=max_sources_per_item,
        )

    edge_keys: set[str] = set()
    material_edges = 0
    for edge in result.edges:
        if not _non_empty(edge.key) or not _non_empty(edge.label):
            raise GraphContractError(
                "invalid_graph_response", "边缺少 key/label", edge.key
            )
        if not _non_empty(edge.edge_type):
            raise GraphContractError(
                "invalid_graph_response", "边缺少类型", edge.key
            )
        if edge.origin not in ORIGINS:
            raise GraphContractError(
                "invalid_graph_response", "边 origin 非法", edge.key
            )
        if edge.key in edge_keys:
            raise GraphContractError(
                "invalid_graph_response", "边 key 重复", edge.key
            )
        edge_keys.add(edge.key)
        if edge.source_key not in node_keys or edge.target_key not in node_keys:
            raise GraphContractError(
                "invalid_graph_response", "边端点不存在", edge.key
            )
        if edge.origin == ORIGIN_MATERIAL:
            material_edges += 1
        _validate_sources(
            target=f"edge:{edge.key}",
            origin=edge.origin,
            sources=edge.sources,
            max_sources=max_sources_per_item,
        )

    if require_material:
        material_nodes = sum(
            1 for node in result.nodes if node.origin == ORIGIN_MATERIAL
        )
        if material_nodes == 0 or material_edges == 0:
            raise GraphContractError(
                "insufficient_graph", "缺少材料节点或材料关系"
            )
