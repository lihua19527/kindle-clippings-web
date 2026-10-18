#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出文件名的唯一出处。

规则（用户 2026-10-18 定下，别随手改）：
    单本    书名_笔记.md / .pdf / .docx        —— 不带书名号
    多本合集 读书笔记合集_YYYY-MM-DD导出.md / .pdf / .docx / .zip

书名里的非法字符用 kindle_clippings.safe_filename 处理（与原脚本一致），
重名时按 (2)(3) 后缀区分，避免解压时互相覆盖。
"""

from __future__ import annotations

from datetime import datetime

from ..clippings import load_kindle_module

BOOK_SUFFIX = "_笔记"
COLLECTION_STEM = "读书笔记合集"


def book_stem(title: str) -> str:
    """书名 → 文件名主干（不含扩展名，不带书名号）。"""
    kc = load_kindle_module()
    base = kc.safe_filename((title or "未命名").strip(), limit=70)
    return f"{base}{BOOK_SUFFIX}"


def book_filename(title: str, ext: str) -> str:
    return f"{book_stem(title)}.{ext}"


def book_filenames(books: list, ext: str) -> list[str]:
    """一批书的文件名，重名加 (2)(3)。顺序与 books 一致。"""
    seen: dict[str, int] = {}
    names: list[str] = []
    for book in books:
        stem = book_stem(book.title)
        count = seen.get(stem, 0) + 1
        seen[stem] = count
        names.append(f"{stem}.{ext}" if count == 1 else f"{stem}({count}).{ext}")
    return names


def collection_filename(ext: str, now: datetime | None = None) -> str:
    """合集文件名：读书笔记合集_2026-10-18导出.zip"""
    now = now or datetime.now()
    return f"{COLLECTION_STEM}_{now:%Y-%m-%d}导出.{ext}"


__all__ = [
    "BOOK_SUFFIX",
    "COLLECTION_STEM",
    "book_filename",
    "book_filenames",
    "book_stem",
    "collection_filename",
]
