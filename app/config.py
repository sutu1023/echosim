"""应用配置：从环境变量读取，缺失时使用开发默认值。"""

from __future__ import annotations

import math
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .engines.business_config import (
    DEFAULT_EVIDENCE_GRAPH_ALLOW_STUB,
    DEFAULT_EVIDENCE_GRAPH_BATCH_SIZE,
    DEFAULT_EVIDENCE_GRAPH_GRAPH_PROVIDER,
    DEFAULT_EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS,
    DEFAULT_EVIDENCE_GRAPH_MAX_DOCUMENTS,
    DEFAULT_EVIDENCE_GRAPH_MAX_EDGES,
    DEFAULT_EVIDENCE_GRAPH_MAX_NODES,
    DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM,
    DEFAULT_EVIDENCE_GRAPH_MODEL_PROVIDER,
    DEFAULT_EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS,
    DEFAULT_EVIDENCE_GRAPH_QUESTION_MAX_CHARS,
)
from .engines.environment_config import (
    DEFAULT_ENVIRONMENT_ALLOW_STUB,
    DEFAULT_ENVIRONMENT_MODEL_PROVIDER,
)
from .engines.environment_contracts import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_MAX_MANUAL_ROUNDS,
    DEFAULT_MINUTES_PER_ROUND,
    DEFAULT_MODEL_TIMEOUT_SECONDS,
    DEFAULT_PROFILE_BATCH_SIZE,
)
from .engines.settings import (
    DEFAULT_GRAPH_INDEX_TIMEOUT_SECONDS,
    DEFAULT_GRAPH_POLL_INTERVAL_SECONDS,
    DEFAULT_GRAPH_STATUS_TIMEOUT_SECONDS,
    DEFAULT_GRAPH_WRITE_TIMEOUT_SECONDS,
    DEFAULT_MODEL_API_BASE_URL,
    DEFAULT_MODEL_API_KEY,
    DEFAULT_MODEL_NAME,
    DEFAULT_MODEL_TIMEOUT_SECONDS,
    DEFAULT_SIMULATION_TIMEOUT_SECONDS,
    DEFAULT_SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS,
    DEFAULT_ZEP_ACCEPTANCE_GRAPH_ID,
    DEFAULT_ZEP_API_BASE_URL,
    DEFAULT_ZEP_API_KEY,
)

# 仓库根目录（app/config.py 的上两级），用于定位本地构建产物。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DATABASE_URL = "postgresql+asyncpg://echosim:echosim@127.0.0.1:5432/echosim"


