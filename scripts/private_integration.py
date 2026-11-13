"""Disposable real PostgreSQL contract tests, never using an existing database."""

import os
import secrets
import subprocess

name = "echosim-contract-" + secrets.token_hex(4)
password = secrets.token_hex(24)


def docker(*args):
    return subprocess.check_output(
        ["docker", *args], text=True, env={**os.environ, "POSTGRES_PASSWORD": password}
    ).strip()


try:
    docker(
        "run",
        "-d",
        "--name",
        name,
        "-e",
        "POSTGRES_PASSWORD",
        "-e",
        "POSTGRES_USER=contract",
        "-e",
        "POSTGRES_DB=contract",
        "-p",
        "127.0.0.1::5432",
        "--health-cmd",
        "pg_isready -U contract",
        "--health-interval=1s",
        "--health-retries=30",
        "pgvector/pgvector:pg17",
    )
    docker(
        "exec",
        name,
        "sh",
        "-c",
        "until pg_isready -U contract >/dev/null; do sleep 1; done",
    )
    port = docker("port", name, "5432/tcp").split(":")[-1]
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "backend",
            "--frozen",
            "pytest",
            "-q",
            "backend/integration",
        ],
        check=False,
        env={
            **os.environ,
            "ECHOSIM_TEST_DATABASE_URL": f"postgresql://contract:{password}@127.0.0.1:{port}/contract",
        },
    )
    raise SystemExit(result.returncode)
finally:
    docker("rm", "-fv", name)
