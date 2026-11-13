from typing import Protocol

MODEL = "deepseek-v4.1-flash"


class ModelFailure(Exception):
    def __init__(self, code: str, status: int = 502):
        self.code = code
        self.status = status
        super().__init__(code)


class ModelProvider(Protocol):
    async def invoke(self, key: str) -> dict: ...
