"""应用配置：从环境变量读取，缺失时使用开发默认值。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录（app/config.py 的上两级），用于定位本地构建产物。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DATABASE_URL = "postgresql+asyncpg://echosim:echosim@127.0.0.1:5432/echosim"


class Settings(BaseSettings):
    """EchoSim 运行时配置。

    环境变量名与字段名一致（大小写不敏感）：
    - DATABASE_URL：SQLAlchemy async DSN，驱动固定为 asyncpg。
    - HEALTH_DB_TIMEOUT_SECONDS：`/health` 探测的整体超时上限，需保证 5 秒内返回 503。
    - WEB_DIST_DIR：前端构建产物目录，同时被静态托管与回退路由使用。
    """

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL
    health_db_timeout_seconds: float = 4.0
    web_dist_dir: Path = PROJECT_ROOT / "web" / "dist"


settings = Settings()
