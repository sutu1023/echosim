import contextlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn

from app.config import Settings
from app.main import create_app


class Probe:
    def __init__(self, failure=False, delay=0):
        self.failure, self.delay = failure, delay

    async def ping(self):
        import asyncio

        await asyncio.sleep(self.delay)
        if self.failure:
            raise RuntimeError("must-not-leak-password")


@pytest.fixture
def settings():
    return Settings(
        app_env="test",
        app_version="test-v1",
        model_mode="fake",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        api_host="127.0.0.1",
        api_port=8000,
        probe_timeout_seconds=0.1,
    )


@contextlib.contextmanager
def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "test server failed to start"
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive(), "leaked test server"


def request(base, path="/health", method="GET", headers=None):
    req = urllib.request.Request(base + path, method=method, headers=headers or {})
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), json.loads(response.read())


@pytest.fixture
def healthy(settings):
    with serve(create_app(settings, Probe())) as url:
        yield url
