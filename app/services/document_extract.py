"""材料读取、清洗与可定位分块（纯函数，不访问数据库与网络）。

位置约定：所有 `source_start`/`source_end` 都是 **UTF-16 代码单元** 偏移，区间
左闭右开，与浏览器 `String.prototype.slice` 的计数一致；PDF 偏移基于该页抽取
文本，文本偏移基于解码后的原文（换行归一与空白清理之前）。

正文一律按普通文本处理：不执行、不解释、不按照材料内容改变解析行为。
"""

from __future__ import annotations

import io
import math
import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------- 异常


class DecodeFailed(Exception):
    """所有候选编码都无法可靠解码。"""

    def __init__(self, attempted: tuple[str, ...]) -> None:
        super().__init__("无法可靠解码")
        self.attempted = attempted


class NoTextLayer(Exception):
    """整份 PDF 没有可提取文字。"""


class TooManyPages(Exception):
    """PDF 页数超过上限。"""

    def __init__(self, pages: int, limit: int) -> None:
        super().__init__("页数过多")
        self.pages = pages
        self.limit = limit


class ParseError(Exception):
    """其他提取失败。"""


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class Paragraph:
    index: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class TextDocument:
    text: str
    encoding: str
    paragraphs: tuple[Paragraph, ...]


@dataclass(frozen=True)
class PageText:
    page_no: int
    text: str


@dataclass(frozen=True)
class PdfDocument:
    pages: tuple[PageText, ...]


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    page_no: int | None
    paragraph_start: int | None
    paragraph_end: int | None
    source_start: int
    source_end: int


# ---------------------------------------------------------------- 基础工具

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SENTENCE_END = "。！？；…"
CLOSERS = "”’」』）)]】"
PRINT_PATH_RE = re.compile(
    r"(file:///|[A-Za-z]:\\|\\\\|/Users/|/home/|\bpage\s+\d+\s+of\s+\d+\b)",
    re.IGNORECASE,
)
PAGE_NUMBER_RE = re.compile(
    r"^(第\s*\d+\s*页(\s*[/／共]\s*\d+\s*页)?|[-–—\s]*\d+[-–—\s]*|\d+\s*/\s*\d+)$"
)


def _u16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _char_width(ch: str) -> int:
    return 2 if ord(ch) > 0xFFFF else 1


def _looks_like_text(text: str) -> bool:
    """判断解码结果像正常文本：无替换字符、控制字符极少、几乎全部可打印。"""
    if "\ufffd" in text:
        return False
    visible = [ch for ch in text if not ch.isspace()]
    if not visible:
        return True
    controls = sum(1 for ch in visible if unicodedata.category(ch) == "Cc")
    if controls / len(visible) > 0.01:
        return False
    printable = sum(1 for ch in visible if ch.isprintable())
    return printable / len(visible) >= 0.9


def decode_text(data: bytes) -> tuple[str, str]:
    """按 UTF-8（含 BOM）→ UTF-16（需 BOM）→ GB18030 的顺序解码。"""
    attempted: list[str] = ["utf-8", "utf-16", "gb18030"]
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = data.decode("utf-16")
            if _looks_like_text(text):
                return text, "utf-16"
        except UnicodeDecodeError:
            pass
    try:
        text = data.decode("gb18030")
    except UnicodeDecodeError:
        raise DecodeFailed(tuple(attempted)) from None
    if _looks_like_text(text):
        return text, "gb18030"
    raise DecodeFailed(tuple(attempted))


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """按空行切分段落，返回解码原文中的字符区间。"""
    spans: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end = 0
    position = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if content.strip():
            if current_start is None:
                current_start = position
            current_end = position + len(content)
        elif current_start is not None:
            spans.append((current_start, current_end))
            current_start = None
        position += len(line)
    if current_start is not None:
        spans.append((current_start, current_end))
    if not spans and text.strip():
        spans.append((0, len(text)))
    return spans


def parse_text(data: bytes) -> TextDocument:
    text, encoding = decode_text(data)
    paragraphs: list[Paragraph] = []
    previous_end = 0
    offset = 0
    for index, (start, end) in enumerate(_paragraph_spans(text), start=1):
        offset += _u16_len(text[previous_end:start])
        paragraph_start = offset
        paragraph_end = paragraph_start + _u16_len(text[start:end])
        paragraphs.append(
            Paragraph(
                index=index,
                start=paragraph_start,
                end=paragraph_end,
                text=text[start:end],
            )
        )
        previous_end = end
        offset = paragraph_end
    return TextDocument(text=text, encoding=encoding, paragraphs=tuple(paragraphs))


