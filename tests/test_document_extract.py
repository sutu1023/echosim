"""材料解析：解码、段落位置、PDF 页码、清洗与分块的纯函数测试。"""

from __future__ import annotations

import unittest

from app.services import document_extract as extract
from tests.pdf_fixtures import build_pdf, header_footer_pdf, no_text_pdf, three_page_pdf


def slice_utf16(text: str, start: int, end: int) -> str:
    encoded = text.encode("utf-16-le")
    return encoded[start * 2 : end * 2].decode("utf-16-le")


class TextDecodingTests(unittest.TestCase):
    def test_utf8_mixed_newlines_keep_paragraph_offsets(self) -> None:
        raw = "第一段。\r\n\r\n第二段。\r\n第三行。\r\n\r\n第三段。"
        document = extract.parse_text(raw.encode("utf-8"))

        self.assertEqual(document.encoding, "utf-8")
        self.assertEqual(
            [slice_utf16(document.text, p.start, p.end) for p in document.paragraphs],
            ["第一段。", "第二段。\r\n第三行。", "第三段。"],
        )

    def test_emoji_offsets_match_javascript_slice(self) -> None:
        raw = "😀😀 开头。\r\n\r\n第二段正文。"
        document = extract.parse_text(raw.encode("utf-8"))
        second = document.paragraphs[1]

        self.assertEqual(second.start, 12)
        self.assertEqual(slice_utf16(document.text, second.start, second.end), "第二段正文。")

        chunks = extract.chunk_text(document, target=6, overlap=0)
        self.assertEqual(chunks[0].source_start, 0)
        self.assertEqual(chunks[1].source_start, second.start)
        self.assertEqual(chunks[1].source_end, second.end)

    def test_gb18030_is_decoded_and_recorded(self) -> None:
        document = extract.parse_text("中文材料。".encode("gb18030"))

        self.assertEqual(document.encoding, "gb18030")
        self.assertEqual(document.text, "中文材料。")

    def test_utf16_with_bom_is_decoded(self) -> None:
        document = extract.parse_text("中文材料。".encode("utf-16"))

        self.assertEqual(document.encoding, "utf-16")
        self.assertEqual(document.text, "中文材料。")

    def test_undecodable_bytes_are_rejected(self) -> None:
        with self.assertRaises(extract.DecodeFailed):
            extract.parse_text(b"\xff" * 20)

    def test_nul_characters_are_removed_but_offsets_stay_in_original(self) -> None:
        document = extract.parse_text("前\x00后。\r\n\r\n第二段。".encode("utf-8"))
        chunks = extract.chunk_text(document, target=1200, overlap=0)

        self.assertEqual(chunks[0].text, "前后。\n第二段。")
        self.assertEqual(chunks[0].source_start, 0)
        self.assertEqual(slice_utf16(document.text, 0, 3), "前\x00后")
        self.assertEqual(slice_utf16(document.text, 3, 8), "。\r\n\r\n")


class PdfReadingTests(unittest.TestCase):
    def test_three_pages_keep_physical_numbers_and_blank_page(self) -> None:
        pdf = extract.parse_pdf(three_page_pdf(), max_pages=10)

        self.assertEqual([(page.page_no, page.text) for page in pdf.pages], [
            (1, "PAGE-ONE-ECHOSIM-MARKER"),
            (2, ""),
            (3, "PAGE-THREE-ECHOSIM-MARKER"),
        ])
        chunks = extract.chunk_pdf(pdf, target=1200, overlap=0)
        self.assertEqual([chunk.page_no for chunk in chunks], [1, 3])
        self.assertIsNone(chunks[0].paragraph_start)

    def test_repeated_header_and_print_path_are_removed_per_page(self) -> None:
        pdf = extract.parse_pdf(header_footer_pdf(), max_pages=10)
        chunks = extract.chunk_pdf(pdf, target=1200, overlap=0)

        self.assertEqual([chunk.page_no for chunk in chunks], [1, 2, 3])
        self.assertEqual([chunk.text for chunk in chunks], [
            "BODY-ONE-KEEP",
            "BODY-TWO-KEEP",
            "BODY-THREE-KEEP",
        ])

    def test_page_limit_boundary(self) -> None:
        three = build_pdf([["A"], ["B"], ["C"]])
        extract.parse_pdf(three, max_pages=3)

        four = build_pdf([["A"], ["B"], ["C"], ["D"]])
        with self.assertRaises(extract.TooManyPages) as raised:
            extract.parse_pdf(four, max_pages=3)
        self.assertEqual((raised.exception.pages, raised.exception.limit), (4, 3))

    def test_pdf_without_text_layer_is_rejected(self) -> None:
        with self.assertRaises(extract.NoTextLayer):
            extract.parse_pdf(no_text_pdf(), max_pages=10)


class TextCleaningTests(unittest.TestCase):
    def test_whitespace_is_normalized_and_paths_are_kept(self) -> None:
        raw = (
            "第一段  含   空格。\r\n"
            "路径 C:\\data\\report.csv 与 file:///tmp/a.txt\r\n\r\n"
            "第二段。"
        )
        document = extract.parse_text(raw.encode("utf-8"))
        chunks = extract.chunk_text(document, target=1200, overlap=0)

        self.assertEqual(len(chunks), 1)
        self.assertIn("第一段 含 空格。", chunks[0].text)
        self.assertIn("C:\\data\\report.csv", chunks[0].text)
        self.assertIn("file:///tmp/a.txt", chunks[0].text)
        self.assertNotIn("\r", chunks[0].text)
        self.assertNotIn("  ", chunks[0].text)

    def test_long_paragraph_splits_at_sentence_end(self) -> None:
        raw = "。".join(["这是一句用于切分的中文句子" * 3] * 12) + "。"
        document = extract.parse_text(raw.encode("utf-8"))
        chunks = extract.chunk_text(document, target=120, overlap=0)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertTrue(chunk.text.endswith("。"), chunk.text[-10:])

    def test_overlap_ranges_are_independently_sliceable(self) -> None:
        raw = "".join(f"第{i}句内容。" for i in range(80))
        document = extract.parse_text(raw.encode("utf-8"))
        chunks = extract.chunk_text(document, target=60, overlap=10)

        self.assertGreater(len(chunks), 2)
        for previous, current in zip(chunks, chunks[1:]):
            self.assertLess(current.source_start, previous.source_end)
            self.assertGreater(current.source_start, previous.source_start)
        for chunk in chunks:
            self.assertEqual(
                slice_utf16(document.text, chunk.source_start, chunk.source_end)[:2],
                chunk.text[:2],
            )


if __name__ == "__main__":
    unittest.main()
