"""离线引擎契约检查：替身调用、状态机与缺配置分别判定。"""

from __future__ import annotations

import unittest

from app.engines.offline import run_offline


class OfflineContractTests(unittest.TestCase):
    def test_all_offline_scenarios_pass_without_real_calls(self) -> None:
        payload = run_offline()

        self.assertEqual(payload["result"], "passed")
        self.assertEqual(payload["mode"], "offline-contract")
        self.assertEqual(payload["calls"]["real"], 0)
        self.assertGreater(payload["calls"]["double"], 0)
        scenarios = {item["id"]: item for item in payload["scenarios"]}
        for scenario_id in (
            "model-response-format-retry",
            "graph-response-lost-first-search-miss",
            "graph-write-unconfirmed",
            "simulation-platform-b-target-mismatch",
            "smoke-missing-zep-keeps-oasis",
            "smoke-missing-model-keeps-oasis",
            "smoke-dirty-refused",
        ):
            with self.subTest(scenario=scenario_id):
                self.assertEqual(scenarios[scenario_id]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
