"""业务建图的离线替身：只替换模型与 Zep 调用，不绕过输入/结果校验。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from .business_model import ChunkInput
from .business_semantics import split_sentences
from .model import HttpResponse


def _short(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:limit] or "EchoSim 原创材料"


@dataclass
class StubModelTransport:
    """确定性模型替身；调用计为 double，不会产生真实网络请求。"""

    chunks: Sequence[ChunkInput]
    question: str
    calls: int = 0
    double_calls: int = 0
    real_calls: int = 0
    _plan_emitted: bool = field(default=False, init=False)

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> HttpResponse:
        del url, headers, timeout
        self.calls += 1
        self.double_calls += 1
        content = ""
        try:
            content = str(payload["messages"][0]["content"])
        except (KeyError, IndexError, TypeError):
            content = ""
        if "本体规划器" in content:
            return HttpResponse(200, json.dumps({"choices": [{"message": {"content": json.dumps(self._ontology_plan(), ensure_ascii=False)}}]}, ensure_ascii=False))
        if "类型规划器" in content and not self._plan_emitted:
            self._plan_emitted = True
            return HttpResponse(200, json.dumps({"choices": [{"message": {"content": json.dumps(self._plan(), ensure_ascii=False)}}]}, ensure_ascii=False))
        match = re.search(r'"batchKey":\s*"([^"]+)"', content)
        batch_key = match.group(1) if match else ""
        return HttpResponse(200, json.dumps({"choices": [{"message": {"content": json.dumps(self._extract(batch_key), ensure_ascii=False)}}]}, ensure_ascii=False))

    def _batches(self) -> list[tuple[str, list[ChunkInput]]]:
        batches: list[tuple[str, list[ChunkInput]]] = []
        for index, chunk in enumerate(self.chunks, start=1):
            batches.append((f"batch-{index}", [chunk]))
        return batches

    def _ontology_plan(self) -> dict[str, Any]:
        return {
            "entityTypes": [
                {"key": "university", "name": "高校", "description": "高校主体"},
                {"key": "organization", "name": "组织机构", "description": "机构或部门"},
                {"key": "person", "name": "人物", "description": "个人当事方"},
                {"key": "group", "name": "群体", "description": "学生、家长、校友等群体"},
                {"key": "event", "name": "事件", "description": "报告中的事件"},
                {"key": "media", "name": "媒体平台", "description": "媒体或传播平台"},
                {"key": "decision", "name": "决定安排", "description": "决定、处分、判决或安排"},
            ],
            "relationTypes": [
                {"key": "responds_to", "name": "回应", "sourceType": "organization", "targetType": "event"},
                {"key": "reports_on", "name": "报道", "sourceType": "media", "targetType": "event"},
                {"key": "participates_in", "name": "参与", "sourceType": "person", "targetType": "event"},
                {"key": "involves", "name": "涉及", "sourceType": "event", "targetType": "university"},
                {"key": "decides", "name": "决定", "sourceType": "university", "targetType": "decision"},
                {"key": "supports", "name": "支持", "sourceType": "group", "targetType": "event"},
                {"key": "opposes", "name": "反对", "sourceType": "group", "targetType": "event"},
                {"key": "adjusts", "name": "调整", "sourceType": "organization", "targetType": "decision"},
            ],
        }

    def _plan(self) -> dict[str, Any]:
        subjects: list[dict[str, Any]] = []
        for chunk in self.chunks:
            token = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{chunk.document_id}:{chunk.chunk_index}",
            ).hex[:8]
            source_text = _short(chunk.text, 12)
            source_start = chunk.source_start
            source_end = min(
                chunk.source_end, source_start + max(1, len(source_text))
            )
            subjects.append(
                {
                    "key": f"subject-{token}",
                    "label": source_text,
                    "typeKey": "entity",
                    "aliases": [],
                    "evidence": [
                        {
                            "documentId": chunk.document_id,
                            "chunkIndex": chunk.chunk_index,
                            "quote": source_text,
                            "sourceStart": source_start,
                            "sourceEnd": source_end,
                            "pageNo": chunk.page_no,
                            "paragraphStart": chunk.paragraph_start,
                            "paragraphEnd": chunk.paragraph_end,
                        }
                    ],
                }
            )
        return {
            "entityTypes": [
                {"key": "entity", "name": "材料事项"},
                {"key": "detail", "name": "材料摘录"},
            ],
            "relationTypes": [
                {
                    "key": "relation",
                    "name": "摘录提及",
                    "sourceType": "entity",
                    "targetType": "detail",
                }
            ],
            "subjects": subjects,
            "batches": [
                {
                    "key": key,
                    "ordinal": index,
                    "chunks": [
                        {
                            "documentId": chunk.document_id,
                            "chunkIndex": chunk.chunk_index,
                        }
                        for chunk in chunks
                    ],
                }
                for index, (key, chunks) in enumerate(self._batches(), start=1)
            ],
        }

    def _extract(self, batch_key: str) -> dict[str, Any]:
        batch = next(
            ((key, chunks) for key, chunks in self._batches() if key == batch_key),
            ("", []),
        )
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for chunk in batch[1]:
            token = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{chunk.document_id}:{chunk.chunk_index}",
            ).hex[:8]
            source_text = _short(chunk.text, 12)
            source_start = chunk.source_start
            source_end = min(chunk.source_end, source_start + max(1, len(source_text)))
            source = {
                "documentId": chunk.document_id,
                "chunkIndex": chunk.chunk_index,
                "quote": source_text,
                "sourceStart": source_start,
                "sourceEnd": source_end,
                "pageNo": chunk.page_no,
                "paragraphStart": chunk.paragraph_start,
                "paragraphEnd": chunk.paragraph_end,
            }
            primary = f"node-{token}-primary"
            detail = f"node-{token}-detail"
            nodes.append(
                {
                    "key": primary,
                    "nodeType": "entity",
                    "subjectKey": f"subject-{token}",
                    "label": source_text,
                    "origin": "material",
                    "summary": f"原创材料主题：{source_text}",
                    "sources": [source],
                }
            )
            nodes.append(
                {
                    "key": detail,
                    "nodeType": "detail",
                    "label": _short(chunk.text, 8),
                    "origin": "material",
                    "summary": "材料中的细节",
                    "sources": [source],
                }
            )
            edges.append(
                {
                    "key": f"edge-{token}",
                    "edgeType": "relation",
                    "claimNature": "other_material",
                    "label": _short(chunk.text, 8),
                    "origin": "material",
                    "sourceKey": primary,
                    "targetKey": detail,
                    "summary": "材料陈述关系",
                    "sources": [source],
                }
            )
        first_batch = self._batches()[0][0] if self._batches() else ""
        if self.question.strip() and batch_key == first_batch:
            nodes.append(
                {
                    "key": "node-question-hypothesis",
                    "nodeType": "entity",
                    "label": _short(self.question, 10),
                    "origin": "hypothesis",
                    "summary": "来自情景问题的用户假设",
                    "sources": [],
                }
            )
        return {"batchKey": batch_key, "nodes": nodes, "edges": edges}


@dataclass
class StubPager:
    items: list[object]
    has_next: bool = False
    next_cursor: str | None = None

    def next_page(self) -> "StubPager | None":
        return None


class StubGraphClient:
    """确定性 Zep 替身；读回数据只来自实际写入的 episode 正文。"""

    def __init__(self) -> None:
        self.graphs: dict[str, str] = {}
        self.create_keys: list[str] = []
        self.episode_keys: list[str] = []
        self.episodes: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.calls = 0
        self.double_calls = 0
        self.real_calls = 0
        self.events: list[tuple[str, str]] = []
        self.search_calls = 0
        self.last_create_kwargs: dict[str, Any] | None = None
        self.last_episode_kwargs: dict[str, Any] | None = None
        self.ontology: dict[str, Any] | None = None
        self.last_node_filters: object | None = None
        self.last_edge_filters: object | None = None
        self.graph = _StubGraphNamespace(self)
        self.lose_create_response = False
        self.lose_episode_response = False
        self.fail_after_create = False

    def _count(self) -> None:
        self.calls += 1
        self.double_calls += 1

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self._count()
        self.last_create_kwargs = kwargs
        key = str(kwargs.get("idempotency_key") or "")
        self.create_keys.append(key)
        self.events.append(("create", key))
        graph_id = self.graphs.setdefault(key, str(uuid.uuid4()))
        if self.fail_after_create:
            self.fail_after_create = False
            raise TimeoutError("模拟收到 UUID 后保存前崩溃")
        if self.lose_create_response:
            self.lose_create_response = False
            raise TimeoutError("模拟图谱创建响应丢失")
        return {"uuid": graph_id, "name": kwargs.get("name")}

    def set_ontology(self, graph_uuid: str, **kwargs: Any) -> dict[str, Any]:
        self._count()
        self.events.append(("set_ontology", graph_uuid))
        self.ontology = {"graph_uuid": graph_uuid, **kwargs}
        return {"graph_uuid": graph_uuid}

    def add(
        self,
        graph_uuid: str,
        *,
        data: str,
        metadata: dict[str, Any],
        idempotency_key: str,
        type: str,
    ) -> dict[str, Any]:
        self._count()
        self.last_episode_kwargs = {
            "graph_uuid": graph_uuid,
            "data": data,
            "metadata": dict(metadata),
            "idempotency_key": idempotency_key,
            "type": type,
        }
        self.episode_keys.append(idempotency_key)
        self.events.append(("add", idempotency_key))
        existing = next(
            (
                item
                for item in self.episodes
                if item["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is None:
            episode_uuid = str(uuid.uuid4())
            existing = {
                "uuid": episode_uuid,
                "graph_uuid": graph_uuid,
                "idempotency_key": idempotency_key,
                "content": data,
                "metadata": dict(metadata),
                "processed": True,
            }
            self.episodes.append(existing)
            self._ingest_content(graph_uuid, episode_uuid, data)
        if self.lose_episode_response:
            self.lose_episode_response = False
            raise TimeoutError("模拟 episode 写入响应丢失")
        return {"episode": {"uuid": existing["uuid"], "processed": True}}

    def _ingest_content(self, graph_uuid: str, episode_uuid: str, data: str) -> None:
        try:
            parsed = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            self._ingest_text(graph_uuid, episode_uuid, data)
            return
        if not isinstance(parsed, dict):
            self._ingest_text(graph_uuid, episode_uuid, data)
            return
        raw_nodes = parsed.get("nodes")
        raw_edges = parsed.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            # 显式 JSON 但没有节点/边：保持旧行为，不从 JSON 文本猜测。
            return
        node_uuid_by_key: dict[str, str] = {}
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or "")
            label = str(raw.get("label") or "")
            if not key or not label:
                continue
            node_uuid = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{graph_uuid}:{episode_uuid}:node:{key}",
                )
            )
            node_uuid_by_key[key] = node_uuid
            self.nodes.append(
                {
                    "uuid": node_uuid,
                    "graph_uuid": graph_uuid,
                    "name": label,
                    "episode_uuids": [episode_uuid],
                }
            )
        for raw in raw_edges:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or "")
            source_key = str(raw.get("sourceKey") or "")
            target_key = str(raw.get("targetKey") or "")
            label = str(raw.get("label") or "")
            if (
                not key
                or not label
                or source_key not in node_uuid_by_key
                or target_key not in node_uuid_by_key
            ):
                continue
            self.edges.append(
                {
                    "uuid": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{graph_uuid}:{episode_uuid}:edge:{key}",
                        )
                    ),
                    "graph_uuid": graph_uuid,
                    "source_node_uuid": node_uuid_by_key[source_key],
                    "target_node_uuid": node_uuid_by_key[target_key],
                    "name": label,
                    "fact": label,
                    "episode_uuids": [episode_uuid],
                }
            )

    _STUB_ACTIONS = (
        "调整", "恢复", "延长", "发布", "决定", "处分", "判决", "回应", "报道",
        "检测", "传播", "执行", "通报", "处理", "指控", "维权", "质疑", "支持",
        "反对", "参与", "涉及", "影响", "开放", "增加", "减少",
    )

    def _node_uuid(self, graph_uuid: str, name: str) -> str:
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{graph_uuid}:node:{name}")
        )

    def _add_node(self, graph_uuid: str, episode_uuid: str, name: str) -> str:
        node_uuid = self._node_uuid(graph_uuid, name)
        existing = next(
            (item for item in self.nodes if item["uuid"] == node_uuid), None
        )
        if existing is None:
            self.nodes.append(
                {
                    "uuid": node_uuid,
                    "graph_uuid": graph_uuid,
                    "name": name,
                    "episode_uuids": [episode_uuid],
                }
            )
        elif episode_uuid not in existing["episode_uuids"]:
            existing["episode_uuids"].append(episode_uuid)
        return node_uuid

    def _ingest_text(self, graph_uuid: str, episode_uuid: str, data: str) -> None:
        """替身从纯文本产生最小可回归事实；绝不用于真实建图。"""
        text = " ".join(data.split())
        if not text:
            return
        created = False
        for _, _, sentence in split_sentences(data):
            value = sentence.strip(" \n\t，,。；;：:")
            if len(value) < 4:
                continue
            action = next((term for term in self._STUB_ACTIONS if term in value), "")
            source_text = ""
            target_text = ""
            if action:
                position = value.find(action)
                prefix = value[:position].strip(" ，,。；;：:")
                suffix = value[position + len(action) :].strip(" ，,。；;：:")
                if "把" in prefix:
                    source_text, target_text = prefix.split("把", 1)
                elif "对" in prefix:
                    source_text = prefix.split("对", 1)[0]
                    target_text = prefix.split("对", 1)[1]
                elif "在" in prefix:
                    source_text = prefix.split("在", 1)[0]
                    target_text = suffix
                else:
                    source_text, target_text = prefix, suffix
            if not source_text or not target_text:
                midpoint = max(2, len(value) // 2)
                source_text = value[:midpoint]
                target_text = value[midpoint:]
                action = action or "涉及"
            source_text = source_text.strip(" ，,。；;：:")[:24]
            target_text = target_text.strip(" ，,。；;：:")[:24]
            if not source_text or not target_text or source_text == target_text:
                continue
            source_uuid = self._add_node(graph_uuid, episode_uuid, source_text)
            target_uuid = self._add_node(graph_uuid, episode_uuid, target_text)
            fact = value
            edge_uuid = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{graph_uuid}:edge:{source_text}:{target_text}:{action}:{fact}",
                )
            )
            existing = next(
                (item for item in self.edges if item["uuid"] == edge_uuid), None
            )
            if existing is None:
                self.edges.append(
                    {
                        "uuid": edge_uuid,
                        "graph_uuid": graph_uuid,
                        "source_node_uuid": source_uuid,
                        "target_node_uuid": target_uuid,
                        "name": action,
                        "fact": fact,
                        "episode_uuids": [episode_uuid],
                    }
                )
            elif episode_uuid not in existing["episode_uuids"]:
                existing["episode_uuids"].append(episode_uuid)
            created = True
        if not created:
            name = text[:16]
            self._add_node(graph_uuid, episode_uuid, name)

    def search_episodes(
        self, graph_uuid: str, *, query: str, page_size: int = 50
    ) -> StubPager:
        del page_size
        self._count()
        self.search_calls += 1
        self.events.append(("search", query))
        items = [
            {
                "uuid": item["uuid"],
                "content": item["content"],
                "metadata": item["metadata"],
                "processed": item["processed"],
            }
            for item in self.episodes
            if item.get("graph_uuid") == graph_uuid
        ]
        return StubPager(items)

    def _filtered(
        self,
        *,
        graph_uuid: str,
        filters: object,
        source: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if isinstance(filters, dict):
            episode_uuids = set(filters.get("episode_uuids") or [])
        else:
            episode_uuids = set(getattr(filters, "episode_uuids", None) or [])
        return [
            item
            for item in source
            if item.get("graph_uuid") == graph_uuid
            and (
                not episode_uuids
                or episode_uuids.intersection(item.get("episode_uuids") or [])
            )
        ]

    def list_nodes(
        self, graph_uuid: str, *, page_size: int, filters: object
    ) -> StubPager:
        del page_size
        self._count()
        self.last_node_filters = filters
        return StubPager(
            self._filtered(graph_uuid=graph_uuid, filters=filters, source=self.nodes)
        )

    def list_edges(
        self, graph_uuid: str, *, page_size: int, filters: object
    ) -> StubPager:
        del page_size
        self._count()
        self.last_edge_filters = filters
        return StubPager(
            self._filtered(graph_uuid=graph_uuid, filters=filters, source=self.edges)
        )


class _StubEpisodeNamespace:
    def __init__(self, client: StubGraphClient) -> None:
        self._client = client

    def add(self, *args: Any, **kwargs: Any):
        return self._client.add(*args, **kwargs)

    def list(self, graph_uuid: str, *, page_size: int = 100, **kwargs: Any):
        del page_size, kwargs
        self._client._count()
        return StubPager(
            [
                {
                    "uuid": item["uuid"],
                    "content": item["content"],
                    "metadata": item["metadata"],
                    "processed": item["processed"],
                }
                for item in self._client.episodes
                if item.get("graph_uuid") == graph_uuid
            ]
        )


class _StubNodeNamespace:
    def __init__(self, client: StubGraphClient) -> None:
        self._client = client

    def list(self, graph_uuid: str, *, page_size: int, filters: object, **kwargs: Any):
        del kwargs
        return self._client.list_nodes(
            graph_uuid, page_size=page_size, filters=filters
        )


class _StubEdgeNamespace:
    def __init__(self, client: StubGraphClient) -> None:
        self._client = client

    def list(self, graph_uuid: str, *, page_size: int, filters: object, **kwargs: Any):
        del kwargs
        return self._client.list_edges(
            graph_uuid, page_size=page_size, filters=filters
        )


class _StubGraphNamespace:
    def __init__(self, client: StubGraphClient) -> None:
        self._client = client
        self.episode = _StubEpisodeNamespace(client)
        self.node = _StubNodeNamespace(client)
        self.edge = _StubEdgeNamespace(client)

    def create(self, **kwargs: Any):
        return self._client.create(**kwargs)

    def set_ontology(self, *args: Any, **kwargs: Any):
        return self._client.set_ontology(*args, **kwargs)

    def search_episodes(self, *args: Any, **kwargs: Any):
        return self._client.search_episodes(*args, **kwargs)
