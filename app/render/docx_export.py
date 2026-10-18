#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Word (.docx) 导出，三种形态：单本、多本合并（带可点击目录）、多本打包 zip。

笔记用 Word **原生黄色高亮**（WD_COLOR_INDEX.YELLOW）—— 在 Word / WPS 里能继续改、能搜、
能被「查找 > 突出显示」识别，不是图片也不是色块。标注则用左侧竖线 + 灰底引用块。
合并文档的目录用 bookmark + w:hyperlink 实现跳转，不依赖「更新域」，打开即可点。
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .naming import COLLECTION_STEM, book_filename, book_filenames, collection_filename

FONT = "微软雅黑"
INK = "20201E"
MUTED = "65645E"
LINK = "234BC4"        # 目录里的跳转链接用 Bauhaus 蓝
NOTE_FILL = "F8E6A3"   # 笔记底色（与网页的 bauhaus 黄同族）
HIGHLIGHT_FILL = "F4F2EB"
RULE = "CCCAC1"

_BOOKMARK_ID = 0



# ---------------------------------------------------------------- 底层小工具


def _apply_fonts(rpr_owner, name: str) -> None:
    """给 style / run 的 rPr 同时设置西文与东亚字体（只设 font.name 中文会回退到宋体）。"""
    rpr = rpr_owner.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)


def _shade(paragraph, fill: str) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    ppr.append(shd)


def _left_rule(paragraph, color: str = INK, size: int = 18) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), str(size))
    left.set(qn("w:space"), "10")
    left.set(qn("w:color"), color)
    borders.append(left)
    ppr.append(borders)


def _bottom_rule(paragraph, color: str = RULE, size: int = 12) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "6")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    ppr.append(borders)


def _run(paragraph, text: str, *, size: float = 10.5, bold: bool = False,
         color: str = INK, italic: bool = False, highlight: bool = False,
         font: str = FONT):
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = RGBColor.from_string(color)
    if highlight:
        run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    _apply_fonts(run._element, font)
    return run


def _meta_text(entry) -> str:
    bits = []
    if entry.page:
        bits.append(f"第 {entry.page} 页")
    if entry.location_raw:
        bits.append(f"位置 {entry.location_raw}")
    if entry.added:
        bits.append(entry.added)
    bits.append(entry.kind)
    return "  ·  ".join(bits)


def _bookmark(paragraph, name: str) -> None:
    """给段落打书签，供目录里的 w:hyperlink w:anchor 跳过来。"""
    global _BOOKMARK_ID
    _BOOKMARK_ID += 1
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(_BOOKMARK_ID))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(_BOOKMARK_ID))
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def _hyperlink(paragraph, text: str, anchor: str, *, size: float = 11, bold: bool = False) -> None:
    """文档内部跳转链接（不用域代码，打开就是可点的，不需要「更新目录」）。"""
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), anchor)
    link.set(qn("w:history"), "1")

    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attr), FONT)
    rpr.append(fonts)
    if bold:
        rpr.append(OxmlElement("w:b"))
    color = OxmlElement("w:color")
    color.set(qn("w:val"), LINK)
    rpr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(underline)
    half_points = OxmlElement("w:sz")
    half_points.set(qn("w:val"), str(int(size * 2)))
    rpr.append(half_points)
    run.append(rpr)

    node = OxmlElement("w:t")
    node.set(qn("xml:space"), "preserve")
    node.text = text
    run.append(node)
    link.append(run)
    paragraph._p.append(link)



# ---------------------------------------------------------------- 文档骨架


def _setup(doc) -> None:
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.4)
    section.right_margin = Cm(2.4)

    normal = doc.styles["Normal"]
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor.from_string(INK)
    _apply_fonts(normal.element, FONT)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.45


