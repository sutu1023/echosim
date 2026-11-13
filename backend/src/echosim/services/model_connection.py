import asyncio
import time

from echosim.domain.model_connection import MODEL, ModelFailure, ModelProvider


class ModelConnectionService:
    def __init__(self, provider: ModelProvider, timeout: float = 60):
        self.provider = provider
        self.timeout = timeout

    async def test(self, key: str) -> dict:
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.timeout):
                data = await self.provider.invoke(key)
        except TimeoutError:
            raise ModelFailure("MODEL_TIMEOUT", 504) from None
        except ModelFailure:
            raise
        except Exception:
            raise ModelFailure("MODEL_UPSTREAM_ERROR") from None
        try:
            content = data["choices"][0]["message"]["content"]
            if "error" in data or not isinstance(content, str) or not content.strip():
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelFailure("MODEL_INVALID_RESPONSE") from None
        usage = data.get("usage")
        fields = ("prompt_tokens", "completion_tokens", "total_tokens")
        if not isinstance(usage, dict) or not all(
            type(usage.get(k)) is int and usage[k] >= 0 for k in fields
        ):
            usage = None
        else:
            usage = {k: usage[k] for k in fields}
        return {
            "status": "success",
            "provider": "OpenCode Go",
            "model": MODEL,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "usage": usage,
        }
