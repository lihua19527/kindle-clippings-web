#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kindle_clippings.py —— 把 Kindle 的 My Clippings.txt 拆成「每本书一个 Markdown 文件」

用途：Kindle 阅读器上只能划线 + 打几个字的短标注，没法写长文。
     回家把 Kindle 用数据线插到电脑上，跑一次这个脚本，
     所有划线和笔记就变成 Obsidian / Typora 能直接用的 Markdown。

去重（重点）：
    My Clippings.txt 是「只追加」的文件，Kindle 从不修改已有记录。所以：
      1. 同一处重复划线、或重新划线，会留下多条记录；
      2. 在 Kindle 上写笔记是「逐字」记录的，每敲一下都可能生成一条新记录，
         于是同一个笔记会留下一串中间态，例如
         "现在的人属只有智人zheyi" → "…这一" → "…这一z"。
        —— 这几条正文各不相同，只比较正文是去不掉的，必须结合「位置」判断。

    本脚本的处理（默认规则，都用真实剪藏数据评估过）：
      · 同一类型 + 同一位置                → 只留一条    所有类型
      · 正文被包含 + 同页 + 位置区间重叠   → 删掉短的那条 所有类型
        「把划线重新拉长一点」的典型特征，实测每一条都是真实重复。
      · 正文相似度 ≥ 0.85                  → 只留一条    只有笔记
        Kindle 逐字记录笔记输入过程，中间态的位置可能偏移，靠这条兜住。
        不作用于高亮：书里重复出现的句子正文可能 100% 相同但位置不同，
        那是两条真实的高亮；实测在真实数据上它对高亮的收益为 0。
    保留谁：按「时间最新 → 正文最长 → 文件里最靠后」挑。

    想关掉：--no-fuzzy 只按位置去重 ｜ --keep-all 完全不去重 ｜ --report 看删了什么

用法（在电脑上）：
    # 1) 自动找 Kindle（插着数据线的时候）
    python kindle_clippings.py -o "D:\\我的读书库\\00-Inbox\\剪藏"

    # 2) 手动指定 My Clippings.txt
    python kindle_clippings.py "E:\\documents\\My Clippings.txt" -o "D:\\我的读书库\\00-Inbox\\剪藏"

    # 3) 只看看会生成什么，不写文件
    python kindle_clippings.py --dry-run

My Clippings.txt 的位置：
    Windows : 此电脑 > Kindle > documents > My Clippings.txt
    macOS   : /Volumes/Kindle/documents/My Clippings.txt

只依赖 Python 标准库。剪藏文件是「生成物」，不要在里面写文章——
写作请用 01-读书笔记\\_读书笔记模板.md 另建文件，这样重复导出不会覆盖你的文章。
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 解析规则

DELIM_RE = re.compile(r"^=+\s*$")

# 中文固件
PAGE_CN = re.compile(r"第\s*(\d+)\s*页")
LOC_CN = re.compile(r"位置\s*#?\s*([0-9]+(?:\s*-\s*[0-9]+)?)")
DATE_CN = re.compile(r"添加于\s*(.+?)\s*$")

# 英文固件
PAGE_EN = re.compile(r"on page\s+(\d+)", re.I)
LOC_EN = re.compile(r"Location\s+([0-9]+(?:\s*-\s*[0-9]+)?)", re.I)
DATE_EN = re.compile(r"Added on\s+(.+?)\s*$", re.I)

# 条目类型
KIND_RULES = [
    ("笔记", ("的笔记", "您的笔记", "Note")),
    ("书签", ("的书签", "Bookmark")),
    ("标注", ("的标注", "Highlight")),
]

NOTE_KIND = "笔记"

# 「剪贴上限」占位符：Kindle 超出剪贴上限时写进正文的提示，不是正文内容
CLIP_LIMIT_RE = re.compile(r"<\s*您已达到本内容的剪贴上限\s*>")

