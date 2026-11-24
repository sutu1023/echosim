"""双平台运行的纯领域契约：冻结选择解析、轮次规划、动作与标记。

契约只描述“本轮该由谁做什么、目标是谁、记录长什么样”，不导入 OASIS，
使替身引擎、真实引擎、worker 与测试共用同一套输入与判定。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

PLATFORM_MAPPING: dict[str, str] = {
    "platform_a": "twitter",
    "platform_b": "reddit",
}
PLATFORM_KEYS = tuple(PLATFORM_MAPPING)

NATIVE_ACTIONS: dict[str, tuple[str, ...]] = {
    "platform_a": ("create_post", "repost", "like_post"),
    "platform_b": ("create_post", "create_comment", "like_post", "dislike_post"),
}
# 规划用的动作循环：保证 3 轮内覆盖该平台支持的全部动作。
ACTION_CYCLE: dict[str, tuple[str, ...]] = {
    "platform_a": ("create_post", "repost", "like_post"),
    "platform_b": ("create_post", "create_comment", "like_post", "dislike_post"),
}
TARGETED_ACTIONS = frozenset({"repost", "like_post", "dislike_post", "create_comment"})
CONTENT_ACTIONS = frozenset({"create_post", "create_comment"})

DEFAULT_ACTIONS_PER_ROUND = 6
MAX_ACTIONS_PER_ROUND = 50
DEFAULT_MAX_ACTIONS = 5000
CONTENT_MAX_CHARS = 600

MARKER_PATTERN = re.compile(r"\[\[ES:[0-9a-f]{8}-[a-z_]+-r\d+-a\d+\]\]")

REASON_CODES = frozenset(
    {
        "selection_missing",
        "selection_invalid",
        "engine_unavailable",
        "platform_a_setup",
        "platform_b_setup",
        "platform_a_action",
        "platform_b_action",
        "platform_a_record",
        "platform_b_record",
        "platform_a_cleanup",
        "platform_b_cleanup",
        "action_unconfirmed",
        "simulation_timeout",
        "simulation_too_large",
        "simulation_failed",
    }
)

ROUND_OUTCOMES = ("in_flight", "committed", "unconfirmed")


class DuetContractError(ValueError):
    """契约不成立；`code` 是稳定原因码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RolePlan:
    role_id: str
    display_name: str
    role_type: str
    order_index: int
    active_hours: tuple[int, ...]
    posting_frequency: float
    influence_weight: float
    response_delay_minutes: float
    platform_keys: tuple[str, ...]
    topics: tuple[str, ...]


