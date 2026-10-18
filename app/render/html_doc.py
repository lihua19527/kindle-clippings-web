#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印用 HTML —— PDF 的中间产物。

为什么绕一层 HTML：中文字体的排版、断行、标点挤压交给浏览器引擎做，比自己用 PDF 库
拼字准确得多，而且导出的 PDF 里文字是可选中、可搜索的真实文本（字体随文件嵌入）。
样式上沿用网页那套 Bauhaus 语汇（黑白纸面 + 红黄蓝三色、细分割线），但为了打印可读性
把色彩压到只剩「笔记 = 黄色块」这一处强调。
"""

from __future__ import annotations

from datetime import datetime
from html import escape

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0; color: #20201e; background: #fff;
  font-family: "Microsoft YaHei", "PingFang SC", "Source Han Sans SC", sans-serif;
  font-size: 10.5pt; line-height: 1.72;
}
.sheet { padding: 0; }

/* ---------- 封面 ---------- */
.cover { height: 240mm; display: flex; flex-direction: column; break-after: page; }
.cover-top { display: flex; align-items: center; justify-content: space-between;
  border-bottom: 2px solid #20201e; padding-bottom: 10mm; }
.cover-mark { width: 13mm; height: 13mm; background: #d93625; color: #fff;
  display: grid; place-items: center; font-size: 8pt; font-weight: 700; letter-spacing: -.4pt; }
.cover-kicker { font-size: 8.5pt; letter-spacing: .18em; color: #65645e; }
.cover-body { margin-top: 34mm; }
.cover h1 { margin: 0; font-size: 40pt; line-height: 1.16; letter-spacing: -.04em; font-weight: 800; }
.cover-lede { margin: 8mm 0 0; font-size: 11pt; color: #65645e; }
.cover-stats { display: flex; margin-top: 20mm; border-top: 1px solid #cccac1; border-bottom: 1px solid #cccac1; }
.cover-stats div { flex: 1; padding: 6mm 0 6mm 6mm; border-left: 1px solid #cccac1; }
.cover-stats div:first-child { border-left: 0; padding-left: 0; }
.cover-stats strong { display: block; font-size: 24pt; font-weight: 700; letter-spacing: -.03em; }
.cover-stats span { font-size: 8pt; color: #65645e; }
.cover-foot { margin-top: auto; display: flex; justify-content: space-between;
  align-items: flex-end; font-size: 8.5pt; color: #65645e; border-top: 1px solid #cccac1; padding-top: 4mm; }
.shapes { letter-spacing: 6pt; color: #d93625; font-size: 9pt; }

/* ---------- 目录 ---------- */
.toc { break-after: page; }
.toc h2 { font-size: 18pt; margin: 0 0 6mm; letter-spacing: -.02em; }
.toc ol { margin: 0; padding: 0; list-style: none; border-top: 2px solid #20201e; }
.toc li { display: flex; align-items: baseline; gap: 4mm;
  padding: 3.2mm 0; border-bottom: 1px solid #cccac1; }
.toc a { color: inherit; text-decoration: none; }
.toc .no { width: 14mm; font-size: 9pt; color: #234bc4; font-weight: 700; }
.toc .name { flex: 1; font-weight: 700; }
.toc .sub { font-size: 8.5pt; color: #65645e; }

/* ---------- 每本书 ---------- */
.book { break-inside: auto; }
.book + .book { break-before: page; }
.book-head { border-bottom: 2px solid #20201e; padding-bottom: 4mm; margin-bottom: 7mm; }
.book-head .kicker { font-size: 8pt; letter-spacing: .16em; color: #65645e; margin: 0 0 2mm; }
.book-head h2 { margin: 0; font-size: 21pt; line-height: 1.24; letter-spacing: -.03em; }
.book-head .meta { margin: 3mm 0 0; font-size: 9pt; color: #65645e; }
.book-head .meta b { color: #20201e; }
.book-head .meta .drop { color: #d93625; }

.entry { margin: 0 0 6mm; break-inside: avoid; }
.entry-head { display: flex; align-items: baseline; gap: 3mm; margin-bottom: 1.6mm; }
.entry-no { font-size: 8.5pt; color: #65645e; font-variant-numeric: tabular-nums; min-width: 8mm; }
.entry-kind { font-size: 9pt; font-weight: 700; padding: .6mm 2mm; }
.entry-kind.note { background: #f4c434; color: #20201e; }
.entry-kind.highlight { border: 1px solid #20201e; }
.entry-kind.bookmark { border: 1px solid #65645e; color: #65645e; }
.quote { margin: 0; padding: 2.6mm 0 2.6mm 5mm; border-left: 2px solid #cccac1; background: #faf9f5; }
.quote.note { background: #f8e6a3; border-left: 2px solid #20201e; }
.quote p { margin: 0 0 1.4mm; }
.quote p:last-child { margin-bottom: 0; }
.entry-meta { margin: 1.6mm 0 0 0; font-size: 7.8pt; color: #65645e; }
"""


