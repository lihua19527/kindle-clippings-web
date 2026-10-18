#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端冒烟检查：用本机 Edge 的无头模式真的把页面跑一遍。

做法：页面带 `?probe=1` 打开时会把渲染结果 POST 到 `/api/probe`
（见 public/assets/app.js 末尾的 reportProbe），这里读回那份报告做断言，
再截图用纯标准库解 PNG 算像素方差，确认不是空白页。

跑法：
    python tests/ui_check.py
没有 Edge / Chrome 时自动跳过（退出码 0），不会让检查假红。
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zlib
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from app.render.pdf_export import find_browser  # noqa: E402
from app.server import build_server  # noqa: E402

SHOT_DIR = PROJECT_DIR / ".cache" / "shots"


def post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read() or b"{}")


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as res:
        return json.loads(res.read() or b"{}")


def run_browser(url: str, *extra: str, timeout: int = 120) -> None:
    browser = find_browser()
    with tempfile.TemporaryDirectory(prefix="clipcheck-") as profile:
        args = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--disable-extensions",
            "--virtual-time-budget=9000",
            f"--user-data-dir={profile}",
            *extra,
            url,
        ]
        subprocess.run(args, capture_output=True, timeout=timeout)


def wait_report(base: str, timeout: float = 25.0) -> dict:
    """等页面把自检报告 POST 上来。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        probe = get(base + "/api/probe").get("probe") or {}
        if "cards" in probe:
            return probe
        time.sleep(0.4)
    return {}


def visit(base: str, query: str) -> dict:
    post(base + "/api/probe", {"reset": True})
    run_browser(f"{base}/?{query}")
    return wait_report(base)


def png_stats(path: Path) -> tuple[int, int, int, float]:
    """不依赖 PIL：读出宽高并抽样估算像素方差。"""
    raw = path.read_bytes()
    width, height = struct.unpack(">II", raw[16:24])
    idat = b""
    pos = 8
    while pos < len(raw):
        length = struct.unpack(">I", raw[pos: pos + 4])[0]
        kind = raw[pos + 4: pos + 8]
        if kind == b"IDAT":
            idat += raw[pos + 8: pos + 8 + length]
        pos += 12 + length
    try:
        pixels = zlib.decompress(idat)
    except zlib.error:
        return width, height, len(raw), -1.0
    stride = len(pixels) // height if height else 0
    if not stride:
        return width, height, len(raw), -1.0
    values: list[int] = []
    step = max(1, stride // 64)
    for y in range(0, height, max(1, height // 60)):
        values.extend(pixels[y * stride + 1: (y + 1) * stride: step])
    if not values:
        return width, height, len(raw), -1.0
    mean = sum(values) / len(values)
    return width, height, len(raw), sum((v - mean) ** 2 for v in values) / len(values)


def underline_states(report: dict) -> set[str]:
    """从 navUnderlines 里挑出横线完整显示的标签。

    页面回报的形如 `rules=matrix(1, 0, 0, 1, 0, 0)`；scaleX 为 0 表示该横线收起。
    """
    lit: set[str] = set()
    for item in report.get("navUnderlines") or []:
        name, _, transform = str(item).partition("=")
        match = re.match(r"matrix\(([-0-9.eE+]+),", transform.strip())
        if match and float(match.group(1)) > 0.9:
            lit.add(name)
    return lit


def main() -> int:
    try:
        find_browser()
    except RuntimeError as exc:
        print(f"[跳过] {exc}")
        return 0

    httpd = build_server("127.0.0.1", 8811, tries=20)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("  ✓ " if ok else "  ✗ ") + name + (f"（{detail}）" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    try:
        print(f"服务已起：{base}")

        # ---------- 1. 空状态 ----------
        report = visit(base, "still=1&probe=1")
        check("页面脚本无报错", "error" not in report, report.get("error", ""))
        check("空状态：统计为占位符", report.get("statBooks") == "—", str(report.get("statBooks")))
        check("空状态：没有书卡", report.get("cards") == 0, str(report.get("cards")))
        check("空状态：导出栏隐藏", report.get("exportHidden") is True)
        check("空状态：详情隐藏", report.get("detailHidden") is True)

        # ---------- 2. 载入示例 ----------
        report = visit(base, "demo=1&still=1&probe=1")
        check("示例无脚本报错", "error" not in report, report.get("error", ""))
        check("5 张书卡", report.get("cards") == 5, str(report.get("cards")))
        check("统计 5 本", report.get("statBooks") == "5", str(report.get("statBooks")))
        check("统计 22 条", report.get("statEntries") == "22", str(report.get("statEntries")))
        check("统计 6 条重复", report.get("statDropped") == "6", str(report.get("statDropped")))
        check("导出栏显示", report.get("exportHidden") is False)
        check("书库网格显示", report.get("gridHidden") is False)

        # ---------- 3. 详情页 ----------
        report = visit(base, "demo=1&still=1&probe=1&book=1")
        check("详情无脚本报错", "error" not in report, report.get("error", ""))
        check("详情标题", report.get("detailTitle") == "《人类简史》", str(report.get("detailTitle")))
        check("详情有 7 条", report.get("entries") == 7, str(report.get("entries")))
        check("详情：书库网格隐藏", report.get("gridHidden") is True)
        check("详情：详情面板显示", report.get("detailHidden") is False)

        # ---------- 3.5 勾选与打包方式 ----------
        report = visit(base, "demo=1&still=1&probe=1&pick=1,3&pack=per_book")
        check("勾选无脚本报错", "error" not in report, report.get("error", ""))
        check("预勾选 2 本", report.get("picked") == 2, str(report.get("picked")))
        check("打包方式为每本单独", report.get("packaging") == "per_book",
              str(report.get("packaging")))
        check("计数文案带「已选 2 本」", "已选 2 本" in str(report.get("exportCount")),
              str(report.get("exportCount")))

        # ---------- 3.7 导航横线跟随所在区块 ----------
        report = visit(base, "still=1&probe=1")
        check("停在首屏时没有标签点亮", report.get("activeNav") == "", str(report.get("activeNav")))

        report = visit(base, "still=1&probe=1&scrollto=rules")
        check("滚到「去重规则」时它点亮", report.get("activeNav") == "rules",
              str(report.get("activeNav")))
        lit = underline_states(report)
        check("这时只有「去重规则」有横线", lit == {"rules"},
              f"有横线的标签：{sorted(lit) or '无'}")

        report = visit(base, "still=1&probe=1&scrollto=formats")
        check("滚到「导出格式」时它点亮", report.get("activeNav") == "formats",
              str(report.get("activeNav")))
        lit = underline_states(report)
        check("这时只有「导出格式」有横线", lit == {"formats"},
              f"有横线的标签：{sorted(lit) or '无'}")

        report = visit(base, "still=1&probe=1&scrollto=library")
        check("滚到「书库」时它点亮", report.get("activeNav") == "library",
              str(report.get("activeNav")))
        lit = underline_states(report)
        check("这时只有「书库」有横线", lit == {"library"},
              f"有横线的标签：{sorted(lit) or '无'}")

        report = visit(base, "still=1&probe=1")
        check("首屏四个标签都没有横线", underline_states(report) == set(),
              f"有横线的标签：{sorted(underline_states(report)) or '无'}")

        report = visit(base, "demo=1&still=1&probe=1")
        check("解析完停在书库时「书库」点亮", report.get("activeNav") == "library",
              str(report.get("activeNav")))
        lit = underline_states(report)
        check("这时只有「书库」有横线", lit == {"library"},
              f"有横线的标签：{sorted(lit) or '无'}")

        # ---------- 4. 截图 ----------
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        shots = [
            ("home-empty", "still=1&probe=1", "1440,1800"),
            ("home-demo", "demo=1&still=1&probe=1&scroll=0", "1440,3400"),
            ("detail", "demo=1&still=1&probe=1&book=1&scroll=0", "1440,2400"),
            ("narrow", "demo=1&still=1&probe=1&scroll=0", "560,2600"),
        ]
        for name, query, size in shots:
            out = SHOT_DIR / f"{name}.png"
            out.unlink(missing_ok=True)
            run_browser(f"{base}/?{query}", "--window-size=" + size, f"--screenshot={out}")
            if not out.exists():
                check(f"截图 {name} 生成", False, "文件没出现")
                continue
            w, h, size_bytes, var = png_stats(out)
            check(
                f"截图 {name} 非空白",
                size_bytes > 40_000 and var > 200,
                f"{w}×{h}，{size_bytes // 1024} KB，方差 {var:.0f}",
            )
            print(f"      {w}×{h}，{size_bytes // 1024} KB")
    finally:
        httpd.shutdown()
        httpd.server_close()

    print("")
    if failures:
        print(f"[×] 失败 {len(failures)} 项：" + "、".join(failures))
        return 1
    print("[✓] 前端检查全部通过，截图在 .cache/shots/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