# 去重参数（对齐参考资料「Kindle 笔记去重工具」的默认值）
DEFAULT_SIMILARITY = 0.85   # 笔记正文 bigram Jaccard 相似度阈值
MIN_SIMILARITY_LEN = 6      # 短于该长度的碎片不参与相似度合并
MIN_CONTAINMENT_LEN = 6     # 短于该长度的正文不参与包含合并

# ---------------------------------------------------------------- 时间解析

_CN_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日(.*)$")
_CN_TIME = re.compile(r"(上午|下午|凌晨|中午)?\s*(\d{1,2})\s*[:：]\s*(\d{2})(?:\s*[:：]\s*(\d{2}))?")
_EN_DATE = re.compile(
    r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?"
    r"(?:\s*([AaPp])\.?[Mm]\.?)?"
)
# 有些工具/固件写的是 2026-05-24 09:47:16
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?")
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _stamp(year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
    """压成 YYYYMMDDHHMMSS 形式的整数，方便直接比大小。"""
    return ((((year * 100 + month) * 100 + day) * 100 + hour) * 100 + minute) * 100 + second


def parse_added_time(added: str) -> int:
    """把 Kindle 的本地化日期串解析成可比较的整数；认不出来就返回 0。

    中文：2026年7月11日星期六 下午2:20:25
    英文：Monday, May 6, 2024 9:15:00 PM
    其它：2026-05-24 09:47:16
    """
    if not added:
        return 0

    m = _ISO_DATE.search(added)
    if m:
        return _stamp(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6) or 0),
        )

    m = _CN_DATE.search(added)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = minute = second = 0
        t = _CN_TIME.search(m.group(4))
        if t:
            marker = t.group(1) or ""
            hour = int(t.group(2))
            minute = int(t.group(3))
            second = int(t.group(4) or 0)
            if marker == "下午" and hour < 12:
                hour += 12
            elif marker in ("上午", "凌晨") and hour == 12:
                hour = 0
        return _stamp(year, month, day, hour, minute, second)

    m = _EN_DATE.search(added)
    if m:
        month = _MONTHS.get(m.group(1).lower()[:3], 0)
        if month:
            hour = int(m.group(4))
            meridiem = (m.group(7) or "").lower()
            if meridiem == "p" and hour < 12:
                hour += 12
            elif meridiem == "a" and hour == 12:
                hour = 0
            return _stamp(
                int(m.group(3)), month, int(m.group(2)),
                hour, int(m.group(5)), int(m.group(6) or 0),
            )
    return 0


# ---------------------------------------------------------------- 数据模型


@dataclass
class Entry:
    title: str          # 书名（已去掉作者部分）
    author: str         # 作者，可能为空
    raw_title: str      # 原始第一行
    kind: str           # 标注 / 笔记 / 书签
    content: str
    page: str = ""
    location: int = 0
    location_raw: str = ""
    added: str = ""
    seq: int = 0        # 在文件里出现的顺序（My Clippings.txt 只追加，越靠后越新）
    time_key: int = 0   # 由 added 解析出来的时间戳，认不出来为 0


@dataclass
class Book:
    title: str
    author: str
    entries: list[Entry] = field(default_factory=list)
    raw_count: int = 0                                          # 去重前的条数
    merged: list[tuple[Entry, list[Entry]]] = field(default_factory=list)


# ---------------------------------------------------------------- 具体解析


def _split_title(line: str) -> tuple[str, str]:
    """'活着 (余华)' -> ('活着', '余华')；没有括号则作者留空。"""
    line = line.strip()
    if line.endswith(")") and "(" in line:
        title, _, author = line.rpartition("(")
        author = author[:-1].strip()
        if title.strip():
            return title.strip(), author
    return line, ""


def _detect_kind(meta: str) -> str:
    for kind, keys in KIND_RULES:
        if any(k in meta for k in keys):
            return kind
    return "标注"


