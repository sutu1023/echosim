from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, PostgresDsn, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"]
    model_connection_test_enabled: bool = False
    dev_quick_login_enabled: bool = False
    public_origin: str = ""
    model_master_key: SecretStr = SecretStr("")
    app_version: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    database_url: SecretStr
    port: int = Field(ge=1, le=65535)
    health_timeout_seconds: float = Field(ge=1, le=10)
    worker_poll_seconds: float = Field(ge=1, le=60)

    @field_validator("public_origin")
    @classmethod
    def valid_origin(cls, value):
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("invalid origin")
        return value

    @field_validator("database_url")
    @classmethod
    def valid_database_url(cls, value: SecretStr) -> SecretStr:
        parsed = PostgresDsn(value.get_secret_value())
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ValueError("unsupported database scheme")
        return value


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError:
        # Never print raw settings errors: they can contain credentials.
        raise SystemExit(
            "Invalid runtime configuration; check required environment variables"
        ) from None
