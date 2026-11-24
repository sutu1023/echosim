"""角色与环境准备的纯单元测试：轮数、契约、选择器与画像适配器。"""

from __future__ import annotations

import json
import unittest
import uuid

from app.engines.environment_config import EnvironmentConfig
from app.engines.environment_contracts import (
    EnvironmentContractError,
    compute_rounds,
    profile_batches,
    validate_profile,
)
from app.engines.environment_model import (
    EnvironmentModelProfiler,
    EnvironmentProfileError,
    ProfileRole,
    StubEnvironmentProfiler,
    create_profiler,
)
from app.engines.environment_roles import select_roles
from app.engines.model import HttpResponse
from app.services.evidence_graph import (
    GraphEdge,
    GraphEvidence,
    GraphNode,
    GraphSnapshot,
)

DOC = uuid.UUID("00000000-0000-4000-8000-0000000000d1")
BUILD = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
SNAPSHOT = uuid.UUID("00000000-0000-4000-8000-0000000000c1")


def evidence(
    target_kind: str,
    target_key: str,
    index: int,
    quote: str,
    *,
    page_no: int | None = None,
) -> GraphEvidence:
    return GraphEvidence(
        target_kind=target_kind,
        target_key=target_key,
        evidence_index=index,
        document_id=DOC,
        chunk_index=1,
        source_start=0,
        source_end=max(1, len(quote)),
        quote=quote,
        page_no=page_no,
    )


def snapshot(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    evidence_items: list[GraphEvidence],
) -> GraphSnapshot:
    return GraphSnapshot(
        id=SNAPSHOT,
        build_id=BUILD,
        revision=1,
        complete=True,
        published=True,
        node_count=len(nodes),
        edge_count=len(edges),
        episode_count=1,
        created_at=None,  # type: ignore[arg-type]
        published_at=None,
        nodes=tuple(nodes),
        edges=tuple(edges),
        evidence=tuple(evidence_items),
    )


def chinese_snapshot() -> GraphSnapshot:
    nodes = [
        GraphNode("p1", "个人", "张同学", "material", None),
        GraphNode("g1", "群体", "某校学生群体", "material", None),
        GraphNode("u1", "高校", "某大学", "material", None),
        GraphNode("m1", "媒体机构", "某报社", "material", None),
        GraphNode("e1", "事件", "某争议事件", "material", None),
        GraphNode("c1", "待核实说法", "某传闻", "material", None),
        GraphNode("pl1", "平台", "某社交平台", "material", None),
        GraphNode("t1", "话题", "调整接驳班次", "material", None),
        GraphNode("l1", "地点", "某地", "material", None),
        GraphNode("o1", "其他主体", "校友李某", "material", None),
        GraphNode("x1", "个人", "无来源人物", "material", None),
        GraphNode("h1", "个人", "用户假设人物", "hypothesis", None),
    ]
    edges = [
        GraphEdge(
            "r1", "报道", "报道", "material", "某报社报道某大学。",
            "m1", "u1", "media_report",
        ),
        GraphEdge(
            "r2", "发生于", "发生于", "material", "某争议事件发生于某地。",
            "e1", "l1", "other_material",
        ),
        GraphEdge(
            "r3", "主张", "主张", "material", "张同学主张调整接驳班次。",
            "p1", "t1", "party_claim",
        ),
        GraphEdge(
            "r4", "支持", "支持", "material", "某校学生群体支持调整接驳班次。",
            "g1", "t1", "party_claim",
        ),
        GraphEdge(
            "r5", "传播", "传播", "material", "某社交平台传播某传闻。",
            "pl1", "c1", "other_material",
        ),
        GraphEdge(
            "r6", "提及", "提及", "material", "校友李某被材料提及。",
            "o1", "t1", None,
        ),
    ]
    items = [
        evidence("node", "p1", 1, "张同学是参与讨论的学生。"),
        evidence("node", "g1", 1, "某校学生群体关注接驳班次。"),
        evidence("node", "u1", 1, "某大学位于东门附近。"),
        evidence("node", "m1", 1, "某报社关注校园交通。"),
        evidence("node", "e1", 1, "某争议事件引发讨论。"),
        evidence("node", "c1", 1, "某传闻称班次将取消。"),
        evidence("node", "pl1", 1, "某社交平台出现相关讨论。"),
        evidence("node", "t1", 1, "调整接驳班次是讨论焦点。"),
        evidence("node", "l1", 1, "某地是事件发生地。"),
        evidence("node", "o1", 1, "校友李某参与了讨论。"),
        evidence("node", "h1", 1, "这是用户假设。"),
        evidence("edge", "r1", 1, "某报社报道某大学。"),
        evidence("edge", "r2", 1, "某争议事件发生于某地。"),
        evidence("edge", "r3", 1, "张同学主张调整接驳班次。"),
        evidence("edge", "r4", 1, "某校学生群体支持调整接驳班次。"),
        evidence("edge", "r5", 1, "某社交平台传播某传闻。"),
        evidence("edge", "r6", 1, "校友李某被材料提及。"),
    ]
    return snapshot(nodes, edges, items)


