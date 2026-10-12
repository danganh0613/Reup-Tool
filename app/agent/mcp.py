"""MCP server qua stdio (JSON-RPC 2.0, mỗi dòng một thông điệp) — không cần thư viện ngoài.

    python -m app.agent mcp

Cấu hình cho client MCP (Claude Desktop, Cline, OpenCode… dùng model DeepSeek), ví dụ:
    {"mcpServers": {"edit-studio": {"command": "python", "args": ["-m", "app.agent", "mcp"],
                                    "cwd": "F:\\\\Project\\\\Edit Studio"}}}

Mỗi lệnh chuyển vào app đang mở qua cổng lệnh cục bộ (app chưa mở thì tự mở). Kết quả trả về là JSON gọn một dòng."""

from __future__ import annotations

import json
import sys

from app.agent import control
from app.agent.schema import TOOLS

PROTOCOL = "2025-06-18"
INSTRUCTIONS = ("Edit Studio dubs / subtitles videos. Typical flow: videos -> settings (optional) -> "
                "run(prepare, until=translation, wait_s=900) -> review each id -> fix -> run(prepare) to dub -> "
                "tell the user which lines to listen to. Do not render unless asked.")


def _reply(msg_id, result=None, error: dict | None = None) -> None:
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> dict | None:
    """Một thông điệp JSON-RPC → kết quả (None: thông báo, không trả lời)."""
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        from app.config.paths import APP_VERSION

        return {"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {}},
                "serverInfo": {"name": "edit-studio", "version": APP_VERSION}, "instructions": INSTRUCTIONS}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name, args = params.get("name", ""), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            raise KeyError(f"không có lệnh «{name}»")
        res = control.call(name, args)
        return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, separators=(",", ":"))}],
                "isError": "error" in res}
    if method == "ping":
        return {}
    raise NotImplementedError(method or "?")


def serve(stdin=None) -> None:
    for line in (stdin or sys.stdin):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _reply(None, error={"code": -32700, "message": "JSON hỏng"})
            continue
        msg_id = msg.get("id")
        try:
            result = handle(msg)
        except NotImplementedError as exc:
            if msg_id is not None:
                _reply(msg_id, error={"code": -32601, "message": f"không hỗ trợ: {exc}"})
            continue
        except Exception as exc:                         # noqa: BLE001 — lỗi một lệnh không làm sập server
            if msg_id is not None:
                _reply(msg_id, error={"code": -32603, "message": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        if msg_id is not None and not str(msg.get("method", "")).startswith("notifications/"):
            _reply(msg_id, result)
