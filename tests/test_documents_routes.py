"""材料路由：鉴权、归属、响应头、错误码与同步拒绝。"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db import get_db
from app.deps import require_user
from app.errors import DocumentNotFound, ProjectNotFound
from app.main import create_app
from app.services import document_storage
from app.services.documents import Document
from app.services.projects import Project
from app.services.sessions import SessionUser

USER = SessionUser(id=uuid.uuid4(), email="a@example.com", display_name="A")
PROJECT = Project(
    id=uuid.uuid4(),
    name="城市韧性推演",
    description="描述",
    created_at=datetime.now(timezone.utc),
)
NOW = datetime.now(timezone.utc)
DOCUMENT = Document(
    id=uuid.uuid4(),
    project_id=PROJECT.id,
    uploaded_by=USER.id,
    display_name="附件.pdf",
    kind="pdf",
    content_type="application/pdf",
    size_bytes=11,
    sha256="a" * 64,
    storage_path="p/d/original.pdf",
    text_encoding=None,
    status="ready",
    page_count=3,
    chunk_count=2,
    failure_code=None,
    failure_message=None,
    created_at=NOW,
    ready_at=NOW,
    job_id=uuid.uuid4(),
    run_id=uuid.uuid4(),
)
BASE = f"/api/projects/{PROJECT.id}/documents"


def build_client(*, authenticated: bool = True) -> TestClient:
    app = create_app()

    async def fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = fake_db
    if authenticated:
        app.dependency_overrides[require_user] = lambda: USER
    return TestClient(app)


class AnonymousDocumentAccessTests(unittest.TestCase):
    def test_all_document_endpoints_are_401_without_database_access(self) -> None:
        client = build_client(authenticated=False)
        document_id = str(DOCUMENT.id)
        paths = [
            ("GET", BASE, None),
            ("POST", BASE, None),
            ("GET", f"{BASE}/{document_id}", None),
            ("GET", f"{BASE}/{document_id}/chunks", None),
            ("GET", f"{BASE}/{document_id}/source", None),
            ("GET", f"{BASE}/{document_id}/file", None),
        ]
        with patch("app.deps.load_session", new=AsyncMock()) as loader:
            for method, path, _ in paths:
                with self.subTest(path=path):
                    response = client.request(method, path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json(), {"detail": "未登录"})
        loader.assert_not_awaited()


class DocumentBoundaryTests(unittest.TestCase):
    def test_other_project_is_indistinguishable_from_missing(self) -> None:
        client = build_client()
        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(side_effect=ProjectNotFound),
        ):
            response = client.get(BASE)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "项目不存在"})

    def test_missing_document_is_404(self) -> None:
        client = build_client()
        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.documents.document_service.get_document",
            new=AsyncMock(side_effect=DocumentNotFound),
        ):
            detail = client.get(f"{BASE}/{uuid.uuid4()}")
            source = client.get(f"{BASE}/{uuid.uuid4()}/source")
            original = client.get(f"{BASE}/{uuid.uuid4()}/file")

        for response in (detail, source, original):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {"detail": "材料不存在"})

    def test_invalid_paging_is_422(self) -> None:
        client = build_client()
        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ):
            too_big = client.get(f"{BASE}/{DOCUMENT.id}/chunks?limit=51")
            negative = client.get(f"{BASE}/{DOCUMENT.id}/chunks?after=-1")

        self.assertEqual(too_big.status_code, 422)
        self.assertEqual(negative.status_code, 422)


class DocumentResponseTests(unittest.TestCase):
    def _patched(self):
        return (
            patch(
                "app.routers.documents.projects.resolve_owned_project",
                new=AsyncMock(return_value=PROJECT),
            ),
            patch(
                "app.routers.documents.document_service.get_document",
                new=AsyncMock(return_value=DOCUMENT),
            ),
        )

    def test_file_headers_prevent_sniffing_and_encode_name(self) -> None:
        client = build_client()
        project_patch, document_patch = self._patched()
        with project_patch, document_patch, patch(
            "app.routers.documents.document_service.original_bytes",
            return_value=b"%PDF-1.4",
        ):
            response = client.get(f"{BASE}/{DOCUMENT.id}/file")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("filename*=UTF-8''%E9%99%84%E4%BB%B6.pdf", response.headers["content-disposition"])

    def test_text_source_is_utf8_and_pdf_source_is_409(self) -> None:
        client = build_client()
        text_document = Document(
            **{**DOCUMENT.__dict__, "kind": "text", "display_name": "材料.txt",
               "text_encoding": "gb18030"}
        )
        project_patch, document_patch = self._patched()
        with project_patch, document_patch, patch(
            "app.routers.documents.document_service.get_document",
            new=AsyncMock(return_value=text_document),
        ), patch(
            "app.routers.documents.document_service.original_bytes",
            return_value="中文".encode("gb18030"),
        ):
            source = client.get(f"{BASE}/{DOCUMENT.id}/source")

        self.assertEqual(source.status_code, 200)
        self.assertIn("charset=utf-8", source.headers["content-type"])
        self.assertEqual(source.text, "中文")

        project_patch, document_patch = self._patched()
        with project_patch, document_patch:
            pdf_source = client.get(f"{BASE}/{DOCUMENT.id}/source")
        self.assertEqual(pdf_source.status_code, 409)

    def test_chunks_before_ready_are_empty(self) -> None:
        client = build_client()
        queued = Document(**{**DOCUMENT.__dict__, "status": "queued", "chunk_count": 0})
        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.documents.document_service.list_chunks",
            new=AsyncMock(return_value=(queued, [])),
        ):
            response = client.get(f"{BASE}/{DOCUMENT.id}/chunks")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chunks"], [])
        self.assertFalse(response.json()["ready"])


class FailedDocumentDetailTests(unittest.TestCase):
    def test_failed_document_exposes_parse_time_limit(self) -> None:
        client = build_client()
        failed = Document(
            **{
                **DOCUMENT.__dict__,
                "status": "failed",
                "page_count": 4,
                "chunk_count": 0,
                "failure_code": "too_many_pages",
                "failure_message": "该 PDF 共 4 页，页数超过当前上限（上限 3 页）",
                "failure_detail": {"pages": 4, "limit": 3},
            }
        )
        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.documents.document_service.get_document",
            new=AsyncMock(return_value=failed),
        ):
            response = client.get(f"{BASE}/{DOCUMENT.id}")

        body = response.json()["document"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["pageCount"], 4)
        self.assertEqual(body["failure"]["code"], "too_many_pages")
        self.assertEqual(body["failure"]["detail"], {"pages": 4, "limit": 3})


class UploadValidationTests(unittest.TestCase):
    def _client(self) -> TestClient:
        return build_client()

    def test_unsupported_extension_returns_415(self) -> None:
        client = self._client()
        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ):
            response = client.post(BASE, files={"file": ("a.exe", b"MZ", "application/x-msdownload")})

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["code"], "unsupported_type")

    def test_pdf_requires_magic_bytes(self) -> None:
        client = self._client()
        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ):
            response = client.post(BASE, files={"file": ("a.pdf", b"not a pdf", "application/pdf")})

        self.assertEqual(response.status_code, 415)

    def test_oversize_and_empty_are_rejected(self) -> None:
        client = self._client()
        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.documents.document_storage.spool_upload",
            new=AsyncMock(side_effect=document_storage.FileTooLarge),
        ):
            oversize = client.post(BASE, files={"file": ("a.txt", b"hello", "text/plain")})

        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.documents.document_storage.spool_upload",
            new=AsyncMock(side_effect=document_storage.EmptyUpload),
        ):
            empty = client.post(BASE, files={"file": ("a.txt", b"", "text/plain")})

        self.assertEqual(oversize.status_code, 413)
        self.assertEqual(oversize.json()["code"], "file_too_large")
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()["code"], "empty_file")

    def test_upload_returns_created_and_job_ids(self) -> None:
        client = self._client()
        spooled = document_storage.SpooledUpload(
            temp_path=__import__("pathlib").Path("/tmp/none"),
            size_bytes=5,
            sha256="b" * 64,
        )
        with patch(
            "app.routers.documents.projects.resolve_owned_project",
            new=AsyncMock(return_value=PROJECT),
        ), patch(
            "app.routers.documents.document_storage.spool_upload",
            new=AsyncMock(return_value=spooled),
        ), patch(
            "app.routers.documents.document_service.register_upload",
            new=AsyncMock(return_value=(DOCUMENT, False)),
        ):
            response = client.post(BASE, files={"file": ("a.txt", b"hello", "text/plain")})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["deduplicated"])
        self.assertEqual(body["jobId"], str(DOCUMENT.job_id))
        self.assertEqual(body["document"]["documentId"], str(DOCUMENT.id))


class NamingTests(unittest.TestCase):
    def test_detect_kind_uses_extension_and_magic(self) -> None:
        from app.routers.documents import detect_kind

        self.assertEqual(detect_kind("a.pdf", b"%PDF-1.7"), "pdf")
        self.assertIsNone(detect_kind("a.pdf", b"PK"))
        self.assertEqual(detect_kind("a.MD", "# 标题".encode("utf-8")), "markdown")
        self.assertEqual(detect_kind("a.txt", b"hello"), "text")
        self.assertIsNone(detect_kind("a.docx", b"PK"))

    def test_display_name_drops_path_and_controls(self) -> None:
        from app.routers.documents import sanitize_display_name

        self.assertEqual(sanitize_display_name("../../etc/pass\x00wd.txt"), "passwd.txt")
        self.assertEqual(sanitize_display_name("C:\\temp\\材料.pdf"), "材料.pdf")
        self.assertEqual(sanitize_display_name("   "), "材料")


if __name__ == "__main__":
    unittest.main()
