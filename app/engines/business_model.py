"""业务建图模型规划与分批抽取：只使用标准库 HTTP 传输与纯契约。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .business_config import BusinessGraphConfig
from .business_contracts import (
    ChunkRef,
    EntityType,
    ExtractionBatch,
    GraphContractError,
    GraphEdge,
    GraphNode,
    GraphResult,
    ORIGINS,
    RelationType,
    SourceRef,
    validate_graph_result,
)
from .model import (
    Transport,
    USER_AGENT,
    _default_transport,
    _extract_content,
    _unsupported_response_format,
    build_chat_completions_url,
)

RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_REQUESTS_PER_STEP = 2


class BusinessModelError(Exception):
    """模型规划或抽取失败；`code` 与业务原因码一致。"""

    def __init__(self, code: str, message: str, target: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.target = target


@dataclass(frozen=True)
class ChunkInput:
    document_id: str
    document_name: str
    chunk_index: int
    text: str
    source_start: int
    source_end: int
    page_no: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None


@dataclass(frozen=True)
class PlanStage:
    entity_types: tuple[EntityType, ...]
    relation_types: tuple[RelationType, ...]
    batches: tuple[ExtractionBatch, ...]
    model_requests: int = 0


@dataclass(frozen=True)
class ExtractedBatch:
    batch: ExtractionBatch
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    model_requests: int = 0


@dataclass(frozen=True)
class BusinessBuildPlan:
    entity_types: tuple[EntityType, ...]
    relation_types: tuple[RelationType, ...]
    batches: tuple[ExtractionBatch, ...]
    graph: GraphResult
    model_requests: int = 0


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string(value: object, name: str, target: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessModelError(
            "invalid_model_output", f"模型字段 {name} 不是非空字符串", target
        )
    return value


def _int(value: object, name: str, *, minimum: int = 0) -> int:
    if not _is_int(value) or int(value) < minimum:
        raise BusinessModelError(
            "invalid_model_output", f"模型字段 {name} 不是合法整数"
        )
    return int(value)


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise BusinessModelError("invalid_model_output", f"模型字段 {name} 不是数组")
    return value


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _int(value, name, minimum=1)


def _source(item: object, *, target: str) -> SourceRef:
    if not isinstance(item, dict):
        raise BusinessModelError("invalid_model_output", "来源不是对象", target)
    document_id = _string(item.get("documentId"), "documentId", target)
    chunk_index = _int(item.get("chunkIndex"), "chunkIndex", minimum=1)
    quote = _string(item.get("quote"), "quote", target)
    source_start = _int(item.get("sourceStart"), "sourceStart", minimum=0)
    source_end = _int(item.get("sourceEnd"), "sourceEnd", minimum=0)
    page_no = _optional_int(item.get("pageNo"), "pageNo")
    paragraph_start = _optional_int(item.get("paragraphStart"), "paragraphStart")
    paragraph_end = _optional_int(item.get("paragraphEnd"), "paragraphEnd")
    return SourceRef(
        document_id=document_id,
        chunk_index=chunk_index,
        quote=quote,
        source_start=source_start,
        source_end=source_end,
        page_no=page_no,
        paragraph_start=paragraph_start,
        paragraph_end=paragraph_end,
    )


def _sources(item: object, *, target: str) -> tuple[SourceRef, ...]:
    if item is None:
        return ()
    return tuple(_source(raw, target=target) for raw in _list(item, "sources"))


def _chunk_key(document_id: str, chunk_index: int) -> tuple[str, int]:
    return (document_id, chunk_index)


class BusinessModelPlanner:
    """先规划类型与批次，再按批次抽取节点、关系与候选来源。"""

    def __init__(
        self,
        *,
        model_api_base_url: str,
        model_api_key: str,
        model_name: str,
        timeout_seconds: float,
        transport: Transport | None = None,
        call_kind: str = "real",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model_api_base_url = model_api_base_url
        self.model_api_key = model_api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _default_transport
        self.call_kind = "double" if call_kind == "double" else "real"
        self.clock = clock
        self.sleep = sleep
        self.model_requests = 0

    def _selected_context(
        self,
        question: str,
        chunks: Sequence[ChunkInput],
        settings: BusinessGraphConfig,
    ) -> tuple[
        str,
        list[ChunkInput],
        set[tuple[str, int]],
        set[str],
    ]:
        question_text = question.strip()
        if not question_text:
            raise BusinessModelError("invalid_graph_scope", "情景问题不能为空")
        selected = list(chunks)
        selected_keys = {
            _chunk_key(chunk.document_id, chunk.chunk_index) for chunk in selected
        }
        if len(selected_keys) != len(selected):
            raise BusinessModelError("invalid_graph_scope", "选中分块重复")
        document_ids = {chunk.document_id for chunk in selected}
        if len(document_ids) > settings.max_documents:
            raise BusinessModelError(
                "too_many_documents",
                f"单次最多 {settings.max_documents} 份材料",
            )
        return question_text, selected, selected_keys, document_ids

    def plan(
        self,
        *,
        question: str,
        chunks: Sequence[ChunkInput],
        config: BusinessGraphConfig | None = None,
    ) -> PlanStage:
        settings = config or BusinessGraphConfig()
        question_text, selected, selected_keys, _ = self._selected_context(
            question, chunks, settings
        )
        started_requests = self.model_requests
        deadline = self.clock() + self.timeout_seconds
        plan_object = self._request_json(
            self._payload(self._plan_instruction(question_text, selected)),
            deadline,
        )
        entity_types, relation_types, batches = self._parse_plan(
            plan_object, selected_keys=selected_keys, settings=settings
        )
        return PlanStage(
            entity_types=entity_types,
            relation_types=relation_types,
            batches=batches,
            model_requests=self.model_requests - started_requests,
        )

    def restore_plan(
        self,
        payload: dict[str, object],
        *,
        chunks: Sequence[ChunkInput],
        config: BusinessGraphConfig | None = None,
    ) -> PlanStage:
        settings = config or BusinessGraphConfig()
        selected_keys = {
            _chunk_key(chunk.document_id, chunk.chunk_index) for chunk in chunks
        }
        entity_types, relation_types, batches = self._parse_plan(
            payload, selected_keys=selected_keys, settings=settings
        )
        return PlanStage(entity_types, relation_types, batches, 0)

    def extract_batch(
        self,
        *,
        question: str,
        entity_types: Sequence[EntityType],
        relation_types: Sequence[RelationType],
        batch: ExtractionBatch,
        chunks: Sequence[ChunkInput],
        config: BusinessGraphConfig | None = None,
    ) -> ExtractedBatch:
        settings = config or BusinessGraphConfig()
        question_text, selected, selected_keys, _ = self._selected_context(
            question, chunks, settings
        )
        selected_by_key = {
            _chunk_key(chunk.document_id, chunk.chunk_index): chunk
            for chunk in selected
        }
        batch_keys: set[tuple[str, int]] = set()
        for ref in batch.chunk_refs:
            key = _chunk_key(ref.document_id, ref.chunk_index)
            if key not in selected_keys:
                raise BusinessModelError(
                    "invalid_graph_scope", "批次包含未选材料"
                )
            batch_keys.add(key)
        batch_chunks = [
            selected_by_key[_chunk_key(ref.document_id, ref.chunk_index)]
            for ref in batch.chunk_refs
        ]
        started_requests = self.model_requests
        deadline = self.clock() + self.timeout_seconds
        extract_object = self._request_json(
            self._payload(
                self._extract_instruction(
                    question_text,
                    tuple(entity_types),
                    tuple(relation_types),
                    batch,
                    batch_chunks,
                )
            ),
            deadline,
        )
        nodes, edges = self._parse_extraction(
            extract_object,
            batch=batch,
            entity_types=tuple(entity_types),
            relation_types=tuple(relation_types),
            selected_keys=batch_keys,
            selected_by_key=selected_by_key,
        )
        return ExtractedBatch(
            batch=batch,
            nodes=tuple(nodes),
            edges=tuple(edges),
            model_requests=self.model_requests - started_requests,
        )

    def plan_and_extract(
        self,
        *,
        question: str,
        chunks: Sequence[ChunkInput],
        config: BusinessGraphConfig | None = None,
    ) -> BusinessBuildPlan:
        settings = config or BusinessGraphConfig()
        stage = self.plan(question=question, chunks=chunks, config=settings)
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        for batch in stage.batches:
            extracted = self.extract_batch(
                question=question,
                entity_types=stage.entity_types,
                relation_types=stage.relation_types,
                batch=batch,
                chunks=chunks,
                config=settings,
            )
            nodes.extend(extracted.nodes)
            edges.extend(extracted.edges)
        graph = GraphResult(
            entity_types=stage.entity_types,
            relation_types=stage.relation_types,
            batches=stage.batches,
            nodes=tuple(nodes),
            edges=tuple(edges),
        )
        self._validate_final_graph(graph, settings)
        return BusinessBuildPlan(
            entity_types=stage.entity_types,
            relation_types=stage.relation_types,
            batches=stage.batches,
            graph=graph,
            model_requests=self.model_requests,
        )

    def _payload(self, instruction: str) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "messages": [{"role": "user", "content": instruction}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.model_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _request_json(
        self, payload: dict[str, Any], deadline: float
    ) -> dict[str, object]:
        try:
            url = build_chat_completions_url(self.model_api_base_url)
        except ValueError as exc:
            raise BusinessModelError("missing_config", str(exc)) from exc
        include_response_format = True
        requests_used = 0
        last_status = 0
        last_body = ""
        while requests_used < MAX_REQUESTS_PER_STEP:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise BusinessModelError("model_unavailable", "模型调用超时")
            payload_to_send = dict(payload)
            if not include_response_format:
                payload_to_send.pop("response_format", None)
            response = self.transport(
                url, self._headers(), payload_to_send, max(0.1, remaining)
            )
            requests_used += 1
            self.model_requests += 1
            last_status = response.status
            last_body = response.body
            if response.status == 200:
                try:
                    content = _extract_content(response.body)
                except ValueError as exc:
                    raise BusinessModelError("invalid_model_output", str(exc)) from exc
                if not isinstance(content, dict):
                    raise BusinessModelError(
                        "invalid_model_output", "模型内容不是 JSON 对象"
                    )
                return content
            if (
                response.status in {400, 422}
                and include_response_format
                and _unsupported_response_format(response)
            ):
                include_response_format = False
                continue
            if response.status in RETRYABLE_STATUSES or response.status <= 0:
                if requests_used < MAX_REQUESTS_PER_STEP:
                    self.sleep(min(1.0, max(0.0, deadline - self.clock())))
                    continue
            break
        if last_status in {401, 403}:
            raise BusinessModelError("auth_failed", "模型认证失败")
        if last_status >= 500 or last_status <= 0:
            raise BusinessModelError("model_unavailable", "模型暂时不可用")
        raise BusinessModelError(
            "invalid_model_output", f"模型返回 HTTP {last_status}"
        )

    def _plan_instruction(
        self, question: str, chunks: Sequence[ChunkInput]
    ) -> str:
        payload = {
            "question": question,
            "chunks": [
                {
                    "documentId": chunk.document_id,
                    "documentName": chunk.document_name,
                    "chunkIndex": chunk.chunk_index,
                    "text": chunk.text,
                    "sourceStart": chunk.source_start,
                    "sourceEnd": chunk.source_end,
                    "pageNo": chunk.page_no,
                    "paragraphStart": chunk.paragraph_start,
                    "paragraphEnd": chunk.paragraph_end,
                }
                for chunk in chunks
            ],
        }
        schema = {
            "entityTypes": [
                {"key": "entity", "name": "实体", "description": "可选"}
            ],
            "relationTypes": [
                {
                    "key": "relation",
                    "name": "关系",
                    "sourceType": "entity",
                    "targetType": "entity",
                    "description": "可选",
                }
            ],
            "batches": [
                {
                    "key": "batch-1",
                    "ordinal": 1,
                    "chunks": [
                        {"documentId": "选中材料 ID", "chunkIndex": 1}
                    ],
                }
            ],
        }
        return (
            "你是 EchoSim 业务证据图谱的类型规划器。只返回 JSON 对象，不要 Markdown 或解释。"
            "根据下面选中的材料分块与情景问题，给出实体类型、关系类型与抽取批次。"
            "批次只能包含下面列出的选中分块，并且每个选中分块恰好出现一次。"
            "问题中的设想属于用户假设，材料正文属于材料陈述。"
            f"\nJSON 契约示例：{json.dumps(schema, ensure_ascii=False)}"
            f"\n输入：{json.dumps(payload, ensure_ascii=False)}"
        )

    def _extract_instruction(
        self,
        question: str,
        entity_types: tuple[EntityType, ...],
        relation_types: tuple[RelationType, ...],
        batch: ExtractionBatch,
        chunks: Sequence[ChunkInput],
    ) -> str:
        payload = {
            "question": question,
            "batchKey": batch.key,
            "entityTypes": [
                {"key": item.key, "name": item.name} for item in entity_types
            ],
            "relationTypes": [
                {
                    "key": item.key,
                    "name": item.name,
                    "sourceType": item.source_type,
                    "targetType": item.target_type,
                }
                for item in relation_types
            ],
            "chunks": [
                {
                    "documentId": chunk.document_id,
                    "documentName": chunk.document_name,
                    "chunkIndex": chunk.chunk_index,
                    "text": chunk.text,
                    "sourceStart": chunk.source_start,
                    "sourceEnd": chunk.source_end,
                    "pageNo": chunk.page_no,
                    "paragraphStart": chunk.paragraph_start,
                    "paragraphEnd": chunk.paragraph_end,
                }
                for chunk in chunks
            ],
        }
        schema = {
            "batchKey": batch.key,
            "nodes": [
                {
                    "key": "node-1",
                    "nodeType": "entity",
                    "label": "节点名称",
                    "origin": "material|hypothesis",
                    "summary": "摘要",
                    "sources": [
                        {
                            "documentId": "选中材料 ID",
                            "chunkIndex": 1,
                            "quote": "原文候选引文",
                            "sourceStart": 0,
                            "sourceEnd": 4,
                            "pageNo": 1,
                            "paragraphStart": 1,
                            "paragraphEnd": 1,
                        }
                    ],
                }
            ],
            "edges": [
                {
                    "key": "edge-1",
                    "edgeType": "relation",
                    "label": "关系名称",
                    "origin": "material|hypothesis",
                    "sourceKey": "node-1",
                    "targetKey": "node-2",
                    "summary": "摘要",
                    "sources": [],
                }
            ],
        }
        return (
            "你是 EchoSim 业务证据图谱的抽取器。只返回 JSON 对象，不要 Markdown 或解释。"
            "只使用下面批次中的材料分块；材料陈述必须有至少一条来源，"
            "问题中的用户假设可以没有来源。节点和关系类型必须来自给定类型计划。"
            "保留候选引文与定位信息，不要编造未提供的材料。"
            f"\nJSON 契约示例：{json.dumps(schema, ensure_ascii=False)}"
            f"\n输入：{json.dumps(payload, ensure_ascii=False)}"
        )

    def _parse_plan(
        self,
        obj: dict[str, object],
        *,
        selected_keys: set[tuple[str, int]],
        settings: BusinessGraphConfig,
    ) -> tuple[
        tuple[EntityType, ...], tuple[RelationType, ...], tuple[ExtractionBatch, ...]
    ]:
        entity_items = _list(obj.get("entityTypes"), "entityTypes")
        relation_items = _list(obj.get("relationTypes"), "relationTypes")
        batch_items = _list(obj.get("batches"), "batches")
        entity_types: list[EntityType] = []
        entity_keys: set[str] = set()
        for raw in entity_items:
            if not isinstance(raw, dict):
                raise BusinessModelError(
                    "invalid_model_output", "entityTypes 项不是对象"
                )
            key = _string(raw.get("key"), "entityTypes.key")
            if key in entity_keys:
                raise BusinessModelError(
                    "invalid_model_output", "实体类型 key 重复", key
                )
            entity_keys.add(key)
            entity_types.append(
                EntityType(
                    key=key,
                    name=_string(raw.get("name"), "entityTypes.name", key),
                    description=str(raw.get("description") or ""),
                )
            )
        if not entity_types:
            raise BusinessModelError("invalid_model_output", "实体类型为空")

        relation_types: list[RelationType] = []
        relation_keys: set[str] = set()
        for raw in relation_items:
            if not isinstance(raw, dict):
                raise BusinessModelError(
                    "invalid_model_output", "relationTypes 项不是对象"
                )
            key = _string(raw.get("key"), "relationTypes.key")
            if key in relation_keys:
                raise BusinessModelError(
                    "invalid_model_output", "关系类型 key 重复", key
                )
            relation_keys.add(key)
            source_type = _string(
                raw.get("sourceType"), "relationTypes.sourceType", key
            )
            target_type = _string(
                raw.get("targetType"), "relationTypes.targetType", key
            )
            if source_type not in entity_keys or target_type not in entity_keys:
                raise BusinessModelError(
                    "invalid_model_output", "关系类型端点不在实体计划中", key
                )
            relation_types.append(
                RelationType(
                    key=key,
                    name=_string(raw.get("name"), "relationTypes.name", key),
                    source_type=source_type,
                    target_type=target_type,
                    description=str(raw.get("description") or ""),
                )
            )
        if not relation_types:
            raise BusinessModelError("invalid_model_output", "关系类型为空")

        batches: list[ExtractionBatch] = []
        batch_keys: set[str] = set()
        seen_chunks: set[tuple[str, int]] = set()
        for raw in batch_items:
            if not isinstance(raw, dict):
                raise BusinessModelError("invalid_model_output", "批次不是对象")
            key = _string(raw.get("key"), "batches.key")
            if key in batch_keys:
                raise BusinessModelError("invalid_model_output", "批次 key 重复", key)
            batch_keys.add(key)
            ordinal = _int(raw.get("ordinal"), "batches.ordinal", minimum=1)
            chunk_refs: list[ChunkRef] = []
            for chunk_raw in _list(raw.get("chunks"), "batches.chunks"):
                if not isinstance(chunk_raw, dict):
                    raise BusinessModelError(
                        "invalid_model_output", "批次分块不是对象", key
                    )
                document_id = _string(
                    chunk_raw.get("documentId"), "batches.chunks.documentId", key
                )
                chunk_index = _int(
                    chunk_raw.get("chunkIndex"),
                    "batches.chunks.chunkIndex",
                    minimum=1,
                )
                chunk_key = (document_id, chunk_index)
                if chunk_key not in selected_keys:
                    raise BusinessModelError(
                        "invalid_graph_scope", "批次包含未选材料", key
                    )
                if chunk_key in seen_chunks:
                    raise BusinessModelError(
                        "invalid_model_output", "批次重复覆盖同一分块", key
                    )
                seen_chunks.add(chunk_key)
                chunk_refs.append(
                    ChunkRef(document_id=document_id, chunk_index=chunk_index)
                )
            if not chunk_refs:
                raise BusinessModelError(
                    "invalid_model_output", "批次没有分块", key
                )
            batches.append(
                ExtractionBatch(
                    key=key,
                    ordinal=ordinal,
                    document_ids=tuple(
                        dict.fromkeys(ref.document_id for ref in chunk_refs)
                    ),
                    chunk_refs=tuple(chunk_refs),
                )
            )
        if not batches:
            raise BusinessModelError("invalid_model_output", "批次为空")
        if seen_chunks != selected_keys:
            raise BusinessModelError(
                "invalid_model_output", "批次未完整覆盖选中分块"
            )
        if len(batches) > settings.max_nodes:  # 防止异常批次数量拖垮预算
            raise BusinessModelError("graph_too_large", "批次数量超过节点上限")
        return tuple(entity_types), tuple(relation_types), tuple(batches)

    def _parse_extraction(
        self,
        obj: dict[str, object],
        *,
        batch: ExtractionBatch,
        entity_types: tuple[EntityType, ...],
        relation_types: tuple[RelationType, ...],
        selected_keys: set[tuple[str, int]],
        selected_by_key: Mapping[tuple[str, int], ChunkInput],
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        if obj.get("batchKey") != batch.key:
            raise BusinessModelError(
                "invalid_model_output", "抽取响应批次不匹配", batch.key
            )
        entity_keys = {item.key for item in entity_types}
        relation_keys = {item.key for item in relation_types}
        nodes: list[GraphNode] = []
        for raw in _list(obj.get("nodes"), "nodes"):
            if not isinstance(raw, dict):
                raise BusinessModelError("invalid_model_output", "节点不是对象")
            key = _string(raw.get("key"), "nodes.key")
            node_type = _string(raw.get("nodeType"), "nodes.nodeType", key)
            if node_type not in entity_keys:
                raise BusinessModelError(
                    "invalid_model_output", "节点类型不在类型计划中", key
                )
            origin = _string(raw.get("origin"), "nodes.origin", key)
            if origin not in ORIGINS:
                raise BusinessModelError("invalid_model_output", "节点 origin 非法", key)
            sources = _sources(raw.get("sources"), target=f"node:{key}")
            self._validate_source_scope(sources, selected_keys, selected_by_key)
            nodes.append(
                GraphNode(
                    key=key,
                    node_type=node_type,
                    label=_string(raw.get("label"), "nodes.label", key),
                    origin=origin,
                    summary=str(raw.get("summary") or ""),
                    sources=sources,
                )
            )

        edges: list[GraphEdge] = []
        for raw in _list(obj.get("edges"), "edges"):
            if not isinstance(raw, dict):
                raise BusinessModelError("invalid_model_output", "边不是对象")
            key = _string(raw.get("key"), "edges.key")
            edge_type = _string(raw.get("edgeType"), "edges.edgeType", key)
            if edge_type not in relation_keys:
                raise BusinessModelError(
                    "invalid_model_output", "关系类型不在类型计划中", key
                )
            origin = _string(raw.get("origin"), "edges.origin", key)
            if origin not in ORIGINS:
                raise BusinessModelError("invalid_model_output", "边 origin 非法", key)
            sources = _sources(raw.get("sources"), target=f"edge:{key}")
            self._validate_source_scope(sources, selected_keys, selected_by_key)
            edges.append(
                GraphEdge(
                    key=key,
                    edge_type=edge_type,
                    label=_string(raw.get("label"), "edges.label", key),
                    origin=origin,
                    source_key=_string(raw.get("sourceKey"), "edges.sourceKey", key),
                    target_key=_string(raw.get("targetKey"), "edges.targetKey", key),
                    summary=str(raw.get("summary") or ""),
                    sources=sources,
                )
            )
        return nodes, edges

    def _validate_source_scope(
        self,
        sources: tuple[SourceRef, ...],
        selected_keys: set[tuple[str, int]],
        selected_by_key: Mapping[tuple[str, int], ChunkInput],
    ) -> None:
        for source in sources:
            key = (source.document_id, source.chunk_index)
            if key not in selected_keys:
                raise BusinessModelError(
                    "invalid_graph_scope", "来源不在本次选中材料中"
                )
            chunk = selected_by_key[key]
            if (
                source.source_start < chunk.source_start
                or source.source_end > chunk.source_end
                or source.source_end <= source.source_start
            ):
                raise BusinessModelError(
                    "source_mismatch",
                    "候选引文偏移超出选中分块范围",
                    source.document_id,
                )

    def validate_result(
        self, graph: GraphResult, config: BusinessGraphConfig | None = None
    ) -> None:
        """公开的最终图校验入口，供 plan/extract 服务在分步执行时复用。"""
        self._validate_final_graph(graph, config or BusinessGraphConfig())

    def _validate_final_graph(
        self, graph: GraphResult, settings: BusinessGraphConfig
    ) -> None:
        node_types = {node.key: node.node_type for node in graph.nodes}
        relation_types = {item.key: item for item in graph.relation_types}
        for edge in graph.edges:
            if edge.source_key not in node_types or edge.target_key not in node_types:
                raise BusinessModelError(
                    "invalid_graph_response", "边端点不存在", edge.key
                )
            relation = relation_types.get(edge.edge_type)
            if relation is None:
                raise BusinessModelError(
                    "invalid_model_output", "边类型不在关系计划中", edge.key
                )
            if (
                node_types[edge.source_key] != relation.source_type
                or node_types[edge.target_key] != relation.target_type
            ):
                raise BusinessModelError(
                    "invalid_model_output", "边两端类型不符合关系计划", edge.key
                )
        try:
            validate_graph_result(
                graph,
                max_nodes=settings.max_nodes,
                max_edges=settings.max_edges,
                max_sources_per_item=settings.max_sources_per_item,
                require_material=True,
            )
        except GraphContractError as exc:
            raise BusinessModelError(exc.code, exc.message, exc.target) from exc


def plan_to_payload(stage: PlanStage) -> dict[str, object]:
    return {
        "entityTypes": [
            {
                "key": item.key,
                "name": item.name,
                "description": item.description,
            }
            for item in stage.entity_types
        ],
        "relationTypes": [
            {
                "key": item.key,
                "name": item.name,
                "sourceType": item.source_type,
                "targetType": item.target_type,
                "description": item.description,
            }
            for item in stage.relation_types
        ],
        "batches": [
            {
                "key": batch.key,
                "ordinal": batch.ordinal,
                "chunks": [
                    {
                        "documentId": ref.document_id,
                        "chunkIndex": ref.chunk_index,
                    }
                    for ref in batch.chunk_refs
                ],
            }
            for batch in stage.batches
        ],
    }


def _source_to_payload(source: SourceRef) -> dict[str, object]:
    return {
        "documentId": source.document_id,
        "chunkIndex": source.chunk_index,
        "quote": source.quote,
        "sourceStart": source.source_start,
        "sourceEnd": source.source_end,
        "pageNo": source.page_no,
        "paragraphStart": source.paragraph_start,
        "paragraphEnd": source.paragraph_end,
    }


def extraction_to_payload(
    nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]
) -> dict[str, object]:
    return {
        "nodes": [
            {
                "key": node.key,
                "nodeType": node.node_type,
                "label": node.label,
                "origin": node.origin,
                "summary": node.summary,
                "sources": [_source_to_payload(source) for source in node.sources],
            }
            for node in nodes
        ],
        "edges": [
            {
                "key": edge.key,
                "edgeType": edge.edge_type,
                "label": edge.label,
                "origin": edge.origin,
                "sourceKey": edge.source_key,
                "targetKey": edge.target_key,
                "summary": edge.summary,
                "sources": [_source_to_payload(source) for source in edge.sources],
            }
            for edge in edges
        ],
    }


def extractions_to_payload(
    extractions: Sequence[ExtractedBatch],
) -> dict[str, object]:
    return {
        "batches": [
            {
                "batchKey": item.batch.key,
                **extraction_to_payload(item.nodes, item.edges),
            }
            for item in extractions
        ]
    }
