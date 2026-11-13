import asyncio

from echosim.domain.health import DependencyProbe


class HealthService:
    def __init__(self, probe: DependencyProbe, timeout: float):
        self.probe = probe
        self.timeout = timeout

    async def ready(self) -> bool:
        try:
            async with asyncio.timeout(self.timeout):
                return await self.probe.check()
        except Exception:
            # A readiness failure is expected and must not leak dependency details.
            return False
