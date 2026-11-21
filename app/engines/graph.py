"""Zep 固定验收图谱：episode 写入、同键重试与 search_episodes 精确核对。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .contracts import (
    AcceptanceMaterial,
    CallCounts,
    ItemResult,
    PASSED,
    FAILED,
    NOT_RUN,
    build_graph_fact,
)
from .settings import EngineSettings

WRITE_CONFIRM_BUDGET_SECONDS = 60.0


class GraphCallError(Exception):
    """图谱调用失败的稳定分类；测试替身也使用同一异常。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_lost: bool = False,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_lost = response_lost
        self.retryable = retryable


@dataclass(frozen=True)
class SearchOutcome:
    match_uuid: str | None
    wrong_uuid: bool
    error: GraphCallError | None


def _default_client_factory(settings: EngineSettings) -> Any:
    from zep_cloud import Zep

    kwargs: dict[str, Any] = {
        "api_key": settings.zep_api_key,
        "timeout": settings.graph_write_timeout_seconds,
        "max_retries": 0,
    }
    if settings.zep_api_base_url:
        kwargs["base_url"] = settings.zep_api_base_url
    return Zep(**kwargs)


def _episode_uuid(item: object) -> str | None:
    if isinstance(item, dict):
        value = item.get("uuid") or item.get("uuid_")
        return str(value) if value else None
    for attribute in ("uuid_", "uuid"):
        value = getattr(item, attribute, None)
        if value:
            return str(value)
    return None


def _episode_content(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("content") or "")
    return str(getattr(item, "content", "") or "")


def _episode_metadata(item: object) -> dict[str, Any]:
    if isinstance(item, dict):
        value = item.get("metadata")
    else:
        value = getattr(item, "metadata", None)
    return value if isinstance(value, dict) else {}


def _result_episode_uuid(result: object) -> str | None:
    if isinstance(result, dict):
        episode = result.get("episode")
    else:
        episode = getattr(result, "episode", None)
    return _episode_uuid(episode) if episode is not None else None


def _close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()
        return
    wrapper = getattr(client, "_client_wrapper", None)
    http_client = getattr(wrapper, "httpx_client", None) if wrapper else None
    close = getattr(http_client, "close", None)
    if callable(close):
        close()


