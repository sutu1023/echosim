"""业务图谱绑定、episode 写入确认与读回分页测试。"""

from __future__ import annotations

import asyncio
import hashlib
import unittest
import uuid
from unittest.mock import patch
from typing import Any, AsyncContextManager, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engines.business_config import BusinessGraphConfig
from app.engines.business_contracts import GraphEdge, GraphNode, SourceRef
from app.engines.graph import GraphCallError
from app.engines import business_graph as graph_module
from app.engines.business_graph import (
    BusinessGraphError,
    EpisodeWriteResult,
    assert_business_graph_scope,
    confirm_and_readback,
    confirm_episode,
    ensure_project_graph,
    map_material_relations,
    readback_graph,
    write_episode,
)
from app.services import evidence_graph as store
from tests.db_support import (
    HAS_TEST_DATABASE,
    SKIP_REASON,
    SeedMaterial,
    apply_all_migrations,
    make_test_engine,
    reset_public_schema,
    seed_original_material,
)


class StubPager:
    def __init__(self, pages: list[list[object]], index: int = 0) -> None:
        self._pages = pages
        self._index = index
        self.items = pages[index] if index < len(pages) else []
        self.has_next = index + 1 < len(pages)

    def next_page(self) -> "StubPager | None":
        if not self.has_next:
            return None
        return StubPager(self._pages, self._index + 1)


class EndlessPager:
    def __init__(self, item: object) -> None:
        self.items = [item]
        self.has_next = True

    def next_page(self) -> "EndlessPager":
        return EndlessPager(self.items[0])


class CreateGraphClient:
    def __init__(self) -> None:
        self.graph = self
        self.graphs: dict[str, str] = {}
        self.create_keys: list[str] = []
        self.create_kwargs: list[dict[str, Any]] = []
        self.active_sessions: list[int] = []
        self.session_factory: Any = None
        self.lose_response = False
        self.fail_after_create = False

    def create(self, **kwargs: Any) -> dict[str, Any]:
        if self.session_factory is not None:
            self.active_sessions.append(self.session_factory.active)
        key = str(kwargs["idempotency_key"])
        self.create_keys.append(key)
        self.create_kwargs.append(dict(kwargs))
        graph_id = self.graphs.setdefault(key, str(uuid.uuid4()))
        if self.fail_after_create:
            self.fail_after_create = False
            raise TimeoutError("模拟收到 UUID 后保存前崩溃")
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("模拟创建响应丢失")
        return {"uuid": graph_id}


