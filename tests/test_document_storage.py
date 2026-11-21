"""材料原件存储：流式写入、哈希、上限与路径安全。"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from app.services import document_storage


async def chunks_of(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    for index in range(0, len(data), size):
        yield data[index : index + size]


class SpoolUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_limit_passes_and_hash_matches(self) -> None:
        payload = b"a" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spooled = await document_storage.spool_upload(
                root, chunks_of(payload), len(payload)
            )
            self.assertEqual(spooled.size_bytes, 64)
            self.assertEqual(spooled.sha256, hashlib.sha256(payload).hexdigest())
            self.assertTrue(spooled.temp_path.exists())

    async def test_over_limit_aborts_without_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(document_storage.FileTooLarge):
                await document_storage.spool_upload(root, chunks_of(b"b" * 65), 64)
            leftovers = list((root / document_storage.TEMP_DIRNAME).iterdir())
            self.assertEqual(leftovers, [])

    async def test_empty_upload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(document_storage.EmptyUpload):
                await document_storage.spool_upload(Path(tmp), chunks_of(b""), 10)


class PathAndReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_path_is_derived_from_identifiers_only(self) -> None:
        project_id = uuid.uuid4()
        document_id = uuid.uuid4()

        relative = document_storage.relative_path(project_id, document_id, "pdf")

        self.assertEqual(
            relative, f"{project_id}/{document_id}/original.pdf"
        )
        self.assertNotIn("..", relative)

    async def test_commit_and_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"%PDF-1.4 test"
            spooled = await document_storage.spool_upload(
                root, chunks_of(payload), 1024
            )
            relative = document_storage.relative_path(
                uuid.uuid4(), uuid.uuid4(), "pdf"
            )
            document_storage.commit_spool(root, spooled, relative)

            self.assertEqual(document_storage.read_bytes(root, relative), payload)
            self.assertFalse(spooled.temp_path.exists())

    def test_absolute_path_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                document_storage.absolute_path(Path(tmp), "../outside.txt")


if __name__ == "__main__":
    unittest.main()
