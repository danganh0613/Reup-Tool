"""Nhận diện giọng nói bằng Gemini (cloud): cắt audio thành đoạn ~8 phút, gửi inline, nhận JSON có mốc thời gian."""

from __future__ import annotations

import base64
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.gemini_client import GeminiClient
from app.services.srt_utils import Cue, wrap_lines
from app.services.whisper_stt import STTResult

logger = logging.getLogger(__name__)

# Mốc thời gian do LLM ước lượng trôi dần theo thời lượng (test thực tế: +40 s sau 3 phút) → gửi đoạn ngắn để
# sai số không tích luỹ; đầu mỗi đoạn là mốc chính xác do ta cắt.
CHUNK_SEC = 60
_LANG_NAMES = {"vi": "Vietnamese", "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "fr": "French",
               "de": "German", "es": "Spanish", "th": "Thai", "id": "Indonesian", "ru": "Russian"}


def _parse_ts(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    parts = s.replace(",", ".").split(":")
    try:
        parts_f = [float(p) for p in parts]
    except ValueError:
        return 0.0
    t = 0.0
    for p in parts_f:
        t = t * 60 + p
    return t


def transcribe(video_path: str | Path, ffmpeg: FFmpeg, *, api_key: str | list[str], model: str = "gemini-flash-latest",
               language: str = "auto", work_dir: Path | None = None,
               on_progress: Callable[[float], None] | None = None,
               log: Callable[[str], None] | None = None,
               cancel: threading.Event | None = None) -> STTResult:
    client = GeminiClient(api_key)
    work_dir = work_dir or Path(video_path).parent
    work_dir.mkdir(parents=True, exist_ok=True)
    duration = ffmpeg.duration_of(video_path)
    n_chunks = max(1, int(duration // CHUNK_SEC) + (1 if duration % CHUNK_SEC > 1 else 0))
    lang_note = f"The speech is in {_LANG_NAMES.get(language, language)}." if language not in ("", "auto") else \
        "Detect the language automatically."
    cues: list[Cue] = []
    detected = language if language not in ("", "auto") else ""
    for i in range(n_chunks):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ nhận diện")
        start = i * CHUNK_SEC
        chunk = work_dir / f"stt_chunk_{i:02d}.mp3"
        if log:
            log(f"Gemini STT: đoạn {i + 1}/{n_chunks} ({start / 60:.0f}–{min(duration, start + CHUNK_SEC) / 60:.0f} phút)…")
        res = ffmpeg.run_simple(["-ss", f"{start:.3f}", "-t", f"{CHUNK_SEC}", "-i", str(video_path), "-vn", "-ac", "1",
                                 "-ar", "16000", "-b:a", "48k", str(chunk)], timeout=300)
        if res.returncode != 0 or not chunk.exists():
            raise RuntimeError("Không tách được audio để gửi Gemini")
        audio_b64 = base64.b64encode(chunk.read_bytes()).decode()
        prompt = (
            f"Transcribe this audio verbatim as subtitles. {lang_note}\n"
            "Split into short cues (max ~2 lines, <= 6 seconds each), with accurate start/end times in seconds "
            "relative to the start of THIS audio. Do not translate. Skip music and noise.\n"
            "Return ONLY JSON: {\"language\": \"<ISO 639-1>\", \"cues\": [{\"start\": 0.0, \"end\": 2.5, \"text\": \"...\"}]}"
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt},
                                                    {"inlineData": {"mimeType": "audio/mp3", "data": audio_b64}}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json", "maxOutputTokens": 65536},
        }
        data = client._post(f"models/{model}:generateContent", body, timeout=600)
        cands = data.get("candidates") or []
        text = "".join(p.get("text", "") for p in (cands[0].get("content", {}).get("parts", []) if cands else []))
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            s, e = text.find("{"), text.rfind("}")
            parsed = json.loads(text[s:e + 1]) if s >= 0 else {}
        if not detected:
            detected = str(parsed.get("language", "")).strip()
        chunk_len = max(0.5, min(CHUNK_SEC, duration - start))
        raw: list[tuple[float, float, str]] = []
        for c in parsed.get("cues", []) or []:
            if not isinstance(c, dict):
                continue
            t0, t1 = _parse_ts(c.get("start", 0)), _parse_ts(c.get("end", 0))
            txt = str(c.get("text", "")).strip()
            if not txt or t1 <= t0:
                continue
            raw.append((t0, t1, txt))
        raw.sort(key=lambda r: r[0])
        # mốc vượt quá độ dài đoạn → co lại theo tỉ lệ, rồi kẹp trong đoạn
        last_end = max((r[1] for r in raw), default=0.0)
        scale = chunk_len / last_end if last_end > chunk_len * 1.03 else 1.0
        if scale < 1.0:
            logger.info("Gemini STT: mốc đoạn %d vượt %.1fs → co %.2f", i + 1, last_end - chunk_len, scale)
        for t0, t1, txt in raw:
            t0, t1 = min(chunk_len, t0 * scale), min(chunk_len, t1 * scale)
            if t1 - t0 < 0.2:
                t1 = min(chunk_len, t0 + 0.2)
            cues.append(Cue(len(cues) + 1, t0 + start, t1 + start, wrap_lines(txt)))
        try:
            chunk.unlink()
        except OSError:
            pass
        if on_progress:
            on_progress((i + 1) / n_chunks)
    cues.sort(key=lambda c: c.start)
    for i, c in enumerate(cues):
        c.index = i + 1
        if i + 1 < len(cues) and c.end > cues[i + 1].start:
            c.end = max(c.start + 0.2, cues[i + 1].start - 0.02)
    return STTResult(cues=cues, language=detected, language_probability=1.0)
