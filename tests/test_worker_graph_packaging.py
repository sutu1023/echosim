"""业务建图 worker 打包边界：依赖文件、构建目标与 Compose 变量。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUSINESS_VARS = {
    "EVIDENCE_GRAPH_MODEL_PROVIDER",
    "EVIDENCE_GRAPH_GRAPH_PROVIDER",
    "EVIDENCE_GRAPH_ALLOW_STUB",
    "EVIDENCE_GRAPH_MAX_DOCUMENTS",
    "EVIDENCE_GRAPH_MAX_NODES",
    "EVIDENCE_GRAPH_MAX_EDGES",
    "EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM",
    "EVIDENCE_GRAPH_QUESTION_MAX_CHARS",
    "EVIDENCE_GRAPH_BATCH_SIZE",
    "EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS",
    "EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS",
}


def _service_environment(compose: str, service: str) -> set[str]:
    lines = compose.splitlines()
    try:
        start = next(
            index for index, line in enumerate(lines) if line == f"  {service}:"
        )
    except StopIteration as exc:
        raise AssertionError(f"Compose 缺少服务 {service}") from exc
    env_index = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].strip() == "environment:"
        ),
        None,
    )
    if env_index is None:
        return set()
    base_indent = len(lines[env_index]) - len(lines[env_index].lstrip())
    keys: set[str] = set()
    for line in lines[env_index + 1 :]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            break
        match = re.match(r"\s+([A-Z0-9_]+):", line)
        if match:
            keys.add(match.group(1))
    return keys


class WorkerGraphPackagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requirements = (
            REPO_ROOT / "app" / "requirements-worker-graph.txt"
        ).read_text(encoding="utf-8")
        self.dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.compose = (REPO_ROOT / "docker-compose.yml").read_text(
            encoding="utf-8"
        )

    def test_worker_requirements_pin_zep_without_candidate_engines(self) -> None:
        self.assertIn("zep-cloud==4.1.0", self.requirements)
        self.assertNotIn("camel", self.requirements.lower())
        self.assertNotIn("oasis", self.requirements.lower())

    def test_dockerfile_has_separate_app_and_worker_targets(self) -> None:
        self.assertIn("FROM runtime AS worker-graph", self.dockerfile)
        self.assertIn("FROM runtime AS app", self.dockerfile)
        self.assertIn("requirements-worker-graph.txt", self.dockerfile)
        self.assertNotIn(
            "requirements-worker-graph.txt",
            self.dockerfile.split("FROM runtime AS app", 1)[1],
        )

    def test_compose_selects_distinct_targets_and_images(self) -> None:
        self.assertIn("target: app", self.compose)
        self.assertIn("image: echosim-app:dev", self.compose)
        self.assertIn("target: worker-graph", self.compose)
        self.assertIn("image: echosim-worker-graph:dev", self.compose)

    def test_worker_receives_business_vars_not_acceptance_graph(self) -> None:
        worker_env = _service_environment(self.compose, "worker")
        self.assertTrue(BUSINESS_VARS.issubset(worker_env))
        self.assertIn("MODEL_API_KEY", worker_env)
        self.assertIn("ZEP_API_KEY", worker_env)
        self.assertIn("ZEP_ACCEPTANCE_GRAPH_FINGERPRINT", worker_env)
        self.assertNotIn("ZEP_ACCEPTANCE_GRAPH_ID", worker_env)

    def test_app_receives_no_model_zep_or_business_vars(self) -> None:
        app_env = _service_environment(self.compose, "app")
        forbidden = BUSINESS_VARS | {
            "MODEL_API_KEY",
            "MODEL_API_BASE_URL",
            "MODEL_NAME",
            "ZEP_API_KEY",
            "ZEP_API_BASE_URL",
            "ZEP_ACCEPTANCE_GRAPH_ID",
        }
        self.assertTrue(forbidden.isdisjoint(app_env))
        self.assertNotIn("ZEP_ACCEPTANCE_GRAPH_FINGERPRINT", app_env)

    def test_acceptance_graph_stays_in_engine_only(self) -> None:
        engine_env = _service_environment(self.compose, "engine")
        self.assertIn("ZEP_ACCEPTANCE_GRAPH_ID", engine_env)
        self.assertNotIn("ZEP_ACCEPTANCE_GRAPH_FINGERPRINT", engine_env)


if __name__ == "__main__":
    unittest.main()
