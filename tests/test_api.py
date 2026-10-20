#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剪藏台的 API 集成测试（只用标准库 unittest）。

跑法：
    python -m unittest discover -s tests -v
或
    python tests/test_api.py

它会真的起一个 HTTP 服务（绑 127.0.0.1 的随机端口），走完整的
「导入 → 解析 → 去重 → 导出」链路，覆盖 md / docx / pdf 与单本 / 全本两种范围。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from app.server import build_server  # noqa: E402
from app import server  # noqa: E402

SAMPLE = (PROJECT_DIR / "public" / "demo-clippings.txt").read_text(encoding="utf-8")
EXPECT_BOOKS = 5
EXPECT_ENTRIES = 22
EXPECT_RAW = 28


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # 测试不能覆盖用户真正的那份「上次导入」：换成临时文件
        cls._session_dir = tempfile.mkdtemp(prefix="clipsession-")
        server.SESSION_PATH = Path(cls._session_dir) / "last-session.json"
        cls.httpd = build_server("127.0.0.1", 8799, tries=20)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls._session_dir, ignore_errors=True)

    # ------------------------------------------------------------ 工具

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=15) as res:
            return res.status, res.read(), dict(res.headers)

    def post(self, path: str, payload: dict):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                return res.status, res.read(), dict(res.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers)

    def parse(self, **payload) -> dict:
        status, body, _ = self.post("/api/parse", payload)
        self.assertEqual(status, 200, body[:400])
        return json.loads(body.decode("utf-8"))

    # ------------------------------------------------------------ 静态与自检

    def test_health(self):
        status, body, _ = self.get("/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertTrue(data["parser"].endswith("kindle_clippings.py"))
        self.assertTrue(data["sample"])

    def test_index_served(self):
        status, body, headers = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        html = body.decode("utf-8")
        self.assertIn("剪藏台", html)
        self.assertIn("/assets/app.js", html)

    def test_assets(self):
        for path, needle in (
            ("/assets/styles.css", "--yellow"),
            ("/assets/app.js", "/api/parse"),
            ("/assets/hero.svg", "clippings"),
            ("/assets/favicon.svg", "K/C"),
        ):
            status, body, _ = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(needle, body.decode("utf-8"), path)

    # ------------------------------------------------------------ 解析

    def test_parse_sample(self):
        data = self.parse(text=SAMPLE)
        self.assertEqual(data["stats"], {
            "books": EXPECT_BOOKS,
            "entries": EXPECT_ENTRIES,
            "raw": EXPECT_RAW,
            "dropped": EXPECT_RAW - EXPECT_ENTRIES,
        })
        self.assertTrue(data["token"].startswith("p"))

        book = data["books"][0]
        for key in ("id", "title", "author", "raw", "kept", "dropped", "counts", "entries", "merged", "span"):
            self.assertIn(key, book)
        entry = book["entries"][0]
        for key in ("kind", "isNote", "content", "page", "locationRaw", "added"):
            self.assertIn(key, entry)

        titles = {b["title"] for b in data["books"]}
        self.assertIn("人类简史", titles)
        notes = sum(b["counts"]["笔记"] for b in data["books"])
        self.assertGreater(notes, 0)

    def test_no_dedupe_keeps_everything(self):
        data = self.parse(text=SAMPLE, options={"keep_all": True})
        self.assertEqual(data["stats"]["entries"], data["stats"]["raw"])
        self.assertEqual(data["stats"]["dropped"], 0)

    def test_position_only(self):
        data = self.parse(text=SAMPLE, options={"fuzzy": False})
        # 关掉相似度后，逐字笔记的中间态不再被合并，条目数应不少于默认
        self.assertGreaterEqual(data["stats"]["entries"], EXPECT_ENTRIES)

    def test_min_entries_filter(self):
        data = self.parse(text=SAMPLE, options={"min_entries": 5})
        self.assertTrue(all(b["kept"] >= 5 for b in data["books"]))
        self.assertLess(data["stats"]["books"], EXPECT_BOOKS)

    def test_files_payload(self):
        data = self.parse(files=[{"name": "My Clippings.txt", "content": SAMPLE}])
        self.assertEqual(data["stats"]["books"], EXPECT_BOOKS)
        self.assertIn("My Clippings.txt", data["sources"])

    def test_empty_input_is_400(self):
        status, body, _ = self.post("/api/parse", {})
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(body)["ok"])

    def test_garbage_input_is_400(self):
        status, body, _ = self.post("/api/parse", {"text": "这不是剪藏。随便一段话。"})
        self.assertEqual(status, 400)

    # ------------------------------------------------------------ 上次导入

    def _session(self) -> dict:
        status, body, _ = self.get("/api/session")
        self.assertEqual(status, 200)
        return json.loads(body.decode("utf-8"))["session"]

    def test_session_remembers_last_parse(self):
        data = self.parse(text=SAMPLE)
        session = self._session()
        self.assertIsNotNone(session)
        self.assertEqual(session["stats"], data["stats"])
        self.assertEqual(session["stats"]["books"], EXPECT_BOOKS)
        self.assertTrue(session["saved_at"])

    def test_session_comes_back_after_restart(self):
        """服务重启（内存清空）后，还有人要就应当从磁盘把上次的重新解析出来。"""
        self.parse(text=SAMPLE)
        # 模拟重启：缓存与会话状态都清掉，磁盘上的 last-session.json 留着
        server.CACHE._data.clear()
        server._SESSION.update({"loaded": False, "token": None, "parsed": None, "saved_at": None})
        session = self._session()
        self.assertEqual(session["stats"]["books"], EXPECT_BOOKS)
        self.assertEqual(session["stats"]["entries"], EXPECT_ENTRIES)
        self.assertTrue(server.SESSION_PATH.is_file())

    def test_new_parse_overwrites_session(self):
        self.parse(text=SAMPLE)
        single = (
            "只有一本 (某人)\n"
            "- 您在位置 #10-12 的标注 | 添加于 2024年1月1日星期一 上午12:00:00\n\n"
            "只有一条。\n==========\n"
        )
        self.parse(text=single)
        session = self._session()
        self.assertEqual(session["stats"]["books"], 1)
        self.assertEqual(session["stats"]["entries"], 1)

    # ------------------------------------------------------------ 导出

    def _export(self, token: str, fmt: str, scope: str = "all", book: int = 0,
                packaging: str | None = None, books: list | None = None):
        payload = {"token": token, "format": fmt, "scope": scope, "book": book}
        if packaging:
            payload["packaging"] = packaging
        if books is not None:
            payload["books"] = books
        status, body, headers = self.post("/api/export", payload)
        self.assertEqual(status, 200, body[:300])
        return body, headers

    def _name(self, headers) -> str:
        import re
        import urllib.parse

        match = re.search(r"filename\*=UTF-8''([^;]+)", headers["Content-Disposition"])
        self.assertIsNotNone(match, headers["Content-Disposition"])
        return urllib.parse.unquote(match.group(1))

    def test_export_markdown_single_named_after_book(self):
        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "md", "book", 1)
        self.assertIn("attachment", headers["Content-Disposition"])
        # 单本：书名_笔记.md，不带书名号
        self.assertEqual(self._name(headers), "人类简史_笔记.md")
        text = body.decode("utf-8")
        self.assertIn("# 《人类简史》", text)
        self.assertIn("==", text)  # 笔记仍以高亮呈现

    def test_export_markdown_merged_has_clickable_toc(self):
        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "md", "all", packaging="merged")
        name = self._name(headers)
        self.assertRegex(name, r"^读书笔记合集_\d{4}-\d{2}-\d{2}导出\.md$")
        text = body.decode("utf-8")
        self.assertIn("## 目录", text)
        # 目录每一条都指向后面那一节的标题（标题链接，Obsidian / VS Code / GitHub 都能跳）
        self.assertEqual(text.count("](#"), 5)
        self.assertEqual(text.count("\n## 《"), 5)

    def test_export_markdown_zip(self):
        import io
        import zipfile

        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "md", "all", packaging="per_book")
        self.assertTrue(body.startswith(b"PK"))
        self.assertIn("application/zip", headers["Content-Type"])
        self.assertRegex(self._name(headers), r"^读书笔记合集_\d{4}-\d{2}-\d{2}导出\.zip$")
        names = zipfile.ZipFile(io.BytesIO(body)).namelist()
        self.assertEqual(len(names), 5)
        self.assertIn("人类简史_笔记.md", names)
        self.assertFalse(any("《" in n for n in names), names)

    def test_export_docx_single(self):
        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "docx", "book", 2)
        self.assertTrue(body.startswith(b"PK"))
        self.assertIn("wordprocessingml", headers["Content-Type"])
        self.assertTrue(self._name(headers).endswith("_笔记.docx"), self._name(headers))

    def test_export_docx_has_highlight(self):
        import io
        import zipfile

        token = self.parse(text=SAMPLE)["token"]
        body, _ = self._export(token, "docx", "book", 1)
        xml = zipfile.ZipFile(io.BytesIO(body)).read("word/document.xml").decode("utf-8")
        self.assertIn("w:highlight", xml)
        self.assertIn("F8E6A3", xml)  # 笔记底色

    def test_export_docx_merged_toc_links(self):
        import io
        import zipfile

        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "docx", "all", packaging="merged")
        self.assertRegex(self._name(headers), r"^读书笔记合集_\d{4}-\d{2}-\d{2}导出\.docx$")
        xml = zipfile.ZipFile(io.BytesIO(body)).read("word/document.xml").decode("utf-8")
        self.assertEqual(xml.count("w:bookmarkStart"), 5)
        self.assertEqual(xml.count('<w:hyperlink'), 5)
        for i in range(1, 6):
            self.assertIn(f'w:anchor="book_{i}"', xml)
            self.assertIn(f'w:name="book_{i}"', xml)

    def test_export_pdf_single(self):
        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "pdf", "book", 1)
        self.assertTrue(body.startswith(b"%PDF"))
        self.assertGreater(len(body), 30000)
        self.assertIn("application/pdf", headers["Content-Type"])
        self.assertTrue(self._name(headers).endswith("_笔记.pdf"))

    def test_export_pdf_merged_has_toc_destinations(self):
        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "pdf", "all", packaging="merged")
        self.assertTrue(body.startswith(b"%PDF"))
        self.assertGreater(len(body), 60000)
        self.assertRegex(self._name(headers), r"^读书笔记合集_\d{4}-\d{2}-\d{2}导出\.pdf$")
        # 目录里的锚点会被 Chromium 写进 PDF 的命名目标
        for i in range(1, 6):
            self.assertIn(f"book-{i}".encode(), body)

    def test_export_epub_single(self):
        import io
        import zipfile

        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "epub", "book", 1)
        self.assertIn("application/epub+zip", headers["Content-Type"])
        self.assertEqual(self._name(headers), "人类简史_笔记.epub")
        zf = zipfile.ZipFile(io.BytesIO(body))
        # mimetype 必须第一个且不压缩，否则阅读器不认这个包
        first = zf.infolist()[0]
        self.assertEqual(first.filename, "mimetype")
        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
        self.assertEqual(zf.read("mimetype"), b"application/epub+zip")
        self.assertIn("META-INF/container.xml", zf.namelist())
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        self.assertIn("<dc:language>zh-CN</dc:language>", opf)
        chapter = zf.read("OEBPS/book-01.xhtml").decode("utf-8")
        self.assertIn("《人类简史》", chapter)
        self.assertIn("quote-note", chapter)  # 笔记的黄色底纹

    def test_export_epub_merged_has_cover_and_toc(self):
        import io
        import zipfile

        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "epub", "all", packaging="merged")
        self.assertRegex(self._name(headers), r"^读书笔记合集_\d{4}-\d{2}-\d{2}导出\.epub$")
        zf = zipfile.ZipFile(io.BytesIO(body))
        names = zf.namelist()
        self.assertIn("OEBPS/cover.xhtml", names)
        for i in range(1, 6):
            self.assertIn(f"OEBPS/book-{i:02d}.xhtml", names)
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
        self.assertEqual(nav.count("<a href="), 6)  # 封面 + 5 本
        ncx = zf.read("OEBPS/toc.ncx").decode("utf-8")
        self.assertEqual(ncx.count("<navPoint"), 6)

    def test_export_epub_zip(self):
        import io
        import zipfile

        token = self.parse(text=SAMPLE)["token"]
        body, headers = self._export(token, "epub", "all", packaging="per_book")
        self.assertIn("application/zip", headers["Content-Type"])
        self.assertRegex(self._name(headers), r"^读书笔记合集_\d{4}-\d{2}-\d{2}导出\.zip$")
        outer = zipfile.ZipFile(io.BytesIO(body))
        names = outer.namelist()
        self.assertEqual(len(names), 5)
        self.assertIn("人类简史_笔记.epub", names)
        inner = zipfile.ZipFile(io.BytesIO(outer.read("人类简史_笔记.epub")))
        self.assertEqual(inner.infolist()[0].filename, "mimetype")

    def test_export_selected_books(self):
        token = self.parse(text=SAMPLE)["token"]
        # 书的 id 是解析后的顺序：0=Sapiens 1=人类简史 2=局外人 3=我们仨 4=活着
        body, headers = self._export(token, "md", "selected", packaging="merged", books=[1, 3])
        self.assertRegex(self._name(headers), r"^读书笔记合集_\d{4}-\d{2}-\d{2}导出\.md$")
        text = body.decode("utf-8")
        self.assertIn("《人类简史》", text)
        self.assertIn("《我们仨》", text)
        self.assertNotIn("《活着》", text)
        self.assertNotIn("《局外人》", text)
        self.assertIn("书目数: 2", text)

    def test_export_selected_single_book_collapses_to_book_name(self):
        token = self.parse(text=SAMPLE)["token"]
        _, headers = self._export(token, "md", "selected", packaging="per_book", books=[2])
        self.assertEqual(self._name(headers), "局外人_笔记.md")

    def test_export_selected_empty_is_400(self):
        token = self.parse(text=SAMPLE)["token"]
        status, body, _ = self.post(
            "/api/export", {"token": token, "format": "md", "scope": "selected", "books": []}
        )
        self.assertEqual(status, 400)
        self.assertIn("没有选中", json.loads(body)["error"])

    def test_bad_token_is_400(self):
        status, body, _ = self.post("/api/export", {"token": "nope", "format": "md"})
        self.assertEqual(status, 400)
        self.assertIn("过期", json.loads(body)["error"])

    def test_bad_format_is_400(self):
        token = self.parse(text=SAMPLE)["token"]
        status, _, _ = self.post("/api/export", {"token": token, "format": "mobi"})
        self.assertEqual(status, 400)

    def test_bad_packaging_is_400(self):
        token = self.parse(text=SAMPLE)["token"]
        status, _, _ = self.post(
            "/api/export", {"token": token, "format": "md", "packaging": "bundle"}
        )
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
