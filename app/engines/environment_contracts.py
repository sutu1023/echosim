"""角色与环境准备的纯契约：轮数、模拟设定与画像输出校验。

本模块只使用标准库，不访问数据库、模型或网络；路由、worker 与测试共用同一套
判定，避免“前端算一次、服务端算一次”的口径分裂。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

DEFAULT_DURATION_HOURS = 72
DEFAULT_MINUTES_PER_ROUND = 60
DEFAULT_MAX_MANUAL_ROUNDS = 1000
DEFAULT_PROFILE_BATCH_SIZE = 2
DEFAULT_MODEL_TIMEOUT_SECONDS = 30.0

MIN_DURATION_HOURS = 1
MAX_DURATION_HOURS = 720
MIN_MINUTES_PER_ROUND = 1
MAX_MINUTES_PER_ROUND = 1440

ROUNDS_MODE_AUTO = "auto"
ROUNDS_MODE_MANUAL = "manual"
ROUNDS_MODES = (ROUNDS_MODE_AUTO, ROUNDS_MODE_MANUAL)

PLATFORM_KEYS = ("platform_a", "platform_b")
NATIVE_PLATFORMS = {"platform_a": "twitter", "platform_b": "reddit"}

SIMULATION_FIELDS = frozenset(
    {
        "motive",
        "stance",
        "activeHours",
        "postingFrequency",
        "responseDelayMinutes",
        "influenceWeight",
        "platformKeys",
        "topics",
        "orderIndex",
        "firstSpeaker",
        "firstTopic",
    }
)


class EnvironmentContractError(ValueError):
    """准备契约不成立；`code` 是稳定原因码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RoundSettings:
    duration_hours: int
    minutes_per_round: int
    rounds_mode: str
    manual_rounds: int | None
    rounds: int
    auto_rounds: int


