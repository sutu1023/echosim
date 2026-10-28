import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.log_retention import prune_logs  # noqa: E402
from scripts.docs_check import check  # noqa: E402
from scripts.licenses import approved  # noqa: E402
from scripts.vulnerability_policy import classify  # noqa: E402

from conftest import request  # noqa: E402


@pytest.mark.boundary
def test_SC_GATE_05_security_exception_is_exact_and_expiring(tmp_path):
    policy = json.loads((ROOT / "config/security-exception.json").read_text())
    for path in [
        *policy["configuration_sha256"],
        "infra/images.env",
        "config/security-exception.json",
    ]:
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, dest)
    entry = policy["entries"][0]
    vulnerability = {
        "Severity": "HIGH",
        "PkgName": entry["package"],
        "InstalledVersion": entry["version"],
        "VulnerabilityID": entry["cve"],
    }
    report = {
        "SchemaVersion": 2,
        "Results": [{"Target": entry["target"], "Vulnerabilities": [vulnerability]}],
    }
    before = datetime(2026, 10, 28, tzinfo=UTC)
    assert len(classify(tmp_path, "web", report, before)["approved_exceptions"]) == 1
    for name, at in [("api", before), ("web", datetime(2026, 11, 4, tzinfo=UTC))]:
        assert classify(tmp_path, name, report, at)["blocked"]
    for field, value in [
        ("Severity", "CRITICAL"),
        ("InstalledVersion", "unknown"),
        ("VulnerabilityID", "NEW-CVE"),
    ]:
        original = vulnerability[field]
        vulnerability[field] = value
        assert classify(tmp_path, "web", report, before)["blocked"]
        vulnerability[field] = original
    (tmp_path / "infra/Caddyfile").write_text("changed config")
    assert classify(tmp_path, "web", report, before)["blocked"]
    with pytest.raises(ValueError):
        classify(tmp_path, "web", {}, before)


@pytest.mark.failure
def test_SC_GATE_04_missing_rule_or_traceability_blocks(tmp_path):
    for folder in ["docs", "config", "openspec", "scripts", "api/tests", "web"]:
        source = ROOT / folder
        if source.exists():
            shutil.copytree(
                source,
                tmp_path / folder,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("node_modules", "dist", ".venv", "__pycache__"),
            )
    # Real validation of both missing policy and absent verification target.
    (tmp_path / "docs/standards/backend.md").unlink()
    assert any("missing standard: backend" in message for message in check(tmp_path))
    extra = tmp_path / "openspec/specs/unverified/spec.md"
    extra.parent.mkdir(parents=True)
    orphan = "SC-" + "ORPHAN-99"
    extra.write_text(f"#### Scenario: {orphan} orphan\n")
    assert any(orphan in message for message in check(tmp_path))


@pytest.mark.failure
@pytest.mark.parametrize(
    "expression,expected",
    [
        ("GPL-3.0-only AND (MIT OR Apache-2.0)", False),
        ("MIT AND (GPL-3.0-only OR Unknown)", False),
        ("MIT OR (GPL-3.0-only AND Unknown)", True),
        ("(MIT OR Apache-2.0) AND BSD-3-Clause", True),
        ("MIT AND", False),
        ("MIT WITH Unknown", False),
        ("MIT)", False),
    ],
)
def test_S13_license_expression_precedence(expression, expected):
    policy = {"allowed": ["MIT", "Apache-2.0", "BSD-3-Clause"], "reviewed_exceptions": {}}
    assert approved(expression, "fixture", policy) is expected


@pytest.mark.boundary
def test_O04_retention_removes_expired_and_bounds_size(tmp_path):
    (tmp_path / "2026-10-21.jsonl").write_text("expired")
    (tmp_path / "2026-10-22.jsonl").write_text("retained")
    (tmp_path / "unrelated.txt").write_text("keep")
    prune_logs(tmp_path, datetime(2026, 10, 28, tzinfo=UTC))
    assert not (tmp_path / "2026-10-21.jsonl").exists()
    assert (tmp_path / "2026-10-22.jsonl").exists()
    assert (tmp_path / "unrelated.txt").exists()
    prune_logs(tmp_path, datetime(2026, 10, 28, tzinfo=UTC), max_bytes=1)
    assert not list(tmp_path.glob("*.jsonl"))


@pytest.mark.failure
def test_SC_GATE_03_real_isolated_mutation(tmp_path):
    source = ROOT / "api"
    shutil.copytree(source / "app", tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(source / "pyproject.toml", tmp_path)
    shutil.copy(source / "tests/conftest.py", tmp_path)
    shutil.copy(source / "tests/test_runtime.py", tmp_path)
    health = tmp_path / "app/services/health.py"
    text = health.read_text()
    assert "ready = False" in text
    health.write_text(text.replace("ready = False", "ready = True", 1))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_runtime.py",
            "-q",
            "-k",
            "SC_GATE_03_mutation_assertion",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and "1 failed" in result.stdout
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "mutation.json").write_text(
        json.dumps(
            {
                "mutation": "dependency failure incorrectly starts ready=True",
                "isolated_copy": True,
                "detected": True,
                "test_exit": result.returncode,
                "production_source_modified": False,
            },
            indent=2,
        )
        + "\n"
    )


@pytest.mark.normal
def test_SC_GATE_08_inflight_sigterm(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    marker = tmp_path / "admitted"
    process = subprocess.Popen(
        [sys.executable, "tests/slow_server.py", str(port), str(marker)],
        cwd=ROOT / "api",
        env={**os.environ, "PYTHONPATH": str(ROOT / "api")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                if request(base, "/livez")[0] == 200:
                    break
            except OSError:
                pass
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.02)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(request, base)
            while not marker.exists():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            started = time.monotonic()
            process.send_signal(signal.SIGTERM)
            assert future.result(timeout=5)[0] == 200
            exit_code = process.wait(timeout=30)
            # Uvicorn restores/replays SIGTERM after a completed graceful drain.
            assert exit_code in {0, -signal.SIGTERM}
            seconds = time.monotonic() - started
            assert seconds < 30
        artifacts = ROOT / "artifacts"
        artifacts.mkdir(exist_ok=True)
        (artifacts / "inflight-shutdown.json").write_text(
            json.dumps(
                {
                    "admitted_request_completed": True,
                    "exit_code": exit_code,
                    "seconds": seconds,
                    "signal": "SIGTERM",
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
