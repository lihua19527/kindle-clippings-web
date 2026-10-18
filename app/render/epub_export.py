#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPUB 导出 —— 给 Kindle / 其他阅读器用的电子书。

为什么手写而不是引第三方库：
EPUB 本质就是一个 zip（`mimetype` + `META-INF/container.xml` + OPF + XHTML），
正文我们已经有了现成的数据模型（和 PDF / Word 共用同一份 `Book`），
用标准库 `zipfile` 拼一遍只要一百多行，能精确控制中文排版与「笔记」的黄色底纹；
`ebooklib` 会额外拉进依赖却换不来什么。验收用 W3C EPUBCheck + epub-doctor 的
kindle_check（见 README「怎么验」）。

结构（合并版）：
    mimetype                      必须第一个、不压缩
    META-INF/container.xml
    OEBPS/content.opf             EPUB 3 + NCX 兜底
    OEBPS/style.css
    OEBPS/nav.xhtml               阅读器里的目录
    OEBPS/toc.ncx                 EPUB 2 阅读器的目录
    OEBPS/cover.xhtml             封面（只有合集才有）
    OEBPS/book-01.xhtml …         每本书一章
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime
from html import escape

from . import naming

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

# 只用相对单位（em / %）：Kindle 上写死 px 会被阅读器的字号设置顶掉。
CSS = """@charset "utf-8";

body {
  margin: 0 0.8em;
  color: #20201e;
  background: #ffffff;
  font-family: "Songti SC", "Source Han Serif SC", "Noto Serif CJK SC", serif;
  font-size: 1em;
  line-height: 1.75;
}
h1 {
  margin: 1.2em 0 0.2em;
  font-size: 1.5em;
  line-height: 1.35;
  font-weight: bold;
}
h2 { font-size: 1.15em; margin: 1.2em 0 0.4em; }
p { margin: 0 0 0.6em; text-indent: 0; }
a { color: #234bc4; text-decoration: none; }

p.kicker {
  margin: 0 0 0.4em;
  font-size: 0.75em;
  letter-spacing: 0.16em;
  color: #65645e;
}
p.byline { margin: 0 0 1.6em; font-size: 0.85em; color: #65645e; }
p.byline b { color: #20201e; }
p.byline .drop { color: #d93625; }

hr.rule { border: 0; border-top: 2px solid #20201e; margin: 0 0 1.4em; }

/* ---------- 每一条剪藏 ---------- */
div.entry { margin: 0 0 1.4em; page-break-inside: avoid; }
p.eh { margin: 0 0 0.3em; font-size: 0.8em; }
span.no { color: #65645e; margin-right: 0.6em; }
span.kind {
  padding: 0.1em 0.4em;
  font-weight: bold;
  border: 1px solid #20201e;
}
span.kind-note { background: #f4c434; border-color: #f4c434; }
span.kind-bookmark { border-color: #65645e; color: #65645e; }

blockquote.quote {
  margin: 0;
  padding: 0.5em 0 0.5em 0.8em;
  border-left: 2px solid #cccac1;
  background: #faf9f5;
}
blockquote.quote p { margin: 0 0 0.4em; }
blockquote.quote p:last-child { margin-bottom: 0; }
/* 笔记：整块黄底，跟网页 / PDF / Word 里的强调保持一致 */
blockquote.quote-note { background: #f8e6a3; border-left: 2px solid #20201e; }

p.emeta { margin: 0.3em 0 0; font-size: 0.68em; color: #65645e; }

/* ---------- 封面 ---------- */
div.cover { margin-top: 2em; }
div.cover h1 { font-size: 2.2em; margin: 0.6em 0 0.3em; }
p.brand {
  display: inline-block;
  margin: 0 0 1.4em;
  padding: 0.45em 0.6em;
  background: #d93625;
  color: #fff;
  font-size: 1.05em;
  font-weight: bold;
  letter-spacing: 0.16em;
}
p.lede { font-size: 0.95em; color: #65645e; margin: 0 0 2em; }
table.stats { width: 100%; border-collapse: collapse; margin: 0 0 2em; }
table.stats td {
  width: 33%;
  padding: 0.8em 0.6em;
  border-top: 1px solid #cccac1;
  border-bottom: 1px solid #cccac1;
  vertical-align: top;
}
table.stats strong { display: block; font-size: 1.6em; }
table.stats span { font-size: 0.72em; color: #65645e; }
p.foot { font-size: 0.75em; color: #65645e; margin-top: 3em; }
p.foot .shapes { color: #d93625; letter-spacing: 0.4em; }
"""


