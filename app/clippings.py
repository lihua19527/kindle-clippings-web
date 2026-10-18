#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剪藏的解析 + 去重接层。

这里刻意**不重新实现**去重算法，而是直接调用 ``D:\\我的读书库\\_tools\\kindle_clippings.py``
—— 那份实现是拿用户 210 本书 / 15290 条真实剪藏评估出来的，规则细节（同位置、包含 + 同页 +
区间重叠、bigram Jaccard 只作用于笔记）都写在它的 docstring 里，重写一遍只会引入分歧。

找不到原脚本时退化到 ``app/vendor/kindle_clippings.py``（同一份代码的快照），
所以这个网页项目单独拷走也能跑。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
VENDOR_DIR = APP_DIR / "vendor"
DEFAULT_TOOLS_DIR = Path(r"D:\我的读书库\_tools")

LOADED_FROM: str = ""


def _candidates() -> list[Path]:
    dirs: list[Path] = []
    # 1) 配置里指定的目录（config.json 的 clippings_lib_dir）
    try:
        from .config import CONFIG
        custom = CONFIG.get("clippings_lib_dir")
    except Exception:  # pragma: no cover - 配置缺失时不影响主流程
        custom = None
    if custom:
        dirs.append(Path(custom))
    # 2) 原项目目录：始终是「唯一真相」
    dirs.append(DEFAULT_TOOLS_DIR)
    # 3) 随项目携带的快照
    dirs.append(VENDOR_DIR)
    return dirs


def load_kindle_module():
    """导入 kindle_clippings 模块（带缓存）。"""
    global LOADED_FROM
    cached = sys.modules.get("kindle_clippings")
    if cached is not None:
        return cached

    tried: list[str] = []
    for d in _candidates():
        target = d / "kindle_clippings.py"
        tried.append(str(target))
        if not target.is_file():
            continue
        sys.path.insert(0, str(d))
        try:
            import kindle_clippings  # noqa: F401  (动态导入)
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"找到 {target} 但导入失败：{exc}") from exc
        LOADED_FROM = str(target)
        return kindle_clippings

    raise RuntimeError(
        "找不到 kindle_clippings.py，试过：\n  " + "\n  ".join(tried)
    )


# ---------------------------------------------------------------- 选项


@dataclass
class Options:
    """与命令行开关一一对应，前端的复选框直接映射到这些字段。"""

    keep_all: bool = False       # --keep-all 完全不去重
    fuzzy: bool = True           # --no-fuzzy 的反面：正文相似度合并
    fuzzy_all: bool = False      # --fuzzy-all 相似度也作用于高亮
    containment: bool = True     # --no-containment 的反面：包含 + 同页 + 区间重叠
    similarity: float = 0.85     # --similarity
    min_entries: int = 1         # --min

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "Options":
        payload = payload or {}
        opts = cls()

        def flag(name: str, default: bool) -> bool:
            raw = payload.get(name, default)
            if isinstance(raw, str):
                return raw.strip().lower() not in ("", "0", "false", "off", "no")
            return bool(raw)

        opts.keep_all = flag("keep_all", False)
        opts.fuzzy = flag("fuzzy", True)
        opts.fuzzy_all = flag("fuzzy_all", False)
        opts.containment = flag("containment", True)

        try:
            sim = float(payload.get("similarity", opts.similarity))
        except (TypeError, ValueError):
            sim = opts.similarity
        opts.similarity = min(1.0, max(0.3, sim))

        try:
            opts.min_entries = max(1, int(payload.get("min_entries", 1)))
        except (TypeError, ValueError):
            opts.min_entries = 1

        if opts.keep_all:
            # --keep-all 时其余开关没有意义
            opts.fuzzy = False
            opts.containment = False
        return opts

    def as_cli(self) -> dict[str, Any]:
        """换算成 kindle_clippings.group_by_book 的关键字参数。"""
        if self.keep_all:
            return dict(similarity=1.0, fuzzy=False, fuzzy_all=False, containment=False, enabled=False)
        if not self.fuzzy:
            # --no-fuzzy = 只按位置去重（相似度关闭、包含合并也关闭）
            return dict(
                similarity=1.0,
                fuzzy=False,
                fuzzy_all=False,
                containment=False,
                enabled=True,
            )
        return dict(
            similarity=self.similarity,
            fuzzy=True,
            fuzzy_all=self.fuzzy_all,
            containment=self.containment,
            enabled=True,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "keep_all": self.keep_all,
            "fuzzy": self.fuzzy,
            "fuzzy_all": self.fuzzy_all,
            "containment": self.containment,
            "similarity": round(self.similarity, 2),
            "min_entries": self.min_entries,
        }


