from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")
    app_env: Literal["development", "test", "production"]
    app_version: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._+-]+$")
    database_url: SecretStr
    model_mode: Literal["fake"]  # M0 must never make a paid request.
    api_host: str = Field(min_length=1)
    api_port: int = Field(ge=1, le=65535)
    log_directory: Path | None = None
    probe_timeout_seconds: float = Field(default=2, gt=0, le=5)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = url.drivername == "postgresql+asyncpg" and bool(
                url.host and url.username and url.password and url.database
            )
        except Exception:
            valid = False
        if not valid:
            raise ValueError("DATABASE_URL must be a complete postgresql+asyncpg URL")
        return value

    @field_validator("log_directory")
    @classmethod
    def require_log_directory(cls, value, info: ValidationInfo):
        if info.data.get("app_env") != "test" and (value is None or not value.is_absolute()):
            raise ValueError("LOG_DIRECTORY must be an absolute path outside test mode")
        return value