def _integer(value: object, *, code: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EnvironmentContractError(code, f"{field} 必须是整数")
    return value


def compute_rounds(
    *,
    duration_hours: object,
    minutes_per_round: object,
    rounds_mode: object,
    manual_rounds: object,
    max_manual_rounds: int = DEFAULT_MAX_MANUAL_ROUNDS,
) -> RoundSettings:
    """按服务端公式计算轮数；非法输入给出稳定原因码。"""
    mode = str(rounds_mode or ROUNDS_MODE_AUTO).strip().lower()
    if mode not in ROUNDS_MODES:
        raise EnvironmentContractError("rounds_invalid", "轮数模式无效")
    hours = _integer(duration_hours, code="duration_invalid", field="durationHours")
    if hours < MIN_DURATION_HOURS or hours > MAX_DURATION_HOURS:
        raise EnvironmentContractError(
            "duration_invalid", "模拟总时长必须是 1–720 小时的整数"
        )
    minutes = _integer(
        minutes_per_round, code="rounds_invalid", field="minutesPerRound"
    )
    if minutes < MIN_MINUTES_PER_ROUND or minutes > MAX_MINUTES_PER_ROUND:
        raise EnvironmentContractError(
            "rounds_invalid", "每轮分钟数必须是 1–1440 的整数"
        )
    total_minutes = hours * 60
    if total_minutes % minutes != 0:
        raise EnvironmentContractError(
            "rounds_not_integral", "自动轮数不能整除，请调整时长或每轮分钟数"
        )
    auto_rounds = total_minutes // minutes
    if auto_rounds < 1 or auto_rounds > DEFAULT_MAX_MANUAL_ROUNDS:
        raise EnvironmentContractError("rounds_invalid", "自动轮数超出允许范围")

    if mode == ROUNDS_MODE_AUTO:
        return RoundSettings(
            duration_hours=hours,
            minutes_per_round=minutes,
            rounds_mode=mode,
            manual_rounds=None,
            rounds=auto_rounds,
            auto_rounds=auto_rounds,
        )

    manual = _integer(manual_rounds, code="rounds_invalid", field="manualRounds")
    if manual < 1 or manual > max_manual_rounds:
        raise EnvironmentContractError(
            "rounds_invalid", f"手动轮数必须是 1–{max_manual_rounds} 的整数"
        )
    return RoundSettings(
        duration_hours=hours,
        minutes_per_round=minutes,
        rounds_mode=mode,
        manual_rounds=manual,
        rounds=manual,
        auto_rounds=auto_rounds,
    )


def rounds_from_db(
    *,
    duration_hours: int,
    minutes_per_round: int,
    rounds_mode: str,
    manual_rounds: int | None,
    rounds: int,
) -> RoundSettings:
    """读回已持久化的轮数，并重新计算自动轮数供页面展示。"""
    auto = (int(duration_hours) * 60) // int(minutes_per_round)
    return RoundSettings(
        duration_hours=int(duration_hours),
        minutes_per_round=int(minutes_per_round),
        rounds_mode=str(rounds_mode),
        manual_rounds=None if manual_rounds is None else int(manual_rounds),
        rounds=int(rounds),
        auto_rounds=auto,
    )


def _number(value: object, *, field: str, minimum: float, strict: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EnvironmentContractError(
            "invalid_model_output", f"{field} 必须是数值"
        )
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        raise EnvironmentContractError(
            "invalid_model_output", f"{field} 必须是有限数值"
        )
    if strict and numeric <= minimum:
        raise EnvironmentContractError(
            "invalid_model_output", f"{field} 必须大于 {minimum}"
        )
    if not strict and numeric < minimum:
        raise EnvironmentContractError(
            "invalid_model_output", f"{field} 不能小于 {minimum}"
        )
    return numeric


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EnvironmentContractError(
            "invalid_model_output", f"{field} 必须是非空字符串"
        )
    return value.strip()


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise EnvironmentContractError(
            "invalid_model_output", f"{field} 必须是非空数组"
        )
    result = [_string(item, field=field) for item in value]
    if len(set(result)) != len(result):
        raise EnvironmentContractError(
            "invalid_model_output", f"{field} 不能包含重复项"
        )
    return result


def validate_simulation(raw: object, *, field: str = "simulation") -> dict[str, Any]:
    """校验单个角色的模拟设定；任何未知字段都拒绝。"""
    if not isinstance(raw, dict):
        raise EnvironmentContractError(
            "invalid_model_output", f"{field} 必须是对象"
        )
    unknown = set(raw).difference(SIMULATION_FIELDS)
    if unknown:
        raise EnvironmentContractError(
            "invalid_model_output", f"{field} 含未知字段：{sorted(unknown)}"
        )
    hours = raw.get("activeHours")
    if not isinstance(hours, list) or not hours:
        raise EnvironmentContractError(
            "invalid_model_output", f"{field}.activeHours 必须是非空数组"
        )
    parsed_hours: list[int] = []
    for item in hours:
        hour = _integer(
            item, code="invalid_model_output", field=f"{field}.activeHours"
        )
        if hour < 0 or hour > 23:
            raise EnvironmentContractError(
                "invalid_model_output", "活跃时段必须是 0–23 的整数"
            )
        parsed_hours.append(hour)
    if parsed_hours != sorted(set(parsed_hours)):
        raise EnvironmentContractError(
            "invalid_model_output", "活跃时段必须升序且不重复"
        )
    platforms = _string_list(raw.get("platformKeys"), field=f"{field}.platformKeys")
    if any(item not in PLATFORM_KEYS for item in platforms):
        raise EnvironmentContractError(
            "invalid_model_output", "平台偏好只能使用 platform_a/platform_b"
        )
    topics = _string_list(raw.get("topics"), field=f"{field}.topics")
    order_index = _integer(
        raw.get("orderIndex"),
        code="invalid_model_output",
        field=f"{field}.orderIndex",
    )
    if order_index < 1:
        raise EnvironmentContractError(
            "invalid_model_output", "激活顺序必须从 1 开始"
        )
    first_speaker = raw.get("firstSpeaker")
    if not isinstance(first_speaker, bool):
        raise EnvironmentContractError(
            "invalid_model_output", "firstSpeaker 必须是布尔值"
        )
    first_topic = raw.get("firstTopic")
    if first_topic is not None:
        first_topic = _string(first_topic, field=f"{field}.firstTopic")
    if first_speaker and first_topic is None:
        raise EnvironmentContractError(
            "invalid_model_output", "首发角色必须对应一个首发话题"
        )
    return {
        "motive": _string(raw.get("motive"), field=f"{field}.motive"),
        "stance": _string(raw.get("stance"), field=f"{field}.stance"),
        "activeHours": parsed_hours,
        "postingFrequency": _number(
            raw.get("postingFrequency"),
            field=f"{field}.postingFrequency",
            minimum=0,
            strict=True,
        ),
        "responseDelayMinutes": _number(
            raw.get("responseDelayMinutes"),
            field=f"{field}.responseDelayMinutes",
            minimum=0,
            strict=False,
        ),
        "influenceWeight": _number(
            raw.get("influenceWeight"),
            field=f"{field}.influenceWeight",
            minimum=0,
            strict=True,
        ),
        "platformKeys": platforms,
        "topics": topics,
        "orderIndex": order_index,
        "firstSpeaker": first_speaker,
        "firstTopic": first_topic,
    }


@dataclass(frozen=True)
class ValidatedProfile:
    roles: dict[str, dict[str, Any]]
    initial_topics: tuple[str, ...]
    order: tuple[str, ...]
    first_speakers: tuple[str, ...]
    first_speaker_topics: dict[str, str]


def validate_profile(
    raw: object,
    *,
    role_keys: Sequence[str],
    question: str,
    require_first_speaker: bool = True,
) -> ValidatedProfile:
    """校验模型/替身输出：角色键、范围、激活顺序与首发话题对应。"""
    if not isinstance(raw, dict):
        raise EnvironmentContractError(
            "invalid_model_output", "画像输出必须是 JSON 对象"
        )
    unknown = set(raw).difference({"roles", "initialTopics", "activation"})
    if unknown:
        raise EnvironmentContractError(
            "invalid_model_output", f"画像输出含未知顶层字段：{sorted(unknown)}"
        )
    expected = list(role_keys)
    if not expected:
        raise EnvironmentContractError(
            "invalid_model_output", "画像请求没有任何角色"
        )
    raw_roles = raw.get("roles")
    if not isinstance(raw_roles, dict):
        raise EnvironmentContractError(
            "invalid_model_output", "roles 必须是对象"
        )
    if set(raw_roles) != set(expected):
        raise EnvironmentContractError(
            "invalid_model_output", "画像角色键与本次候选不一致"
        )
    roles = {
        key: validate_simulation(raw_roles[key], field=f"roles.{key}")
        for key in expected
    }

    topics = _string_list(raw.get("initialTopics"), field="initialTopics")
    activation = raw.get("activation")
    if not isinstance(activation, dict):
        raise EnvironmentContractError(
            "invalid_model_output", "activation 必须是对象"
        )
    unknown_activation = set(activation).difference(
        {"order", "firstSpeakerRoles", "firstSpeakerTopics"}
    )
    if unknown_activation:
        raise EnvironmentContractError(
            "invalid_model_output",
            f"activation 含未知字段：{sorted(unknown_activation)}",
        )
    order = _string_list(activation.get("order"), field="activation.order")
    if len(order) != len(expected) or set(order) != set(expected):
        raise EnvironmentContractError(
            "invalid_model_output", "激活顺序必须恰好覆盖本次全部角色"
        )
    for position, key in enumerate(order, start=1):
        if roles[key]["orderIndex"] != position:
            raise EnvironmentContractError(
                "invalid_model_output", "角色顺序号必须与激活顺序一致"
            )
    raw_first = activation.get("firstSpeakerRoles")
    if require_first_speaker:
        first_speakers = _string_list(
            raw_first,
            field="activation.firstSpeakerRoles",
        )
    else:
        if raw_first is None:
            first_speakers = []
        elif isinstance(raw_first, list) and not raw_first:
            first_speakers = []
        else:
            raise EnvironmentContractError(
                "invalid_model_output", "非首批不能声明首发角色"
            )
    if first_speakers and (
        len(first_speakers) > len(order)
        or order[: len(first_speakers)] != first_speakers
    ):
        raise EnvironmentContractError(
            "invalid_model_output", "首发角色必须是激活顺序的前缀"
        )
    raw_topics = activation.get("firstSpeakerTopics")
    if not isinstance(raw_topics, list):
        raise EnvironmentContractError(
            "invalid_model_output", "firstSpeakerTopics 必须是数组"
        )
    allowed_topics = {question.strip(), *topics}
    for key in expected:
        allowed_topics.update(roles[key]["topics"])
    mapping: dict[str, str] = {}
    for item in raw_topics:
        if not isinstance(item, dict) or set(item) != {"roleKey", "topic"}:
            raise EnvironmentContractError(
                "invalid_model_output",
                "首发话题必须只包含 roleKey 与 topic",
            )
        role_key = _string(item.get("roleKey"), field="firstSpeakerTopics.roleKey")
        topic = _string(item.get("topic"), field="firstSpeakerTopics.topic")
        if role_key not in first_speakers:
            raise EnvironmentContractError(
                "invalid_model_output", "首发话题引用了非首发角色"
            )
        if topic not in allowed_topics:
            raise EnvironmentContractError(
                "invalid_model_output", "首发话题不在允许的初始话题内"
            )
        if role_key in mapping:
            raise EnvironmentContractError(
                "invalid_model_output", "同一首发角色只能对应一个话题"
            )
        mapping[role_key] = topic
    if set(mapping) != set(first_speakers):
        raise EnvironmentContractError(
            "invalid_model_output", "每个首发角色都必须对应一个首发话题"
        )
    for key in expected:
        is_first = key in first_speakers
        if roles[key]["firstSpeaker"] != is_first:
            raise EnvironmentContractError(
                "invalid_model_output", "角色首发标记与激活首发列表不一致"
            )
        if is_first and roles[key]["firstTopic"] != mapping[key]:
            raise EnvironmentContractError(
                "invalid_model_output", "角色首发话题与激活对应关系不一致"
            )
        if not is_first and roles[key]["firstTopic"] is not None:
            raise EnvironmentContractError(
                "invalid_model_output", "非首发角色不能带首发话题"
            )
    return ValidatedProfile(
        roles=roles,
        initial_topics=tuple(topics),
        order=tuple(order),
        first_speakers=tuple(first_speakers),
        first_speaker_topics=mapping,
    )


def profile_batches(role_keys: Sequence[str], batch_size: int) -> list[list[str]]:
    """按角色键稳定排序切分批次；同一快照每次都得到同一批次。"""
    ordered = sorted(role_keys)
    size = max(1, int(batch_size))
    return [ordered[index : index + size] for index in range(0, len(ordered), size)]
