"""业务建图的纯配置：provider 选择、替身开关、规模上限与预算。

本模块只使用标准库，不导入模型或 Zep SDK，供 app/worker 与测试共用。
"""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from dataclasses import dataclass
from typing import Mapping

from .settings import is_configured, is_placeholder

DEFAULT_EVIDENCE_GRAPH_MODEL_PROVIDER = "auto"
DEFAULT_EVIDENCE_GRAPH_GRAPH_PROVIDER = "auto"
DEFAULT_EVIDENCE_GRAPH_ALLOW_STUB = False
DEFAULT_EVIDENCE_GRAPH_MAX_DOCUMENTS = 5
DEFAULT_EVIDENCE_GRAPH_MAX_NODES = 200
DEFAULT_EVIDENCE_GRAPH_MAX_EDGES = 400
DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM = 5
DEFAULT_EVIDENCE_GRAPH_QUESTION_MAX_CHARS = 500
DEFAULT_EVIDENCE_GRAPH_BATCH_SIZE = 4
DEFAULT_EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS = 60.0
DEFAULT_EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS = 120.0

MODEL_PROVIDER_VALUES = ("auto", "unconfigured", "http", "stub")
GRAPH_PROVIDER_VALUES = ("auto", "unconfigured", "zep", "stub")

BUSINESS_GRAPH_ENV_VARS = (
    "EVIDENCE_GRAPH_MODEL_PROVIDER",
    "EVIDENCE_GRAPH_GRAPH_PROVIDER",
    "EVIDENCE_GRAPH_ALLOW_STUB",
    "EVIDENCE_GRAPH_MAX_DOCUMENTS",
    "EVIDENCE_GRAPH_MAX_NODES",
    "EVIDENCE_GRAPH_MAX_EDGES",
    "EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM",
    "EVIDENCE_GRAPH_QUESTION_MAX_CHARS",
    "EVIDENCE_GRAPH_BATCH_SIZE",
    "EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS",
    "EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS",
)
BUSINESS_CREDENTIAL_ENV_VARS = (
    "MODEL_API_BASE_URL",
    "MODEL_API_KEY",
    "MODEL_NAME",
    "ZEP_API_KEY",
    "ZEP_API_BASE_URL",
)


