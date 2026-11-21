"""OASIS 双平台：原生动作、目标帖、独立判定与清理。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.engines.contracts import (
    AcceptanceMaterial,
    build_acceptance_material,
    build_action_content,
    build_target_post_content,
)
from app.engines.settings import EngineSettings
from app.engines.simulation import (
    SharedSetupError,
    run_platform,
)


def settings(**overrides: Any) -> EngineSettings:
    values = {"simulation_timeout_seconds": 60.0}
    values.update(overrides)
    return EngineSettings(**values)


class FakePlatform:
    def __init__(self, state: dict[str, Any], *, fail_close: bool = False) -> None:
        self.state = state
        self.fail_close = fail_close

    async def sign_up(self, agent_id: int, message: tuple[str, str, str]) -> dict[str, Any]:
        self.state["role"] = message[1]
        return {"success": True}

    async def create_post(self, agent_id: int, content: str) -> dict[str, Any]:
        post_id = self.state["next_post"]
        self.state["next_post"] += 1
        self.state["posts"][content] = post_id
        return {"success": True, "post_id": post_id}

    async def create_comment(self, agent_id: int, message: tuple[int, str]) -> dict[str, Any]:
        post_id, content = message
        comment_id = self.state["next_comment"]
        self.state["next_comment"] += 1
        self.state["comments"][(post_id, content)] = comment_id
        return {"success": True, "comment_id": comment_id}

    def find_post_id(self, content: str) -> int | None:
        return self.state["posts"].get(content)

    def close(self) -> None:
        if self.fail_close:
            raise RuntimeError("cleanup failed")


def readback(state: dict[str, Any], *, wrong_target: bool = False):
    def read(_: Path, key: str, material: AcceptanceMaterial) -> dict[str, Any] | None:
        if key == "platform_a":
            content = build_action_content(key, material)
            post_id = state["posts"].get(content)
            if post_id is None:
                return None
            return {
                "platformKey": "platform_a",
                "nativePlatform": "twitter",
                "nativeAction": "create_post",
                "role": "居民代表",
                "content": content,
                "recordId": f"post:{post_id}",
                "result": "success",
            }
        target_content = build_target_post_content(key, material)
        comment_content = build_action_content(key, material)
        target_post_id = state["posts"].get(target_content)
        if target_post_id is None:
            return None
        comment_id = state["comments"].get((target_post_id, comment_content))
        if comment_id is None:
            return None
        return {
            "platformKey": "platform_b",
            "nativePlatform": "reddit",
            "nativeAction": "create_comment",
            "role": "值班志愿者",
            "content": comment_content,
            "recordId": f"comment:{comment_id}",
            "result": "success",
            "targetPostId": target_post_id + (10 if wrong_target else 0),
        }

    return read


class SimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.material = build_acceptance_material("sim-run")

    def run_key(self, key: str, **kwargs: Any):
        state = {"posts": {}, "comments": {}, "next_post": 1, "next_comment": 1}
        fail_close = kwargs.pop("fail_close", False)
        wrong_target = kwargs.pop("wrong_target", False)
        fail_factory = kwargs.pop("fail_factory", False)

        def factory(platform_key: str, db_path: Path, work_root: Path) -> FakePlatform:
            if fail_factory:
                raise RuntimeError("factory failed")
            return FakePlatform(state, fail_close=fail_close)

        with tempfile.TemporaryDirectory() as tmp:
            return run_platform(
                key,
                settings(),
                self.material,
                Path(tmp),
                platform_factory=factory,
                readback=readback(state, wrong_target=wrong_target),
            )

    def test_platform_a_create_post(self) -> None:
        outcome = self.run_key("platform_a")

        self.assertEqual(outcome.item.status, "passed")
        self.assertEqual(outcome.item.evidence["nativePlatform"], "twitter")
        self.assertEqual(outcome.item.evidence["nativeAction"], "create_post")
        self.assertEqual(outcome.cleanup, "passed")

    def test_platform_b_comment_matches_target_post(self) -> None:
        outcome = self.run_key("platform_b")

        self.assertEqual(outcome.item.status, "passed")
        self.assertEqual(outcome.item.evidence["nativePlatform"], "reddit")
        self.assertEqual(outcome.item.evidence["nativeAction"], "create_comment")
        self.assertEqual(outcome.item.evidence["targetPostId"], 1)

    def test_platform_b_wrong_target_post_fails(self) -> None:
        outcome = self.run_key("platform_b", wrong_target=True)

        self.assertEqual(outcome.item.status, "failed")
        self.assertEqual(outcome.item.reason, "platform_b_record")

    def test_platform_setup_failure_is_independent(self) -> None:
        outcome = self.run_key("platform_a", fail_factory=True)

        self.assertEqual(outcome.item.status, "failed")
        self.assertEqual(outcome.item.reason, "platform_a_setup")

    def test_cleanup_failure_is_recorded_without_hiding_action(self) -> None:
        outcome = self.run_key("platform_a", fail_close=True)

        self.assertEqual(outcome.item.status, "passed")
        self.assertEqual(outcome.cleanup, "failed")
        self.assertEqual(outcome.item.evidence["cleanup"], "failed")

    def test_shared_setup_is_not_misattributed(self) -> None:
        def factory(platform_key: str, db_path: Path, work_root: Path) -> Any:
            raise SharedSetupError("shared")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SharedSetupError):
                run_platform(
                    "platform_a",
                    settings(),
                    self.material,
                    Path(tmp),
                    platform_factory=factory,
                    readback=readback({"posts": {}, "comments": {}, "next_post": 1, "next_comment": 1}),
                )


if __name__ == "__main__":
    unittest.main()
