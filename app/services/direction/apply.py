"""Áp «direction» (cảm xúc đã chỉnh tay, file ``<srt>.directions.json``, gắn vào ``cue.meta["direction"]``) vào lồng
tiếng: Edge TTS chỉnh tốc độ / cao độ theo năng lượng / sắc thái cảm xúc; engine khác (VieNeu, ElevenLabs…) bỏ qua.
Gemini TTS / Gemini Live và giao diện đặt cảm xúc đã gỡ (10/2026) — chỉ còn áp cho file cảm xúc đã lưu từ trước."""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.state import Settings, TTSConfig
from app.services.direction.blend import Compiled, compile_direction
from app.services.tts.base import TTSEngine

logger = logging.getLogger(__name__)

FASTER_EN = "speaking a bit faster than normal"
_SLOW = "speaking slowly, soft and low"


def compiled_for(direction: dict, *, faster: bool = False) -> Compiled:
    """Biên dịch direction; ``faster`` → bỏ «nói chậm» (nếu có) và dặn nói nhanh hơn một chút."""
    c = compile_direction(direction)
    if faster and "speaking rapidly" not in c.style_en:      # cảm xúc mạnh đã dặn nói nhanh → không thêm
        style = c.style_en.replace(f", {_SLOW}", "").replace(_SLOW, "").strip(", ")
        c.style_en = f"{style}, {FASTER_EN}" if style else FASTER_EN
    if faster:
        c.edge_rate = max(c.edge_rate, 8)
    return c


def effective(direction: dict | None, cfg: TTSConfig) -> dict | None:
    """Direction được dùng khi lồng tiếng: chỉ câu đã chỉnh tay (``manual``)."""
    if direction and direction.get("manual"):
        return direction
    return None


class Director:
    """Đổi (tốc độ, cao độ) của một câu theo direction — chỉ Edge TTS chỉnh được."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.styles: dict[str, int] = {}          # lời dặn → số câu (để báo log)

    def plan(self, cfg: TTSConfig, eng: TTSEngine, vid: str, speed: float, pitch: int, direction: dict | None, *,
             faster: bool = False) -> tuple[TTSEngine, str, float, int]:
        if not direction:
            return eng, vid, speed, pitch
        c = compiled_for(direction, faster=faster)
        if cfg.tts_provider == "Edge TTS":
            return eng, vid, speed * (1.0 + c.edge_rate / 100.0), pitch + c.edge_pitch
        return eng, vid, speed, pitch


def emotion_label(direction: dict | None) -> str:
    """«Vui tươi 2/5» — để báo cáo / hiển thị."""
    if not direction:
        return ""
    from app.services.direction.blend import blend, emotions_map

    e = direction.get("emotion") or {}
    level = max(1, min(5, int(e.get("level", 3))))
    main, _w = blend(float(e.get("theta", 0.0)), level, e.get("overrides"))
    return f"{emotions_map()[main]['vi']} {level}/5"


def annotate(cues: list, srt_path: str | Path | None) -> int:
    """Gắn direction (theo số thứ tự câu trong SRT — ``meta["src_idx"]`` nếu có) vào ``cue.meta``. → số câu gắn được."""
    from app.services.direction import suggest
    from app.services.srt_utils import load_srt

    try:
        n = len(load_srt(srt_path)) if srt_path else None
    except Exception:                                    # noqa: BLE001 — SRT lỗi → không kiểm số câu
        n = None
    items = suggest.load(srt_path, n)
    if not items:
        return 0
    n = 0
    for k, c in enumerate(cues):
        d = items.get(int(c.meta.get("src_idx", k)))
        if d:
            c.meta["direction"] = d
            n += 1
    return n


def signature(srt_path: str | Path | None) -> str:
    """Thay đổi khi file direction đổi → track giọng được dựng lại (câu không đổi lấy từ cache)."""
    from app.services.direction import suggest

    if not srt_path:
        return ""
    try:
        return str(suggest.sidecar_path(srt_path).stat().st_mtime_ns)
    except OSError:
        return ""
