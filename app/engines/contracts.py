"""引擎联通的纯领域契约：材料、结构化结果、行动证据与报告项。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

PASSED = "passed"
FAILED = "failed"
NOT_RUN = "not_run"
ITEM_STATUSES = (PASSED, FAILED, NOT_RUN)

KIND_VALUES = ("schedule", "ownership", "constraint")
FACT_FIELDS = ("text", "evidenceQuote", "sourceStart", "sourceEnd", "kind")

REASON_CODES = frozenset(
    {
        "missing_config",
        "auth_failed",
        "billing_required",
        "edge_blocked",
        "invalid_request",
        "invalid_output",
        "source_mismatch",
        "marker_missing",
        "rate_limited",
        "timeout",
        "server_error",
        "network_error",
        "write_failed",
        "write_unconfirmed",
        "index_timeout",
        "stale_or_foreign_hit",
        "search_failed",
        "prerequisite_failed",
        "dependency_unresolved",
        "shared_setup",
        "platform_a_setup",
        "platform_b_setup",
        "platform_a_action",
        "platform_b_action",
        "platform_a_record",
        "platform_b_record",
        "platform_a_cleanup",
        "platform_b_cleanup",
        "cleanup_failed",
        "action_unconfirmed",
        "dirty_worktree",
        "invalid_graph_scope",
    }
)


@dataclass(frozen=True)
class CallCounts:
    real: int = 0
    double: int = 0

    def add(self, kind: str, count: int = 1) -> "CallCounts":
        if kind == "double":
            return CallCounts(real=self.real, double=self.double + count)
        return CallCounts(real=self.real + count, double=self.double)

    def merge(self, other: "CallCounts") -> "CallCounts":
        return CallCounts(
            real=self.real + other.real, double=self.double + other.double
        )

    def to_dict(self) -> dict[str, int]:
        return {"real": self.real, "double": self.double}


@dataclass
class ItemResult:
    item_id: str
    dependency: str
    stage: str
    status: str
    reason: str | None = None
    duration_ms: int = 0
    calls: CallCounts = field(default_factory=CallCounts)
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in ITEM_STATUSES:
            self.status = FAILED
            self.reason = self.reason or "invalid_request"
        if self.reason is not None and self.reason not in REASON_CODES:
            self.status = FAILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "dependency": self.dependency,
            "stage": self.stage,
            "status": self.status,
            "reason": self.reason,
            "durationMs": self.duration_ms,
            "calls": self.calls.to_dict(),
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class AcceptanceMaterial:
    run_id: str
    material_id: str
    marker: str
    text: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None
    fields_valid: bool
    source_matches: int
    fact_count: int
    marker_matched: bool
    detail: str = ""


def new_run_id() -> str:
    return uuid.uuid4().hex


def build_acceptance_material(run_id: str | None = None) -> AcceptanceMaterial:
    """构造本次运行的原创微型材料；不读取任何项目材料或私有样本。"""
    actual_run_id = run_id or new_run_id()
    marker = f"ECHOSIM-ACCEPT-{actual_run_id}"
    text = (
        f"河畔社区图书角在 2026 年 3 月 14 日收到编号 {marker} 的蓝色书箱。\n"
        "志愿者林舟负责在每周六 10:00 开放借阅，并把新到书目登记在纸质清单上。\n"
        "居民可以在留言簿写下推荐书目，但不允许把书带出活动室。"
    )
    return AcceptanceMaterial(
        run_id=actual_run_id,
        material_id=f"acceptance-{actual_run_id}",
        marker=marker,
        text=text,
    )


def build_graph_fact(material: AcceptanceMaterial) -> str:
    return (
        f"河畔社区图书角验收批次 {material.run_id} 的蓝色书箱编号为 "
        f"{material.marker}，保管人是林舟。"
    )


def build_action_content(platform_key: str, material: AcceptanceMaterial) -> str:
    if platform_key == "platform_a":
        return (
            f"河畔社区图书角验收批次 {material.run_id}："
            "建议周六增加一次开放时段。"
        )
    if platform_key == "platform_b":
        return (
            f"河畔社区图书角验收批次 {material.run_id}："
            "已记录建议，将在下次排班讨论。"
        )
    raise ValueError(f"未知平台：{platform_key}")


def build_target_post_content(platform_key: str, material: AcceptanceMaterial) -> str:
    if platform_key != "platform_b":
        raise ValueError("只有平台 B 需要目标帖")
    return (
        f"河畔社区图书角验收批次 {material.run_id} 的目标帖："
        "请讨论周末开放时段。"
    )


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def utf16_slice(text: str, start: int, end: int) -> str:
    if start < 0 or end < start or end > utf16_len(text):
        raise ValueError("UTF-16 偏移越界")
    encoded = text.encode("utf-16-le")
    return encoded[start * 2 : end * 2].decode("utf-16-le")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_structured_payload(
    payload: object, material: AcceptanceMaterial
) -> ValidationResult:
    if not isinstance(payload, dict):
        return ValidationResult(False, "invalid_output", False, 0, 0, False, "结果不是对象")

    material_id = payload.get("materialId")
    summary = payload.get("summary")
    facts = payload.get("facts")
    if not isinstance(material_id, str) or not material_id:
        return ValidationResult(False, "invalid_output", False, 0, 0, False, "materialId 缺失")
    if material_id != material.material_id:
        return ValidationResult(False, "invalid_output", False, 0, 0, False, "materialId 不一致")
    if not isinstance(summary, str) or not summary.strip():
        return ValidationResult(False, "invalid_output", False, 0, 0, False, "summary 缺失")
    if not isinstance(facts, list) or not facts:
        return ValidationResult(False, "invalid_output", False, 0, 0, False, "facts 为空")

    source_matches = 0
    marker_matched = False
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            return ValidationResult(
                False, "invalid_output", False, source_matches, len(facts), marker_matched,
                f"facts[{index}] 不是对象",
            )
        for field_name in FACT_FIELDS:
            if field_name not in fact:
                return ValidationResult(
                    False, "invalid_output", False, source_matches, len(facts), marker_matched,
                    f"facts[{index}].{field_name} 缺失",
                )
        text = fact.get("text")
        quote = fact.get("evidenceQuote")
        start = fact.get("sourceStart")
        end = fact.get("sourceEnd")
        kind = fact.get("kind")
        if not isinstance(text, str) or not text.strip():
            return ValidationResult(
                False, "invalid_output", False, source_matches, len(facts), marker_matched,
                f"facts[{index}].text 非法",
            )
        if not isinstance(quote, str) or not quote:
            return ValidationResult(
                False, "invalid_output", False, source_matches, len(facts), marker_matched,
                f"facts[{index}].evidenceQuote 非法",
            )
        if kind not in KIND_VALUES:
            return ValidationResult(
                False, "invalid_output", False, source_matches, len(facts), marker_matched,
                f"facts[{index}].kind 非法",
            )
        if not _is_int(start) or not _is_int(end):
            return ValidationResult(
                False, "invalid_output", False, source_matches, len(facts), marker_matched,
                f"facts[{index}] 偏移不是整数",
            )
        try:
            sliced = utf16_slice(material.text, int(start), int(end))
        except (ValueError, UnicodeDecodeError):
            return ValidationResult(
                False, "source_mismatch", True, source_matches, len(facts), marker_matched,
                f"facts[{index}] 偏移越界或不在字符边界",
            )
        if sliced != quote:
            return ValidationResult(
                False, "source_mismatch", True, source_matches, len(facts), marker_matched,
                f"facts[{index}] 原文切片不一致",
            )
        source_matches += 1
        if material.marker in quote:
            marker_matched = True

    if not marker_matched:
        return ValidationResult(
            False, "marker_missing", True, source_matches, len(facts), False,
            "没有任何事实包含本次运行标识",
        )
    return ValidationResult(True, None, True, source_matches, len(facts), True)


def parse_json_object(text: str) -> object:
    """解析模型返回的 JSON 文本；不执行任何内容。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("响应不是 JSON") from exc


def merge_calls(results: Iterable[ItemResult]) -> CallCounts:
    total = CallCounts()
    for result in results:
        total = total.merge(result.calls)
    return total
