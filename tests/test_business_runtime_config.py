"""正式启动流程：Compose 最终解析 → worker 指纹 → 业务适配器配置。"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from app.engines.business_config import BusinessGraphConfig, graph_id_fingerprint
from app.engines.business_graph import BusinessGraphError, write_episode
from app.services import evidence_graph as store

REPO_ROOT = Path(__file__).resolve().parent.parent
FILE_GRAPH = "11111111-1111-4111-8111-111111111111"
OVERRIDE_GRAPH = "22222222-2222-4222-8222-222222222222"
NORMAL_GRAPH = "33333333-3333-4333-8333-333333333333"
HAS_DOCKER = shutil.which("docker") is not None


class CountingEpisodeClient:
    def __init__(self) -> None:
        self.graph = self
        self.episode = self
        self.add_calls = 0

    def add(self, graph_uuid: str, **kwargs: object) -> dict[str, object]:
        del graph_uuid, kwargs
        self.add_calls += 1
        return {"episode": {"uuid": str(uuid.uuid4()), "processed": True}}


def _episode() -> store.GraphEpisode:
    content = "原创 episode 内容"
    return store.GraphEpisode(
        build_id=uuid.uuid4(),
        episode_key="episode-1",
        idempotency_key=uuid.uuid4(),
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        episode_uuid=None,
        state="planned",
        attempts=0,
    )


def _write_source(tmp: Path, acceptance_line: str) -> Path:
    source = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    source = source.replace(
        "ZEP_ACCEPTANCE_GRAPH_ID=00000000-0000-0000-0000-000000000000",
        acceptance_line,
    )
    source = source.replace(
        "ZEP_API_KEY=replace-with-zep-key", "ZEP_API_KEY=test-zep-key"
    )
    path = tmp / "source.env"
    path.write_text(source, encoding="utf-8")
    return path


def _compose_config(env_file: Path, overrides: dict[str, str]) -> dict[str, object]:
    env = dict(os.environ)
    env.update(overrides)
    export_cmd = (
        f"{shlex.quote(sys.executable)} "
        f"{shlex.quote(str(REPO_ROOT / 'scripts' / 'host_config.py'))} export "
        f"--env-file {shlex.quote(str(env_file))}"
    )
    compose_cmd = (
        "docker compose --profile live "
        f"--env-file {shlex.quote(str(env_file))} config --format json"
    )
    result = subprocess.run(
        ["bash", "-lc", f'eval "$({export_cmd})" && {compose_cmd}'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def _host_fingerprint(env_file: Path, overrides: dict[str, str]) -> str:
    env = dict(os.environ)
    env.update(overrides)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "host_config.py"),
            "fingerprint",
            "--env-file",
            str(env_file),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def _worker_env(config: dict[str, object]) -> dict[str, str]:
    return dict(config["services"]["worker"]["environment"])


def _engine_env(config: dict[str, object]) -> dict[str, str]:
    return dict(config["services"]["engine"]["environment"])


@unittest.skipUnless(HAS_DOCKER, "docker compose 不可用，跳过运行时配置装配测试")
class RuntimeConfigAssemblyTests(unittest.TestCase):
    def test_file_quotes_shell_override_and_sequential_switches(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            source = _write_source(
                tmp, f'ZEP_ACCEPTANCE_GRAPH_ID="{FILE_GRAPH}"'
            )
            config_file = _compose_config(source, {})
            fingerprint_file = _host_fingerprint(source, {})
            worker_file = _worker_env(config_file)
            engine_file = _engine_env(config_file)

            self.assertEqual(engine_file["ZEP_ACCEPTANCE_GRAPH_ID"], FILE_GRAPH)
            self.assertEqual(
                fingerprint_file, graph_id_fingerprint(FILE_GRAPH)
            )
            self.assertEqual(
                worker_file["ZEP_ACCEPTANCE_GRAPH_FINGERPRINT"],
                fingerprint_file,
            )
            self.assertNotIn("ZEP_ACCEPTANCE_GRAPH_ID", worker_file)

            override_one = {"ZEP_ACCEPTANCE_GRAPH_ID": OVERRIDE_GRAPH}
            config_override = _compose_config(source, override_one)
            fingerprint_override = _host_fingerprint(source, override_one)
            self.assertEqual(
                _engine_env(config_override)["ZEP_ACCEPTANCE_GRAPH_ID"],
                OVERRIDE_GRAPH,
            )
            self.assertEqual(
                fingerprint_override, graph_id_fingerprint(OVERRIDE_GRAPH)
            )
            self.assertEqual(
                _worker_env(config_override)["ZEP_ACCEPTANCE_GRAPH_FINGERPRINT"],
                fingerprint_override,
            )
            self.assertNotEqual(fingerprint_override, fingerprint_file)

            second = {"ZEP_ACCEPTANCE_GRAPH_ID": FILE_GRAPH}
            self.assertEqual(
                _host_fingerprint(source, second), fingerprint_file
            )

    def test_make_entry_uses_same_compose_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            source = _write_source(
                tmp, f"ZEP_ACCEPTANCE_GRAPH_ID={FILE_GRAPH}"
            )
            env = dict(os.environ)
            env["ZEP_ACCEPTANCE_GRAPH_ID"] = OVERRIDE_GRAPH
            result = subprocess.run(
                ["make", "compose-fingerprint", f"ENV_FILE={source}"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise AssertionError(result.stderr or result.stdout)
        self.assertEqual(
            result.stdout.strip(), graph_id_fingerprint(OVERRIDE_GRAPH)
        )

    def test_make_up_aborts_before_compose_when_fingerprint_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            source = _write_source(
                tmp, f"ZEP_ACCEPTANCE_GRAPH_ID={FILE_GRAPH}"
            )
            fake_python = tmp / "fake-python"
            fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            fake_python.chmod(0o755)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            docker_log = tmp / "docker-calls.log"
            fake_docker = bin_dir / "docker"
            fake_docker.write_text(
                "#!/bin/sh\necho \"$@\" >> \"$DOCKER_LOG\"\nexit 0\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
            env["DOCKER_LOG"] = str(docker_log)
            result = subprocess.run(
                [
                    "make",
                    "up",
                    f"ENV_FILE={source}",
                    f"PYTHON={fake_python}",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(docker_log.exists())

    def test_worker_fingerprint_drives_scope_and_unconfigured_startup(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            source = _write_source(
                tmp, f"ZEP_ACCEPTANCE_GRAPH_ID={FILE_GRAPH}"
            )
            config = _compose_config(source, {})
            worker_env = _worker_env(config)
            business = BusinessGraphConfig.from_env(worker_env)
            self.assertEqual(
                business.acceptance_graph_fingerprint,
                graph_id_fingerprint(FILE_GRAPH),
            )

            misbound = CountingEpisodeClient()
            with self.assertRaises(BusinessGraphError) as caught:
                write_episode(
                    misbound,
                    graph_id=FILE_GRAPH,
                    episode=_episode(),
                    config=business,
                )
            self.assertEqual(caught.exception.code, "invalid_graph_scope")
            self.assertEqual(misbound.add_calls, 0)

            normal = CountingEpisodeClient()
            written = write_episode(
                normal,
                graph_id=NORMAL_GRAPH,
                episode=_episode(),
                config=business,
            )
            self.assertTrue(written.accepted)
            self.assertEqual(normal.add_calls, 1)

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            unset = _write_source(
                tmp,
                "ZEP_ACCEPTANCE_GRAPH_ID=00000000-0000-0000-0000-000000000000",
            )
            config = _compose_config(unset, {})
            worker_env = _worker_env(config)
            self.assertEqual(
                worker_env["ZEP_ACCEPTANCE_GRAPH_FINGERPRINT"], ""
            )
            business = BusinessGraphConfig.from_env(worker_env)
            self.assertIsNone(business.acceptance_graph_fingerprint)
            client = CountingEpisodeClient()
            written = write_episode(
                client,
                graph_id=NORMAL_GRAPH,
                episode=_episode(),
                config=business,
            )
            self.assertTrue(written.accepted)
            self.assertEqual(client.add_calls, 1)


if __name__ == "__main__":
    unittest.main()
