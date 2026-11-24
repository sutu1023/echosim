"""真实本地 OASIS 证据的通过条件：缺库或计数不一致必须失败。"""

from __future__ import annotations

import unittest

from scripts.simulate_live import compare_platform_counts, validate_run


def frozen(roles: int = 6, rounds: int = 3) -> dict:
    return {
        "roles": [{"roleId": f"r{i}"} for i in range(roles)],
        "settings": {"rounds": rounds},
    }


def detail(**overrides) -> dict:
    body = {
        "phase": "succeeded",
        "engine": "oasis",
        "platforms": [
            {
                "platformKey": "platform_a",
                "roundsCompleted": 3,
                "actionCount": 2,
                "byAction": {"create_post": 1, "like_post": 1},
            },
            {
                "platformKey": "platform_b",
                "roundsCompleted": 3,
                "actionCount": 1,
                "byAction": {"create_comment": 1},
            },
        ],
    }
    body.update(overrides)
    return body


ENGINE = {
    "platform_a": {
        "create_post": 1,
        "repost": 0,
        "create_comment": 0,
        "like_post": 1,
        "dislike_post": 0,
    },
    "platform_b": {
        "create_post": 0,
        "repost": 0,
        "create_comment": 1,
        "like_post": 0,
        "dislike_post": 0,
    },
}


class ValidateRunTests(unittest.TestCase):
    def test_accepts_matching_run(self) -> None:
        self.assertEqual(validate_run(detail(), frozen(), ENGINE), [])

    def test_missing_platform_database_fails(self) -> None:
        errors = validate_run(detail(), frozen(), {"platform_a": ENGINE["platform_a"]})
        self.assertTrue(any("platform_b" in item for item in errors))

    def test_wrong_count_fails(self) -> None:
        broken = {
            "platform_a": {**ENGINE["platform_a"], "like_post": 2},
            "platform_b": ENGINE["platform_b"],
        }
        errors = validate_run(detail(), frozen(), broken)
        self.assertTrue(
            any("platform_a" in item and "不一致" in item for item in errors)
        )

    def test_wrong_role_or_round_count_fails(self) -> None:
        self.assertTrue(
            any("6" in item for item in validate_run(detail(), frozen(roles=5), ENGINE))
        )
        self.assertTrue(
            any(
                "3" in item
                for item in validate_run(detail(), frozen(rounds=2), ENGINE)
            )
        )
        bad_rounds = detail()
        bad_rounds["platforms"][0]["roundsCompleted"] = 2
        self.assertTrue(any("platform_a" in item for item in validate_run(bad_rounds, frozen(), ENGINE)))

    def test_platform_without_actions_fails(self) -> None:
        empty = detail()
        empty["platforms"][0]["actionCount"] = 0
        empty["platforms"][0]["byAction"] = {}
        engine = {**ENGINE, "platform_a": {key: 0 for key in ENGINE["platform_a"]}}
        errors = validate_run(empty, frozen(), engine)
        self.assertTrue(any("没有原生动作" in item for item in errors))

    def test_compare_reports_each_platform(self) -> None:
        platforms = {item["platformKey"]: item for item in detail()["platforms"]}
        errors = compare_platform_counts(
            {"platform_a": {"create_post": 2}, "platform_b": {"create_comment": 1}},
            platforms,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("platform_a", errors[0])