class Settings(BaseSettings):
    """EchoSim 运行时配置。

    环境变量名与字段名一致（大小写不敏感）：
    - DATABASE_URL：SQLAlchemy async DSN，驱动固定为 asyncpg。
    - HEALTH_DB_TIMEOUT_SECONDS：`/health` 探测的整体超时上限，需保证 5 秒内返回 503。
    - WEB_DIST_DIR：前端构建产物目录，同时被静态托管与回退路由使用。
    - SESSION_TTL_SECONDS：服务端会话绝对有效期上限，默认 7 天。
    - SESSION_COOKIE_SECURE：是否为会话 Cookie 添加 `Secure`（本地 HTTP 默认关闭）。
    - JOB_LEASE_SECONDS：任务租约时长，过期后允许其它 worker 接手。
    - JOB_HEARTBEAT_SECONDS：执行中的续租间隔，必须小于租约时长。
    - WORKER_POLL_INTERVAL_SECONDS：worker 空闲时的领取轮询间隔。
    - TEST_TASK_STEP_DELAY_MS：测试任务每步之间的间隔，用于放大验收窗口。
    - DOCUMENT_STORAGE_DIR：材料原件存储根目录，按项目与材料标识分目录。
    - DOCUMENT_MAX_BYTES：单份材料字节上限，默认 20 MiB。
    - DOCUMENT_MAX_PAGES：单份 PDF 页数上限，默认 2000 页。
    - DOCUMENT_CHUNK_TARGET_CHARS：分块目标长度（字符）。
    - DOCUMENT_CHUNK_OVERLAP_CHARS：同一段落被拆开时的重叠长度（字符）。
    - DOCUMENT_INGEST_DELAY_MS：解析前的可控延迟，仅用于验收放大慢解析窗口。
    - MODEL_API_BASE_URL / MODEL_API_KEY / MODEL_NAME：Chat Completions 兼容模型配置。
    - ZEP_API_KEY / ZEP_ACCEPTANCE_GRAPH_ID：固定验收图谱配置。
    - EVIDENCE_GRAPH_*：业务建图 provider、替身开关、规模上限与预算。
    - ENVIRONMENT_*：角色画像 provider、替身开关、默认轮数与画像批次。
    - 其余 GRAPH_* / SIMULATION_* / SMOKE_LIVE_*：引擎超时与预算。
    """

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL
    health_db_timeout_seconds: float = 4.0
    web_dist_dir: Path = PROJECT_ROOT / "web" / "dist"
    session_ttl_seconds: int = 7 * 24 * 60 * 60
    session_cookie_secure: bool = False
    job_lease_seconds: float = 6.0
    job_heartbeat_seconds: float = 2.0
    worker_poll_interval_seconds: float = 0.25
    test_task_step_delay_ms: int = 250
    document_storage_dir: Path = PROJECT_ROOT / "data" / "documents"
    document_max_bytes: int = 20 * 1024 * 1024
    document_max_pages: int = 2000
    document_chunk_target_chars: int = 1200
    document_chunk_overlap_chars: int = 120
    document_ingest_delay_ms: int = 0
    model_api_base_url: str = DEFAULT_MODEL_API_BASE_URL
    model_api_key: str = DEFAULT_MODEL_API_KEY
    model_name: str = DEFAULT_MODEL_NAME
    model_timeout_seconds: float = DEFAULT_MODEL_TIMEOUT_SECONDS
    zep_api_key: str = DEFAULT_ZEP_API_KEY
    zep_api_base_url: str = DEFAULT_ZEP_API_BASE_URL
    zep_acceptance_graph_id: str = DEFAULT_ZEP_ACCEPTANCE_GRAPH_ID
    evidence_graph_model_provider: str = DEFAULT_EVIDENCE_GRAPH_MODEL_PROVIDER
    evidence_graph_graph_provider: str = DEFAULT_EVIDENCE_GRAPH_GRAPH_PROVIDER
    evidence_graph_allow_stub: bool = DEFAULT_EVIDENCE_GRAPH_ALLOW_STUB
    evidence_graph_max_documents: int = DEFAULT_EVIDENCE_GRAPH_MAX_DOCUMENTS
    evidence_graph_max_nodes: int = DEFAULT_EVIDENCE_GRAPH_MAX_NODES
    evidence_graph_max_edges: int = DEFAULT_EVIDENCE_GRAPH_MAX_EDGES
    evidence_graph_max_sources_per_item: int = (
        DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM
    )
    evidence_graph_question_max_chars: int = (
        DEFAULT_EVIDENCE_GRAPH_QUESTION_MAX_CHARS
    )
    evidence_graph_batch_size: int = DEFAULT_EVIDENCE_GRAPH_BATCH_SIZE
    evidence_graph_index_timeout_seconds: float = (
        DEFAULT_EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS
    )
    evidence_graph_processing_timeout_seconds: float = (
        DEFAULT_EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS
    )
    environment_model_provider: str = DEFAULT_ENVIRONMENT_MODEL_PROVIDER
    environment_allow_stub: bool = DEFAULT_ENVIRONMENT_ALLOW_STUB
    environment_default_duration_hours: int = DEFAULT_DURATION_HOURS
    environment_default_minutes_per_round: int = DEFAULT_MINUTES_PER_ROUND
    environment_max_manual_rounds: int = DEFAULT_MAX_MANUAL_ROUNDS
    environment_profile_batch_size: int = DEFAULT_PROFILE_BATCH_SIZE
    environment_model_timeout_seconds: float = DEFAULT_MODEL_TIMEOUT_SECONDS
    graph_write_timeout_seconds: float = DEFAULT_GRAPH_WRITE_TIMEOUT_SECONDS
    graph_status_timeout_seconds: float = DEFAULT_GRAPH_STATUS_TIMEOUT_SECONDS
    graph_index_timeout_seconds: float = DEFAULT_GRAPH_INDEX_TIMEOUT_SECONDS
    graph_poll_interval_seconds: float = DEFAULT_GRAPH_POLL_INTERVAL_SECONDS
    simulation_timeout_seconds: float = DEFAULT_SIMULATION_TIMEOUT_SECONDS
    smoke_live_total_timeout_seconds: float = DEFAULT_SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS

    @field_validator(
        "evidence_graph_max_documents",
        "evidence_graph_max_nodes",
        "evidence_graph_max_edges",
        "evidence_graph_max_sources_per_item",
    )
    @classmethod
    def _clamp_evidence_graph_caps(cls, value: int, info: object) -> int:
        caps = {
            "evidence_graph_max_documents": DEFAULT_EVIDENCE_GRAPH_MAX_DOCUMENTS,
            "evidence_graph_max_nodes": DEFAULT_EVIDENCE_GRAPH_MAX_NODES,
            "evidence_graph_max_edges": DEFAULT_EVIDENCE_GRAPH_MAX_EDGES,
            "evidence_graph_max_sources_per_item": (
                DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM
            ),
        }
        field_name = getattr(info, "field_name", "")
        limit = caps.get(field_name, value)
        if value <= 0:
            return limit
        return min(value, limit)

    @field_validator(
        "evidence_graph_index_timeout_seconds",
        "evidence_graph_processing_timeout_seconds",
    )
    @classmethod
    def _finite_evidence_graph_budget(cls, value: float, info: object) -> float:
        defaults = {
            "evidence_graph_index_timeout_seconds": (
                DEFAULT_EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS
            ),
            "evidence_graph_processing_timeout_seconds": (
                DEFAULT_EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS
            ),
        }
        default = defaults.get(getattr(info, "field_name", ""), value)
        if not math.isfinite(value) or value <= 0:
            return default
        return value


settings = Settings()
