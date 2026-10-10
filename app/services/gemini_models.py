"""Danh sách model Gemini dùng trong app — một chỗ duy nhất để cập nhật.

Google tắt model khá nhanh (2.5 Flash tắt 06/2026, Imagen 4 tắt 08/2026, Veo 3.0 tắt 06/2026,
2.5 Flash Image tắt 10/2026…). Ba lớp bảo vệ:

1. Danh sách mặc định bên dưới ưu tiên bí danh «…-latest» — Google luôn trỏ nó tới bản đang phục vụ.
2. ``migrate_model``: cấu hình cũ còn ghi tên model đã tắt → tự đổi sang model thay thế khi nạp.
3. ``fetch_models``: nút «Lấy danh sách model mới nhất» trong Cài đặt hỏi thẳng Google (models.list) bằng key của
   người dùng; kết quả lưu ở cache/gemini_models.json và được dùng thay danh sách mặc định.

Cập nhật lần cuối: 10/2026.
"""

from __future__ import annotations

import json
import logging
import re

import requests

from app.config.paths import CACHE_DIR

logger = logging.getLogger(__name__)

BASE = "https://generativelanguage.googleapis.com/v1beta"
CACHE_FILE = CACHE_DIR / "gemini_models.json"

#: văn bản (dịch, viết kịch bản, STT cloud). «-latest» trước: không bao giờ bị gỡ.
#: «gemini-3.5-flash-lite»: gói miễn phí 500 lượt/ngày (Flash chỉ 20) — hợp để dịch phụ đề
DEFAULT_TEXT = ["gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-pro-latest",
                "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]
#: tạo ảnh (Nano Banana). Imagen 4 đã tắt.
DEFAULT_IMAGE = ["gemini-3.1-flash-image", "gemini-3-pro-image", "gemini-3.1-flash-lite-image"]
#: tạo video (Veo 3.1). Veo 2 / 3.0 đã tắt.
DEFAULT_VIDEO = ["veo-3.1-fast-generate-preview", "veo-3.1-generate-preview", "veo-3.1-lite-generate-preview"]

#: model đã / sắp bị Google tắt → model thay thế (khớp đúng tên, hoặc theo tiền tố khi kết thúc bằng «*»)
RETIRED: dict[str, str] = {
    "gemini-1.5-*": "gemini-flash-latest",
    "gemini-2.0-flash*": "gemini-flash-latest",
    "gemini-2.5-flash-lite*": "gemini-flash-lite-latest",
    "gemini-2.5-flash-image*": "gemini-3.1-flash-image",
    "gemini-2.5-flash": "gemini-flash-latest",
    "gemini-2.5-flash-preview*": "gemini-flash-latest",
    "gemini-2.5-pro*": "gemini-pro-latest",
    "gemini-3-flash-preview": "gemini-flash-latest",
    "gemini-3-pro-preview": "gemini-pro-latest",
    "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image",
    "gemini-3-pro-image-preview": "gemini-3-pro-image",
    "imagen-*": "gemini-3.1-flash-image",
    "veo-2.0-*": "veo-3.1-fast-generate-preview",
    "veo-3.0-fast-*": "veo-3.1-fast-generate-preview",
    "veo-3.0-*": "veo-3.1-generate-preview",
}


def migrate_model(name: str) -> str:
    """Tên model đã bị tắt → tên thay thế; còn dùng được thì giữ nguyên."""
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


def _load_cache() -> dict[str, list[str]]:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return {k: [m for m in data.get(k, []) if isinstance(m, str) and not is_retired(m)]
                for k in ("text", "image", "video")}
    except Exception:
        return {}


def _merge(defaults: list[str], fetched: list[str]) -> list[str]:
    """Bí danh «-latest» + mặc định lên đầu, rồi các model lấy được từ Google (bỏ trùng).
    Đã có danh sách thật từ Google → bỏ model mặc định mà key này không có (giữ bí danh «-latest»)."""
    if fetched:
        defaults = [d for d in defaults if d in fetched or d.endswith("-latest")]
    out = list(defaults)
    out += [m for m in fetched if m not in out]
    return out


def text_models() -> list[str]:
    return _merge(DEFAULT_TEXT, _load_cache().get("text", []))


def image_models() -> list[str]:
    return _merge(DEFAULT_IMAGE, _load_cache().get("image", []))


def video_models() -> list[str]:
    return _merge(DEFAULT_VIDEO, _load_cache().get("video", []))


# model văn bản không dùng được cho dịch / viết (TTS, embedding, live/audio, điều khiển máy…)
_SKIP_TEXT = re.compile(r"(tts|embedding|aqa|live|native-audio|audio|computer-use|robotics|image|veo|imagen|"
                        r"learnlm|gemma|nano|thinking-exp|exp-\d)", re.I)


def fetch_models(api_key: str, timeout: float = 20) -> dict[str, list[str]]:
    """Hỏi Google các model key này dùng được (models.list), chia nhóm văn bản / ảnh / video, lưu cache."""
    if not api_key:
        raise RuntimeError("Chưa có Gemini API key.")
    names: list[tuple[str, list[str]]] = []
    page = ""
    for _ in range(10):
        params = {"pageSize": 1000}
        if page:
            params["pageToken"] = page
        r = requests.get(f"{BASE}/models", params=params, headers={"x-goog-api-key": api_key}, timeout=timeout)
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:
                msg = r.text[:200]
            raise RuntimeError(f"Google trả lỗi {r.status_code}: {msg}")
        data = r.json()
        for m in data.get("models", []):
            name = str(m.get("name", "")).removeprefix("models/")
            names.append((name, list(m.get("supportedGenerationMethods") or [])))
        page = data.get("nextPageToken", "")
        if not page:
            break

    text, image, video = [], [], []
    for name, methods in names:
        if not name or is_retired(name):
            continue
        if name.startswith("veo-") and "predictLongRunning" in methods:
            video.append(name)
        elif name.startswith("gemini-") and "image" in name and "generateContent" in methods:
            image.append(name)
        elif name.startswith("gemini-") and "generateContent" in methods and not _SKIP_TEXT.search(name):
            text.append(name)

    def newest_first(items: list[str]) -> list[str]:
        def key(n: str):
            v = re.search(r"(\d+(?:\.\d+)?)", n)
            return (-(float(v.group(1)) if v else 0.0), "preview" in n, n)
        return sorted(dict.fromkeys(items), key=key)

    result = {"text": newest_first(text), "image": newest_first(image), "video": newest_first(video)}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("Không lưu được danh sách model", exc_info=True)
    logger.info("Danh sách model Gemini: %d văn bản · %d ảnh · %d video", len(text), len(image), len(video))
    return result