def _parse_meta(meta: str) -> dict:
    out: dict = {"page": "", "location_raw": "", "location": 0, "added": ""}

    m = PAGE_CN.search(meta) or PAGE_EN.search(meta)
    if m:
        out["page"] = m.group(1)

    m = LOC_CN.search(meta) or LOC_EN.search(meta)
    if m:
        raw = re.sub(r"\s+", "", m.group(1))
        out["location_raw"] = raw
        out["location"] = int(raw.split("-")[0])

    m = DATE_CN.search(meta) or DATE_EN.search(meta)
    if m:
        out["added"] = m.group(1).strip()

    return out


def _is_meta_line(line: str) -> bool:
    """判断这一行是不是 Kindle 写的元数据行（'- 您在第 12 页…的标注 | 添加于 …'）。"""
    s = line.strip()
    if not s.startswith("-"):
        return False
    if s.startswith("- "):
        s = s[2:]
    has_kind = any(k in s for k in ("标注", "笔记", "书签", "Highlight", "Note", "Bookmark"))
    has_pos = bool(
        PAGE_CN.search(s) or LOC_CN.search(s) or PAGE_EN.search(s) or LOC_EN.search(s)
    )
    return has_kind and has_pos


def _clean_content(text: str) -> str:
    """剔除「剪贴上限」占位符并按行规整正文。"""
    text = CLIP_LIMIT_RE.sub(" ", text)
    lines = [re.sub(r"[ \t]+$", "", line) for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _make_entry(raw_title: str, meta: str, content: str, seq: int) -> Entry:
    title, author = _split_title(raw_title)
    info = _parse_meta(meta)
    return Entry(
        title=title,
        author=author,
        raw_title=raw_title,
        kind=_detect_kind(meta),
        content=content,
        page=info["page"],
        location=info["location"],
        location_raw=info["location_raw"],
        added=info["added"],
        seq=seq,
        time_key=parse_added_time(info["added"]),
    )


def parse_clippings(text: str) -> list[Entry]:
    """
    把 My Clippings.txt 的全文解析成条目列表。

    真实文件在不同固件/语言下有细微差别，这里不依赖固定行号，而是先按分隔线
    （==========）切块，再判断每块是「头部块」（书名 + 元数据）还是「正文块」：

        布局 A：书名 / 元数据 / 正文 / ==========
        布局 B：书名 / 元数据 / ========== / 正文 / ==========

    两种都能解析。书签（Bookmark）和空笔记没有正文，会被自动丢弃；
    正文里的空行和多段落划线会原样保留。
    """
    # 1) 按分隔线切块
    raw_chunks: list[list[str]] = [[]]
    for line in text.splitlines():
        if DELIM_RE.match(line):
            raw_chunks.append([])
        else:
            raw_chunks[-1].append(line)

    chunks: list[list[str]] = []
    for raw in raw_chunks:
        lines = [l.strip() for l in raw]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        if lines:
            chunks.append(lines)

    def is_header(chunk: list[str]) -> bool:
        """头部块 = 第 1 行是书名、第 2 行是元数据。"""
        return (
            len(chunk) >= 2
            and not _is_meta_line(chunk[0])
            and _is_meta_line(chunk[1])
        )

    # 2) 头部块 + 正文块配对
    entries: list[Entry] = []
    pending: tuple[str, str] | None = None  # (书名行, 元数据行)
    last_title = ""

    for chunk in chunks:
        if is_header(chunk):
            # 上一个悬空的头部块没有正文（书签/空笔记）-> 直接丢弃
            pending = None
            last_title = chunk[0]
            if len(chunk) >= 3:
                content = _clean_content("\n".join(chunk[2:]))
                if content:
                    entries.append(_make_entry(chunk[0], chunk[1], content, len(entries)))
            else:
                pending = (chunk[0], chunk[1])
            continue

        # 正文块
        content = _clean_content("\n".join(chunk))
        if not content:
            continue

        if pending is not None:
            entries.append(_make_entry(pending[0], pending[1], content, len(entries)))
            pending = None
        else:
            # 没有配到元数据的孤儿正文，归到最近一本书，别丢
            entries.append(
                Entry(last_title, "", last_title, "标注", content, seq=len(entries))
            )

    return entries


# ---------------------------------------------------------------- 去重

# ---- 正文归一化与相似度（bigram Jaccard，与参考资料一致）


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a) + len(b) - intersection
    return 0.0 if union == 0 else intersection / union


