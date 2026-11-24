"""验收隔离：项目名校验、独占锁与清理命令顺序。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.verify import (
    VerifyIsolationError,
    acquire_verify_lock,
    cleanup_isolated_compose,
    isolated_compose_project,
    release_verify_lock,
)


class RecordingRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, args: list[str]) -> SimpleNamespace:
        self.calls.append(list(args))
        return SimpleNamespace(returncode=self.returncode, stdout="", stderr="")


class VerifyIsolationTests(unittest.TestCase):
    def test_dev_and_arbitrary_projects_are_rejected(self) -> None:
        for name in (
            "echosim",
            "echosim05review25115ce",
            "other-verify",
            "echosim-verify/../echosim",
        ):
            with self.subTest(name=name):
                with self.assertRaises(VerifyIsolationError):
                    isolated_compose_project(name)

    def test_default_and_suffixed_ci_projects_are_allowed(self) -> None:
        self.assertEqual(isolated_compose_project(None), "echosim-verify")
        self.assertEqual(
            isolated_compose_project("echosim-verify-ci-123"),
            "echosim-verify-ci-123",
        )

    def test_invalid_project_is_rejected_before_any_command(self) -> None:
        runner = RecordingRunner()
        with self.assertRaises(VerifyIsolationError):
            cleanup_isolated_compose("echosim", runner)
        self.assertEqual(runner.calls, [])

    def test_cleanup_is_scoped_and_failure_is_checked(self) -> None:
        runner = RecordingRunner()
        cleanup_isolated_compose("echosim-verify-ci-9", runner)
        self.assertEqual(
            runner.calls,
            [
                [
                    "docker",
                    "compose",
                    "-p",
                    "echosim-verify-ci-9",
                    "down",
                    "-v",
                    "--remove-orphans",
                ]
            ],
        )
        failing = RecordingRunner(returncode=1)
        with self.assertRaises(RuntimeError):
            cleanup_isolated_compose("echosim-verify", failing)

    def test_same_project_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = acquire_verify_lock("echosim-verify", directory=Path(tmp))
            try:
                with self.assertRaises(VerifyIsolationError):
                    acquire_verify_lock("echosim-verify", directory=Path(tmp))
            finally:
                release_verify_lock(first)
            second = acquire_verify_lock("echosim-verify", directory=Path(tmp))
            release_verify_lock(second)


if __name__ == "__main__":
    unittest.main()