class RoundContractTests(unittest.TestCase):
    def test_default_is_72_rounds(self) -> None:
        settings = compute_rounds(
            duration_hours=72,
            minutes_per_round=60,
            rounds_mode="auto",
            manual_rounds=None,
        )
        self.assertEqual(settings.rounds, 72)
        self.assertEqual(settings.auto_rounds, 72)
        self.assertEqual(settings.rounds_mode, "auto")

    def test_manual_40_and_3(self) -> None:
        for value in (40, 3):
            with self.subTest(value=value):
                settings = compute_rounds(
                    duration_hours=72,
                    minutes_per_round=60,
                    rounds_mode="manual",
                    manual_rounds=value,
                )
                self.assertEqual(settings.rounds, value)
                self.assertEqual(settings.manual_rounds, value)
                self.assertEqual(settings.auto_rounds, 72)

    def test_invalid_values_have_stable_codes(self) -> None:
        cases = [
            ({"duration_hours": 0}, "duration_invalid"),
            ({"minutes_per_round": 0}, "rounds_invalid"),
            ({"rounds_mode": "manual", "manual_rounds": 0}, "rounds_invalid"),
            ({"rounds_mode": "manual", "manual_rounds": 1001}, "rounds_invalid"),
            (
                {"duration_hours": 2, "minutes_per_round": 90},
                "rounds_not_integral",
            ),
        ]
        for overrides, code in cases:
            with self.subTest(overrides=overrides):
                payload = {
                    "duration_hours": 72,
                    "minutes_per_round": 60,
                    "rounds_mode": "auto",
                    "manual_rounds": None,
                }
                payload.update(overrides)
                with self.assertRaises(EnvironmentContractError) as caught:
                    compute_rounds(**payload)
                self.assertEqual(caught.exception.code, code)

    def test_profile_batches_are_stable(self) -> None:
        keys = ["b", "a", "c", "d", "e"]
        self.assertEqual(
            profile_batches(keys, 2),
            [["a", "b"], ["c", "d"], ["e"]],
        )