def parse_pdf(data: bytes, *, max_pages: int) -> PdfDocument:
    """逐页读取 PDF，物理页码从 1 开始；空白页保留页码与空文本。"""
    from pypdf import PdfReader  # 延迟导入，便于无 PDF 场景的纯函数测试

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 - 加密且无法空密码解密
                raise ParseError("加密 PDF 无法读取") from None
        total = len(reader.pages)
        if total > max_pages:
            raise TooManyPages(total, max_pages)
        pages: list[PageText] = []
        for index in range(total):
            try:
                text = reader.pages[index].extract_text() or ""
            except Exception:  # noqa: BLE001 - 单页提取失败统一视为解析失败
                raise ParseError("PDF 页面提取失败") from None
            pages.append(PageText(page_no=index + 1, text=text))
    except (TooManyPages, ParseError):
        raise
    except Exception:  # noqa: BLE001 - 损坏文件
        raise ParseError("PDF 无法解析") from None
    if not any(page.text.strip() for page in pages):
        raise NoTextLayer
    return PdfDocument(pages=tuple(pages))


# ---------------------------------------------------------------- 清洗


def _normalize_segment(
    raw: str, base_offset: int
) -> tuple[str, list[int], int]:
    """归一化一段原文，返回清洗文本、每个字符的原文偏移与段末偏移。"""
    out: list[str] = []
    positions: list[int] = []
    offset = base_offset
    pending_space = False
    for ch in raw:
        if ch in "\r\n":
            if out:
                pending_space = True
        elif ch.isspace() or CONTROL_RE.match(ch):
            if ch.isspace() and out:
                pending_space = True
        else:
            if pending_space:
                out.append(" ")
                positions.append(offset)
                pending_space = False
            out.append(ch)
            positions.append(offset)
        offset += _char_width(ch)
    while out and out[-1] == " ":
        out.pop()
        positions.pop()
    return "".join(out), positions, offset


def _line_records(page_text: str) -> list[tuple[str, int, str]]:
    records: list[tuple[str, int, str]] = []
    offset = 0
    for line in page_text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        records.append((" ".join(content.split()), offset, content))
        offset += _u16_len(line)
    return records


def _repeated_margin_lines(pages: tuple[PageText, ...]) -> set[str]:
    """找出跨页重复的页首页尾行；阈值至少 3 页且覆盖 60% 页面。"""
    counts: dict[str, int] = {}
    for page in pages:
        norms = [norm for norm, _, _ in _line_records(page.text) if norm]
        margin = norms[:2] + norms[-2:]
        for norm in set(margin):
            counts[norm] = counts.get(norm, 0) + 1
    threshold = max(3, math.ceil(len(pages) * 0.6))
    return {norm for norm, count in counts.items() if count >= threshold}


def _is_margin_artifact(norm: str, repeated: set[str]) -> bool:
    if norm in repeated:
        return True
    if PRINT_PATH_RE.search(norm):
        return True
    return bool(PAGE_NUMBER_RE.match(norm))


def clean_page_text(page_text: str, repeated: set[str]) -> tuple[str, list[int]]:
    """逐页清理页首页尾的重复行与打印路径，保留页内全部正文行。"""
    records = _line_records(page_text)
    total = len(records)
    kept: list[tuple[str, int, str]] = []
    for index, record in enumerate(records):
        norm, _, _ = record
        in_margin = index < 2 or index >= total - 2
        if norm and in_margin and _is_margin_artifact(norm, repeated):
            continue
        if norm:
            kept.append(record)
    if not kept:
        kept = [record for record in records if record[0]]
    out: list[str] = []
    positions: list[int] = []
    for record_index, (_, offset, content) in enumerate(kept):
        cleaned, char_positions, _ = _normalize_segment(content, offset)
        if not cleaned:
            continue
        if out:
            out.append("\n")
            positions.append(offset)
        out.append(cleaned)
        positions.extend(char_positions)
    return "".join(out), positions


def clean_pdf(pdf: PdfDocument) -> tuple[tuple[int, str, list[int]], ...]:
    """返回 `(page_no, 清洗文本, 原文偏移)` 列表，空白页文本为空。"""
    repeated = _repeated_margin_lines(pdf.pages)
    cleaned_pages: list[tuple[int, str, list[int]]] = []
    for page in pdf.pages:
        text, positions = clean_page_text(page.text, repeated)
        cleaned_pages.append((page.page_no, text, positions))
    return tuple(cleaned_pages)


