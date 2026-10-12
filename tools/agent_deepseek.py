"""Agent DeepSeek điều khiển Edit Studio bằng 5 lệnh thô (videos · run · review · fix · settings) — vòng lặp nhỏ,
lời dặn hệ thống ngắn (rẻ token hơn các harness lớn). App đang mở là nơi làm việc; chưa mở thì tự mở.

    python tools\\agent_deepseek.py "Chuẩn bị các video trong C:\\video\\moi theo mẫu Review phim dọc 9:16, dừng ở bản dịch, soát rồi báo mình"

Key: biến môi trường DEEPSEEK_API_KEY (hoặc dòng DEEPSEEK_API_KEY=… trong file .env ở thư mục app); không có thì dùng
key DeepSeek đã nhập trong app (Cài đặt › AI dùng API key). Model: DEEPSEEK_MODEL hoặc model DeepSeek của app.
Không in key ra màn hình / log."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

API = "https://api.deepseek.com/chat/completions"
MAX_STEPS = 30
SYSTEM = (
    "Bạn là quản đốc của Edit Studio (lồng tiếng / phụ đề video). Chỉ dùng 5 lệnh. Quy trình: videos (thêm video nếu "
    "được bảo) → settings nếu cần → run(action=prepare, until=translation, wait_s=900) → với từng video có flags: "
    "review(id) → sửa bằng fix (text cho câu cụ thể, request cho yêu cầu chung như xưng hô; giữ đúng cặp xưng hô trong "
    "people) → run(action=prepare, wait_s=900) để tạo giọng → review lại. Không xuất video (render) trừ khi được bảo. "
    "Kết thúc: báo ngắn gọn từng video còn những câu nào người dùng nên nghe (số câu + lý do). Trả lời tiếng Việt, gọn.")


def _key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    env = ROOT / ".env"
    if not k and env.exists():
        for ln in env.read_text(encoding="utf-8").splitlines():
            if ln.strip().startswith("DEEPSEEK_API_KEY="):
                k = ln.split("=", 1)[1].strip().strip('"').strip("'")
    if not k:
        from app.core.state import AppState

        st = AppState()
        st.load_settings()
        k = st.settings.deepseek_api_key.strip()
    return k


def _model() -> str:
    m = os.environ.get("DEEPSEEK_MODEL", "").strip()
    if m:
        return m
    from app.core.state import AppState

    st = AppState()
    st.load_settings()
    return st.settings.deepseek_model or "deepseek-chat"


def _post(payload: dict, key: str) -> dict:
    import requests

    r = requests.post(API, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"DeepSeek {r.status_code}: {r.text[:300]}")
    return r.json()


def run_agent(task: str, *, post: Callable[[dict], dict] | None = None, tool: Callable[[str, dict], dict] | None = None,
              model: str | None = None, log: Callable[[str], None] = lambda m: print(m, file=sys.stderr)) -> str:
    """Vòng lặp: gửi hội thoại + 5 lệnh → AI gọi lệnh → trả kết quả lệnh → … → câu trả lời cuối. ``post`` / ``tool``:
    thay được để kiểm thử (không gọi mạng / app thật)."""
    from app.agent import control
    from app.agent.schema import openai_tools

    if post is None:
        key = _key()
        if not key:
            raise SystemExit("Chưa có key DeepSeek: đặt DEEPSEEK_API_KEY (hoặc .env), hoặc nhập trong app.")
        post = lambda p: _post(p, key)                  # noqa: E731
    tool = tool or control.call
    msgs: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    tools = openai_tools()
    model = model or _model()
    for step in range(MAX_STEPS):
        t0 = time.monotonic()
        res = post({"model": model, "messages": msgs, "tools": tools, "temperature": 0.2})
        msg = res["choices"][0]["message"]
        usage = res.get("usage") or {}
        log(f"[{step + 1}] {time.monotonic() - t0:.1f}s · token vào {usage.get('prompt_tokens', '?')} "
            f"(cache {usage.get('prompt_cache_hit_tokens', '?')}) · ra {usage.get('completion_tokens', '?')}")
        msgs.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip()
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            log(f"    → {name} {json.dumps(args, ensure_ascii=False)[:160]}")
            out = tool(name, args)
            msgs.append({"role": "tool", "tool_call_id": c["id"],
                         "content": json.dumps(out, ensure_ascii=False, separators=(",", ":"))})
    return "Dừng: quá số bước cho phép — xem tab Duyệt / Lời thoại trong app."


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:                                    # noqa: BLE001
        pass
    task = " ".join(sys.argv[1:]).strip()
    if not task:
        print(__doc__)
        return 0
    print(run_agent(task))
    return 0


if __name__ == "__main__":
    sys.exit(main())
