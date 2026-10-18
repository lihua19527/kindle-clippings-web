#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剪藏台后端：一个只用标准库的本地 HTTP 服务。

接口
    GET  /                     单页应用
    GET  /assets/*             静态资源
    GET  /api/health           自检（解析库、浏览器、Python 版本）
    GET  /api/sample           示例剪藏文本
    POST /api/parse            解析 + 去重，返回 JSON（带 token，导出时复用）
    POST /api/export           按 token 导出 md / pdf / docx
    POST /api/probe            前端自检报告（?probe=1，本地测试用）

设计取舍
    · 解析结果缓存在内存里，导出时只传 token，不重复上传几 MB 的剪藏文本；
    · 全部导出（多本书）时 Markdown 打成 zip，PDF/Word 合并成一个文件（带封面与目录）；
    · 只监听 127.0.0.1，剪藏不出本机。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import threading
import traceback
import webbrowser
from collections import OrderedDict
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

from .clippings import Options, analyze, decode_bytes, load_kindle_module
from .render import docx_export, epub_export, markdown_export, pdf_export

PROJECT_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = PROJECT_DIR / "public"
SAMPLE_PATH = PUBLIC_DIR / "demo-clippings.txt"
MAX_BODY = 64 * 1024 * 1024
CACHE_LIMIT = 6

_MIME_FALLBACK = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


class ParseCache:
    """极小的 LRU：token -> ParsedSet。单机单用户，够用。"""

    def __init__(self, limit: int = CACHE_LIMIT) -> None:
        self._data: OrderedDict[str, object] = OrderedDict()
        self._limit = limit
        self._lock = threading.Lock()
        self._counter = 0

    def put(self, value) -> str:
        with self._lock:
            self._counter += 1
            token = f"p{self._counter}"
            self._data[token] = value
            while len(self._data) > self._limit:
                self._data.popitem(last=False)
            return token

    def get(self, token: str):
        with self._lock:
            value = self._data.get(token)
            if value is not None:
                self._data.move_to_end(token)
            return value


CACHE = ParseCache()

# 前端自检报告（?probe=1 时由页面 POST 上来，tests/ui_check.py 读取）
_PROBE: dict = {"report": None}


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _attachment(name: str) -> str:
    """中文文件名要按 RFC 5987 编码，否则浏览器会存成乱码。"""
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


class Handler(SimpleHTTPRequestHandler):
    server_version = "ClippingsDesk/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ 基础工具

    def log_message(self, fmt: str, *args) -> None:  # 精简日志
        sys.stdout.write(f"  {self.address_string()} {fmt % args}\n")

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

    def _error(self, status: int, message: str, detail: str = "") -> None:
        self._json(status, {"ok": False, "error": message, "detail": detail})

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > MAX_BODY:
            raise ValueError("请求体过大")
        return self.rfile.read(length)

    def _read_json(self) -> dict:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"请求体不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    # ------------------------------------------------------------ 路由

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/" or path == "/index.html":
                return self._serve_file(PUBLIC_DIR / "index.html")
            if path == "/api/health":
                return self._health()
            if path == "/api/sample":
                return self._sample()
            if path == "/api/probe":
                return self._json(HTTPStatus.OK, {"ok": True, "probe": _PROBE["report"]})
            if path.startswith("/assets/"):
                target = (PUBLIC_DIR / path.lstrip("/")).resolve()
                if PUBLIC_DIR.resolve() in target.parents and target.is_file():
                    return self._serve_file(target)
                return self._error(HTTPStatus.NOT_FOUND, "资源不存在")
            if path == "/favicon.ico":
                return self._serve_file(PUBLIC_DIR / "assets" / "favicon.svg")
            return self._error(HTTPStatus.NOT_FOUND, "页面不存在")
        except BrokenPipeError:
            return
        except Exception as exc:  # pragma: no cover
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "服务器内部错误", str(exc))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/parse":
                return self._parse()
            if path == "/api/export":
                return self._export()
            if path == "/api/probe":
                return self._probe()
            return self._error(HTTPStatus.NOT_FOUND, "接口不存在")
        except BrokenPipeError:
            return
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "处理失败", str(exc))

    # ------------------------------------------------------------ 具体处理

    def _serve_file(self, target: Path) -> None:
        if not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "文件不存在")
        data = target.read_bytes()
        suffix = target.suffix.lower()
        ctype = _MIME_FALLBACK.get(suffix) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, data, ctype)

    def _health(self) -> None:
        try:
            kc = load_kindle_module()
            parser = getattr(kc, "__file__", "")
        except RuntimeError as exc:
            parser = f"× {exc}"
        try:
            browser = pdf_export.find_browser()
        except RuntimeError:
            browser = ""
        self._json(HTTPStatus.OK, {
            "ok": bool(parser and not parser.startswith("×")),
            "python": sys.version.split()[0],
            "parser": parser,
            "browser": browser,
            "sample": SAMPLE_PATH.is_file(),
            "version": "1.0",
        })

    def _sample(self) -> None:
        if not SAMPLE_PATH.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "示例文件缺失")
        self._send(HTTPStatus.OK, SAMPLE_PATH.read_bytes(), "text/plain; charset=utf-8")

    def _probe(self) -> None:
        report = self._read_json()
        if not isinstance(report, dict):
            raise ValueError("自检报告必须是 JSON 对象")
        _PROBE["report"] = report
        self._json(HTTPStatus.OK, {"ok": True})

    def _parse(self) -> None:
        payload = self._read_json()
        options = Options.from_payload(payload.get("options"))
        sources: list[str] = []

        chunks: list[str] = []
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text)
            sources.append("粘贴文本")

        files = payload.get("files")
        if isinstance(files, list):
            for item in files:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                name = str(item.get("name") or "My Clippings.txt")
                if isinstance(content, str) and content.strip():
                    chunks.append(content)
                    sources.append(name)

        folder = payload.get("folder")
        if isinstance(folder, str) and folder.strip():
            text_or_none, names = _read_folder(Path(folder.strip()))
            if text_or_none:
                chunks.append(text_or_none)
                sources.extend(names)

        if payload.get("detect"):
            kc = load_kindle_module()
            found = kc.detect_clippings()
            if found is None:
                raise ValueError("没找到已连接的 Kindle（未发现 */documents/My Clippings.txt）。")
            chunks.append(kc.read_text(found))
            sources.append(str(found))

        if not chunks:
            raise ValueError("没有收到剪藏内容：请粘贴文本、选择文件，或指定文件夹。")

        merged = "\n".join(chunk.replace("\r\n", "\n") for chunk in chunks)
        parsed = analyze(merged, options, sources)
        # 解析器很宽容：没有分隔符的文本也会被当成一条「标注」，但书名为空。
        # 这种情况在网页上只会得到一本「未知书籍」，不如直接告诉用户格式不对。
        if parsed.kept_total == 0 or not any(
            (book.title or "").strip() for book in parsed.books
        ):
            raise ValueError(
                "这段文本里没有认出剪藏记录。请确认是 Kindle 导出的 My Clippings.txt"
                "（每条剪藏以 ========== 分隔，第一行是「书名 (作者)」）。"
            )
        token = CACHE.put(parsed)
        body = parsed.to_json()
        body["token"] = token
        self._json(HTTPStatus.OK, body)

    def _export(self) -> None:
        payload = self._read_json()
        token = str(payload.get("token") or "")
        parsed = CACHE.get(token)
        if parsed is None:
            raise ValueError("解析结果已过期，请重新导入一次再导出。")

        fmt = str(payload.get("format") or "md").lower()
        scope = str(payload.get("scope") or "all").lower()
        packaging = str(payload.get("packaging") or "merged").lower()
        now = datetime.now()

        if fmt not in ("md", "docx", "pdf", "epub"):
            raise ValueError(f"不支持的导出格式：{fmt}")
        if packaging not in ("merged", "per_book"):
            raise ValueError(f"不支持的打包方式：{packaging}")

        if scope == "book":
            try:
                index = int(payload.get("book", 0))
            except (TypeError, ValueError):
                index = 0
            try:
                book = parsed.book(index)
            except KeyError as exc:
                raise ValueError(str(exc)) from exc
            books = [book]
        elif scope == "selected":
            raw_ids = payload.get("books") or []
            if not isinstance(raw_ids, list):
                raise ValueError("books 应该是一个数组。")
            picks: list = []
            seen_ids: set[int] = set()
            for raw in raw_ids:
                try:
                    index = int(raw)
                except (TypeError, ValueError):
                    continue
                if index in seen_ids:
                    continue
                try:
                    picks.append(parsed.book(index))
                except KeyError:
                    continue
                seen_ids.add(index)
            if not picks:
                raise ValueError("没有选中任何书。")
            books = picks
        else:
            books = parsed.books

        if not books:
            raise ValueError("没有可导出的书。")

        # 只有一本时不做合集：直接给「书名_笔记.ext」，不管选的是合并还是打包
        if len(books) == 1:
            single = {
                "md": markdown_export.single_markdown,
                "docx": docx_export.single_docx,
                "pdf": pdf_export.single_pdf,
                "epub": epub_export.single_epub,
            }[fmt]
            name, data = single(books[0], now)
        elif packaging == "per_book":
            zipped = {
                "md": markdown_export.markdown_zip,
                "docx": docx_export.docx_zip,
                "pdf": pdf_export.pdf_zip,
                "epub": epub_export.epub_zip,
            }[fmt]
            name, data = zipped(books, now)
        else:
            merged = {
                "md": markdown_export.merged_markdown,
                "docx": docx_export.merged_docx,
                "pdf": pdf_export.merged_pdf,
                "epub": epub_export.merged_epub,
            }[fmt]
            name, data = merged(books, now)

        ctype = {
            "md": "text/markdown; charset=utf-8",
            "zip": "application/zip",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pdf": "application/pdf",
            "epub": "application/epub+zip",
        }[name.rsplit(".", 1)[-1].lower()]
        print(f"  [导出] {name}  {len(data) / 1024:.0f} KB  （{len(books)} 本 / {packaging if len(books) > 1 else 'single'}）")
        self._send(HTTPStatus.OK, data, ctype, {"Content-Disposition": _attachment(name)})


