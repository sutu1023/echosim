"""角色候选与材料事实分层：只读已发布快照，不重新建图、不调用模型。

判定顺序固定为“平台渠道 → 非角色类型 → 可参与主体 → 未知类型看来源原句”，
因此媒体机构仍可发言，而平台渠道不会被画成角色。关系方向只提供上下文：
只出现在终点的高校仍可入选，有出边的事件与待核实说法仍被排除。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from ..services.evidence_graph import GraphEdge, GraphEvidence, GraphNode, GraphSnapshot

CLASSIFIER_VERSION = "environment-roles-v1"

CHINESE_ROLE_TYPES = frozenset(
    {
        "个人",
        "人物",
        "群体",
        "高校",
        "机构",
        "机构组织",
        "部门",
        "政府机构",
        "法院",
        "医疗机构",
        "社会组织",
        "媒体机构",
        "新闻机构",
        "媒体",
        "学校",
        "学院",
        "大学",
        "企业",
        "公司",
        "协会",
    }
)
CHINESE_PLATFORM_TYPES = frozenset(
    {
        "平台",
        "社交媒体",
        "传播平台",
        "媒体平台",
        "平台渠道",
        "渠道",
    }
)
CHINESE_NON_ROLE_TYPES = frozenset(
    {
        "事件",
        "事故",
        "待核实说法",
        "传言",
        "建议",
        "措施",
        "话题",
        "地点",
        "决定",
        "决定安排",
        "处分",
        "判决",
        "安排",
        "材料事项",
        "材料摘录",
        "细节",
    }
)

ENGLISH_ROLE_SUFFIXES = (
    "Person",
    "User",
    "Group",
    "Community",
    "Organization",
    "Organisation",
    "Institution",
    "Company",
    "University",
    "School",
    "Government",
    "MediaOutlet",
    "NewsAgency",
    "Agency",
    "Association",
)
ENGLISH_PLATFORM_SUFFIXES = ("Platform", "Channel", "SocialNetwork")
ENGLISH_NON_ROLE_SUFFIXES = (
    "Event",
    "Incident",
    "Claim",
    "Allegation",
    "Rumor",
    "Rumour",
    "Recommendation",
    "Suggestion",
    "Measure",
    "Topic",
    "Location",
    "Decision",
    "Detail",
)
GENERIC_TYPES = frozenset({"其他主体", "entity", "Entity", "Node", "node", ""})

PLATFORM_MARKERS = ("平台", "社交媒体", "传播平台", "渠道")
NON_ROLE_MARKERS = (
    "事件",
    "事故",
    "说法",
    "传言",
    "传闻",
    "据称",
    "未证实",
    "建议",
    "措施",
    "话题",
    "地点",
    "平台",
)
PERSON_MARKERS = ("记者", "教授", "教师", "负责人", "发言人", "当事人", "居民", "市民", "学生")
GROUP_MARKERS = ("群体", "家长", "校友", "居民", "市民", "学生")
UNIVERSITY_MARKERS = ("大学", "学院", "学校", "高校")
MEDIA_MARKERS = ("报社", "媒体", "新闻", "电视台", "广播", "杂志")
ORGANIZATION_MARKERS = (
    "组织",
    "机构",
    "医院",
    "法院",
    "政府",
    "公司",
    "企业",
    "协会",
    "部门",
    "中心",
    "委员会",
)
EXPRESSION_MARKERS = (
    "主张",
    "表示",
    "称",
    "指出",
    "认为",
    "回应",
    "反对",
    "支持",
    "质疑",
    "呼吁",
    "要求",
    "表达",
    "宣布",
    "声明",
    "报道",
    "通报",
    "发布",
)


@dataclass(frozen=True)
class SourceRef:
    material_field: str
    field_index: int
    source_index: int
    target_kind: str
    target_key: str
    evidence_index: int
    claim_nature: str | None = None


@dataclass(frozen=True)
class RoleDraft:
    node_key: str
    display_name: str
    role_type: str
    mention: dict[str, object]
    background: dict[str, object]
    expressed_topics: tuple[dict[str, object], ...]
    involved_topics: tuple[dict[str, object], ...]
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class EnvironmentTopicDraft:
    text: str
    origin: str
    claim_nature: str | None
    claim_nature_known: bool
    sources: tuple[SourceRef, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExcludedNode:
    node_key: str
    label: str
    node_type: str
    reason: str


@dataclass(frozen=True)
class RoleSelection:
    roles: tuple[RoleDraft, ...]
    material_topics: tuple[EnvironmentTopicDraft, ...]
    excluded: tuple[ExcludedNode, ...]
    classifier: str = CLASSIFIER_VERSION


def _is_platform_type(node_type: str) -> bool:
    text = node_type.strip()
    if text in CHINESE_PLATFORM_TYPES:
        return True
    if any(marker in text for marker in PLATFORM_MARKERS):
        return True
    return text.endswith(ENGLISH_PLATFORM_SUFFIXES)


def _is_english_non_role(node_type: str) -> bool:
    return node_type.endswith(ENGLISH_NON_ROLE_SUFFIXES)


def _is_english_role(node_type: str) -> bool:
    return node_type.endswith(ENGLISH_ROLE_SUFFIXES)


def _local_clause(quote: str, label: str) -> str:
    """返回同时包含当前主体名称的从句，避免把别人的类型/动作算给该主体。"""
    if not label or label not in quote:
        return ""
    for segment in re.split(r"[，,；;。:：\n]", quote):
        if label in segment:
            return segment.strip()
    return ""


def _resolve_generic_type(quote: str, label: str) -> str | None:
    """未知类型按当前主体名称及其所在从句判定，不整句借用其他主体。"""
    text = quote.strip()
    if not text or not label.strip() or label not in text:
        return None
    # 名称本身已包含明确身份时优先使用它；“林记者”不能因句中出现“某大学”
    # 被判成高校。
    if any(marker in label for marker in UNIVERSITY_MARKERS):
        return "高校"
    if any(marker in label for marker in MEDIA_MARKERS):
        return "媒体机构"
    if any(marker in label for marker in GROUP_MARKERS):
        return "群体"
    if any(marker in label for marker in PERSON_MARKERS):
        return "个人"
    if any(marker in label for marker in ORGANIZATION_MARKERS):
        return "机构"
    clause = _local_clause(text, label)
    if not clause or any(marker in clause for marker in NON_ROLE_MARKERS):
        return None
    if any(marker in clause for marker in UNIVERSITY_MARKERS):
        return "高校"
    if any(marker in clause for marker in MEDIA_MARKERS):
        return "媒体机构"
    if any(marker in clause for marker in GROUP_MARKERS):
        return "群体"
    if any(marker in clause for marker in PERSON_MARKERS):
        return "个人"
    if any(marker in clause for marker in ORGANIZATION_MARKERS):
        return "机构"
    return None


def classify_node(
    node: GraphNode, node_evidence: Iterable[GraphEvidence]
) -> tuple[bool, str | None, str]:
    """返回 (是否可参与, 解析出的身份类型, 判定说明)。"""
    if node.origin != "material":
        return False, None, "origin_not_material"
    node_type = (node.node_type or "").strip()
    if _is_platform_type(node_type):
        return False, None, "platform_channel"
    if node_type in CHINESE_ROLE_TYPES:
        return True, node_type, "role_type"
    if node_type in CHINESE_NON_ROLE_TYPES:
        return False, None, "non_role_type"
    if _is_english_non_role(node_type):
        return False, None, "non_role_type"
    if _is_english_role(node_type):
        return True, node_type, "role_type"
    if node_type not in GENERIC_TYPES:
        # 未登记的中文类型：先按非角色标记判断，再尝试按来源原句判定。
        if any(marker in node_type for marker in NON_ROLE_MARKERS):
            return False, None, "non_role_type"
    quote = _first_quote(node_evidence)
    resolved = _resolve_generic_type(quote, node.label)
    if resolved is None:
        return False, None, "unresolved_type"
    return True, resolved, "resolved_by_quote"


def _first_quote(items: Iterable[GraphEvidence]) -> str:
    ordered = sorted(items, key=lambda item: item.evidence_index)
    return ordered[0].quote if ordered else ""


def _valid_evidence(items: Iterable[GraphEvidence]) -> list[GraphEvidence]:
    return [
        item
        for item in sorted(items, key=lambda value: value.evidence_index)
        if item.quote.strip()
        and item.source_start >= 0
        and item.source_end > item.source_start
    ]


def _edge_label(edge: GraphEdge) -> str:
    return (edge.label or edge.edge_type or "").strip()


def _is_expression(edge: GraphEdge, quote: str, role_label: str) -> bool:
    """表达话题必须绑定到当前主体自己的动作，而不是同句其他人的话。"""
    action = f"{edge.label} {edge.edge_type}"
    if any(marker in action for marker in EXPRESSION_MARKERS):
        return True
    clause = _local_clause(quote, role_label)
    if not clause:
        return False
    position = clause.find(role_label)
    if position < 0:
        return False
    after = clause[position + len(role_label) :]
    return any(marker in after for marker in EXPRESSION_MARKERS)


def _topic_text(edge: GraphEdge, other: GraphNode, other_is_role: bool) -> str:
    if other_is_role:
        return f"{_edge_label(edge)} · {other.label}"
    return other.label


def _source_refs(
    *,
    field_name: str,
    field_index: int,
    target_kind: str,
    target_key: str,
    evidence: Iterable[GraphEvidence],
    claim_nature: str | None,
) -> tuple[SourceRef, ...]:
    return tuple(
        SourceRef(
            material_field=field_name,
            field_index=field_index,
            source_index=position,
            target_kind=target_kind,
            target_key=target_key,
            evidence_index=item.evidence_index,
            claim_nature=claim_nature,
        )
        for position, item in enumerate(
            sorted(evidence, key=lambda value: value.evidence_index), start=1
        )
    )


def select_roles(snapshot: GraphSnapshot) -> RoleSelection:
    """从同一已发布快照选出角色，并把材料提及/背景/话题分开。"""
    node_evidence: dict[str, list[GraphEvidence]] = {}
    edge_evidence: dict[str, list[GraphEvidence]] = {}
    for item in snapshot.evidence:
        target = node_evidence if item.target_kind == "node" else edge_evidence
        target.setdefault(item.target_key, []).append(item)

    nodes = {node.key: node for node in snapshot.nodes}
    ordered_nodes = sorted(snapshot.nodes, key=lambda item: (item.label, item.key))
    decisions: dict[str, tuple[bool, str | None, str]] = {}
    for node in ordered_nodes:
        decisions[node.key] = classify_node(node, node_evidence.get(node.key, []))

    candidates: dict[str, GraphNode] = {}
    excluded: list[ExcludedNode] = []
    for node in ordered_nodes:
        eligible, _identity, reason = decisions[node.key]
        valid = _valid_evidence(node_evidence.get(node.key, []))
        if not eligible:
            excluded.append(
                ExcludedNode(node.key, node.label, node.node_type, reason)
            )
            continue
        if not valid:
            excluded.append(
                ExcludedNode(node.key, node.label, node.node_type, "missing_source")
            )
            continue
        candidates[node.key] = node

    roles: list[RoleDraft] = []
    material_topics: list[EnvironmentTopicDraft] = []
    seen_material_topics: set[tuple[str, str | None]] = set()
    for node_key in sorted(candidates, key=lambda key: (candidates[key].label, key)):
        node = candidates[node_key]
        node_sources = _valid_evidence(node_evidence.get(node_key, []))
        mention_evidence = node_sources[:1]
        mention = {
            "text": mention_evidence[0].quote if mention_evidence else node.label,
        }
        sources: list[SourceRef] = list(
            _source_refs(
                field_name="mention",
                field_index=0,
                target_kind="node",
                target_key=node_key,
                evidence=mention_evidence,
                claim_nature=None,
            )
        )

        expressed: list[dict[str, object]] = []
        involved: list[dict[str, object]] = []
        background: dict[str, object] = {"text": "", "stated": False}
        expressed_keys: set[tuple[str, str | None]] = set()

        incident: list[tuple[GraphEdge, list[GraphEvidence], GraphNode, str]] = []
        for edge in sorted(snapshot.edges, key=lambda item: item.key):
            if node_key not in (edge.source_key, edge.target_key):
                continue
            evidence = _valid_evidence(edge_evidence.get(edge.key, []))
            if not evidence:
                continue
            other_key = (
                edge.target_key if edge.source_key == node_key else edge.source_key
            )
            other = nodes.get(other_key)
            if other is None:
                continue
            incident.append((edge, evidence, other, other_key))

        # 先确定角色表达过的话题；同一话题不再重复出现在“材料涉及”。
        expressed_edges: set[str] = set()
        for edge, evidence, other, other_key in incident:
            if edge.claim_nature == "author_recommendation":
                continue
            if (
                edge.source_key != node_key
                or other_key in candidates
                or other.origin != "material"
                or not _is_expression(edge, evidence[0].quote, node.label)
            ):
                continue
            topic_text = _topic_text(edge, other, False)
            marker = (topic_text, edge.claim_nature)
            if not topic_text or marker in expressed_keys:
                continue
            expressed_keys.add(marker)
            expressed_edges.add(edge.key)
            field_index = len(expressed)
            expressed.append(
                {
                    "text": topic_text,
                    "claimNature": edge.claim_nature,
                    "claimNatureKnown": edge.claim_nature is not None,
                }
            )
            sources.extend(
                _source_refs(
                    field_name="expressedTopics",
                    field_index=field_index,
                    target_kind="edge",
                    target_key=edge.key,
                    evidence=evidence[:2],
                    claim_nature=edge.claim_nature,
                )
            )
            if marker not in seen_material_topics:
                seen_material_topics.add(marker)
                material_topics.append(
                    EnvironmentTopicDraft(
                        text=topic_text,
                        origin="material",
                        claim_nature=edge.claim_nature,
                        claim_nature_known=edge.claim_nature is not None,
                        sources=_source_refs(
                            field_name="expressedTopics",
                            field_index=field_index,
                            target_kind="edge",
                            target_key=edge.key,
                            evidence=evidence[:1],
                            claim_nature=edge.claim_nature,
                        ),
                    )
                )

        involved_keys: set[tuple[str, str | None]] = set()
        for edge, evidence, other, other_key in incident:
            claim_nature = edge.claim_nature
            if claim_nature == "author_recommendation":
                # 报告建议只作为环境背景，不作为角色表达或关注话题。
                if background["stated"] is False and node.label in evidence[0].quote:
                    background = {"text": evidence[0].quote, "stated": True}
                text = other.label
                marker = (text, claim_nature)
                if marker not in seen_material_topics:
                    seen_material_topics.add(marker)
                    material_topics.append(
                        EnvironmentTopicDraft(
                            text=text,
                            origin="material",
                            claim_nature=claim_nature,
                            claim_nature_known=True,
                            sources=_source_refs(
                                field_name="involvedTopics",
                                field_index=0,
                                target_kind="edge",
                                target_key=edge.key,
                                evidence=evidence[:1],
                                claim_nature=claim_nature,
                            ),
                        )
                    )
                continue

            if background["stated"] is False and node.label in evidence[0].quote:
                background = {"text": evidence[0].quote, "stated": True}
                sources.extend(
                    _source_refs(
                        field_name="background",
                        field_index=0,
                        target_kind="edge",
                        target_key=edge.key,
                        evidence=evidence[:1],
                        claim_nature=claim_nature,
                    )
                )

            if edge.key in expressed_edges:
                continue
            topic_text = _topic_text(edge, other, other_key in candidates)
            marker = (topic_text, claim_nature)
            if not topic_text or marker in involved_keys or marker in expressed_keys:
                continue
            involved_keys.add(marker)
            field_index = len(involved)
            involved.append(
                {
                    "text": topic_text,
                    "claimNature": claim_nature,
                    "claimNatureKnown": claim_nature is not None,
                }
            )
            sources.extend(
                _source_refs(
                    field_name="involvedTopics",
                    field_index=field_index,
                    target_kind="edge",
                    target_key=edge.key,
                    evidence=evidence[:2],
                    claim_nature=claim_nature,
                )
            )
            if marker not in seen_material_topics:
                seen_material_topics.add(marker)
                material_topics.append(
                    EnvironmentTopicDraft(
                        text=topic_text,
                        origin="material",
                        claim_nature=claim_nature,
                        claim_nature_known=claim_nature is not None,
                        sources=_source_refs(
                            field_name="involvedTopics",
                            field_index=field_index,
                            target_kind="edge",
                            target_key=edge.key,
                            evidence=evidence[:1],
                            claim_nature=claim_nature,
                        ),
                    )
                )

        roles.append(
            RoleDraft(
                node_key=node_key,
                display_name=node.label,
                role_type=(
                    (decisions[node_key][1] or node.node_type)
                    if (node.node_type or "").strip() in GENERIC_TYPES
                    else node.node_type
                ),
                mention=mention,
                background=background,
                expressed_topics=tuple(expressed),
                involved_topics=tuple(involved),
                sources=tuple(sources),
            )
        )

    return RoleSelection(
        roles=tuple(roles),
        material_topics=tuple(material_topics),
        excluded=tuple(excluded),
    )


def material_topic_labels(selection: RoleSelection) -> Mapping[str, str]:
    """调试与报告用的稳定话题标签映射。"""
    return {
        f"{index}:{topic.text}": topic.origin
        for index, topic in enumerate(selection.material_topics, start=1)
    }
