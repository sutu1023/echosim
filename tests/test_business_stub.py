"""业务建图离线替身：稳定、随材料变化且不产生真实调用。"""

from __future__ import annotations

import hashlib
import json
import unittest
import uuid

from app.engines.business_config import BusinessGraphConfig
from app.engines.business_graph import (
    confirm_episode,
    readback_graph,
    write_episode,
)
from app.engines.business_model import (
    BusinessModelError,
    BusinessModelPlanner,
    ChunkInput,
    extraction_to_payload,
)
from app.engines.business_stub import StubGraphClient, StubModelTransport
from app.services import evidence_graph as store

QUESTION = "雨天是否应该增加一班校园接驳车？"
CHUNK_A = ChunkInput(
    document_id="00000000-0000-4000-8000-00000000000a",
    document_name="原创接驳车材料",
    chunk_index=1,
    text="校园接驳车在雨天把发车间隔调整为十分钟。",
    source_start=0,
    source_end=21,
    paragraph_start=1,
    paragraph_end=1,
)
CHUNK_B = ChunkInput(
    document_id="00000000-0000-4000-8000-00000000000b",
    document_name="原创图书馆材料",
    chunk_index=1,
    text="图书馆在考试周延长开放到晚上十点。",
    source_start=0,
    source_end=18,
    paragraph_start=1,
    paragraph_end=1,
)
CHUNK_C = ChunkInput(
    document_id="00000000-0000-4000-8000-00000000000c",
    document_name="原创接驳车晴天材料",
    chunk_index=1,
    text="校园接驳车在晴天恢复正常班次。",
    source_start=0,
    source_end=16,
    paragraph_start=1,
    paragraph_end=1,
)


def run_stub(chunk: ChunkInput):
    transport = StubModelTransport(chunks=[chunk], question=QUESTION)
    planner = BusinessModelPlanner(
        model_api_base_url="https://model.test.local/v1",
        model_api_key="test-key",
        model_name="test-model",
        timeout_seconds=5,
        transport=transport,
        call_kind="double",
        sleep=lambda _: None,
    )
    plan = planner.plan_and_extract(question=QUESTION, chunks=[chunk])
    return transport, plan


def batch_manifest(plan, batch, *, first_batch: bool):
    refs = {
        (ref.document_id, ref.chunk_index) for ref in batch.chunk_refs
    }
    nodes = []
    for node in plan.graph.nodes:
        source_refs = {
            (source.document_id, source.chunk_index) for source in node.sources
        }
        if source_refs.intersection(refs) or (
            first_batch and node.origin == "hypothesis"
        ):
            nodes.append(node)
    edges = [
        edge
        for edge in plan.graph.edges
        if {
            (source.document_id, source.chunk_index)
            for source in edge.sources
        }.intersection(refs)
    ]
    return tuple(nodes), tuple(edges)


def run_full_chain(chunks: list[ChunkInput]):
    transport = StubModelTransport(chunks=chunks, question=QUESTION)
    planner = BusinessModelPlanner(
        model_api_base_url="https://model.test.local/v1",
        model_api_key="test-key",
        model_name="test-model",
        timeout_seconds=5,
        transport=transport,
        call_kind="double",
        sleep=lambda _: None,
    )
    plan = planner.plan_and_extract(question=QUESTION, chunks=chunks)
    client = StubGraphClient()
    build_id = uuid.uuid4()
    episode_uuids: list[str] = []
    for index, batch in enumerate(plan.batches):
        nodes, edges = batch_manifest(
            plan, batch, first_batch=index == 0
        )
        content = json.dumps(
            extraction_to_payload(nodes, edges), ensure_ascii=False
        )
        episode = store.GraphEpisode(
            build_id=build_id,
            episode_key=batch.key,
            idempotency_key=uuid.uuid4(),
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            episode_uuid=None,
            state="planned",
            attempts=0,
        )
        written = write_episode(
            client,
            graph_id="stub-graph",
            episode=episode,
            sleep=lambda _: None,
        )
        assert written.episode_uuid is not None
        episode_uuids.append(written.episode_uuid)
        confirmation = confirm_episode(
            client,
            graph_id="stub-graph",
            episode=episode,
            expected_uuid=written.episode_uuid,
            index_timeout_seconds=1,
            sleep=lambda _: None,
        )
        assert confirmation.processed
    readback = readback_graph(
        client,
        graph_id="stub-graph",
        episode_uuids=episode_uuids,
        processing_timeout_seconds=1,
        manifest_nodes=plan.graph.nodes,
        manifest_edges=plan.graph.edges,
        sleep=lambda _: None,
    )
    return transport, plan, readback