class _UnionFind:
    """并查集：把「同一条剪藏」的记录传递性地并成一组。"""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def _better(candidate: Entry, best: Entry) -> bool:
    """挑「最新、最完整」的一条：时间最新 → 正文最长 → 文件里最靠后。"""
    if candidate.time_key != best.time_key:
        return candidate.time_key > best.time_key
    if len(candidate.content) != len(best.content):
        return len(candidate.content) > len(best.content)
    return candidate.seq > best.seq


def _location_end(e: Entry) -> int:
    """位置区间的终点。"123-127" -> 127；"160" -> 160。"""
    if not e.location_raw:
        return e.location
    tail = e.location_raw.split("-")[-1]
    return int(tail) if tail.isdigit() else e.location


def _same_passage(a: Entry, b: Entry) -> bool:
    """两条记录是否指向书里同一段文字：同页，且位置区间重叠。

    真实数据里「把划线重新拉长一点」正是这个特征，例如
    第 8 页 #69-70 与 #68-70、第 342 页 #4764-4766 与 #4763-4766。
    要求区间重叠，是为了避免把「前面某页的短句」和「后面某页恰好包含它的长段」
    误判成同一段 —— 那种只是文字偶然包含，不是同一条剪藏。
    """
    if a.page != b.page:
        return False
    if not a.location or not b.location:
        return False
    return a.location <= _location_end(b) and b.location <= _location_end(a)


def _dedupe_one_kind(
    part: list[Entry],
    similarity: float,
    use_similarity: bool,
    use_containment: bool,
) -> tuple[list[Entry], list[tuple[Entry, list[Entry]]]]:
    """对同一类型的条目去重，返回（保留的条目, [(保留的, [被合并掉的…])]）。"""
    n = len(part)
    if n <= 1:
        return list(part), []

    uf = _UnionFind(n)
    contents = [_normalize(e.content) for e in part]

    # 规则 1：同一位置 = 同一条剪藏。
    # 位置只取起点（"123-125" 和 "123-127" 视为同一处的重复划线）。
    first_by_key: dict[tuple, int] = {}
    for i, e in enumerate(part):
        key = (e.page, e.location) if e.location else ("__noloc__", i)
        if key in first_by_key:
            uf.union(i, first_by_key[key])
        else:
            first_by_key[key] = i

    # 规则 2：正文高度相似 = 同一条（覆盖逐字输入时位置发生偏移的情况）
    if use_similarity:
        cache: list[set[str] | None] = [None] * n
        for i in range(n):
            len_i = len(contents[i])
            if len_i < MIN_SIMILARITY_LEN:
                continue
            for j in range(i + 1, n):
                len_j = len(contents[j])
                if len_j < MIN_SIMILARITY_LEN:
                    continue
                if uf.find(i) == uf.find(j):
                    continue
                # 必要条件预筛：Jaccard ≥ t 必然要求 短/长 ≥ t
                if min(len_i, len_j) / max(len_i, len_j) < similarity:
                    continue
                if cache[i] is None:
                    cache[i] = _bigrams(contents[i])
                if cache[j] is None:
                    cache[j] = _bigrams(contents[j])
                if _jaccard(cache[i], cache[j]) >= similarity:
                    uf.union(i, j)

    # 规则 3：分组，每组挑一条代表
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    merged_into: dict[int, list[Entry]] = {}
    kept_idx: list[int] = []
    for idxs in groups.values():
        best = idxs[0]
        for idx in idxs[1:]:
            if _better(part[idx], part[best]):
                best = idx
        kept_idx.append(best)
        if len(idxs) > 1:
            merged_into[best] = [part[i] for i in sorted(idxs) if i != best]

    # 规则 4：包含合并 —— 正文完全出现在另一条更长、且指向同一段文字的正文里
    if use_containment:
        reps = sorted(kept_idx)
        drop: set[int] = set()
        for pos, i in enumerate(reps):
            a = contents[i]
            if len(a) < MIN_CONTAINMENT_LEN:
                continue
            for j in reps[pos + 1:]:
                b = contents[j]
                if len(b) <= len(a) or a not in b:
                    continue
                if not _same_passage(part[i], part[j]):
                    continue
                drop.add(i)
                merged_into.setdefault(j, []).append(part[i])
                break
        kept_idx = [i for i in reps if i not in drop]

    detail = [(part[k], v) for k, v in merged_into.items() if v]
    return [part[i] for i in sorted(kept_idx)], detail


