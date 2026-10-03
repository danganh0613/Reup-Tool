"""Gemini TTS (Google AI) — 30 giọng đọc đa ngôn ngữ (có tiếng Việt), chỉnh phong cách bằng lời dặn.

Dùng chung key Gemini với phần dịch (Cài đặt → Gemini). Chất lượng cao, đọc được giọng vùng miền, cảm xúc
(«review phim cuốn hút», «giọng miền Nam», «thì thầm»…). Gói miễn phí rất ít lượt mỗi ngày → video dài nên bật
thanh toán (khoảng vài trăm đồng mỗi phút giọng), hoặc nhập nhiều key để máy tự xoay vòng.

REST: ``models/{model}:generateContent`` với ``responseModalities=["AUDIO"]`` → PCM 16-bit mono (thường 24 kHz)
trong ``inlineData``; ghi ra WAV.
"""

from __future__ import annotations

import base64
import logging
import re
import threading
import time
import wave
from pathlib import Path

import requests

from app.core.state import GEMINI_TTS_MODELS
from app.services.tts.base import TTSEngine, Voice, part_path

logger = logging.getLogger(__name__)

BASE = "https://generativelanguage.googleapis.com/v1beta"

#: (tên, giới tính, chất giọng) — 30 giọng dựng sẵn của Gemini TTS
VOICES: list[tuple[str, str, str]] = [
    ("Leda", "Female", "trẻ trung"), ("Kore", "Female", "chắc, rõ ràng"), ("Aoede", "Female", "nhẹ nhàng, thoáng"),
    ("Zephyr", "Female", "trong, sáng"), ("Autonoe", "Female", "tươi sáng"), ("Callirrhoe", "Female", "thoải mái"),
    ("Despina", "Female", "mượt mà"), ("Erinome", "Female", "rõ ràng"), ("Laomedeia", "Female", "vui tươi"),
    ("Achernar", "Female", "mềm mại"), ("Gacrux", "Female", "chín chắn"), ("Pulcherrima", "Female", "dứt khoát"),
    ("Vindemiatrix", "Female", "dịu dàng"), ("Sulafat", "Female", "ấm áp"),
    ("Puck", "Male", "hào hứng"), ("Charon", "Male", "rõ, kiểu thuyết minh"), ("Fenrir", "Male", "sôi nổi, trầm"),
    ("Orus", "Male", "chắc giọng"), ("Enceladus", "Male", "nhiều hơi thở"), ("Iapetus", "Male", "rõ ràng"),
    ("Umbriel", "Male", "thoải mái"), ("Algieba", "Male", "mượt mà"), ("Algenib", "Male", "khàn, có chất"),
    ("Rasalgethi", "Male", "kiểu thuyết minh"), ("Alnilam", "Male", "chắc giọng"), ("Schedar", "Male", "đều, điềm tĩnh"),
    ("Achird", "Male", "thân thiện"), ("Zubenelgenubi", "Male", "tự nhiên, đời thường"),
    ("Sadachbia", "Male", "sinh động"), ("Sadaltager", "Male", "hiểu biết, từ tốn"),
]

#: phong cách đọc → lời dặn đặt trước câu thoại ("" = đọc tự nhiên)
STYLES: dict[str, str] = {
    "Tự nhiên": "",
    "Review phim cuốn hút": "Đọc bằng tiếng Việt như người review phim trên TikTok: cuốn hút, nhịp nhanh, nhấn nhá kịch tính",
    "Kể chuyện trầm ấm": "Đọc bằng tiếng Việt như kể chuyện đêm khuya: trầm ấm, chậm rãi, truyền cảm",
    "Tin tức / thời sự": "Đọc bằng tiếng Việt như phát thanh viên thời sự: rõ ràng, trang trọng, nhịp đều",
    "Hài hước, tếu táo": "Đọc bằng tiếng Việt thật hài hước, tếu táo, tinh nghịch",
    "Bán hàng sôi nổi": "Đọc bằng tiếng Việt như livestream bán hàng: sôi nổi, hào hứng, thuyết phục",
    "Kinh dị, thì thầm": "Đọc bằng tiếng Việt giọng thì thầm rùng rợn, chậm, căng thẳng",
    "Thuyết minh phim": "Đọc bằng tiếng Việt như thuyết minh phim truyền hình: đều giọng, rõ chữ, không cường điệu",
    "Giọng miền Bắc": "Đọc bằng tiếng Việt giọng Hà Nội (miền Bắc) chuẩn, tự nhiên",
    "Giọng miền Nam": "Đọc bằng tiếng Việt giọng Sài Gòn (miền Nam) tự nhiên, thân thiện",
    "Giọng miền Trung": "Đọc bằng tiếng Việt giọng Huế / miền Trung tự nhiên",
    "Tự viết…": None,       # dùng TTSConfig.gemini_style_custom
}

