"""业务模型规划与分批抽取：请求边界、类型计划与稳定错误。"""

from __future__ import annotations

import json
import unittest
from typing import Any

from app.engines.business_contracts import ORIGIN_HYPOTHESIS, ORIGIN_MATERIAL
from app.engines.business_model import (
    BusinessModelError,
    BusinessModelPlanner,
    ChunkInput,
)
from app.engines.model import HttpResponse

CHUNK = ChunkInput(
    document_id="00000000-0000-4000-8000-000000000001",
    document_name="原创接驳车材料",
    chunk_index=1,
    text="校园接驳车在雨天把发车间隔调整为十分钟。",
    source_start=0,
    source_end=21,
    paragraph_start=1,
    paragraph_end=1,
)
CHUNK_B = ChunkInput(
    document_id="00000000-0000-4000-8000-000000000002",
    document_name="原创图书馆材料",
    chunk_index=1,
    text="图书馆在考试周延长开放到晚上十点。",
    source_start=0,
    source_end=18,
    paragraph_start=1,
    paragraph_end=1,
)
UNSELECTED = ChunkInput(
    document_id="00000000-0000-4000-8000-000000000099",
    document_name="未选材料",
    chunk_index=1,
    text="这段未选材料不应进入请求。",
    source_start=0,
    source_end=14,
    paragraph_start=1,
    paragraph_end=1,
)
QUESTION = "如果雨天增加一班备用车，会怎样？"


def plan_response() -> dict[str, Any]:
    return {
        "entityTypes": [
            {"key": "entity", "name": "实体"},
        ],
        "relationTypes": [
            {
                "key": "relation",
                "name": "关系",
                "sourceType": "entity",
                "targetType": "entity",
            }
        ],
        "batches": [
            {
                "key": "batch-1",
                "ordinal": 1,
                "chunks": [
                    {
                        "documentId": CHUNK.document_id,
                        "chunkIndex": CHUNK.chunk_index,
                    }
                ],
            }
        ],
    }


def source(**overrides: Any) -> dict[str, Any]:
    value = {
        "documentId": CHUNK.document_id,
        "chunkIndex": CHUNK.chunk_index,
        "quote": "校园接驳车",
        "sourceStart": 0,
        "sourceEnd": 5,
        "paragraphStart": 1,
        "paragraphEnd": 1,
    }
    value.update(overrides)
    return value


def extraction_response(
    *,
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "batchKey": "batch-1",
        "nodes": nodes
        if nodes is not None
        else [
            {
                "key": "node-1",
                "nodeType": "entity",
                "label": "接驳车",
                "origin": ORIGIN_MATERIAL,
                "summary": "材料陈述",
                "sources": [source()],
            },
            {
                "key": "node-2",
                "nodeType": "entity",
                "label": "雨天备用车",
                "origin": ORIGIN_HYPOTHESIS,
                "summary": "问题假设",
                "sources": [],
            },
        ],
        "edges": edges
        if edges is not None
        else [
            {
                "key": "edge-1",
                "edgeType": "relation",
                "label": "增加备用车",
                "origin": ORIGIN_MATERIAL,
                "sourceKey": "node-1",
                "targetKey": "node-2",
                "summary": "材料关系",
                "sources": [source()],
            }
        ],
    }


def response_for(payload: dict[str, Any]) -> HttpResponse:
    body = {
        "choices": [
            {"message": {"content": json.dumps(payload, ensure_ascii=False)}}
        ]
    }
    return HttpResponse(200, json.dumps(body, ensure_ascii=False))


