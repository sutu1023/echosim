"""测试用的最小 PDF 生成器（仅标准库）。

对象编号：1 目录、2 页树、每页一个 Page 对象与一个内容流，最后是 Helvetica 字体。
内容只使用 ASCII，避免嵌入字体，便于 `pypdf` 稳定提取。
"""

from __future__ import annotations


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages: list[list[str]]) -> bytes:
    """按给定的“每页若干行文本”生成 PDF；空列表表示空白页。"""
    page_count = len(pages)
    font_number = 3 + page_count * 2
    objects: dict[int, bytes] = {}
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(page_count))
    objects[1] = (
        b"<< /Type /Catalog /Pages 2 0 R >>"
    )
    objects[2] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("utf-8")
    )
    for index, lines in enumerate(pages):
        page_number = 3 + index * 2
        content_number = page_number + 1
        objects[page_number] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_number} 0 R >> >> "
            f"/Contents {content_number} 0 R >>"
        ).encode("utf-8")
        stream = _content_stream(lines)
        objects[content_number] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("utf-8")
            + stream
            + b"\nendstream"
        )
    objects[font_number] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    )

    output = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(output)
        output += f"{number} 0 obj\n".encode("utf-8")
        output += objects[number]
        output += b"\nendobj\n"
    xref_offset = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode("utf-8")
    output += b"0000000000 65535 f \n"
    for number in sorted(objects):
        output += f"{offsets[number]:010d} 00000 n \n".encode("utf-8")
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("utf-8")
    return bytes(output)


def _content_stream(lines: list[str]) -> bytes:
    if not lines:
        return b""
    parts = ["BT", "/F1 12 Tf", "72 780 Td"]
    for index, line in enumerate(lines):
        if index:
            parts.append("0 -18 Td")
        parts.append(f"({_escape(line)}) Tj")
    parts.append("ET")
    return "\n".join(parts).encode("utf-8")


def three_page_pdf() -> bytes:
    return build_pdf(
        [
            ["PAGE-ONE-ECHOSIM-MARKER"],
            [],
            ["PAGE-THREE-ECHOSIM-MARKER"],
        ]
    )


def header_footer_pdf() -> bytes:
    return build_pdf(
        [
            ["ECHOSIM-REPEATED-HEADER", "BODY-ONE-KEEP", "file:///tmp/print-one.pdf"],
            ["ECHOSIM-REPEATED-HEADER", "BODY-TWO-KEEP", "file:///tmp/print-two.pdf"],
            ["ECHOSIM-REPEATED-HEADER", "BODY-THREE-KEEP", "file:///tmp/print-three.pdf"],
        ]
    )


def no_text_pdf() -> bytes:
    return build_pdf([[]])
