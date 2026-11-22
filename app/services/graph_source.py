"""来源定位：把模型引文映射回材料原件中的 UTF-16 区间。

映射遵循入库坐标语义：TXT/Markdown 使用解码后完整原文的 UTF-16 偏移，PDF 使用
对应物理页原始抽取文本的 UTF-16 偏移。清洗文本只用于查找引文出现位置，最终区间
始终落在原文或物理页文本上；同一引文出现多次且无法由模型偏移消歧时明确失败。
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..engines.business_contracts import (
    ORIGIN_MATERIAL,
    SourceRef,
)
from . import document_extract, document_storage
from . import evidence_graph as graph_store

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)


class SourceMappingError(Exception):
    """来源无法可靠映射；`code` 是稳定原因码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SourceDocument:
    id: uuid.UUID
    kind: str
    storage_path: str
    text_encoding: str | None
    page_count: int | None


@dataclass(frozen=True)
class ResolvedSource:
    source: SourceRef
    document_id: uuid.UUID
    chunk_index: int
    page_no: int | None
    paragraph_start: int | None
    paragraph_end: int | None
    source_start: int
    source_end: int
    quote: str


@dataclass(frozen=True)
class _RawDocument:
    text: str
    pages: dict[int, str]


def _u16_len(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _char_width(ch: str) -> int:
    return 2 if ord(ch) > 0xFFFF else 1


def _u16_offsets(value: str) -> list[int]:
    offsets: list[int] = [0]
    total = 0
    for ch in value:
        total += _char_width(ch)
        offsets.append(total)
    return offsets


def _py_index_for_u16(offsets: Sequence[int], target: int) -> int:
    """返回 UTF-16 偏移对应的 Python 索引；偏移落在字符中间时向上取整。"""
    return bisect.bisect_left(offsets, target)


def _non_space(value: str) -> list[str]:
    return [ch for ch in value if not ch.isspace()]


def _same_visible(left: str, right: str) -> bool:
    return _non_space(left) == _non_space(right)


def _find_quote_occurrences(haystack: str, quote: str) -> list[int]:
    """返回引文在清洗文本中的全部起始位置；空白差异按可见字符回退匹配。"""
    positions: list[int] = []
    start = 0
    while True:
        index = haystack.find(quote, start)
        if index < 0:
            break
        positions.append(index)
        start = index + 1
    if positions:
        return positions
    # 模型可能把原文换行写成空格：按非空白字符序列定位，仍返回覆盖区间。
    visible = _non_space(quote)
    if not visible:
        return []
    cleaned = _non_space(haystack)
    for index in range(0, max(0, len(cleaned) - len(visible) + 1)):
        if cleaned[index : index + len(visible)] != visible:
            continue
        # 把可见字符下标回推为清洗文本下标。
        seen = 0
        for position, ch in enumerate(haystack):
            if not ch.isspace():
                if seen == index:
                    positions.append(position)
                    break
                seen += 1
        break
    return positions


def _map_cleaned_span(
    chunk_text: str,
    chunk_start: int,
    raw_text: str,
    cleaned_start: int,
    cleaned_end: int,
) -> tuple[int, int] | None:
    """把清洗文本区间映射到原始文本的 UTF-16 区间。

    只比较非空白字符，忽略清洗引入的换行/折叠空白差异；返回 `(start, end)`。
    """
    offsets = _u16_offsets(raw_text)
    raw_index = _py_index_for_u16(offsets, max(0, chunk_start))
    first: tuple[int, int] | None = None
    last: tuple[int, int] | None = None
    for position, ch in enumerate(chunk_text):
        if ch.isspace():
            continue
        while raw_index < len(raw_text) and raw_text[raw_index].isspace():
            raw_index += 1
        if raw_index >= len(raw_text):
            return None
        if raw_text[raw_index] != ch:
            return None
        char_start = offsets[raw_index]
        char_end = char_start + _char_width(ch)
        if position >= cleaned_start and position < cleaned_end:
            if first is None:
                first = (char_start, char_end)
            last = (char_start, char_end)
        raw_index += 1
    if first is None or last is None:
        return None
    if first[0] < chunk_start:
        return None
    return first[0], last[1]


def _slice_raw(raw_text: str, start: int, end: int) -> str:
    offsets = _u16_offsets(raw_text)
    left = _py_index_for_u16(offsets, start)
    right = _py_index_for_u16(offsets, end)
    return raw_text[left:right]


def resolve_source(
    source: SourceRef,
    *,
    chunk_text: str,
    chunk_start: int,
    raw_text: str,
) -> tuple[int, int]:
    """在原始文本中唯一确定引文区间；歧义时绝不取第一次匹配。"""
    occurrences = _find_quote_occurrences(chunk_text, source.quote)
    if not occurrences:
        raise SourceMappingError("source_mismatch", "引文不在分块文本中")
    mapped: list[tuple[int, int]] = []
    for start in occurrences:
        value = _map_cleaned_span(
            chunk_text, chunk_start, raw_text, start, start + len(source.quote)
        )
        if value is not None and value not in mapped:
            mapped.append(value)
    if not mapped:
        raise SourceMappingError("source_mismatch", "引文无法映射回原文位置")
    if len(mapped) > 1:
        # 模型偏移是原文/物理页坐标：只有唯一命中给定区间时才允许消歧。
        expected = [
            value
            for value in mapped
            if value[0] == source.source_start
            and value[1] == source.source_end
        ]
        if len(expected) == 1:
            mapped = expected
        else:
            raise SourceMappingError(
                "ambiguous_source", "引文在材料中多次出现"
            )
    start, end = mapped[0]
    raw_slice = _slice_raw(raw_text, start, end)
    if not _same_visible(raw_slice, source.quote):
        raise SourceMappingError("source_mismatch", "引文与原文区间不一致")
    return start, end


async def _load_documents(
    db: AsyncSession, *, build_id: uuid.UUID, project_id: uuid.UUID
) -> dict[uuid.UUID, SourceDocument]:
    result = await db.execute(
        text(
            "SELECT d.id, d.kind, d.storage_path, d.text_encoding, d.page_count "
            "FROM graph_build_documents b JOIN graph_builds g ON g.id = b.build_id "
            "JOIN documents d ON d.id = b.document_id "
            "WHERE b.build_id = :build_id AND g.project_id = :project_id "
            "AND d.status = 'ready'"
        ),
        {"build_id": build_id, "project_id": project_id},
    )
    return {
        row.id: SourceDocument(
            id=row.id,
            kind=row.kind,
            storage_path=row.storage_path,
            text_encoding=row.text_encoding,
            page_count=None if row.page_count is None else int(row.page_count),
        )
        for row in result.fetchall()
    }


def _decode_document(document: SourceDocument, data: bytes) -> _RawDocument:
    if document.kind == "pdf":
        pages_needed = document.page_count or 1
        pdf = document_extract.parse_pdf(data, max_pages=max(1, pages_needed))
        return _RawDocument(
            text="",
            pages={page.page_no: page.text for page in pdf.pages},
        )
    encoding = document.text_encoding or "utf-8"
    try:
        value = data.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise SourceMappingError(
            "source_unavailable", "原件无法按解析编码解码"
        ) from exc
    return _RawDocument(text=value, pages={})


async def resolve_sources(
    db: AsyncSession,
    *,
    build_id: uuid.UUID,
    project_id: uuid.UUID,
    chunks: Sequence[graph_store.BuildChunk],
    sources: Sequence[SourceRef],
    storage_root: object,
    raw_cache: dict[uuid.UUID, _RawDocument] | None = None,
) -> dict[tuple[uuid.UUID, int, int, int], ResolvedSource]:
    """按 `(document, chunk, sourceStart, sourceEnd)` 缓存并校验全部候选来源。"""
    documents = await _load_documents(
        db, build_id=build_id, project_id=project_id
    )
    chunk_index = {
        (chunk.document_id, chunk.chunk_index): chunk for chunk in chunks
    }
    cache: dict[tuple[uuid.UUID, int, int, int], ResolvedSource] = {}
    raw_cache = {} if raw_cache is None else raw_cache
    for source in sources:
        try:
            document_id = uuid.UUID(str(source.document_id))
        except (ValueError, TypeError, AttributeError):
            raise SourceMappingError("source_mismatch", "来源材料标识非法") from None
        key = (
            document_id,
            int(source.chunk_index),
            int(source.source_start),
            int(source.source_end),
        )
        if key in cache:
            continue
        chunk = chunk_index.get(key[:2])
        if chunk is None:
            raise SourceMappingError("source_mismatch", "来源分块不属于本次材料")
        document = documents.get(document_id)
        if document is None:
            raise SourceMappingError(
                "source_unavailable", "来源材料原件不可读取"
            )
        if document_id not in raw_cache:
            try:
                data = await asyncio.to_thread(
                    document_storage.read_bytes,
                    storage_root,
                    document.storage_path,
                )
            except (OSError, ValueError):
                raise SourceMappingError(
                    "source_unavailable", "来源材料原件不可读取"
                ) from None
            try:
                raw_cache[document_id] = await asyncio.to_thread(
                    _decode_document, document, data
                )
            except SourceMappingError:
                raise
            except Exception as exc:  # noqa: BLE001 - 解码/抽取失败统一不可用
                logger.warning(
                    "来源原件解析失败 document=%s: %s",
                    document_id,
                    type(exc).__name__,
                )
                raise SourceMappingError(
                    "source_unavailable", "来源材料原件不可读取"
                ) from exc
        raw = raw_cache[document_id]
        if document.kind == "pdf":
            if chunk.page_no is None:
                raise SourceMappingError("source_mismatch", "PDF 来源缺少页码")
            raw_text = raw.pages.get(int(chunk.page_no))
            if raw_text is None:
                raise SourceMappingError("source_mismatch", "PDF 物理页不存在")
        else:
            raw_text = raw.text
        start, end = resolve_source(
            source,
            chunk_text=chunk.text,
            chunk_start=chunk.source_start,
            raw_text=raw_text,
        )
        cache[key] = ResolvedSource(
            source=source,
            document_id=document_id,
            chunk_index=chunk.chunk_index,
            page_no=chunk.page_no,
            paragraph_start=chunk.paragraph_start,
            paragraph_end=chunk.paragraph_end,
            source_start=start,
            source_end=end,
            quote=source.quote,
        )
    return cache


def material_sources(
    nodes: Sequence[object], edges: Sequence[object]
) -> list[SourceRef]:
    """收集材料节点的全部候选来源；假设节点允许无来源。"""
    collected: list[SourceRef] = []
    for item in list(nodes) + list(edges):
        if getattr(item, "origin", "") != ORIGIN_MATERIAL:
            continue
        collected.extend(getattr(item, "sources", ()) or ())
    return collected
