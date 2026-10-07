"""Danh sách model Groq dùng để dịch — một chỗ duy nhất để cập nhật (giống ``gemini_models``).

Groq gỡ model khá nhanh (Llama 3.x, Qwen3 32B / 3.6 đã gỡ khỏi key thường, 10/2026). Ba lớp bảo vệ:

1. Danh sách mặc định bên dưới — đã dịch thử Trung → Việt bằng prompt của app (audit_out/groq/test_models.py):
   qwen3.8-27b dịch tự nhiên nhất và nhanh; gpt-oss-120b được nhưng hay sai tên con vật; gpt-oss-20b nhanh, dịch yếu.
2. ``migrate_model``: cấu hình cũ còn ghi model đã gỡ → tự đổi sang model mặc định khi nạp.
3. ``fetch_models``: nút làm mới danh sách model trong Cài đặt hỏi thẳng Groq (GET /models) bằng key của người dùng;
   kết quả lưu ở cache/groq_models.json và được dùng thay danh sách mặc định.

Cập nhật lần cuối: 10/2026.
"""

from __future__ import annotations

import json
import logging
import re

import requests

from app.config.paths import CACHE_DIR

logger = logging.getLogger(__name__)

URL = "https://api.groq.com/openai/v1/models"
CACHE_FILE = CACHE_DIR / "groq_models.json"

#: model dịch, tốt nhất trước
DEFAULT_TEXT = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]

#: model đã gỡ → model thay thế (khớp đúng tên, hoặc theo tiền tố khi kết thúc bằng «*»)
RETIRED: dict[str, str] = {
    "llama-*": "qwen/qwen3.8-27b",                 # llama-3.3-70b-versatile, llama-3.1-8b-instant… (404 từ 10/2026)
    "llama3-*": "qwen/qwen3.8-27b",
    "gemma*": "qwen/qwen3.8-27b",
    "mixtral-*": "qwen/qwen3.8-27b",
    "deepseek-r1-distill-*": "qwen/qwen3.8-27b",
    "qwen-*": "qwen/qwen3.8-27b",                   # qwen-qwq-32b, qwen-2.5-*
    "qwen/qwen3-32b": "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b": "qwen/qwen3.8-27b",
}

# không dùng để dịch: nhận diện / đọc giọng, lọc an toàn, model ngữ cảnh ngắn
_SKIP = re.compile(r"(whisper|orpheus|tts|playai|guard|safeguard|allam|compound)", re.I)


def migrate_model(name: str) -> str:
    """Tên model Groq đã gỡ → tên thay thế; còn dùng được thì giữ nguyên."""
    n = (name or "").strip()
    if not n:
        return n
    if n in RETIRED:
        return RETIRED[n]
    for pattern, repl in RETIRED.items():
        if pattern.endswith("*") and n.startswith(pattern[:-1]):
            return repl
    return n


def is_retired(name: str) -> bool:
    return migrate_model(name) != (name or "").strip()


def _load_cache() -> list[str]:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return [m for m in data.get("text", []) if isinstance(m, str) and not is_retired(m)]
    except Exception:
        return []


def text_models() -> list[str]:
    """Mặc định (bỏ model key này không có, nếu đã hỏi Groq) rồi tới các model lấy được từ Groq."""
    fetched = _load_cache()
    out = [d for d in DEFAULT_TEXT if not fetched or d in fetched]
    out += [m for m in fetched if m not in out]
    return out or list(DEFAULT_TEXT)


def fetch_models(api_key: str, timeout: float = 20) -> list[str]:
    """Hỏi Groq các model key này dùng được, giữ model chat dịch được (bỏ STT / TTS / lọc an toàn), lưu cache."""
    if not api_key:
        raise RuntimeError("Chưa có Groq API key.")
    r = requests.get(URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"Groq trả lỗi {r.status_code}: {msg}")
    text = []
    for m in r.json().get("data", []):
        mid = str(m.get("id", ""))
        if (not mid or m.get("active") is False or _SKIP.search(mid) or is_retired(mid)
                or int(m.get("context_window") or 0) < 32000):
            continue
        text.append(mid)
    text.sort(key=lambda n: (n not in DEFAULT_TEXT, DEFAULT_TEXT.index(n) if n in DEFAULT_TEXT else 0, n))
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"text": text}, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("Không lưu được danh sách model Groq", exc_info=True)
    logger.info("Danh sách model Groq: %d model dịch", len(text))
    return text
