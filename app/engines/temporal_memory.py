"""时序记忆纯契约：稳定标识、episode 正文、节点/边键与来源分层。

本模块只使用标准库，不导入图谱 SDK，供 worker、服务层、测试与验收共用。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
from unicodedata import normalize

MEMORY_PURPOSE = "simulation-memory"
ORIGINS = ("role", "simulation")
FACETS = ("role_speech", "simulation_result")
MEMORY_STATUSES = (
    "unconfigured",
    "pending",
    "syncing",
    "complete",
    "partial",
    "unconfirmed",
    "unavailable",
)


def memory_id_for(simulation_id: uuid.UUID | str, action_id: uuid.UUID | str) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"echosim:memory:{simulation_id}:{action_id}",
    )


def episode_key_for(action_id: uuid.UUID | str) -> str:
    return f"action:{action_id}"


def episode_idempotency_key_for(
    simulation_id: uuid.UUID | str, action_id: uuid.UUID | str
) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"echosim:memory-episode:{simulation_id}:{action_id}",
    )


def graph_create_key_for(simulation_id: uuid.UUID | str) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"echosim:memory-graph:{simulation_id}",
    )


def deterministic_episode_uuid(
    simulation_id: uuid.UUID | str, action_id: uuid.UUID | str
) -> uuid.UUID:
    """离线图谱替身使用确定性 episode UUID，重启后仍可查账。"""
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"echosim:memory-episode-uuid:{simulation_id}:{action_id}",
    )


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def episode_content_for(
    *,
    simulation_id: uuid.UUID | str,
    action_id: uuid.UUID | str,
    memory_id: uuid.UUID | str,
    platform_key: str,
    native_platform: str,
    round_index: int,
    simulated_at: datetime,
    actor_role_id: uuid.UUID | str,
    actor_display_name: str,
    native_action: str,
    content: str,
    target_engine_record_id: str | None,
) -> str:
    """确定性 episode 正文；只含行动字段，不含材料正文或凭据。"""
    lines = [
        f"[{simulated_at.isoformat()}] {native_platform} 第{round_index}轮",
        f"角色：{actor_display_name}（{actor_role_id}）",
        f"动作：{native_action}",
    ]
    if content:
        lines.append(f"发言：{content}")
    if target_engine_record_id:
        lines.append(f"目标：{target_engine_record_id}")
    lines.append(f"episodeKey={episode_key_for(action_id)}")
    lines.append(f"memoryId={memory_id}")
    lines.append(f"simulationId={simulation_id}")
    lines.append(f"platformKey={platform_key}")
    return "\n".join(lines)


def normalize_name(value: str) -> str:
    return " ".join(normalize("NFKC", value or "").split()).casefold()


def node_key_for(node_type: str, name: str) -> str:
    return f"{node_type}:{normalize_name(name)}"


def edge_key_for(
    source_node_key: str,
    target_node_key: str,
    edge_type: str,
    fact: str,
) -> str:
    return (
        f"{source_node_key}|{edge_type}|{target_node_key}|"
        f"{normalize_name(fact)}"
    )


@dataclass(frozen=True)
class MemoryRef:
    memory_id: str
    action_id: str
    round_index: int
    simulated_at: str
    episode_id: str | None = None
    source_event_seq: int | None = None
    facets: tuple[str, ...] = FACETS
    material_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "memoryId": self.memory_id,
            "actionId": self.action_id,
            "roundIndex": self.round_index,
            "simulatedAt": self.simulated_at,
            "facets": list(self.facets),
        }
        if self.episode_id:
            payload["episodeId"] = self.episode_id
        if self.source_event_seq:
            payload["sourceEventSeq"] = self.source_event_seq
        if self.material_refs:
            payload["materialRefs"] = [dict(item) for item in self.material_refs]
        return payload


def memory_refs_payload(refs: Iterable[MemoryRef]) -> list[dict[str, Any]]:
    return [ref.as_dict() for ref in refs]


def dedupe_refs(refs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for ref in refs:
        memory_id = str(ref.get("memoryId") or "")
        if not memory_id or memory_id in seen:
            continue
        seen.add(memory_id)
        ordered.append(dict(ref))
    return ordered


def refs_from_payload(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    raw = payload.get("memoryRefs")
    if not isinstance(raw, list):
        return []
    return dedupe_refs([item for item in raw if isinstance(item, Mapping)])


def plan_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