class SelectorTests(unittest.TestCase):
    def test_chinese_snapshot_selects_roles_and_three_counterexamples(self) -> None:
        selection = select_roles(chinese_snapshot())
        names = {role.display_name for role in selection.roles}
        self.assertEqual(
            names,
            {"张同学", "某校学生群体", "某大学", "某报社", "校友李某"},
        )
        self.assertNotIn("某争议事件", names)
        self.assertNotIn("某传闻", names)
        self.assertNotIn("某社交平台", names)
        self.assertNotIn("无来源人物", names)
        self.assertNotIn("用户假设人物", names)
        reasons = {item.label: item.reason for item in selection.excluded}
        self.assertEqual(reasons["某争议事件"], "non_role_type")
        self.assertEqual(reasons["某传闻"], "non_role_type")
        self.assertEqual(reasons["某社交平台"], "platform_channel")
        self.assertEqual(reasons["无来源人物"], "missing_source")
        self.assertEqual(reasons["用户假设人物"], "origin_not_material")

    def test_university_only_at_edge_target_is_selected(self) -> None:
        selection = select_roles(chinese_snapshot())
        university = next(
            role for role in selection.roles if role.display_name == "某大学"
        )
        self.assertTrue(university.involved_topics)
        self.assertTrue(university.background["stated"])

    def test_node_quote_only_proves_mention(self) -> None:
        selection = select_roles(chinese_snapshot())
        university = next(
            role for role in selection.roles if role.display_name == "某大学"
        )
        self.assertEqual(
            university.mention["text"], "某大学位于东门附近。"
        )
        self.assertNotEqual(
            university.mention["text"], university.background["text"]
        )

    def test_expression_and_involvement_are_separate(self) -> None:
        selection = select_roles(chinese_snapshot())
        student = next(
            role for role in selection.roles if role.display_name == "张同学"
        )
        self.assertEqual(
            [item["text"] for item in student.expressed_topics],
            ["调整接驳班次"],
        )
        self.assertEqual(
            student.expressed_topics[0]["claimNature"], "party_claim"
        )
        self.assertTrue(student.expressed_topics[0]["claimNatureKnown"])

    def test_old_null_claim_nature_stays_unknown(self) -> None:
        selection = select_roles(chinese_snapshot())
        alumnus = next(
            role for role in selection.roles if role.display_name == "校友李某"
        )
        topics = alumnus.involved_topics + alumnus.expressed_topics
        self.assertTrue(topics)
        self.assertTrue(all(item["claimNature"] is None for item in topics))
        self.assertTrue(
            all(item["claimNatureKnown"] is False for item in topics)
        )

    def test_generic_type_binds_to_current_subject(self) -> None:
        nodes = [
            GraphNode("r", "其他主体", "林记者", "material", None),
            GraphNode("e", "事件", "讲师聘任活动", "material", None),
        ]
        edges = [
            GraphEdge(
                "e1", "参加", "参加", "material",
                "某大学聘请林记者担任讲师。", "r", "e", "other_material",
            )
        ]
        items = [
            evidence("node", "r", 1, "某大学聘请林记者担任讲师。"),
            evidence("node", "e", 1, "讲师聘任活动举行。"),
            evidence("edge", "e1", 1, "某大学聘请林记者担任讲师。"),
        ]
        selection = select_roles(snapshot(nodes, edges, items))
        reporter = next(
            role for role in selection.roles if role.display_name == "林记者"
        )
        self.assertEqual(reporter.role_type, "个人")
        self.assertNotEqual(reporter.role_type, "高校")

    def test_expression_does_not_borrow_other_actor(self) -> None:
        nodes = [
            GraphNode("w", "个人", "王同学", "material", None),
            GraphNode("m", "事件", "咨询会", "material", None),
            GraphNode("t", "话题", "调整安排", "material", None),
        ]
        quote = "王同学参加咨询会，主办方表示将调整安排。"
        edges = [
            GraphEdge(
                "e1", "参加", "参加", "material", quote,
                "w", "m", "party_claim",
            )
        ]
        items = [
            evidence("node", "w", 1, "王同学参加咨询会。"),
            evidence("node", "m", 1, "咨询会举行。"),
            evidence("node", "t", 1, "调整安排是讨论焦点。"),
            evidence("edge", "e1", 1, quote),
        ]
        selection = select_roles(snapshot(nodes, edges, items))
        student = next(
            role for role in selection.roles if role.display_name == "王同学"
        )
        self.assertEqual(student.expressed_topics, ())
        self.assertTrue(student.involved_topics)

    def test_old_english_snapshot_types(self) -> None:
        nodes = [
            GraphNode("a", "Person", "记者甲", "material", None),
            GraphNode("b", "Organization", "某协会", "material", None),
            GraphNode("c", "Event", "某事故", "material", None),
            GraphNode("d", "其他主体", "校友乙", "material", None),
        ]
        edges = [
            GraphEdge(
                "e1", "mentions", "提及", "material",
                "记者甲提及某协会。", "a", "b", "other_material",
            )
        ]
        items = [
            evidence("node", "a", 1, "记者甲参加讨论。"),
            evidence("node", "b", 1, "某协会发布说明。"),
            evidence("node", "c", 1, "某事故发生。"),
            evidence("node", "d", 1, "校友乙参加讨论。"),
            evidence("edge", "e1", 1, "记者甲提及某协会。"),
        ]
        selection = select_roles(snapshot(nodes, edges, items))
        names = {role.display_name for role in selection.roles}
        self.assertEqual(names, {"记者甲", "某协会", "校友乙"})
        self.assertNotIn("某事故", names)


