"""来源映射纯函数：UTF-16 偏移、空白归一与歧义拒绝。"""

from __future__ import annotations

import unittest

from app.engines.business_contracts import SourceRef
from app.services.graph_source import SourceMappingError, resolve_source


def source(quote: str, start: int = 0, end: int = 0, chunk: int = 1) -> SourceRef:
    return SourceRef(
        document_id="00000000-0000-4000-8000-000000000001",
        chunk_index=chunk,
        quote=quote,
        source_start=start,
        source_end=end,
    )


class GraphSourceMappingTests(unittest.TestCase):
    def test_emoji_and_crlf_map_to_utf16_original_span(self) -> None:
        raw = "第一段😀 有空格\r\n第二段内容。"
        chunk = "第一段😀 有空格 第二段内容。"
        start, end = resolve_source(
            source("第一段😀", 0, 5),
            chunk_text=chunk,
            chunk_start=0,
            raw_text=raw,
        )
        self.assertEqual((start, end), (0, 5))
        self.assertEqual(raw[: len("第一段😀")], "第一段😀")

    def test_pdf_page_chunk_start_skips_removed_header(self) -> None:
        raw = "HEADER\nBODY THREE TEXT\nfile:///tmp/x.pdf"
        chunk = "BODY THREE TEXT"
        start, end = resolve_source(
            source("THREE", 12, 17),
            chunk_text=chunk,
            chunk_start=len("HEADER\n"),
            raw_text=raw,
        )
        self.assertEqual((start, end), (12, 17))
        self.assertEqual(raw[7:22][5:10], "THREE")

    def test_repeated_quote_requires_unique_offsets(self) -> None:
        with self.assertRaises(SourceMappingError) as caught:
            resolve_source(
                source("重复", 99, 101),
                chunk_text="重复 重复",
                chunk_start=0,
                raw_text="重复 重复",
            )
        self.assertEqual(caught.exception.code, "ambiguous_source")

    def test_model_offsets_disambiguate_repeated_quote(self) -> None:
        start, end = resolve_source(
            source("重复", 3, 5),
            chunk_text="重复 重复",
            chunk_start=0,
            raw_text="重复 重复",
        )
        self.assertEqual((start, end), (3, 5))

    def test_missing_quote_is_source_mismatch(self) -> None:
        with self.assertRaises(SourceMappingError) as caught:
            resolve_source(
                source("不存在", 0, 3),
                chunk_text="只有其它文字",
                chunk_start=0,
                raw_text="只有其它文字",
            )
        self.assertEqual(caught.exception.code, "source_mismatch")


if __name__ == "__main__":
    unittest.main()
