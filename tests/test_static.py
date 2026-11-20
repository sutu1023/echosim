"""同域静态托管与 SPA 回退的行为测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import create_app


class StaticHostingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dist = Path(self._tmp.name) / "dist"
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text(
            "<!doctype html><html><body><h1>EchoSim</h1></body></html>",
            encoding="utf-8",
        )
        (self.dist / "assets" / "app.js").write_text(
            "console.log('EchoSim asset')", encoding="utf-8"
        )
        (Path(self._tmp.name) / "secret.txt").write_text(
            "TOP-SECRET-CONTENT", encoding="utf-8"
        )
        self.client = TestClient(create_app(web_dist_dir=self.dist))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_home_serves_index_html(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("EchoSim", response.text)

    def test_existing_asset_is_served(self) -> None:
        response = self.client.get("/assets/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn("EchoSim asset", response.text)

    def test_unknown_path_falls_back_to_index(self) -> None:
        response = self.client.get("/some/deep/route")

        self.assertEqual(response.status_code, 200)
        self.assertIn("EchoSim", response.text)

    def test_path_outside_dist_is_rejected(self) -> None:
        # 编码后的点段不会被客户端归一化，直接到达路由，必须 404。
        for path in (
            "/%2e%2e/secret.txt",
            "/..%2Fsecret.txt",
            "/assets/%2e%2e%2f%2e%2e%2fsecret.txt",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("TOP-SECRET-CONTENT", response.text)

        # 字面点段会被 HTTP 客户端归一化到 dist 内路径；无论如何不得泄漏外部文件。
        for path in ("/../secret.txt", "/assets/../../secret.txt"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotIn("TOP-SECRET-CONTENT", response.text)

        # 解析函数本身必须拒绝越界路径。
        from app.static import resolve_within

        for raw in ("../secret.txt", "../../etc/passwd", "/../secret.txt", "assets/../../secret.txt"):
            with self.subTest(raw=raw):
                self.assertIsNone(resolve_within(self.dist, raw))

    def test_api_routes_take_precedence_over_static_fallback(self) -> None:
        probe = AsyncMock(return_value=None)
        with patch("app.health.select_one", new=probe):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})


if __name__ == "__main__":
    unittest.main()
