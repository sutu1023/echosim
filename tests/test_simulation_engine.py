"""双平台引擎：原生表读回、内容标记、目标校验与三种中断形态。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.engines.duet_contracts import DuetContractError
from app.engines.duet_engine import (
    execute_round,
    native_counts,
    open_engine,
    round_records,
)

ACTIONS = [
    {
        "actionKey": "platform_b:r1:a1",
        "actionIndex": 1,
        "nativeAction": "create_post",
        "actorRoleId": "role-1",
        "actorAgentId": 1,
        "actorName": "角色一",
        "content": "首条主题 [[ES:abcdef12-platform_b-r1-a1]]",
        "marker": "[[ES:abcdef12-platform_b-r1-a1]]",
        "targetActionKey": None,
        "targetEngineRecordId": None,
    },
    {
        "actionKey": "platform_b:r1:a2",
        "actionIndex": 2,
        "nativeAction": "create_comment",
        "actorRoleId": "role-2",
        "actorAgentId": 2,
        "actorName": "角色二",
        "content": "评论 [[ES:abcdef12-platform_b-r1-a2]]",
        "marker": "[[ES:abcdef12-platform_b-r1-a2]]",
        "targetActionKey": "platform_b:r1:a1",
        "targetEngineRecordId": "",
    },
    {
        "actionKey": "platform_b:r1:a3",
        "actionIndex": 3,
        "nativeAction": "like_post",
        "actorRoleId": "role-2",
        "actorAgentId": 2,
        "actorName": "角色二",
        "content": "",
        "marker": "[[ES:abcdef12-platform_b-r1-a3]]",
        "targetActionKey": None,
        "targetEngineRecordId": "post:1",
    },
    {
        "actionKey": "platform_b:r1:a4",
        "actionIndex": 4,
        "nativeAction": "dislike_post",
        "actorRoleId": "role-3",
        "actorAgentId": 3,
        "actorName": "角色三",
        "content": "",
        "marker": "[[ES:abcdef12-platform_b-r1-a4]]",
        "targetActionKey": None,
        "targetEngineRecordId": "post:1",
    },
]


def plain(actions):
    return [{"action_key": item["actionKey"], "content": item["content"]} for item in actions]


class DoubleEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = Path(tempfile.mkdtemp(prefix="duet-engine-"))

    def _run(self, actions=ACTIONS, fault="none", platform="platform_b"):
        execute_round(
            mode="double",
            platform_key=platform,
            runtime_dir=self.runtime,
            round_index=1,
            actions=actions,
            fault=fault,
        )
        return round_records(
            runtime_dir=self.runtime,
            platform_key=platform,
            round_index=1,
            actions=plain(actions),
        )

    def test_readback_verifies_native_rows_and_targets(self) -> None:
        readback = self._run()
        self.assertEqual(readback["status"], "committed")
        self.assertEqual(readback["planned"], [])
        self.assertEqual(readback["unconfirmed"], [])
        records = {item["nativeAction"]: item for item in readback["records"]}
        self.assertEqual(records["create_post"]["engineRecordId"], "post:1")
        self.assertEqual(records["create_comment"]["targetEngineRecordId"], "post:1")
        self.assertEqual(records["like_post"]["targetEngineRecordId"], "post:1")
        self.assertEqual(records["dislike_post"]["targetEngineRecordId"], "post:1")
        self.assertEqual(
            native_counts(self.runtime, "platform_b"),
            {"create_post": 1, "create_comment": 1, "like_post": 1, "dislike_post": 1},
        )

    def test_repeat_readback_is_idempotent(self) -> None:
        first = self._run()
        second = round_records(
            runtime_dir=self.runtime,
            platform_key="platform_b",
            round_index=1,
            actions=plain(ACTIONS),
        )
        self.assertEqual(len(first["records"]), len(second["records"]))
        self.assertEqual(
            [item["engineRecordId"] for item in first["records"]],
            [item["engineRecordId"] for item in second["records"]],
        )

    def test_after_first_action_is_adoptable(self) -> None:
        readback = self._run(fault="after_first_action")
        self.assertEqual(len(readback["records"]), 1)
        self.assertEqual(readback["records"][0]["engineRecordId"], "post:1")
        self.assertFalse(readback["roundCommitted"])
        self.assertEqual(
            [item["actionKey"] for item in readback["records"]], ["platform_b:r1:a1"]
        )
        self.assertTrue(readback["planned"])
        # 同一目录再次执行（接手）会认领已写入的原生行并补齐其余动作。
        resumed = self._run()
        self.assertEqual(len(resumed["records"]), 4)
        identifiers = [item["engineRecordId"] for item in resumed["records"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(native_counts(self.runtime, "platform_b")["create_post"], 1)

    def test_before_native_is_provably_not_executed(self) -> None:
        readback = self._run(fault="before_native")
        self.assertEqual(readback["records"], [])
        self.assertEqual(readback["unconfirmed"], [])
        self.assertEqual(len(readback["planned"]), 4)
        resumed = self._run()
        self.assertEqual(len(resumed["records"]), 4)

    def test_strip_marker_is_unconfirmable(self) -> None:
        readback = self._run(fault="strip_marker")
        self.assertEqual(readback["records"], [])
        self.assertEqual(readback["unconfirmed"], ["platform_b:r1:a1"])

    def test_untargeted_action_without_target_is_rejected(self) -> None:
        broken = [dict(ACTIONS[2], targetEngineRecordId=None)]
        with self.assertRaises(DuetContractError):
            self._run(actions=broken)

    def test_oasis_mode_without_dependency_is_not_silent(self) -> None:
        with self.assertRaises(DuetContractError) as caught:
            open_engine(
                mode="oasis",
                platform_key="platform_a",
                db_path=self.runtime / "oasis.db",
                step=1,
            )
        self.assertEqual(caught.exception.code, "engine_unavailable")
