#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kindle_clippings.py 的回归测试。改完解析器跑一下这个，确保没弄坏别的地方：

    python "D:\\我的读书库\\_tools\\test_kindle_clippings.py"

只依赖标准库，不联网，不写任何文件。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARSER = HERE / "kindle_clippings.py"

spec = importlib.util.spec_from_file_location("kindle_clippings", PARSER)
assert spec and spec.loader, f"找不到解析器：{PARSER}"
kc = importlib.util.module_from_spec(spec)
sys.modules["kindle_clippings"] = kc   # dataclass 需要能反查自己的模块
spec.loader.exec_module(kc)

PASS: list[str] = []
FAIL: list[tuple[str, object, object]] = []


def check(name: str, got: object, want: object) -> None:
    if got == want:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append((name, got, want))
        print(f"[FAIL] {name}\n        实际={got!r}\n        期望={want!r}")


def parse(raw: str):
    return kc.parse_clippings(raw)


def dedup(raw: str, **kw):
    return kc.dedupe(parse(raw), **kw)


def kept_count(raw: str, **kw) -> int:
    """走真实代码路径：group_by_book。"""
    return sum(len(b.entries) for b in kc.group_by_book(parse(raw), **kw))


def raw_count(raw: str) -> int:
    return sum(b.raw_count for b in kc.group_by_book(parse(raw), enabled=False))


# ---------------------------------------------------------------- 夹具

# 3 条同位置的笔记 = 同一个笔记的逐字输入中间态（用户实际遇到的问题）
# + 同位置的 1 条标注（不能被笔记吞掉）
# + 1 组「同页 + 区间重叠 + 正文被包含」的高亮（默认应该合并：重新划线的特征）
# + 1 组「跨页 + 文字被包含」的高亮（默认不该合并：只是文字偶然包含）
# + 1 条独立笔记
DUP = """人类简史 (尤瓦尔·赫拉利)
- 您在第 15 页（位置 #160）的笔记 | 添加于 2026年7月11日星期六 下午2:20:22
==========
现在的人属只有智人zheyi
==========
人类简史 (尤瓦尔·赫拉利)
- 您在第 15 页（位置 #160）的笔记 | 添加于 2026年7月11日星期六 下午2:20:24
==========
现在的人属只有智人这一
==========
人类简史 (尤瓦尔·赫拉利)
- 您在第 15 页（位置 #160）的笔记 | 添加于 2026年7月11日星期六 下午2:20:25
==========
现在的人属只有智人这一z
==========
人类简史 (尤瓦尔·赫拉利)
- 您在第 15 页（位置 #160）的标注 | 添加于 2026年7月11日星期六 下午2:20:20
==========
现在的人属只有智人一种。
==========
人类简史 (尤瓦尔·赫拉利)
- 您在第 40 页（位置 #500-510）的标注 | 添加于 2026年5月25日星期日 上午12:08:06
==========
宇宙的物质、能量、时间和空间才成了现在的样子。
==========
人类简史 (尤瓦尔·赫拉利)
- 您在第 40 页（位置 #498-510）的标注 | 添加于 2026年5月25日星期日 上午12:08:20
==========
大约在135亿年前，经过所谓的“大爆炸”之后，宇宙的物质、能量、时间和空间才成了现在的样子。
==========
人类简史 (尤瓦尔·赫拉利)
- 您在第 90 页（位置 #1200）的笔记 | 添加于 2026年7月13日星期一 上午10:00:00
==========
这是另一条独立笔记
==========
人类简史 (尤瓦尔·赫拉利)
- 您在第 41 页（位置 #600-610）的标注 | 添加于 2026年5月26日星期二 上午9:00:00
==========
认知革命是这一切的起点。
==========
人类简史 (尤瓦尔·赫拉利)
- 您在第 45 页（位置 #700-720）的标注 | 添加于 2026年5月26日星期二 上午9:05:00
==========
认知革命是这一切的起点。它改变了智人的合作方式。
==========
"""

# 布局 B（分隔符在正文之前）+ 英文固件
LAYOUT_B = """Sapiens: A Brief History of Humankind (Yuval Noah Harari)
- Your Highlight on page 45 | Location 678-680 | Added on Monday, May 6, 2024 9:15:00 PM
==========
The real question is not whether we can, but whether we should.
==========
A Book Without Author
- Your Note on page 3 | Location 20 | Added on Tuesday, May 7, 2024 7:00:00 AM
==========
这是我的短标注
==========
"""

# 书签（无正文，应丢弃）+ 同一起点不同区间的重复划线 + 剪贴上限占位符
MISC = """活着 (余华)
- 您在第 5 页（位置 #60）的书签 | 添加于 2024年3月1日星期五 下午9:00:00
==========
活着 (余华)
- 您在第 12 页（位置 #123-125）的标注 | 添加于 2024年3月1日星期五 下午10:30:00
==========
人是为了活着本身而活着。
==========
活着 (余华)
- 您在第 12 页（位置 #123-127）的标注 | 添加于 2024年3月1日星期五 下午10:31:00
==========
人是为了活着本身而活着。
==========
活着 (余华)
- 您在第 3 页（位置 #30）的标注 | 添加于 2024年3月1日星期五 下午9:10:00
==========
正文内容<您已达到本内容的剪贴上限>
==========
"""