def _e(text: str) -> str:
    return escape(text, quote=False)


def _meta(entry) -> str:
    bits = []
    if entry.page:
        bits.append(f"第 {entry.page} 页")
    if entry.location_raw:
        bits.append(f"位置 {entry.location_raw}")
    if entry.added:
        bits.append(entry.added)
    return " · ".join(_e(b) for b in bits)


def _kind_class(kind: str) -> str:
    return {"笔记": "note", "书签": "bookmark"}.get(kind, "highlight")


def _entry_html(index: int, entry) -> str:
    is_note = entry.kind == "笔记"
    paras = [f"<p>{_e(line) if line.strip() else '&nbsp;'}</p>" for line in entry.content.splitlines()]
    return f"""<div class="entry">
  <div class="entry-head">
    <span class="entry-no">{index:02d}</span>
    <span class="entry-kind {_kind_class(entry.kind)}">{_e(entry.kind)}</span>
  </div>
  <blockquote class="quote{' note' if is_note else ''}">{''.join(paras)}</blockquote>
  <p class="entry-meta">{_meta(entry)}</p>
</div>"""


def _book_html(book, ordinal: int) -> str:
    dropped = book.raw_count - len(book.entries)
    drop_html = f'<span class="drop"> · 已去重 {dropped} 条</span>' if dropped > 0 else ""
    entries = "".join(_entry_html(i, e) for i, e in enumerate(book.entries, 1))
    return f"""<section class="book" id="book-{ordinal}">
  <div class="book-head">
    <p class="kicker">BOOK {ordinal:02d}</p>
    <h2>《{_e(book.title)}》</h2>
    <p class="meta"><b>{_e(book.author or '未知作者')}</b> · {len(book.entries)} 条{drop_html}</p>
  </div>
  {entries}
</section>"""


def _cover_html(books: list, now: datetime) -> str:
    total = sum(len(b.entries) for b in books)
    raw = sum(b.raw_count for b in books)
    return f"""<section class="cover">
  <div class="cover-top">
    <div class="cover-mark">K/C</div>
    <div class="cover-kicker">KINDLE CLIPPINGS · 本地整理</div>
  </div>
  <div class="cover-body">
    <h1>剪藏全书</h1>
    <p class="cover-lede">把 Kindle 上的划线与笔记，按书整理到一起。</p>
    <div class="cover-stats">
      <div><strong>{len(books)}</strong><span>本书</span></div>
      <div><strong>{total}</strong><span>条剪藏</span></div>
      <div><strong>{raw - total}</strong><span>条重复已去</span></div>
    </div>
  </div>
  <div class="cover-foot">
    <span>导出于 {now:%Y-%m-%d %H:%M}</span>
    <span class="shapes">● ▲ ■</span>
  </div>
</section>"""


def _toc_html(books: list) -> str:
    items = "".join(
        f'<li><a class="no" href="#book-{i}">{i:02d}</a>'
        f'<a class="name" href="#book-{i}">《{_e(b.title)}》</a>'
        f'<span class="sub">{_e(b.author or "未知作者")} · {len(b.entries)} 条</span></li>'
        for i, b in enumerate(books, 1)
    )
    return f'<section class="toc"><h2>目录</h2><ol>{items}</ol></section>'


def document_html(books: list, now: datetime, *, cover: bool) -> str:
    body = []
    if cover:
        body.append(_cover_html(books, now))
        body.append(_toc_html(books))
    for i, book in enumerate(books, 1):
        body.append(_book_html(book, i))
    title = "读书笔记合集" if cover else f"《{books[0].title}》"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{_e(title)}</title>
<style>{CSS}</style></head>
<body class="sheet">{''.join(body)}</body></html>"""
