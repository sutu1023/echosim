"""业务建图模型规划与分批抽取：只使用标准库 HTTP 传输与纯契约。"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from .business_config import BusinessGraphConfig
from .business_contracts import (
    CLAIM_NATURES,
    ChunkRef,
    EntityType,
    ExtractionBatch,
    GraphContractError,
    GraphEdge,
    GraphNode,
    GraphResult,
    ORIGIN_MATERIAL,
    ORIGINS,
    RelationType,
    SourceRef,
    Subject,
    validate_graph_result,
    validate_semantic_graph,
)
from .business_semantics import (
    consolidate,
    is_generic_relation,
    is_placeholder_entity,
    normalized_name,
    representative_sources,
    resolve_subject,
    validate_claim_nature,
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
MAX_ONTOLOGY_TYPES = 10

logger = logging.getLogger(__name__)


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
    subjects: tuple[Subject, ...] = ()
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


def _compact_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"[\s\W_]+", "", normalized).casefold()


def _locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    exact = text.find(quote)
    if exact >= 0:
        return exact, exact + len(quote)
    compact_chars: list[str] = []
    indexes: list[int] = []
    for index, char in enumerate(text):
        if re.match(r"[\w\u4e00-\u9fa5]", char):
            compact_chars.append(char.casefold())
            indexes.append(index)
    target = _compact_name(quote)
    if not target:
        return None
    compact_text = "".join(compact_chars)
    position = compact_text.find(target)
    if position < 0:
        return None
    start = indexes[position]
    end = indexes[position + len(target) - 1] + 1
    return start, end


def _normalize_source(
    source: SourceRef,
    selected_by_key: Mapping[tuple[str, int], ChunkInput],
    allowed_keys: set[tuple[str, int]],
) -> SourceRef:
    selected_documents = {key[0] for key in allowed_keys}
    if source.document_id not in selected_documents:
        return source
    current = selected_by_key.get((source.document_id, source.chunk_index))
    if current is not None:
        located = _locate_quote(current.text, source.quote)
        if located is not None:
            start, end = located
            actual = current.text[start:end]
            return replace(
                source,
                quote=actual,
                source_start=current.source_start,
                source_end=min(
                    current.source_end,
                    current.source_start + max(1, len(actual)),
                ),
            )
    candidates: list[tuple[ChunkInput, int, int]] = []
    for candidate_key in allowed_keys:
        candidate = selected_by_key[candidate_key]
        located = _locate_quote(candidate.text, source.quote)
        if located is not None:
            candidates.append((candidate, located[0], located[1]))
    if len(candidates) == 1:
        candidate, start, end = candidates[0]
        actual = candidate.text[start:end]
        return replace(
            source,
            document_id=candidate.document_id,
            chunk_index=candidate.chunk_index,
            quote=actual,
            source_start=candidate.source_start,
            source_end=min(
                candidate.source_end,
                candidate.source_start + max(1, len(actual)),
            ),
            page_no=candidate.page_no,
            paragraph_start=candidate.paragraph_start,
            paragraph_end=candidate.paragraph_end,
        )
    return source


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
        self.session_id = f"ses_{uuid.uuid4()}"

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
        entity_types, relation_types, batches, subjects = self._parse_plan(
            plan_object,
            selected_keys=selected_keys,
            selected_by_key={
                _chunk_key(chunk.document_id, chunk.chunk_index): chunk
                for chunk in selected
            },
            settings=settings,
        )
        return PlanStage(
            entity_types=entity_types,
            relation_types=relation_types,
            batches=batches,
            subjects=subjects,
            model_requests=self.model_requests - started_requests,
        )

    def plan_ontology(
        self,
        *,
        question: str,
        chunks: Sequence[ChunkInput],
        config: BusinessGraphConfig | None = None,
    ) -> PlanStage:
        """基于整份报告与问题设计实体/关系类型，不登记主体白名单。"""
        settings = config or BusinessGraphConfig()
        question_text, selected, _, _ = self._selected_context(
            question, chunks, settings
        )
        started_requests = self.model_requests
        deadline = self.clock() + self.timeout_seconds
        plan_object = self._request_json(
            self._payload(self._ontology_instruction(question_text, selected)),
            deadline,
        )
        entity_types, relation_types = self._parse_ontology(plan_object)
        return PlanStage(
            entity_types=entity_types,
            relation_types=relation_types,
            batches=(),
            subjects=(),
            model_requests=self.model_requests - started_requests,
        )

    def _ontology_instruction(
        self, question: str, chunks: Sequence[ChunkInput]
    ) -> str:
        payload = {
            "question": question,
            "documents": [
                {
                    "documentId": chunk.document_id,
                    "documentName": chunk.document_name,
                    "chunkIndex": chunk.chunk_index,
                    "pageNo": chunk.page_no,
                    "paragraphStart": chunk.paragraph_start,
                    "paragraphEnd": chunk.paragraph_end,
                    "text": chunk.text,
                }
                for chunk in chunks
            ],
        }
        schema = {
            "entityTypes": [
                {
                    "key": "university",
                    "name": "University",
                    "displayName": "高校",
                    "description": "报告中的高校主体",
                }
            ],
            "relationTypes": [
                {
                    "key": "responds_to",
                    "name": "RESPONDS_TO",
                    "displayName": "回应",
                    "sourceType": "organization",
                    "targetType": "event",
                    "description": "原文中的回应动作",
                }
            ],
        }
        return (
            "你是 EchoSim 证据图谱的本体规划器。只返回 JSON 对象，不要 Markdown 或解释。"
            "下面是整份报告的选中材料与情景问题。请为后续图谱服务从原文中抽取实体和关系设计类型。"
            "实体类型要覆盖报告中会出现的机构、部门、当事方、个人、群体、事件、决定/安排、媒体/平台等；"
            "不要使用“实体/细节/关系/关联”等占位名称。"
            "关系类型必须是原文动作词（如发布、执行、回应、报道、维权、检测、传播），"
            "不要使用“关联/相关/提及”等泛化词；给出 sourceType 与 targetType。"
            "不要列出具体主体白名单，也不要输出 batches 或 subjects；具体主体由图谱服务从原文分块抽取。"
            "实体类型与关系类型各最多 10 个，优先保留最能覆盖报告的；描述保持简短。"
            "图谱服务要求 name 使用英文：实体类型 PascalCase，关系类型 UPPER_SNAKE_CASE；"
            "displayName 使用中文名称，供界面显示；description 可用中文。"
            f"\nJSON 契约示例：{json.dumps(schema, ensure_ascii=False)}"
            f"\n输入：{json.dumps(payload, ensure_ascii=False)}"
        )

    def _parse_ontology(
        self, obj: dict[str, object]
    ) -> tuple[tuple[EntityType, ...], tuple[RelationType, ...]]:
        entity_types: list[EntityType] = []
        entity_keys: set[str] = set()
        entity_by_name: dict[str, str] = {}
        for raw in _list(obj.get("entityTypes"), "entityTypes"):
            if not isinstance(raw, dict):
                raise BusinessModelError(
                    "invalid_model_output", "entityTypes 项不是对象"
                )
            key = _string(raw.get("key"), "entityTypes.key")
            if key in entity_keys:
                raise BusinessModelError(
                    "invalid_model_output", "实体类型 key 重复", key
                )
            name = _string(raw.get("name"), "entityTypes.name", key)
            if is_placeholder_entity(name):
                raise BusinessModelError(
                    "invalid_model_output", "实体类型不能是占位名称", key
                )
            entity_keys.add(key)
            entity_by_name[normalized_name(name)] = key
            entity_by_name[normalized_name(key)] = key
            display_name = str(raw.get("displayName") or name)
            entity_types.append(
                EntityType(
                    key=key,
                    name=name,
                    description=str(raw.get("description") or ""),
                    display_name=display_name,
                )
            )
        if not entity_types:
            raise BusinessModelError("invalid_model_output", "实体类型为空")
        if len(entity_types) > MAX_ONTOLOGY_TYPES:
            entity_types = entity_types[:MAX_ONTOLOGY_TYPES]
            entity_keys = {item.key for item in entity_types}
            entity_by_name = {}
            for item in entity_types:
                entity_by_name[normalized_name(item.name)] = item.key
                entity_by_name[normalized_name(item.key)] = item.key

        relation_types: list[RelationType] = []
        relation_keys: set[str] = set()
        for raw in _list(obj.get("relationTypes"), "relationTypes"):
            if not isinstance(raw, dict):
                raise BusinessModelError(
                    "invalid_model_output", "relationTypes 项不是对象"
                )
            key = _string(raw.get("key"), "relationTypes.key")
            if key in relation_keys:
                raise BusinessModelError(
                    "invalid_model_output", "关系类型 key 重复", key
                )
            name = _string(raw.get("name"), "relationTypes.name", key)
            if is_generic_relation(name) or is_generic_relation(key):
                raise BusinessModelError(
                    "invalid_model_output", "关系类型不能是泛化词", key
                )
            source_raw = _string(
                raw.get("sourceType"), "relationTypes.sourceType", key
            )
            target_raw = _string(
                raw.get("targetType"), "relationTypes.targetType", key
            )
            source_type = (
                source_raw
                if source_raw in entity_keys
                else entity_by_name.get(normalized_name(source_raw), "")
            )
            target_type = (
                target_raw
                if target_raw in entity_keys
                else entity_by_name.get(normalized_name(target_raw), "")
            )
            if source_type not in entity_keys or target_type not in entity_keys:
                # 实体类型超过图谱上限被裁剪时，跳过端点已不存在的类型。
                continue
            relation_keys.add(key)
            relation_types.append(
                RelationType(
                    key=key,
                    name=name,
                    source_type=source_type,
                    target_type=target_type,
                    description=str(raw.get("description") or ""),
                    display_name=str(raw.get("displayName") or name),
                )
            )
        if len(relation_types) > MAX_ONTOLOGY_TYPES:
            relation_types = relation_types[:MAX_ONTOLOGY_TYPES]
        if not relation_types:
            raise BusinessModelError("invalid_model_output", "关系类型为空")
        return tuple(entity_types), tuple(relation_types)

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
        entity_types, relation_types, batches, subjects = self._parse_plan(
            payload,
            selected_keys=selected_keys,
            selected_by_key={
                _chunk_key(chunk.document_id, chunk.chunk_index): chunk
                for chunk in chunks
            },
            settings=settings,
        )
        return PlanStage(
            entity_types=entity_types,
            relation_types=relation_types,
            batches=batches,
            subjects=subjects,
            model_requests=0,
        )

    def extract_batch(
        self,
        *,
        question: str,
        entity_types: Sequence[EntityType],
        relation_types: Sequence[RelationType],
        subjects: Sequence[Subject] = (),
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
                    tuple(subjects),
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
            subjects=tuple(subjects),
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
        extracted_batches: list[ExtractedBatch] = []
        for batch in stage.batches:
            extracted = self.extract_batch(
                question=question,
                entity_types=stage.entity_types,
                relation_types=stage.relation_types,
                subjects=stage.subjects,
                batch=batch,
                chunks=chunks,
                config=settings,
            )
            extracted_batches.append(extracted)
        if stage.subjects:
            consolidated = consolidate(
                subjects=stage.subjects,
                entity_types=stage.entity_types,
                extracted_batches=extracted_batches,
                max_sources_per_item=settings.max_sources_per_item,
            )
            nodes = consolidated.nodes
            edges = consolidated.edges
        else:
            nodes = tuple(
                node for extracted in extracted_batches for node in extracted.nodes
            )
            edges = tuple(
                edge for extracted in extracted_batches for edge in extracted.edges
            )
        graph = GraphResult(
            entity_types=stage.entity_types,
            relation_types=stage.relation_types,
            batches=stage.batches,
            subjects=stage.subjects,
            nodes=nodes,
            edges=edges,
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
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": instruction}],
            "temperature": 0,
            "max_tokens": 16000,
            "response_format": {"type": "json_object"},
        }
        if (
            self.model_name == "deepseek-v4.1-flash"
            and build_chat_completions_url(self.model_api_base_url)
            == "https://opencode.ai/inference/go/openai/v1/chat/completions"
        ):
            payload["reasoning_effort"] = "none"
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.model_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if (
            build_chat_completions_url(self.model_api_base_url)
            == "https://opencode.ai/inference/go/openai/v1/chat/completions"
        ):
            headers["x-opencode-session"] = self.session_id
        return headers

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
                    if requests_used < MAX_REQUESTS_PER_STEP:
                        continue
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
                    "name": "发布决定",
                    "sourceType": "entity",
                    "targetType": "entity",
                    "description": "可选",
                }
            ],
            "subjects": [
                {
                    "key": "subject-1",
                    "label": "主体规范名称",
                    "typeKey": "entity",
                    "aliases": ["材料中出现的简称"],
                    "evidence": [
                        {
                            "documentId": "选中材料 ID",
                            "chunkIndex": 1,
                            "quote": "包含该名称的原文",
                            "sourceStart": 0,
                            "sourceEnd": 4,
                            "pageNo": 1,
                            "paragraphStart": 1,
                            "paragraphEnd": 1,
                        }
                    ],
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
            "根据下面选中的材料分块与情景问题，给出实体类型、关系类型、主体注册表与抽取批次。"
            "实体类型必须是报告中可识别的具体主体或事项，不能使用“实体/细节/关系/关联”等占位词。"
            "关系类型必须是原文动作谓词，不能使用“关联/相关/提及”等泛化词。"
            "主体注册表至少一个、最多 24 个主体；只登记与问题相关、在报告中反复出现或参与关键事件的主体。"
            "每个主体给出规范名称、类型、全称/简称别名和最多 2 条能证明名称的原文依据。"
            "同一主体在不同页使用全称和简称时，用同一个主体键登记别名，并分别给出依据。"
            "别名必须出现在材料中；没有依据的别名不要登记。描述保持简短。"
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
        subjects: tuple[Subject, ...],
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
            "subjects": [
                {
                    "key": item.key,
                    "label": item.label,
                    "typeKey": item.type_key,
                    "aliases": list(item.aliases),
                }
                for item in subjects
            ],
            "claimNatures": list(CLAIM_NATURES),
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
                    "subjectKey": "subject-1 或省略",
                    "label": "节点名称（规范名称或已声明别名）",
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
                    "claimNature": "media_report|party_claim|official_decision|other_material",
                    "label": "动作名称",
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
            "表示主体时使用 subjects 中的主体键；节点名称必须是规范名称或已声明别名。"
            "不要用分块首段文本当作主体名称。每条材料关系至少一个端点必须是主体，"
            "并给出动作名称与 claimNature。原句只写“某方称/据…报道”时用 party_claim 或 media_report，不能标 official_decision。"
            "每个关键正式动作（处分、判决、发布决定）必须摘出同时包含执行主体、动作和对象的完整原句；"
            "不要把报告作者的建议、撤销、纠正或呼吁当成已经发生的正式决定。"
            "报道关系必须有同时包含媒体、报道动作和对象的原句；只有媒体名单或机构名单时不要输出该关系。"
            "保留候选引文与定位信息，不要编造未提供的材料。"
            f"\nJSON 契约示例：{json.dumps(schema, ensure_ascii=False)}"
            f"\n输入：{json.dumps(payload, ensure_ascii=False)}"
        )

    def _parse_plan(
        self,
        obj: dict[str, object],
        *,
        selected_keys: set[tuple[str, int]],
        selected_by_key: Mapping[tuple[str, int], ChunkInput],
        settings: BusinessGraphConfig,
    ) -> tuple[
        tuple[EntityType, ...],
        tuple[RelationType, ...],
        tuple[ExtractionBatch, ...],
        tuple[Subject, ...],
    ]:
        entity_items = _list(obj.get("entityTypes"), "entityTypes")
        relation_items = _list(obj.get("relationTypes"), "relationTypes")
        batch_items = _list(obj.get("batches"), "batches")
        semantic_mode = obj.get("subjects") is not None
        entity_types: list[EntityType] = []
        entity_keys: set[str] = set()
        type_names: dict[str, str] = {}
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
            name = _string(raw.get("name"), "entityTypes.name", key)
            if semantic_mode and is_placeholder_entity(name):
                raise BusinessModelError(
                    "invalid_model_output", "实体类型不能是占位名称", key
                )
            entity_keys.add(key)
            type_names[key] = name
            entity_types.append(
                EntityType(
                    key=key,
                    name=name,
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
            name = _string(raw.get("name"), "relationTypes.name", key)
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
                    name=name,
                    source_type=source_type,
                    target_type=target_type,
                    description=str(raw.get("description") or ""),
                )
            )
        if not relation_types:
            raise BusinessModelError("invalid_model_output", "关系类型为空")

        subjects: list[Subject] = []
        subject_keys: set[str] = set()
        raw_subjects = obj.get("subjects")
        if raw_subjects is not None:
            subject_items = _list(raw_subjects, "subjects")
            if not subject_items:
                raise BusinessModelError("invalid_model_output", "主体注册表为空")
            for raw in subject_items:
                if not isinstance(raw, dict):
                    raise BusinessModelError(
                        "invalid_model_output", "subjects 项不是对象"
                    )
                key = _string(raw.get("key"), "subjects.key")
                if key in subject_keys:
                    raise BusinessModelError(
                        "invalid_model_output", "主体 key 重复", key
                    )
                label = _string(raw.get("label"), "subjects.label", key)
                type_key = _string(raw.get("typeKey"), "subjects.typeKey", key)
                if type_key not in entity_keys:
                    raise BusinessModelError(
                        "invalid_model_output", "主体类型不在实体计划中", key
                    )
                aliases_raw = raw.get("aliases") or []
                if not isinstance(aliases_raw, list):
                    raise BusinessModelError(
                        "invalid_model_output", "subjects.aliases 不是数组", key
                    )
                aliases = [str(item).strip() for item in aliases_raw if str(item).strip()]
                if len(aliases) > 4:
                    raise BusinessModelError(
                        "invalid_model_output", "主体别名超过上限", key
                    )
                evidence_raw = _list(raw.get("evidence"), "subjects.evidence")
                if not evidence_raw:
                    raise BusinessModelError(
                        "invalid_model_output", "主体缺少材料依据", key
                    )
                evidence: list[SourceRef] = []
                for item in evidence_raw:
                    source = _source(item, target=f"subject:{key}")
                    chunk_key = (source.document_id, source.chunk_index)
                    if chunk_key not in selected_keys:
                        raise BusinessModelError(
                            "invalid_graph_scope",
                            "主体依据不在本次选中材料中",
                            key,
                        )
                    chunk = selected_by_key[chunk_key]
                    # 规划依据只用于确认身份，不作为发布来源；偏移统一落在分块范围。
                    evidence.append(
                        SourceRef(
                            document_id=source.document_id,
                            chunk_index=source.chunk_index,
                            quote=source.quote,
                            source_start=chunk.source_start,
                            source_end=min(
                                chunk.source_end,
                                chunk.source_start + max(1, len(source.quote)),
                            ),
                            page_no=chunk.page_no,
                            paragraph_start=chunk.paragraph_start,
                            paragraph_end=chunk.paragraph_end,
                        )
                    )
                valid_aliases: list[str] = []
                for alias in aliases:
                    if normalized_name(alias) == normalized_name(label):
                        continue
                    if any(
                        normalized_name(alias) in normalized_name(chunk.text)
                        or _compact_name(alias) in _compact_name(chunk.text)
                        for chunk in selected_by_key.values()
                    ):
                        valid_aliases.append(alias)
                subject_keys.add(key)
                subjects.append(
                    Subject(
                        key=key,
                        label=label,
                        type_key=type_key,
                        aliases=tuple(valid_aliases),
                        sources=representative_sources(
                            evidence,
                            limit=settings.max_sources_per_item,
                        ),
                    )
                )
            if subjects:
                counts: dict[str, set[str]] = {}
                for subject in subjects:
                    for alias in subject.aliases:
                        counts.setdefault(normalized_name(alias), set()).add(
                            subject.key
                        )
                canonical_labels = {
                    normalized_name(subject.label): subject.key
                    for subject in subjects
                }
                subjects = [
                    Subject(
                        key=subject.key,
                        label=subject.label,
                        type_key=subject.type_key,
                        aliases=tuple(
                            alias
                            for alias in subject.aliases
                            if len(counts.get(normalized_name(alias), set())) == 1
                            and normalized_name(alias) not in canonical_labels
                        ),
                        sources=subject.sources,
                    )
                    for subject in subjects
                ]
                subject_type_keys = {subject.type_key for subject in subjects}
                if not any(
                    item.source_type in subject_type_keys
                    for item in relation_types
                ):
                    raise BusinessModelError(
                        "invalid_model_output",
                        "关系计划没有以主体为起点",
                    )

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
        batch_size = max(1, int(getattr(settings, "batch_size", 4)))
        normalized_batches: list[ExtractionBatch] = []
        for batch in batches:
            refs = list(batch.chunk_refs)
            if len(refs) <= batch_size:
                normalized_batches.append(batch)
                continue
            for offset in range(0, len(refs), batch_size):
                part = refs[offset : offset + batch_size]
                normalized_batches.append(
                    ExtractionBatch(
                        key=f"{batch.key}-{offset // batch_size + 1}",
                        ordinal=len(normalized_batches) + 1,
                        document_ids=tuple(
                            dict.fromkeys(ref.document_id for ref in part)
                        ),
                        chunk_refs=tuple(part),
                    )
                )
        if len(normalized_batches) > settings.max_nodes:
            raise BusinessModelError("graph_too_large", "批次数量超过节点上限")
        return (
            tuple(entity_types),
            tuple(relation_types),
            tuple(normalized_batches),
            tuple(subjects),
        )

    def _parse_extraction(
        self,
        obj: dict[str, object],
        *,
        batch: ExtractionBatch,
        entity_types: tuple[EntityType, ...],
        relation_types: tuple[RelationType, ...],
        subjects: tuple[Subject, ...] = (),
        selected_keys: set[tuple[str, int]],
        selected_by_key: Mapping[tuple[str, int], ChunkInput],
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        if obj.get("batchKey") != batch.key:
            raise BusinessModelError(
                "invalid_model_output", "抽取响应批次不匹配", batch.key
            )
        entity_keys = {item.key for item in entity_types}
        type_names = {item.key: item.name for item in entity_types}
        entity_key_by_name = {
            normalized_name(item.name): item.key for item in entity_types
        }
        entity_key_by_name.update(
            {normalized_name(item.key): item.key for item in entity_types}
        )
        relation_names = {item.key: item.name for item in relation_types}
        relation_keys = {item.key for item in relation_types}
        relation_key_by_name = {
            normalized_name(item.name): item.key for item in relation_types
        }
        relation_key_by_name.update(
            {normalized_name(item.key): item.key for item in relation_types}
        )
        nodes: list[GraphNode] = []
        seen_node_keys: set[str] = set()
        for raw in _list(obj.get("nodes"), "nodes"):
            if not isinstance(raw, dict):
                raise BusinessModelError("invalid_model_output", "节点不是对象")
            key = _string(raw.get("key"), "nodes.key")
            if key in seen_node_keys:
                raise BusinessModelError(
                    "invalid_graph_response", "节点键重复", key
                )
            seen_node_keys.add(key)
            raw_node_type = _string(raw.get("nodeType"), "nodes.nodeType", key)
            node_type = (
                raw_node_type
                if raw_node_type in entity_keys
                else entity_key_by_name.get(normalized_name(raw_node_type), "")
            )
            if node_type not in entity_keys:
                # 模型可能返回计划外的类型名称；保留原始类型用于展示，
                # 主体键存在时稍后仍会规范到主体类型。
                node_type = raw_node_type
            origin = _string(raw.get("origin"), "nodes.origin", key)
            if origin not in ORIGINS:
                raise BusinessModelError("invalid_model_output", "节点 origin 非法", key)
            label = _string(raw.get("label"), "nodes.label", key)
            subject_key_raw = raw.get("subjectKey")
            subject_key = (
                str(subject_key_raw).strip()
                if isinstance(subject_key_raw, str) and subject_key_raw.strip()
                else None
            )
            subject = None
            if subjects:
                try:
                    subject = resolve_subject(
                        subjects, label=label, subject_key=subject_key
                    )
                except GraphContractError as exc:
                    raise BusinessModelError(exc.code, exc.message, key) from exc
                if subject is not None:
                    # 主体键优先；模型给出的类型名称可能来自类型 name。
                    node_type = subject.type_key
                    subject_key = subject.key
                elif subject_key:
                    raise BusinessModelError(
                        "invalid_model_output", "节点引用未知主体", key
                    )
            else:
                subject_key = None
            raw_sources = _sources(raw.get("sources"), target=f"node:{key}")
            if origin == ORIGIN_MATERIAL and not raw_sources:
                if subjects:
                    continue
                raise BusinessModelError(
                    "source_mismatch", "材料陈述必须至少有一条来源", key
                )
            selected_documents = {item[0] for item in selected_by_key}
            sources_list: list[SourceRef] = []
            for source in raw_sources:
                if source.document_id not in selected_documents:
                    raise BusinessModelError(
                        "invalid_graph_scope", "来源不在本次选中材料中", key
                    )
                normalized = _normalize_source(
                    source, selected_by_key, set(selected_by_key)
                )
                try:
                    self._validate_source_scope(
                        (normalized,), set(selected_by_key), selected_by_key
                    )
                except BusinessModelError:
                    continue
                sources_list.append(
                    replace(
                        normalized,
                        source_end=max(
                            normalized.source_start + 1, normalized.source_end
                        ),
                    )
                )
            sources = tuple(sources_list)
            if origin == ORIGIN_MATERIAL and not sources:
                continue
            nodes.append(
                GraphNode(
                    key=key,
                    node_type=node_type,
                    type_name=type_names.get(node_type, node_type),
                    label=label,
                    origin=origin,
                    summary=str(raw.get("summary") or ""),
                    sources=sources,
                    subject_key=subject_key or "",
                    aliases=subject.aliases if subject else (),
                )
            )

        edges: list[GraphEdge] = []
        for raw in _list(obj.get("edges"), "edges"):
            if not isinstance(raw, dict):
                raise BusinessModelError("invalid_model_output", "边不是对象")
            key = _string(raw.get("key"), "edges.key")
            raw_edge_type = _string(raw.get("edgeType"), "edges.edgeType", key)
            edge_type = (
                raw_edge_type
                if raw_edge_type in relation_keys
                else relation_key_by_name.get(normalized_name(raw_edge_type), "")
            )
            if edge_type not in relation_keys:
                # 未知关系类型不发布，但不阻断其它已识别关系。
                continue
            origin = _string(raw.get("origin"), "edges.origin", key)
            if origin not in ORIGINS:
                raise BusinessModelError("invalid_model_output", "边 origin 非法", key)
            claim_nature = str(raw.get("claimNature") or "").strip()
            raw_sources = _sources(raw.get("sources"), target=f"edge:{key}")
            if origin == ORIGIN_MATERIAL and not raw_sources:
                if subjects:
                    continue
                raise BusinessModelError(
                    "source_mismatch", "材料陈述必须至少有一条来源", key
                )
            selected_documents = {item[0] for item in selected_by_key}
            sources_list: list[SourceRef] = []
            for source in raw_sources:
                if source.document_id not in selected_documents:
                    raise BusinessModelError(
                        "invalid_graph_scope", "来源不在本次选中材料中", key
                    )
                normalized = _normalize_source(
                    source, selected_by_key, set(selected_by_key)
                )
                try:
                    self._validate_source_scope(
                        (normalized,), set(selected_by_key), selected_by_key
                    )
                except BusinessModelError:
                    continue
                sources_list.append(
                    replace(
                        normalized,
                        source_end=max(
                            normalized.source_start + 1, normalized.source_end
                        ),
                    )
                )
            sources = tuple(sources_list)
            if origin == ORIGIN_MATERIAL and not sources:
                continue
            label = _string(raw.get("label"), "edges.label", key)
            if (
                subjects
                and origin == ORIGIN_MATERIAL
                and is_generic_relation(label)
            ):
                # 泛化关系不发布；保留同一批次中的其它有效动作关系。
                continue
            if subjects and origin == ORIGIN_MATERIAL:
                for source in sources:
                    try:
                        validate_claim_nature(claim_nature, source.quote)
                    except GraphContractError as exc:
                        raise BusinessModelError(
                            exc.code, exc.message, key
                        ) from exc
            edges.append(
                GraphEdge(
                    key=key,
                    edge_type=edge_type,
                    edge_type_name=relation_names.get(edge_type, edge_type),
                    label=label,
                    origin=origin,
                    source_key=_string(raw.get("sourceKey"), "edges.sourceKey", key),
                    target_key=_string(raw.get("targetKey"), "edges.targetKey", key),
                    summary=str(raw.get("summary") or ""),
                    sources=sources,
                    claim_nature=claim_nature,
                )
            )

        node_by_key = {node.key: node for node in nodes}
        for edge in edges:
            source = node_by_key.get(edge.source_key)
            target = node_by_key.get(edge.target_key)
            if source is None or target is None:
                continue
            if (
                subjects
                and edge.origin == ORIGIN_MATERIAL
                and not source.subject_key
                and not target.subject_key
            ):
                continue
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
            if normalized_name(source.quote) not in normalized_name(chunk.text):
                raise BusinessModelError(
                    "source_mismatch",
                    "候选引文不在选中分块中",
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
            # 关系类型是动作语义；语义计划下端点类型只作提示，旧计划仍保持严格校验。
            if not graph.subjects and (
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
            if graph.subjects:
                validate_semantic_graph(
                    graph,
                    max_sources_per_item=settings.max_sources_per_item,
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
        "subjects": [
            {
                "key": item.key,
                "label": item.label,
                "typeKey": item.type_key,
                "aliases": list(item.aliases),
                "evidence": [
                    _source_to_payload(source) for source in item.sources
                ],
            }
            for item in stage.subjects
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
                "typeName": node.type_name,
                "subjectKey": node.subject_key,
                "aliases": list(node.aliases),
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
                "edgeTypeName": edge.edge_type_name,
                "claimNature": edge.claim_nature,
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


def manifest_to_payload(
    nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]
) -> dict[str, object]:
    """归一后的清单：保留主体键、别名、来源性质与贡献批次。"""
    return {
        "nodes": [
            {
                "key": node.key,
                "nodeType": node.type_name or node.node_type,
                "typeKey": node.node_type,
                "subjectKey": node.subject_key,
                "aliases": list(node.aliases),
                "batchKeys": list(node.batch_keys),
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
                "edgeType": edge.edge_type_name or edge.edge_type,
                "typeKey": edge.edge_type,
                "claimNature": edge.claim_nature,
                "batchKeys": list(edge.batch_keys),
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