class FakeTransport:
    def __init__(self, plan: dict[str, Any], extracts: list[dict[str, Any]]) -> None:
        self.plan = plan
        self.extracts = extracts
        self.payloads: list[dict[str, Any]] = []

    def __call__(
        self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> HttpResponse:
        del url, headers, timeout
        self.payloads.append(payload)
        if len(self.payloads) == 1:
            return response_for(self.plan)
        index = len(self.payloads) - 2
        return response_for(self.extracts[index])


def planner(transport: FakeTransport) -> BusinessModelPlanner:
    return BusinessModelPlanner(
        model_api_base_url="https://model.test.local/v1",
        model_api_key="test-key",
        model_name="test-model",
        timeout_seconds=5,
        transport=transport,
        call_kind="double",
        sleep=lambda _: None,
    )


class RawTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def __call__(
        self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> HttpResponse:
        del url, headers, payload, timeout
        self.calls += 1
        index = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[index]


def raw_planner(transport: RawTransport, timeout: float = 5) -> BusinessModelPlanner:
    return BusinessModelPlanner(
        model_api_base_url="https://model.test.local/v1",
        model_api_key="test-key",
        model_name="test-model",
        timeout_seconds=timeout,
        transport=transport,
        call_kind="double",
        sleep=lambda _: None,
    )


class BusinessModelPlannerTests(unittest.TestCase):
    def test_request_contains_only_selected_chunks_and_question(self) -> None:
        transport = FakeTransport(plan_response(), [extraction_response()])
        result = planner(transport).plan_and_extract(
            question=QUESTION, chunks=[CHUNK]
        )

        self.assertEqual(result.model_requests, 2)
        plan_text = str(transport.payloads[0]["messages"][0]["content"])
        extract_text = str(transport.payloads[1]["messages"][0]["content"])
        for text in (plan_text, extract_text):
            self.assertIn(CHUNK.text, text)
            self.assertIn(QUESTION, text)
            self.assertNotIn(UNSELECTED.text, text)
            self.assertNotIn(UNSELECTED.document_id, text)
            self.assertNotIn("private-inputs", text)
            self.assertNotIn("acceptance-material", text)
        self.assertEqual(result.graph.nodes[0].origin, ORIGIN_MATERIAL)
        self.assertTrue(result.graph.nodes[0].sources)
        self.assertEqual(result.graph.nodes[1].origin, ORIGIN_HYPOTHESIS)
        self.assertFalse(result.graph.nodes[1].sources)
        self.assertEqual(result.graph.edges[0].sources[0].document_id, CHUNK.document_id)

    def test_plan_can_contain_only_types_and_batches(self) -> None:
        transport = FakeTransport(plan_response(), [extraction_response()])
        result = planner(transport).plan_and_extract(
            question=QUESTION, chunks=[CHUNK]
        )

        self.assertEqual(len(result.entity_types), 1)
        self.assertEqual(len(result.relation_types), 1)
        self.assertEqual(len(result.batches), 1)

    def test_final_graph_requires_material_node_and_relation(self) -> None:
        transport = FakeTransport(
            plan_response(),
            [
                extraction_response(
                    nodes=[
                        {
                            "key": "node-1",
                            "nodeType": "entity",
                            "label": "用户假设",
                            "origin": ORIGIN_HYPOTHESIS,
                            "sources": [],
                        },
                        {
                            "key": "node-2",
                            "nodeType": "entity",
                            "label": "另一个假设",
                            "origin": ORIGIN_HYPOTHESIS,
                            "sources": [],
                        },
                    ],
                    edges=[
                        {
                            "key": "edge-1",
                            "edgeType": "relation",
                            "label": "假设关系",
                            "origin": ORIGIN_HYPOTHESIS,
                            "sourceKey": "node-1",
                            "targetKey": "node-2",
                            "sources": [],
                        }
                    ],
                )
            ],
        )

        with self.assertRaises(BusinessModelError) as caught:
            planner(transport).plan_and_extract(question=QUESTION, chunks=[CHUNK])
        self.assertEqual(caught.exception.code, "insufficient_graph")

    def test_unknown_node_type_is_rejected(self) -> None:
        extract = extraction_response()
        extract["nodes"][0]["nodeType"] = "unknown"
        with self.assertRaises(BusinessModelError) as caught:
            planner(FakeTransport(plan_response(), [extract])).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "invalid_model_output")

    def test_wrong_field_type_is_rejected(self) -> None:
        extract = extraction_response()
        extract["nodes"][0]["key"] = 123
        with self.assertRaises(BusinessModelError) as caught:
            planner(FakeTransport(plan_response(), [extract])).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "invalid_model_output")

    def test_unselected_material_is_rejected(self) -> None:
        extract = extraction_response()
        extract["nodes"][0]["sources"] = [source(documentId=UNSELECTED.document_id)]
        with self.assertRaises(BusinessModelError) as caught:
            planner(FakeTransport(plan_response(), [extract])).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "invalid_graph_scope")

    def test_dangling_endpoint_is_rejected(self) -> None:
        extract = extraction_response()
        extract["edges"][0]["targetKey"] = "missing"
        with self.assertRaises(BusinessModelError) as caught:
            planner(FakeTransport(plan_response(), [extract])).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "invalid_graph_response")

    def test_edge_endpoint_types_must_match_relation_plan(self) -> None:
        plan = plan_response()
        plan["entityTypes"].append({"key": "detail", "name": "细节"})
        plan["relationTypes"][0]["targetType"] = "detail"
        extract = extraction_response()
        extract["nodes"][0]["nodeType"] = "entity"
        extract["nodes"][1]["nodeType"] = "entity"
        with self.assertRaises(BusinessModelError) as caught:
            planner(FakeTransport(plan, [extract])).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "invalid_model_output")

    def test_material_statement_without_source_is_rejected(self) -> None:
        extract = extraction_response()
        extract["nodes"][0]["sources"] = []
        with self.assertRaises(BusinessModelError) as caught:
            planner(FakeTransport(plan_response(), [extract])).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "source_mismatch")

    def test_relation_plan_endpoint_type_must_exist(self) -> None:
        plan = plan_response()
        plan["relationTypes"][0]["targetType"] = "missing"
        with self.assertRaises(BusinessModelError) as caught:
            planner(FakeTransport(plan, [extraction_response()])).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "invalid_model_output")

    def test_auth_failure_is_classified(self) -> None:
        transport = RawTransport([HttpResponse(401, "unauthorized")])
        with self.assertRaises(BusinessModelError) as caught:
            raw_planner(transport).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "auth_failed")

    def test_network_budget_exhaustion_is_model_unavailable(self) -> None:
        transport = RawTransport(
            [
                HttpResponse(0, "", "timeout"),
                HttpResponse(0, "", "timeout"),
            ]
        )
        with self.assertRaises(BusinessModelError) as caught:
            raw_planner(transport).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "model_unavailable")
        self.assertEqual(transport.calls, 2)

    def test_zero_budget_fails_before_transport(self) -> None:
        transport = RawTransport([HttpResponse(200, "{}")])
        with self.assertRaises(BusinessModelError) as caught:
            raw_planner(transport, timeout=0).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "model_unavailable")
        self.assertEqual(transport.calls, 0)

    def test_source_must_belong_to_call_batch_refs(self) -> None:
        plan = plan_response()
        plan["batches"] = [
            {
                "key": "batch-a",
                "ordinal": 1,
                "chunks": [
                    {
                        "documentId": CHUNK.document_id,
                        "chunkIndex": CHUNK.chunk_index,
                    }
                ],
            },
            {
                "key": "batch-b",
                "ordinal": 2,
                "chunks": [
                    {
                        "documentId": CHUNK_B.document_id,
                        "chunkIndex": CHUNK_B.chunk_index,
                    }
                ],
            },
        ]
        extract_a = extraction_response()
        extract_a["batchKey"] = "batch-a"
        extract_a["nodes"][0]["sources"] = [
            source(
                documentId=CHUNK_B.document_id,
                chunkIndex=CHUNK_B.chunk_index,
            )
        ]
        extract_b = {
            "batchKey": "batch-b",
            "nodes": [
                {
                    "key": "node-b",
                    "nodeType": "entity",
                    "label": "图书馆",
                    "origin": ORIGIN_MATERIAL,
                    "sources": [
                        source(
                            documentId=CHUNK_B.document_id,
                            chunkIndex=CHUNK_B.chunk_index,
                            quote="图书馆",
                            sourceStart=0,
                            sourceEnd=3,
                        )
                    ],
                }
            ],
            "edges": [],
        }
        with self.assertRaises(BusinessModelError) as caught:
            planner(
                FakeTransport(plan, [extract_a, extract_b])
            ).plan_and_extract(
                question=QUESTION, chunks=[CHUNK, CHUNK_B]
            )
        self.assertEqual(caught.exception.code, "invalid_graph_scope")

    def test_batch_must_cover_selected_chunks(self) -> None:
        plan = plan_response()
        plan["batches"][0]["chunks"] = []
        with self.assertRaises(BusinessModelError) as caught:
            planner(FakeTransport(plan, [extraction_response()])).plan_and_extract(
                question=QUESTION, chunks=[CHUNK]
            )
        self.assertEqual(caught.exception.code, "invalid_model_output")


if __name__ == "__main__":
    unittest.main()
