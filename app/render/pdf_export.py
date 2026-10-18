#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 导出：把打印用 HTML 交给本机 Edge/Chrome 的 headless 模式 --print-to-pdf。

选这条路而不是引第三方 PDF 库的原因：
  · 中文断行、标点、字距由浏览器排版引擎负责，效果最好；
  · PDF 里的文字是可选中、可搜索的，字体随文件嵌入（ToUnicode 映射齐全）；
  · 不需要往环境里装 reportlab / weasyprint 这类重依赖，用系统本来就有的浏览器。

合并文档的目录用 `<a href="#book-N">`，Chromium 打印时会转成 PDF 的命名目标
（/Dests 里的 book-N），Edge / Acrobat / SumatraPDF 里都能点着跳。
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .html_doc import document_html
from .naming import book_filename, book_filenames, collection_filename

CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str:
    """按 config.json → 常见安装路径 → PATH 的顺序找浏览器。"""
    try:
        from ..config import CONFIG
        configured = CONFIG.get("edge_path")
    except Exception:  # pragma: no cover
        configured = None
    if configured and Path(configured).is_file():
        return str(configured)

    for path in CANDIDATES:
        if Path(path).is_file():
            return path
    for exe in ("msedge.exe", "chrome.exe"):
        found = shutil.which(exe)
        if found:
            return found
    raise RuntimeError(
        "找不到 Edge/Chrome，无法生成 PDF。请在 config.json 里用 edge_path 指定 msedge.exe 的路径。"
    )


def html_to_pdf(html: str, timeout: int = 180) -> bytes:
    """HTML 字符串 → PDF 字节。临时文件放在项目 .cache 下，生成后立即清掉。"""
    exe = find_browser()
    cache_root = Path(__file__).resolve().parent.parent.parent / ".cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="pdf-", dir=cache_root))
    html_path = work / "doc.html"
    pdf_path = work / "doc.pdf"
    profile = work / "profile"
    try:
        html_path.write_text(html, encoding="utf-8", newline="\n")
        args = [
            exe,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not pdf_path.is_file() or pdf_path.stat().st_size < 1024:
            tail = (completed.stderr or b"").decode("utf-8", "replace").strip()[-800:]
            raise RuntimeError(f"浏览器没有生成 PDF（退出码 {completed.returncode}）。{tail}")
        data = pdf_path.read_bytes()
        if not data.startswith(b"%PDF"):
            raise RuntimeError("生成的文件不是 PDF，浏览器可能输出了错误页。")
        return data
    finally:
        shutil.rmtree(work, ignore_errors=True)


def single_pdf(book, now: datetime | None = None) -> tuple[str, bytes]:
    now = now or datetime.now()
    html = document_html([book], now, cover=False)
    return book_filename(book.title, "pdf"), html_to_pdf(html)


def merged_pdf(books: list, now: datetime | None = None) -> tuple[str, bytes]:
    now = now or datetime.now()
    html = document_html(books, now, cover=True)
    return collection_filename("pdf", now), html_to_pdf(html)


def pdf_zip(books: list, now: datetime | None = None) -> tuple[str, bytes]:
    """每本书一个独立 PDF，打包成 zip（每本单独调一次浏览器，书多会慢一些）。"""
    now = now or datetime.now()
    names = book_filenames(books, "pdf")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for book, name in zip(books, names):
            zf.writestr(name, html_to_pdf(document_html([book], now, cover=False)))
    return collection_filename("zip", now), buffer.getvalue()


__all__ = [
    "find_browser",
    "html_to_pdf",
    "merged_pdf",
    "pdf_zip",
    "single_pdf",
]