# ---------------------------------------------------------------- 分块


def _sentence_cut(text: str, start: int, lower: int, end: int) -> int | None:
    for index in range(end - 1, max(lower, start) - 1, -1):
        ch = text[index]
        if ch in SENTENCE_END:
            cut = index + 1
            while cut < len(text) and text[cut] in CLOSERS:
                cut += 1
            return cut
        if ch in ".!?;" and (index + 1 >= len(text) or text[index + 1].isspace()):
            return index + 1
    return None


def _space_cut(text: str, start: int, lower: int, end: int) -> int | None:
    for index in range(end - 1, max(lower, start) - 1, -1):
        if text[index].isspace():
            return index + 1
    return None


def _split_ranges(text: str, target: int, overlap: int) -> list[tuple[int, int]]:
    """按句末优先切分；只有单句超长时才硬切，可在段内保留少量重叠。"""
    length = len(text)
    if length == 0:
        return []
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < length:
        end = min(start + target, length)
        if end < length:
            lower = start + max(1, target // 2)
            cut = _sentence_cut(text, start, lower, end)
            if cut is None:
                cut = _space_cut(text, start, lower, end)
            if cut is not None and cut > start:
                end = cut
        while end > start and text[end - 1] == " ":
            end -= 1
        if end <= start:
            end = min(start + 1, length)
        ranges.append((start, end))
        if end >= length:
            break
        next_start = end - overlap
        if next_start <= start:
            next_start = start + 1
        while next_start < length and text[next_start] == " ":
            next_start += 1
        start = next_start
    return ranges


def _end_offset(positions: list[int], text: str, start: int, end: int) -> int:
    last = end - 1
    return positions[last] + _char_width(text[last])


def chunk_text(
    document: TextDocument, *, target: int, overlap: int
) -> list[Chunk]:
    chunks: list[Chunk] = []

    def make(
        text: str,
        positions: list[int],
        start: int,
        end: int,
        paragraph_start: int,
        paragraph_end: int,
    ) -> None:
        chunks.append(
            Chunk(
                index=len(chunks) + 1,
                text=text[start:end],
                page_no=None,
                paragraph_start=paragraph_start,
                paragraph_end=paragraph_end,
                source_start=positions[start],
                source_end=_end_offset(positions, text, start, end),
            )
        )

    pending: list[tuple[str, list[int], int, int, int]] = []
    pending_length = 0

    def flush() -> None:
        nonlocal pending, pending_length
        if not pending:
            return
        text = "\n".join(item[0] for item in pending)
        positions: list[int] = []
        for index, item in enumerate(pending):
            if index:
                positions.append(item[4])
            positions.extend(item[1])
        make(
            text,
            positions,
            0,
            len(text),
            pending[0][2],
            pending[-1][2],
        )
        pending = []
        pending_length = 0

    for paragraph in document.paragraphs:
        cleaned, positions, _ = _normalize_segment(paragraph.text, paragraph.start)
        if not cleaned:
            continue
        if len(cleaned) > target:
            flush()
            for start, end in _split_ranges(cleaned, target, overlap):
                if not cleaned[start:end].strip():
                    continue
                make(
                    cleaned,
                    positions,
                    start,
                    end,
                    paragraph.index,
                    paragraph.index,
                )
            continue
        if pending and pending_length + 1 + len(cleaned) > target:
            flush()
        pending.append(
            (
                cleaned,
                positions,
                paragraph.index,
                paragraph.start,
                positions[-1] + _char_width(cleaned[-1]),
            )
        )
        pending_length += len(cleaned) + (1 if len(pending) > 1 else 0)
    flush()
    return chunks


def chunk_pdf(document: PdfDocument, *, target: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page_no, text, positions in clean_pdf(document):
        if not text.strip():
            continue
        for start, end in _split_ranges(text, target, overlap):
            if not text[start:end].strip():
                continue
            chunks.append(
                Chunk(
                    index=len(chunks) + 1,
                    text=text[start:end],
                    page_no=page_no,
                    paragraph_start=None,
                    paragraph_end=None,
                    source_start=positions[start],
                    source_end=_end_offset(positions, text, start, end),
                )
            )
    return chunks
