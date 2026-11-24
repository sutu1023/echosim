"""业务建图图谱适配器：项目绑定、episode 写入确认与节点/边读回。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ..services import evidence_graph as store
from .business_config import BusinessGraphConfig, graph_id_fingerprint
from .business_contracts import GraphEdge as ContractEdge, SourceRef
from .graph import (
    _classify_exception,
    _episode_content,
    _episode_metadata,
    _episode_uuid,
    _is_auth_error,
    _result_episode_uuid,
)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
ClientFactory = Callable[[], Any]
EPISODE_PURPOSE = "echosim-evidence-graph"
MAX_PAGER_PAGES = 50


class BusinessGraphError(Exception):
    """业务图谱适配器失败；`code` 使用稳定原因码。"""

    def __init__(self, code: str, message: str, target: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.target = target


@dataclass(frozen=True)
class GraphBindingResult:
    graph_id: str
    reused: bool
    create_attempts: int


@dataclass(frozen=True)
class EpisodeWriteResult:
    episode_uuid: str | None
    accepted: bool
    response_lost: bool
    attempts: int


@dataclass(frozen=True)
class EpisodeConfirmation:
    episode_uuid: str | None
    matched: bool
    processed: bool
    search_attempts: int
    state: str
    processing_remaining_seconds: float = 0.0


@dataclass(frozen=True)
class MatchedRelation:
    edge_uuid: str
    manifest_key: str
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class ReadbackResult:
    nodes: tuple[object, ...]
    edges: tuple[object, ...]
    relations: dict[str, MatchedRelation]
    node_count: int
    edge_count: int
    stable_reads: int
    raw_node_count: int = 0
    raw_edge_count: int = 0
    publishable_edges: tuple[object, ...] = ()

    @property
    def mapped_relation_count(self) -> int:
        return len(self.relations)

    @property
    def mapped_source_count(self) -> int:
        return sum(len(item.sources) for item in self.relations.values())

    @property
    def publishable_edge_count(self) -> int:
        return len(self.publishable_edges)


def _scope_fingerprint(
    config: BusinessGraphConfig | None, acceptance_graph_id: str | None
) -> str | None:
    if config is not None:
        return config.acceptance_graph_fingerprint
    return graph_id_fingerprint(acceptance_graph_id)


def assert_business_graph_scope(
    graph_id: str,
    acceptance_graph_id: str | None = None,
    *,
    config: BusinessGraphConfig | None = None,
) -> None:
    """业务图谱不得指向验收专用图谱；业务侧只比较规范化 UUID 的指纹。"""
    expected = _scope_fingerprint(config, acceptance_graph_id)
    if expected and graph_id_fingerprint(graph_id) == expected:
        raise BusinessGraphError(
            "invalid_graph_scope", "业务图谱与验收专用图谱相同", str(graph_id)
        )


def set_graph_ontology(
    client: object,
    *,
    graph_id: str,
    entity_types: Sequence[object],
    relation_types: Sequence[object],
    idempotency_key: str | None = None,
) -> None:
    """按 zep-cloud 4.1.0 的 set_ontology 接口安装本次规划的本体。"""
    try:
        from zep_cloud.types.edge_source_target import EdgeSourceTarget
        from zep_cloud.types.edge_type import EdgeType as ZepEdgeType
        from zep_cloud.types.entity_type import EntityType as ZepEntityType
    except ImportError:  # 离线替身环境不安装 zep-cloud
        EdgeSourceTarget = None  # type: ignore[assignment]
        ZepEdgeType = None  # type: ignore[assignment]
        ZepEntityType = None  # type: ignore[assignment]

    type_name_by_key = {
        str(getattr(item, "key")): str(getattr(item, "name"))
        for item in entity_types
    }
    entities = (
        [
            ZepEntityType(
                name=str(getattr(item, "name")),
                description=str(getattr(item, "description") or ""),
            )
            for item in entity_types
        ]
        if ZepEntityType is not None
        else [
            {
                "name": str(getattr(item, "name")),
                "description": str(getattr(item, "description") or ""),
            }
            for item in entity_types
        ]
    )
    edges = []
    for item in relation_types:
        source_type = type_name_by_key.get(
            str(getattr(item, "source_type")), ""
        )
        target_type = type_name_by_key.get(
            str(getattr(item, "target_type")), ""
        )
        source_targets = []
        if source_type and target_type:
            source_targets.append(
                EdgeSourceTarget(source=source_type, target=target_type)
                if EdgeSourceTarget is not None
                else {"source": source_type, "target": target_type}
            )
        edges.append(
            ZepEdgeType(
                name=str(getattr(item, "name")),
                description=str(getattr(item, "description") or ""),
                source_targets=source_targets,
            )
            if ZepEdgeType is not None
            else {
                "name": str(getattr(item, "name")),
                "description": str(getattr(item, "description") or ""),
                "source_targets": source_targets,
            }
        )
    client.graph.set_ontology(  # type: ignore[attr-defined]
        graph_id,
        entity_types=entities,
        edge_types=edges,
        idempotency_key=idempotency_key,
    )


def _graph_uuid(graph: object) -> str | None:
    if isinstance(graph, dict):
        value = graph.get("uuid") or graph.get("uuid_")
        return str(value) if value else None
    for attribute in ("uuid_", "uuid"):
        value = getattr(graph, attribute, None)
        if value:
            return str(value)
    return None


def _episode_processed(item: object) -> bool:
    if isinstance(item, dict):
        return bool(item.get("processed"))
    return bool(getattr(item, "processed", False))


def _node_uuid(item: object) -> str | None:
    return _episode_uuid(item)


def _node_name(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "")
    return str(getattr(item, "name", "") or "")


def _edge_source(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("source_node_uuid") or "")
    return str(getattr(item, "source_node_uuid", "") or "")


def _edge_target(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("target_node_uuid") or "")
    return str(getattr(item, "target_node_uuid", "") or "")


def _edge_label(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("fact") or item.get("name") or "")
    return str(getattr(item, "fact", "") or getattr(item, "name", "") or "")


def _normalized_label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _episode_query(content: str) -> str:
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return content[:120]
    if isinstance(parsed, dict) and not content.lstrip().startswith(
        '{"searchText"'
    ):
        text = parsed.get("searchText")
        if isinstance(text, str) and text.strip():
            return text.strip()[:120]
    return content[:120]


def _episode_uuids(item: object) -> set[str]:
    if isinstance(item, dict):
        value = item.get("episode_uuids") or []
    else:
        value = getattr(item, "episode_uuids", None) or []
    return {str(entry) for entry in value if entry}


def _iter_pager_items(pager: object) -> Iterable[object]:
    page: object | None = pager
    pages = 0
    while page is not None:
        if pages >= MAX_PAGER_PAGES:
            raise BusinessGraphError(
                "graph_incomplete", "分页达到保护上限但仍有下一页"
            )
        pages += 1
        items = getattr(page, "items", None)
        if items is None and isinstance(page, dict):
            items = page.get("items")
        for item in items or []:
            yield item
        has_next = getattr(page, "has_next", None)
        if has_next is None and isinstance(page, dict):
            has_next = page.get("has_next")
        if not has_next:
            return
        next_page = getattr(page, "next_page", None)
        if callable(next_page):
            page = next_page()
        elif isinstance(page, dict):
            page = page.get("next_page")
            if callable(page):
                page = page()
        else:
            page = None


def _filters(kind: str, episode_uuids: Sequence[str]) -> object:
    try:
        if kind == "node":
            from zep_cloud.types.node_list_filters import NodeListFilters

            return NodeListFilters(episode_uuids=list(episode_uuids))
        from zep_cloud.types.edge_list_filters import EdgeListFilters

        return EdgeListFilters(episode_uuids=list(episode_uuids))
    except ImportError:
        return {"episode_uuids": list(episode_uuids)}


def _filter_episode_uuids(filters: object) -> list[str]:
    if isinstance(filters, dict):
        return list(filters.get("episode_uuids") or [])
    return list(getattr(filters, "episode_uuids", None) or [])


async def ensure_project_graph(
    session_factory: SessionFactory,
    *,
    project_id: uuid.UUID,
    client_factory: ClientFactory,
    acceptance_graph_id: str | None = None,
    config: BusinessGraphConfig | None = None,
    max_attempts: int = 2,
    sleep: Callable[[float], None] = time.sleep,
    call: Callable[[Callable[[], Any]], Awaitable[Any]] | None = None,
) -> GraphBindingResult:
    """先提交绑定，再在无数据库事务时调用 graph.create；重试复用原键原参数。"""
    async with session_factory() as db:
        binding = await store.get_or_create_binding(db, project_id=project_id)
    if binding.graph_id is not None:
        assert_business_graph_scope(
            str(binding.graph_id),
            acceptance_graph_id,
            config=config,
        )
        return GraphBindingResult(
            graph_id=str(binding.graph_id), reused=True, create_attempts=0
        )

    params = binding.create_params()
    idempotency_key = str(binding.create_idempotency_key)
    client = client_factory()
    attempts = 0
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        def _create() -> Any:
            return client.graph.create(
                name=params["name"],
                description=params["description"],
                time_zone=params["time_zone"],
                idempotency_key=idempotency_key,
            )

        try:
            graph = await call(_create) if call is not None else _create()
        except Exception as exc:  # noqa: BLE001 - SDK 异常按稳定分类处理
            error = _classify_exception(exc)
            if _is_auth_error(error):
                raise BusinessGraphError(
                    "auth_failed", "图谱创建认证失败"
                ) from exc
            if error.response_lost or error.retryable:
                if attempt < max_attempts:
                    sleep(0.05)
                    continue
                raise BusinessGraphError(
                    "graph_unavailable", "图谱创建响应未确认"
                ) from exc
            raise BusinessGraphError(
                "graph_unavailable", "图谱创建失败"
            ) from exc

        graph_id = _graph_uuid(graph)
        if not graph_id:
            raise BusinessGraphError(
                "invalid_graph_response", "图谱创建返回空 UUID"
            )
        assert_business_graph_scope(
            graph_id, acceptance_graph_id, config=config
        )
        async with session_factory() as db:
            saved = await store.save_binding_graph_id(
                db, project_id=project_id, graph_id=graph_id
            )
        return GraphBindingResult(
            graph_id=str(saved.graph_id), reused=False, create_attempts=attempts
        )
    raise BusinessGraphError("graph_unavailable", "图谱创建失败")


def write_episode(
    client: object,
    *,
    graph_id: str,
    episode: store.GraphEpisode,
    acceptance_graph_id: str | None = None,
    config: BusinessGraphConfig | None = None,
    max_attempts: int = 2,
    sleep: Callable[[float], None] = time.sleep,
) -> EpisodeWriteResult:
    """使用持久化幂等键/内容写入 episode；响应丢失时返回未确认状态。"""
    assert_business_graph_scope(graph_id, acceptance_graph_id, config=config)
    metadata = {
        "purpose": EPISODE_PURPOSE,
        "buildId": str(episode.build_id),
        "episodeKey": episode.episode_key,
        "contentSha256": episode.content_sha256,
    }
    attempts = 0
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        try:
            result = client.graph.episode.add(
                graph_id,
                data=episode.content,
                metadata=metadata,
                idempotency_key=str(episode.idempotency_key),
                type="text",
            )
        except Exception as exc:  # noqa: BLE001 - SDK 异常按稳定分类处理
            error = _classify_exception(exc)
            if _is_auth_error(error):
                raise BusinessGraphError(
                    "auth_failed", "episode 写入认证失败"
                ) from exc
            if error.response_lost:
                # 服务端可能已受理：不再次写入，交由检索确认。
                return EpisodeWriteResult(
                    episode_uuid=None,
                    accepted=False,
                    response_lost=True,
                    attempts=attempts,
                )
            if error.retryable and attempt < max_attempts:
                sleep(0.05)
                continue
            if error.retryable:
                return EpisodeWriteResult(
                    episode_uuid=None,
                    accepted=False,
                    response_lost=False,
                    attempts=attempts,
                )
            raise BusinessGraphError(
                "graph_unavailable", "episode 写入失败"
            ) from exc
        episode_uuid = _result_episode_uuid(result)
        if not episode_uuid:
            # 响应已到达但没有 UUID，同样视为可能已受理，先检索确认。
            return EpisodeWriteResult(
                episode_uuid=None,
                accepted=False,
                response_lost=True,
                attempts=attempts,
            )
        return EpisodeWriteResult(
            episode_uuid=str(episode_uuid),
            accepted=True,
            response_lost=False,
            attempts=attempts,
        )
    return EpisodeWriteResult(None, False, True, attempts)


def _search_episodes(
    client: object, graph_id: str, query: str, page_size: int = 50
) -> list[object]:
    """优先列本次图谱 episode，避免语义搜索漏掉刚写入的记录。"""
    episode_namespace = getattr(getattr(client, "graph", None), "episode", None)
    lister = getattr(episode_namespace, "list", None)
    if callable(lister):
        pager = lister(graph_id, page_size=max(page_size, 100))
        return list(_iter_pager_items(pager))
    pager = client.graph.search_episodes(  # type: ignore[attr-defined]
        graph_id, query=query, page_size=page_size
    )
    return list(_iter_pager_items(pager))


def _episode_match(
    item: object,
    *,
    build_id: uuid.UUID,
    episode_key: str,
    content_sha256: str,
    expected_uuid: str | None,
) -> tuple[bool, bool]:
    episode_uuid = _episode_uuid(item)
    metadata = _episode_metadata(item)
    content = _episode_content(item)
    build_ok = (
        str(metadata.get("buildId")) == str(build_id)
        and str(metadata.get("episodeKey")) == episode_key
    )
    # 只以实际返回的完整正文计算哈希；元数据哈希仅用于辅助排查。
    hash_ok = hashlib.sha256(content.encode("utf-8")).hexdigest() == content_sha256
    uuid_ok = expected_uuid is None or episode_uuid == expected_uuid
    matched = bool(episode_uuid) and build_ok and hash_ok and uuid_ok
    wrong_uuid = bool(
        expected_uuid
        and episode_uuid
        and episode_uuid != expected_uuid
        and build_ok
        and hash_ok
    )
    return matched, wrong_uuid


@dataclass(frozen=True)
class EpisodeSearch:
    matches: dict[str, bool]
    wrong_uuid_seen: bool


def search_episode_once(
    client: object,
    *,
    graph_id: str,
    episode: store.GraphEpisode,
    expected_uuid: str | None,
) -> EpisodeSearch:
    """扫描一次全部候选页，返回精确匹配 UUID 与是否出现错误 UUID。"""
    items = _search_episodes(client, graph_id, _episode_query(episode.content))
    matches: dict[str, bool] = {}
    wrong_uuid_seen = False
    for item in items:
        matched, wrong_uuid = _episode_match(
            item,
            build_id=episode.build_id,
            episode_key=episode.episode_key,
            content_sha256=episode.content_sha256,
            expected_uuid=expected_uuid,
        )
        if matched:
            item_uuid = _episode_uuid(item)
            if item_uuid:
                matches[item_uuid] = _episode_processed(item)
        if wrong_uuid:
            wrong_uuid_seen = True
    return EpisodeSearch(matches=matches, wrong_uuid_seen=wrong_uuid_seen)


def confirm_episode(
    client: object,
    *,
    graph_id: str,
    episode: store.GraphEpisode,
    expected_uuid: str | None,
    index_timeout_seconds: float,
    processing_timeout_seconds: float | None = None,
    require_processed: bool = True,
    poll_interval_seconds: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> EpisodeConfirmation:
    """用索引预算等待精确匹配，再用独立处理预算等待 processed。

    索引截止时间从调用开始计算；首次精确匹配后另行建立处理截止时间。轮询、
    单次搜索与分页都不会重置任一截止时间，且在接受返回值前复核剩余预算。
    跨轮保留已见的精确匹配 UUID；出现多个身份时拒绝任取。
    """
    processing_budget = (
        index_timeout_seconds
        if processing_timeout_seconds is None
        else processing_timeout_seconds
    )
    started = clock()
    index_deadline = started + max(0.001, index_timeout_seconds)
    processing_deadline: float | None = None
    search_attempts = 0
    seen_matches: dict[str, bool] = {}
    wrong_uuid_seen = False
    query = _episode_query(episode.content)
    while True:
        search_attempts += 1
        items = _search_episodes(client, graph_id, query)
        now = clock()
        for item in items:
            matched, wrong_uuid = _episode_match(
                item,
                build_id=episode.build_id,
                episode_key=episode.episode_key,
                content_sha256=episode.content_sha256,
                expected_uuid=expected_uuid,
            )
            if matched:
                item_uuid = _episode_uuid(item)
                if item_uuid:
                    seen_matches[item_uuid] = _episode_processed(item)
            if wrong_uuid:
                wrong_uuid_seen = True
        if len(seen_matches) > 1:
            raise BusinessGraphError(
                "write_unconfirmed", "同一正文命中多个 episode UUID"
            )
        if seen_matches:
            matched_uuid, matched_processed = next(iter(seen_matches.items()))
            if processing_deadline is None:
                if now > index_deadline:
                    code = (
                        "write_unconfirmed"
                        if expected_uuid is None
                        else "index_timeout"
                    )
                    raise BusinessGraphError(
                        code, "精确匹配在索引预算耗尽后才返回", matched_uuid
                    )
                processing_deadline = now + max(0.001, processing_budget)
            if now > processing_deadline:
                raise BusinessGraphError(
                    "processing_timeout", "episode 处理预算耗尽", matched_uuid
                )
            if matched_processed or not require_processed:
                return EpisodeConfirmation(
                    matched_uuid,
                    True,
                    matched_processed,
                    search_attempts,
                    "processed" if matched_processed else "matched",
                    max(0.0, processing_deadline - now),
                )
            deadline = processing_deadline
            state = "matched"
        else:
            if now > index_deadline:
                if expected_uuid is None:
                    code = "write_unconfirmed"
                elif wrong_uuid_seen:
                    code = "stale_or_foreign_hit"
                else:
                    code = "index_timeout"
                raise BusinessGraphError(
                    code, "episode 未能确认", expected_uuid
                )
            deadline = index_deadline
            state = "write_unconfirmed" if expected_uuid is None else "indexing"
        remaining = deadline - clock()
        if remaining <= 0:
            if seen_matches:
                raise BusinessGraphError(
                    "processing_timeout",
                    "episode 处理预算耗尽",
                    next(iter(seen_matches)),
                )
            code = "write_unconfirmed" if expected_uuid is None else "index_timeout"
            raise BusinessGraphError(code, "episode 未能确认", expected_uuid)
        sleep(min(max(0.0, poll_interval_seconds), remaining))


def confirm_and_readback(
    client: object,
    *,
    graph_id: str,
    episode: store.GraphEpisode,
    expected_uuid: str | None,
    index_timeout_seconds: float,
    processing_timeout_seconds: float,
    episode_uuids: Sequence[str],
    manifest_nodes: Sequence[object] = (),
    manifest_edges: Sequence[ContractEdge] = (),
    episode_batch_keys: Mapping[str, str] | None = None,
    poll_interval_seconds: float = 0.1,
    page_size: int = 100,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[EpisodeConfirmation, ReadbackResult]:
    """确认 episode 后，把处理阶段剩余预算交给节点/边分页读回。"""
    confirmation = confirm_episode(
        client,
        graph_id=graph_id,
        episode=episode,
        expected_uuid=expected_uuid,
        index_timeout_seconds=index_timeout_seconds,
        processing_timeout_seconds=processing_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        clock=clock,
        sleep=sleep,
    )
    remaining = confirmation.processing_remaining_seconds
    if remaining <= 0:
        raise BusinessGraphError("processing_timeout", "处理阶段没有剩余读回预算")
    readback = readback_graph(
        client,
        graph_id=graph_id,
        episode_uuids=episode_uuids,
        processing_timeout_seconds=remaining,
        manifest_nodes=manifest_nodes,
        manifest_edges=manifest_edges,
        poll_interval_seconds=poll_interval_seconds,
        page_size=page_size,
        clock=clock,
        sleep=sleep,
    )
    return confirmation, readback


def _list_all(
    client: object,
    *,
    graph_id: str,
    kind: str,
    episode_uuids: Sequence[str],
    page_size: int = 100,
) -> list[object]:
    filters = _filters(kind, episode_uuids)
    if kind == "node":
        pager = client.graph.node.list(
            graph_id, page_size=page_size, filters=filters
        )
    else:
        pager = client.graph.edge.list(
            graph_id, page_size=page_size, filters=filters
        )
    result: list[object] = []
    seen: set[str] = set()
    for item in _iter_pager_items(pager):
        item_uuid = _node_uuid(item)
        if item_uuid and item_uuid in seen:
            continue
        if item_uuid:
            seen.add(item_uuid)
        result.append(item)
    return result


def _signature(nodes: Sequence[object], edges: Sequence[object]) -> tuple[object, ...]:
    node_sig = tuple(
        sorted(
            (_node_uuid(item) or "", _node_name(item)) for item in nodes
        )
    )
    edge_sig = tuple(
        sorted(
            (
                _node_uuid(item) or "",
                _edge_source(item),
                _edge_target(item),
                _edge_label(item),
            )
            for item in edges
        )
    )
    return node_sig, edge_sig


def map_material_relations(
    manifest_nodes: Sequence[object],
    manifest_edges: Sequence[ContractEdge],
    nodes: Sequence[object],
    edges: Sequence[object],
    *,
    episode_uuids: Sequence[str] = (),
    episode_batch_keys: Mapping[str, str] | None = None,
) -> dict[str, MatchedRelation]:
    """把读回边唯一映射到本次清单关系；按 episode + 规范名/别名，不做模糊匹配。"""
    current_episodes = set(episode_uuids)
    batch_keys_by_episode = dict(episode_batch_keys or {})
    has_manifest_batches = any(
        getattr(node, "batch_keys", ()) for node in manifest_nodes
    )

    def normalized_forms(node: object) -> set[str]:
        forms = {getattr(node, "label", "")}
        forms.update(getattr(node, "aliases", ()) or ())
        return {_normalized_label(form) for form in forms if form}

    node_name_by_uuid: dict[str, str] = {}
    for item in nodes:
        node_uuid = _node_uuid(item)
        episodes = _episode_uuids(item)
        if not node_uuid:
            continue
        if current_episodes and episodes and not episodes.intersection(current_episodes):
            continue
        node_name_by_uuid[node_uuid] = _node_name(item)
    readback_node_to_manifest: dict[str, str] = {}
    if has_manifest_batches:
        for item in nodes:
            node_uuid = _node_uuid(item)
            if not node_uuid:
                continue
            readback_batches = {
                batch_keys_by_episode[episode]
                for episode in _episode_uuids(item)
                if episode in batch_keys_by_episode
            }
            name = _normalized_label(_node_name(item))
            candidates = [
                node
                for node in manifest_nodes
                if (
                    not readback_batches
                    or readback_batches.intersection(
                        getattr(node, "batch_keys", ()) or ()
                    )
                )
                and name in normalized_forms(node)
            ]
            if len(candidates) == 1:
                readback_node_to_manifest[node_uuid] = str(
                    getattr(candidates[0], "key", "")
                )
    else:
        # 旧清单兼容：沿用全局精确标签映射。
        label_to_keys: dict[str, list[str]] = {}
        for node in manifest_nodes:
            label = getattr(node, "label", "")
            key = getattr(node, "key", "")
            if label and key:
                label_to_keys.setdefault(label, []).append(key)
        for node_uuid, name in node_name_by_uuid.items():
            keys = label_to_keys.get(name, [])
            if len(keys) == 1:
                readback_node_to_manifest[node_uuid] = keys[0]

    matched: dict[str, MatchedRelation] = {}
    for edge in edges:
        edge_uuid = _node_uuid(edge)
        episodes = _episode_uuids(edge)
        if not edge_uuid:
            continue
        if current_episodes and not episodes.intersection(current_episodes):
            continue
        source_key = readback_node_to_manifest.get(_edge_source(edge))
        target_key = readback_node_to_manifest.get(_edge_target(edge))
        if not source_key or not target_key:
            continue
        edge_text = _normalized_label(_edge_label(edge))
        candidates = [
            item
            for item in manifest_edges
            if item.source_key == source_key
            and item.target_key == target_key
            and item.sources
        ]
        if has_manifest_batches:
            claim_nature = str(getattr(edge, "claim_nature", "") or "")
            if claim_nature:
                candidates = [
                    item
                    for item in candidates
                    if getattr(item, "claim_nature", "") == claim_nature
                ]
        if has_manifest_batches:
            def label_matches_text(item: object) -> bool:
                label = _normalized_label(getattr(item, "label", ""))
                if not label:
                    return False
                if label in edge_text:
                    return True
                # 服务端常把动作改写成完整句子；动作前缀仍可唯一消歧。
                return len(label) >= 2 and label[:2] in edge_text

            label_matches = [
                item for item in candidates if label_matches_text(item)
            ]
        else:
            label_matches = [
                item
                for item in candidates
                if _normalized_label(item.label) == edge_text
            ]
        chosen = label_matches[0] if len(label_matches) == 1 else None
        if chosen is None and has_manifest_batches and len(candidates) == 1:
            # 服务端可能把动作改写成完整句子；端点间只有一条清单关系时仍可唯一映射。
            chosen = candidates[0]
        if chosen is not None:
            matched[edge_uuid] = MatchedRelation(
                edge_uuid=edge_uuid,
                manifest_key=chosen.key,
                sources=tuple(chosen.sources),
            )
    return matched


@dataclass(frozen=True)
class RawReadbackResult:
    nodes: tuple[object, ...]
    edges: tuple[object, ...]
    node_count: int
    edge_count: int
    stable_reads: int


def readback_raw_graph(
    client: object,
    *,
    graph_id: str,
    episode_uuids: Sequence[str],
    processing_timeout_seconds: float,
    poll_interval_seconds: float = 0.1,
    page_size: int = 100,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RawReadbackResult:
    """按本次 build 的 episode UUID 分页读回全部节点与边，不要求命中清单。"""
    if not episode_uuids:
        raise BusinessGraphError("graph_incomplete", "没有可读回的 episode")
    deadline = clock() + max(0.001, processing_timeout_seconds)
    previous: tuple[object, ...] | None = None
    stable_reads = 0
    backoff = 1.0
    while True:
        try:
            nodes = _list_all(
                client,
                graph_id=graph_id,
                kind="node",
                episode_uuids=episode_uuids,
                page_size=page_size,
            )
            edges = _list_all(
                client,
                graph_id=graph_id,
                kind="edge",
                episode_uuids=episode_uuids,
                page_size=page_size,
            )
        except BusinessGraphError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider 异常按稳定分类处理
            error = _classify_exception(exc)
            remaining = deadline - clock()
            # 图谱仍在写入时分页游标会被服务端判为过期（409）；从头重读。
            if (error.retryable or error.status_code == 409) and remaining > 0:
                sleep(min(backoff, remaining))
                backoff = min(backoff * 2, 8.0)
                previous = None
                stable_reads = 0
                continue
            if remaining <= 0:
                raise BusinessGraphError(
                    "processing_timeout", "分页读回越过处理截止时间"
                ) from exc
            raise BusinessGraphError(
                "graph_unavailable", "节点或边读回失败"
            ) from exc
        signature = _signature(nodes, edges)
        now = clock()
        if previous == signature:
            stable_reads += 1
            if stable_reads >= 1 and nodes:
                if now > deadline:
                    raise BusinessGraphError(
                        "processing_timeout", "分页读回越过处理截止时间"
                    )
                return RawReadbackResult(
                    nodes=tuple(nodes),
                    edges=tuple(edges),
                    node_count=len(nodes),
                    edge_count=len(edges),
                    stable_reads=stable_reads,
                )
        else:
            stable_reads = 0
            previous = signature
        remaining = deadline - clock()
        if remaining <= 0:
            if not nodes:
                raise BusinessGraphError(
                    "processing_timeout", "读回预算耗尽且没有可用节点"
                )
            return RawReadbackResult(
                nodes=tuple(nodes),
                edges=tuple(edges),
                node_count=len(nodes),
                edge_count=len(edges),
                stable_reads=stable_reads,
            )
        sleep(min(max(0.0, poll_interval_seconds), remaining))


def readback_graph(
    client: object,
    *,
    graph_id: str,
    episode_uuids: Sequence[str],
    processing_timeout_seconds: float,
    manifest_nodes: Sequence[object] = (),
    manifest_edges: Sequence[ContractEdge] = (),
    episode_batch_keys: Mapping[str, str] | None = None,
    poll_interval_seconds: float = 0.1,
    page_size: int = 100,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ReadbackResult:
    """按本次 episode UUID 过滤，分页读回节点/边并等待相邻完整读取稳定。"""
    if not episode_uuids:
        raise BusinessGraphError("graph_incomplete", "没有可读回的 episode")
    deadline = clock() + max(0.001, processing_timeout_seconds)
    previous: tuple[object, ...] | None = None
    stable_reads = 0
    backoff = 1.0
    while True:
        try:
            nodes = _list_all(
                client,
                graph_id=graph_id,
                kind="node",
                episode_uuids=episode_uuids,
                page_size=page_size,
            )
            edges = _list_all(
                client,
                graph_id=graph_id,
                kind="edge",
                episode_uuids=episode_uuids,
                page_size=page_size,
            )
        except BusinessGraphError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider 异常按稳定分类处理
            error = _classify_exception(exc)
            remaining = deadline - clock()
            if (error.retryable or error.status_code == 409) and remaining > 0:
                sleep(min(backoff, remaining))
                backoff = min(backoff * 2, 8.0)
                previous = None
                stable_reads = 0
                continue
            if remaining <= 0:
                raise BusinessGraphError(
                    "processing_timeout", "分页读回越过处理截止时间"
                ) from exc
            raise BusinessGraphError(
                "graph_unavailable", "节点或边读回失败"
            ) from exc
        signature = _signature(nodes, edges)
        now = clock()
        if previous == signature:
            stable_reads += 1
            if stable_reads >= 1 and nodes and edges:
                if now > deadline:
                    raise BusinessGraphError(
                        "processing_timeout", "分页读回越过处理截止时间"
                    )
                relations = map_material_relations(
                    manifest_nodes,
                    manifest_edges,
                    nodes,
                    edges,
                    episode_uuids=episode_uuids,
                    episode_batch_keys=episode_batch_keys,
                )
                if manifest_edges and not relations:
                    raise BusinessGraphError(
                        "graph_incomplete", "读回边无法唯一对应抽取清单"
                    )
                publishable_edges = tuple(
                    edge for edge in edges if _node_uuid(edge) in relations
                )
                return ReadbackResult(
                    nodes=tuple(nodes),
                    edges=tuple(edges),
                    relations=relations,
                    node_count=len(nodes),
                    edge_count=len(publishable_edges),
                    stable_reads=stable_reads,
                    raw_node_count=len(nodes),
                    raw_edge_count=len(edges),
                    publishable_edges=publishable_edges,
                )
        else:
            stable_reads = 0
            previous = signature
        remaining = deadline - clock()
        if remaining <= 0:
            if not nodes or not edges:
                raise BusinessGraphError(
                    "processing_timeout", "读回预算耗尽且没有可用节点或边"
                )
            raise BusinessGraphError("processing_timeout", "节点/边读回不稳定")
        sleep(min(max(0.0, poll_interval_seconds), remaining))
