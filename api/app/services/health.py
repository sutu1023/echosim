import asyncio
from datetime import UTC, datetime
from typing import Protocol

from app.build_info import BUILD_ID


class Probe(Protocol):
    async def ping(self) -> None: ...


class HealthService:
    def __init__(self, probe: Probe, version: str, timeout: float):
        self.probe, self.version, self.timeout = probe, version, timeout
        self.draining = False

    async def inspect(self) -> tuple[bool, dict]:
        ready = False
        if not self.draining:
            try:
                async with asyncio.timeout(self.timeout):
                    await self.probe.ping()
                ready = True
            except Exception:
                pass  # Never include database exceptions/DSNs in user output or logs.
        return ready, {
            "status": "ready" if ready else "not_ready",
            "version": self.version,
            "build_id": BUILD_ID,
            "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "dependencies": {"postgres": "up" if ready else "unavailable"},
        }