@dataclass(frozen=True)
class SelectionPlan:
    selection_id: str
    preparation_id: str
    project_id: str
    build_id: str
    snapshot_id: str
    question: str
    rounds: int
    minutes_per_round: int
    duration_hours: int
    roles: dict[str, RolePlan]
    order: tuple[str, ...]
    first_speaker_role_ids: tuple[str, ...]
    first_speaker_topics: dict[str, str]
    topic_texts: dict[str, str]
    platforms: tuple[tuple[str, str], ...]
    start_hour: int = 8

    def role(self, role_id: str) -> RolePlan:
        return self.roles[role_id]

    def simulated_hour(self, round_index: int) -> int:
        """推演时钟从首个首发角色的最早活跃小时开始，避免全部落在深夜。"""
        minutes = (round_index - 1) * self.minutes_per_round
        return (self.start_hour + minutes // 60) % 24


@dataclass(frozen=True)
class KnownPost:
    action_key: str
    engine_record_id: str
    role_id: str
    topic: str


@dataclass(frozen=True)
class PlannedAction:
    action_key: str
    action_index: int
    native_action: str
    actor_role_id: str
    actor_agent_id: int
    actor_name: str
    content: str
    marker: str
    topic: str
    target_action_key: str | None = None
    target_engine_record_id: str | None = None

    @property
    def has_content(self) -> bool:
        return self.native_action in CONTENT_ACTIONS


@dataclass(frozen=True)
class EngineRecord:
    action_key: str
    native_action: str
    actor_role_id: str
    actor_agent_id: int
    content: str
    marker: str
    engine_record_id: str
    engine_sequence: int
    target_engine_record_id: str | None
    target_action_key: str | None
    round_index: int


@dataclass(frozen=True)
class RoundReadback:
    round_index: int
    status: str
    records: tuple[EngineRecord, ...]
    unconfirmed: tuple[str, ...] = ()
    planned: tuple[str, ...] = ()
    round_committed: bool = False

    @property
    def complete(self) -> bool:
        return self.status == "committed" and not self.unconfirmed and not self.planned


def _string(raw: Any, *, field_name: str, required: bool = True) -> str:
    if raw is None:
        if required:
            raise DuetContractError("selection_invalid", f"缺少 {field_name}")
        return ""
    text = str(raw).strip()
    if required and not text:
        raise DuetContractError("selection_invalid", f"缺少 {field_name}")
    return text


def _int(raw: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise DuetContractError(
            "selection_invalid", f"{field_name} 不是整数"
        ) from None
    if not minimum <= value <= maximum:
        raise DuetContractError("selection_invalid", f"{field_name} 超出范围")
    return value


def _number(raw: Any, *, field_name: str, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def parse_selection(payload: Mapping[str, Any]) -> SelectionPlan:
    """把 Change 08 的冻结选择解析成运行输入；任何缺口都拒绝开始。"""
    if not isinstance(payload, Mapping):
        raise DuetContractError("selection_invalid", "冻结选择不是对象")
    selection_id = _string(payload.get("selectionId"), field_name="selectionId")
    try:
        uuid.UUID(selection_id)
    except (ValueError, AttributeError):
        raise DuetContractError("selection_invalid", "selectionId 非法") from None
    question = _string(payload.get("question"), field_name="question")
    settings = payload.get("settings") or {}
    if not isinstance(settings, Mapping):
        raise DuetContractError("selection_invalid", "轮数配置缺失")
    rounds = _int(settings.get("rounds"), field_name="rounds", minimum=1, maximum=1000)
    minutes = _int(
        settings.get("minutesPerRound"),
        field_name="minutesPerRound",
        minimum=1,
        maximum=1440,
    )
    duration = _int(
        settings.get("durationHours"),
        field_name="durationHours",
        minimum=1,
        maximum=720,
    )
    raw_roles = payload.get("roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        raise DuetContractError("selection_invalid", "冻结选择没有角色")
    roles: dict[str, RolePlan] = {}
    for raw in raw_roles:
        if not isinstance(raw, Mapping):
            raise DuetContractError("selection_invalid", "角色记录非法")
        role_id = _string(raw.get("roleId"), field_name="roleId")
        try:
            uuid.UUID(role_id)
        except (ValueError, AttributeError):
            raise DuetContractError("selection_invalid", "roleId 非法") from None
        if role_id in roles:
            raise DuetContractError("selection_invalid", "角色重复")
        simulation = raw.get("simulation") or {}
        if not isinstance(simulation, Mapping):
            raise DuetContractError("selection_invalid", "角色缺少模拟设定")
        hours = simulation.get("activeHours")
        if not isinstance(hours, list) or not hours:
            raise DuetContractError("selection_invalid", "角色缺少活跃时段")
        parsed_hours: list[int] = []
        for item in hours:
            hour = _int(item, field_name="activeHours", minimum=0, maximum=23)
            parsed_hours.append(hour)
        platforms = _string_list(simulation.get("platformKeys")) or list(PLATFORM_KEYS)
        for platform_key in platforms:
            if platform_key not in PLATFORM_MAPPING:
                raise DuetContractError("selection_invalid", "角色平台键非法")
        roles[role_id] = RolePlan(
            role_id=role_id,
            display_name=_string(raw.get("displayName"), field_name="displayName"),
            role_type=_string(raw.get("roleType"), field_name="roleType", required=False)
            or "其他主体",
            order_index=_int(
                simulation.get("orderIndex", len(roles)),
                field_name="orderIndex",
                minimum=0,
                maximum=10000,
            ),
            active_hours=tuple(sorted(set(parsed_hours))),
            posting_frequency=max(
                0.0, _number(simulation.get("postingFrequency"), field_name="postingFrequency", default=1.0)
            ),
            influence_weight=max(
                0.0, _number(simulation.get("influenceWeight"), field_name="influenceWeight", default=1.0)
            ),
            response_delay_minutes=max(
                0.0,
                _number(simulation.get("responseDelayMinutes"), field_name="responseDelayMinutes", default=0.0),
            ),
            platform_keys=tuple(platforms),
            topics=tuple(_string_list(simulation.get("topics"))),
        )

    raw_platforms = payload.get("platforms")
    platforms: list[tuple[str, str]] = []
    if isinstance(raw_platforms, list):
        for item in raw_platforms:
            if not isinstance(item, Mapping):
                continue
            key = _string(item.get("platformKey"), field_name="platformKey")
            native = _string(item.get("nativePlatform"), field_name="nativePlatform")
            if key in PLATFORM_MAPPING and PLATFORM_MAPPING[key] != native:
                raise DuetContractError("selection_invalid", "平台映射不一致")
            platforms.append((key, native))
    if dict(platforms) != PLATFORM_MAPPING:
        raise DuetContractError("selection_invalid", "平台映射不完整")

    raw_topics = payload.get("initialTopics")
    topic_texts: dict[str, str] = {}
    has_question_topic = False
    if isinstance(raw_topics, list):
        for item in raw_topics:
            if not isinstance(item, Mapping):
                continue
            topic_id = str(item.get("topicId") or "").strip()
            text = _string(item.get("text"), field_name="topicText")
            if not topic_id:
                raise DuetContractError("selection_invalid", "话题缺少标识")
            topic_texts[topic_id] = text
            if item.get("origin") == "question":
                has_question_topic = True
    if not has_question_topic:
        raise DuetContractError("selection_invalid", "缺少推演问题话题")

    activation = payload.get("activation") or {}
    if not isinstance(activation, Mapping):
        raise DuetContractError("selection_invalid", "缺少激活顺序")
    order = tuple(_string_list(activation.get("order")))
    if not order:
        order = tuple(
            role_id
            for role_id, _ in sorted(
                roles.items(), key=lambda item: item[1].order_index
            )
        )
    if set(order) != set(roles) or len(order) != len(roles):
        raise DuetContractError("selection_invalid", "激活顺序必须覆盖全部角色")
    first_ids = tuple(_string_list(activation.get("firstSpeakerRoleIds")))
    if not first_ids:
        raise DuetContractError("selection_invalid", "缺少首发角色")
    for role_id in first_ids:
        if role_id not in roles:
            raise DuetContractError("selection_invalid", "首发角色不属于本次选择")
    if order[: len(first_ids)] != first_ids:
        raise DuetContractError("selection_invalid", "首发角色必须是激活顺序前缀")
    first_topics: dict[str, str] = {}
    raw_first_topics = activation.get("firstSpeakerTopics")
    if isinstance(raw_first_topics, list):
        for item in raw_first_topics:
            if not isinstance(item, Mapping):
                continue
            role_id = str(item.get("roleId") or "").strip()
            topic_id = str(item.get("topicId") or "").strip()
            if role_id in first_ids and topic_id in topic_texts:
                first_topics[role_id] = topic_id
    for role_id in first_ids:
        if role_id not in first_topics:
            raise DuetContractError("selection_invalid", "首发角色缺少首发话题")

    first_role = roles[first_ids[0]]
    start_hour = min(first_role.active_hours) if first_role.active_hours else 8

    return SelectionPlan(
        selection_id=selection_id,
        preparation_id=_string(payload.get("preparationId"), field_name="preparationId"),
        project_id=_string(payload.get("projectId"), field_name="projectId"),
        build_id=_string(payload.get("buildId"), field_name="buildId"),
        snapshot_id=_string(payload.get("snapshotId"), field_name="snapshotId"),
        question=question,
        rounds=rounds,
        minutes_per_round=minutes,
        duration_hours=duration,
        roles=roles,
        order=order,
        first_speaker_role_ids=first_ids,
        first_speaker_topics=first_topics,
        topic_texts=topic_texts,
        platforms=tuple(platforms),
        start_hour=start_hour,
    )


def agent_id_for(selection: SelectionPlan, role_id: str) -> int:
    """角色在引擎中的稳定 agent/user 标识：按激活顺序从 1 开始。"""
    return selection.order.index(role_id) + 1


def marker_for(run_marker: str, platform_key: str, round_index: int, action_index: int) -> str:
    short = run_marker.replace("-", "")[:8] or "00000000"
    return f"[[ES:{short}-{platform_key}-r{round_index}-a{action_index}]]"


def _short(text: str, limit: int = 80) -> str:
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _topic_for(role: RolePlan, selection: SelectionPlan, fallback: str) -> str:
    for topic in role.topics:
        if topic:
            return topic
    return fallback


def plan_round(
    selection: SelectionPlan,
    *,
    platform_key: str,
    round_index: int,
    run_marker: str,
    known_posts: Sequence[KnownPost],
    actions_per_round: int = DEFAULT_ACTIONS_PER_ROUND,
) -> list[PlannedAction]:
    """确定性规划一轮动作；替身与真实引擎共用同一份计划。"""
    if platform_key not in PLATFORM_MAPPING:
        raise DuetContractError("selection_invalid", "未知平台")
    if not 1 <= round_index <= selection.rounds:
        raise DuetContractError("selection_invalid", "轮次超出冻结值")
    limit = max(1, min(actions_per_round, MAX_ACTIONS_PER_ROUND))
    hour = selection.simulated_hour(round_index)
    actors: list[tuple[RolePlan, bool]] = []
    for index, role_id in enumerate(selection.order):
        role = selection.roles[role_id]
        if platform_key not in role.platform_keys:
            continue
        is_first = round_index == 1 and role_id in selection.first_speaker_role_ids
        if is_first or hour in role.active_hours:
            actors.append((role, is_first))
    actors = actors[:limit]

    actions: list[PlannedAction] = []
    posts = list(known_posts)
    cycle = ACTION_CYCLE[platform_key]
    for position, (role, is_first) in enumerate(actors):
        action_index = len(actions) + 1
        if is_first:
            native_action = "create_post"
            target = None
            topic_id = selection.first_speaker_topics.get(role.role_id)
            topic = selection.topic_texts.get(topic_id or "", selection.question)
        else:
            if not posts:
                native_action = "create_post"
                target = None
                topic = _topic_for(role, selection, selection.question)
            else:
                native_action = cycle[
                    (role.order_index + round_index + position) % len(cycle)
                ]
                candidate = posts[
                    (role.order_index + round_index + position) % len(posts)
                ]
                if (
                    candidate.role_id == role.role_id
                    and native_action in TARGETED_ACTIONS
                ):
                    candidate = posts[
                        (role.order_index + round_index + position + 1) % len(posts)
                    ]
                target = candidate
                topic = candidate.topic

        action_key = f"{platform_key}:r{round_index}:a{action_index}"
        marker = marker_for(run_marker, platform_key, round_index, action_index)
        content = ""
        if native_action == "create_post":
            content = (
                f"{_short(topic, 60)}｜{role.display_name}就“{_short(selection.question, 60)}”"
                f"表达关注。{marker}"
            )
        elif native_action == "create_comment":
            content = f"回应“{_short(topic, 60)}”：{role.display_name}提出补充意见。{marker}"

        action = PlannedAction(
            action_key=action_key,
            action_index=action_index,
            native_action=native_action,
            actor_role_id=role.role_id,
            actor_agent_id=agent_id_for(selection, role.role_id),
            actor_name=role.display_name,
            content=content,
            marker=marker,
            topic=topic,
            target_action_key=target.action_key if target else None,
            target_engine_record_id=target.engine_record_id if target else None,
        )
        if len(action.content) > CONTENT_MAX_CHARS:
            raise DuetContractError("selection_invalid", "动作正文超出上限")
        actions.append(action)
        if native_action == "create_post":
            posts.append(
                KnownPost(
                    action_key=action_key,
                    engine_record_id="",  # 引擎返回后由读回填充
                    role_id=role.role_id,
                    topic=topic,
                )
            )
    return actions


def marker_present(content: str, marker: str) -> bool:
    return bool(marker) and marker in (content or "")


def action_record_id(native_action: str, native_id: int | str) -> str:
    prefixes = {
        "create_post": "post",
        "repost": "post",
        "create_comment": "comment",
        "like_post": "like",
        "dislike_post": "dislike",
    }
    prefix = prefixes.get(native_action)
    if prefix is None:
        raise DuetContractError("platform_b_record", "未知原生动作")
    return f"{prefix}:{native_id}"


def json_ready(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return value


def dump(value: Any) -> str:
    return json.dumps(json_ready(value), ensure_ascii=False)


def iter_action_types(actions: Iterable[PlannedAction]) -> Iterable[str]:
    for action in actions:
        yield action.native_action
