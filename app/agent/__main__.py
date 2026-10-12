"""CLI: gửi một lệnh vào Edit Studio đang mở (chưa mở thì tự mở), in JSON gọn.

    python -m app.agent videos
    python -m app.agent videos "{\\"add\\": [\\"C:/video/a.mp4\\"], \\"preset\\": \\"Review phim dọc 9:16\\"}"
    python -m app.agent run "{\\"until\\": \\"translation\\", \\"wait_s\\": 900}"
    python -m app.agent review "{\\"id\\": \\"a1b2c3\\"}"
    python -m app.agent fix "{\\"id\\": \\"a1b2c3\\", \\"text\\": {\\"12\\": \\"Câu mới\\"}, \\"voice\\": true}"
    python -m app.agent settings "{\\"ratio\\": \\"9:16\\", \\"fit\\": \\"nen_mo\\", \\"offset_y\\": -20}"
    python -m app.agent mcp                 MCP server (stdio)
Tham số có thể đọc từ file: ``@args.json``. Thoát mã 1 khi kết quả có «error»."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                    # noqa: BLE001
        pass
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "mcp":
        from app.agent.mcp import serve

        serve()
        return 0
    raw = " ".join(argv[1:]).strip() or "{}"
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8-sig")
    try:
        args = json.loads(raw)
    except ValueError as exc:
        print(json.dumps({"error": f"tham số JSON hỏng: {exc}"}, ensure_ascii=False))
        return 2
    from app.agent import control

    res = control.call(argv[0], args)
    print(json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    return 1 if "error" in res else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