def profile_role(key: str = "n:1", name: str = "张同学") -> ProfileRole:
    return ProfileRole(
        role_key=key,
        display_name=name,
        role_type="个人",
        mention="张同学是参与讨论的学生。",
        expressed_topics=("调整接驳班次",),
    )


def valid_profile(keys: list[str], question: str, *, first: bool = True) -> dict:
    ordered = sorted(keys)
    return {
        "roles": {
            key: {
                "motive": "参与讨论",
                "stance": "审慎",
                "activeHours": [9, 10],
                "postingFrequency": 1.0,
                "responseDelayMinutes": 10,
                "influenceWeight": 1.0,
                "platformKeys": ["platform_a", "platform_b"],
                "topics": ["模拟关注"],
                "orderIndex": ordered.index(key) + 1,
                "firstSpeaker": first and key == ordered[0],
                "firstTopic": question if first and key == ordered[0] else None,
            }
            for key in keys
        },
        "initialTopics": [question],
        "activation": {
            "order": ordered,
            "firstSpeakerRoles": [ordered[0]] if first else [],
            "firstSpeakerTopics": (
                [{"roleKey": ordered[0], "topic": question}] if first else []
            ),
        },
    }


class ProfileContractTests(unittest.TestCase):
    def test_valid_profile_and_unknown_role(self) -> None:
        valid = validate_profile(
            valid_profile(["n:1", "n:2"], "问题"),
            role_keys=["n:1", "n:2"],
            question="问题",
        )
        self.assertEqual(valid.order, ("n:1", "n:2"))
        bad = valid_profile(["n:1", "n:9"], "问题")
        with self.assertRaises(EnvironmentContractError) as caught:
            validate_profile(bad, role_keys=["n:1", "n:2"], question="问题")
        self.assertEqual(caught.exception.code, "invalid_model_output")

    def test_unknown_fields_and_material_rewrite_are_rejected(self) -> None:
        raw = valid_profile(["n:1"], "问题")
        raw["roles"]["n:1"]["displayName"] = "被改写"
        with self.assertRaises(EnvironmentContractError):
            validate_profile(raw, role_keys=["n:1"], question="问题")
        raw = valid_profile(["n:1"], "问题")
        raw["materialFacts"] = ["改写"]
        with self.assertRaises(EnvironmentContractError):
            validate_profile(raw, role_keys=["n:1"], question="问题")

    def test_invalid_hours_order_and_first_topic(self) -> None:
        raw = valid_profile(["n:1"], "问题")
        raw["roles"]["n:1"]["activeHours"] = [25]
        with self.assertRaises(EnvironmentContractError):
            validate_profile(raw, role_keys=["n:1"], question="问题")
        raw = valid_profile(["n:1", "n:2"], "问题")
        raw["activation"]["order"] = ["n:1"]
        with self.assertRaises(EnvironmentContractError):
            validate_profile(raw, role_keys=["n:1", "n:2"], question="问题")
        raw = valid_profile(["n:1"], "问题")
        raw["activation"]["firstSpeakerTopics"] = []
        with self.assertRaises(EnvironmentContractError):
            validate_profile(raw, role_keys=["n:1"], question="问题")
        raw = valid_profile(["n:1"], "问题")
        raw["activation"]["order"] = ["n:1"]
        raw["activation"]["firstSpeakerRoles"] = []
        with self.assertRaises(EnvironmentContractError):
            validate_profile(raw, role_keys=["n:1"], question="问题")


class StubProfilerTests(unittest.TestCase):
    def test_same_input_is_stable_and_zero_real_calls(self) -> None:
        profiler = StubEnvironmentProfiler()
        roles = [profile_role("n:1"), profile_role("n:2", "某大学")]
        first = profiler.profile(
            question="问题", roles=roles, require_first_speaker=True
        )
        second = profiler.profile(
            question="问题", roles=roles, require_first_speaker=True
        )
        self.assertEqual(first.profile.roles, second.profile.roles)
        self.assertEqual(first.real_calls, 0)
        self.assertGreater(first.double_calls, 0)

    def test_config_rejects_stub_without_allow(self) -> None:
        config = EnvironmentConfig.from_env(
            {"ENVIRONMENT_MODEL_PROVIDER": "stub"}
        )
        with self.assertRaises(EnvironmentProfileError) as caught:
            create_profiler(config)
        self.assertEqual(caught.exception.code, "stub_not_allowed")

    def test_config_explicit_stub_has_zero_real_calls(self) -> None:
        config = EnvironmentConfig.from_env(
            {
                "ENVIRONMENT_MODEL_PROVIDER": "stub",
                "ENVIRONMENT_ALLOW_STUB": "true",
            }
        )
        profiler = create_profiler(config)
        outcome = profiler.profile(
            question="问题",
            roles=[profile_role()],
            require_first_speaker=True,
        )
        self.assertEqual(outcome.real_calls, 0)
        self.assertGreater(outcome.double_calls, 0)


