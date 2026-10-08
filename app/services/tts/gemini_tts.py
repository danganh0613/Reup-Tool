"""Gemini TTS (Google AI) — 30 giọng đọc đa ngôn ngữ (có tiếng Việt), chỉnh phong cách bằng lời dặn.

Dùng chung key Gemini với phần dịch (Cài đặt → Gemini). Chất lượng cao, đọc được giọng vùng miền, cảm xúc
(«review phim cuốn hút», «giọng miền Nam», «thì thầm»…). Gói miễn phí rất ít lượt mỗi ngày → video dài nên bật
thanh toán (khoảng vài trăm đồng mỗi phút giọng), hoặc nhập nhiều key để máy tự xoay vòng.

REST: ``models/{model}:generateContent`` với ``responseModalities=["AUDIO"]`` → âm thanh trong ``inlineData``:
model 2.5 trả PCM 16-bit thô (``audio/L16;rate=24000``), model 3.x trả nguyên file WAV (``audio/wav``: header + âm
thanh + khối chữ ký C2PA ở cuối) — chỉ lấy khối «data», ghi ra WAV sạch.
"""

from __future__ import annotations

import base64
import logging
import random
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np
import requests

from app.core.state import GEMINI_TTS_MODELS
from app.services.tts import postprocess
from app.services.tts.base import TTSEngine, Voice

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

#: lời dặn nằm trong khung đánh dấu rõ — kiểu cũ «{lời dặn}:\n{câu}» làm gemini-3.8-flash-tts đọc to cả lời dặn;
#: systemInstruction bị model TTS từ chối («Developer instruction is not enabled») — xem audit_out/gemini_prompt
PROMPT = ("[Lời dặn cách đọc — chỉ để định giọng, KHÔNG đọc thành tiếng]\n{style}\n\n"
          "[Lời thoại — chỉ đọc nguyên văn phần này]\n{text}")

#: đọc gộp nhiều câu một lượt (tts/batch.py tách lại): nghỉ rõ giữa các dòng để có chỗ cắt, không đọc số thứ tự
BATCH_PROMPT = ("[Lời dặn cách đọc — chỉ để định giọng, KHÔNG đọc thành tiếng]\n{style}"
                "Đọc lần lượt từng dòng lời thoại bên dưới. Hết mỗi dòng nghỉ hẳn khoảng một giây rồi mới đọc dòng sau. "
                "Không đọc số thứ tự, không thêm hay bỏ chữ nào.\n\n"
                "[Lời thoại — chỉ đọc nguyên văn phần này]\n{text}")

_working_model: dict[str, str] = {}     # model chọn → model thật sự dùng được (sau khi tự thử model kế tiếp)
#: (model, key) → hết lượt NGÀY tới thời điểm này (time.time()): không gọi lại key đó cho model đó tới lúc hồi
_exhausted: dict[tuple[str, str], float] = {}
_lock = threading.Lock()


def _quota_info(r: requests.Response) -> tuple[bool, float | None]:
    """429 → (hết lượt theo NGÀY?, số giây máy chủ bảo chờ). Đọc cả khối ``details`` (quotaId «…PerDay…»,
    retryDelay) — 300 ký tự đầu chỉ có câu «You exceeded your current quota» nên trước đây tưởng là giới hạn phút,
    thử lại ~14 lần kèm chờ cho TỪNG câu."""
    try:
        err = r.json().get("error") or {}
    except ValueError:
        return "per day" in r.text.lower() or "PerDay" in r.text, None
    daily, delay = "per day" in str(err.get("message", "")).lower(), None
    for d in err.get("details") or []:
        for v in d.get("violations") or []:
            if "PerDay" in str(v.get("quotaId", "")) or "per_day" in str(v.get("quotaMetric", "")).lower():
                daily = True
        rd = str(d.get("retryDelay") or "").rstrip("s")
        try:
            delay = float(rd) if rd else delay
        except ValueError:
            pass
    return daily or (delay is not None and delay > 3600), delay


def _wait(seconds: float) -> None:
    """Chờ có ngẫu nhiên: nhiều luồng / nhiều key lỗi cùng lúc không gọi lại đúng cùng một nhịp."""
    time.sleep(seconds * random.uniform(0.6, 1.2))


def style_prompt(style: str, custom: str) -> str:
    p = STYLES.get(style, "")
    if p is None:
        p = (custom or "").strip()
    return p