print("=" * 66)
print("1. 默认行为：位置去重 + 包含合并 + 只对笔记做相似度合并")
print("=" * 66)
kept, detail = dedup(DUP)
check("9 条 → 6 条", len(kept), 6)
notes160 = [e for e in kept if e.kind == "笔记" and e.location == 160]
check("位置160的笔记只剩1条", len(notes160), 1)
check("保留最新最完整的那条", notes160[0].content, "现在的人属只有智人这一z")
check("同位置的标注没被笔记吞掉",
      len([e for e in kept if e.kind == "标注" and e.location == 160]), 1)
check("同页+区间重叠+被包含的高亮合并为1条（第40页）",
      len([e for e in kept if e.page == "40"]), 1)
check("跨页的'文字被包含'不合并（第41、45页各留1条）",
      (len([e for e in kept if e.page == "41"]), len([e for e in kept if e.page == "45"])),
      (1, 1))
check("独立笔记保留", len([e for e in kept if e.location == 1200]), 1)
check("合并明细记录了 2 组", len(detail), 2)

print()
print("=" * 66)
print("2. --no-containment：关掉包含合并，只按位置 + 相似度")
print("=" * 66)
kept2, _ = dedup(DUP, containment=False)
check("9 条 → 7 条（第40页那组被保留）", len(kept2), 7)
check("第40页保留 2 条", len([e for e in kept2 if e.page == "40"]), 2)

print()
print("=" * 66)
print("3. --no-fuzzy：只按位置去重（走真实代码路径 group_by_book）")
print("=" * 66)
check("9 条 → 7 条", kept_count(DUP, similarity=1.0, fuzzy=False, containment=False), 7)

print()
print("=" * 66)
print("4. --keep-all：完全不去重（走真实代码路径）")
print("=" * 66)
check("9 条原样保留", kept_count(DUP, enabled=False), 9)
check("raw_count 等于保留数", raw_count(DUP), 9)
check("keep_all 时合并明细为空",
      sum(len(b.merged) for b in kc.group_by_book(parse(DUP), enabled=False)), 0)

print()
print("=" * 66)
print("5. 时间解析（决定'哪条更新')".replace(")", "）"))
print("=" * 66)
check("中文 下午2:20:25", kc.parse_added_time("2026年7月11日星期六 下午2:20:25"), 20260711142025)
check("中文 下午10:30:00 不越界", kc.parse_added_time("2024年3月1日星期五 下午10:30:00"), 20240301223000)
check("中文 上午9:00:00", kc.parse_added_time("2026年7月12日星期日 上午9:00:00"), 20260712090000)
check("中文 上午12:00 → 0 点", kc.parse_added_time("2026年7月12日星期日 上午12:00:00"), 20260712000000)
check("英文 PM", kc.parse_added_time("Monday, May 6, 2024 9:15:00 PM"), 20240506211500)
check("英文 AM", kc.parse_added_time("Monday, May 6, 2024 9:15:00 AM"), 20240506091500)
check("空字符串 → 0", kc.parse_added_time(""), 0)
check("认不出的格式 → 0", kc.parse_added_time("很久以前"), 0)

print()
print("=" * 66)
print("6. 回归：原有解析能力")
print("=" * 66)
e_b = parse(LAYOUT_B)
check("布局B 解析出 2 条", len(e_b), 2)
check("布局B 书名/作者", (e_b[0].title, e_b[0].author),
      ("Sapiens: A Brief History of Humankind", "Yuval Noah Harari"))
check("布局B 页码/位置", (e_b[0].page, e_b[0].location_raw), ("45", "678-680"))
check("布局B 时间解析成功", e_b[0].time_key, 20240506211500)

e_c = parse(MISC)
check("书签被丢弃，解析出 3 条", len(e_c), 3)
kept_c, _ = kc.dedupe(e_c)
check("同起点不同区间的重复划线合并（123-125 vs 123-127）",
      len([e for e in kept_c if e.kind == "标注" and e.page == "12"]), 1)
check("保留后出现的那条", [e for e in kept_c if e.page == "12"][0].location_raw, "123-127")
check("剪贴上限占位符被剔除",
      [e for e in kept_c if e.page == "3"][0].content, "正文内容")

print()
print("=" * 66)
print("7. 渲染：笔记标题要高亮，标注标题不高亮")
print("=" * 66)
import datetime as _dt
import re as _re

_md = kc.render_book(kc.group_by_book(parse(DUP))[0], _dt.datetime(2026, 1, 1))
_headings = _re.findall(r"^### (.*)$", _md, _re.M)
check("笔记标题全部带 ==…== 高亮",
      [h for h in _headings if "笔记" in h and not h.startswith("==")], [])
check("标注标题全部不加高亮",
      [h for h in _headings if "标注" in h and h.startswith("==")], [])
check("高亮范围是整个『序号. 笔记』",
      [h for h in _headings if "笔记" in h and not _re.fullmatch(r"==\d+\. 笔记==", h)], [])
check("标题里既有笔记也有标注",
      (any("笔记" in h for h in _headings), any("标注" in h for h in _headings)),
      (True, True))

print()
print("=" * 66)
print(f"结果：{len(PASS)} 项通过，{len(FAIL)} 项失败")
for name, got, want in FAIL:
    print(f"  [FAIL] {name}: 实际={got!r} 期望={want!r}")
print("=" * 66)
raise SystemExit(1 if FAIL else 0)