class ModelProfilerTests(unittest.TestCase):
    def make(
        self, responses: list[HttpResponse]
    ) -> tuple[EnvironmentModelProfiler, list[dict]]:
        calls: list[dict] = []

        def transport(url, headers, payload, timeout):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "payload": payload,
                    "timeout": timeout,
                }
            )
            return responses[min(len(calls) - 1, len(responses) - 1)]

        profiler = EnvironmentModelProfiler(
            base_url="https://model.test.local/v1",
            api_key="test-key",
            model_name="test-model",
            timeout_seconds=5,
            transport=transport,
            clock=lambda: 0.0,
        )
        return profiler, calls

    def body(self, raw: dict) -> HttpResponse:
        return HttpResponse(
            200,
            json.dumps(
                {"choices": [{"message": {"content": json.dumps(raw)}}]},
                ensure_ascii=False,
            ),
        )

    def test_normal_call_uses_user_agent_and_json_object(self) -> None:
        profiler, calls = self.make([self.body(valid_profile(["n:1"], "问题"))])
        outcome = profiler.profile(
            question="问题",
            roles=[profile_role()],
            require_first_speaker=True,
        )
        self.assertEqual(outcome.real_calls, 1)
        self.assertEqual(calls[0]["url"], "https://model.test.local/v1/chat/completions")
        self.assertEqual(
            calls[0]["payload"]["response_format"], {"type": "json_object"}
        )
        self.assertIn("本次推演问题：问题", calls[0]["payload"]["messages"][0]["content"])

    def test_unsupported_response_format_retries_once(self) -> None:
        profiler, calls = self.make(
            [
                HttpResponse(
                    400,
                    '{"error":{"message":"response_format is not supported"}}',
                ),
                self.body(valid_profile(["n:1"], "问题")),
            ]
        )
        outcome = profiler.profile(
            question="问题",
            roles=[profile_role()],
            require_first_speaker=True,
        )
        self.assertEqual(outcome.real_calls, 2)
        self.assertIn("response_format", calls[0]["payload"])
        self.assertNotIn("response_format", calls[1]["payload"])

    def test_failures_are_classified_without_extra_retries(self) -> None:
        cases = [
            (HttpResponse(401, "{}"), "auth_failed"),
            (HttpResponse(402, "{}"), "billing_required"),
            (HttpResponse(403, "{}"), "auth_failed"),
            (HttpResponse(0, "", "TimeoutError"), "model_unavailable"),
        ]
        for response, code in cases:
            with self.subTest(code=code):
                profiler, calls = self.make([response])
                with self.assertRaises(EnvironmentProfileError) as caught:
                    profiler.profile(
                        question="问题",
                        roles=[profile_role()],
                        require_first_speaker=True,
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertLessEqual(len(calls), 2)

    def test_invalid_json_and_contract_violations(self) -> None:
        profiler, _ = self.make([HttpResponse(200, "not json")])
        with self.assertRaises(EnvironmentProfileError) as caught:
            profiler.profile(
                question="问题",
                roles=[profile_role()],
                require_first_speaker=True,
            )
        self.assertEqual(caught.exception.code, "invalid_model_output")

        raw = valid_profile(["n:1"], "问题")
        raw["roles"]["n:1"]["label"] = "被改写"
        profiler, _ = self.make([self.body(raw)])
        with self.assertRaises(EnvironmentProfileError) as caught:
            profiler.profile(
                question="问题",
                roles=[profile_role()],
                require_first_speaker=True,
            )
        self.assertEqual(caught.exception.code, "invalid_model_output")


if __name__ == "__main__":
    unittest.main()