# ---------------------------------------------------------------- 解析结果


class ParsedSet:
    """一次解析的结果：书列表 + 统计 + 去重明细。"""

    def __init__(self, books: list, options: Options, source_names: list[str] | None = None):
        self.books = books              # list[kindle_clippings.Book]（已过滤 min_entries）
        self.options = options
        self.sources = list(source_names or [])
        self.raw_total = sum(b.raw_count for b in books)
        self.kept_total = sum(len(b.entries) for b in books)

    @property
    def dropped_total(self) -> int:
        return self.raw_total - self.kept_total

    def book(self, book_id: int):
        if not (0 <= book_id < len(self.books)):
            raise KeyError(f"没有第 {book_id} 本书")
        return self.books[book_id]

    def to_json(self) -> dict[str, Any]:
        return {
            "options": self.options.describe(),
            "sources": self.sources,
            "stats": {
                "books": len(self.books),
                "entries": self.kept_total,
                "raw": self.raw_total,
                "dropped": self.dropped_total,
            },
            "books": [_book_json(b, i) for i, b in enumerate(self.books)],
        }


def _entry_json(entry) -> dict[str, Any]:
    return {
        "kind": entry.kind,
        "isNote": entry.kind == "笔记",
        "content": entry.content,
        "page": entry.page,
        "location": entry.location,
        "locationRaw": entry.location_raw,
        "added": entry.added,
        "seq": entry.seq,
        "timeKey": entry.time_key,
    }


def _merged_json(book) -> list[dict[str, Any]]:
    """把「被合并掉的记录」整理成前端能看懂的结构。"""
    index_by_seq = {e.seq: i for i, e in enumerate(book.entries)}
    out: list[dict[str, Any]] = []
    for kept, dropped in book.merged:
        if not dropped:
            continue
        out.append(
            {
                "keptAt": index_by_seq.get(kept.seq),
                "kept": {"kind": kept.kind, "content": kept.content},
                "page": kept.page,
                "locationRaw": kept.location_raw,
                "dropped": [
                    {"kind": d.kind, "content": d.content, "seq": d.seq} for d in dropped
                ],
            }
        )
    return out


def _book_json(book, index: int) -> dict[str, Any]:
    counts = {"标注": 0, "笔记": 0, "书签": 0}
    for e in book.entries:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    entries = [_entry_json(e) for e in book.entries]
    return {
        "id": index,
        "title": book.title,
        "author": book.author,
        "raw": book.raw_count,
        "kept": len(book.entries),
        "dropped": book.raw_count - len(book.entries),
        "counts": counts,
        "entries": entries,
        "merged": _merged_json(book),
        "firstContent": entries[0]["content"] if entries else "",
        "span": _span(entries),
    }


def _span(entries: list[dict[str, Any]]) -> str:
    """位置跨度，例如「60 – 1200」，卡片上用。写法与条目里的 location_raw 一致（不带 #）。"""
    locs = [e["location"] for e in entries if e["location"]]
    if not locs:
        return ""
    lo, hi = min(locs), max(locs)
    return f"{lo}" if lo == hi else f"{lo} – {hi}"


def analyze(text: str, options: Options | None = None, sources: list[str] | None = None) -> ParsedSet:
    """解析 + 去重 + 过滤，返回 ParsedSet。"""
    kc = load_kindle_module()
    options = options or Options()

    entries = kc.parse_clippings(text)
    if not entries:
        raise ValueError("没有解析出任何条目，请确认内容是 Kindle 的 My Clippings.txt。")

    all_books = kc.group_by_book(entries, **options.as_cli())
    books = [b for b in all_books if len(b.entries) >= options.min_entries]
    return ParsedSet(books, options, sources)


def decode_bytes(raw: bytes) -> str:
    """剪藏文件可能是 utf-8(-sig) / utf-16 / gb18030，逐个试。"""
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gb18030", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")