_working_model: dict[str, str] = {}     # model chọn → model thật sự dùng được (sau khi tự thử model kế tiếp)
_lock = threading.Lock()


def style_prompt(style: str, custom: str) -> str:
    p = STYLES.get(style, "")
    if p is None:
        p = (custom or "").strip()
    return p


class GeminiTTS(TTSEngine):
    name = "gemini_tts"
    ext = ".wav"
    native_speed_range = (1.0, 1.0)      # tốc độ chỉnh bằng atempo khi ghép
    workers = 2

    def __init__(self, keys: list[str], *, model: str = GEMINI_TTS_MODELS[0], style: str = "Tự nhiên",
                 custom_style: str = "") -> None:
        self.keys = [k for k in dict.fromkeys(k.strip() for k in keys) if k]
        self._key_idx = 0
        self.model = model or GEMINI_TTS_MODELS[0]
        self.style = style_prompt(style, custom_style)

    def cache_signature(self, voice_id: str, speed: float, pitch: int) -> str:
        return f"{self.name}|{voice_id}|{self.model}|{self.style}"

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        return [Voice(id=n, name=f"{n} — {d}", locale="", gender=g) for n, g, d in VOICES]

    # ── Gọi API ───────────────────────────────────────────────────────────
    def _models(self) -> list[str]:
        first = _working_model.get(self.model, self.model)
        return [first] + [m for m in [self.model, *GEMINI_TTS_MODELS] if m != first]

    def _key(self) -> str:
        if not self.keys:
            raise RuntimeError("Chưa có Gemini API key — vào Cài đặt → Gemini để nhập (dùng chung với phần dịch).")
        return self.keys[self._key_idx % len(self.keys)]

    def synthesize(self, text: str, voice_id: str, out_path: Path, *, speed: float = 1.0, pitch: int = 0) -> Path:
        prompt = f"{self.style}:\n{text}" if self.style else text
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                                         "voiceName": voice_id or "Leda"}}}}}
        out_path = out_path.with_suffix(".wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        models = self._models()
        mi = 0
        tried_keys = 0
        last = ""
        for attempt in range(8 + 2 * max(1, len(self.keys))):
            model = models[mi]
            try:
                r = requests.post(f"{BASE}/models/{model}:generateContent", headers={"x-goog-api-key": self._key()},
                                  json=body, timeout=180)
            except requests.RequestException as exc:
                last = str(exc)
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                pcm, rate = self._audio(r.json())
                if not pcm:
                    last = "Gemini không trả âm thanh (câu bị chặn hoặc trống)"
                    time.sleep(1.0)
                    continue
                with _lock:
                    _working_model[self.model] = model
                tmp = part_path(out_path)
                with wave.open(str(tmp), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(rate)
                    wf.writeframes(pcm)
                tmp.replace(out_path)
                return out_path
            msg = r.text[:300]
            last = f"{r.status_code}: {msg}"
            if r.status_code in (404, 400) and ("not found" in msg.lower() or "not supported" in msg.lower()
                                                or "does not support" in msg.lower()) and mi + 1 < len(models):
                logger.info("Gemini TTS: model %s không dùng được → thử %s", model, models[mi + 1])
                mi += 1
                continue
            if r.status_code in (401, 403, 429):
                # hết lượt / key lỗi → key kế tiếp; đã thử hết key thì đợi chút (giới hạn theo phút) rồi thử lại
                tried_keys += 1
                self._key_idx += 1
                if tried_keys % max(1, len(self.keys)) == 0:
                    if r.status_code != 429 or "per day" in msg.lower() or "PerDay" in msg:
                        raise RuntimeError(
                            "Gemini TTS hết lượt / key không dùng được (" + msg[:160] + "). Gói miễn phí chỉ vài lượt "
                            "mỗi ngày — bật thanh toán cho key, thêm key khác (mỗi dòng một key) hoặc chọn nhà cung cấp khác.")
                    time.sleep(min(30.0, 6.0 * (attempt + 1)))
                continue
            if r.status_code >= 500:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise RuntimeError(f"Gemini TTS lỗi {last}")
        raise RuntimeError(f"Gemini TTS thất bại: {last}")

    @staticmethod
    def _audio(data: dict) -> tuple[bytes, int]:
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    mime = inline.get("mimeType") or inline.get("mime_type") or ""
                    m = re.search(r"rate=(\d+)", mime)
                    return base64.b64decode(inline["data"]), int(m.group(1)) if m else 24000
        return b"", 24000
