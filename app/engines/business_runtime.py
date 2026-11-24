"""业务建图运行时的 provider 装配；凭据缺失时在外部调用前失败。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .business_config import BusinessGraphConfig, BusinessGraphConfigError
from .business_model import BusinessModelPlanner, ChunkInput
from .business_stub import StubGraphClient, StubModelTransport


class ProviderCallMetrics:
    """在 provider 边界累计真实/替身调用次数。"""

    def __init__(self) -> None:
        self.model_real = 0
        self.model_double = 0
        self.graph_real = 0
        self.graph_double = 0

    @property
    def real(self) -> int:
        return self.model_real + self.graph_real

    @property
    def double(self) -> int:
        return self.model_double + self.graph_double

    def add_model(self, kind: str, count: int = 1) -> None:
        if count <= 0:
            return
        if kind == "double":
            self.model_double += count
        else:
            self.model_real += count

    def add_graph(self, kind: str, count: int = 1) -> None:
        if count <= 0:
            return
        if kind == "double":
            self.graph_double += count
        else:
            self.graph_real += count


class CountingPlanner:
    """按模型请求增量计数；local 规划恢复不产生 provider 调用。"""

    def __init__(
        self, inner: BusinessModelPlanner, metrics: ProviderCallMetrics
    ) -> None:
        self.inner = inner
        self.metrics = metrics

    @property
    def model_requests(self) -> int:
        return self.inner.model_requests

    @property
    def call_kind(self) -> str:
        return self.inner.call_kind

    def _count(self, before: int) -> None:
        self.metrics.add_model(
            self.inner.call_kind, self.inner.model_requests - before
        )

    def plan(self, **kwargs: object):
        before = self.inner.model_requests
        try:
            return self.inner.plan(**kwargs)
        finally:
            self._count(before)

    def restore_plan(self, *args: object, **kwargs: object):
        return self.inner.restore_plan(*args, **kwargs)

    def extract_batch(self, **kwargs: object):
        before = self.inner.model_requests
        try:
            return self.inner.extract_batch(**kwargs)
        finally:
            self._count(before)

    def plan_ontology(self, **kwargs: object):
        before = self.inner.model_requests
        try:
            return self.inner.plan_ontology(**kwargs)
        finally:
            self._count(before)

    def validate_result(self, *args: object, **kwargs: object):
        return self.inner.validate_result(*args, **kwargs)


class _CountingPager:
    def __init__(self, pager: object, metrics: ProviderCallMetrics, kind: str) -> None:
        self._pager = pager
        self._metrics = metrics
        self._kind = kind

    def __getattr__(self, name: str):
        return getattr(self._pager, name)

    def next_page(self):
        self._metrics.add_graph(self._kind)
        page = self._pager.next_page()
        if page is None:
            return None
        return _CountingPager(page, self._metrics, self._kind)


class _CountingListNamespace:
    def __init__(self, inner: object, metrics: ProviderCallMetrics, kind: str) -> None:
        self.inner = inner
        self.metrics = metrics
        self.kind = kind

    def list(self, *args: object, **kwargs: object):
        self.metrics.add_graph(self.kind)
        return _CountingPager(self.inner.list(*args, **kwargs), self.metrics, self.kind)


class _CountingSearchNamespace:
    def __init__(self, inner: object, metrics: ProviderCallMetrics, kind: str) -> None:
        self.inner = inner
        self.metrics = metrics
        self.kind = kind

    def search_episodes(self, *args: object, **kwargs: object):
        self.metrics.add_graph(self.kind)
        return _CountingPager(
            self.inner.search_episodes(*args, **kwargs), self.metrics, self.kind
        )


class _CountingEpisodeNamespace:
    def __init__(self, inner: object, metrics: ProviderCallMetrics, kind: str) -> None:
        self.inner = inner
        self.metrics = metrics
        self.kind = kind

    def add(self, *args: object, **kwargs: object):
        self.metrics.add_graph(self.kind)
        return self.inner.add(*args, **kwargs)

    def list(self, *args: object, **kwargs: object):
        self.metrics.add_graph(self.kind)
        return _CountingPager(
            self.inner.list(*args, **kwargs), self.metrics, self.kind
        )


class _CountingGraphNamespace:
    def __init__(self, inner: object, metrics: ProviderCallMetrics, kind: str) -> None:
        self.inner = inner
        self.metrics = metrics
        self.kind = kind
        self.node = _CountingListNamespace(inner.node, metrics, kind)
        self.edge = _CountingListNamespace(inner.edge, metrics, kind)
        self.episode = _CountingEpisodeNamespace(inner.episode, metrics, kind)

    def create(self, *args: object, **kwargs: object):
        self.metrics.add_graph(self.kind)
        return self.inner.create(*args, **kwargs)

    def get(self, *args: object, **kwargs: object):
        self.metrics.add_graph(self.kind)
        return self.inner.get(*args, **kwargs)

    def set_ontology(self, *args: object, **kwargs: object):
        self.metrics.add_graph(self.kind)
        return self.inner.set_ontology(*args, **kwargs)

    def search_episodes(self, *args: object, **kwargs: object):
        self.metrics.add_graph(self.kind)
        return _CountingPager(
            self.inner.search_episodes(*args, **kwargs), self.metrics, self.kind
        )


class CountingGraphClient:
    """在图谱 provider 边界计数，并保留底层客户端的其它属性。"""

    def __init__(self, inner: object, metrics: ProviderCallMetrics, kind: str) -> None:
        self.inner = inner
        self.graph = _CountingGraphNamespace(inner.graph, metrics, kind)

    def __getattr__(self, name: str):
        return getattr(self.inner, name)


def create_planner(
    config: BusinessGraphConfig,
    *,
    model_api_base_url: str,
    model_api_key: str,
    model_name: str,
    timeout_seconds: float,
    question: str,
    chunks: Sequence[ChunkInput],
    metrics: ProviderCallMetrics | None = None,
) -> BusinessModelPlanner:
    selection = config.resolve_providers()
    if selection.model == "stub":
        planner = BusinessModelPlanner(
            model_api_base_url=model_api_base_url or "https://stub.invalid/v1",
            model_api_key=model_api_key or "stub",
            model_name=model_name or "stub",
            timeout_seconds=timeout_seconds,
            transport=StubModelTransport(chunks=chunks, question=question),
            call_kind="double",
        )
        return (
            planner
            if metrics is None
            else CountingPlanner(planner, metrics)
        )
    if selection.model != "http":
        raise BusinessGraphConfigError(
            "missing_config", "业务模型配置不完整"
        )
    planner = BusinessModelPlanner(
        model_api_base_url=model_api_base_url,
        model_api_key=model_api_key,
        model_name=model_name,
        timeout_seconds=timeout_seconds,
    )
    return planner if metrics is None else CountingPlanner(planner, metrics)


def create_graph_client(
    config: BusinessGraphConfig,
    *,
    zep_api_key: str,
    zep_api_base_url: str,
    timeout_seconds: float,
    metrics: ProviderCallMetrics | None = None,
) -> Any:
    selection = config.resolve_providers()
    if selection.graph == "stub":
        client = StubGraphClient()
        return client if metrics is None else CountingGraphClient(client, metrics, "double")
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
    client = Zep(**kwargs)
    return client if metrics is None else CountingGraphClient(client, metrics, "real")
