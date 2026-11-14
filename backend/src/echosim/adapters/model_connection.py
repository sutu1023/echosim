import asyncio
import ipaddress
import json
import socket
import time
from uuid import uuid4

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from echosim.domain.model_connection import MODEL, ModelFailure
from echosim.logging import emit

HOST = "opencode.ai"


class PublicBackend(AutoBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != HOST or port != 443:
            raise ModelFailure("MODEL_UPSTREAM_ERROR")
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        ips = list(dict.fromkeys(item[4][0] for item in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ModelFailure("MODEL_UPSTREAM_ERROR")
        # Every fallback stays pinned to this validated DNS answer; TLS keeps the hostname.
        for index, ip in enumerate(ips):
            try:
                return await super().connect_tcp(
                    ip, port, min(timeout or 5, 5), local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError):
                if index == len(ips) - 1:
                    raise


class OpenCodeProvider:
    def __init__(self):
        self._client = None

    @staticmethod
    def _new_client(retries=1):
        transport = httpx.AsyncHTTPTransport()
        # Connection retries cover only TCP/TLS setup, before any request bytes are sent.
        transport._pool = httpcore.AsyncConnectionPool(
            network_backend=PublicBackend(), retries=retries, keepalive_expiry=60
        )
        return httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False, timeout=60
        )

    async def __aenter__(self):
        self._client = self._new_client()
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()
        self._client = None

    async def invoke(self, key: str) -> dict:
        return await self._request(
            key, MODEL, [{"role": "user", "content": "请仅回复：连接测试成功。"}], 256
        )

    async def generate(
        self,
        key,
        model,
        messages,
        *,
        max_tokens=2048,
        temperature=0.7,
        prompt_version="creative-v1",
        configuration_version=None,
        session_id=None,
        on_text=None,
    ):
        started = time.monotonic()
        usage = None
        try:
            async with asyncio.timeout(60):
                data = await self._request(
                    key, model, messages, max_tokens, temperature, session_id, on_text
                )
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ModelFailure("MODEL_INVALID_RESPONSE")
            content = choice["message"]["content"]
            if "error" in data or not isinstance(content, str) or not content.strip():
                raise ModelFailure("MODEL_INVALID_RESPONSE")
            raw = data.get("usage")
            fields = ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(raw, dict) and all(
                type(raw.get(k)) is int and raw[k] >= 0 for k in fields
            ):
                usage = {k: raw[k] for k in fields}
            return {"text": content, "usage": usage}
        except TimeoutError:
            raise ModelFailure("MODEL_TIMEOUT", 504) from None
        except ModelFailure:
            raise
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelFailure("MODEL_INVALID_RESPONSE") from None
        except Exception as exc:
            kinds, cause = [], exc
            while cause is not None and len(kinds) < 4:
                kinds.append(type(cause).__name__)
                cause = cause.__cause__
            emit("model_transport_error", service="model", error_code="/".join(kinds))
            raise ModelFailure("MODEL_UPSTREAM_ERROR") from None
        finally:
            emit(
                "model_invocation",
                service="model",
                model=model,
                configuration_version=configuration_version,
                prompt_version=prompt_version,
                timeout_seconds=60,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort="low" if model == MODEL else "provider-default",
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                usage=usage,
            )

    async def _request(
        self, key, model, messages, max_tokens, temperature=None, session_id=None, on_text=None
    ):
        creative = temperature is not None and max_tokens > 256
        client = self._client or self._new_client(retries=0)
        try:
            # Do not merge a shared client's cookies into a different user's request.
            request = httpx.Request(
                "POST",
                f"https://{HOST}/inference/go/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "EchoSim-Connection-Test/0.0.1",
                    "x-opencode-session": session_id or str(uuid4()),
                },
                json={
                    "model": model,
                    "stream": creative,
                    **({"stream_options": {"include_usage": True}} if creative else {}),
                    "max_tokens": max_tokens,
                    "messages": messages,
                    **({"reasoning_effort": "low"} if model == MODEL else {}),
                    **({"temperature": temperature} if temperature is not None else {}),
                },
            )
            response = await client.send(request, stream=True)
            try:
                emit("model_http_result", service="model", status=response.status_code, model=model)
                if response.status_code in (401, 403):
                    raise ModelFailure("MODEL_AUTH_FAILED")
                if response.status_code == 429:
                    raise ModelFailure("MODEL_RATE_LIMITED", 429)
                if not 200 <= response.status_code < 300:
                    raise ModelFailure("MODEL_UPSTREAM_ERROR")
                body = bytearray()
                if on_text and response.headers.get("content-type", "").startswith(
                    "text/event-stream"
                ):
                    pending = []
                    last = time.monotonic()
                    async for line in response.aiter_lines():
                        body.extend((line + "\n").encode())
                        if len(body) > 1048576:
                            raise ModelFailure("MODEL_INVALID_RESPONSE")
                        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                            continue
                        packet = json.loads(line[5:])
                        for choice in packet.get("choices", []):
                            if choice.get("index", 0) == 0:
                                pending.append(choice.get("delta", {}).get("content") or "")
                        if pending and time.monotonic() - last >= 0.3:
                            await on_text("".join(pending))
                            pending.clear()
                            last = time.monotonic()
                    if pending:
                        await on_text("".join(pending))
                    return complete_stream(body)
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 1048576:
                        raise ModelFailure("MODEL_INVALID_RESPONSE")
                try:
                    if creative and response.headers.get("content-type", "").startswith(
                        "text/event-stream"
                    ):
                        return complete_stream(body)
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError
                    return data
                except (ValueError, UnicodeError):
                    raise ModelFailure("MODEL_INVALID_RESPONSE") from None
            finally:
                await response.aclose()
        finally:
            if client is not self._client:
                await client.aclose()


def complete_stream(body):
    """Reassemble the bounded upstream SSE response before parsing or publishing any story."""
    pieces = []
    reason = usage = None
    for line in body.decode().splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            if reason is None:
                break
            return {
                "choices": [{"finish_reason": reason, "message": {"content": "".join(pieces)}}],
                "usage": usage,
            }
        packet = json.loads(payload)
        if not isinstance(packet, dict) or "error" in packet:
            break
        if packet.get("usage") is not None:
            usage = packet["usage"]
        for choice in packet.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            fragment = choice.get("delta", {}).get("content")
            if fragment is not None:
                if not isinstance(fragment, str):
                    raise ModelFailure("MODEL_INVALID_RESPONSE")
                pieces.append(fragment)
            reason = choice.get("finish_reason") or reason
    raise ModelFailure("MODEL_INVALID_RESPONSE")