class ConflictBatchTransport(StubModelTransport):
    def _extract(self, batch_key: str) -> dict[str, object]:
        data = super()._extract(batch_key)
        for node in data["nodes"]:  # type: ignore[index]
            if node["nodeType"] == "entity":
                node["key"] = "duplicate-cross-batch"
        for edge in data["edges"]:  # type: ignore[index]
            if edge["sourceKey"].startswith("node-"):
                edge["sourceKey"] = "duplicate-cross-batch"
        return data


class StubProviderTests(unittest.TestCase):
    def test_stub_requires_explicit_allow(self) -> None:
        config = BusinessGraphConfig.from_env(
            {
                "EVIDENCE_GRAPH_MODEL_PROVIDER": "stub",
                "EVIDENCE_GRAPH_GRAPH_PROVIDER": "stub",
            }
        )
        from app.engines.business_config import BusinessGraphConfigError

        with self.assertRaises(BusinessGraphConfigError):
            config.resolve_providers()

    def test_same_input_is_stable_and_real_calls_are_zero(self) -> None:
        transport_one, first = run_stub(CHUNK_A)
        transport_two, second = run_stub(CHUNK_A)

        self.assertEqual(transport_one.real_calls, 0)
        self.assertEqual(transport_one.double_calls, 2)
        self.assertEqual(transport_one.double_calls, transport_two.double_calls)
        self.assertEqual(
            [(node.key, node.label, node.origin) for node in first.graph.nodes],
            [(node.key, node.label, node.origin) for node in second.graph.nodes],
        )
        self.assertEqual(
            [edge.label for edge in first.graph.edges],
            [edge.label for edge in second.graph.edges],
        )
        self.assertEqual(
            [source.document_id for source in first.graph.edges[0].sources],
            [source.document_id for source in second.graph.edges[0].sources],
        )

    def test_different_material_changes_relation_and_sources(self) -> None:
        _, first = run_stub(CHUNK_A)
        _, second = run_stub(CHUNK_B)

        self.assertNotEqual(first.graph.nodes[0].label, second.graph.nodes[0].label)
        self.assertNotEqual(first.graph.edges[0].label, second.graph.edges[0].label)
        self.assertNotEqual(
            first.graph.edges[0].sources[0].document_id,
            second.graph.edges[0].sources[0].document_id,
        )

    def test_stub_batch_readback_is_scoped_to_written_episode(self) -> None:
        chunks = [CHUNK_A, CHUNK_B]
        transport = StubModelTransport(chunks=chunks, question=QUESTION)
        planner = BusinessModelPlanner(
            model_api_base_url="https://model.test.local/v1",
            model_api_key="test-key",
            model_name="test-model",
            timeout_seconds=5,
            transport=transport,
            call_kind="double",
            sleep=lambda _: None,
        )
        plan = planner.plan_and_extract(question=QUESTION, chunks=chunks)
        client = StubGraphClient()
        build_id = uuid.uuid4()
        batch_a, batch_b = plan.batches[0], plan.batches[1]
        nodes_a, edges_a = batch_manifest(plan, batch_a, first_batch=True)
        nodes_b, edges_b = batch_manifest(plan, batch_b, first_batch=False)

        def write_batch(batch, nodes, edges) -> tuple[str, str]:
            content = json.dumps(
                extraction_to_payload(nodes, edges), ensure_ascii=False
            )
            episode = store.GraphEpisode(
                build_id=build_id,
                episode_key=batch.key,
                idempotency_key=uuid.uuid4(),
                content=content,
                content_sha256=hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                episode_uuid=None,
                state="planned",
                attempts=0,
            )
            written = write_episode(
                client,
                graph_id="stub-graph",
                episode=episode,
                sleep=lambda _: None,
            )
            assert written.episode_uuid is not None
            confirm_episode(
                client,
                graph_id="stub-graph",
                episode=episode,
                expected_uuid=written.episode_uuid,
                index_timeout_seconds=1,
                sleep=lambda _: None,
            )
            return batch.key, written.episode_uuid

        _, uuid_a = write_batch(batch_a, nodes_a, edges_a)
        self.assertEqual(len(client.nodes), len(nodes_a))
        self.assertEqual(len(client.edges), len(edges_a))
        read_a = readback_graph(
            client,
            graph_id="stub-graph",
            episode_uuids=[uuid_a],
            processing_timeout_seconds=1,
            manifest_nodes=nodes_a,
            manifest_edges=edges_a,
            sleep=lambda _: None,
        )
        self.assertEqual(
            {source.document_id for item in read_a.relations.values() for source in item.sources},
            {CHUNK_A.document_id},
        )

        _, uuid_b = write_batch(batch_b, nodes_b, edges_b)
        read_a_after_b = readback_graph(
            client,
            graph_id="stub-graph",
            episode_uuids=[uuid_a],
            processing_timeout_seconds=1,
            manifest_nodes=nodes_a,
            manifest_edges=edges_a,
            sleep=lambda _: None,
        )
        self.assertEqual(
            {source.document_id for item in read_a_after_b.relations.values() for source in item.sources},
            {CHUNK_A.document_id},
        )
        read_both = readback_graph(
            client,
            graph_id="stub-graph",
            episode_uuids=[uuid_a, uuid_b],
            processing_timeout_seconds=1,
            manifest_nodes=plan.graph.nodes,
            manifest_edges=plan.graph.edges,
            sleep=lambda _: None,
        )
        self.assertEqual(
            {source.document_id for item in read_both.relations.values() for source in item.sources},
            {CHUNK_A.document_id, CHUNK_B.document_id},
        )

    def test_full_chain_two_chunks_maps_relation_to_material_source(self) -> None:
        transport, plan, readback = run_full_chain([CHUNK_A, CHUNK_B])

        hypothesis = [
            node for node in plan.graph.nodes if node.origin == "hypothesis"
        ]
        self.assertEqual(len(hypothesis), 1)
        self.assertGreaterEqual(readback.mapped_relation_count, 1)
        self.assertEqual(
            readback.mapped_source_count,
            sum(len(item.sources) for item in readback.relations.values()),
        )
        matched = next(iter(readback.relations.values()))
        self.assertIn(matched.sources[0].document_id, {CHUNK_A.document_id, CHUNK_B.document_id})
        self.assertEqual(matched.sources[0].chunk_index, 1)
        self.assertTrue(matched.sources[0].quote)
        self.assertEqual(transport.real_calls, 0)
        self.assertGreater(transport.double_calls, 0)

    def test_full_chain_changes_relation_and_source_with_material(self) -> None:
        _, first_plan, first_readback = run_full_chain([CHUNK_A, CHUNK_B])
        _, second_plan, second_readback = run_full_chain([CHUNK_B, CHUNK_C])

        first_edge = next(
            edge for edge in first_plan.graph.edges if edge.key in {
                item.manifest_key for item in first_readback.relations.values()
            }
        )
        second_edge = next(
            edge for edge in second_plan.graph.edges if edge.key in {
                item.manifest_key for item in second_readback.relations.values()
            }
        )
        self.assertNotEqual(first_edge.label, second_edge.label)
        self.assertNotEqual(
            first_edge.sources[0].document_id,
            second_edge.sources[0].document_id,
        )

    def test_cross_batch_duplicate_key_is_still_rejected(self) -> None:
        transport = ConflictBatchTransport(
            chunks=[CHUNK_A, CHUNK_B], question=QUESTION
        )
        planner = BusinessModelPlanner(
            model_api_base_url="https://model.test.local/v1",
            model_api_key="test-key",
            model_name="test-model",
            timeout_seconds=5,
            transport=transport,
            call_kind="double",
            sleep=lambda _: None,
        )
        with self.assertRaises(BusinessModelError) as caught:
            planner.plan_and_extract(
                question=QUESTION, chunks=[CHUNK_A, CHUNK_B]
            )
        self.assertEqual(caught.exception.code, "invalid_graph_response")

    def test_content_without_manifest_does_not_create_relations(self) -> None:
        client = StubGraphClient()
        content = json.dumps({"batch": "batch-1", "chunkCount": 1})
        episode = store.GraphEpisode(
            build_id=uuid.uuid4(),
            episode_key="batch-1",
            idempotency_key=uuid.uuid4(),
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            episode_uuid=None,
            state="planned",
            attempts=0,
        )
        written = write_episode(
            client,
            graph_id="graph-1",
            episode=episode,
            sleep=lambda _: None,
        )
        self.assertTrue(written.accepted)
        self.assertEqual(client.nodes, [])
        self.assertEqual(client.edges, [])

    def test_cross_graph_queries_do_not_leak_episode_records(self) -> None:
        transport = StubModelTransport(chunks=[CHUNK_A], question=QUESTION)
        planner = BusinessModelPlanner(
            model_api_base_url="https://model.test.local/v1",
            model_api_key="test-key",
            model_name="test-model",
            timeout_seconds=5,
            transport=transport,
            call_kind="double",
            sleep=lambda _: None,
        )
        plan = planner.plan_and_extract(question=QUESTION, chunks=[CHUNK_A])
        nodes, edges = batch_manifest(plan, plan.batches[0], first_batch=True)
        content = json.dumps(
            extraction_to_payload(nodes, edges), ensure_ascii=False
        )
        client = StubGraphClient()
        episode = store.GraphEpisode(
            build_id=uuid.uuid4(),
            episode_key="batch-1",
            idempotency_key=uuid.uuid4(),
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            episode_uuid=None,
            state="planned",
            attempts=0,
        )
        written = write_episode(
            client,
            graph_id="graph-1",
            episode=episode,
            sleep=lambda _: None,
        )
        assert written.episode_uuid is not None
        search = client.search_episodes(
            "graph-2", query=content[:20], page_size=10
        )
        self.assertEqual(search.items, [])
        node_page = client.graph.node.list(
            "graph-2",
            page_size=10,
            filters={"episode_uuids": [written.episode_uuid]},
        )
        edge_page = client.graph.edge.list(
            "graph-2",
            page_size=10,
            filters={"episode_uuids": [written.episode_uuid]},
        )
        self.assertEqual(node_page.items, [])
        self.assertEqual(edge_page.items, [])

    def test_second_build_on_same_graph_keeps_old_and_new_episode_scopes(self) -> None:
        transport = StubModelTransport(chunks=[CHUNK_A], question=QUESTION)
        planner = BusinessModelPlanner(
            model_api_base_url="https://model.test.local/v1",
            model_api_key="test-key",
            model_name="test-model",
            timeout_seconds=5,
            transport=transport,
            call_kind="double",
            sleep=lambda _: None,
        )
        plan = planner.plan_and_extract(question=QUESTION, chunks=[CHUNK_A])
        nodes, edges = batch_manifest(plan, plan.batches[0], first_batch=True)
        content = json.dumps(
            extraction_to_payload(nodes, edges), ensure_ascii=False
        )
        client = StubGraphClient()

        def write_new(build_id: uuid.UUID) -> tuple[str, str]:
            episode = store.GraphEpisode(
                build_id=build_id,
                episode_key="batch-1",
                idempotency_key=uuid.uuid4(),
                content=content,
                content_sha256=hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                episode_uuid=None,
                state="planned",
                attempts=0,
            )
            written = write_episode(
                client,
                graph_id="graph-1",
                episode=episode,
                sleep=lambda _: None,
            )
            assert written.episode_uuid is not None
            return written.episode_uuid, str(episode.idempotency_key)

        old_uuid, _ = write_new(uuid.uuid4())
        new_uuid, new_key = write_new(uuid.uuid4())
        read_new = readback_graph(
            client,
            graph_id="graph-1",
            episode_uuids=[new_uuid],
            processing_timeout_seconds=1,
            manifest_nodes=nodes,
            manifest_edges=edges,
            sleep=lambda _: None,
        )
        read_old = readback_graph(
            client,
            graph_id="graph-1",
            episode_uuids=[old_uuid],
            processing_timeout_seconds=1,
            manifest_nodes=nodes,
            manifest_edges=edges,
            sleep=lambda _: None,
        )
        self.assertGreaterEqual(read_new.mapped_relation_count, 1)
        self.assertGreaterEqual(read_old.mapped_relation_count, 1)
        episode_count = len(client.episodes)
        node_count = len(client.nodes)
        edge_count = len(client.edges)
        # 重复同一幂等写入不新增记录。
        duplicate = store.GraphEpisode(
            build_id=uuid.uuid4(),
            episode_key="batch-1",
            idempotency_key=uuid.UUID(new_key),
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            episode_uuid=new_uuid,
            state="written",
            attempts=0,
        )
        write_episode(
            client,
            graph_id="graph-1",
            episode=duplicate,
            sleep=lambda _: None,
        )
        self.assertEqual(len(client.episodes), episode_count)
        self.assertEqual(len(client.nodes), node_count)
        self.assertEqual(len(client.edges), edge_count)

    def test_stub_graph_write_and_confirmation(self) -> None:
        client = StubGraphClient()
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
        written = write_episode(
            client,
            graph_id="graph-1",
            episode=episode,
            sleep=lambda _: None,
        )
        self.assertTrue(written.accepted)
        self.assertEqual(client.real_calls, 0)
        self.assertEqual(client.double_calls, 1)
        self.assertEqual(client.episode_keys, [str(episode.idempotency_key)])

        confirmed = confirm_episode(
            client,
            graph_id="graph-1",
            episode=episode,
            expected_uuid=written.episode_uuid,
            index_timeout_seconds=1,
            sleep=lambda _: None,
        )
        self.assertEqual(confirmed.episode_uuid, written.episode_uuid)
        self.assertTrue(confirmed.processed)


if __name__ == "__main__":
    unittest.main()