class BusinessGraphConfigError(ValueError):
    """业务建图配置不可用；`code` 是稳定原因标识。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(env: Mapping[str, str], key: str, default: str = "") -> str:
    value = env.get(key)
    return default if value is None else str(value).strip()


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _text(env, key)
    if not raw:
        return default
    lowered = raw.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _int(
    env: Mapping[str, str], key: str, default: int, *, maximum: int | None = None
) -> int:
    raw = _text(env, key)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return min(value, maximum) if maximum is not None else value


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _text(env, key)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


def normalized_graph_uuid(value: str | None) -> str | None:
    """规范化图谱 UUID；占位值或非法值返回 None。"""
    if not value:
        return None
    raw = str(value).strip()
    if not raw or is_placeholder(raw):
        return None
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        return None
    if parsed.int == 0:
        return None
    return str(parsed)


def graph_id_fingerprint(value: str | None) -> str | None:
    """对规范化 UUID 生成指纹；业务侧只比较指纹，不保存原始验收图谱 ID。"""
    normalized = normalized_graph_uuid(value)
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_fingerprint(value: str | None) -> str | None:
    """只接受 64 位十六进制指纹。"""
    if not value:
        return None
    raw = str(value).strip().lower()
    if len(raw) != 64 or any(char not in "0123456789abcdef" for char in raw):
        return None
    return raw


@dataclass(frozen=True)
class ProviderSelection:
    """解析后的 provider；真实 provider 只在配置完整时启用。"""

    model: str
    graph: str
    model_configured: bool
    graph_configured: bool
    stub_allowed: bool

    @property
    def real_model(self) -> bool:
        return self.model == "http"

    @property
    def real_graph(self) -> bool:
        return self.graph == "zep"

    @property
    def uses_stub(self) -> bool:
        return self.model == "stub" or self.graph == "stub"


@dataclass(frozen=True)
class BusinessGraphConfig:
    model_provider: str = DEFAULT_EVIDENCE_GRAPH_MODEL_PROVIDER
    graph_provider: str = DEFAULT_EVIDENCE_GRAPH_GRAPH_PROVIDER
    allow_stub: bool = DEFAULT_EVIDENCE_GRAPH_ALLOW_STUB
    max_documents: int = DEFAULT_EVIDENCE_GRAPH_MAX_DOCUMENTS
    max_nodes: int = DEFAULT_EVIDENCE_GRAPH_MAX_NODES
    max_edges: int = DEFAULT_EVIDENCE_GRAPH_MAX_EDGES
    max_sources_per_item: int = DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM
    question_max_chars: int = DEFAULT_EVIDENCE_GRAPH_QUESTION_MAX_CHARS
    batch_size: int = DEFAULT_EVIDENCE_GRAPH_BATCH_SIZE
    index_timeout_seconds: float = DEFAULT_EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS
    processing_timeout_seconds: float = (
        DEFAULT_EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS
    )
    model_configured: bool = False
    graph_configured: bool = False
    acceptance_graph_fingerprint: str | None = None

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "BusinessGraphConfig":
        env = os.environ if environ is None else environ
        model_configured = all(
            is_configured(_text(env, key))
            for key in ("MODEL_API_BASE_URL", "MODEL_API_KEY", "MODEL_NAME")
        )
        graph_configured = is_configured(_text(env, "ZEP_API_KEY"))
        return cls(
            model_provider=_text(
                env,
                "EVIDENCE_GRAPH_MODEL_PROVIDER",
                DEFAULT_EVIDENCE_GRAPH_MODEL_PROVIDER,
            ),
            graph_provider=_text(
                env,
                "EVIDENCE_GRAPH_GRAPH_PROVIDER",
                DEFAULT_EVIDENCE_GRAPH_GRAPH_PROVIDER,
            ),
            allow_stub=_bool(
                env,
                "EVIDENCE_GRAPH_ALLOW_STUB",
                DEFAULT_EVIDENCE_GRAPH_ALLOW_STUB,
            ),
            max_documents=_int(
                env,
                "EVIDENCE_GRAPH_MAX_DOCUMENTS",
                DEFAULT_EVIDENCE_GRAPH_MAX_DOCUMENTS,
                maximum=DEFAULT_EVIDENCE_GRAPH_MAX_DOCUMENTS,
            ),
            max_nodes=_int(
                env,
                "EVIDENCE_GRAPH_MAX_NODES",
                DEFAULT_EVIDENCE_GRAPH_MAX_NODES,
                maximum=DEFAULT_EVIDENCE_GRAPH_MAX_NODES,
            ),
            max_edges=_int(
                env,
                "EVIDENCE_GRAPH_MAX_EDGES",
                DEFAULT_EVIDENCE_GRAPH_MAX_EDGES,
                maximum=DEFAULT_EVIDENCE_GRAPH_MAX_EDGES,
            ),
            max_sources_per_item=_int(
                env,
                "EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM",
                DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM,
                maximum=DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM,
            ),
            question_max_chars=_int(
                env,
                "EVIDENCE_GRAPH_QUESTION_MAX_CHARS",
                DEFAULT_EVIDENCE_GRAPH_QUESTION_MAX_CHARS,
            ),
            batch_size=_int(
                env,
                "EVIDENCE_GRAPH_BATCH_SIZE",
                DEFAULT_EVIDENCE_GRAPH_BATCH_SIZE,
            ),
            index_timeout_seconds=_float(
                env,
                "EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS",
                DEFAULT_EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS,
            ),
            processing_timeout_seconds=_float(
                env,
                "EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS",
                DEFAULT_EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS,
            ),
            model_configured=model_configured,
            graph_configured=graph_configured,
            acceptance_graph_fingerprint=(
                normalize_fingerprint(
                    _text(env, "ZEP_ACCEPTANCE_GRAPH_FINGERPRINT")
                )
                or graph_id_fingerprint(_text(env, "ZEP_ACCEPTANCE_GRAPH_ID"))
            ),
        )

    def resolve_providers(self) -> ProviderSelection:
        """解析 provider；显式 stub 未开启允许开关时明确拒绝。"""
        model = self._resolve_one(
            requested=self.model_provider,
            real="http",
            configured=self.model_configured,
            allowed=MODEL_PROVIDER_VALUES,
            label="model",
        )
        graph = self._resolve_one(
            requested=self.graph_provider,
            real="zep",
            configured=self.graph_configured,
            allowed=GRAPH_PROVIDER_VALUES,
            label="graph",
        )
        return ProviderSelection(
            model=model,
            graph=graph,
            model_configured=self.model_configured,
            graph_configured=self.graph_configured,
            stub_allowed=self.allow_stub,
        )

    def _resolve_one(
        self,
        *,
        requested: str,
        real: str,
        configured: bool,
        allowed: tuple[str, ...],
        label: str,
    ) -> str:
        value = requested.strip().lower() or "auto"
        if value not in allowed:
            raise BusinessGraphConfigError(
                "invalid_provider", f"未知 {label} provider：{requested}"
            )
        if value == "auto":
            return real if configured else "unconfigured"
        if value == "unconfigured":
            return "unconfigured"
        if value == "stub":
            if not self.allow_stub:
                raise BusinessGraphConfigError(
                    "stub_not_allowed",
                    "测试替身未显式允许，拒绝启用 stub",
                )
            return "stub"
        return real if configured else "unconfigured"

    def public_dict(self) -> dict[str, object]:
        """只包含可写入报告的变量名与判定结果，不含任何密钥。"""
        selection = self.resolve_providers()
        return {
            "modelProvider": selection.model,
            "graphProvider": selection.graph,
            "modelConfigured": selection.model_configured,
            "graphConfigured": selection.graph_configured,
            "stubAllowed": selection.stub_allowed,
            "maxDocuments": self.max_documents,
            "maxNodes": self.max_nodes,
            "maxEdges": self.max_edges,
            "maxSourcesPerItem": self.max_sources_per_item,
            "questionMaxChars": self.question_max_chars,
            "batchSize": self.batch_size,
            "indexTimeoutSeconds": self.index_timeout_seconds,
            "processingTimeoutSeconds": self.processing_timeout_seconds,
        }
