"""Run the desktop parser and exporters inside the Android app process."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from app import server


def initialize(files_dir: str) -> None:
    server.SESSION_DIR = Path(files_dir)
    server.SESSION_PATH = server.SESSION_DIR / "last-session.json"


def request_json(route: str, body: str) -> str:
    if route == "/api/session":
        result = server.session_payload()
    elif route == "/api/parse":
        result = server.parse_payload(json.loads(body), desktop_sources=False)
    else:
        raise ValueError("不支持的请求。")
    return json.dumps(result, ensure_ascii=False)


def export_file(body: str, cache_dir: str) -> tuple[str, str, str]:
    payload = json.loads(body)
    if payload.get("format") not in {"md", "docx", "epub"}:
        raise ValueError("安卓版支持 Markdown、Word 和 EPUB 导出。")
    name, data, mime = server.export_payload(payload)
    with NamedTemporaryFile(dir=cache_dir, prefix="clippings-", delete=False) as output:
        try:
            output.write(data)
        except OSError:
            Path(output.name).unlink(missing_ok=True)
            raise
    return name, output.name, mime.split(";", 1)[0]
