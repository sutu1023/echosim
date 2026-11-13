from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


class Fault(Exception):
    def __init__(self, code: str, status: int = 422, retry_after: int = 0):
        super().__init__(code)
        self.code, self.status, self.retry_after = code, status, retry_after


class Store(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[Any]: ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, encoded: str, password: str) -> bool: ...
