"""报告与日志的脱敏工具。"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

SENSITIVE_KEY_PARTS = (
    "key",
    "secret",
    "token",
    "authorization",
    "password",
    "credential",
)

REDACTED = "[redacted]"


def redact_text(text: str, secrets: tuple[str, ...] | list[str] = ()) -> str:
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, REDACTED)
    return result


def redact_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, host + port, parts.path, "", ""))


def sanitize_mapping(value: Any, secrets: tuple[str, ...] | list[str] = ()) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                cleaned[key_text] = REDACTED
            else:
                cleaned[key_text] = sanitize_mapping(item, secrets)
        return cleaned
    if isinstance(value, list):
        return [sanitize_mapping(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [sanitize_mapping(item, secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secrets)
    return value


def contains_secret(value: Any, secret: str) -> bool:
    if not secret:
        return False
    if isinstance(value, str):
        return secret in value
    if isinstance(value, Mapping):
        return any(contains_secret(item, secret) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(item, secret) for item in value)
    return False
