#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取可选的 config.json（不存在就用默认值，不需要任何配置也能跑）。"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.json"

DEFAULTS: dict = {
    "host": "127.0.0.1",
    "port": 8765,
    "open_browser": True,
    "edge_path": None,           # 指定 msedge.exe；为 null 时自动找
    "clippings_lib_dir": None,   # 指定 kindle_clippings.py 所在目录
}


def _load() -> dict:
    data = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            data.update({k: v for k, v in raw.items() if v is not None})
    return data


CONFIG = _load()
