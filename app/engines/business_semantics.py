"""业务证据图谱的语义层：主体别名、来源性质、跨批次归一与代表性来源。

这里只做确定性处理，不调用模型或图谱服务。批次抽取结果仍按批次写入 episode；
清单保存归一后的主体、关系、贡献批次与代表性来源，供读回与快照发布使用。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Sequence

from .business_contracts import (
    CLAIM_NATURES,
    MAX_ALIASES_PER_SUBJECT,
    ORIGIN_MATERIAL,
    GraphContractError,
    GraphEdge,
    GraphNode,
    GraphResult,
    SourceRef,
    Subject,
    validate_semantic_graph,
)

PLACEHOLDER_ENTITY_NAMES = frozenset(
    {
        "实体",
        "细节",
        "关系",
        "关联",
        "entity",
        "detail",
        "relationship",
        "related",
        "association",
        "link",
    }
)
GENERIC_RELATION_NAMES = frozenset(
    {
        "实体",
        "细节",
        "关系",
        "关联",
        "相关",
        "联系",
        "提及",
        "entity",
        "detail",
        "relationship",
        "related",
        "association",
        "link",
        "mention",
    }
)

_ATTRIBUTION_PATTERNS = (
    re.compile(r"声称|宣称|据报道|据[^。；;\n]{0,20}(?:报道|消息|了解)|获悉"),
    re.compile(r"[\u4e00-\u9fa5A-Za-z0-9“”‘’\-]{1,16}(?<!名)称"),
)


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def is_placeholder_entity(name: str) -> bool:
    return normalized_name(name) in PLACEHOLDER_ENTITY_NAMES


def is_generic_relation(name: str) -> bool:
    value = normalized_name(name)
    if value in GENERIC_RELATION_NAMES:
        return True
    return any(
        token in value
        for token in ("related", "relation", "association", "mention")
    )


def looks_attributed(quote: str) -> bool:
    return any(pattern.search(quote) for pattern in _ATTRIBUTION_PATTERNS)


def validate_claim_nature(claim_nature: str, quote: str) -> None:
    if claim_nature not in CLAIM_NATURES:
        raise GraphContractError(
            "invalid_model_output", "材料关系来源性质不合法"
        )
    if claim_nature == "official_decision" and looks_attributed(quote):
        raise GraphContractError(
            "invalid_model_output",
            "归属或转述表述不能标为校方决定",
        )


def _source_key(source: SourceRef) -> tuple[str, int, int, int]:
    return (
        source.document_id,
        source.chunk_index,
        source.source_start,
        source.source_end,
    )


def representative_sources(
    sources: Sequence[SourceRef], *, limit: int = 5
) -> tuple[SourceRef, ...]:
    """保留有代表性的来源：先按页码/段落分散，再按出现顺序补齐；不丢身份。"""
    unique: list[SourceRef] = []
    seen: set[tuple[str, int, int, int]] = set()
    for source in sources:
        key = _source_key(source)
        if key in seen:
            continue
        seen.add(key)
        unique.append(source)
    if len(unique) <= limit:
        return tuple(unique)

    picked: list[SourceRef] = []
    picked_keys: set[tuple[str, int, int, int]] = set()
    seen_locations: set[tuple[int | None, int | None]] = set()
    for source in unique:
        location = (
            source.page_no,
            source.paragraph_start if source.page_no is None else None,
        )
        if location in seen_locations:
            continue
        seen_locations.add(location)
        picked.append(source)
        picked_keys.add(_source_key(source))
        if len(picked) == limit:
            return tuple(picked)
    for source in unique:
        key = _source_key(source)
        if key in picked_keys:
            continue
        picked.append(source)
        if len(picked) == limit:
            break
    return tuple(picked)


def build_subject_index(
    subjects: Sequence[Subject],
) -> tuple[dict[str, Subject], dict[str, set[str]]]:
    """返回主体键索引与无歧义名称索引；歧义别名不参与自动解析。"""
    by_key = {subject.key: subject for subject in subjects}
    canonical: dict[str, set[str]] = {}
    aliases: dict[str, set[str]] = {}
    for subject in subjects:
        canonical.setdefault(normalized_name(subject.label), set()).add(subject.key)
        for alias in subject.aliases:
            aliases.setdefault(normalized_name(alias), set()).add(subject.key)
    forms: dict[str, set[str]] = {}
    for form, keys in canonical.items():
        if len(keys) == 1:
            forms[form] = set(keys)
    for form, keys in aliases.items():
        if form in forms:
            forms[form] = forms[form] | keys
            continue
        if len(keys) == 1:
            forms[form] = set(keys)
    forms = {form: keys for form, keys in forms.items() if len(keys) == 1}
    return by_key, forms


def resolve_subject(
    subjects: Sequence[Subject],
    *,
    label: str,
    subject_key: str | None = None,
) -> Subject | None:
    by_key, forms = build_subject_index(subjects)
    if subject_key:
        subject = by_key.get(subject_key)
        if subject is None:
            raise GraphContractError(
                "invalid_model_output", "节点引用未知主体", subject_key
            )
        # 有稳定主体键时以键为准；名称只是模型看到的表面形式。
        return subject
    keys = forms.get(normalized_name(label), set())
    if len(keys) > 1:
        raise GraphContractError(
            "invalid_model_output", "主体名称存在歧义"
        )
    if not keys:
        return None
    return by_key[next(iter(keys))]


@dataclass
class _NodeAggregate:
    key: str
    label: str
    type_key: str
    type_name: str
    origin: str
    subject_key: str
    aliases: tuple[str, ...]
    sources: list[SourceRef] = field(default_factory=list)
    batch_keys: set[str] = field(default_factory=set)


@dataclass
class _EdgeAggregate:
    key: str
    label: str
    type_key: str
    type_name: str
    origin: str
    claim_nature: str
    source_key: str
    target_key: str
    sources: list[SourceRef] = field(default_factory=list)
    batch_keys: set[str] = field(default_factory=set)


def _matter_key(node: GraphNode) -> str:
    source_material = "|".join(
        sorted(
            f"{item.document_id}:{item.chunk_index}:{item.source_start}:{item.source_end}"
            for item in node.sources
        )
    )
    raw = (
        f"{node.node_type}|{normalized_name(node.label)}|"
        f"{node.origin}|{source_material}"
    )
    return "matter:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _edge_key(
    source_key: str,
    target_key: str,
    type_key: str,
    label: str,
    claim_nature: str,
) -> str:
    nature_group = (
        "official" if claim_nature == "official_decision" else "non_official"
    )
    raw = "|".join(
        (source_key, target_key, type_key, normalized_name(label), nature_group)
    )
    return "edge:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


_NATURE_PRIORITY = {
    "other_material": 1,
    "media_report": 2,
    "party_claim": 3,
    "official_decision": 4,
}


def _preferred_nature(left: str, right: str) -> str:
    if left == "official_decision" or right == "official_decision":
        if left == right:
            return left
        # 正式决定与非正式决定不共享同一关系键；到这里说明身份异常。
        return "official_decision"
    if _NATURE_PRIORITY.get(right, 0) > _NATURE_PRIORITY.get(left, 0):
        return right
    return left


def _merge_sources(
    existing: Sequence[SourceRef], incoming: Sequence[SourceRef]
) -> list[SourceRef]:
    return list(representative_sources([*existing, *incoming]))


_REVERSAL_MARKERS = ("撤销", "撤回", "取消", "纠正", "废止", "不予", "建议", "呼吁")


def _mentions(label: str, aliases: Sequence[str], text: str) -> bool:
    forms = [label, *aliases]
    compact_text = re.sub(
        r"[\s\W_]+", "", unicodedata.normalize("NFKC", text)
    ).casefold()
    for form in forms:
        if not form:
            continue
        normalized = normalized_name(form)
        compact = re.sub(
            r"[\s\W_]+", "", unicodedata.normalize("NFKC", form)
        ).casefold()
        if normalized and normalized in text:
            return True
        if compact and compact in compact_text:
            return True
        if len(normalized) >= 4 and normalized[:2] in text and normalized[-2:] in text:
            return True
    return False


def _quote_supports_edge(
    *,
    label: str,
    claim_nature: str,
    source_label: str,
    source_aliases: Sequence[str],
    target_label: str,
    target_aliases: Sequence[str],
    quote: str,
) -> bool:
    text = normalized_name(quote)
    action = normalized_name(label)
    if not text or not action:
        return False
    action_index = text.find(action)
    if action_index < 0:
        action_index = text.find(action[:2])
    reversal_seen = any(marker in text for marker in _REVERSAL_MARKERS)
    for marker in _REVERSAL_MARKERS:
        marker_index = text.find(marker)
        if marker_index >= 0 and action_index >= 0 and marker_index < action_index:
            return False
    # 任何材料关系都必须能读出主体、动作与对象；建议/撤销不能挂到已发生动作下。
    if reversal_seen and any(
        word in normalized_name(label)
        for word in ("处分", "决定", "批准", "发布", "判决", "处理")
    ):
        return False
    if not _mentions(source_label, source_aliases, text):
        return False
    if not _mentions(target_label, target_aliases, text):
        return False
    if action_index < 0:
        return False
    return True


def consolidate(
    *,
    subjects: Sequence[Subject],
    entity_types: Sequence[object],
    extracted_batches: Sequence[object],
    max_sources_per_item: int = 5,
) -> GraphResult:
    """跨批次归一：主体按主体键合并，事项按类型/名称/来源合并。"""
    type_name_by_key = {
        str(getattr(item, "key", "")): str(getattr(item, "name", ""))
        for item in entity_types
    }
    subject_by_key, _ = build_subject_index(subjects)
    node_aggregates: dict[str, _NodeAggregate] = {}
    edge_aggregates: dict[str, _EdgeAggregate] = {}
    batch_node_keys: dict[tuple[str, str], str] = {}

    for extracted in extracted_batches:
        batch = getattr(extracted, "batch")
        batch_key = str(getattr(batch, "key"))
        for node in getattr(extracted, "nodes"):
            subject = None
            if node.subject_key:
                subject = subject_by_key.get(node.subject_key)
                if subject is None:
                    raise GraphContractError(
                        "invalid_model_output", "节点引用未知主体", node.key
                    )
            else:
                subject = resolve_subject(
                    subjects, label=node.label, subject_key=None
                )
            if subject is not None:
                canonical_key = f"subject:{subject.key}"
                label = subject.label
                type_key = subject.type_key
                aliases = subject.aliases
            else:
                canonical_key = _matter_key(node)
                label = node.label
                type_key = node.node_type
                aliases = ()

            existing = node_aggregates.get(canonical_key)
            if existing is None:
                node_aggregates[canonical_key] = _NodeAggregate(
                    key=canonical_key,
                    label=label,
                    type_key=type_key,
                    type_name=(
                        node.type_name
                        or type_name_by_key.get(type_key, type_key)
                    ),
                    origin=node.origin,
                    subject_key=subject.key if subject else "",
                    aliases=aliases,
                    sources=list(node.sources),
                    batch_keys={batch_key},
                )
            else:
                if (
                    existing.label != label
                    or existing.type_key != type_key
                    or existing.origin != node.origin
                ):
                    raise GraphContractError(
                        "invalid_model_output", "主体或事项身份冲突", canonical_key
                    )
                existing.sources = _merge_sources(existing.sources, node.sources)
                existing.batch_keys.add(batch_key)
            batch_node_keys[(batch_key, node.key)] = canonical_key

        for edge in getattr(extracted, "edges"):
            source_key = batch_node_keys.get((batch_key, edge.source_key))
            target_key = batch_node_keys.get((batch_key, edge.target_key))
            if source_key is None or target_key is None:
                # 端点节点因来源无法定位被排除时，关系也不能发布；保留其它有效关系。
                continue
            claim_nature = edge.claim_nature or ""
            if edge.origin == ORIGIN_MATERIAL:
                for source in edge.sources:
                    validate_claim_nature(claim_nature, source.quote)
            source_node = node_aggregates.get(source_key)
            target_node = node_aggregates.get(target_key)
            if source_node is None or target_node is None:
                continue
            if edge.origin == ORIGIN_MATERIAL and not (
                source_node.subject_key or target_node.subject_key
            ):
                continue
            filtered_sources = [
                source
                for source in edge.sources
                if _quote_supports_edge(
                    label=edge.label,
                    claim_nature=claim_nature,
                    source_label=source_node.label,
                    source_aliases=source_node.aliases,
                    target_label=target_node.label,
                    target_aliases=target_node.aliases,
                    quote=source.quote,
                )
            ]
            if edge.origin == ORIGIN_MATERIAL and not filtered_sources:
                continue
            canonical_key = _edge_key(
                source_key,
                target_key,
                edge.edge_type,
                edge.label,
                claim_nature,
            )
            existing_edge = edge_aggregates.get(canonical_key)
            if existing_edge is None:
                edge_aggregates[canonical_key] = _EdgeAggregate(
                    key=canonical_key,
                    label=edge.label,
                    type_key=edge.edge_type,
                    type_name=(
                        edge.edge_type_name
                        or type_name_by_key.get(edge.edge_type, edge.edge_type)
                    ),
                    origin=edge.origin,
                    claim_nature=claim_nature,
                    source_key=source_key,
                    target_key=target_key,
                    sources=filtered_sources,
                    batch_keys={batch_key},
                )
            else:
                if (
                    existing_edge.label != edge.label
                    or existing_edge.type_key != edge.edge_type
                    or existing_edge.origin != edge.origin
                ):
                    raise GraphContractError(
                        "invalid_model_output", "关系身份冲突", canonical_key
                    )
                existing_edge.sources = _merge_sources(
                    existing_edge.sources, filtered_sources
                )
                existing_edge.claim_nature = _preferred_nature(
                    existing_edge.claim_nature, claim_nature
                )
                existing_edge.batch_keys.add(batch_key)

    media_types = {"媒体机构", "媒体", "传播平台", "社交平台"}
    party_actions = ("指控", "回应", "维权", "主张", "质疑", "声援", "反对")
    for edge in edge_aggregates.values():
        if (
            edge.origin != ORIGIN_MATERIAL
            or edge.claim_nature not in {"media_report", "other_material"}
        ):
            continue
        source_node = node_aggregates.get(edge.source_key)
        if source_node is None or source_node.type_name in media_types:
            continue
        if any(action in edge.label for action in party_actions):
            edge.claim_nature = "party_claim"

    material_edge_nodes: set[str] = set()
    for edge in edge_aggregates.values():
        if edge.origin == ORIGIN_MATERIAL:
            material_edge_nodes.add(edge.source_key)
            material_edge_nodes.add(edge.target_key)

    nodes: list[GraphNode] = []
    for node in node_aggregates.values():
        if node.origin == ORIGIN_MATERIAL and node.key not in material_edge_nodes:
            continue
        identity_sources = [
            source
            for source in node.sources
            if not any(
                marker in normalized_name(source.quote)
                for marker in _REVERSAL_MARKERS
            )
        ]
        nodes.append(
            GraphNode(
                key=node.key,
                node_type=node.type_key,
                type_name=node.type_name,
                label=node.label,
                origin=node.origin,
                summary="",
                sources=representative_sources(
                    identity_sources or node.sources,
                    limit=max_sources_per_item,
                ),
                subject_key=node.subject_key,
                aliases=node.aliases,
                batch_keys=tuple(sorted(node.batch_keys)),
            )
        )
    edges = [
        GraphEdge(
            key=edge.key,
            edge_type=edge.type_key,
            edge_type_name=edge.type_name,
            label=edge.label,
            origin=edge.origin,
            source_key=edge.source_key,
            target_key=edge.target_key,
            summary="",
            claim_nature=edge.claim_nature,
            sources=representative_sources(
                edge.sources, limit=max_sources_per_item
            ),
            batch_keys=tuple(sorted(edge.batch_keys)),
        )
        for edge in edge_aggregates.values()
    ]
    result = GraphResult(
        entity_types=tuple(entity_types),  # type: ignore[arg-type]
        subjects=tuple(subjects),
        nodes=tuple(sorted(nodes, key=lambda item: item.key)),
        edges=tuple(sorted(edges, key=lambda item: item.key)),
    )
    validate_semantic_graph(
        result, max_sources_per_item=max_sources_per_item
    )
    return result


# ---------------------------------------------------------------- 读回事实定位

_SENTENCE_BREAKS = frozenset("。！？；!?;\n")
_ACTION_TERMS = (
    "报道", "发布", "决定", "处分", "判决", "裁定", "回应", "指控",
    "起诉", "维权", "质疑", "检测", "传播", "执行", "通报", "处理",
    "开除", "免职", "批准", "撤销", "撤回", "道歉", "支持", "反对",
    "参与", "涉及", "发生于", "影响", "投诉", "解释", "澄清", "调查",
    "录取", "举报", "暴露", "讨论", "建立", "抱怨", "形成", "引发",
    "获得", "面临", "上涨", "发表", "削弱", "动摇", "追求", "分析",
    "计划", "创作", "承载", "承认", "否认", "要求", "建议", "呼吁",
    "整改", "回应", "辟谣", "删除", "封禁", "转发", "评论", "阅读",
    "监测", "通报", "起诉", "判决", "赔偿", "罚款", "停职", "降级",
    "标签", "辩护", "追责", "泛化", "改编", "预测", "降低", "列入",
    "引用", "贴", "指向", "构成", "导致", "成为", "提出", "发布",
)
_SUGGESTION_MARKERS = ("建议", "呼吁", "应当", "应该", "提请", "要求", "希望")

_EN_RELATION_ACTIONS = (
    ("HAS_ACADEMIC_STRENGTH_IN", "学术优势"),
    ("HAS_HIGHEST_PROPORTION_OF", "占比最高"),
    ("HAS_POSITIVE_HOTSPOT", "正面热点"),
    ("HAS_PUBLIC_OPINION_EXPOSURE", "舆情曝光"),
    ("HAS_SECONDARY_REBOUND_PROBABILITY", "二次反弹可能"),
    ("SHOULD_TRANSFORM_INTO", "应转型为"),
    ("SHOULD_TRANSFORM", "应转型"),
    ("IS_ANGRIER_ABOUT_THAN", "更愤怒"),
    ("ASSOCIATES_WITH", "关联"),
    ("BELONGS_TO", "属于"),
    ("COMPARED_WITH", "对比"),
    ("CONTRASTS_WITH", "对比"),
    ("DIAGNOSED_WITH", "被诊断为"),
    ("EXPERIENCED", "经历"),
    ("FACILITATES", "促进"),
    ("INVOLVED_IN", "参与"),
    ("APPLIED_FOR", "申请"),
    ("DISTRUSTS", "不信任"),
    ("PRODUCES", "产生"),
    ("RECORDS", "记录"),
    ("REQUIRES", "需要"),
    ("REVEALS", "揭示"),
    ("VIOLATES", "违反"),
    ("INCLUDES", "包含"),
    ("LACKS", "缺乏"),
    ("WARNS", "警告"),
    ("USES", "使用"),
    ("OWNS", "拥有"),
    ("HAS_RISK", "存在风险"),
    ("HAS", "拥有"),
    ("HAS_BUDGET_FOR", "预算投入"),
    ("HAS_BRAND_IMAGE", "品牌形象"),
    ("HAS_RISK_OF", "存在风险"),
    ("HAS_CULTURAL_SYMBOL", "文化符号"),
    ("HAS_WEAKNESS_IN", "存在短板"),
    ("HAS_NEGATIVE_ISSUE", "存在负面议题"),
    ("TRIGGERED_DISSATISFACTION_OF", "引发不满"),
    ("HOSTS_DEEP_ANALYSIS_OF", "深度分析"),
    ("PUBLISHED_ON", "发布于"),
    ("RAISED_TUITION", "上涨费用"),
    ("OCCURS_AT", "发生于"),
    ("PARTICIPATED_IN", "参与"),
    ("INVOLVES", "涉及"),
    ("RELATES_TO", "涉及"),
    ("LABELED_AS", "标签"),
    ("IS_ALUMNUS_OF", "校友"),
    ("PREDICTS_FOR", "预测"),
    ("ASSOCIATED_WITH", "关联"),
    ("LISTED_IN", "列入"),
    ("UNDERMINE", "削弱"),
    ("FACE", "面临"),
    ("HOST", "承载"),
    ("AUTHOR", "撰写"),
    ("CREATE", "创作"),
    ("PLAN", "计划"),
    ("PURSUE", "追求"),
    ("ANALYZ", "分析"),
    ("TRIGGER", "引发"),
    ("ADMIT", "录取"),
    ("RELAT", "涉及"),
    ("CONCERN", "关切"),
    ("REPRESENT", "代表"),
    ("PRIORIT", "优先考虑"),
    ("DAMAGE", "损害"),
    ("PUBLISH", "发布"),
    ("ISSUE", "作出"),
    ("APPEAL", "起诉"),
    ("ACCUSE", "指控"),
    ("ORGANIZ", "组织"),
    ("SPREAD", "传播"),
    ("DETECT", "检测"),
    ("CRITICIZ", "质疑"),
    ("IMPROVE", "改善"),
    ("SUPPORT", "支持"),
    ("OPPOSE", "反对"),
    ("PARTICIPAT", "参与"),
    ("AFFECT", "影响"),
    ("INCREAS", "增加"),
    ("REDUC", "减少"),
    ("DECREAS", "减少"),
    ("CAUSE", "导致"),
    ("RESULT", "导致"),
    ("PROVID", "提供"),
    ("REPORT", "报道"),
    ("RESPOND", "回应"),
    ("DECID", "决定"),
    ("PUNISH", "处分"),
    ("JUDG", "判决"),
    ("SUE", "起诉"),
    ("COMPLAIN", "投诉"),
    ("PROTEST", "抗议"),
    ("MONITOR", "监测"),
    ("REVIEW", "复盘"),
    ("CONDEMN", "谴责"),
    ("DENOUNCE", "谴责"),
    ("CONFRONT", "面对"),
)


def infer_relation_label(raw: str, fact: str = "") -> str:
    """为图谱服务返回的英文关系名提供中文动作显示。"""
    value = raw.strip()
    if not value:
        return "关联"
    upper = value.upper()
    for token, label in _EN_RELATION_ACTIONS:
        if token in upper:
            return label
    combined = normalized_name(fact)
    for term in _ACTION_TERMS:
        if term in combined:
            return term
    return value
_DECISION_MARKERS = ("决定", "作出", "已", "正式", "通报", "认定", "裁定")


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """按中英文句末与换行切句，返回 `(start, end, sentence)`。"""
    spans: list[tuple[int, int, str]] = []
    start = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_BREAKS:
            end = index + 1
            value = text[start:end]
            if value.strip():
                spans.append((start, end, value))
            start = end
    if start < len(text) and text[start:].strip():
        spans.append((start, len(text), text[start:]))
    return spans


def _char_bigrams(value: str) -> set[str]:
    compact = re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", value)).casefold()
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _bigram_overlap(left: str, right: str) -> float:
    left_set = _char_bigrams(left)
    right_set = _char_bigrams(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _action_terms(action: str, fact: str) -> tuple[str, ...]:
    combined = normalized_name(action) + normalized_name(fact)
    return tuple(term for term in _ACTION_TERMS if term in combined)


def is_suggestion_reversal(quote: str, action: str, fact: str) -> bool:
    """建议/呼吁撤销不能证明已经撤销；仅在缺少已发生表达时判为建议。"""
    text = normalized_name(quote)
    combined = normalized_name(action) + normalized_name(fact)
    if not any(term in combined for term in ("撤销", "撤回", "纠正", "处分", "决定", "判决", "裁定")):
        return False
    marker_positions = [
        text.find(marker) for marker in _SUGGESTION_MARKERS if text.find(marker) >= 0
    ]
    if not marker_positions:
        return False
    first_marker = min(marker_positions)
    decision_positions = [
        text.find(marker) for marker in _DECISION_MARKERS if text.find(marker) >= 0
    ]
    if not decision_positions:
        return True
    return min(decision_positions) > first_marker


def classify_claim_nature(
    *,
    action: str,
    fact: str,
    quote: str,
    source_label: str,
) -> str:
    """按支持句、动作与起点保守地给出来源性质。"""
    text = normalized_name(quote)
    source = normalized_name(source_label)
    combined_action = normalized_name(action) + normalized_name(fact)
    attributed = looks_attributed(quote)
    media_source = any(
        token in source
        for token in (
            "媒体",
            "新闻",
            "报",
            "网",
            "平台",
            "微博",
            "知乎",
            "抖音",
            "小红书",
            "b站",
            "哔哩",
            "tv",
        )
    )
    report_action = any(
        token in combined_action for token in ("报道", "披露", "获悉", "转发", "传播")
    )
    party_action = any(
        token in combined_action
        for token in ("指控", "回应", "维权", "主张", "质疑", "声援", "反对", "投诉", "道歉", "称")
    )
    official_action = any(
        token in combined_action
        for token in ("决定", "处分", "判决", "裁定", "批准", "通报", "处理", "开除", "免职", "撤销")
    )
    if is_suggestion_reversal(quote, action, fact):
        return "party_claim" if attributed else "other_material"
    if attributed:
        return "media_report" if media_source or report_action else "party_claim"
    if media_source and report_action:
        return "media_report"
    if party_action:
        return "party_claim"
    if official_action:
        return "official_decision"
    return "other_material"


def select_mention_quote(
    text: str, *, label: str, aliases: Sequence[str] = ()
) -> tuple[int, int, str] | None:
    """选择能定位节点名称的句子，供节点身份来源使用。"""
    for start, end, sentence in split_sentences(text):
        if _mentions(label, aliases, normalized_name(sentence)):
            return start, end, sentence.strip()
    return None


def select_support_quote(
    text: str,
    *,
    fact: str,
    action: str,
    source_label: str,
    target_label: str,
    source_aliases: Sequence[str] = (),
    target_aliases: Sequence[str] = (),
) -> tuple[int, int, str] | None:
    """在读回事实对应的原文分块中定位同时支持主体、动作与对象的原句。

    允许使用相邻句补齐主体或对象；媒体名单、实体名单等没有动作的句子不会通过。
    """
    sentences = split_sentences(text)
    if not sentences:
        return None
    action_terms = _action_terms(action, fact)
    best: tuple[float, int, int, str] | None = None
    for index in range(len(sentences)):
        candidates = [
            (sentences[index][0], sentences[index][1], sentences[index][2]),
        ]
        if index + 1 < len(sentences):
            candidates.append(
                (
                    sentences[index][0],
                    sentences[index + 1][1],
                    text[sentences[index][0] : sentences[index + 1][1]],
                )
            )
        if index > 0:
            candidates.append(
                (
                    sentences[index - 1][0],
                    sentences[index][1],
                    text[sentences[index - 1][0] : sentences[index][1]],
                )
            )
        if index > 0 and index + 1 < len(sentences):
            candidates.append(
                (
                    sentences[index - 1][0],
                    sentences[index + 1][1],
                    text[sentences[index - 1][0] : sentences[index + 1][1]],
                )
            )
        for start, end, quote in candidates:
            value = normalized_name(quote)
            if not value:
                continue
            source_ok = _mentions(
                source_label, source_aliases, value
            )
            target_ok = _mentions(
                target_label, target_aliases, value
            )
            if not source_ok or not target_ok:
                continue
            action_ok = (
                any(term in value for term in action_terms)
                if action_terms
                else False
            )
            if not action_ok:
                action_ok = _bigram_overlap(fact, quote) >= 0.2
            if not action_ok:
                continue
            if is_suggestion_reversal(quote, action, fact):
                continue
            score = _bigram_overlap(fact, quote) * 100
            score += 20 if action_ok else 0
            if len(quote.strip()) <= 180:
                score += 2
            if best is None or score > best[0]:
                best = (score, start, end, quote.strip())
    if best is None:
        return None
    return best[1], best[2], best[3]
