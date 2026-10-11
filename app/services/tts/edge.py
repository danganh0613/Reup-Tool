"""Edge TTS (miễn phí, giọng Microsoft Neural)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from app.config.paths import CACHE_DIR
from app.services.tts.base import TTSEngine, Voice, part_path

logger = logging.getLogger(__name__)

SYNTH_TIMEOUT = 45.0     # giây cho một câu; câu dài nhất thường < 10 s

_VOICES_CACHE = CACHE_DIR / "edge_voices.json"
_CACHE_TTL = 7 * 24 * 3600

# Ưu tiên hiển thị các locale hay dùng lên đầu
PREFERRED_LOCALES = ["vi-VN", "en-US", "en-GB", "en-AU", "zh-CN", "ja-JP", "ko-KR", "fr-FR", "de-DE",
                     "es-ES", "th-TH", "id-ID", "ru-RU"]


_NET = {"at": 0.0, "down": False}


def _offline() -> bool:
    """Máy chủ Edge không kết nối được (mất mạng / DNS lỗi) — dò nhanh 3 s, nhớ kết quả 20 s cho mọi câu."""
    import socket

    now = time.time()
    if now - _NET["at"] < 20:
        return _NET["down"]
    try:
        socket.create_connection(("speech.platform.bing.com", 443), timeout=3).close()
        down = False
    except OSError:
        down = True
    _NET.update(at=now, down=down)
    return down


class EdgeTTS(TTSEngine):
    name = "edge"
    native_speed_range = (0.5, 2.0)
    workers = 4
    qc_retries = 1                       # miễn phí → câu không đạt kiểm tra được đọc lại một lần

    def __init__(self) -> None:
        self._voices: list[Voice] | None = None

    # ── Voices ────────────────────────────────────────────────────────────
    def _fetch_voices(self) -> list[dict]:
        import edge_tts

        return asyncio.run(asyncio.wait_for(edge_tts.list_voices(), timeout=30))

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        if self._voices is None:
            raw: list[dict] | None = None
            try:
                if _VOICES_CACHE.exists() and time.time() - _VOICES_CACHE.stat().st_mtime < _CACHE_TTL:
                    raw = json.loads(_VOICES_CACHE.read_text(encoding="utf-8"))
            except Exception:
                raw = None
            if not raw:
                raw = self._fetch_voices()
                try:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    _VOICES_CACHE.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    logger.debug("Không ghi được cache voice Edge", exc_info=True)
            voices: list[Voice] = []
            for v in raw:
                short = v.get("ShortName") or v.get("Name", "")
                loc = v.get("Locale", "")
                friendly = (v.get("FriendlyName") or short).replace("Microsoft ", "").replace(" Online (Natural)", "")
                voices.append(Voice(id=short, name=friendly, locale=loc, gender=v.get("Gender", ""),
                                    extra={"personalities": (v.get("VoiceTag") or {}).get("VoicePersonalities", [])}))
            # giọng «Multilingual» đọc được tiếng Việt (hơi lơ lớ giọng Tây) → thêm vào danh sách tiếng Việt
            multi = [v for v in voices if "Multilingual" in v.id]
            for v in multi:
                voices.append(Voice(id=v.id, name=f"🌐 {v.name.split(' - ')[0]} (đa ngôn ngữ, hơi lơ lớ)",
                                    locale="vi-VN", gender=v.gender, extra=dict(v.extra)))
            voices.sort(key=lambda x: (PREFERRED_LOCALES.index(x.locale) if x.locale in PREFERRED_LOCALES else 99,
                                       x.locale, "🌐" in x.name, x.gender, x.name))
            self._voices = voices
        if locale:
            return [v for v in self._voices if v.locale == locale]
        return list(self._voices)

    def locales(self) -> list[str]:
        seen: dict[str, None] = {}
        for v in self.list_voices():
            seen.setdefault(v.locale, None)
        return list(seen)

    # ── Synthesize ────────────────────────────────────────────────────────
    def synthesize(self, text: str, voice_id: str, out_path: Path, *, speed: float = 1.0, pitch: int = 0) -> Path:
        import edge_tts

        speed = self.clamp_speed(speed)
        rate = f"{int(round((speed - 1.0) * 100)):+d}%"
        pitch_s = f"{int(pitch):+d}Hz"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = part_path(out_path)
        last: Exception | None = None
        for attempt in range(5):
            try:
                com = edge_tts.Communicate(text, voice_id, rate=rate, pitch=pitch_s)
                # websocket có thể treo vô hạn khi mạng chập chờn → giới hạn thời gian mỗi câu rồi thử lại
                # câu dài cần lâu hơn; câu ngắn mà treo thì bỏ sớm để thử lại (Edge đôi lúc treo im khi quá tải)
                asyncio.run(asyncio.wait_for(com.save(str(tmp)), timeout=min(SYNTH_TIMEOUT, 12 + 0.1 * len(text))))
                if tmp.exists() and tmp.stat().st_size > 0:
                    tmp.replace(out_path)
                    return out_path
                raise RuntimeError("Edge TTS trả về file rỗng")
            except Exception as exc:
                last = exc
                logger.warning("Edge TTS lỗi (lần %d): %s", attempt + 1, exc)
                if _offline():
                    raise RuntimeError("Mất mạng — giọng Edge cần Internet. Kiểm tra mạng rồi Thử lại.") from exc
                if "No audio was received" in str(exc) and attempt >= 1:
                    # Edge trả rỗng ngay lập tức khi giọng không đọc được văn bản (khác ngôn ngữ) → thử lại vô ích
                    raise RuntimeError("Edge TTS không đọc được câu này (ngôn ngữ chữ không khớp giọng, hoặc câu chỉ có ký hiệu): "
                                       + text[:60]) from exc
                time.sleep(1.0 + attempt * 1.5)
        raise RuntimeError(f"Edge TTS thất bại: {last}")
