"""密码哈希、邮箱规范化与输入校验。

使用标准库 scrypt，避免新增二进制依赖；哈希字符串自带参数前缀，便于将来升级。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX_LENGTH = 254
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen
    )


def hash_password(password: str) -> str:
    """生成 `scrypt$n$r$p$salt$hash` 形式的哈希。"""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN)
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}"
        f"${_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_password(password: str, encoded: str) -> bool:
    """校验密码；任何解析失败都按不匹配处理。"""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
        actual = _scrypt(password, salt, int(n), int(r), int(p), len(expected))
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> str | None:
    """返回错误文案，合法时返回 None。"""
    normalized = normalize_email(email)
    if not normalized:
        return "邮箱不能为空"
    if len(normalized) > EMAIL_MAX_LENGTH:
        return f"邮箱长度不能超过 {EMAIL_MAX_LENGTH} 字符"
    if not EMAIL_PATTERN.fullmatch(normalized):
        return "邮箱格式不正确"
    return None


def validate_new_password(password: str) -> str | None:
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"密码长度不能少于 {PASSWORD_MIN_LENGTH} 字符"
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"密码长度不能超过 {PASSWORD_MAX_LENGTH} 字符"
    return None


# 未知邮箱也执行一次哈希校验，减少“账户是否存在”的时间侧信道。
DUMMY_PASSWORD_HASH = hash_password("echosim-dummy-password-for-timing")
