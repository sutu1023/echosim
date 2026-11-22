"""在真实 Zep SDK + 模拟传输下验证分页读回签名与字段。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
import uuid
from typing import Any

from app.engines.business_contracts import GraphEdge, GraphNode, SourceRef
from app.engines.business_graph import (
    confirm_episode,
    readback_graph,
    write_episode,
)
from app.services import evidence_graph as store

HAS_ZEP = importlib.util.find_spec("zep_cloud") is not None and importlib.util.find_spec("httpx") is not None
SKIP_REASON = "未安装 zep-cloud/httpx，跳过真实 SDK 模拟传输测试"


@unittest.skipUnless(HAS_ZEP, SKIP_REASON)
class ZepSdkReadbackTests(unittest.TestCase):
    def test_real_sdk_signature_filters_and_multi_page_readback(self) -> None:
        import httpx
        from zep_cloud import Zep

        requests: list[dict[str, Any]] = []

        def node_page(cursor: str | None) -> dict[str, Any]:
            if cursor is None:
                return {
                    "items": [
                        {
                            "uuid": "n1",
                            "name": "接驳车",
                            "episode_uuids": ["ep-1", "ep-2"],
                            "episode_uuids_truncated": False,
                        }
                    ],
                    "next_cursor": "n-page-2",
                    "total_size": 2,
                }
            return {
                "items": [
                    {
                        "uuid": "n2",
                        "name": "备用车",
                        "episode_uuids": ["ep-2"],
                        "episode_uuids_truncated": False,
                    },
                    {
                        "uuid": "n1",
                        "name": "接驳车",
                        "episode_uuids": ["ep-1"],
                        "episode_uuids_truncated": False,
                    },
                ],
                "next_cursor": None,
                "total_size": 2,
            }

        def edge_page(cursor: str | None) -> dict[str, Any]:
            if cursor is None:
                return {
                    "items": [
                        {
                            "uuid": "e1",
                            "source_node_uuid": "n1",
                            "target_node_uuid": "n2",
                            "fact": "增加备用车",
                            "episode_uuids": ["ep-1"],
                        }
                    ],
                    "next_cursor": "e-page-2",
                    "total_size": 1,
                }
            return {
                "items": [
                    {
                        "uuid": "e1",
                        "source_node_uuid": "n1",
                        "target_node_uuid": "n2",
                        "fact": "增加备用车",
                        "episode_uuids": ["ep-1"],
                    }
                ],
                "next_cursor": None,
                "total_size": 1,
            }

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode("utf-8")
            requests.append(
                {
                    "path": request.url.path,
                    "query": request.url.query.decode("utf-8"),
                    "body": body,
                }
            )
            payload = json.loads(body or "{}")
            filters = payload.get("filters") or {}
            assert filters.get("episode_uuids") == ["ep-1", "ep-2"]
            cursor = request.url.params.get("cursor")
            if request.url.path.endswith("/nodes/list"):
                return httpx.Response(200, json=node_page(cursor))
            if request.url.path.endswith("/edges/list"):
                return httpx.Response(200, json=edge_page(cursor))
            return httpx.Response(404, json={"error": "unexpected"})

        client = Zep(
            api_key="test-key",
            base_url="https://zep.test.local/api/v4",
            httpx_client=httpx.Client(
                transport=httpx.MockTransport(handler)
            ),
            max_retries=0,
        )
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
            "node-2", "entity", "备用车", "hypothesis"
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

        result = readback_graph(
            client,
            graph_id="graph-1",
            episode_uuids=["ep-1", "ep-2"],
            processing_timeout_seconds=2,
            manifest_nodes=[node_a, node_b],
            manifest_edges=[edge],
            sleep=lambda _: None,
        )

        self.assertEqual(result.node_count, 2)
        self.assertEqual(result.edge_count, 1)
        self.assertEqual(result.relations["e1"].manifest_key, "edge-1")
        self.assertEqual(result.mapped_relation_count, 1)
        self.assertEqual(result.mapped_source_count, 1)
        self.assertGreaterEqual(len(requests), 4)
        self.assertTrue(any("cursor=" in item["query"] for item in requests))
        node_requests = [
            item for item in requests if item["path"].endswith("/nodes/list")
        ]
        self.assertTrue(node_requests)
        self.assertTrue(
            all(json.loads(item["body"])["filters"]["episode_uuids"] for item in node_requests)
        )


    def test_lost_write_response_does_not_trigger_sdk_retry(self) -> None:
        import httpx
        from zep_cloud import Zep

        content = "原创 episode 内容"
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        counters = {"write": 0, "search": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if (
                request.url.path.endswith("/episodes")
                and "/search/" not in request.url.path
                and request.method == "POST"
            ):
                counters["write"] += 1
                raise httpx.ReadTimeout("simulated response lost", request=request)
            if request.url.path.endswith("/search/episodes"):
                counters["search"] += 1
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "uuid": "ep-lost",
                                "content": content,
                                "metadata": {
                                    "buildId": str(episode.build_id),
                                    "episodeKey": episode.episode_key,
                                    "contentSha256": content_hash,
                                },
                                "processed": False,
                            }
                        ],
                        "next_cursor": None,
                        "total_size": 1,
                    },
                )
            return httpx.Response(404, json={"error": "unexpected"})

        client = Zep(
            api_key="test-key",
            base_url="https://zep.test.local/api/v4",
            httpx_client=httpx.Client(
                transport=httpx.MockTransport(handler)
            ),
            max_retries=0,
        )
        episode = store.GraphEpisode(
            build_id=uuid.uuid4(),
            episode_key="episode-1",
            idempotency_key=uuid.uuid4(),
            content=content,
            content_sha256=content_hash,
            episode_uuid=None,
            state="planned",
            attempts=0,
        )
        written = write_episode(
            client,
            graph_id="graph-lost",
            episode=episode,
            max_attempts=1,
            sleep=lambda _: None,
        )
        self.assertFalse(written.accepted)
        self.assertTrue(written.response_lost)
        self.assertEqual(counters["write"], 1)
        confirmation = confirm_episode(
            client,
            graph_id="graph-lost",
            episode=episode,
            expected_uuid=None,
            index_timeout_seconds=1,
            processing_timeout_seconds=1,
            require_processed=False,
            sleep=lambda _: None,
        )
        self.assertEqual(confirmation.episode_uuid, "ep-lost")
        self.assertEqual(counters["write"], 1)
        self.assertGreaterEqual(counters["search"], 1)


if __name__ == "__main__":
    unittest.main()
