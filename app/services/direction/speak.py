"""Đọc văn bản theo «direction» đã biên dịch: Gemini TTS (lời dặn + temperature) → lỗi / hết lượt thì Edge TTS
(tốc độ + cao độ suy từ cảm xúc). Có cache (synthesize_cached): nghe lại cùng câu cùng hướng không tốn lượt."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from app.config.paths import CACHE_DIR
from app.core.state import Settings
from app.services.direction.blend import Compiled, compile_direction
from app.services.tts import postprocess

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash-lite-tts"     # spec: điểm tiếng Việt cao nhất, hợp lồng tiếng hàng loạt
EDGE_VOICE = {"Female": "vi-VN-HoaiMyNeural", "Male": "vi-VN-NamMinhNeural"}
STUDIO_DIR = CACHE_DIR / "studio"
_PAUSE = 0.35                                    # nghỉ giữa các lượt nói khi ghép hội thoại


def speak(text: str, *, voice_id: str, compiled: Compiled, settings: Settings, model: str = DEFAULT_MODEL,
          gender: str = "Female", log: Callable[[str], None] | None = None) -> tuple[Path, str, bool]:
    """→ (file âm thanh, engine đã dùng, có phải dự phòng không)."""
    from app.services.tts.gemini_tts import GeminiTTS
    from app.services.tts.generator import qc_options, synthesize_cached

    eng = GeminiTTS(settings.gemini_keys(), model=model, style="Tự viết…", custom_style=compiled.style_en,
                    temperature=compiled.temperature)
    try:
        p = synthesize_cached(eng, voice_id, text, speed=1.0, pitch=0, qc=qc_options(settings), label="studio")
        return Path(p), f"Gemini · {model}", False
    except Exception as exc:                       # hết lượt / chưa có key / mạng → Edge, đánh dấu dự phòng
        logger.warning("Studio: Gemini lỗi (%s) → Edge TTS", exc)
        if log:
            log(f"⚠ Gemini không đọc được ({str(exc)[:90]}) → đọc tạm bằng Edge TTS")
    from app.services.tts.edge import EdgeTTS
    from app.services.tts.generator import synthesize_cached

    edge = EdgeTTS()
    p = synthesize_cached(edge, EDGE_VOICE.get(gender, EDGE_VOICE["Female"]), text,
                          speed=1.0 + compiled.edge_rate / 100.0, pitch=compiled.edge_pitch, qc=None, label="studio")
    return Path(p), "Edge TTS", True


def speak_dialogue(lines: list[tuple[str, str]], directions: dict[str, dict], *, settings: Settings,
                   genders: dict[str, str] | None = None, model: str = DEFAULT_MODEL,
                   log: Callable[[str], None] | None = None) -> tuple[Path, list[str], bool]:
    """Hội thoại: ``lines`` = [(người nói "A"/"B", câu)], mỗi người một direction → đọc từng câu, ghép kèm nghỉ ngắn.
    → (file ghép, engine từng câu, có câu nào dùng dự phòng)."""
    pcms: list[bytes] = []
    engines: list[str] = []
    fallback = False
    rate = 24000
    for spk, text in lines:
        d = directions[spk]
        p, eng, fb = speak(text, voice_id=d["voice_id"], compiled=compile_direction(d), settings=settings,
                           model=model, gender=(genders or {}).get(spk, "Female"), log=log)
        r = postprocess.decode_any(p, rate)
        if r is None:
            raise RuntimeError(f"Không đọc được file âm thanh vừa tạo: {p.name}")
        pcm, r_rate = r
        if r_rate != rate:                          # Gemini / Edge đều 24 kHz; engine khác thì đổi tần số cho khớp
            import numpy as np

            a = np.frombuffer(pcm, "<i2").astype(np.float32)
            n = int(len(a) * rate / r_rate)
            pcm = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype("<i2").tobytes()
        pcms.append(pcm)
        engines.append(eng)
        fallback |= fb
    STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out = STUDIO_DIR / f"hoi_thoai_{abs(hash(tuple(lines))) % 10**10}.wav"
    postprocess.write_wav(out, (b"\x00\x00" * int(_PAUSE * rate)).join(pcms), rate)
    return out, engines, fallback
