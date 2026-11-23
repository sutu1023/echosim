"""业务建图纯契约：合法图、结构错误、来源与数量边界。"""

from __future__ import annotations

import unittest

from app.engines.business_config import (
    DEFAULT_EVIDENCE_GRAPH_MAX_EDGES,
    DEFAULT_EVIDENCE_GRAPH_MAX_NODES,
)
from app.engines.business_contracts import (
    EntityType,
    ExtractionBatch,
    GraphContractError,
    GraphEdge,
    GraphNode,
    GraphResult,
    ORIGIN_HYPOTHESIS,
    ORIGIN_MATERIAL,
    RelationType,
    SourceRef,
    validate_graph_result,
)


def source(index: int = 1, quote: str = "EchoSim 原创测试材料") -> SourceRef:
    return SourceRef(
        document_id="00000000-0000-4000-8000-000000000001",
        chunk_index=index,
        quote=quote,
        source_start=0,
        source_end=len(quote),
        paragraph_start=1,
        paragraph_end=1,
    )


def material_node(
    key: str = "node-1",
    *,
    sources: tuple[SourceRef, ...] | None = None,
) -> GraphNode:
    return GraphNode(
        key=key,
        node_type="entity",
        label="校园接驳车",
        origin=ORIGIN_MATERIAL,
        summary="原创材料中的接驳车",
        sources=(source(),) if sources is None else sources,
    )


def hypothesis_node(key: str = "node-h") -> GraphNode:
    return GraphNode(
        key=key,
        node_type="assumption",
        label="用户假设",
        origin=ORIGIN_HYPOTHESIS,
        summary="来自情景问题",
    )


def material_edge(
    key: str = "edge-1",
    *,
    source_key: str = "node-1",
    target_key: str = "node-2",
    sources: tuple[SourceRef, ...] | None = None,
) -> GraphEdge:
    return GraphEdge(
        key=key,
        edge_type="relation",
        label="调整发车间隔",
        origin=ORIGIN_MATERIAL,
        source_key=source_key,
        target_key=target_key,
        summary="原创材料中的关系",
        sources=(source(),) if sources is None else sources,
    )


def legal_result() -> GraphResult:
    return GraphResult(
        entity_types=(EntityType("entity", "实体"),),
        relation_types=(
            RelationType("relation", "关系", "entity", "entity"),
        ),
        batches=(
            ExtractionBatch(
                key="batch-1",
                ordinal=1,
                document_ids=("00000000-0000-4000-8000-000000000001",),
            ),
        ),
        nodes=(material_node(), hypothesis_node("node-2")),
        edges=(material_edge(),),
    )


