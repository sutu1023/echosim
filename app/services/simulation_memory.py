"""运行时序记忆：行动记账、按轮读模型、增量投影、查账与记忆同步任务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..engines import temporal_memory as contract
from ..engines.business_config import BusinessGraphConfig
from ..engines.business_graph import (
    BusinessGraphError,
    readback_raw_graph,
    write_episode,
)
from ..engines.business_runtime import create_graph_client
from ..engines.duet_contracts import (
    PLATFORM_MAPPING,
    PlannedAction,
)
from ..errors import LeaseLost, ServiceUnavailable, SimulationConflict
from . import run_events
from . import worker as worker_service

logger = logging.getLogger(__name__)

MEMORY_STAGE = "dual-platform-run"
MEMORY_KIND = "simulation.memory"
MEMORY_TOTAL_STEPS = 3
MEMORY_STEP_KEYS = ("bind", "sync", "publish")
MEMORY_OPERATION_PREFIX = "simulation-memory:v1:"
DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)


class MemoryUnavailable(Exception):
    """记忆投影暂时不可用；`code` 为稳定原因码。"""

    def __init__(self, code: str = "memory_unavailable", message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True)
class MemoryConfig:
    provider: str = "unconfigured"
    max_refs: int = 20
    max_nodes: int = 2000
    max_edges: int = 4000
    index_timeout_seconds: float = 30.0
    processing_timeout_seconds: float = 60.0
    retry_budget_seconds: float = 120.0
    batch_size: int = 20
    acceptance_graph_fingerprint: str | None = None
    accept_pause_seconds: float = 0.0
    sync_pause_seconds: float = 0.0
    fault: str = ""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "MemoryConfig":
        env = os.environ if environ is None else environ
        requested = str(env.get("SIMULATION_MEMORY_PROVIDER", "auto") or "auto").lower()
        allow_stub = str(env.get("EVIDENCE_GRAPH_ALLOW_STUB", "")).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        graph_provider = str(
            env.get("EVIDENCE_GRAPH_GRAPH_PROVIDER", "auto") or "auto"
        ).lower()
        zep_key = str(env.get("ZEP_API_KEY", "") or "").strip()
        if requested == "stub":
            # 显式选择 stub 即授权离线运行；auto 仍需 allow_stub 才启用。
            provider = "stub"
        elif requested == "zep" and zep_key:
            provider = "zep"
        elif requested == "auto" and allow_stub and graph_provider in {"auto", "stub"}:
            provider = "stub"
        elif requested == "auto" and zep_key:
            provider = "zep"
        elif requested in {"stub", "zep", "unconfigured"}:
            provider = "unconfigured"
        else:
            provider = "unconfigured"

        def _int(key: str, default: int, low: int, high: int) -> int:
            try:
                value = int(str(env.get(key, default)).strip())
            except (TypeError, ValueError):
                return default
            return value if low <= value <= high else default

        def _float(key: str, default: float) -> float:
            try:
                value = float(str(env.get(key, default)).strip())
            except (TypeError, ValueError):
                return default
            return value if value > 0 else default

        return cls(
            provider=provider,
            max_refs=_int("SIMULATION_MEMORY_MAX_REFS", 20, 1, 200),
            max_nodes=_int("SIMULATION_MEMORY_MAX_NODES", 2000, 1, 100000),
            max_edges=_int("SIMULATION_MEMORY_MAX_EDGES", 4000, 1, 200000),
            index_timeout_seconds=_float("SIMULATION_MEMORY_INDEX_TIMEOUT_SECONDS", 30.0),
            processing_timeout_seconds=_float(
                "SIMULATION_MEMORY_PROCESSING_TIMEOUT_SECONDS", 60.0
            ),
            retry_budget_seconds=_float("SIMULATION_MEMORY_RETRY_BUDGET_SECONDS", 120.0),
            batch_size=_int("SIMULATION_MEMORY_BATCH_SIZE", 20, 1, 500),
            accept_pause_seconds=float(
                env.get("SIMULATION_MEMORY_ACCEPT_PAUSE_SECONDS", "0") or 0
            ),
            sync_pause_seconds=float(
                env.get("SIMULATION_MEMORY_SYNC_PAUSE_SECONDS", "0") or 0
            ),
            fault=str(env.get("SIMULATION_MEMORY_FAULT", "") or ""),
            acceptance_graph_fingerprint=(
                str(env.get("ZEP_ACCEPTANCE_GRAPH_FINGERPRINT", "") or "") or None
            ),
        )

    @property
    def enabled(self) -> bool:
        return self.provider in {"stub", "zep"}


# ---------------------------------------------------------------- 绑定与写入


async def ensure_binding(
    db: AsyncSession, *, simulation_id: uuid.UUID, config: MemoryConfig
) -> str | None:
    """先持久化幂等键，再创建运行级图谱；stub 使用确定性 UUID。"""
    if not config.enabled:
        return None
    key = contract.graph_create_key_for(simulation_id)
    await db.execute(
        text(
            "INSERT INTO simulation_graph_bindings (simulation_id, "
            "create_idempotency_key, request_name, request_description, "
            "request_time_zone, state) VALUES (:simulation_id, :key, :name, "
            ":description, 'UTC', 'pending') ON CONFLICT (simulation_id) DO NOTHING"
        ),
        {
            "simulation_id": simulation_id,
            "key": key,
            "name": f"EchoSim run memory {simulation_id}",
            "description": "Third-stage temporal memory for one simulation run",
        },
    )
    row = (
        await db.execute(
            text(
                "SELECT graph_id, state, create_idempotency_key FROM "
                "simulation_graph_bindings WHERE simulation_id = :simulation_id"
            ),
            {"simulation_id": simulation_id},
        )
    ).first()
    if row is None:
        return None
    if row.graph_id is not None:
        return str(row.graph_id)
    if config.provider == "stub":
        graph_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"echosim:memory-graph-id:{simulation_id}",
            )
        )
    else:
        client = _graph_client()
        graph = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                graph = client.graph.create(
                    name=f"EchoSim run memory {simulation_id}",
                    description="Third-stage temporal memory for one simulation run",
                    time_zone="UTC",
                    idempotency_key=str(row.create_idempotency_key),
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.05)
                    continue
        if graph is None:
            raise MemoryUnavailable(
                "memory_unavailable", "运行图谱创建失败"
            ) from last_error
        graph_id = _graph_uuid(graph)
        if not graph_id:
            raise MemoryUnavailable("memory_unavailable", "运行图谱创建返回空 UUID")
        from ..engines.business_config import (
            graph_id_fingerprint,
            normalize_fingerprint,
        )

        expected = normalize_fingerprint(
            config.acceptance_graph_fingerprint
        ) or graph_id_fingerprint(config.acceptance_graph_fingerprint)
        if expected and graph_id_fingerprint(graph_id) == expected:
            raise MemoryUnavailable("invalid_graph_scope", "运行图谱指向验收专用图谱")
    await db.execute(
        text(
            "UPDATE simulation_graph_bindings SET graph_id = :graph_id, "
            "state = 'bound', bound_at = clock_timestamp() "
            "WHERE simulation_id = :simulation_id AND graph_id IS NULL"
        ),
        {"graph_id": graph_id, "simulation_id": simulation_id},
    )
    column = "memory_calls_real" if config.provider == "zep" else "memory_calls_double"
    await db.execute(
        text(
            f"UPDATE simulations SET {column} = {column} + 1, "
            "updated_at = clock_timestamp() WHERE id = :simulation_id"
        ),
        {"simulation_id": simulation_id},
    )
    return graph_id


def _graph_uuid(graph: object) -> str | None:
    if isinstance(graph, dict):
        value = graph.get("uuid") or graph.get("uuid_")
        return str(value) if value else None
    for name in ("uuid_", "uuid"):
        value = getattr(graph, name, None)
        if value:
            return str(value)
    return None


def _graph_client() -> Any:
    config = BusinessGraphConfig.from_env()
    from ..config import settings

    return create_graph_client(
        config,
        zep_api_key=settings.zep_api_key,
        zep_api_base_url=settings.zep_api_base_url,
        timeout_seconds=settings.graph_write_timeout_seconds,
    )


async def remember_action(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    action_id: uuid.UUID,
    platform_key: str,
    round_index: int,
    action_seq: int,
    actor_role_id: uuid.UUID,
    actor_display_name: str,
    speech_text: str,
    native_action: str,
    target_action_id: uuid.UUID | None,
    target_engine_record_id: str | None,
    simulated_at: datetime,
    source_event_seq: int,
    content: str,
) -> uuid.UUID:
    """在同一记账事务写入行动记忆与 episode 计划；重复行动只校验。"""
    memory_id = contract.memory_id_for(simulation_id, action_id)
    await db.execute(
        text(
            "INSERT INTO simulation_memory_entries (id, simulation_id, action_id, "
            "platform_key, round_index, action_seq, actor_role_id, "
            "actor_display_name, speech_text, native_action, target_action_id, "
            "target_engine_record_id, simulated_at, source_event_seq) VALUES "
            "(:id, :simulation_id, :action_id, :platform_key, :round_index, "
            ":action_seq, :actor_role_id, :actor_name, :speech, :native_action, "
            ":target_action_id, :target_record, :simulated_at, :event_seq) "
            "ON CONFLICT (simulation_id, action_id) DO NOTHING"
        ),
        {
            "id": memory_id,
            "simulation_id": simulation_id,
            "action_id": action_id,
            "platform_key": platform_key,
            "round_index": round_index,
            "action_seq": action_seq,
            "actor_role_id": actor_role_id,
            "actor_name": actor_display_name,
            "speech": speech_text,
            "native_action": native_action,
            "target_action_id": target_action_id,
            "target_record": target_engine_record_id,
            "simulated_at": simulated_at,
            "event_seq": source_event_seq,
        },
    )
    episode_content = contract.episode_content_for(
        simulation_id=simulation_id,
        action_id=action_id,
        memory_id=memory_id,
        platform_key=platform_key,
        native_platform=PLATFORM_MAPPING[platform_key],
        round_index=round_index,
        simulated_at=simulated_at,
        actor_role_id=actor_role_id,
        actor_display_name=actor_display_name,
        native_action=native_action,
        content=speech_text or content,
        target_engine_record_id=target_engine_record_id,
    )
    await db.execute(
        text(
            "INSERT INTO simulation_memory_episodes (simulation_id, memory_id, "
            "action_id, episode_key, idempotency_key, content, content_sha256, "
            "round_index, simulated_at, source_event_seq) VALUES "
            "(:simulation_id, :memory_id, :action_id, :episode_key, :idempotency, "
            ":content, :sha, :round_index, :simulated_at, :event_seq) "
            "ON CONFLICT (simulation_id, memory_id) DO NOTHING"
        ),
        {
            "simulation_id": simulation_id,
            "memory_id": memory_id,
            "action_id": action_id,
            "episode_key": contract.episode_key_for(action_id),
            "idempotency": contract.episode_idempotency_key_for(
                simulation_id, action_id
            ),
            "content": episode_content,
            "sha": contract.content_sha256(episode_content),
            "round_index": round_index,
            "simulated_at": simulated_at,
            "event_seq": source_event_seq,
        },
    )
    return memory_id


async def record_reads(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    action_id: uuid.UUID,
    platform_key: str,
    round_index: int,
    refs: Sequence[Mapping[str, Any]],
) -> None:
    for ref in refs:
        raw = ref.get("memoryId")
        if not raw:
            continue
        try:
            memory_id = uuid.UUID(str(raw))
        except (ValueError, AttributeError):
            continue
        episode = ref.get("episodeId")
        await db.execute(
            text(
                "INSERT INTO simulation_action_memory_reads (action_id, memory_id, "
                "simulation_id, platform_key, round_index, episode_uuid) VALUES "
                "(:action_id, :memory_id, :simulation_id, :platform_key, "
                ":round_index, :episode_uuid) ON CONFLICT DO NOTHING"
            ),
            {
                "action_id": action_id,
                "memory_id": memory_id,
                "simulation_id": simulation_id,
                "platform_key": platform_key,
                "round_index": round_index,
                "episode_uuid": uuid.UUID(str(episode)) if episode else None,
            },
        )


# ---------------------------------------------------------------- 计划与读取


async def plan_refs(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    platform_key: str,
    round_index: int,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = (
        await db.execute(
            text(
                "SELECT e.id, e.action_id, e.round_index, e.simulated_at, "
                "e.source_event_seq, p.episode_uuid FROM simulation_memory_entries e "
                "LEFT JOIN simulation_memory_episodes p ON p.simulation_id = e.simulation_id "
                "AND p.memory_id = e.id WHERE e.simulation_id = :simulation_id "
                "AND e.platform_key = :platform_key AND e.round_index < :round_index "
                "ORDER BY e.round_index, e.action_seq"
            ),
            {
                "simulation_id": simulation_id,
                "platform_key": platform_key,
                "round_index": round_index,
            },
        )
    ).fetchall()
    refs = [
        {
            "memoryId": str(row.id),
            "actionId": str(row.action_id),
            "roundIndex": int(row.round_index),
            "simulatedAt": _iso(row.simulated_at),
            "episodeId": str(row.episode_uuid) if row.episode_uuid else None,
            "sourceEventSeq": int(row.source_event_seq),
            "facets": ["role_speech", "simulation_result"],
        }
        for row in rows[-max(1, limit) :]
    ]
    material_rows = (
        await db.execute(
            text(
                "SELECT node_key, label, node_type FROM graph_nodes "
                "WHERE snapshot_id = :snapshot_id ORDER BY node_key LIMIT :limit"
            ),
            {"snapshot_id": snapshot_id, "limit": min(5, limit)},
        )
    ).fetchall()
    material_refs = [
        {
            "snapshotId": str(snapshot_id),
            "nodeKey": row.node_key,
            "label": row.label,
            "nodeType": row.node_type,
        }
        for row in material_rows
    ]
    return refs, material_refs


async def load_plan(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    platform_key: str,
    round_index: int,
) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                "SELECT request_payload FROM simulation_round_plans "
                "WHERE simulation_id = :simulation_id AND platform_key = :platform_key "
                "AND round_index = :round_index"
            ),
            {
                "simulation_id": simulation_id,
                "platform_key": platform_key,
                "round_index": round_index,
            },
        )
    ).first()
    return None if row is None else dict(row.request_payload or {})


async def save_plan(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    platform_key: str,
    round_index: int,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    await db.execute(
        text(
            "INSERT INTO simulation_round_plans (simulation_id, platform_key, "
            "round_index, memory_cutoff_round, request_payload, request_sha256) "
            "VALUES (:simulation_id, :platform_key, :round_index, :cutoff, "
            "CAST(:payload AS jsonb), :sha) ON CONFLICT DO NOTHING"
        ),
        {
            "simulation_id": simulation_id,
            "platform_key": platform_key,
            "round_index": round_index,
            "cutoff": round_index - 1,
            "payload": encoded,
            "sha": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        },
    )
    stored = await load_plan(
        db,
        simulation_id=simulation_id,
        platform_key=platform_key,
        round_index=round_index,
    )
    return stored or dict(payload)


def actions_from_plan(payload: Mapping[str, Any]) -> list[PlannedAction]:
    actions: list[PlannedAction] = []
    for raw in payload.get("actions") or []:
        if not isinstance(raw, Mapping):
            continue
        actions.append(
            PlannedAction(
                action_key=str(raw.get("actionKey") or ""),
                action_index=int(raw.get("actionIndex") or 0),
                native_action=str(raw.get("nativeAction") or ""),
                actor_role_id=str(raw.get("actorRoleId") or ""),
                actor_agent_id=int(raw.get("actorAgentId") or 0),
                actor_name=str(raw.get("actorName") or ""),
                content=str(raw.get("content") or ""),
                marker=str(raw.get("marker") or ""),
                topic=str(raw.get("topic") or ""),
                target_action_key=raw.get("targetActionKey"),
                target_engine_record_id=raw.get("targetEngineRecordId"),
                memory_refs=tuple(raw.get("memoryRefs") or ()),
                material_refs=tuple(raw.get("materialRefs") or ()),
            )
        )
    return actions


def plan_actions_payload(actions: Sequence[PlannedAction]) -> list[dict[str, Any]]:
    return [
        {
            "actionKey": action.action_key,
            "actionIndex": action.action_index,
            "nativeAction": action.native_action,
            "actorRoleId": action.actor_role_id,
            "actorAgentId": action.actor_agent_id,
            "actorName": action.actor_name,
            "content": action.content,
            "marker": action.marker,
            "topic": action.topic,
            "targetActionKey": action.target_action_key,
            "targetEngineRecordId": action.target_engine_record_id,
            "memoryRefs": list(action.memory_refs),
            "materialRefs": list(action.material_refs),
        }
        for action in actions
    ]


# ---------------------------------------------------------------- 投影与读回


async def _episode_row(
    db: AsyncSession, *, simulation_id: uuid.UUID, memory_id: uuid.UUID
) -> Any:
    return (
        await db.execute(
            text(
                "SELECT p.*, e.platform_key, e.round_index, e.action_seq, "
                "e.actor_role_id, e.actor_display_name, e.speech_text, "
                "e.native_action, e.target_action_id, e.target_engine_record_id, "
                "e.simulated_at, e.source_event_seq, e.action_id "
                "FROM simulation_memory_episodes p JOIN simulation_memory_entries e "
                "ON e.simulation_id = p.simulation_id AND e.id = p.memory_id "
                "WHERE p.simulation_id = :simulation_id AND p.memory_id = :memory_id"
            ),
            {"simulation_id": simulation_id, "memory_id": memory_id},
        )
    ).first()


async def project_round(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    platform_key: str,
    round_index: int,
    config: MemoryConfig,
) -> None:
    if not config.enabled:
        return
    rows = (
        await db.execute(
            text(
                "SELECT memory_id FROM simulation_memory_episodes "
                "WHERE simulation_id = :simulation_id AND round_index = :round_index "
                "AND platform_key = :platform_key AND state <> 'processed' "
                "ORDER BY memory_id"
            ),
            {
                "simulation_id": simulation_id,
                "round_index": round_index,
                "platform_key": platform_key,
            },
        )
    ).fetchall()
    for row in rows:
        await _project_episode(
            db,
            simulation_id=simulation_id,
            memory_id=row.memory_id,
            config=config,
        )
    await refresh_status(db, simulation_id=simulation_id, config=config)


async def project_pending(
    db: AsyncSession, *, simulation_id: uuid.UUID, config: MemoryConfig
) -> None:
    if not config.enabled:
        return
    rows = (
        await db.execute(
            text(
                "SELECT memory_id FROM simulation_memory_episodes "
                "WHERE simulation_id = :simulation_id AND state <> 'processed' "
                "ORDER BY round_index, memory_id"
            ),
            {"simulation_id": simulation_id},
        )
    ).fetchall()
    for row in rows:
        await _project_episode(
            db,
            simulation_id=simulation_id,
            memory_id=row.memory_id,
            config=config,
        )
    await refresh_status(db, simulation_id=simulation_id, config=config)


async def _project_episode(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    memory_id: uuid.UUID,
    config: MemoryConfig,
) -> None:
    row = await _episode_row(db, simulation_id=simulation_id, memory_id=memory_id)
    if row is None or str(row.state) == "processed":
        return
    if config.provider == "stub":
        await _project_stub(db, row=row, simulation_id=simulation_id, config=config)
        return
    await _project_zep(db, row=row, simulation_id=simulation_id, config=config)


async def _project_stub(
    db: AsyncSession, *, row: Any, simulation_id: uuid.UUID, config: MemoryConfig
) -> None:
    """离线 provider 独立保留已受理记录；本地只负责查账补记与读回。"""
    provider = (
        await db.execute(
            text(
                "SELECT episode_uuid, action_id, content_sha256, processed "
                "FROM simulation_memory_provider_records WHERE simulation_id = "
                ":simulation_id AND idempotency_key = :key"
            ),
            {
                "simulation_id": simulation_id,
                "key": row.idempotency_key,
            },
        )
    ).first()
    if provider is not None:
        if (
            str(provider.action_id) != str(row.action_id)
            or str(provider.content_sha256) != str(row.content_sha256)
        ):
            raise MemoryUnavailable(
                "write_unconfirmed", "provider 记录身份与本地 episode 不一致"
            )
        await db.execute(
            text(
                "UPDATE simulation_memory_episodes SET add_count = "
                "GREATEST(add_count, 1), episode_uuid = :episode_uuid, "
                "write_outcome = 'accepted', state = 'accepted', "
                "updated_at = clock_timestamp() WHERE simulation_id = :simulation_id "
                "AND memory_id = :memory_id"
            ),
            {
                "episode_uuid": provider.episode_uuid,
                "simulation_id": simulation_id,
                "memory_id": row.memory_id,
            },
        )
        if not bool(provider.processed):
            await db.commit()
            raise MemoryUnavailable("write_unconfirmed", "provider episode 尚未处理")
        await db.commit()
        refreshed = await _episode_row(
            db, simulation_id=simulation_id, memory_id=row.memory_id
        )
        await _readback_stub(db, simulation_id=simulation_id, row=refreshed or row)
        await db.execute(
            text(
                "UPDATE simulation_memory_episodes SET state = 'processed', "
                "write_outcome = 'accepted', updated_at = clock_timestamp() "
                "WHERE simulation_id = :simulation_id AND memory_id = :memory_id"
            ),
            {"simulation_id": simulation_id, "memory_id": row.memory_id},
        )
        await db.commit()
        return

    claimed = await db.execute(
        text(
            "UPDATE simulation_memory_episodes SET attempts = attempts + 1, "
            "add_count = add_count + 1, write_outcome = 'in_flight', "
            "state = 'accepted', updated_at = clock_timestamp() "
            "WHERE simulation_id = :simulation_id AND memory_id = :memory_id "
            "AND add_count = 0 AND state IN ('planned', 'rejected_retryable') "
            "RETURNING add_count"
        ),
        {"simulation_id": simulation_id, "memory_id": row.memory_id},
    )
    if claimed.first() is None:
        # 本地认为已尝试，但 provider 没有可核对记录：结果未知，不换键重写。
        await db.execute(
            text(
                "UPDATE simulation_memory_episodes SET state = 'unconfirmed', "
                "write_outcome = 'unconfirmed', updated_at = clock_timestamp() "
                "WHERE simulation_id = :simulation_id AND memory_id = :memory_id"
            ),
            {"simulation_id": simulation_id, "memory_id": row.memory_id},
        )
        await db.commit()
        raise MemoryUnavailable("write_unconfirmed", "provider 未找到已受理记录")
    await db.execute(
        text(
            "UPDATE simulations SET memory_calls_double = "
            "memory_calls_double + 1, updated_at = clock_timestamp() "
            "WHERE id = :simulation_id"
        ),
        {"simulation_id": simulation_id},
    )
    await db.commit()
    episode_uuid = contract.deterministic_episode_uuid(
        simulation_id, row.action_id
    )
    await db.execute(
        text(
            "INSERT INTO simulation_memory_provider_records (simulation_id, "
            "memory_id, action_id, idempotency_key, content_sha256, episode_uuid, "
            "processed) VALUES (:simulation_id, :memory_id, :action_id, :key, "
            ":sha, :episode_uuid, true) ON CONFLICT DO NOTHING"
        ),
        {
            "simulation_id": simulation_id,
            "memory_id": row.memory_id,
            "action_id": row.action_id,
            "key": row.idempotency_key,
            "sha": row.content_sha256,
            "episode_uuid": episode_uuid,
        },
    )
    await db.commit()
    if config.accept_pause_seconds > 0:
        # 验收窗口：provider 已受理，本地尚未保存 UUID。
        await asyncio.sleep(config.accept_pause_seconds)
    await db.execute(
        text(
            "UPDATE simulation_memory_episodes SET episode_uuid = :episode_uuid, "
            "write_outcome = 'accepted', state = 'accepted', "
            "updated_at = clock_timestamp() WHERE simulation_id = :simulation_id "
            "AND memory_id = :memory_id"
        ),
        {
            "episode_uuid": episode_uuid,
            "simulation_id": simulation_id,
            "memory_id": row.memory_id,
        },
    )
    await db.commit()
    refreshed = await _episode_row(
        db, simulation_id=simulation_id, memory_id=row.memory_id
    )
    await _readback_stub(db, simulation_id=simulation_id, row=refreshed or row)
    await db.execute(
        text(
            "UPDATE simulation_memory_episodes SET state = 'processed', "
            "write_outcome = 'accepted', updated_at = clock_timestamp() "
            "WHERE simulation_id = :simulation_id AND memory_id = :memory_id"
        ),
        {"simulation_id": simulation_id, "memory_id": row.memory_id},
    )
    await db.commit()


async def _readback_stub(
    db: AsyncSession, *, simulation_id: uuid.UUID, row: Any
) -> None:
    role_key = contract.node_key_for("role", str(row.actor_role_id))
    platform_key = contract.node_key_for(
        "platform", str(PLATFORM_MAPPING[str(row.platform_key)])
    )
    await _upsert_node(
        db,
        simulation_id=simulation_id,
        node_key=role_key,
        node_type="role",
        label=str(row.actor_display_name),
        origin="role",
        summary=f"角色 {row.actor_display_name}",
    )
    await _upsert_node(
        db,
        simulation_id=simulation_id,
        node_key=platform_key,
        node_type="platform",
        label=str(PLATFORM_MAPPING[str(row.platform_key)]),
        origin="simulation",
        summary="推演平台",
    )
    edge_key = contract.edge_key_for(
        role_key,
        platform_key,
        f"ACTS_ON_{str(row.native_action).upper()}",
        f"{row.actor_display_name} {row.native_action}",
    )
    await _upsert_edge(
        db,
        simulation_id=simulation_id,
        edge_key=edge_key,
        edge_type=f"ACTS_ON_{str(row.native_action).upper()}",
        label=str(row.native_action),
        origin="simulation",
        source_node_key=role_key,
        target_node_key=platform_key,
        summary=f"{row.actor_display_name} 在 {PLATFORM_MAPPING[str(row.platform_key)]} 执行 {row.native_action}",
    )
    facets = ["simulation_result"]
    if str(row.speech_text or "").strip():
        facets.insert(0, "role_speech")
    for facet in facets:
        for target_kind, target_key in (
            ("node", role_key),
            ("node", platform_key),
            ("edge", edge_key),
        ):
            await _upsert_provenance(
                db,
                simulation_id=simulation_id,
                target_kind=target_kind,
                target_key=target_key,
                row=row,
                facet=facet,
                summary=(
                    f"R{row.round_index} {row.actor_display_name} 发言"
                    if facet == "role_speech"
                    else f"R{row.round_index} {row.actor_display_name} {row.native_action}"
                ),
            )
    target_action_id = row.target_action_id
    if target_action_id:
        target = (
            await db.execute(
                text(
                    "SELECT actor_role_id, actor_display_name FROM simulation_memory_entries "
                    "WHERE simulation_id = :simulation_id AND action_id = :action_id"
                ),
                {"simulation_id": simulation_id, "action_id": target_action_id},
            )
        ).first()
        if target is not None:
            target_key = contract.node_key_for("role", str(target.actor_role_id))
            await _upsert_node(
                db,
                simulation_id=simulation_id,
                node_key=target_key,
                node_type="role",
                label=str(target.actor_display_name),
                origin="role",
                summary=f"角色 {target.actor_display_name}",
            )
            target_edge = contract.edge_key_for(
                role_key,
                target_key,
                "RESPONDS_TO",
                f"{row.actor_display_name} 回应 {target.actor_display_name}",
            )
            await _upsert_edge(
                db,
                simulation_id=simulation_id,
                edge_key=target_edge,
                edge_type="RESPONDS_TO",
                label="回应",
                origin="simulation",
                source_node_key=role_key,
                target_node_key=target_key,
                summary=f"{row.actor_display_name} 回应 {target.actor_display_name}",
            )
            await _upsert_provenance(
                db,
                simulation_id=simulation_id,
                target_kind="node",
                target_key=target_key,
                row=row,
                facet="simulation_result",
                summary=f"R{row.round_index} {row.actor_display_name} 回应 {target.actor_display_name}",
            )
            await _upsert_provenance(
                db,
                simulation_id=simulation_id,
                target_kind="edge",
                target_key=target_edge,
                row=row,
                facet="simulation_result",
                summary=f"R{row.round_index} {row.actor_display_name} 回应 {target.actor_display_name}",
            )


async def _upsert_node(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    node_key: str,
    node_type: str,
    label: str,
    origin: str,
    summary: str,
) -> None:
    await db.execute(
        text(
            "INSERT INTO simulation_memory_nodes (simulation_id, node_key, "
            "node_type, label, origin, summary) VALUES (:simulation_id, :node_key, "
            ":node_type, :label, :origin, :summary) ON CONFLICT (simulation_id, "
            "node_key) DO UPDATE SET node_type = EXCLUDED.node_type, "
            "label = EXCLUDED.label, origin = EXCLUDED.origin, "
            "summary = EXCLUDED.summary, updated_at = clock_timestamp()"
        ),
        {
            "simulation_id": simulation_id,
            "node_key": node_key,
            "node_type": node_type,
            "label": label,
            "origin": origin,
            "summary": summary,
        },
    )


async def _upsert_edge(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    edge_key: str,
    edge_type: str,
    label: str,
    origin: str,
    source_node_key: str,
    target_node_key: str,
    summary: str,
) -> None:
    await db.execute(
        text(
            "INSERT INTO simulation_memory_edges (simulation_id, edge_key, "
            "edge_type, label, origin, source_node_key, target_node_key, summary) "
            "VALUES (:simulation_id, :edge_key, :edge_type, :label, :origin, "
            ":source, :target, :summary) ON CONFLICT (simulation_id, edge_key) "
            "DO UPDATE SET label = EXCLUDED.label, summary = EXCLUDED.summary, "
            "updated_at = clock_timestamp()"
        ),
        {
            "simulation_id": simulation_id,
            "edge_key": edge_key,
            "edge_type": edge_type,
            "label": label,
            "origin": origin,
            "source": source_node_key,
            "target": target_node_key,
            "summary": summary,
        },
    )


async def _upsert_provenance(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    target_kind: str,
    target_key: str,
    row: Any,
    facet: str,
    summary: str | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO simulation_memory_provenance (simulation_id, target_kind, "
            "target_key, memory_id, facet, action_id, episode_uuid, platform_key, "
            "round_index, simulated_at, source_event_seq, summary) VALUES "
            "(:simulation_id, :target_kind, :target_key, :memory_id, :facet, "
            ":action_id, :episode_uuid, :platform_key, :round_index, :simulated_at, "
            ":event_seq, :summary) ON CONFLICT DO NOTHING"
        ),
        {
            "simulation_id": simulation_id,
            "target_kind": target_kind,
            "target_key": target_key,
            "memory_id": row.memory_id,
            "facet": facet,
            "action_id": row.action_id,
            "episode_uuid": row.episode_uuid,
            "platform_key": row.platform_key,
            "round_index": row.round_index,
            "simulated_at": row.simulated_at,
            "event_seq": row.source_event_seq,
            "summary": summary,
        },
    )


async def _project_zep(
    db: AsyncSession, *, row: Any, simulation_id: uuid.UUID, config: MemoryConfig
) -> None:
    """真实 Zep 路径：先 add，响应丢失或中断后改用查账，不换键。"""
    binding = (
        await db.execute(
            text(
                "SELECT graph_id FROM simulation_graph_bindings "
                "WHERE simulation_id = :simulation_id"
            ),
            {"simulation_id": simulation_id},
        )
    ).first()
    if binding is None or binding.graph_id is None:
        raise MemoryUnavailable("memory_unavailable", "运行图谱尚未绑定")
    graph_id = str(binding.graph_id)
    client = _graph_client()
    if int(row.add_count or 0) == 0:
        await db.execute(
            text(
                "UPDATE simulation_memory_episodes SET attempts = attempts + 1, "
                "add_count = add_count + 1, write_outcome = 'in_flight', "
                "state = 'planned', updated_at = clock_timestamp() "
                "WHERE simulation_id = :simulation_id AND memory_id = :memory_id"
            ),
            {"simulation_id": simulation_id, "memory_id": row.memory_id},
        )
        await db.execute(
            text(
                "UPDATE simulations SET memory_calls_real = "
                "memory_calls_real + 1, updated_at = clock_timestamp() "
                "WHERE id = :simulation_id"
            ),
            {"simulation_id": simulation_id},
        )
        await db.commit()
        try:
            result = client.graph.episode.add(
                graph_id,
                data=row.content,
                metadata={
                    "purpose": contract.MEMORY_PURPOSE,
                    "simulationId": str(simulation_id),
                    "actionId": str(row.action_id),
                    "memoryId": str(row.memory_id),
                    "platformKey": str(row.platform_key),
                    "roundIndex": int(row.round_index),
                    "contentSha256": str(row.content_sha256),
                },
                idempotency_key=str(row.idempotency_key),
                type="text",
            )
        except Exception as exc:  # noqa: BLE001 - 响应丢失按未知处理
            # 已尝试写入：不换键重写，先查账；查不到保留 unconfirmed。
            await db.execute(
                text(
                    "UPDATE simulation_memory_episodes SET write_outcome = 'in_flight' "
                    "WHERE simulation_id = :simulation_id AND memory_id = :memory_id"
                ),
                {"simulation_id": simulation_id, "memory_id": row.memory_id},
            )
            await db.commit()
            if not await _reconcile_zep(db, client, graph_id, row):
                await db.execute(
                    text(
                        "UPDATE simulation_memory_episodes SET state = 'unconfirmed', "
                        "write_outcome = 'unconfirmed', updated_at = clock_timestamp() "
                        "WHERE simulation_id = :simulation_id AND memory_id = :memory_id"
                    ),
                    {"simulation_id": simulation_id, "memory_id": row.memory_id},
                )
                raise MemoryUnavailable("write_unconfirmed", "episode 写入结果未知") from exc
            return
        episode_uuid = _episode_uuid(result)
        if not episode_uuid:
            if not await _reconcile_zep(db, client, graph_id, row):
                await db.execute(
                    text(
                        "UPDATE simulation_memory_episodes SET state = 'unconfirmed', "
                        "write_outcome = 'unconfirmed', updated_at = clock_timestamp() "
                        "WHERE simulation_id = :simulation_id AND memory_id = :memory_id"
                    ),
                    {"simulation_id": simulation_id, "memory_id": row.memory_id},
                )
                raise MemoryUnavailable("write_unconfirmed", "episode 写入无 UUID")
            return
        await db.execute(
            text(
                "UPDATE simulation_memory_episodes SET episode_uuid = :episode_uuid, "
                "state = 'accepted', write_outcome = 'accepted', "
                "updated_at = clock_timestamp() WHERE simulation_id = :simulation_id "
                "AND memory_id = :memory_id"
            ),
            {
                "episode_uuid": episode_uuid,
                "simulation_id": simulation_id,
                "memory_id": row.memory_id,
            },
        )
    elif row.episode_uuid is None:
        if not await _reconcile_zep(db, client, graph_id, row):
            await db.execute(
                text(
                    "UPDATE simulation_memory_episodes SET state = 'unconfirmed', "
                    "write_outcome = 'unconfirmed', updated_at = clock_timestamp() "
                    "WHERE simulation_id = :simulation_id AND memory_id = :memory_id"
                ),
                {"simulation_id": simulation_id, "memory_id": row.memory_id},
            )
            raise MemoryUnavailable("write_unconfirmed", "episode 写入结果未知")

    refreshed = await _episode_row(
        db, simulation_id=simulation_id, memory_id=row.memory_id
    )
    if refreshed is None or refreshed.episode_uuid is None:
        raise MemoryUnavailable("write_unconfirmed", "episode UUID 未确认")
    read = readback_raw_graph(
        client,
        graph_id=graph_id,
        episode_uuids=[str(refreshed.episode_uuid)],
        processing_timeout_seconds=config.processing_timeout_seconds,
    )
    await _persist_zep_readback(
        db,
        simulation_id=simulation_id,
        row=refreshed,
        nodes=list(read.nodes),
        edges=list(read.edges),
    )
    await db.execute(
        text(
            "UPDATE simulation_memory_episodes SET state = 'processed', "
            "updated_at = clock_timestamp() WHERE simulation_id = :simulation_id "
            "AND memory_id = :memory_id"
        ),
        {"simulation_id": simulation_id, "memory_id": row.memory_id},
    )


def _episode_uuid(result: object) -> str | None:
    episode = getattr(result, "episode", None)
    if episode is not None:
        for name in ("uuid_", "uuid"):
            value = getattr(episode, name, None)
            if value:
                return str(value)
    if isinstance(result, dict):
        episode = result.get("episode") or {}
        value = episode.get("uuid") or episode.get("uuid_")
        return str(value) if value else None
    return None


async def _reconcile_zep(
    db: AsyncSession, client: object, graph_id: str, row: Any
) -> bool:
    """按运行、行动、记忆、幂等键与内容哈希查账；确认 processed 后补记。"""
    try:
        pager = client.graph.episode.list(graph_id, page_size=100)
        items = list(_iter_pager(pager))
    except Exception:  # noqa: BLE001
        try:
            pager = client.graph.search_episodes(graph_id, query=str(row.content)[:120])
            items = list(_iter_pager(pager))
        except Exception:  # noqa: BLE001
            return False
    for item in items:
        metadata = getattr(item, "metadata", None) or {}
        if isinstance(item, dict):
            metadata = item.get("metadata") or metadata
        identity = {
            "simulationId": str(metadata.get("simulationId") or ""),
            "actionId": str(metadata.get("actionId") or ""),
            "memoryId": str(metadata.get("memoryId") or ""),
            "contentSha256": str(metadata.get("contentSha256") or ""),
        }
        if identity != {
            "simulationId": str(row.simulation_id),
            "actionId": str(row.action_id),
            "memoryId": str(row.memory_id),
            "contentSha256": str(row.content_sha256),
        }:
            continue
        expected_key = str(metadata.get("idempotencyKey") or "")
        if expected_key and expected_key != str(row.idempotency_key):
            continue
        processed = getattr(item, "processed", None)
        if processed is None and isinstance(item, dict):
            processed = item.get("processed")
        if not bool(processed):
            continue
        episode_uuid = getattr(item, "uuid_", None) or getattr(item, "uuid", None)
        if isinstance(item, dict):
            episode_uuid = item.get("uuid_") or item.get("uuid") or episode_uuid
        if not episode_uuid:
            continue
        await db.execute(
            text(
                "UPDATE simulation_memory_episodes SET episode_uuid = :episode_uuid, "
                "state = 'matched', write_outcome = 'accepted', "
                "updated_at = clock_timestamp() WHERE simulation_id = :simulation_id "
                "AND memory_id = :memory_id"
            ),
            {
                "episode_uuid": str(episode_uuid),
                "simulation_id": row.simulation_id,
                "memory_id": row.memory_id,
            },
        )
        await db.commit()
        return True
    return False


def _iter_pager(pager: object):
    page = pager
    while page is not None:
        for item in getattr(page, "items", []) or []:
            yield item
        if not getattr(page, "has_next", False):
            return
        next_page = getattr(page, "next_page", None)
        page = next_page() if callable(next_page) else None


async def _persist_zep_readback(
    db: AsyncSession,
    *,
    simulation_id: uuid.UUID,
    row: Any,
    nodes: Sequence[object],
    edges: Sequence[object],
) -> None:
    node_by_uuid: dict[str, str] = {}
    for item in nodes:
        uuid_value = getattr(item, "uuid_", None) or getattr(item, "uuid", None)
        name = str(getattr(item, "name", "") or "")
        if not uuid_value or not name:
            continue
        labels = getattr(item, "labels", None) or []
        node_type = str(labels[0]) if labels else "entity"
        key = contract.node_key_for(node_type, name)
        node_by_uuid[str(uuid_value)] = key
        await _upsert_node(
            db,
            simulation_id=simulation_id,
            node_key=key,
            node_type=node_type,
            label=name,
            origin="role" if node_type.lower() == "role" else "simulation",
            summary=str(getattr(item, "summary", "") or ""),
        )
        await _upsert_provenance(
            db,
            simulation_id=simulation_id,
            target_kind="node",
            target_key=key,
            row=row,
            facet="simulation_result",
            summary=f"R{row.round_index} {name}",
        )
    for item in edges:
        source = node_by_uuid.get(
            str(getattr(item, "source_node_uuid", "") or "")
        )
        target = node_by_uuid.get(
            str(getattr(item, "target_node_uuid", "") or "")
        )
        fact = str(getattr(item, "fact", "") or getattr(item, "name", "") or "")
        edge_type = str(getattr(item, "name", "") or "RELATED_TO")
        if not source or not target or not fact:
            continue
        key = contract.edge_key_for(source, target, edge_type, fact)
        await _upsert_edge(
            db,
            simulation_id=simulation_id,
            edge_key=key,
            edge_type=edge_type,
            label=fact,
            origin="simulation",
            source_node_key=source,
            target_node_key=target,
            summary=fact,
        )
        await _upsert_provenance(
            db,
            simulation_id=simulation_id,
            target_kind="edge",
            target_key=key,
            row=row,
            facet="simulation_result",
            summary=f"R{row.round_index} {fact}",
        )


# ---------------------------------------------------------------- 状态与视图


async def refresh_status(
    db: AsyncSession, *, simulation_id: uuid.UUID, config: MemoryConfig
) -> str:
    current = (
        await db.execute(
            text("SELECT memory_status FROM simulations WHERE id = :id"),
            {"id": simulation_id},
        )
    ).scalar_one_or_none()
    if not config.enabled:
        status = "unconfigured"
    else:
        rows = (
            await db.execute(
                text(
                    "SELECT state, COUNT(*) AS total, MAX(round_index) AS last_round "
                    "FROM simulation_memory_episodes WHERE simulation_id = :simulation_id "
                    "GROUP BY state"
                ),
                {"simulation_id": simulation_id},
            )
        ).fetchall()
        counts = {
            str(row.state): int(row.total) for row in rows
        }
        total = sum(counts.values())
        processed = counts.get("processed", 0)
        unconfirmed = counts.get("unconfirmed", 0)
        failed = counts.get("failed", 0)
        if total == 0:
            status = "pending"
        elif unconfirmed:
            status = "unconfirmed"
        elif processed == total:
            status = "complete"
        elif str(current) == "unavailable":
            status = "unavailable"
        elif failed and processed == 0:
            status = "partial"
        else:
            status = "pending" if processed == 0 else "partial"
    last_round = (
        await db.execute(
            text(
                "SELECT COALESCE(MAX(round_index), 0) FROM simulation_memory_episodes "
                "WHERE simulation_id = :simulation_id AND state = 'processed'"
            ),
            {"simulation_id": simulation_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            "UPDATE simulations SET memory_status = :status, "
            "memory_last_synced_round = :last_round, "
            "memory_finished_at = CASE WHEN :status IN ('complete', 'unconfigured') "
            "THEN COALESCE(memory_finished_at, clock_timestamp()) "
            "ELSE memory_finished_at END, updated_at = clock_timestamp() "
            "WHERE id = :simulation_id"
        ),
        {
            "status": status,
            "last_round": int(last_round or 0),
            "simulation_id": simulation_id,
        },
    )
    return status


async def memory_summary(
    db: AsyncSession, *, simulation_id: uuid.UUID, status: str
) -> dict[str, Any]:
    rows = (
        await db.execute(
            text(
                "SELECT state, COUNT(*) AS total FROM simulation_memory_episodes "
                "WHERE simulation_id = :simulation_id GROUP BY state"
            ),
            {"simulation_id": simulation_id},
        )
    ).fetchall()
    counts = {str(row.state): int(row.total) for row in rows}
    pending = sum(
        count for state, count in counts.items() if state != "processed"
    )
    sync = (
        await db.execute(
            text(
                "SELECT job_id, phase, last_error_code FROM simulation_memory_syncs "
                "WHERE simulation_id = :simulation_id ORDER BY created_at DESC LIMIT 1"
            ),
            {"simulation_id": simulation_id},
        )
    ).first()
    sim = (
        await db.execute(
            text(
                "SELECT memory_last_synced_round, memory_failure_code "
                "FROM simulations WHERE id = :simulation_id"
            ),
            {"simulation_id": simulation_id},
        )
    ).first()
    return {
        "status": status,
        "pendingEpisodes": pending,
        "unconfirmedEpisodes": counts.get("unconfirmed", 0),
        "lastSyncedRound": int(sim.memory_last_synced_round) if sim else 0,
        "reason": (sync.last_error_code if sync and sync.last_error_code else None)
        or (sim.memory_failure_code if sim else None),
        "memoryJobId": str(sync.job_id) if sync else None,
    }


async def memory_view(
    db: AsyncSession,
    *,
    simulation: Any,
    round_index: int | None,
    platform_key: str | None,
    config: MemoryConfig,
) -> dict[str, Any]:
    platforms = (
        await db.execute(
            text(
                "SELECT platform_key, rounds_completed FROM simulation_platforms "
                "WHERE simulation_id = :simulation_id ORDER BY platform_key"
            ),
            {"simulation_id": simulation.id},
        )
    ).fetchall()
    completed = {str(row.platform_key): int(row.rounds_completed) for row in platforms}
    if platform_key is not None and platform_key not in PLATFORM_MAPPING:
        raise SimulationConflict("platform_invalid", "平台非法")
    available_max = (
        completed.get(platform_key, 0)
        if platform_key
        else max(completed.values() or [0])
    )
    if round_index is None:
        round_index = max(1, available_max)
    if round_index < 1 or round_index > simulation.rounds:
        raise SimulationConflict("round_invalid", "轮次非法")
    if round_index > available_max:
        raise SimulationConflict(
            "round_not_available", "该轮次尚未完成"
        )

    params: dict[str, Any] = {
        "simulation_id": simulation.id,
        "round_index": round_index,
    }
    entry_platform_clause = ""
    provenance_platform_clause = ""
    if platform_key:
        entry_platform_clause = " AND e.platform_key = :platform_key"
        provenance_platform_clause = " AND platform_key = :platform_key"
        params["platform_key"] = platform_key
    entry_rows = (
        await db.execute(
            text(
                "SELECT e.*, p.episode_uuid, p.state, p.write_outcome "
                "FROM simulation_memory_entries e LEFT JOIN simulation_memory_episodes p "
                "ON p.simulation_id = e.simulation_id AND p.memory_id = e.id "
                "WHERE e.simulation_id = :simulation_id AND e.round_index <= :round_index"
                + entry_platform_clause
                + " ORDER BY e.action_seq"
            ),
            params,
        )
    ).fetchall()
    entries = [_entry_view(row) for row in entry_rows]
    entry_by_memory = {item["memoryId"]: item for item in entries}
    action_ids = [uuid.UUID(item["actionId"]) for item in entries]
    if action_ids:
        read_rows = (
            await db.execute(
                text(
                    "SELECT r.action_id, r.memory_id, e.round_index AS source_round, "
                    "r.episode_uuid, e.actor_display_name, e.speech_text, e.native_action "
                    "FROM simulation_action_memory_reads r JOIN simulation_memory_entries e "
                    "ON e.simulation_id = r.simulation_id AND e.id = r.memory_id "
                    "WHERE r.simulation_id = :simulation_id "
                    "AND r.action_id = ANY(CAST(:action_ids AS uuid[])) "
                    "ORDER BY r.action_id, r.round_index, r.memory_id"
                ),
                {
                    "simulation_id": simulation.id,
                    "action_ids": [str(item) for item in action_ids],
                },
            )
        ).fetchall()
        reads_by_action: dict[str, list[dict[str, Any]]] = {}
        for read in read_rows:
            reads_by_action.setdefault(str(read.action_id), []).append(
                {
                    "memoryId": str(read.memory_id),
                    "roundIndex": int(read.source_round),
                    "episodeId": str(read.episode_uuid)
                    if read.episode_uuid
                    else None,
                    "actorRoleName": read.actor_display_name,
                    "speechText": read.speech_text or "",
                    "nativeAction": read.native_action,
                }
            )
        for entry in entries:
            entry["memoryReads"] = reads_by_action.get(entry["actionId"], [])

    provenance_rows = (
        await db.execute(
            text(
                "SELECT * FROM simulation_memory_provenance WHERE simulation_id = "
                ":simulation_id AND round_index <= :round_index"
                + provenance_platform_clause
                + " ORDER BY round_index, source_event_seq"
            ),
            params,
        )
    ).fetchall()
    node_sources: dict[str, list[dict[str, Any]]] = {}
    edge_sources: dict[str, list[dict[str, Any]]] = {}
    for row in provenance_rows:
        target = {
            "memoryId": str(row.memory_id),
            "actionId": str(row.action_id),
            "facet": row.facet,
            "episodeId": str(row.episode_uuid) if row.episode_uuid else None,
            "platformKey": row.platform_key,
            "roundIndex": int(row.round_index),
            "simulatedAt": _iso(row.simulated_at),
            "sourceEventSeq": int(row.source_event_seq),
            "summary": getattr(row, "summary", None),
        }
        entry = entry_by_memory.get(str(row.memory_id))
        if entry:
            target.update(
                {
                    "actorRoleId": entry["actorRoleId"],
                    "actorRoleName": entry["actorRoleName"],
                    "nativeAction": entry["nativeAction"],
                    "targetActionId": entry.get("targetActionId"),
                    "content": entry.get("speechText") or "",
                }
            )
        bucket = node_sources if row.target_kind == "node" else edge_sources
        bucket.setdefault(str(row.target_key), []).append(target)

    node_rows = (
        await db.execute(
            text(
                "SELECT * FROM simulation_memory_nodes WHERE simulation_id = :simulation_id"
            ),
            {"simulation_id": simulation.id},
        )
    ).fetchall()
    runtime_nodes = [
        {
            "key": row.node_key,
            "nodeType": row.node_type,
            "label": row.label,
            "origin": row.origin,
            "summary": _cutoff_summary(node_sources.get(row.node_key, [])),
            "sources": node_sources.get(row.node_key, []),
            "sourceCount": len(node_sources.get(row.node_key, [])),
            "firstRound": min(
                (src["roundIndex"] for src in node_sources.get(row.node_key, [])),
                default=None,
            ),
            "lastRound": max(
                (src["roundIndex"] for src in node_sources.get(row.node_key, [])),
                default=None,
            ),
        }
        for row in node_rows
        if node_sources.get(row.node_key)
    ]
    edge_rows = (
        await db.execute(
            text(
                "SELECT * FROM simulation_memory_edges WHERE simulation_id = :simulation_id"
            ),
            {"simulation_id": simulation.id},
        )
    ).fetchall()
    runtime_edges = [
        {
            "key": row.edge_key,
            "edgeType": row.edge_type,
            "label": row.label,
            "origin": row.origin,
            "summary": _cutoff_summary(edge_sources.get(row.edge_key, [])),
            "sourceKey": row.source_node_key,
            "targetKey": row.target_node_key,
            "sources": edge_sources.get(row.edge_key, []),
            "sourceCount": len(edge_sources.get(row.edge_key, [])),
            "firstRound": min(
                (src["roundIndex"] for src in edge_sources.get(row.edge_key, [])),
                default=None,
            ),
            "lastRound": max(
                (src["roundIndex"] for src in edge_sources.get(row.edge_key, [])),
                default=None,
            ),
        }
        for row in edge_rows
        if edge_sources.get(row.edge_key)
    ]
    material = await _material_layer(
        db, snapshot_id=simulation.snapshot_id, project_id=simulation.project_id
    )
    status = await memory_summary(
        db, simulation_id=simulation.id, status=simulation.memory_status
    )
    return {
        "runId": str(simulation.run_id),
        "round": round_index,
        "platform": platform_key,
        "cutoff": {
            "round": round_index,
            "availableMaxRound": available_max,
            "platforms": {
                key: {"completed": value} for key, value in completed.items()
            },
        },
        "status": status,
        "material": material,
        "runtime": {"nodes": runtime_nodes, "edges": runtime_edges},
        "entries": entries,
    }


def _cutoff_summary(sources: Sequence[Mapping[str, Any]]) -> str | None:
    if not sources:
        return None
    ordered = sorted(
        sources,
        key=lambda item: (int(item.get("roundIndex") or 0), int(item.get("sourceEventSeq") or 0)),
    )
    return ordered[-1].get("summary")


def _entry_view(row: Any) -> dict[str, Any]:
    return {
        "memoryId": str(row.id),
        "actionId": str(row.action_id),
        "platformKey": row.platform_key,
        "roundIndex": int(row.round_index),
        "actionSeq": int(row.action_seq),
        "simulatedAt": _iso(row.simulated_at),
        "sourceEventSeq": int(row.source_event_seq),
        "actorRoleId": str(row.actor_role_id),
        "actorRoleName": row.actor_display_name,
        "nativeAction": row.native_action,
        "speechText": row.speech_text or "",
        "targetActionId": str(row.target_action_id) if row.target_action_id else None,
        "targetEngineRecordId": row.target_engine_record_id,
        "episodeId": str(row.episode_uuid) if row.episode_uuid else None,
        "graphState": row.state,
        "writeOutcome": row.write_outcome,
        "facets": ["role_speech", "simulation_result"]
        if str(row.speech_text or "").strip()
        else ["simulation_result"],
    }


async def _material_layer(
    db: AsyncSession, *, snapshot_id: uuid.UUID, project_id: uuid.UUID
) -> dict[str, Any]:
    nodes = (
        await db.execute(
            text(
                "SELECT node_key, node_type, label, origin, summary FROM graph_nodes "
                "WHERE snapshot_id = :snapshot_id ORDER BY node_key LIMIT 500"
            ),
            {"snapshot_id": snapshot_id},
        )
    ).fetchall()
    edges = (
        await db.execute(
            text(
                "SELECT edge_key, edge_type, label, origin, summary, source_node_key, "
                "target_node_key FROM graph_edges WHERE snapshot_id = :snapshot_id "
                "ORDER BY edge_key LIMIT 800"
            ),
            {"snapshot_id": snapshot_id},
        )
    ).fetchall()
    evidence = (
        await db.execute(
            text(
                "SELECT g.target_kind, g.target_key, g.evidence_index, g.document_id, "
                "g.chunk_index, g.page_no, g.paragraph_start, g.paragraph_end, "
                "g.source_start, g.source_end, g.quote, d.kind, d.display_name "
                "FROM graph_evidence g JOIN documents d ON d.id = g.document_id "
                "WHERE g.snapshot_id = :snapshot_id "
                "ORDER BY g.target_kind, g.target_key, g.evidence_index"
            ),
            {"snapshot_id": snapshot_id},
        )
    ).fetchall()
    sources: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in evidence:
        document_path = (
            f"/api/projects/{project_id}/documents/{row.document_id}"
        )
        if str(row.kind) == "pdf":
            page = int(row.page_no or 1)
            locator = {
                "kind": "pdf",
                "pageNo": page,
                "url": f"{document_path}/file#page={page}",
            }
        else:
            locator = {
                "kind": "text",
                "url": f"{document_path}/source",
                "sourceStart": int(row.source_start),
                "sourceEnd": int(row.source_end),
            }
        sources.setdefault((row.target_kind, row.target_key), []).append(
            {
                "snapshotId": str(snapshot_id),
                "documentId": str(row.document_id),
                "documentName": row.display_name,
                "kind": row.kind,
                "chunkIndex": int(row.chunk_index),
                "pageNo": int(row.page_no) if row.page_no is not None else None,
                "paragraphStart": int(row.paragraph_start)
                if row.paragraph_start is not None
                else None,
                "paragraphEnd": int(row.paragraph_end)
                if row.paragraph_end is not None
                else None,
                "sourceStart": int(row.source_start),
                "sourceEnd": int(row.source_end),
                "quote": row.quote,
                "locator": locator,
            }
        )
    return {
        "snapshotId": str(snapshot_id),
        "nodes": [
            {
                "key": row.node_key,
                "nodeType": row.node_type,
                "label": row.label,
                "origin": "material",
                "summary": row.summary,
                "sources": sources.get(("node", row.node_key), []),
                "sourceCount": len(sources.get(("node", row.node_key), [])),
            }
            for row in nodes
        ],
        "edges": [
            {
                "key": row.edge_key,
                "edgeType": row.edge_type,
                "label": row.label,
                "origin": "material",
                "summary": row.summary,
                "sourceKey": row.source_node_key,
                "targetKey": row.target_node_key,
                "sources": sources.get(("edge", row.edge_key), []),
                "sourceCount": len(sources.get(("edge", row.edge_key), [])),
            }
            for row in edges
        ],
    }


# ---------------------------------------------------------------- 同步任务


async def ensure_sync_job(
    db: AsyncSession, *, simulation: Any
) -> tuple[uuid.UUID, bool]:
    """活动任务复用；终态后允许同一 simulationId 新建任务并保留历史。"""
    active = (
        await db.execute(
            text(
                "SELECT s.job_id FROM simulation_memory_syncs s JOIN jobs j "
                "ON j.id = s.job_id WHERE s.simulation_id = :simulation_id "
                "AND j.status IN ('queued', 'running') ORDER BY s.created_at DESC LIMIT 1"
            ),
            {"simulation_id": simulation.id},
        )
    ).first()
    if active is not None:
        return active.job_id, False
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"simulation-memory:{simulation.id}"},
    )
    active = (
        await db.execute(
            text(
                "SELECT s.job_id FROM simulation_memory_syncs s JOIN jobs j "
                "ON j.id = s.job_id WHERE s.simulation_id = :simulation_id "
                "AND j.status IN ('queued', 'running') ORDER BY s.created_at DESC LIMIT 1"
            ),
            {"simulation_id": simulation.id},
        )
    ).first()
    if active is not None:
        return active.job_id, False
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    owner_id = (
        await db.execute(
            text("SELECT created_by FROM runs WHERE id = :run_id"),
            {"run_id": simulation.run_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO runs (id, project_id, created_by, kind, stage, total_steps, "
            "fail_at_step, simulated_epoch) VALUES (:id, :project_id, :created_by, "
            ":kind, :stage, :steps, NULL, :epoch)"
        ),
        {
            "id": run_id,
            "project_id": simulation.project_id,
            "created_by": owner_id,
            "kind": MEMORY_KIND,
            "stage": MEMORY_STAGE,
            "steps": MEMORY_TOTAL_STEPS,
            "epoch": datetime.now(timezone.utc),
        },
    )
    await db.execute(
        text(
            "INSERT INTO jobs (id, run_id, project_id, operation_key, status, "
            "next_step, attempts) VALUES (:id, :run_id, :project_id, :key, 'queued', "
            "0, 0)"
        ),
        {
            "id": job_id,
            "run_id": run_id,
            "project_id": simulation.project_id,
            "key": f"{MEMORY_OPERATION_PREFIX}{simulation.id}",
        },
    )
    await db.execute(
        text(
            "INSERT INTO simulation_memory_syncs (run_id, simulation_id, job_id, "
            "phase, pending_episodes) VALUES (:run_id, :simulation_id, :job_id, "
            "'queued', :pending)"
        ),
        {
            "run_id": run_id,
            "simulation_id": simulation.id,
            "job_id": job_id,
            "pending": await _pending_count(db, simulation.id),
        },
    )
    return job_id, True


async def _pending_count(db: AsyncSession, simulation_id: uuid.UUID) -> int:
    value = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM simulation_memory_episodes "
                "WHERE simulation_id = :simulation_id AND state <> 'processed'"
            ),
            {"simulation_id": simulation_id},
        )
    ).scalar_one()
    return int(value or 0)


async def maybe_create_sync_job(
    db: AsyncSession, *, simulation: Any, config: MemoryConfig
) -> uuid.UUID | None:
    status = await refresh_status(
        db, simulation_id=simulation.id, config=config
    )
    if status in {"complete", "unconfigured"}:
        return None
    if not config.enabled:
        return None
    job_id, _ = await ensure_sync_job(db, simulation=simulation)
    return job_id


async def retry_memory_sync(
    db: AsyncSession, *, simulation: Any, config: MemoryConfig
) -> tuple[uuid.UUID, bool]:
    if not config.enabled:
        raise SimulationConflict("memory_unconfigured", "运行时记忆未配置图谱")
    job_id, created = await ensure_sync_job(db, simulation=simulation)
    await db.commit()
    return job_id, created


# ---------------------------------------------------------------- 记忆同步 worker


async def _memory_claim_guard(
    db: AsyncSession, claim: worker_service.Claim, *, step: int
) -> tuple[str, Any | None]:
    row = await worker_service._locked_job(db, claim)
    if row is None:
        await db.rollback()
        return "lost", None
    if row.status in worker_service.TERMINAL_STATUSES:
        await db.rollback()
        return str(row.status), None
    db_now = await worker_service._db_now(db)
    if not worker_service._lease_valid(row, claim, db_now):
        await db.rollback()
        return "lost", None
    if int(row.next_step) > step:
        await db.rollback()
        return "duplicate", None
    if int(row.next_step) != step - 1:
        await db.rollback()
        return "lost", None
    return "ready", row


async def _memory_commit_step(
    db: AsyncSession,
    claim: worker_service.Claim,
    *,
    step: int,
    step_key: str,
    simulation_id: uuid.UUID,
    extra: Mapping[str, Any] | None = None,
) -> str:
    inserted = await db.execute(
        text(
            "INSERT INTO run_steps (run_id, step_index, step_key, payload) "
            "VALUES (:run_id, :step, :step_key, CAST(:payload AS jsonb)) "
            "ON CONFLICT (run_id, step_index) DO NOTHING"
        ),
        {
            "run_id": claim.run_id,
            "step": step,
            "step_key": step_key,
            "payload": json.dumps({"step": step}, ensure_ascii=False),
        },
    )
    if inserted.rowcount != 1:
        await db.rollback()
        return "duplicate"
    seq = await run_events.max_seq(db, run_id=claim.run_id) + 1
    payload = {
        "simulationId": str(simulation_id),
        "step": step,
        "stepKey": step_key,
        "phase": "running",
    }
    if extra:
        payload.update(extra)
    await run_events.append_event(
        db,
        run_id=claim.run_id,
        seq=seq,
        type="progress",
        stage=claim.stage,
        simulated_at=run_events.simulated_time(claim.simulated_epoch, seq),
        payload=payload,
    )
    await db.execute(
        text(
            "UPDATE jobs SET next_step = :step, lease_expires_at = "
            "clock_timestamp() + make_interval(secs => "
            + worker_service.LEASE_SECONDS_PARAM
            + "), updated_at = clock_timestamp() WHERE id = :job_id "
            "AND lease_token = :lease_token AND status = 'running'"
        ),
        {
            "step": step,
            "job_id": claim.job_id,
            "lease_token": claim.lease_token,
            "lease_seconds": 120.0,
        },
    )
    await db.execute(
        text(
            "UPDATE simulation_memory_syncs SET phase = 'running' WHERE run_id = :run_id"
        ),
        {"run_id": claim.run_id},
    )
    await db.commit()
    return "progress"


async def execute_memory_sync(
    claim: worker_service.Claim,
    *,
    session_factory: worker_service.SessionFactory,
    stop_event: asyncio.Event | None,
    lease_seconds: float,
    heartbeat_seconds: float,
    config: MemoryConfig,
) -> str:
    del heartbeat_seconds
    sync = None
    async with session_factory() as db:
        sync = (
            await db.execute(
                text(
                    "SELECT simulation_id FROM simulation_memory_syncs "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": claim.run_id},
            )
        ).first()
        await db.commit()
    if sync is None:
        return "failed"
    simulation_id = uuid.UUID(str(sync.simulation_id))
    for step in range(claim.next_step + 1, MEMORY_TOTAL_STEPS + 1):
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        async with session_factory() as db:
            status, _ = await _memory_claim_guard(db, claim, step=step)
            if status != "ready":
                await db.rollback()
                return status
            try:
                if step == 1:
                    await ensure_binding(
                        db, simulation_id=simulation_id, config=config
                    )
                    extra = {"provider": config.provider}
                elif step == 2:
                    if config.sync_pause_seconds > 0:
                        await db.rollback()
                        await asyncio.sleep(config.sync_pause_seconds)
                        status, _ = await _memory_claim_guard(db, claim, step=step)
                        if status != "ready":
                            await db.rollback()
                            return status
                    await project_pending(
                        db, simulation_id=simulation_id, config=config
                    )
                    extra = {"pending": await _pending_count(db, simulation_id)}
                else:
                    extra = {"memoryStatus": await refresh_status(
                        db, simulation_id=simulation_id, config=config
                    )}
                await _memory_commit_step(
                    db,
                    claim,
                    step=step,
                    step_key=MEMORY_STEP_KEYS[step - 1],
                    simulation_id=simulation_id,
                    extra=extra,
                )
            except MemoryUnavailable as exc:
                await db.execute(
                    text(
                        "UPDATE simulations SET memory_status = :status, "
                        "memory_failure_code = :code, updated_at = clock_timestamp() "
                        "WHERE id = :simulation_id"
                    ),
                    {
                        "status": "unconfirmed"
                        if exc.code == "write_unconfirmed"
                        else "unavailable",
                        "code": exc.code,
                        "simulation_id": simulation_id,
                    },
                )
                await db.execute(
                    text(
                        "UPDATE simulation_memory_syncs SET phase = 'failed', "
                        "last_error_code = :code, pending_episodes = :pending, "
                        "finished_at = clock_timestamp() WHERE run_id = :run_id"
                    ),
                    {
                        "code": exc.code,
                        "pending": await _pending_count(db, simulation_id),
                        "run_id": claim.run_id,
                    },
                )
                await _finish_memory_job(
                    db, claim, step, status="failed", reason=exc.code
                )
                await db.commit()
                return "failed"
        if step == MEMORY_TOTAL_STEPS:
            async with session_factory() as db:
                await db.execute(
                    text(
                        "UPDATE simulation_memory_syncs SET phase = 'succeeded', "
                        "pending_episodes = 0, finished_at = clock_timestamp() "
                        "WHERE run_id = :run_id"
                    ),
                    {"run_id": claim.run_id},
                )
                await _finish_memory_job(
                    db, claim, MEMORY_TOTAL_STEPS, status="succeeded", reason=None
                )
                await db.commit()
            return "succeeded"
    return "succeeded"


async def _finish_memory_job(
    db: AsyncSession,
    claim: worker_service.Claim,
    step: int,
    *,
    status: str,
    reason: str | None,
) -> None:
    row = await worker_service._locked_job(db, claim)
    if row is None or row.status in worker_service.TERMINAL_STATUSES:
        return
    db_now = await worker_service._db_now(db)
    if not worker_service._lease_valid(row, claim, db_now):
        return
    await worker_service._finish(
        db, claim, step, status=status, reason=reason
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