def _e(text: str) -> str:
    return escape(text, quote=False)


def _xhtml(title: str, body: str, css_href: str = "style.css") -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>{_e(title)}</title>
<link rel="stylesheet" type="text/css" href="{css_href}"/>
</head>
<body>
{body}
</body>
</html>
"""


def _kind_class(kind: str) -> str:
    return {"笔记": "note", "书签": "bookmark"}.get(kind, "highlight")


def _meta(entry) -> str:
    bits = []
    if entry.page:
        bits.append(f"第 {entry.page} 页")
    if entry.location_raw:
        bits.append(f"位置 {entry.location_raw}")
    if entry.added:
        bits.append(entry.added)
    return " · ".join(_e(b) for b in bits)


def _entry_html(index: int, entry) -> str:
    is_note = entry.kind == "笔记"
    paras = "".join(
        f"<p>{_e(line) if line.strip() else '&#160;'}</p>" for line in entry.content.splitlines()
    )
    return f"""<div class="entry">
<p class="eh"><span class="no">{index:02d}</span><span class="kind kind-{_kind_class(entry.kind)}">{_e(entry.kind)}</span></p>
<blockquote class="quote{' quote-note' if is_note else ''}">{paras}</blockquote>
<p class="emeta">{_meta(entry)}</p>
</div>
"""


def _book_xhtml(book, ordinal: int) -> str:
    dropped = book.raw_count - len(book.entries)
    drop_html = f'<span class="drop"> · 已去重 {dropped} 条</span>' if dropped > 0 else ""
    entries = "".join(_entry_html(i, e) for i, e in enumerate(book.entries, 1))
    body = f"""<p class="kicker">BOOK {ordinal:02d}</p>
<h1>《{_e(book.title)}》</h1>
<p class="byline"><b>{_e(book.author or '未知作者')}</b> · {len(book.entries)} 条{drop_html}</p>
<hr class="rule"/>
{entries}"""
    return _xhtml(f"《{book.title}》", body)


def _cover_xhtml(books: list, now: datetime) -> str:
    total = sum(len(b.entries) for b in books)
    raw = sum(b.raw_count for b in books)
    body = f"""<div class="cover">
<p class="brand">K/C</p>
<p class="kicker">KINDLE CLIPPINGS · 本地整理</p>
<h1>读书笔记合集</h1>
<p class="lede">把 Kindle 上的划线与笔记，按书整理到一起。</p>
<table class="stats">
<tr>
<td><strong>{len(books)}</strong><span>本书</span></td>
<td><strong>{total}</strong><span>条剪藏</span></td>
<td><strong>{raw - total}</strong><span>条重复已去</span></td>
</tr>
</table>
<p class="foot">导出于 {now:%Y-%m-%d %H:%M}<br/><span class="shapes">● ▲ ■</span></p>
</div>"""
    return _xhtml("读书笔记合集", body)


def _nav_xhtml(books: list, *, with_cover: bool) -> str:
    items = []
    if with_cover:
        items.append('<li><a href="cover.xhtml">封面</a></li>')
    for i, book in enumerate(books, 1):
        items.append(f'<li><a href="book-{i:02d}.xhtml">《{_e(book.title)}》</a></li>')
    body = f"""<nav epub:type="toc" id="toc">
