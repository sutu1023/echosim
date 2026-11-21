"""依赖四阶段基线：只有全部阶段成功才 verified。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.engines.baseline import (
    PINS,
    StageResult,
    evaluate,
    install_stage,
    resolve_stage,
)


def resolve_payload(versions: dict[str, str] | None = None) -> dict[str, object]:
    actual = versions or dict(PINS)
    return {
        "install": [
            {"metadata": {"name": name, "version": version}}
            for name, version in actual.items()
        ]
    }


class StageTests(unittest.TestCase):
    def test_resolve_accepts_exact_pins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resolve.json"
            path.write_text(json.dumps(resolve_payload()), encoding="utf-8")

            stage = resolve_stage(path)

        self.assertEqual(stage.status, "passed")

    def test_resolve_rejects_missing_or_mismatched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resolve.json"
            path.write_text(
                json.dumps(resolve_payload({"camel-ai": "0.2.89"})), encoding="utf-8"
            )
            missing = Path(tmp) / "missing.json"

            self.assertEqual(resolve_stage(path).status, "failed")
            self.assertEqual(resolve_stage(missing).status, "not_run")

    def test_install_reads_build_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "build.json"
            path.write_text(
                json.dumps(
                    {
                        "install": {
                            "status": "passed",
                            "detail": "ok",
                            "versions": PINS,
                        }
                    }
                ),
                encoding="utf-8",
            )

            stage = install_stage(path)

        self.assertEqual(stage.status, "passed")
        self.assertEqual(stage.versions["camel-oasis"], "0.2.6")


class EvaluateTests(unittest.TestCase):
    def test_all_stages_pass_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolve = Path(tmp) / "resolve.json"
            build = Path(tmp) / "build.json"
            resolve.write_text(json.dumps(resolve_payload()), encoding="utf-8")
            build.write_text(
                json.dumps(
                    {"install": {"status": "passed", "detail": "ok", "versions": PINS}}
                ),
                encoding="utf-8",
            )
            with patch(
                "app.engines.baseline.import_stage",
                return_value=StageResult("passed", "ok", 0, dict(PINS)),
            ):
                baseline = evaluate(
                    resolve_path=resolve,
                    build_path=build,
                    minimal_runner=lambda _: (True, "ok"),
                )

        self.assertEqual(baseline.status, "verified")
        self.assertEqual(baseline.to_dict()["stages"]["minimalRun"]["status"], "passed")

    def test_any_stage_failure_is_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolve = Path(tmp) / "resolve.json"
            build = Path(tmp) / "build.json"
            resolve.write_text(json.dumps(resolve_payload()), encoding="utf-8")
            build.write_text(
                json.dumps(
                    {"install": {"status": "passed", "detail": "ok", "versions": PINS}}
                ),
                encoding="utf-8",
            )
            with patch(
                "app.engines.baseline.import_stage",
                return_value=StageResult("failed", "missing", 0, {}),
            ):
                baseline = evaluate(
                    resolve_path=resolve,
                    build_path=build,
                    minimal_runner=lambda _: (True, "ok"),
                )

        self.assertEqual(baseline.status, "unverified")
        self.assertIn("import", baseline.detail)


if __name__ == "__main__":
    unittest.main()
