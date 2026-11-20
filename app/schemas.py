"""请求体校验：邮箱、密码与项目字段。"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from .security import normalize_email, validate_email

PROJECT_NAME_MAX_LENGTH = 80
PROJECT_DESCRIPTION_MAX_LENGTH = 500


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        error = validate_email(normalized)
        if error:
            raise ValueError(error)
        return normalized

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        if not 1 <= len(value) <= 128:
            raise ValueError("密码长度必须在 1–128 字符之间")
        return value


class ProjectCreateRequest(BaseModel):
    name: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        stripped = value.strip()
        if not 1 <= len(stripped) <= PROJECT_NAME_MAX_LENGTH:
            raise ValueError(f"项目名称长度必须在 1–{PROJECT_NAME_MAX_LENGTH} 字符之间")
        return stripped

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > PROJECT_DESCRIPTION_MAX_LENGTH:
            raise ValueError(
                f"项目描述长度不能超过 {PROJECT_DESCRIPTION_MAX_LENGTH} 字符"
            )
        return stripped
