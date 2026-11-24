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
        self.assertIsNotNone(
            select_support_quote(
                quote,
                fact="武汉大学撤销对肖某某的处分。",
                action="撤销",
                source_label="武汉大学",
                target_label="肖某某",
            )
        )
        self.assertEqual(
            classify_claim_nature(
                action="撤销",
                fact="武汉大学撤销对肖某某的处分。",
                quote=quote,
                source_label="武汉大学",
            ),
            "author_recommendation",
        )




class ClaimNatureTests(unittest.TestCase):
    def test_report_recommendation_is_not_official_decision(self) -> None:
        from app.engines.business_semantics import classify_claim_nature

        self.assertEqual(
            classify_claim_nature(
                action="需要",
                fact="引入第三方匿名评审机制，对研究生学位论文进行抽检。",
                quote="引入第三方匿名评审机制，对研究生学位论文进行抽检。",
                source_label="杨景媛论文事件",
            ),
            "author_recommendation",
        )

    def test_explicit_past_decision_stays_official(self) -> None:
        from app.engines.business_semantics import classify_claim_nature

        self.assertEqual(
            classify_claim_nature(
                action="处分",
                fact="武汉大学对肖某某作出记过处分。",
                quote="武汉大学对肖某某作出记过处分。",
                source_label="武汉大学",
            ),
            "official_decision",
        )

    def test_recommendation_label_is_not_double_prefixed(self) -> None:
        from app.engines.business_semantics import recommendation_action_label

        self.assertEqual(
            recommendation_action_label("建议", "建议在B站进行直播沟通。"),
            "建议",
        )
        self.assertEqual(
            recommendation_action_label("需要", "引入第三方匿名评审机制。"),
            "建议引入",
        )


if __name__ == "__main__":
    unittest.main()


class ReadbackEdgeSemanticsTests(unittest.TestCase):
    def _materialize(self, text: str, nodes, edges):
        from app.services.worker import _materialize_readback_text

        positions = [[None, index, 1, None, None] for index in range(len(text))]
        document_id = "00000000-0000-4000-8000-0000000000d0"
        chunks = {
            "text-0001": {
                "key": "text-0001",
                "documentId": document_id,
                "text": text,
                "positions": positions,
            }
        }
        documents = {
            document_id: {
                "documentId": document_id,
                "text": text,
                "positions": positions,
            }
        }
        episode = SimpleNamespace(
            episode_uuid="ep", episode_key="text-0001"
        )
        return _materialize_readback_text(
            readback_nodes=nodes,
            readback_edges=edges,
            episode_uuids=["ep"],
            episodes=[episode],
            chunks_by_key=chunks,
            documents_by_id=documents,
            relation_labels={},
            entity_labels={},
            max_sources_per_item=5,
        )

    def test_platform_user_action_direction(self) -> None:
        text = "小红书成为女性用户“维权”和“避坑”的主要阵地。"
        nodes = [
            SimpleNamespace(
                uuid_="p", name="小红书", labels=["媒体/平台"],
                episode_uuids=["ep"],
            ),
            SimpleNamespace(
                uuid_="u", name="女性用户", labels=["群体"],
                episode_uuids=["ep"],
            ),
        ]
        edges = [
            SimpleNamespace(
                uuid_="e", source_node_uuid="p", target_node_uuid="u",
                name="维权", fact=text, episode_uuids=["ep"],
            )
        ]
        result_nodes, result_edges, _, _ = self._materialize(text, nodes, edges)
        labels = {node.key: node.label for node in result_nodes}
        edge = result_edges[0]
        self.assertEqual(labels[edge.source_key], "女性用户")
        self.assertEqual(labels[edge.target_key], "小红书")
        self.assertEqual(edge.edge_type, "platform_action")
        self.assertEqual(edge.label, "维权")

    def test_attributed_claim_keeps_quote_scope(self) -> None:
        text = "自媒体平台的分化加剧混乱，部分账号传播“某校副校长子女驾车逼停学生”等不实信息。"
        nodes = [
            SimpleNamespace(
                uuid_="p", name="自媒体平台", labels=["媒体/平台"],
                episode_uuids=["ep"],
            ),
            SimpleNamespace(
                uuid_="t", name="某校副校长子女驾车逼停学生",
                labels=["事件"], episode_uuids=["ep"],
            ),
        ]
        edges = [
            SimpleNamespace(
                uuid_="e", source_node_uuid="p", target_node_uuid="t",
                name="传播", fact=text, episode_uuids=["ep"],
            )
        ]
        result_nodes, result_edges, _, _ = self._materialize(text, nodes, edges)
        labels = {node.key: node.label for node in result_nodes}
        edge = result_edges[0]
        self.assertEqual(edge.edge_type, "attributed_claim")
        self.assertEqual(edge.label, "传播不实信息")
        self.assertEqual(labels[edge.source_key], "部分账号")
        self.assertIn("报告所称", labels[edge.target_key])

    def test_recommendation_not_decision(self) -> None:
        text = "论文事件需要整改。引入第三方匿名评审机制，对研究生学位论文进行抽检。"
        nodes = [
            SimpleNamespace(
                uuid_="e1", name="论文事件", labels=["事件"],
                episode_uuids=["ep"],
            ),
            SimpleNamespace(
                uuid_="d1", name="第三方匿名评审机制",
                labels=["决定/安排"], episode_uuids=["ep"],
            ),
        ]
        edges = [
            SimpleNamespace(
                uuid_="e2", source_node_uuid="e1", target_node_uuid="d1",
                name="需要", fact=text, episode_uuids=["ep"],
            )
        ]
        _, result_edges, _, _ = self._materialize(text, nodes, edges)
        edge = result_edges[0]
        self.assertEqual(edge.claim_nature, "author_recommendation")
        self.assertEqual(edge.edge_type, "recommendation")
        self.assertTrue(edge.label.startswith("建议"))


if __name__ == "__main__":
    unittest.main()