def _read_folder(folder: Path) -> tuple[str, list[str]]:
    """读一个目录下的剪藏文本：优先 My Clippings.txt，否则合并所有 .txt。"""
    if not folder.is_dir():
        raise ValueError(f"文件夹不存在：{folder}")
    preferred = folder / "My Clippings.txt"
    names: list[str] = []
    chunks: list[str] = []
    if preferred.is_file():
        return decode_bytes(preferred.read_bytes()), [str(preferred)]
    for path in sorted(folder.glob("*.txt")):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        chunks.append(decode_bytes(raw))
        names.append(str(path))
    if not chunks:
        raise ValueError(f"{folder} 里没有 My Clippings.txt 或 .txt 文件。")
    return "\n".join(chunks), names


def build_server(host: str, port: int, tries: int = 10) -> ThreadingHTTPServer:
    last: Exception | None = None
    for offset in range(tries):
        candidate = port + offset
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            httpd.daemon_threads = True
            return httpd
        except OSError as exc:  # 端口被占用就顺延
            last = exc
            continue
    raise RuntimeError(f"{port}-{port + tries - 1} 都被占用：{last}")


def main(argv: list[str] | None = None) -> int:
    from .config import CONFIG

    ap = argparse.ArgumentParser(description="剪藏台：Kindle 剪藏查重与整理的本地网页")
    ap.add_argument("--port", type=int, default=int(CONFIG.get("port", 8765)))
    ap.add_argument("--host", default=str(CONFIG.get("host", "127.0.0.1")))
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args(argv)

    if not (PUBLIC_DIR / "index.html").is_file():
        print(f"[×] 找不到前端文件：{PUBLIC_DIR / 'index.html'}", file=sys.stderr)
        return 2

    try:
        httpd = build_server(args.host, args.port)
    except RuntimeError as exc:
        print(f"[×] {exc}", file=sys.stderr)
        return 2

    host, port = httpd.server_address[0], httpd.server_address[1]
    url = f"http://{host}:{port}/"
    print("")
    print("  剪藏台 · Kindle clippings desk")
    print(f"  地址：{url}")
    print("  关闭服务：在这个窗口按 Ctrl+C")
    print("")
    if not args.no_open and CONFIG.get("open_browser", True):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
