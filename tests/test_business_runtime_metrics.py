"""Provider 边界计数：替身调用累计为 double，真实计数保持 0。"""

from __future__ import annotations

import unittest

from app.engines.business_config import BusinessGraphConfig
from app.engines.business_runtime import (
    ProviderCallMetrics,
    create_graph_client,
    create_planner,
)
from app.engines.business_model import ChunkInput


class ProviderCallMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = BusinessGraphConfig.from_env(
            {
                "EVIDENCE_GRAPH_MODEL_PROVIDER": "stub",
                "EVIDENCE_GRAPH_GRAPH_PROVIDER": "stub",
                "EVIDENCE_GRAPH_ALLOW_STUB": "true",
            }
        )
        self.metrics = ProviderCallMetrics()
        self.chunks = [
            ChunkInput(
                document_id="00000000-0000-4000-8000-000000000001",
                document_name="原创材料.txt",
                chunk_index=1,
                text="原创校园接驳车材料。",
                source_start=0,
                source_end=10,
                paragraph_start=1,
                paragraph_end=1,
            )
        ]

    def test_model_calls_count_at_provider_boundary(self) -> None:
        planner = create_planner(
            self.config,
            model_api_base_url="",
            model_api_key="",
            model_name="",
            timeout_seconds=5,
            question="雨天如何调整接驳车？",
            chunks=self.chunks,
            metrics=self.metrics,
        )
        stage = planner.plan(
            question="雨天如何调整接驳车？",
            chunks=self.chunks,
            config=self.config,
        )
        self.assertGreaterEqual(self.metrics.model_double, 1)
        self.assertEqual(self.metrics.model_real, 0)
        planner.extract_batch(
            question="雨天如何调整接驳车？",
            entity_types=stage.entity_types,
            relation_types=stage.relation_types,
            batch=stage.batches[0],
            chunks=self.chunks,
            config=self.config,
        )
        self.assertGreaterEqual(self.metrics.model_double, 2)
        self.assertEqual(self.metrics.model_real, 0)

    def test_graph_calls_count_create_add_search_and_readback(self) -> None:
        client = create_graph_client(
            self.config,
            zep_api_key="",
            zep_api_base_url="",
            timeout_seconds=5,
            metrics=self.metrics,
        )
        graph = client.graph.create(
            name="原创图谱",
            description="验收计数",
            time_zone="UTC",
            idempotency_key="00000000-0000-4000-8000-000000000002",
        )
        graph_id = graph["uuid"]
        client.graph.episode.add(
            graph_id,
            data='{"nodes":[],"edges":[]}',
            metadata={"buildId": "b"},
            idempotency_key="00000000-0000-4000-8000-000000000003",
            type="text",
        )
        client.graph.search_episodes(graph_id, query="probe")
        client.graph.node.list(graph_id, page_size=10, filters={})
        client.graph.edge.list(graph_id, page_size=10, filters={})
        self.assertGreaterEqual(self.metrics.graph_double, 5)
        self.assertEqual(self.metrics.graph_real, 0)
        self.assertEqual(self.metrics.real, 0)


if __name__ == "__main__":
    unittest.main()
