"""Áp «direction» từng câu vào lồng tiếng video (mốc 6).

Mỗi câu có direction (file ``<srt>.directions.json``, gắn vào ``cue.meta["direction"]``) → engine đọc câu đó:
- Gemini TTS: lời dặn ``style_en`` + temperature theo bậc. Câu CÙNG lời dặn dùng chung một engine → bước đọc gộp
  (``prefill_batches`` gom theo engine) tự xếp các câu cùng cảm xúc vào một lượt, dù nằm rải rác trong video — cảm xúc
  đổi giữa chừng không bao giờ nằm chung một lượt (spec 4.4).
- Edge TTS: chỉ chỉnh được tốc độ / cao độ (suy từ năng lượng / sắc thái cảm xúc).
- Engine khác (VieNeu, ElevenLabs…): giữ nguyên, bỏ qua direction.
Câu có nguy cơ tràn khung (chữ nhiều so với khe thời gian) → thêm «nói nhanh hơn một chút» vào lời dặn ngay lượt đầu
(spec mục time-fit bước 2) thay vì đọc rồi mới nén / rút gọn.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.state import Settings, TTSConfig
from app.services.direction.blend import Compiled, compile_direction
from app.services.tts.base import TTSEngine

logger = logging.getLogger(__name__)

FASTER_EN = "speaking a bit faster than normal"
FAST_RATIO = 1.0                  # dự kiến (đã hiệu chỉnh) dài hơn khe → dặn nói nhanh hơn
#: thời lượng thật trên track (Gemini, gồm lặng đệm đầu / cuối câu) ≈ PACE × ước lượng theo số tiếng + PAD — khớp bình
#: phương tối thiểu trên 77 câu đo thật (audit_out/m7/fast_fit.py): báo đúng 30/34 câu tràn khung, trước chỉ 8/34
PACE, PAD = 0.87, 0.34
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


def is_gemini_voice(voice_id: str | None) -> bool:
    from app.services.tts.gemini_tts import VOICES

    return bool(voice_id) and any(n == voice_id for n, _g, _d in VOICES)


class Director:
    """Đổi (engine, giọng, tốc độ, cao độ) của một câu theo direction. Engine Gemini dùng chung theo lời dặn."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._gemini: dict[tuple, TTSEngine] = {}
        self.styles: dict[str, int] = {}          # lời dặn → số câu (để báo log)

    def plan(self, cfg: TTSConfig, eng: TTSEngine, vid: str, speed: float, pitch: int, direction: dict | None, *,
             faster: bool = False) -> tuple[TTSEngine, str, float, int]:
        if not direction:
            return eng, vid, speed, pitch
        c = compiled_for(direction, faster=faster)
        if cfg.tts_provider.startswith("Gemini"):
            from app.services.tts.gemini_tts import GeminiTTS

            key = (cfg.gemini_tts_model, c.style_en, c.temperature)
            g = self._gemini.get(key)
            if g is None:
                g = self._gemini[key] = GeminiTTS(self.settings.gemini_keys(), model=cfg.gemini_tts_model,
                                                  style="Tự viết…", custom_style=c.style_en, temperature=c.temperature)
            self.styles[c.style_en] = self.styles.get(c.style_en, 0) + 1
            v = direction.get("voice_id")
            return g, v if is_gemini_voice(v) else vid, speed, pitch
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


def needs_faster(text: str, slot: float, speed: float = 1.0) -> bool:
    """Chữ nhiều so với khe thời gian của câu → nên dặn nói nhanh hơn ngay từ lượt đọc đầu."""
    from app.services.tts.quality import expected_duration

    return slot > 0 and expected_duration(text, speed) * PACE + PAD > slot * FAST_RATIO


def preview_cue(cues: list, i: int, direction: dict | None, cfg: TTSConfig, settings: Settings, *,
                video_speed: float = 1.0) -> Path:
    """Đọc thử câu ``i`` đúng như lúc dựng track (cùng engine, lời dặn, «nói nhanh hơn», khoá cache) → lần dựng track
    sau lấy luôn từ cache, không gọi API lại."""
    from app.services.tts.generator import make_engine, qc_options, synthesize_cached, voice_id_for

    c = cues[i]
    eng = make_engine(cfg, settings)
    vid = voice_id_for(cfg)
    pitch = cfg.tts_pitch if cfg.tts_provider == "Edge TTS" else 0
    sp = max(0.25, video_speed or 1.0)
    nxt = next((x for x in cues[i + 1:] if x.plain), None)
    slot = (nxt.start - c.start) / sp - cfg.min_gap if nxt is not None else c.duration / sp
    plan = (eng, vid, cfg.tts_speed, pitch)
    if direction and cfg.emotion_direction:
        fast = needs_faster(c.plain, slot, cfg.tts_speed)
        plan = Director(settings).plan(cfg, eng, vid, cfg.tts_speed, pitch, direction, faster=fast)
    e, v, s, p = plan
    return Path(synthesize_cached(e, v, c.plain, speed=s, pitch=p, qc=qc_options(settings), label=f"câu {i + 1}"))


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