<h1>目录</h1>
<ol>
{chr(10).join(items)}
</ol>
</nav>"""
    return _xhtml("目录", body)


def _ncx(books: list, identifier: str, title: str, *, with_cover: bool) -> str:
    points = []

    def point(pid: str, order: int, label: str, src: str) -> str:
        return (
            f'<navPoint id="{pid}" playOrder="{order}">'
            f"<navLabel><text>{_e(label)}</text></navLabel>"
            f'<content src="{src}"/></navPoint>'
        )

    order = 1
    if with_cover:
        points.append(point("nav-cover", order, "封面", "cover.xhtml"))
        order += 1
    for i, book in enumerate(books, 1):
        points.append(
            point(f"nav-{i}", order, f"《{book.title}》", f"book-{i:02d}.xhtml")
        )
        order += 1
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zh-CN">
<head>
<meta name="dtb:uid" content="{_e(identifier)}"/>
<meta name="dtb:depth" content="1"/>
<meta name="dtb:totalPageCount" content="0"/>
<meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle><text>{_e(title)}</text></docTitle>
<navMap>
{chr(10).join(points)}
</navMap>
</ncx>
"""
    return body


def _opf(
    books: list,
    identifier: str,
    title: str,
    now: datetime,
    *,
    with_cover: bool,
) -> str:
    items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
    ]
    spine = []
    if with_cover:
        items.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="cover"/>')
    for i in range(1, len(books) + 1):
        items.append(
            f'<item id="book-{i:02d}" href="book-{i:02d}.xhtml" '
            'media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="book-{i:02d}"/>')

    authors = []
    for book in books:
        author = (book.author or "").strip()
        if author and author not in authors:
            authors.append(author)
    creators = "".join(f'\n    <dc:creator>{_e(a)}</dc:creator>' for a in authors[:3])

    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="zh-CN">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{identifier}</dc:identifier>
    <dc:title>{_e(title)}</dc:title>
    <dc:language>zh-CN</dc:language>{creators}
    <dc:date>{now:%Y-%m-%dT%H:%M:%SZ}</dc:date>
    <meta property="dcterms:modified">{now:%Y-%m-%dT%H:%M:%SZ}</meta>
  </metadata>
  <manifest>
    {chr(10).join('    ' + item for item in items).strip()}
  </manifest>
  <spine toc="ncx">
    {chr(10).join('    ' + item for item in spine).strip()}
  </spine>
</package>
"""


def _build(books: list, now: datetime, *, with_cover: bool) -> bytes:
    title = naming.COLLECTION_STEM if with_cover else f"《{books[0].title}》"
    identifier = "urn:uuid:" + str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"kindle-clippings:{title}:{now:%Y%m%d%H%M%S}")
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        # mimetype 必须是第一个条目且不压缩，否则阅读器不认这个包
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o644 << 16
        zf.writestr(info, "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", _opf(books, identifier, title, now, with_cover=with_cover))
        zf.writestr("OEBPS/style.css", CSS)
        zf.writestr("OEBPS/nav.xhtml", _nav_xhtml(books, with_cover=with_cover))
        zf.writestr("OEBPS/toc.ncx", _ncx(books, identifier, title, with_cover=with_cover))
        if with_cover:
            zf.writestr("OEBPS/cover.xhtml", _cover_xhtml(books, now))
        for i, book in enumerate(books, 1):
            zf.writestr(f"OEBPS/book-{i:02d}.xhtml", _book_xhtml(book, i))
    return buffer.getvalue()


# ------------------------------------------------------------------ 对外接口


def single_epub(book, now: datetime | None = None) -> tuple[str, bytes]:
    now = now or datetime.now()
    return naming.book_filename(book.title, "epub"), _build([book], now, with_cover=False)


def merged_epub(books: list, now: datetime | None = None) -> tuple[str, bytes]:
    now = now or datetime.now()
    return naming.collection_filename("epub", now), _build(books, now, with_cover=True)


def epub_zip(books: list, now: datetime | None = None) -> tuple[str, bytes]:
    """每本一个 .epub，打成一个 zip（EPUB 本身就是压缩包，这里不再二次压缩）。"""
    now = now or datetime.now()
    names = naming.book_filenames(books, "epub")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for name, book in zip(names, books):
            zf.writestr(name, _build([book], now, with_cover=False))
    return naming.collection_filename("zip", now), buffer.getvalue()


__all__ = ["CSS", "epub_zip", "merged_epub", "single_epub"]