class GraphContractTests(unittest.TestCase):
    def test_legal_graph_passes(self) -> None:
        validate_graph_result(legal_result())

    def test_empty_graph_is_rejected(self) -> None:
        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(GraphResult())

        self.assertEqual(caught.exception.code, "insufficient_graph")

    def test_duplicate_node_key_is_rejected(self) -> None:
        result = GraphResult(
            nodes=(material_node(), material_node()),
            edges=(material_edge(target_key="node-1"),),
        )

        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(result)

        self.assertEqual(caught.exception.code, "invalid_graph_response")

    def test_duplicate_edge_key_is_rejected(self) -> None:
        result = GraphResult(
            nodes=(material_node(), hypothesis_node("node-2")),
            edges=(material_edge(), material_edge()),
        )

        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(result)

        self.assertEqual(caught.exception.code, "invalid_graph_response")

    def test_dangling_endpoint_is_rejected(self) -> None:
        result = GraphResult(
            nodes=(material_node(),),
            edges=(material_edge(source_key="missing", target_key="node-1"),),
        )

        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(result)

        self.assertEqual(caught.exception.code, "invalid_graph_response")

    def test_invalid_origin_is_rejected(self) -> None:
        result = GraphResult(
            nodes=(
                GraphNode(
                    key="node-1",
                    node_type="entity",
                    label="非法来源",
                    origin="external",
                    sources=(source(),),
                ),
            ),
            edges=(material_edge(target_key="node-1"),),
        )

        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(result)

        self.assertEqual(caught.exception.code, "invalid_graph_response")

    def test_material_node_without_source_is_rejected(self) -> None:
        result = GraphResult(
            nodes=(material_node(sources=()), hypothesis_node("node-2")),
            edges=(material_edge(),),
        )

        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(result)

        self.assertEqual(caught.exception.code, "source_mismatch")

    def test_material_edge_without_source_is_rejected(self) -> None:
        result = GraphResult(
            nodes=(material_node(), hypothesis_node("node-2")),
            edges=(material_edge(sources=()),),
        )

        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(result)

        self.assertEqual(caught.exception.code, "source_mismatch")

    def test_hypothesis_without_source_is_allowed(self) -> None:
        validate_graph_result(legal_result())

    def test_hypothesis_with_invalid_source_is_rejected(self) -> None:
        bad = GraphNode(
            key="node-h",
            node_type="assumption",
            label="用户假设",
            origin=ORIGIN_HYPOTHESIS,
            sources=(
                SourceRef(
                    document_id="doc-1",
                    chunk_index=0,
                    quote="",
                    source_start=5,
                    source_end=5,
                ),
            ),
        )
        result = GraphResult(
            nodes=(material_node(), bad),
            edges=(material_edge(target_key="node-h"),),
        )

        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(result)

        self.assertEqual(caught.exception.code, "source_mismatch")

    def test_too_many_sources_is_rejected(self) -> None:
        too_many = tuple(source(index) for index in range(1, 7))
        result = GraphResult(
            nodes=(material_node(sources=too_many), hypothesis_node("node-2")),
            edges=(material_edge(),),
        )

        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(result)

        self.assertEqual(caught.exception.code, "too_many_sources")

    def test_node_limit_boundary(self) -> None:
        nodes = [material_node()]
        nodes.extend(
            hypothesis_node(f"node-{index}")
            for index in range(2, DEFAULT_EVIDENCE_GRAPH_MAX_NODES + 1)
        )
        self.assertEqual(len(nodes), DEFAULT_EVIDENCE_GRAPH_MAX_NODES)
        result = GraphResult(
            nodes=tuple(nodes),
            edges=(material_edge(target_key="node-2"),),
        )
        validate_graph_result(result)

        over = GraphResult(
            nodes=tuple(
                nodes
                + [
                    hypothesis_node(
                        f"node-{DEFAULT_EVIDENCE_GRAPH_MAX_NODES + 1}"
                    )
                ]
            ),
            edges=(material_edge(target_key="node-2"),),
        )
        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(over)
        self.assertEqual(caught.exception.code, "graph_too_large")

    def test_edge_limit_boundary(self) -> None:
        edges = [material_edge(target_key="node-2")]
        edges.extend(
            GraphEdge(
                key=f"edge-{index}",
                edge_type="relation",
                label="用户假设关系",
                origin=ORIGIN_HYPOTHESIS,
                source_key="node-1",
                target_key="node-2",
            )
            for index in range(2, DEFAULT_EVIDENCE_GRAPH_MAX_EDGES)
        )
        self.assertEqual(len(edges), DEFAULT_EVIDENCE_GRAPH_MAX_EDGES - 1)
        edges.append(
            GraphEdge(
                key=f"edge-{DEFAULT_EVIDENCE_GRAPH_MAX_EDGES}",
                edge_type="relation",
                label="用户假设关系",
                origin=ORIGIN_HYPOTHESIS,
                source_key="node-1",
                target_key="node-2",
            )
        )
        result = GraphResult(
            nodes=(material_node(), hypothesis_node("node-2")),
            edges=tuple(edges),
        )
        validate_graph_result(result)

        over = GraphResult(
            nodes=result.nodes,
            edges=tuple(
                edges
                + [
                    GraphEdge(
                        key=f"edge-{DEFAULT_EVIDENCE_GRAPH_MAX_EDGES + 1}",
                        edge_type="relation",
                        label="用户假设关系",
                        origin=ORIGIN_HYPOTHESIS,
                        source_key="node-1",
                        target_key="node-2",
                    )
                ]
            ),
        )
        with self.assertRaises(GraphContractError) as caught:
            validate_graph_result(over)
        self.assertEqual(caught.exception.code, "graph_too_large")

    def test_source_limit_boundary(self) -> None:
        five = tuple(source(index) for index in range(1, 6))
        result = GraphResult(
            nodes=(material_node(sources=five), hypothesis_node("node-2")),
            edges=(material_edge(),),
        )
        validate_graph_result(result)


if __name__ == "__main__":
    unittest.main()