class WriteEpisodeClient:
    def __init__(self, log: list[tuple[str, object]] | None = None) -> None:
        self.graph = self
        self.episode = self
        self.episode_keys: list[str] = []
        self.calls = 0
        self.stored: dict[str, dict[str, Any]] = {}
        self.lose_once = False
        self.always_lose = False
        self.retryable_not_accepted_once = False
        self.log = log if log is not None else []

    def add(self, graph_uuid: str, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        key = str(kwargs["idempotency_key"])
        self.episode_keys.append(key)
        self.log.append(("write", key))
        if self.retryable_not_accepted_once:
            self.retryable_not_accepted_once = False
            raise GraphCallError(
                "rate limited before acceptance",
                status_code=429,
                response_lost=False,
                retryable=True,
            )
        item = self.stored.get(key)
        if item is None:
            item = {
                "uuid": str(uuid.uuid4()),
                "content": kwargs["data"],
                "metadata": dict(kwargs["metadata"]),
                "processed": True,
            }
            self.stored[key] = item
        if self.always_lose or self.lose_once:
            self.lose_once = False
            raise TimeoutError("模拟 episode 写入响应丢失")
        return {"episode": {"uuid": item["uuid"], "processed": True}}


class SearchClient:
    def __init__(
        self,
        pages: list[list[object]] | None = None,
        log: list[tuple[str, object]] | None = None,
        *,
        page_sequence: list[list[list[object]]] | None = None,
    ) -> None:
        self.graph = self
        self.pages = pages or [[]]
        self.page_sequence = page_sequence
        self.calls = 0
        self.log = log if log is not None else []

    def search_episodes(
        self, graph_uuid: str, *, query: str, page_size: int = 50
    ) -> StubPager:
        del graph_uuid, query, page_size
        self.calls += 1
        self.log.append(("search", self.calls))
        if self.page_sequence:
            index = min(self.calls - 1, len(self.page_sequence) - 1)
            return StubPager(self.page_sequence[index])
        return StubPager(self.pages)


class _NodeNamespace:
    def __init__(self, client: "ReadbackClient") -> None:
        self.client = client

    def list(self, graph_uuid: str, *, page_size: int, filters: object, **kwargs: Any):
        del graph_uuid, page_size, kwargs
        return self.client.next_nodes(filters)


class _EdgeNamespace:
    def __init__(self, client: "ReadbackClient") -> None:
        self.client = client

    def list(self, graph_uuid: str, *, page_size: int, filters: object, **kwargs: Any):
        del graph_uuid, page_size, kwargs
        return self.client.next_edges(filters)


class TimedProcessedSearchClient:
    def __init__(self, episode: store.GraphEpisode, clock: object, processed_at: float) -> None:
        self.graph = self
        self.episode = episode
        self.clock = clock
        self.processed_at = processed_at
        self.calls = 0

    def search_episodes(self, *args: Any, **kwargs: Any) -> StubPager:
        del args, kwargs
        self.calls += 1
        processed = self.clock() >= self.processed_at  # type: ignore[operator]
        return StubPager(
            [[
                {
                    "uuid": "ep-1",
                    "content": self.episode.content,
                    "metadata": {
                        "buildId": str(self.episode.build_id),
                        "episodeKey": self.episode.episode_key,
                        "contentSha256": self.episode.content_sha256,
                    },
                    "processed": processed,
                }
            ]]
        )


class SlowSearchClient:
    def __init__(
        self,
        item: dict[str, Any] | None,
        clock: object,
        delay: float,
        *,
        delay_after_first: float | None = None,
    ) -> None:
        self.graph = self
        self.item = item
        self.clock = clock
        self.delay = delay
        self.delay_after_first = delay_after_first
        self.calls = 0

    def search_episodes(self, *args: Any, **kwargs: Any) -> StubPager:
        del args, kwargs
        self.calls += 1
        delay = (
            self.delay_after_first
            if self.calls > 1 and self.delay_after_first is not None
            else self.delay
        )
        self.clock[0] += delay  # type: ignore[index]
        return StubPager([[self.item] if self.item else []])


class SlowSearchSequenceClient:
    def __init__(
        self,
        items: list[dict[str, Any]],
        clock: list[float],
        delays: list[float],
    ) -> None:
        self.graph = self
        self.items = items
        self.clock = clock
        self.delays = delays
        self.calls = 0

    def search_episodes(self, *args: Any, **kwargs: Any) -> StubPager:
        del args, kwargs
        index = min(self.calls, len(self.delays) - 1)
        self.clock[0] += self.delays[index]
        item = self.items[min(self.calls, len(self.items) - 1)]
        self.calls += 1
        return StubPager([[item]] if item else [])


class ReadbackClient:
    def __init__(
        self,
        node_reads: list[list[list[object]]],
        edge_reads: list[list[list[object]]],
    ) -> None:
        self.graph = self
        self.node = _NodeNamespace(self)
        self.edge = _EdgeNamespace(self)
        self.node_reads = node_reads
        self.edge_reads = edge_reads
        self.node_index = 0
        self.edge_index = 0
        self.node_filters: list[object] = []
        self.edge_filters: list[object] = []

    def next_nodes(self, filters: object) -> StubPager:
        self.node_filters.append(filters)
        index = min(self.node_index, len(self.node_reads) - 1)
        self.node_index += 1
        return StubPager(self.node_reads[index])

    def next_edges(self, filters: object) -> StubPager:
        self.edge_filters.append(filters)
        index = min(self.edge_index, len(self.edge_reads) - 1)
        self.edge_index += 1
        return StubPager(self.edge_reads[index])


class TrackingSessionFactory:
    def __init__(self, factory: async_sessionmaker) -> None:
        self.factory = factory
        self.active = 0

    def __call__(self) -> AsyncContextManager[AsyncSession]:
        return _TrackingContext(self)


class _TrackingContext:
    def __init__(self, owner: TrackingSessionFactory) -> None:
        self.owner = owner
        self.session = owner.factory()

    async def __aenter__(self) -> AsyncSession:
        self.owner.active += 1
        return await self.session.__aenter__()

    async def __aexit__(self, *args: object) -> object:
        self.owner.active -= 1
        return await self.session.__aexit__(*args)


def _manifest() -> tuple[GraphNode, GraphNode, GraphEdge]:
    source = SourceRef(
        document_id="doc-1",
        chunk_index=1,
        quote="校园接驳车",
        source_start=0,
        source_end=5,
    )
    node_a = GraphNode(
        "node-1", "entity", "接驳车", "material", sources=(source,)
    )
    node_b = GraphNode(
        "node-2", "entity", "备用车", "hypothesis", sources=()
    )
    edge = GraphEdge(
        "edge-1",
        "relation",
        "增加备用车",
        "material",
        "node-1",
        "node-2",
        sources=(source,),
    )
    return node_a, node_b, edge


class ReadbackTests(unittest.TestCase):
    def test_cross_page_duplicate_uuids_are_deduplicated(self) -> None:
        nodes = [
            [{"uuid": "n1", "name": "接驳车"}, {"uuid": "n2", "name": "备用车"}],
            [{"uuid": "n1", "name": "接驳车"}, {"uuid": "n3", "name": "其他"}],
        ]
        edges = [
            [
                {
                    "uuid": "e1",
                    "source_node_uuid": "n1",
                    "target_node_uuid": "n2",
                    "name": "增加备用车",
                    "episode_uuids": ["ep-1"],
                }
            ],
            [
                {
                    "uuid": "e1",
                    "source_node_uuid": "n1",
                    "target_node_uuid": "n2",
                    "name": "增加备用车",
                    "episode_uuids": ["ep-1"],
                },
                {
                    "uuid": "e2",
                    "source_node_uuid": "n2",
                    "target_node_uuid": "n3",
                    "name": "其他",
                    "episode_uuids": ["ep-1"],
                },
            ],
        ]
        client = ReadbackClient([nodes, nodes], [edges, edges])
        node_a, node_b, edge = _manifest()
        result = readback_graph(
            client,
            graph_id="g",
            episode_uuids=["ep-1"],
            processing_timeout_seconds=5,
            manifest_nodes=[node_a, node_b],
            manifest_edges=[edge],
            sleep=lambda _: None,
        )

        self.assertEqual(result.node_count, 3)
        self.assertEqual(result.raw_edge_count, 2)
        self.assertEqual(result.edge_count, 1)
        self.assertEqual(result.publishable_edge_count, 1)
        self.assertEqual(len(result.relations), 1)
        self.assertEqual(result.relations["e1"].manifest_key, "edge-1")
        self.assertEqual(result.mapped_relation_count, 1)
        self.assertEqual(result.mapped_source_count, len(edge.sources))
        self.assertTrue(all(getattr(f, "get", None) and f["episode_uuids"] == ["ep-1"] for f in client.node_filters))
        self.assertTrue(all(getattr(f, "get", None) and f["episode_uuids"] == ["ep-1"] for f in client.edge_filters))

    def test_same_count_but_changed_endpoint_keeps_waiting(self) -> None:
        nodes = [[{"uuid": "n1", "name": "接驳车"}, {"uuid": "n2", "name": "备用车"}]]
        first_edges = [
            [
                {
                    "uuid": "e1",
                    "source_node_uuid": "n1",
                    "target_node_uuid": "n2",
                    "name": "增加备用车",
                    "episode_uuids": ["ep-1"],
                }
            ]
        ]
        changed_edges = [
            [
                {
                    "uuid": "e1",
                    "source_node_uuid": "n2",
                    "target_node_uuid": "n1",
                    "name": "增加备用车",
                    "episode_uuids": ["ep-1"],
                }
            ]
        ]
        client = ReadbackClient(
            [nodes, nodes, nodes, nodes],
            [first_edges, changed_edges, changed_edges, changed_edges],
        )
        node_a, node_b, edge = _manifest()
        result = readback_graph(
            client,
            graph_id="g",
            episode_uuids=["ep-1"],
            processing_timeout_seconds=5,
            manifest_nodes=[node_a, node_b],
            manifest_edges=[],
            sleep=lambda _: None,
        )

        self.assertGreaterEqual(client.edge_index, 3)
        self.assertEqual(result.raw_edge_count, 1)
        self.assertEqual(result.edge_count, 0)

    def test_no_edges_is_processing_timeout(self) -> None:
        client = ReadbackClient(
            [[[{"uuid": "n1", "name": "接驳车"}]]],
            [[[]]],
        )
        clock = [0.0]
        with self.assertRaises(BusinessGraphError) as caught:
            readback_graph(
                client,
                graph_id="g",
                episode_uuids=["ep-1"],
                processing_timeout_seconds=0.01,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            )
        self.assertEqual(caught.exception.code, "processing_timeout")

    def test_unmapped_material_relation_is_graph_incomplete(self) -> None:
        nodes = [[
            {"uuid": "n1", "name": "不同节点"},
            {"uuid": "n2", "name": "另一个节点"},
        ]]
        edges = [[
            {
                "uuid": "e1",
                "source_node_uuid": "n1",
                "target_node_uuid": "n2",
                "name": "不同关系",
                "episode_uuids": ["ep-1"],
            }
        ]]
        client = ReadbackClient([nodes, nodes], [edges, edges])
        node_a, node_b, edge = _manifest()
        with self.assertRaises(BusinessGraphError) as caught:
            readback_graph(
                client,
                graph_id="g",
                episode_uuids=["ep-1"],
                processing_timeout_seconds=1,
                manifest_nodes=[node_a, node_b],
                manifest_edges=[edge],
                sleep=lambda _: None,
            )
        self.assertEqual(caught.exception.code, "graph_incomplete")

    def test_opposite_relation_is_not_published(self) -> None:
        nodes = [[
            {"uuid": "n1", "name": "接驳车"},
            {"uuid": "n2", "name": "备用车"},
        ]]
        edges = [[
            {
                "uuid": "e1",
                "source_node_uuid": "n1",
                "target_node_uuid": "n2",
                "name": "取消备用车",
                "episode_uuids": ["ep-1"],
            }
        ]]
        client = ReadbackClient([nodes, nodes], [edges, edges])
        node_a, node_b, edge = _manifest()
        with self.assertRaises(BusinessGraphError) as caught:
            readback_graph(
                client,
                graph_id="g",
                episode_uuids=["ep-1"],
                processing_timeout_seconds=1,
                manifest_nodes=[node_a, node_b],
                manifest_edges=[edge],
                sleep=lambda _: None,
            )
        self.assertEqual(caught.exception.code, "graph_incomplete")

    def test_relation_fact_equivalence_is_exact(self) -> None:
        nodes = [[
            {"uuid": "n1", "name": "接驳车"},
            {"uuid": "n2", "name": "备用车"},
        ]]
        node_a, node_b, edge = _manifest()
        for label, should_map in (
            ("增加备用车", True),
            ("不增加备用车", False),
            ("曾提议增加备用车，但该方案未获批准", False),
        ):
            with self.subTest(label=label):
                edges = [[
                    {
                        "uuid": "e1",
                        "source_node_uuid": "n1",
                        "target_node_uuid": "n2",
                        "name": label,
                        "episode_uuids": ["ep-1"],
                    }
                ]]
                client = ReadbackClient([nodes, nodes], [edges, edges])
                if should_map:
                    result = readback_graph(
                        client,
                        graph_id="g",
                        episode_uuids=["ep-1"],
                        processing_timeout_seconds=1,
                        manifest_nodes=[node_a, node_b],
                        manifest_edges=[edge],
                        sleep=lambda _: None,
                    )
                    self.assertEqual(result.relations["e1"].manifest_key, "edge-1")
                    self.assertEqual(result.publishable_edge_count, 1)
                else:
                    with self.assertRaises(BusinessGraphError) as caught:
                        readback_graph(
                            client,
                            graph_id="g",
                            episode_uuids=["ep-1"],
                            processing_timeout_seconds=1,
                            manifest_nodes=[node_a, node_b],
                            manifest_edges=[edge],
                            sleep=lambda _: None,
                        )
                    self.assertEqual(caught.exception.code, "graph_incomplete")

    def test_same_endpoints_different_relation_maps_by_meaning(self) -> None:
        nodes = [[
            {"uuid": "n1", "name": "接驳车"},
            {"uuid": "n2", "name": "备用车"},
        ]]
        edges = [[
            {
                "uuid": "e1",
                "source_node_uuid": "n1",
                "target_node_uuid": "n2",
                "name": "取消备用车",
                "episode_uuids": ["ep-1"],
            }
        ]]
        client = ReadbackClient([nodes, nodes], [edges, edges])
        node_a, node_b, edge_add = _manifest()
        source = edge_add.sources
        edge_cancel = GraphEdge(
            "edge-cancel",
            "relation",
            "取消备用车",
            "material",
            "node-1",
            "node-2",
            sources=source,
        )
        result = readback_graph(
            client,
            graph_id="g",
            episode_uuids=["ep-1"],
            processing_timeout_seconds=1,
            manifest_nodes=[node_a, node_b],
            manifest_edges=[edge_add, edge_cancel],
            sleep=lambda _: None,
        )
        self.assertEqual(result.relations["e1"].manifest_key, "edge-cancel")
        self.assertEqual(result.mapped_source_count, len(source))

    def test_old_episode_edge_is_ignored(self) -> None:
        nodes = [[
            {"uuid": "n1", "name": "接驳车"},
            {"uuid": "n2", "name": "备用车"},
        ]]
        edges = [[
            {
                "uuid": "e1",
                "source_node_uuid": "n1",
                "target_node_uuid": "n2",
                "name": "增加备用车",
                "episode_uuids": ["old-episode"],
            }
        ]]
        client = ReadbackClient([nodes, nodes], [edges, edges])
        node_a, node_b, edge = _manifest()
        with self.assertRaises(BusinessGraphError) as caught:
            readback_graph(
                client,
                graph_id="g",
                episode_uuids=["ep-1"],
                processing_timeout_seconds=1,
                manifest_nodes=[node_a, node_b],
                manifest_edges=[edge],
                sleep=lambda _: None,
            )
        self.assertEqual(caught.exception.code, "graph_incomplete")

    def test_two_edges_only_one_can_be_mapped(self) -> None:
        nodes = [[
            {"uuid": "n1", "name": "接驳车"},
            {"uuid": "n2", "name": "备用车"},
        ]]
        edges = [[
            {
                "uuid": "e1",
                "source_node_uuid": "n1",
                "target_node_uuid": "n2",
                "name": "增加备用车",
                "episode_uuids": ["ep-1"],
            },
            {
                "uuid": "e2",
                "source_node_uuid": "n1",
                "target_node_uuid": "n2",
                "name": "未在清单中的关系",
                "episode_uuids": ["ep-1"],
            },
        ]]
        client = ReadbackClient([nodes, nodes], [edges, edges])
        node_a, node_b, edge = _manifest()
        result = readback_graph(
            client,
            graph_id="g",
            episode_uuids=["ep-1"],
            processing_timeout_seconds=1,
            manifest_nodes=[node_a, node_b],
            manifest_edges=[edge],
            sleep=lambda _: None,
        )
        self.assertEqual(result.raw_edge_count, 2)
        self.assertEqual(result.edge_count, 1)
        self.assertEqual(result.publishable_edge_count, 1)
        self.assertEqual(result.mapped_relation_count, 1)
        self.assertEqual(result.mapped_source_count, len(edge.sources))

    def test_delayed_edges_poll_until_stable(self) -> None:
        nodes = [[
            {"uuid": "n1", "name": "接驳车"},
            {"uuid": "n2", "name": "备用车"},
        ]]
        edges = [[
            {
                "uuid": "e1",
                "source_node_uuid": "n1",
                "target_node_uuid": "n2",
                "name": "增加备用车",
                "episode_uuids": ["ep-1"],
            }
        ]]
        client = ReadbackClient(
            [nodes],
            [[], [], edges, edges],
        )
        node_a, node_b, edge = _manifest()
        clock = [0.0]
        result = readback_graph(
            client,
            graph_id="g",
            episode_uuids=["ep-1"],
            processing_timeout_seconds=2,
            manifest_nodes=[node_a, node_b],
            manifest_edges=[edge],
            clock=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.001)),
        )
        self.assertEqual(result.edge_count, 1)
        self.assertEqual(result.raw_edge_count, 1)
        self.assertEqual(result.relations["e1"].manifest_key, "edge-1")
        self.assertGreaterEqual(client.edge_index, 4)

    def test_pager_limit_reports_incomplete(self) -> None:
        class NodeList:
            def list(self, *args: Any, **kwargs: Any):
                del args, kwargs
                return EndlessPager({"uuid": "n1", "name": "接驳车"})

        class EdgeList:
            def list(self, *args: Any, **kwargs: Any):
                del args, kwargs
                return StubPager([[]])

        class Client:
            def __init__(self) -> None:
                self.graph = self
                self.node = NodeList()
                self.edge = EdgeList()

        with self.assertRaises(BusinessGraphError) as caught:
            readback_graph(
                Client(),
                graph_id="g",
                episode_uuids=["ep-1"],
                processing_timeout_seconds=1,
                sleep=lambda _: None,
            )
        self.assertEqual(caught.exception.code, "graph_incomplete")

    def test_search_pager_limit_reports_incomplete(self) -> None:
        content = "原创 episode 内容"
        episode = store.GraphEpisode(
            build_id=uuid.uuid4(),
            episode_key="episode-1",
            idempotency_key=uuid.uuid4(),
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            episode_uuid=None,
            state="planned",
            attempts=0,
        )

        class SearchEndlessClient:
            def __init__(self) -> None:
                self.graph = self

            def search_episodes(self, *args: Any, **kwargs: Any):
                del args, kwargs
                return EndlessPager({"uuid": "ep-1"})

        with self.assertRaises(BusinessGraphError) as caught:
            confirm_episode(
                SearchEndlessClient(),
                graph_id="g",
                episode=episode,
                expected_uuid=None,
                index_timeout_seconds=1,
                sleep=lambda _: None,
            )
        self.assertEqual(caught.exception.code, "graph_incomplete")

    def _manual_episode(self) -> store.GraphEpisode:
        content = "原创 episode 内容"
        return store.GraphEpisode(
            build_id=uuid.uuid4(),
            episode_key="episode-1",
            idempotency_key=uuid.uuid4(),
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            episode_uuid=None,
            state="planned",
            attempts=0,
        )

    def test_index_and_processing_budgets_are_independent(self) -> None:
        episode = self._manual_episode()
        clock = [0.0]
        client = TimedProcessedSearchClient(episode, lambda: clock[0], 0.4)
        confirmation = confirm_episode(
            client,
            graph_id="g",
            episode=episode,
            expected_uuid="ep-1",
            index_timeout_seconds=0.2,
            processing_timeout_seconds=1.0,
            poll_interval_seconds=0.1,
            clock=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.001)),
        )
        self.assertTrue(confirmation.processed)
        self.assertGreaterEqual(clock[0], 0.4)
        self.assertLess(clock[0], 0.6)
        self.assertAlmostEqual(confirmation.processing_remaining_seconds, 0.6, places=1)

    def test_unmatched_index_budget_is_index_timeout(self) -> None:
        episode = self._manual_episode()
        clock = [0.0]
        with self.assertRaises(BusinessGraphError) as caught:
            confirm_episode(
                SearchClient([[]]),
                graph_id="g",
                episode=episode,
                expected_uuid="ep-1",
                index_timeout_seconds=0.2,
                processing_timeout_seconds=1.0,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            )
        self.assertEqual(caught.exception.code, "index_timeout")
        self.assertGreaterEqual(clock[0], 0.2)
        self.assertLess(clock[0], 0.3)

    def test_unprocessed_processing_budget_is_processing_timeout(self) -> None:
        episode = self._manual_episode()
        clock = [0.0]
        client = TimedProcessedSearchClient(
            episode, lambda: clock[0], processed_at=99.0
        )
        with self.assertRaises(BusinessGraphError) as caught:
            confirm_episode(
                client,
                graph_id="g",
                episode=episode,
                expected_uuid="ep-1",
                index_timeout_seconds=0.2,
                processing_timeout_seconds=1.0,
                poll_interval_seconds=0.1,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.001)),
            )
        self.assertEqual(caught.exception.code, "processing_timeout")
        self.assertGreaterEqual(clock[0], 1.0)
        self.assertLess(clock[0], 1.2)

    def test_remaining_processing_budget_is_passed_to_readback(self) -> None:
        episode = self._manual_episode()
        clock = [0.0]
        client = TimedProcessedSearchClient(episode, lambda: clock[0], 0.0)
        sentinel = object()
        with patch.object(
            graph_module, "readback_graph", return_value=sentinel
        ) as mocked:
            confirmation, result = confirm_and_readback(
                client,
                graph_id="g",
                episode=episode,
                expected_uuid="ep-1",
                index_timeout_seconds=0.2,
                processing_timeout_seconds=1.0,
                episode_uuids=["ep-1"],
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.001)),
            )
        self.assertIs(result, sentinel)
        self.assertTrue(confirmation.processed)
        self.assertAlmostEqual(
            mocked.call_args.kwargs["processing_timeout_seconds"],
            confirmation.processing_remaining_seconds,
        )

    def test_same_uuid_unprocessed_then_processed_succeeds(self) -> None:
        episode = self._manual_episode()
        item = {
            "content": episode.content,
            "metadata": {
                "buildId": str(episode.build_id),
                "episodeKey": episode.episode_key,
                "contentSha256": episode.content_sha256,
            },
        }
        search = SearchClient(
            page_sequence=[
                [[{"uuid": "ep-a", **item, "processed": False}]],
                [[{"uuid": "ep-a", **item, "processed": True}]],
            ]
        )
        clock = [0.0]
        confirmation = confirm_episode(
            search,
            graph_id="g",
            episode=episode,
            expected_uuid="ep-a",
            index_timeout_seconds=1,
            processing_timeout_seconds=1,
            clock=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.001)),
        )
        self.assertEqual(confirmation.episode_uuid, "ep-a")
        self.assertTrue(confirmation.processed)

    def test_cross_round_distinct_matches_are_ambiguous(self) -> None:
        episode = self._manual_episode()
        item = {
            "content": episode.content,
            "metadata": {
                "buildId": str(episode.build_id),
                "episodeKey": episode.episode_key,
                "contentSha256": episode.content_sha256,
            },
        }
        search = SearchClient(
            page_sequence=[
                [[{"uuid": "ep-a", **item, "processed": False}]],
                [[{"uuid": "ep-b", **item, "processed": True}]],
            ]
        )
        clock = [0.0]
        with self.assertRaises(BusinessGraphError) as caught:
            confirm_episode(
                search,
                graph_id="g",
                episode=episode,
                expected_uuid=None,
                index_timeout_seconds=1,
                processing_timeout_seconds=1,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.001)),
            )
        self.assertEqual(caught.exception.code, "write_unconfirmed")

    def test_slow_search_after_index_budget_is_index_timeout(self) -> None:
        episode = self._manual_episode()
        item = {
            "uuid": "ep-a",
            "content": episode.content,
            "metadata": {
                "buildId": str(episode.build_id),
                "episodeKey": episode.episode_key,
                "contentSha256": episode.content_sha256,
            },
            "processed": True,
        }
        clock = [0.0]
        client = SlowSearchClient(item, clock, 0.8)
        with self.assertRaises(BusinessGraphError) as caught:
            confirm_episode(
                client,
                graph_id="g",
                episode=episode,
                expected_uuid="ep-a",
                index_timeout_seconds=0.2,
                processing_timeout_seconds=1.0,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.001)),
            )
        self.assertEqual(caught.exception.code, "index_timeout")

    def test_slow_search_after_processing_deadline_is_processing_timeout(self) -> None:
        episode = self._manual_episode()
        item = {
            "uuid": "ep-a",
            "content": episode.content,
            "metadata": {
                "buildId": str(episode.build_id),
                "episodeKey": episode.episode_key,
                "contentSha256": episode.content_sha256,
            },
            "processed": True,
        }
        clock = [0.0]
        # 第一轮 t=0 匹配但未处理；第二轮搜索耗时 1.2s，返回已处理也不能接受。
        client = SlowSearchSequenceClient(
            [{**item, "processed": False}, item],
            clock,
            [0.0, 1.2],
        )
        with self.assertRaises(BusinessGraphError) as caught:
            confirm_episode(
                client,
                graph_id="g",
                episode=episode,
                expected_uuid="ep-a",
                index_timeout_seconds=0.2,
                processing_timeout_seconds=1.0,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, 0.001)),
            )
        self.assertEqual(caught.exception.code, "processing_timeout")

    def test_slow_readback_over_processing_budget_is_processing_timeout(self) -> None:
        class SlowNodeList:
            def __init__(self, clock: list[float]) -> None:
                self.clock = clock

            def list(self, *args: Any, **kwargs: Any) -> StubPager:
                del args, kwargs
                self.clock[0] += 0.6
                return StubPager([[[
                    {"uuid": "n1", "name": "接驳车", "episode_uuids": ["ep-1"]}
                ]]])

        class SlowEdgeList:
            def __init__(self, clock: list[float]) -> None:
                self.clock = clock

            def list(self, *args: Any, **kwargs: Any) -> StubPager:
                del args, kwargs
                self.clock[0] += 0.6
                return StubPager([[[
                    {
                        "uuid": "e1",
                        "source_node_uuid": "n1",
                        "target_node_uuid": "n1",
                        "name": "增加备用车",
                        "episode_uuids": ["ep-1"],
                    }
                ]]])

        class Client:
            def __init__(self, clock: list[float]) -> None:
                self.graph = self
                self.node = SlowNodeList(clock)
                self.edge = SlowEdgeList(clock)

        clock = [0.0]
        with self.assertRaises(BusinessGraphError) as caught:
            readback_graph(
                Client(clock),
                graph_id="g",
                episode_uuids=["ep-1"],
                processing_timeout_seconds=1.0,
                clock=lambda: clock[0],
                sleep=lambda _: None,
            )
        self.assertEqual(caught.exception.code, "processing_timeout")

    def test_scope_guard_rejects_acceptance_graph(self) -> None:
        acceptance = "11111111-1111-4111-8111-111111111111"
        with self.assertRaises(BusinessGraphError) as caught:
            assert_business_graph_scope(acceptance, acceptance)
        self.assertEqual(caught.exception.code, "invalid_graph_scope")


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class BindingAndEpisodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)
        async with self.session_factory() as db:
            self.seed = await seed_original_material(db, marker="business-graph")
            await db.commit()
        async with self.session_factory() as db:
            self.build = await store.create_build(
                db,
                project_id=self.seed.project_id,
                actor_id=self.seed.user_id,
                question="原创业务图谱问题",
                document_ids=[self.seed.document_id],
            )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _episode(self) -> store.GraphEpisode:
        content = "原创 episode 内容"
        async with self.session_factory() as db:
            await store.record_episode(
                db,
                build_id=self.build.id,
                episode_key="episode-1",
                content=content,
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
            await db.commit()
        async with self.session_factory() as db:
            return await store.get_episode(
                db, build_id=self.build.id, episode_key="episode-1"
            )

    async def test_binding_is_committed_before_network_and_reused(self) -> None:
        tracking = TrackingSessionFactory(self.session_factory)
        client = CreateGraphClient()
        client.session_factory = tracking
        first = await ensure_project_graph(
            tracking,
            project_id=self.seed.project_id,
            client_factory=lambda: client,
            sleep=lambda _: None,
        )
        second = await ensure_project_graph(
            tracking,
            project_id=self.seed.project_id,
            client_factory=lambda: client,
            sleep=lambda _: None,
        )

        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(first.graph_id, second.graph_id)
        self.assertEqual(len(client.graphs), 1)
        self.assertEqual(client.active_sessions, [0])
        self.assertEqual(len(set(client.create_keys)), 1)
        async with self.session_factory() as db:
            binding = await store.require_binding(
                db, project_id=self.seed.project_id
            )
        self.assertEqual(str(binding.graph_id), first.graph_id)

    async def test_lost_create_response_retries_same_key_and_binds_one_graph(self) -> None:
        client = CreateGraphClient()
        client.lose_response = True
        result = await ensure_project_graph(
            self.session_factory,
            project_id=self.seed.project_id,
            client_factory=lambda: client,
            sleep=lambda _: None,
        )

        self.assertEqual(len(client.graphs), 1)
        self.assertEqual(len(client.create_keys), 2)
        self.assertEqual(len(set(client.create_keys)), 1)
        self.assertEqual(result.create_attempts, 2)
        params = [
            (item["name"], item["description"], item["time_zone"])
            for item in client.create_kwargs
        ]
        self.assertEqual(params[0], params[1])
        async with self.session_factory() as db:
            binding = await store.require_binding(
                db, project_id=self.seed.project_id
            )
        self.assertEqual(str(binding.graph_id), result.graph_id)

    async def test_crash_after_uuid_retries_same_key_and_binds_one_graph(self) -> None:
        client = CreateGraphClient()
        client.fail_after_create = True
        result = await ensure_project_graph(
            self.session_factory,
            project_id=self.seed.project_id,
            client_factory=lambda: client,
            sleep=lambda _: None,
        )

        self.assertEqual(len(client.graphs), 1)
        self.assertEqual(len(client.create_keys), 2)
        self.assertEqual(len(set(client.create_keys)), 1)
        self.assertEqual(result.create_attempts, 2)

    async def test_reused_binding_matching_acceptance_graph_is_rejected(self) -> None:
        acceptance_graph_id = "11111111-1111-4111-8111-111111111111"
        async with self.session_factory() as db:
            await store.get_or_create_binding(db, project_id=self.seed.project_id)
            await store.save_binding_graph_id(
                db,
                project_id=self.seed.project_id,
                graph_id=acceptance_graph_id,
            )
        client = CreateGraphClient()
        with self.assertRaises(BusinessGraphError) as caught:
            await ensure_project_graph(
                self.session_factory,
                project_id=self.seed.project_id,
                client_factory=lambda: client,
                acceptance_graph_id=acceptance_graph_id,
            )
        self.assertEqual(caught.exception.code, "invalid_graph_scope")
        self.assertEqual(client.create_keys, [])

    async def test_runtime_config_fingerprint_rejects_misbound_acceptance_graph(self) -> None:
        acceptance = "11111111-1111-4111-8111-111111111111"
        config = BusinessGraphConfig.from_env(
            {
                "ZEP_ACCEPTANCE_GRAPH_ID": acceptance,
                "ZEP_API_KEY": "test-zep-key",
            }
        )
        self.assertIsNotNone(config.acceptance_graph_fingerprint)
        self.assertNotIn(acceptance, str(config))
        async with self.session_factory() as db:
            await store.get_or_create_binding(db, project_id=self.seed.project_id)
            await store.save_binding_graph_id(
                db,
                project_id=self.seed.project_id,
                graph_id=acceptance,
            )
        client = CreateGraphClient()
        with self.assertRaises(BusinessGraphError) as caught:
            await ensure_project_graph(
                self.session_factory,
                project_id=self.seed.project_id,
                client_factory=lambda: client,
                config=config,
            )
        self.assertEqual(caught.exception.code, "invalid_graph_scope")
        self.assertEqual(client.create_keys, [])

        episode = await self._episode()
        writer = WriteEpisodeClient()
        with self.assertRaises(BusinessGraphError) as write_caught:
            write_episode(
                writer,
                graph_id=acceptance,
                episode=episode,
                config=config,
            )
        self.assertEqual(write_caught.exception.code, "invalid_graph_scope")
        self.assertEqual(writer.calls, 0)

    async def test_clear_retryable_not_accepted_retries_same_key(self) -> None:
        episode = await self._episode()
        log: list[tuple[str, object]] = []
        client = WriteEpisodeClient(log=log)
        client.retryable_not_accepted_once = True
        written = write_episode(
            client,
            graph_id="graph-1",
            episode=episode,
            max_attempts=2,
            sleep=lambda _: None,
        )
        self.assertTrue(written.accepted)
        self.assertEqual(written.attempts, 2)
        self.assertEqual(len(client.episode_keys), 2)
        self.assertEqual(set(client.episode_keys), {str(episode.idempotency_key)})
        self.assertEqual(
            [event for event, _ in log], ["write", "write"]
        )
        self.assertEqual(
            client.stored[str(episode.idempotency_key)]["metadata"]["contentSha256"],
            episode.content_sha256,
        )

    async def test_lost_response_searches_without_second_write(self) -> None:
        episode = await self._episode()
        log: list[tuple[str, object]] = []
        client = WriteEpisodeClient(log=log)
        client.always_lose = True
        written = write_episode(
            client,
            graph_id="graph-1",
            episode=episode,
            max_attempts=2,
            sleep=lambda _: None,
        )
        self.assertFalse(written.accepted)
        self.assertIsNone(written.episode_uuid)
        self.assertEqual(written.attempts, 1)
        self.assertEqual(client.calls, 1)

        stored = client.stored[str(episode.idempotency_key)]
        search = SearchClient(
            page_sequence=[
                [],
                [
                    [
                        {
                            "uuid": "old-episode",
                            "content": "旧内容",
                            "metadata": {
                                "buildId": str(episode.build_id),
                                "episodeKey": "old",
                                "contentSha256": "b" * 64,
                            },
                            "processed": True,
                        }
                    ]
                ],
                [
                    [
                        {
                            "uuid": stored["uuid"],
                            "content": stored["content"],
                            "metadata": stored["metadata"],
                            "processed": True,
                        }
                    ]
                ],
            ],
            log=log,
        )
        confirmation = confirm_episode(
            search,
            graph_id="graph-1",
            episode=episode,
            expected_uuid=None,
            index_timeout_seconds=1,
            sleep=lambda _: None,
        )
        self.assertEqual(confirmation.episode_uuid, stored["uuid"])
        self.assertTrue(confirmation.processed)
        events = [event for event, _ in log]
        self.assertEqual(events[0], "write")
        self.assertEqual(events.count("write"), 1)
        self.assertTrue(all(event == "search" for event in events[1:]))
        self.assertGreaterEqual(events.count("search"), 2)

    async def test_wrong_uuid_before_correct_record_is_scanned(self) -> None:
        episode = await self._episode()
        correct_uuid = "correct-episode"
        search = SearchClient(
            [
                [
                    {
                        "uuid": "wrong-episode",
                        "content": episode.content,
                        "metadata": {
                            "buildId": str(episode.build_id),
                            "episodeKey": episode.episode_key,
                            "contentSha256": episode.content_sha256,
                        },
                        "processed": True,
                    },
                    {
                        "uuid": correct_uuid,
                        "content": episode.content,
                        "metadata": {
                            "buildId": str(episode.build_id),
                            "episodeKey": episode.episode_key,
                            "contentSha256": episode.content_sha256,
                        },
                        "processed": True,
                    },
                ]
            ]
        )
        confirmation = confirm_episode(
            search,
            graph_id="g",
            episode=episode,
            expected_uuid=correct_uuid,
            index_timeout_seconds=1,
            sleep=lambda _: None,
        )
        self.assertEqual(confirmation.episode_uuid, correct_uuid)

    async def test_changed_body_with_same_metadata_is_not_confirmed(self) -> None:
        episode = await self._episode()
        search = SearchClient(
            [
                [
                    {
                        "uuid": "ep-1",
                        "content": "正文被修改但元数据未变",
                        "metadata": {
                            "buildId": str(episode.build_id),
                            "episodeKey": episode.episode_key,
                            "contentSha256": episode.content_sha256,
                        },
                        "processed": True,
                    }
                ]
            ]
        )
        clock = [0.0]
        with self.assertRaises(BusinessGraphError) as caught:
            confirm_episode(
                search,
                graph_id="g",
                episode=episode,
                expected_uuid="ep-1",
                index_timeout_seconds=0.01,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            )
        self.assertEqual(caught.exception.code, "index_timeout")

    async def test_multiple_matching_uuids_without_expected_is_ambiguous(self) -> None:
        episode = await self._episode()
        item = {
            "content": episode.content,
            "metadata": {
                "buildId": str(episode.build_id),
                "episodeKey": episode.episode_key,
                "contentSha256": episode.content_sha256,
            },
            "processed": True,
        }
        search = SearchClient(
            [
                [
                    {"uuid": "ep-a", **item},
                    {"uuid": "ep-b", **item},
                ]
            ]
        )
        with self.assertRaises(BusinessGraphError) as caught:
            confirm_episode(
                search,
                graph_id="g",
                episode=episode,
                expected_uuid=None,
                index_timeout_seconds=1,
                sleep=lambda _: None,
            )
        self.assertEqual(caught.exception.code, "write_unconfirmed")

    async def test_old_and_correct_results_in_same_page(self) -> None:
        episode = await self._episode()
        correct_uuid = "correct-episode"
        search = SearchClient(
            [
                [
                    {
                        "uuid": "old-episode",
                        "content": "旧内容",
                        "metadata": {
                            "buildId": str(episode.build_id),
                            "episodeKey": "old",
                            "contentSha256": "b" * 64,
                        },
                        "processed": True,
                    },
                    {
                        "uuid": correct_uuid,
                        "content": episode.content,
                        "metadata": {
                            "buildId": str(episode.build_id),
                            "episodeKey": episode.episode_key,
                            "contentSha256": episode.content_sha256,
                        },
                        "processed": True,
                    },
                ]
            ]
        )
        confirmation = confirm_episode(
            search,
            graph_id="g",
            episode=episode,
            expected_uuid=correct_uuid,
            index_timeout_seconds=1,
            sleep=lambda _: None,
        )
        self.assertEqual(confirmation.episode_uuid, correct_uuid)
        self.assertTrue(confirmation.processed)

    async def test_confirmation_distinguishes_index_processing_and_unconfirmed(self) -> None:
        episode = await self._episode()
        empty = SearchClient([[]])
        clock = [0.0]
        with self.assertRaises(BusinessGraphError) as index_timeout:
            confirm_episode(
                empty,
                graph_id="g",
                episode=episode,
                expected_uuid="ep-1",
                index_timeout_seconds=0.01,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            )
        self.assertEqual(index_timeout.exception.code, "index_timeout")

        clock[0] = 0.0
        with self.assertRaises(BusinessGraphError) as unconfirmed:
            confirm_episode(
                empty,
                graph_id="g",
                episode=episode,
                expected_uuid=None,
                index_timeout_seconds=0.01,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            )
        self.assertEqual(unconfirmed.exception.code, "write_unconfirmed")

        matched_not_processed = SearchClient(
            [
                [
                    {
                        "uuid": "ep-1",
                        "content": episode.content,
                        "metadata": {
                            "buildId": str(episode.build_id),
                            "episodeKey": episode.episode_key,
                            "contentSha256": episode.content_sha256,
                        },
                        "processed": False,
                    }
                ]
            ]
        )
        clock[0] = 0.0
        with self.assertRaises(BusinessGraphError) as processing_timeout:
            confirm_episode(
                matched_not_processed,
                graph_id="g",
                episode=episode,
                expected_uuid="ep-1",
                index_timeout_seconds=0.01,
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            )
        self.assertEqual(processing_timeout.exception.code, "processing_timeout")

    async def test_scope_guard_runs_before_first_episode_write(self) -> None:
        episode = await self._episode()
        client = WriteEpisodeClient()
        acceptance = "11111111-1111-4111-8111-111111111111"
        with self.assertRaises(BusinessGraphError) as caught:
            write_episode(
                client,
                graph_id=acceptance,
                episode=episode,
                acceptance_graph_id=acceptance,
            )
        self.assertEqual(caught.exception.code, "invalid_graph_scope")
        self.assertEqual(client.calls, 0)


if __name__ == "__main__":
    unittest.main()