def dedupe(
    entries: list[Entry],
    similarity: float = DEFAULT_SIMILARITY,
    fuzzy: bool = True,
    fuzzy_all: bool = False,
    containment: bool = True,
    enabled: bool = True,
) -> tuple[list[Entry], list[tuple[Entry, list[Entry]]]]:
    """按类型分开去重。

    规则与生效范围（默认值，均由真实数据评估过）：
      · 同一类型 + 同一位置 → 只保留一条        所有类型
      · 正文包含 + 同页 + 区间重叠 → 删短的    所有类型   （重新划线的典型特征）
      · 正文相似度 ≥ 阈值 → 只保留一条          只有笔记
        相似度合并默认不作用于高亮：书里重复出现的句子（诗句、口头禅）正文可能
        100% 相同但位置不同，那是两条真实的高亮；而且实测在你的数据上它收益为 0。
        想对高亮也开，传 fuzzy_all=True。
    enabled=False 时原样返回（对应 --keep-all）。
    """
    if not enabled:
        return list(entries), []

    kept: list[Entry] = []
    detail: list[tuple[Entry, list[Entry]]] = []
    for kind in sorted({e.kind for e in entries}):
        part = [e for e in entries if e.kind == kind]
        use_similarity = fuzzy and (fuzzy_all or kind == NOTE_KIND)
        kept_part, part_detail = _dedupe_one_kind(
            part, similarity, use_similarity, containment
        )
        kept.extend(kept_part)
        detail.extend(part_detail)
    return kept, detail


def group_by_book(
    entries: list[Entry],
    similarity: float = DEFAULT_SIMILARITY,
    fuzzy: bool = True,
    fuzzy_all: bool = False,
    containment: bool = True,
    enabled: bool = True,
) -> list[Book]:
    books: dict[str, Book] = {}
    for e in entries:
        key = e.raw_title or e.title
        book = books.get(key)
        if book is None:
            book = Book(title=e.title, author=e.author)
            books[key] = book
        book.entries.append(e)

    result: list[Book] = []
    for b in books.values():
        b.raw_count = len(b.entries)
        kept, detail = dedupe(b.entries, similarity, fuzzy, fuzzy_all, containment, enabled)
        # 按位置排序，位置未知的排最后；位置相同时按文件顺序
        kept.sort(key=lambda e: (e.location == 0, e.location, e.seq))
        b.entries = kept
        b.merged = detail
        result.append(b)

    result.sort(key=lambda b: b.title)
    return result


# ---------------------------------------------------------------- 输出

ILLEGAL = r'[\\/:*?"<>|\r\n\t]'


