"""业务建图运行时的 provider 装配；凭据缺失时在外部调用前失败。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .business_config import BusinessGraphConfig, BusinessGraphConfigError
from .business_model import BusinessModelPlanner, ChunkInput
from .business_stub import StubGraphClient, StubModelTransport


def create_planner(
    config: BusinessGraphConfig,
    *,
    model_api_base_url: str,
    model_api_key: str,
    model_name: str,
    timeout_seconds: float,
    question: str,
    chunks: Sequence[ChunkInput],
) -> BusinessModelPlanner:
    selection = config.resolve_providers()
    if selection.model == "stub":
        return BusinessModelPlanner(
            model_api_base_url=model_api_base_url or "https://stub.invalid/v1",
            model_api_key=model_api_key or "stub",
            model_name=model_name or "stub",
            timeout_seconds=timeout_seconds,
            transport=StubModelTransport(chunks=chunks, question=question),
            call_kind="double",
        )
    if selection.model != "http":
        raise BusinessGraphConfigError(
            "missing_config", "业务模型配置不完整"
        )
    return BusinessModelPlanner(
        model_api_base_url=model_api_base_url,
        model_api_key=model_api_key,
        model_name=model_name,
        timeout_seconds=timeout_seconds,
    )


def create_graph_client(
    config: BusinessGraphConfig,
    *,
    zep_api_key: str,
    zep_api_base_url: str,
    timeout_seconds: float,
) -> Any:
    selection = config.resolve_providers()
    if selection.graph == "stub":
        return StubGraphClient()
    if selection.graph != "zep":
        raise BusinessGraphConfigError(
            "missing_config", "业务图谱配置不完整"
        )
    from zep_cloud import Zep

    kwargs: dict[str, Any] = {
        "api_key": zep_api_key,
        "timeout": timeout_seconds,
        "max_retries": 0,
    }
    if zep_api_base_url:
        kwargs["base_url"] = zep_api_base_url
    return Zep(**kwargs)