def _cover(doc, books: list, now: datetime) -> None:
    total = sum(len(b.entries) for b in books)
    raw = sum(b.raw_count for b in books)

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(60)
    _run(p, "KINDLE CLIPPINGS", size=9, bold=True, color=MUTED)

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    _run(p, COLLECTION_STEM, size=34, bold=True)

    p = doc.add_paragraph()
    _run(p, "把 Kindle 上的划线与笔记，按书整理到一起。", size=11, color=MUTED)

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(26)
    _run(p, f"{len(books)} 本书  ·  {total} 条剪藏  ·  去重 {raw - total} 条", size=10.5, bold=True)

    p = doc.add_paragraph()
    _run(p, f"导出于 {now:%Y-%m-%d %H:%M}", size=9, color=MUTED)

    doc.add_page_break()

    p = doc.add_paragraph()
    _run(p, "目录", size=20, bold=True)
    _bottom_rule(p, INK, 18)
    for i, book in enumerate(books, 1):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        _run(p, f"{i:02d}  ", size=9, bold=True, color=MUTED)
        # 点书名直接跳到那一本（bookmark 在 _book() 里打）
        _hyperlink(p, f"《{book.title}》", f"book_{i}", size=12, bold=True)
        _run(p, f"   {book.author or '未知作者'}  ·  {len(book.entries)} 条", size=9, color=MUTED)
    doc.add_page_break()


def _book(doc, book, now: datetime, ordinal: int) -> None:
    heading = doc.add_heading(level=1)
    heading.paragraph_format.space_before = Pt(0)
    heading.paragraph_format.space_after = Pt(2)
    _run(heading, f"《{book.title}》", size=22, bold=True)
    _bookmark(heading, f"book_{ordinal}")

    dropped = book.raw_count - len(book.entries)
    sub = doc.add_paragraph()
    _run(sub, f"{book.author or '未知作者'}", size=9.5, color=MUTED)
    _run(sub, f"   ·   {len(book.entries)} 条", size=9.5, color=MUTED)
    if dropped > 0:
        _run(sub, f"   ·   已去重 {dropped} 条", size=9.5, color="D93625")
    _bottom_rule(sub, INK, 18)
    sub.paragraph_format.space_after = Pt(14)

    for i, entry in enumerate(book.entries, 1):
        is_note = entry.kind == "笔记"

        head = doc.add_paragraph()
        head.paragraph_format.space_before = Pt(12)
        head.paragraph_format.space_after = Pt(4)
        head.paragraph_format.keep_with_next = True
        _run(head, f"{i:02d}", size=9, color=MUTED, bold=True)
        _run(head, f"  {entry.kind}", size=11, bold=True, highlight=is_note)

        fill = NOTE_FILL if is_note else HIGHLIGHT_FILL
        for line in entry.content.splitlines():
            para = doc.add_paragraph()
            para.paragraph_format.left_indent = Cm(0.5)
            para.paragraph_format.space_after = Pt(2)
            para.paragraph_format.line_spacing = 1.4
            _shade(para, fill)
            _left_rule(para, INK if is_note else RULE, 18)
            _run(para, line if line.strip() else " ", size=10.5)

        meta = doc.add_paragraph()
        meta.paragraph_format.left_indent = Cm(0.5)
        meta.paragraph_format.space_before = Pt(2)
        meta.paragraph_format.space_after = Pt(2)
        _run(meta, _meta_text(entry), size=8, color=MUTED, italic=True)


def _build(books: list, now: datetime, cover: bool) -> Document:
    doc = Document()
    _setup(doc)
    doc.core_properties.title = COLLECTION_STEM if cover else f"《{books[0].title}》"
    doc.core_properties.subject = "Kindle 剪藏"
    doc.core_properties.created = now

    if cover:
        _cover(doc, books, now)

    for i, book in enumerate(books, 1):
        if i > 1:  # 每本书另起一页（封面/目录已经自己分过页）
            doc.add_page_break()
        _book(doc, book, now, i)
    return doc


def _to_bytes(doc) -> bytes:
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- 对外接口


def single_docx(book, now: datetime | None = None) -> tuple[str, bytes]:
    now = now or datetime.now()
    doc = _build([book], now, cover=False)
    return book_filename(book.title, "docx"), _to_bytes(doc)


def merged_docx(books: list, now: datetime | None = None) -> tuple[str, bytes]:
    now = now or datetime.now()
    doc = _build(books, now, cover=True)
    return collection_filename("docx", now), _to_bytes(doc)


def docx_zip(books: list, now: datetime | None = None) -> tuple[str, bytes]:
    """每本书一个独立的 .docx（不带封面目录），打包成 zip。"""
    now = now or datetime.now()
    buffer = io.BytesIO()
    names = book_filenames(books, "docx")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for book, name in zip(books, names):
            zf.writestr(name, _to_bytes(_build([book], now, cover=False)))
    return collection_filename("zip", now), buffer.getvalue()

