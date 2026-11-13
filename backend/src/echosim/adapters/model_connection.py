import asyncio
import ipaddress
import json
import socket
from uuid import uuid4

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from echosim.domain.model_connection import MODEL, ModelFailure

HOST = "opencode.ai"


class PublicBackend(AutoBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != HOST or port != 443:
            raise ModelFailure("MODEL_UPSTREAM_ERROR")
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        ips = [item[4][0] for item in addresses]
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ModelFailure("MODEL_UPSTREAM_ERROR")
        # Connect to a validated numeric address, keeping the original hostname for TLS/SNI.
        return await super().connect_tcp(ips[0], port, timeout, local_address, socket_options)


class OpenCodeProvider:
    async def invoke(self, key: str) -> dict:
        transport = httpx.AsyncHTTPTransport(retries=0)
        # httpx has no public network-backend hook; this seam is covered by transport tests.
        transport._pool = httpcore.AsyncConnectionPool(network_backend=PublicBackend())
        async with httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False, timeout=60
        ) as client:
            async with client.stream(
                "POST",
                f"https://{HOST}/inference/go/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "EchoSim-Connection-Test/0.0.1",
                    "x-opencode-session": str(uuid4()),
                },
                json={
                    "model": MODEL,
                    "stream": False,
                    "max_tokens": 256,
                    "messages": [{"role": "user", "content": "请仅回复：连接测试成功。"}],
                },
            ) as response:
                if response.status_code in (401, 403):
                    raise ModelFailure("MODEL_AUTH_FAILED")
                if response.status_code == 429:
                    raise ModelFailure("MODEL_RATE_LIMITED", 429)
                if not 200 <= response.status_code < 300:
                    raise ModelFailure("MODEL_UPSTREAM_ERROR")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 1048576:
                        raise ModelFailure("MODEL_INVALID_RESPONSE")
                try:
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError
                    return data
                except (ValueError, UnicodeError):
                    raise ModelFailure("MODEL_INVALID_RESPONSE") from None
