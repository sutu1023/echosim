"""Real dependency outage/recovery in a disposable, isolated Compose project."""

import json
import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
project = "echosim-smoke-" + secrets.token_hex(4)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
env = {
    **os.environ,
    "APP_ENV": "test",
    "APP_VERSION": "smoke",
    "PORT": str(port),
    "HEALTH_TIMEOUT_SECONDS": "1",
    "WORKER_POLL_SECONDS": "60",
    "POSTGRES_DB": "smoke",
    "POSTGRES_USER": "smoke",
    "POSTGRES_PASSWORD": secrets.token_hex(24),
}
command = ["docker", "compose", "--env-file", "/dev/null", "-p", project]


def compose(*args, capture=False):
    return subprocess.run(
        command + list(args),
        env=env,
        cwd=root,
        check=True,
        text=True,
        capture_output=capture,
    )


def get(path="/health"):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=12
        ) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


try:
    compose("up", "--build", "-d", "--wait", "--wait-timeout", "120")
    code, body = get()
    assert code == 200 and json.loads(body)["dependencies"]["database"] == "up"
    assert "EchoSim" in get("/")[1]
    assert get("/api/v1/works")[0] == 404
    uid = compose("exec", "-T", "api", "id", "-u", capture=True).stdout.strip()
    assert uid == "10001", uid
    compose("stop", "db")
    start = time.monotonic()
    code, body = get()
    assert code == 503 and json.loads(body)["dependencies"]["database"] == "down"
    assert time.monotonic() - start < 2
    compose("start", "db")
    for _ in range(30):
        if get()[0] == 200:
            break
        time.sleep(1)
    else:
        raise AssertionError("database did not recover")
    start = time.monotonic()
    compose("stop", "worker")
    assert time.monotonic() - start < 5
    logs = compose("logs", "--no-log-prefix", "worker", capture=True).stdout
    rows = [json.loads(line) for line in logs.splitlines() if line.strip()]
    assert any(x["event"] == "worker_stopped" for x in rows)
    assert env["POSTGRES_PASSWORD"] not in logs
    print(
        "PASS: real readiness, outage, recovery, frontend, closed business paths, non-root, SIGTERM"
    )
finally:
    compose("down", "--volumes", "--remove-orphans")
