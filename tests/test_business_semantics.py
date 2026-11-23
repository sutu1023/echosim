"""语义层：主体别名、跨页归一、来源性质与读回映射。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.engines.business_contracts import (
    EntityType,
    ExtractionBatch,
    GraphContractError,
    GraphEdge,
    GraphNode,
    RelationType,
    SourceRef,
    Subject,
)
from app.engines.business_graph import map_material_relations
from app.engines.business_model import BusinessModelPlanner, ChunkInput
from app.engines.business_semantics import (
    consolidate,
    representative_sources,
    validate_claim_nature,
)


def chunk(index: int, text: str, page: int) -> ChunkInput:
    return ChunkInput(
        document_id="00000000-0000-4000-8000-000000000001",
        document_name="原创校园材料.txt",
        chunk_index=index,
        text=text,
        source_start=0,
        source_end=len(text),
        page_no=page,
        paragraph_start=1,
        paragraph_end=1,
    )


def source(index: int, text: str, page: int) -> SourceRef:
    return SourceRef(
        document_id="00000000-0000-4000-8000-000000000001",
        chunk_index=index,
        quote=text,
        source_start=0,
        source_end=len(text),
        page_no=page,
        paragraph_start=1,
        paragraph_end=1,
    )


def planner() -> BusinessModelPlanner:
    return BusinessModelPlanner(
        model_api_base_url="https://model.test.local/v1",
        model_api_key="test-key",
        model_name="test-model",
        timeout_seconds=5,
        transport=lambda *_: None,  # type: ignore[arg-type]
        call_kind="double",
    )


class SubjectPlanTests(unittest.TestCase):
    def test_full_and_short_name_are_one_subject(self) -> None:
        first = chunk(1, "武汉大学发布决定。", 2)
        second = chunk(2, "武大回应了该决定。", 9)
        plan = {
            "entityTypes": [{"key": "org", "name": "机构"}],
            "relationTypes": [
                {
                    "key": "act",
                    "name": "发布决定",
                    "sourceType": "org",
                    "targetType": "org",
                }
            ],
            "subjects": [
                {
                    "key": "subject-1",
                    "label": "武汉大学",
                    "typeKey": "org",
                    "aliases": ["武大"],
                    "evidence": [
                        {
                            "documentId": first.document_id,
                            "chunkIndex": 1,
                            "quote": "武汉大学",
                            "sourceStart": 0,
                            "sourceEnd": 4,
                            "pageNo": 2,
                            "paragraphStart": 1,
                            "paragraphEnd": 1,
                        },
                        {
                            "documentId": second.document_id,
                            "chunkIndex": 2,
                            "quote": "武大",
                            "sourceStart": 0,
                            "sourceEnd": 2,
                            "pageNo": 9,
                            "paragraphStart": 1,
                            "paragraphEnd": 1,
                        },
                    ],
                }
            ],
            "batches": [
                {
                    "key": "batch-1",
                    "ordinal": 1,
                    "chunks": [{"documentId": first.document_id, "chunkIndex": 1}],
                },
                {
                    "key": "batch-2",
                    "ordinal": 2,
                    "chunks": [{"documentId": second.document_id, "chunkIndex": 2}],
                },
            ],
        }
        selected = {
            (first.document_id, 1): first,
            (second.document_id, 2): second,
        }
        result = planner()._parse_plan(  # noqa: SLF001 - 纯解析契约测试
            plan,
            selected_keys=set(selected),
            selected_by_key=selected,
            settings=SimpleNamespace(max_nodes=200, max_sources_per_item=5),
        )
        self.assertEqual(result[3][0].label, "武汉大学")
        self.assertEqual(result[3][0].aliases, ("武大",))

    def test_official_decision_cannot_hide_attribution(self) -> None:
        with self.assertRaises(GraphContractError):
            validate_claim_nature("official_decision", "某方称已经增开班次。")
        validate_claim_nature("party_claim", "某方称已经增开班次。")
        validate_claim_nature("official_decision", "校务会决定增开班次。")


class ConsolidationTests(unittest.TestCase):
    def test_cross_page_subject_keeps_two_relations(self) -> None:
        subject = Subject(
            key="subject-1",
            label="武汉大学",
            type_key="org",
            aliases=("武大",),
            sources=(source(1, "武汉大学", 2), source(2, "武大", 9)),
        )
        batches = []
        for index, page in ((1, 2), (2, 9)):
            text = "武汉大学" if index == 1 else "武大"
            node = GraphNode(
                key=f"node-{index}",
                node_type="org",
                type_name="机构",
                label=text,
                origin="material",
                subject_key="subject-1",
                aliases=("武大",),
                sources=(source(index, text, page),),
            )
            detail = GraphNode(
                key=f"detail-{index}",
                node_type="matter",
                type_name="事项",
                label=f"决定 {index}",
                origin="material",
                sources=(source(index, f"{text}发布决定 {index}", page),),
            )
            edge = GraphEdge(
                key=f"edge-{index}",
                edge_type="act",
                edge_type_name="发布决定",
                claim_nature="official_decision",
                label="发布决定",
                origin="material",
                source_key=node.key,
                target_key=detail.key,
                sources=(source(index, f"{text}发布决定 {index}", page),),
            )
            batches.append(
                SimpleNamespace(
                    batch=ExtractionBatch(
                        key=f"batch-{index}",
                        ordinal=index,
                        document_ids=(subject.sources[0].document_id,),
                    ),
                    nodes=(node, detail),
                    edges=(edge,),
                )
            )
        graph = consolidate(
            subjects=(subject,),
            entity_types=(
                EntityType("org", "机构"),
                EntityType("matter", "事项"),
            ),
            extracted_batches=batches,
        )
        self.assertEqual(len([n for n in graph.nodes if n.subject_key]), 1)
        merged = next(n for n in graph.nodes if n.subject_key)
        self.assertEqual(merged.batch_keys, ("batch-1", "batch-2"))
        self.assertEqual(len(graph.edges), 2)

    def test_repeated_subject_uses_representative_sources(self) -> None:
        sources = [source(index, f"武大第{index}页", index) for index in range(1, 19)]
        picked = representative_sources(sources)
        self.assertEqual(len(picked), 5)
        self.assertEqual(len({item.page_no for item in picked}), 5)


    def test_non_media_claim_action_becomes_party_claim(self) -> None:
        subject = Subject(
            key="subject-1",
            label="杨景媛",
            type_key="person",
            sources=(source(1, "杨景媛", 7),),
        )
        target = Subject(
            key="subject-2",
            label="肖某某",
            type_key="person",
            sources=(source(1, "肖某某", 7),),
        )
        node = GraphNode(
            key="node-1",
            node_type="person",
            type_name="个人",
            label="杨景媛",
            origin="material",
            subject_key="subject-1",
            sources=(source(1, "杨景媛", 7),),
        )
        target_node = GraphNode(
            key="node-2",
            node_type="person",
            type_name="个人",
            label="肖某某",
            origin="material",
            subject_key="subject-2",
            sources=(source(1, "肖某某", 7),),
        )
        edge = GraphEdge(
            key="edge-1",
            edge_type="accuse",
            edge_type_name="指控",
            claim_nature="media_report",
            label="指控",
            origin="material",
            source_key="node-1",
            target_key="node-2",
            sources=(source(1, "杨景媛指控肖某某", 7),),
        )
        graph = consolidate(
            subjects=(subject, target),
            entity_types=(EntityType("person", "个人"),),
            extracted_batches=(
                SimpleNamespace(
                    batch=ExtractionBatch(
                        key="batch-1",
                        ordinal=1,
                        document_ids=("00000000-0000-4000-8000-000000000001",),
                    ),
                    nodes=(node, target_node),
                    edges=(edge,),
                ),
            ),
        )
        self.assertEqual(graph.edges[0].claim_nature, "party_claim")

    def test_unregistered_subject_is_not_published(self) -> None:
        node = GraphNode(
            key="node-1",
            node_type="org",
            type_name="机构",
            label="未登记主体",
            origin="material",
            sources=(source(1, "未登记主体", 1),),
        )
        detail = GraphNode(
            key="node-2",
            node_type="matter",
            type_name="事项",
            label="某事项",
            origin="material",
            sources=(source(1, "某事项", 1),),
        )
        edge = GraphEdge(
            key="edge-1",
            edge_type="act",
            edge_type_name="提及",
            claim_nature="other_material",
            label="提及",
            origin="material",
            source_key="node-1",
            target_key="node-2",
            sources=(source(1, "某事项", 1),),
        )
        graph = consolidate(
            subjects=(
                Subject(
                    key="subject-1",
                    label="已登记主体",
                    type_key="org",
                    sources=(source(1, "已登记主体", 1),),
                ),
            ),
            entity_types=(EntityType("org", "机构"), EntityType("matter", "事项")),
            extracted_batches=(
                SimpleNamespace(
                    batch=ExtractionBatch(
                        key="batch-1",
                        ordinal=1,
                        document_ids=("00000000-0000-4000-8000-000000000001",),
                    ),
                    nodes=(node, detail),
                    edges=(edge,),
                ),
            ),
        )
        self.assertEqual(graph.nodes, ())
        self.assertEqual(graph.edges, ())


class ReadbackMappingTests(unittest.TestCase):
    def test_short_name_and_cross_page_duplicate_are_disambiguated(self) -> None:
        manifest_nodes = [
            SimpleNamespace(
                key="subject-a",
                label="武汉大学",
                aliases=("武大",),
                batch_keys=("batch-1",),
            ),
            SimpleNamespace(
                key="subject-b",
                label="武汉大学",
                aliases=(),
                batch_keys=("batch-2",),
            ),
        ]
        manifest_edges = [
            GraphEdge(
                key="edge-a",
                edge_type="act",
                label="发布决定",
                claim_nature="official_decision",
                origin="material",
                source_key="subject-a",
                target_key="subject-a",
                sources=(source(1, "武汉大学发布决定", 2),),
            ),
            GraphEdge(
                key="edge-b",
                edge_type="act",
                label="作出回应",
                claim_nature="party_claim",
                origin="material",
                source_key="subject-b",
                target_key="subject-b",
                sources=(source(2, "武大表示", 9),),
            ),
        ]
        readback_nodes = [
            SimpleNamespace(
                uuid_="n1",
                name="武大",
                episode_uuids=["ep-1"],
            ),
            SimpleNamespace(
                uuid_="n2",
                name="武汉大学",
                episode_uuids=["ep-2"],
            ),
        ]
        readback_edges = [
            SimpleNamespace(
                uuid_="e1",
                fact="发布决定",
                name="发布决定",
                source_node_uuid="n1",
                target_node_uuid="n1",
                episode_uuids=["ep-1"],
                claim_nature="official_decision",
            ),
            SimpleNamespace(
                uuid_="e2",
                fact="作出回应",
                name="作出回应",
                source_node_uuid="n2",
                target_node_uuid="n2",
                episode_uuids=["ep-2"],
                claim_nature="party_claim",
            ),
        ]
        mapped = map_material_relations(
            manifest_nodes,
            manifest_edges,
            readback_nodes,
            readback_edges,
            episode_uuids=("ep-1", "ep-2"),
            episode_batch_keys={"ep-1": "batch-1", "ep-2": "batch-2"},
        )
        self.assertEqual(mapped["e1"].manifest_key, "edge-a")
        self.assertEqual(mapped["e2"].manifest_key, "edge-b")


if __name__ == "__main__":
    unittest.main()


class ReadbackQuoteTests(unittest.TestCase):
    def test_media_list_without_action_is_not_support(self) -> None:
        from app.engines.business_semantics import select_support_quote

        selected = select_support_quote(
            "微博、知乎、B站和抖音。",
            fact="微博报道了图书馆事件。",
            action="报道",
            source_label="微博",
            target_label="图书馆事件",
        )
        self.assertIsNone(selected)

    def test_support_quote_allows_adjacent_sentence(self) -> None:
        from app.engines.business_semantics import select_support_quote

        selected = select_support_quote(
            "武汉大学发布通报。图书馆事件随后得到回应。",
            fact="武汉大学回应图书馆事件。",
            action="回应",
            source_label="武汉大学",
            target_label="图书馆事件",
        )
        self.assertIsNotNone(selected)

    def test_suggested_revocation_is_not_official_decision(self) -> None:
        from app.engines.business_semantics import (
            classify_claim_nature,
            select_support_quote,
        )

        quote = "报告建议武汉大学撤销对肖某某的处分。"
        self.assertIsNone(
            select_support_quote(
                quote,
                fact="武汉大学撤销对肖某某的处分。",
                action="撤销",
                source_label="武汉大学",
                target_label="肖某某",
            )
        )
        self.assertNotEqual(
            classify_claim_nature(
                action="撤销",
                fact="武汉大学撤销对肖某某的处分。",
                quote=quote,
                source_label="武汉大学",
            ),
            "official_decision",
        )


if __name__ == "__main__":
    unittest.main()
