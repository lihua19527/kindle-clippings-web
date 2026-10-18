#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown 导出，三种形态：

    single_markdown   单本 → 一本书一个 .md（内容与原脚本 render_book 逐字一致）
    merged_markdown   多本 → 一个 .md，开头是可点击的目录（链到各节标题）
    markdown_zip      多本 → 每本一个 .md，打成一个 zip

文件名规则集中在 naming.py：单本 `书名_笔记.md`，合集 `读书笔记合集_YYYY-MM-DD导出.md`。
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

from ..clippings import load_kindle_module
from .naming import book_filenames, book_stem, collection_filename

NOTE_KIND = "笔记"

# 链接目标里这些字符会截断 `[标题](#目标)`，换成百分号写法；中文和书名号不动
_NEEDS_ESCAPE = str.maketrans({
    " ": "%20", "(": "%28", ")": "%29", "<": "%3C", ">": "%3E",
    "#": "%23", "[": "%5B", "]": "%5D", "|": "%7C",
})


def _heading_link(heading: str) -> str:
    """把标题变成同文件内的标题链接，只把会破坏链接语法的字符转义。

    空格和括号在 `](...)` 里会截断链接，其它字符（中文、书名号、冒号）留着更好读。
    Obsidian / VS Code / GitHub 的预览都会先解码再匹配标题。
    """
    return "#" + heading.translate(_NEEDS_ESCAPE)


def _entry_lines(index: int, entry) -> list[str]:
    """一条剪藏 → Markdown 行。笔记的标题用 ==…== 高亮（Obsidian 原生语法）。"""
    heading = f"{index}. {entry.kind}"
    if entry.kind == NOTE_KIND:
        heading = f"=={heading}=="
    lines = [f"### {heading}", ""]
    for para in entry.content.splitlines():
        lines.append(f"> {para}" if para.strip() else ">")
    lines.append("")

    bits = []
    if entry.page:
        bits.append(f"第 {entry.page} 页")
    if entry.location_raw:
        bits.append(f"位置 {entry.location_raw}")
    if entry.added:
        bits.append(entry.added)
    lines.append("*" + " · ".join(bits) + "*")
    lines.append("")
    return lines


def single_markdown(book, now: datetime | None = None) -> tuple[str, bytes]:
    """返回（文件名, 字节）。内容与 kindle_clippings.py 写出的文件完全相同。"""
    kc = load_kindle_module()
    now = now or datetime.now()
    return f"{book_stem(book.title)}.md", kc.render_book(book, now).encode("utf-8")


def merged_markdown(books: list, now: datetime | None = None) -> tuple[str, bytes]:
    """多本合成一个 .md：frontmatter + 可跳转目录 + 每本一节。"""
    now = now or datetime.now()
    total = sum(len(b.entries) for b in books)
    raw = sum(b.raw_count for b in books)
    dropped = raw - total

    lines = [
        "---",
        "type: 读书笔记合集",
        f"书目数: {len(books)}",
        f"条数: {total}",
        f"导出时间: {now:%Y-%m-%d %H:%M}",
        "tags:",
        "  - 读书剪藏",
        "---",
        "",
        "# 读书笔记合集",
        "",
        f"> 共 {len(books)} 本，{total} 条剪藏"
        + (f"（已去重 {dropped} 条重复记录）" if dropped > 0 else "")
        + f" ｜ 导出于 {now:%Y-%m-%d}",
        "",
        "## 目录",
        "",
    ]

    for i, book in enumerate(books, 1):
        drop = book.raw_count - len(book.entries)
        tail = f" · 已去重 {drop} 条" if drop > 0 else ""
        heading = f"《{book.title}》"
        lines.append(
            f"{i}. [{heading}]({_heading_link(heading)})"
            f" · {book.author or '未知作者'} · {len(book.entries)} 条{tail}"
        )

    lines += ["", "---", ""]

    for i, book in enumerate(books, 1):
        drop = book.raw_count - len(book.entries)
        lines += [
            f"## 《{book.title}》",
            "",
            f"> {book.author or '未知作者'} ｜ {len(book.entries)} 条 ｜ 导出于 {now:%Y-%m-%d}",
            "",
        ]
        if drop > 0:
            lines += [f"> [!note] 已自动去重 {drop} 条重复记录。", ""]
        for j, entry in enumerate(book.entries, 1):
            lines += _entry_lines(j, entry)
        lines += ["---", ""]

    name = collection_filename("md", now)
    return name, ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def markdown_zip(books: list, now: datetime | None = None) -> tuple[str, bytes]:
    """每本书一个 .md（与原脚本格式一致），打包成 zip。"""
    kc = load_kindle_module()
    now = now or datetime.now()
    buffer = io.BytesIO()
    names = book_filenames(books, "md")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for book, name in zip(books, names):
            zf.writestr(name, kc.render_book(book, now).encode("utf-8"))
    return collection_filename("zip", now), buffer.getvalue()


__all__ = ["merged_markdown", "markdown_zip", "single_markdown"]
