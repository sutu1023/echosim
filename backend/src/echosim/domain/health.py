from typing import Protocol


class DependencyProbe(Protocol):
    async def check(self) -> bool: ...