def _classify_exception(exc: BaseException) -> GraphCallError:
    if isinstance(exc, GraphCallError):
        return exc
    status: int | None = None
    for attribute in ("status_code", "status", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            status = value
            break
    text = f"{type(exc).__name__} {exc}".lower()
    retryable = status in {408, 429, 500, 502, 503, 504} or any(
        token in text for token in ("timeout", "timed out", "connection reset", "connect error")
    )
    return GraphCallError(
        f"{type(exc).__name__}: {exc}",
        status_code=status,
        response_lost=retryable,
        retryable=retryable,
    )


def _is_auth_error(exc: GraphCallError) -> bool:
    return exc.status_code in {401, 403}


class GraphRunner:
    def __init__(
        self,
        settings: EngineSettings,
        *,
        client_factory: Callable[[EngineSettings], Any] | None = None,
        call_kind: str = "real",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory or _default_client_factory
        self.call_kind = "double" if call_kind == "double" else "real"
        self.clock = clock
        self.sleep = sleep

    def _calls(self, count: int) -> CallCounts:
        return CallCounts(
            real=count if self.call_kind == "real" else 0,
            double=count if self.call_kind == "double" else 0,
        )

    def run(self, material: AcceptanceMaterial) -> ItemResult:
        started = self.clock()
        if self.settings.missing_graph:
            return ItemResult(
                "graph.searchable",
                "graph",
                "episode-search",
                NOT_RUN,
                "missing_config",
                0,
                CallCounts(),
                {"missing": list(self.settings.missing_graph)},
            )
        graph_uuid = self.settings.zep_acceptance_graph_id
        # 每次新的逻辑写入只生成一次 UUIDv4；响应丢失后的重试复用同一键。
        idempotency_key = str(uuid.uuid4())
        fact = build_graph_fact(material)
        metadata = {
            "runId": material.run_id,
            "marker": material.marker,
            "purpose": "echosim-acceptance",
        }
        calls = 0
        write_attempts = 0
        search_attempts = 0
        write_lost = False
        first_search_miss = False
        accepted = False
        state = "submitting"
        episode_id: str | None = None
        client: object | None = None

        try:
            client = self.client_factory(self.settings)
            write_deadline = started + WRITE_CONFIRM_BUDGET_SECONDS
            while write_attempts < 2 and self.clock() < write_deadline:
                write_attempts += 1
                try:
                    result = client.graph.episode.add(  # type: ignore[attr-defined]
                        graph_uuid,
                        data=fact,
                        metadata=metadata,
                        idempotency_key=idempotency_key,
                        type="text",
                    )
                    calls += 1
                    episode_id = _result_episode_uuid(result)
                    if episode_id:
                        accepted = True
                        state = "accepted"
                        break
                    write_lost = True
                except Exception as exc:  # noqa: BLE001 - SDK 异常按稳定分类处理
                    calls += 1
                    error = _classify_exception(exc)
                    if _is_auth_error(error):
                        return self._result(
                            FAILED,
                            "auth_failed",
                            started,
                            calls,
                            write_attempts,
                            search_attempts,
                            accepted,
                            state,
                            episode_id,
                            idempotency_key,
                            write_lost,
                            first_search_miss,
                            detail="写入认证失败",
                        )
                    if not (error.response_lost or error.retryable):
                        return self._result(
                            FAILED,
                            "write_failed",
                            started,
                            calls,
                            write_attempts,
                            search_attempts,
                            accepted,
                            state,
                            episode_id,
                            idempotency_key,
                            write_lost,
                            first_search_miss,
                            detail="写入失败",
                        )
                    write_lost = True

                search_attempts += 1
                outcome = self._search(client, graph_uuid, material, episode_id)
                calls += 1
                if outcome.match_uuid:
                    episode_id = outcome.match_uuid
                    accepted = True
                    state = "searchable"
                    return self._result(
                        PASSED,
                        None,
                        started,
                        calls,
                        write_attempts,
                        search_attempts,
                        accepted,
                        state,
                        episode_id,
                        idempotency_key,
                        write_lost,
                        False,
                    )
                if outcome.error and _is_auth_error(outcome.error):
                    return self._result(
                        FAILED,
                        "auth_failed",
                        started,
                        calls,
                        write_attempts,
                        search_attempts,
                        accepted,
                        state,
                        episode_id,
                        idempotency_key,
                        write_lost,
                        first_search_miss,
                        detail="检索认证失败",
                    )
                if outcome.wrong_uuid:
                    return self._result(
                        FAILED,
                        "stale_or_foreign_hit",
                        started,
                        calls,
                        write_attempts,
                        search_attempts,
                        accepted,
                        state,
                        episode_id,
                        idempotency_key,
                        write_lost,
                        first_search_miss,
                        detail="运行标识出现在错误 episode UUID 上",
                    )
                if search_attempts == 1:
                    first_search_miss = True
                state = "indexing"
                if write_attempts < 2:
                    self.sleep(min(1.0, max(0.0, write_deadline - self.clock())))

            if not accepted and not write_lost:
                return self._result(
                    FAILED,
                    "write_failed",
                    started,
                    calls,
                    write_attempts,
                    search_attempts,
                    accepted,
                    state,
                    episode_id,
                    idempotency_key,
                    write_lost,
                    first_search_miss,
                    detail="写入未受理",
                )

            index_deadline = self.clock() + self.settings.graph_index_timeout_seconds
            while self.clock() < index_deadline:
                search_attempts += 1
                outcome = self._search(client, graph_uuid, material, episode_id)
                calls += 1
                if outcome.match_uuid:
                    episode_id = outcome.match_uuid
                    accepted = True
                    state = "searchable"
                    return self._result(
                        PASSED,
                        None,
                        started,
                        calls,
                        write_attempts,
                        search_attempts,
                        accepted,
                        state,
                        episode_id,
                        idempotency_key,
                        write_lost,
                        first_search_miss,
                    )
                if outcome.error and _is_auth_error(outcome.error):
                    return self._result(
                        FAILED,
                        "auth_failed",
                        started,
                        calls,
                        write_attempts,
                        search_attempts,
                        accepted,
                        state,
                        episode_id,
                        idempotency_key,
                        write_lost,
                        first_search_miss,
                        detail="检索认证失败",
                    )
                if outcome.wrong_uuid:
                    return self._result(
                        FAILED,
                        "stale_or_foreign_hit",
                        started,
                        calls,
                        write_attempts,
                        search_attempts,
                        accepted,
                        state,
                        episode_id,
                        idempotency_key,
                        write_lost,
                        first_search_miss,
                        detail="运行标识出现在错误 episode UUID 上",
                    )
                if search_attempts == 1:
                    first_search_miss = True
                state = "indexing"
                self.sleep(min(self.settings.graph_poll_interval_seconds, max(0.0, index_deadline - self.clock())))

            if accepted:
                return self._result(
                    FAILED,
                    "index_timeout",
                    started,
                    calls,
                    write_attempts,
                    search_attempts,
                    accepted,
                    state,
                    episode_id,
                    idempotency_key,
                    write_lost,
                    first_search_miss,
                )
            return self._result(
                FAILED,
                "write_unconfirmed",
                started,
                calls,
                write_attempts,
                search_attempts,
                accepted,
                state,
                episode_id,
                idempotency_key,
                write_lost,
                first_search_miss,
            )
        finally:
            if client is not None:
                try:
                    _close_client(client)
                except Exception:  # noqa: BLE001 - 关闭失败不能掩盖主结果
                    pass

    def _search(
        self,
        client: object,
        graph_uuid: str,
        material: AcceptanceMaterial,
        expected_uuid: str | None,
    ) -> SearchOutcome:
        fact = build_graph_fact(material)
        try:
            pager = client.graph.search_episodes(  # type: ignore[attr-defined]
                graph_uuid,
                query=material.marker,
                page_size=50,
            )
            items = getattr(pager, "items", None)
            if items is None and isinstance(pager, list):
                items = pager

            wrong_uuid_seen = False
            for item in items or []:
                uuid = _episode_uuid(item)
                content = _episode_content(item)
                metadata = _episode_metadata(item)
                metadata_hint = (
                    metadata.get("marker") == material.marker
                    or metadata.get("runId") == material.run_id
                )
                content_marker = material.marker in content
                content_fact = fact in content

                # 元数据只用于把候选项纳入扫描；它本身不能构成命中证据。
                if not (metadata_hint or content_marker or content_fact):
                    continue

                uuid_match = expected_uuid is None or uuid == expected_uuid
                if uuid and uuid_match and content_marker and content_fact:
                    return SearchOutcome(uuid, False, None)

                # 正文携带本次标记与唯一事实、但 UUID 不匹配的旧/外部记录，
                # 不能在扫描结束前遮蔽后面的精确命中。
                if (
                    expected_uuid is not None
                    and not uuid_match
                    and content_marker
                    and content_fact
                ):
                    wrong_uuid_seen = True

            return SearchOutcome(None, wrong_uuid_seen, None)
        except Exception as exc:  # noqa: BLE001 - 检索异常按稳定分类处理
            return SearchOutcome(None, False, _classify_exception(exc))

    def _result(
        self,
        status: str,
        reason: str | None,
        started: float,
        calls: int,
        write_attempts: int,
        search_attempts: int,
        accepted: bool,
        state: str,
        episode_id: str | None,
        idempotency_key: str,
        write_lost: bool,
        first_search_miss: bool,
        *,
        detail: str = "",
    ) -> ItemResult:
        return ItemResult(
            "graph.searchable",
            "graph",
            "episode-search",
            status,
            reason,
            int((self.clock() - started) * 1000),
            self._calls(calls),
            {
                "accepted": accepted,
                "state": state,
                "episodeId": episode_id,
                "idempotencyKey": idempotency_key,
                "writeAttempts": write_attempts,
                "searchAttempts": search_attempts,
                "writeResponseLost": write_lost,
                "firstSearchMiss": first_search_miss,
                "indexWaitMs": int((self.clock() - started) * 1000),
                "detail": detail,
            },
        )
