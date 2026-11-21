"""材料解析执行器：失败码映射、工件计算与任务标识。"""

from __future__ import annotations

import unittest
import uuid

from app.services import document_extract
from app.services import worker
from tests.pdf_fixtures import header_footer_pdf, three_page_pdf


class FailureMappingTests(unittest.TestCase):
    def test_decode_failure_maps_to_stable_code(self) -> None:
        code, message = worker.ingest_failure(
            document_extract.DecodeFailed(("utf-8", "gb18030"))
        )

        self.assertEqual(code, "decode_failed")
        self.assertIn("UTF-8", message)

    def test_missing_text_layer_maps_to_stable_code(self) -> None:
        code, message = worker.ingest_failure(document_extract.NoTextLayer())

        self.assertEqual(code, "no_text_layer")
        self.assertIn("文字层", message)

    def test_page_limit_message_contains_limit_and_page_count(self) -> None:
        code, message = worker.ingest_failure(document_extract.TooManyPages(4, 3))

        self.assertEqual(code, "too_many_pages")
        self.assertIn("4", message)
        self.assertIn("3", message)
        self.assertNotIn("该 PDF 该 PDF", message)

    def test_unknown_failure_is_parse_error(self) -> None:
        code, _ = worker.ingest_failure(RuntimeError("boom"))

        self.assertEqual(code, "parse_error")


class ArtifactTests(unittest.TestCase):
    def test_pdf_artifacts_keep_page_numbers_and_blank_page(self) -> None:
        artifacts = worker._compute_artifacts(
            kind="pdf",
            data=three_page_pdf(),
            max_pages=10,
            chunk_target=1200,
            chunk_overlap=0,
            delay_ms=0,
        )

        self.assertEqual(artifacts.page_count, 3)
        self.assertIsNone(artifacts.text_encoding)
        self.assertEqual([chunk.page_no for chunk in artifacts.chunks], [1, 3])

    def test_pdf_cleaning_removes_margins(self) -> None:
        artifacts = worker._compute_artifacts(
            kind="pdf",
            data=header_footer_pdf(),
            max_pages=10,
            chunk_target=1200,
            chunk_overlap=0,
            delay_ms=0,
        )

        self.assertEqual(
            [chunk.text for chunk in artifacts.chunks],
            ["BODY-ONE-KEEP", "BODY-TWO-KEEP", "BODY-THREE-KEEP"],
        )

    def test_text_artifacts_record_encoding(self) -> None:
        artifacts = worker._compute_artifacts(
            kind="text",
            data="中文。".encode("gb18030"),
            max_pages=10,
            chunk_target=1200,
            chunk_overlap=0,
            delay_ms=0,
        )

        self.assertEqual(artifacts.text_encoding, "gb18030")
        self.assertIsNone(artifacts.page_count)
        self.assertEqual(artifacts.chunks[0].text, "中文。")


class ClaimShapeTests(unittest.TestCase):
    def test_claim_carries_kind_and_document_id(self) -> None:
        document_id = uuid.uuid4()
        claim = worker.Claim(
            job_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            attempt=1,
            lease_token=uuid.uuid4(),
            next_step=0,
            total_steps=worker.INGEST_STEP_KEYS.__len__(),
            stage="evidence-graph",
            fail_at_step=None,
            simulated_epoch=None,  # type: ignore[arg-type]
            kind="document.ingest",
            document_id=document_id,
        )

        self.assertEqual(claim.kind, "document.ingest")
        self.assertEqual(claim.document_id, document_id)
        self.assertEqual(claim.total_steps, 3)


if __name__ == "__main__":
    unittest.main()
