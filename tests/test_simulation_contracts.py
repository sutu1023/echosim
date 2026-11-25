"""冻结选择解析与轮次规划：输入校验、首发顺序、动作覆盖与目标选择。"""

from __future__ import annotations

import unittest

from app.engines.duet_contracts import (
    DuetContractError,
    KnownPost,
    NATIVE_ACTIONS,
    PLATFORM_MAPPING,
    parse_selection,
    plan_round,
)

ROLE_IDS = [f"00000000-0000-4000-8000-00000000000{i}" for i in range(6)]


def role(index: int, *, hours=None, platforms=None) -> dict[str, object]:
    return {
        "roleId": ROLE_IDS[index],
        "displayName": f"角色{index}",
        "roleType": "个人",
        "simulation": {
            "activeHours": hours or list(range(24)),
            "postingFrequency": 1,
            "influenceWeight": 1,
            "responseDelayMinutes": 0,
            "platformKeys": platforms or ["platform_a", "platform_b"],
            "topics": [f"话题{index}"],
            "orderIndex": index,
        },
    }


def payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "selectionId": "11111111-1111-4111-8111-111111111111",
        "preparationId": "22222222-2222-4222-8222-222222222222",
        "projectId": "33333333-3333-4333-8333-333333333333",
        "buildId": "44444444-4444-4444-8444-444444444444",
        "snapshotId": "55555555-5555-4555-8555-555555555555",
        "question": "接驳班次是否应调整？",
        "settings": {"rounds": 3, "minutesPerRound": 60, "durationHours": 3},
        "roles": [role(index) for index in range(6)],
        "platforms": [
            {"platformKey": "platform_a", "nativePlatform": "twitter"},
            {"platformKey": "platform_b", "nativePlatform": "reddit"},
        ],
        "initialTopics": [
            {"topicId": "q", "text": "接驳班次是否应调整？", "origin": "question"},
            {"topicId": "t0", "text": "话题0", "origin": "simulation"},
        ],
        "activation": {
            "firstSpeakerRoleIds": [ROLE_IDS[0]],
            "firstSpeakerTopics": [{"roleId": ROLE_IDS[0], "topicId": "q"}],
            "order": list(ROLE_IDS),
        },
    }
    body.update(overrides)
    return body


class SelectionContractTests(unittest.TestCase):
    def test_parses_roles_and_first_speaker(self) -> None:
        plan = parse_selection(payload())
        self.assertEqual(len(plan.roles), 6)
        self.assertEqual(plan.order, tuple(ROLE_IDS))
        self.assertEqual(plan.first_speaker_role_ids, (ROLE_IDS[0],))
        self.assertEqual(plan.first_speaker_topics[ROLE_IDS[0]], "q")
        self.assertEqual(plan.rounds, 3)
        self.assertEqual(plan.start_hour, 0)

    def test_rejects_incomplete_payloads(self) -> None:
        cases = {
            "rounds": payload(settings={"rounds": 0, "minutesPerRound": 60, "durationHours": 3}),
            "no_roles": payload(roles=[]),
            "mapping": payload(
                platforms=[
                    {"platformKey": "platform_a", "nativePlatform": "reddit"},
                    {"platformKey": "platform_b", "nativePlatform": "twitter"},
                ]
            ),
            "prefix": payload(
                activation={
                    "firstSpeakerRoleIds": [ROLE_IDS[1]],
                    "firstSpeakerTopics": [{"roleId": ROLE_IDS[1], "topicId": "q"}],
                    "order": list(ROLE_IDS),
                }
            ),
            "topic": payload(
                activation={
                    "firstSpeakerRoleIds": [ROLE_IDS[0]],
                    "firstSpeakerTopics": [],
                    "order": list(ROLE_IDS),
                }
            ),
            "question": payload(
                initialTopics=[
                    {"topicId": "t0", "text": "话题0", "origin": "simulation"}
                ]
            ),
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(DuetContractError) as caught:
                    parse_selection(body)
                self.assertEqual(caught.exception.code, "selection_invalid")


class RoundPlanningTests(unittest.TestCase):
    def test_first_speaker_always_acts_in_round_one(self) -> None:
        plan = parse_selection(
            payload(roles=[role(0, hours=[22, 23])] + [role(i) for i in range(1, 6)])
        )
        actions = plan_round(
            plan, platform_key="platform_a", round_index=1, run_marker="abcdef12", known_posts=[]
        )
        self.assertTrue(
            any(
                action.actor_role_id == plan.first_speaker_role_ids[0]
                and action.native_action == "create_post"
                for action in actions
            )
        )

    def test_three_rounds_cover_every_native_action(self) -> None:
        plan = parse_selection(payload())
        for platform_key, allowed in NATIVE_ACTIONS.items():
            seen: set[str] = set()
            known: list[KnownPost] = []
            for round_index in (1, 2, 3):
                actions = plan_round(
                    plan,
                    platform_key=platform_key,
                    round_index=round_index,
                    run_marker="abcdef12",
                    known_posts=known,
                )
                self.assertLessEqual(len(actions), 6)
                seen.update(action.native_action for action in actions)
                known = [
                    KnownPost(
                        action_key=action.action_key,
                        engine_record_id=f"post:{index}",
                        role_id=action.actor_role_id,
                        topic=action.topic,
                    )
                    for index, action in enumerate(actions)
                    if action.native_action == "create_post"
                ] + known
            self.assertEqual(seen, set(allowed))
            for action in plan_round(
                plan,
                platform_key=platform_key,
                round_index=3,
                run_marker="abcdef12",
                known_posts=known,
            ):
                if action.native_action != "create_post":
                    self.assertIsNotNone(action.target_engine_record_id)
                    self.assertNotEqual(
                        action.target_action_key, action.action_key
                    )

    def test_platform_preference_and_cap(self) -> None:
        roles = [role(i, platforms=["platform_b"]) for i in range(6)]
        plan = parse_selection(payload(roles=roles))
        actions = plan_round(
            plan,
            platform_key="platform_a",
            round_index=1,
            run_marker="abcdef12",
            known_posts=[],
            actions_per_round=2,
        )
        self.assertEqual(actions, [])
        capped = plan_round(
            plan,
            platform_key="platform_b",
            round_index=1,
            run_marker="abcdef12",
            known_posts=[],
            actions_per_round=2,
        )
        self.assertLessEqual(len(capped), 2)

    def test_marker_only_on_content_actions(self) -> None:
        plan = parse_selection(payload())
        known = [
            KnownPost("known:1", "post:1", ROLE_IDS[1], "话题1"),
        ]
        actions = plan_round(
            plan,
            platform_key="platform_a",
            round_index=2,
            run_marker="abcdef12",
            known_posts=known,
        )
        for action in actions:
            if action.native_action in {"create_post", "create_comment"}:
                self.assertIn(action.marker, action.content)
            else:
                self.assertEqual(action.content, "")

    def test_simulated_hour_starts_at_first_speaker_window(self) -> None:
        plan = parse_selection(
            payload(
                roles=[role(0, hours=[13, 14])] + [role(i) for i in range(1, 6)],
            )
        )
        self.assertEqual(plan.simulated_hour(1), 13)
        self.assertEqual(plan.simulated_hour(2), 14)
        self.assertEqual(plan.simulated_hour(3), 15)
        self.assertEqual(PLATFORM_MAPPING["platform_a"], "twitter")