class GeminiTTS(TTSEngine):
    name = "gemini_tts"
    ext = ".wav"
    native_speed_range = (1.0, 1.0)      # tốc độ chỉnh bằng atempo khi ghép
    workers = 1                          # gói miễn phí ~15 lượt/phút: gọi song song chỉ thêm lỗi 429
    qc_retries = 1                       # mỗi lần đọc lại tốn một lượt API
    reread_soft = False                  # lỗi nhẹ (Whisper nghe «Bắt» thành «Bắc») không đáng một lượt gọi
    max_chars = 200                      # model sinh âm thanh kiểu LLM: câu quá dài dễ lạc / đọc thêm
    batch_lines = 25                     # gói miễn phí ~15 lượt/phút → gộp ~25 câu một lượt (thử thật: tách đúng 25/25)

    def __init__(self, keys: list[str], *, model: str = GEMINI_TTS_MODELS[0], style: str = "Tự nhiên",
                 custom_style: str = "", temperature: float | None = 0.7) -> None:
        self.keys = [k for k in dict.fromkeys(k.strip() for k in keys) if k]
        self._key_idx = 0
        self.model = model or GEMINI_TTS_MODELS[0]
        self.style = style_prompt(style, custom_style)
        self.temperature = temperature

    def cache_signature(self, voice_id: str, speed: float, pitch: int) -> str:
        # «p2» = kiểu prompt mới: bản cache cũ có lời dặn (có thể đã bị đọc to) không dùng lại; câu «Tự nhiên» giữ cache.
        # temperature khác mặc định 0,7 (Đạo diễn âm thanh chỉnh theo bậc) → khoá riêng; mặc định giữ khoá cũ
        temp = "" if self.temperature in (None, 0.7) else f"|t{self.temperature:.2f}"
        return f"{self.name}|{voice_id}|{self.model}|{self.style}" + ("|p2" if self.style else "") + temp

    def repair_cached(self, path: Path) -> None:
        """Bản cũ ghi nguyên file WAV Gemini trả về (header + chữ ký C2PA) vào trong một WAV khác → «tách» ở đầu,
        ~125 ms nhiễu to ở cuối mọi câu. Bóc lại đúng phần âm thanh — không tốn lượt API."""
        try:
            with wave.open(str(path)) as wf:
                frames = wf.readframes(wf.getnframes())
        except Exception:
            return
        w = postprocess.parse_wav(frames)
        if w is None:
            return
        pcm, rate, ch, bits, tag = w
        postprocess.write_wav(path, postprocess.to_mono16(pcm, ch, bits, tag), rate)
        logger.info("Đã sửa file cache Gemini cũ (WAV lồng trong WAV + C2PA): %s", path.name)

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        return [Voice(id=n, name=f"{n} — {d}", locale="", gender=g) for n, g, d in VOICES]

    # ── Gọi API ───────────────────────────────────────────────────────────
    def _models(self) -> list[str]:
        first = _working_model.get(self.model, self.model)
        return list(dict.fromkeys([first, self.model, *GEMINI_TTS_MODELS]))

    def _key(self) -> str:
        if not self.keys:
            raise RuntimeError("Chưa có Gemini API key — vào Cài đặt → Gemini để nhập (dùng chung với phần dịch).")
        return self.keys[self._key_idx % len(self.keys)]

    def _body(self, text: str, voice_id: str, style: str) -> dict:
        gen: dict = {"responseModalities": ["AUDIO"],
                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_id or "Leda"}}}}
        if self.temperature is not None:
            gen["temperature"] = self.temperature            # cố định → các câu cùng một chất giọng
        prompt = PROMPT.format(style=style, text=text) if style else text
        return {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}

    @staticmethod
    def _too_long(pcm: bytes, rate: int, text: str) -> bool:
        """Âm thanh dài bất thường so với số tiếng của câu → nhiều khả năng model đọc cả lời dặn / đọc thêm."""
        from app.services.tts.quality import expected_duration

        sh = postprocess.shape(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768, rate)
        return sh is not None and (sh.last - sh.first + 1) * postprocess.FRAME > 1.7 * expected_duration(text) + 1.0

    def synthesize(self, text: str, voice_id: str, out_path: Path, *, speed: float = 1.0, pitch: int = 0) -> Path:
        out_path = out_path.with_suffix(".wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pcm, rate = self._request(self._body(text, voice_id, self.style))
        if self.style and self._too_long(pcm, rate, text):
            logger.warning("Gemini TTS: câu «%s» dài bất thường — có thể đã đọc cả lời dặn → đọc lại không kèm lời dặn",
                           text[:50])
            pcm, rate = self._request(self._body(text, voice_id, ""))
        return postprocess.write_wav(out_path, pcm, rate)

    def synthesize_batch(self, lines: list[str], voice_id: str) -> tuple[bytes, int]:
        body = self._body("", voice_id, "")
        body["contents"][0]["parts"][0]["text"] = BATCH_PROMPT.format(
            style=f"{self.style}\n" if self.style else "", text="\n".join(lines))
        return self._request(body)

    def _alive(self, model: str) -> list[str]:
        """Key còn lượt NGÀY cho ``model`` (key đã báo hết lượt hôm nay thì bỏ qua tới lúc hồi)."""
        now = time.time()
        return [k for k in self.keys if _exhausted.get((model, k), 0.0) <= now]

    def _request(self, body: dict) -> tuple[bytes, int]:
        models = self._models()
        mi = 0
        tried_keys = 0
        last = ""
        self._key()                                       # chưa có key → báo ngay
        for attempt in range(8 + 2 * max(1, len(self.keys)) + len(models)):
            model = models[mi]
            alive = self._alive(model)
            if not alive:
                # mọi key hết lượt NGÀY của model này (hạn mức tính theo từng model) → model TTS kế tiếp; hết cả thì
                # báo lỗi ngay để engine dự phòng (Edge) đọc, không thử lại từng câu
                if mi + 1 < len(models):
                    logger.warning("Gemini TTS: %s hết lượt hôm nay (mọi key) → dùng %s", model, models[mi + 1])
                    mi += 1
                    continue
                raise RuntimeError("Gemini TTS hết lượt hôm nay với mọi key / model (gói miễn phí giới hạn số lượt mỗi "
                                   "ngày cho từng model, hồi lúc nửa đêm giờ Mỹ) — bật thanh toán cho key hoặc chọn "
                                   "nhà cung cấp khác. " + last[:120])
            key = alive[self._key_idx % len(alive)]
            try:
                r = requests.post(f"{BASE}/models/{model}:generateContent", headers={"x-goog-api-key": key},
                                  json=body, timeout=180)
            except requests.RequestException as exc:
                last = str(exc)
                _wait(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                pcm, rate = self._audio(r.json())
                if not pcm:
                    last = "Gemini không trả âm thanh (câu bị chặn hoặc trống)"
                    _wait(1.0)
                    continue
                with _lock:
                    _working_model[self.model] = model
                return pcm, rate
            msg = r.text[:300]
            last = f"{r.status_code}: {msg}"
            if r.status_code in (404, 400) and ("not found" in msg.lower() or "not supported" in msg.lower()
                                                or "does not support" in msg.lower()) and mi + 1 < len(models):
                logger.info("Gemini TTS: model %s không dùng được → thử %s", model, models[mi + 1])
                mi += 1
                continue
            if r.status_code == 429:
                daily, delay = _quota_info(r)
                if daily:                                  # hết lượt NGÀY: nhớ lại, không gọi key này nữa tới lúc hồi
                    with _lock:
                        _exhausted[(model, key)] = time.time() + (delay if delay and delay > 60 else 6 * 3600)
                    logger.info("Gemini TTS: một key hết lượt hôm nay cho %s (còn %d key)", model, len(alive) - 1)
                    continue
            if r.status_code in (401, 403, 429):
                # hết lượt / key lỗi → key kế tiếp; đã thử hết key thì đợi chút (giới hạn theo phút) rồi thử lại
                tried_keys += 1
                self._key_idx += 1
                if tried_keys % max(1, len(alive)) == 0:
                    if r.status_code != 429:
                        raise RuntimeError(
                            "Gemini TTS hết lượt / key không dùng được (" + msg[:160] + "). Gói miễn phí chỉ vài lượt "
                            "mỗi ngày — bật thanh toán cho key, thêm key khác (mỗi dòng một key) hoặc chọn nhà cung cấp khác.")
                    _wait(min(30.0, 6.0 * (attempt + 1)))
                continue
            if r.status_code >= 500:
                _wait(2.0 * (attempt + 1))
                continue
            raise RuntimeError(f"Gemini TTS lỗi {last}")
        raise RuntimeError(f"Gemini TTS thất bại: {last}")

    @staticmethod
    def _audio(data: dict) -> tuple[bytes, int]:
        """→ (PCM s16 mono, tần số). WAV (model 3.x) → chỉ lấy khối «data», bỏ header + chữ ký C2PA; PCM thô
        (``audio/L16`` / ``pcm`` / có ``rate=``, model 2.5) giữ nguyên; định dạng khác (mp3, ogg…) giải mã bằng ffmpeg —
        không bao giờ coi một file nén là PCM (sẽ thành tiếng rè)."""
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    raw = base64.b64decode(inline["data"])
                    w = postprocess.parse_wav(raw)
                    if w is not None:
                        pcm, rate, ch, bits, tag = w
                        return postprocess.to_mono16(pcm, ch, bits, tag), rate
                    mime = (inline.get("mimeType") or inline.get("mime_type") or "").lower()
                    m = re.search(r"rate=(\d+)", mime)
                    if m or "l16" in mime or "pcm" in mime:
                        return raw[: len(raw) // 2 * 2], int(m.group(1)) if m else 24000
                    return GeminiTTS._decode_other(raw, mime)
        return b"", 24000

    @staticmethod
    def _decode_other(raw: bytes, mime: str) -> tuple[bytes, int]:
        import tempfile

        ext = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg", "audio/opus": ".opus",
               "audio/aac": ".aac", "audio/flac": ".flac"}.get(mime.split(";")[0].strip(), ".bin")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"gemini{ext}"
            p.write_bytes(raw)
            r = postprocess.decode_any(p)
        if r is None:
            raise RuntimeError(f"Gemini TTS trả định dạng âm thanh lạ ({mime or 'không rõ'}) — không giải mã được.")
        logger.info("Gemini TTS: âm thanh dạng %s → đã giải mã bằng ffmpeg", mime or "không rõ")
        return r