def safe_filename(name: str, limit: int = 80) -> str:
    name = re.sub(ILLEGAL, "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:limit] or "未命名").strip()


def render_book(book: Book, now: dt.datetime) -> str:
    author = book.author or "未知"
    dropped = book.raw_count - len(book.entries)

    lines = [
        "---",
        "type: kindle-剪藏",
        f'书名: "{book.title}"',
        f'作者: "{author}"',
        f"条数: {len(book.entries)}",
        f"导出时间: {now:%Y-%m-%d %H:%M}",
    ]
    if dropped > 0:
        lines.append(f"去重丢弃: {dropped}")
    lines += [
        "tags:",
        "  - 读书剪藏",
        "---",
        "",
        f"# 《{book.title}》",
        "",
        f"> 作者：{author} ｜ {len(book.entries)} 条 ｜ 导出于 {now:%Y-%m-%d}",
        "",
    ]
    if dropped > 0:
        lines += [
            f"> [!note] 已自动去重 {dropped} 条重复记录"
            "（同一处的重复标注、Kindle 逐字记录的笔记中间态）。",
            "",
        ]
    lines += [
        "> [!info] 这是脚本生成的原始素材，请勿在此文件里写文章。",
        "> 写作用 01-读书笔记\\_读书笔记模板.md 另建《书名》读书笔记.md。",
        "",
        "---",
        "",
        "## 剪藏原文",
        "",
    ]

    for i, e in enumerate(book.entries, 1):
        # 笔记的标题加 ==…== 高亮（Obsidian 的高亮语法），方便一眼区分标注和笔记
        heading = f"{i}. {e.kind}"
        if e.kind == NOTE_KIND:
            heading = f"=={heading}=="
        lines.append(f"### {heading}")
        lines.append("")
        for para in e.content.splitlines():
            lines.append(f"> {para}" if para.strip() else ">")
        lines.append("")

        bits = []
        if e.page:
            bits.append(f"第 {e.page} 页")
        if e.location_raw:
            bits.append(f"位置 {e.location_raw}")
        if e.added:
            bits.append(e.added)
        bits.append(e.kind)
        lines.append("*" + " · ".join(bits) + "*")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_index(books: list[Book], now: dt.datetime) -> str:
    total = sum(len(b.entries) for b in books)
    raw = sum(b.raw_count for b in books)
    lines = [
        "---",
        "type: 剪藏书单",
        f"更新时间: {now:%Y-%m-%d %H:%M}",
        "---",
        "",
        "# Kindle 剪藏书单",
        "",
        f"共 {len(books)} 本，{total} 条剪藏"
        + (f"（已去重 {raw - total} 条重复记录）。" if raw > total else "。"),
        "",
        "| 书名 | 作者 | 条数 |",
        "| --- | --- | --- |",
    ]
    for b in books:
        lines.append(f"| [[《{b.title}》]] | {b.author or '未知'} | {len(b.entries)} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 找 Kindle


def detect_clippings() -> Path | None:
    """自动寻找插着的 Kindle。"""
    candidates: list[Path] = []

    if sys.platform.startswith("win"):
        import string

        for letter in string.ascii_uppercase:
            candidates.append(Path(f"{letter}:\\documents\\My Clippings.txt"))
    else:
        candidates.append(Path("/Volumes/Kindle/documents/My Clippings.txt"))
        volumes = Path("/Volumes")
        if volumes.is_dir():
            for v in volumes.iterdir():
                candidates.append(v / "documents" / "My Clippings.txt")

    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def read_text(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gb18030", "cp1252"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 主流程


def _print_report(books: list[Book]) -> None:
    any_merged = False
    for b in books:
        if not b.merged:
            continue
        any_merged = True
        print(f"\n《{b.title}》被合并的记录：")
        for kept, dropped in b.merged:
            where = f"第 {kept.page} 页 位置 {kept.location_raw}".strip()
            print(f"  · {where} 保留「{kept.content.splitlines()[0][:30]}」")
            for d in dropped:
                first = d.content.splitlines()[0] if d.content else ""
                print(f"      丢弃({d.kind}) 「{first[:30]}」")
    if not any_merged:
        print("\n没有需要合并的重复记录。")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把 Kindle My Clippings.txt 拆成每本书一个 Markdown 文件（含自动去重）"
    )
    ap.add_argument("clippings", nargs="?", help="My Clippings.txt 路径（省略则自动查找 Kindle）")
    ap.add_argument("-o", "--out", default=".", help="输出目录，默认当前目录")
    ap.add_argument("--min", type=int, default=1, help="只导出条数 >= N 的书，默认 1")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    ap.add_argument(
        "--similarity", type=float, default=DEFAULT_SIMILARITY,
        help=f"笔记正文相似度阈值 0~1，默认 {DEFAULT_SIMILARITY}；设为 1 关闭相似度合并",
    )
    ap.add_argument(
        "--no-fuzzy", action="store_true",
        help="只按位置去重（等价于 --similarity 1 --no-containment）",
    )
    ap.add_argument(
        "--no-containment", action="store_true",
        help="关闭「同页+区间重叠+正文被包含」的合并",
    )
    ap.add_argument(
        "--fuzzy-all", action="store_true",
        help="把相似度合并也应用到高亮（默认只对笔记生效）",
    )
    ap.add_argument("--keep-all", action="store_true", help="完全不去重（排查问题时用）")
    ap.add_argument("--report", action="store_true", help="打印每条被合并掉的记录明细")
    args = ap.parse_args(argv)

    src = Path(args.clippings) if args.clippings else detect_clippings()
    if src is None:
        print("[×] 没找到 My Clippings.txt。请插上 Kindle，或手动把路径作为参数传入。", file=sys.stderr)
        return 2
    if not src.is_file():
        print(f"[×] 文件不存在：{src}", file=sys.stderr)
        return 2

    print(f"[√] 读取：{src}")
    entries = parse_clippings(read_text(src))
    if not entries:
        print("[!] 没有解析出任何条目。请确认这是 Kindle 的 My Clippings.txt。", file=sys.stderr)
        return 1

    similarity = 1.0 if args.no_fuzzy else args.similarity
    fuzzy = not args.no_fuzzy
    containment = not (args.no_fuzzy or args.no_containment)

    all_books = group_by_book(
        entries,
        similarity=similarity,
        fuzzy=fuzzy,
        fuzzy_all=args.fuzzy_all,
        containment=containment,
        enabled=not args.keep_all,
    )
    books = [b for b in all_books if len(b.entries) >= args.min]
    now = dt.datetime.now()

    raw_total = sum(b.raw_count for b in all_books)
    kept_total = sum(len(b.entries) for b in all_books)
    print(
        f"[√] 解析出 {raw_total} 条剪藏，来自 {len(all_books)} 本书"
        f"（去重丢弃 {raw_total - kept_total} 条）"
    )
    for b in books:
        extra = (
            f"  ← 原始 {b.raw_count} 条，去重 {b.raw_count - len(b.entries)} 条"
            if b.raw_count > len(b.entries)
            else ""
        )
        print(f"    - 《{b.title}》 {b.author or '未知作者'}：{len(b.entries)} 条{extra}")

    if args.report:
        _print_report(books)

    if args.dry_run:
        print("\n[dry-run] 未写入任何文件。")
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for b in books:
        target = out_dir / f"《{safe_filename(b.title)}》.md"
        target.write_text(render_book(b, now), encoding="utf-8", newline="\n")
        print(f"[√] 写入 {target}")

    index = out_dir / "_剪藏书单.md"
    index.write_text(render_index(books, now), encoding="utf-8", newline="\n")
    print(f"[√] 写入 {index}")
    print("\n完成。剪藏文件是素材，写作请另建文件（用 01-读书笔记\\_读书笔记模板.md）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
