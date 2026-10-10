"""Nhận diện giọng nói bằng Gemini (cloud): cắt audio thành đoạn ~2,5 phút tại khoảng lặng, gửi inline, nhận JSON có mốc thời gian."""

from __future__ import annotations

import base64
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.gemini_client import GeminiBusy, GeminiClient, GeminiError, GeminiModelGone
from app.services.srt_utils import Cue, wrap_lines
from app.services.whisper_stt import STTResult

logger = logging.getLogger(__name__)

# Mốc thời gian do LLM ước lượng trôi dần theo thời lượng (test thực tế: +40 s sau 3 phút nếu gửi cả video) → cắt
# đoạn ~2,5 phút để ít lượt gọi, nhưng cắt TẠI KHOẢNG LẶNG (không cắt ngang câu); đầu mỗi đoạn là mốc chính xác do
# ta cắt, cuối đoạn đã có bước co tỉ lệ. Không tìm được khoảng lặng → cắt ở ``TARGET_SEC``.
TARGET_SEC = 150.0        # độ dài đoạn mong muốn
MIN_SEC = 60.0            # không cắt sớm hơn (để đoạn không quá vụn)
MAX_SEC = 210.0           # không để đoạn dài quá (mốc trôi nhiều)
_SILENCE_DB = -32.0       # ngưỡng im lặng cho silencedetect
_SILENCE_MIN = 0.35       # khoảng lặng tối thiểu (giây) coi là chỗ nghỉ giữa câu
CHUNK_SEC = TARGET_SEC    # tương thích tên cũ (bị ghi đè khi chạy thử)
_LANG_NAMES = {"vi": "Vietnamese", "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "fr": "French",
               "de": "German", "es": "Spanish", "th": "Thai", "id": "Indonesian", "ru": "Russian"}


def _silence_mids(path: Path, ffmpeg: FFmpeg) -> list[float]:
    """Thời điểm GIỮA mỗi khoảng lặng (giây) trong audio — chỗ có thể cắt đoạn mà không cắt ngang câu."""
    import re
    import subprocess

    from app.core.platform import NO_WINDOW

    try:
        r = subprocess.run([ffmpeg.ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
                            "-af", f"silencedetect=noise={_SILENCE_DB}dB:d={_SILENCE_MIN}", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return []
    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", r.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", r.stderr)]
    return [(a + b) / 2 for a, b in zip(starts, ends)]


def _chunk_bounds(duration: float, silences: list[float], *, target: float = TARGET_SEC, min_sec: float = MIN_SEC,
                  max_sec: float = MAX_SEC) -> list[tuple[float, float]]:
    """Chia [0, duration] thành các đoạn (bắt đầu, độ dài): mỗi đoạn kết thúc ở khoảng lặng gần mốc ``target`` nhất
    trong [min_sec, max_sec]; không có khoảng lặng nào trong khoảng đó thì cắt cứng ở ``target``."""
    bounds: list[tuple[float, float]] = []
    pos = 0.0
    while duration - pos > max_sec:
        lo, hi = pos + min_sec, pos + max_sec
        cand = [t for t in silences if lo <= t <= hi]
        cut = min(cand, key=lambda t: abs(t - (pos + target))) if cand else pos + target
        bounds.append((pos, cut - pos))
        pos = cut
    bounds.append((pos, duration - pos))
    return bounds


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
               cancel: threading.Event | None = None, fallback: bool = False) -> STTResult:
    """``fallback``: model quá tải → thử model dự phòng; tắt (mặc định, Cài đặt «tự đổi model») → dừng, báo người dùng."""
    client = GeminiClient(api_key)
    from app.services.gemini_models import DEFAULT_TEXT
    from app.services.translate_router import ModelUnavailable, _load_json, notify_unavailable

    models = [model] + ([m for m in DEFAULT_TEXT if m != model and "pro" not in m][:2] if fallback else [])
    work_dir = work_dir or Path(video_path).parent
    work_dir.mkdir(parents=True, exist_ok=True)
    duration = ffmpeg.duration_of(video_path)
    target = float(globals().get("CHUNK_SEC", TARGET_SEC))        # chạy thử có thể ghi đè CHUNK_SEC
    bounds = _chunk_bounds(duration, _silence_mids(Path(video_path), ffmpeg) if duration > target + MIN_SEC else [],
                           target=target, max_sec=max(target + 1, MAX_SEC if target >= TARGET_SEC else target * 1.4))
    n_chunks = len(bounds)
    lang_note = f"The speech is in {_LANG_NAMES.get(language, language)}." if language not in ("", "auto") else \
        "Detect the language automatically."
    cues: list[Cue] = []
    detected = language if language not in ("", "auto") else ""
    for i, (start, seg_len) in enumerate(bounds):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ nhận diện")
        chunk = work_dir / f"stt_chunk_{i:02d}.mp3"
        if log:
            log(f"Gemini STT: đoạn {i + 1}/{n_chunks} ({start / 60:.0f}–{min(duration, start + seg_len) / 60:.0f} phút)…")
        res = ffmpeg.run_simple(["-ss", f"{start:.3f}", "-t", f"{seg_len:.3f}", "-i", str(video_path), "-vn", "-ac", "1",
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
        parsed = None
        for attempt in range(3):            # model trả JSON hỏng (thiếu dấu phẩy, bị cắt…) → gọi lại, không bỏ cả video
            data = None
            for m in models:                # máy chủ quá tải / model bị gỡ → thử model dự phòng (nếu cho phép)
                try:
                    data = client._post(f"models/{m}:generateContent", body, timeout=600)
                    break
                except (GeminiBusy, GeminiModelGone, GeminiError) as exc:
                    quota = "(429)" in str(exc)
                    if m == models[-1]:
                        if fallback or not (quota or isinstance(exc, GeminiBusy)):
                            raise
                        err = ModelUnavailable("Gemini", model, daily=quota)
                        if log:
                            log(f"✖ {err.message}")
                        notify_unavailable(err)
                        raise err from exc
                    nxt = models[models.index(m) + 1]
                    logger.warning("Gemini STT: %s lỗi (%s) → thử %s", m, exc, nxt)
                    if log:
                        log(f"⚠ Gemini {m} đang quá tải / lỗi — chuyển sang {nxt}…")
            cands = data.get("candidates") or []
            text = "".join(p.get("text", "") for p in (cands[0].get("content", {}).get("parts", []) if cands else []))
            try:
                parsed = _load_json(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and isinstance(parsed.get("cues"), list):
                break
            logger.warning("Gemini STT: đoạn %d trả JSON hỏng (lần %d) — gọi lại", i + 1, attempt + 1)
            if log:
                log(f"⚠ Gemini trả kết quả hỏng ở đoạn {i + 1} — nhận diện lại đoạn này…")
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Gemini trả kết quả nhận diện hỏng 3 lần liền ở đoạn {i + 1} — thử lại sau.")
        if not detected:
            detected = str(parsed.get("language", "")).strip()
        chunk_len = max(0.5, min(seg_len, duration - start))
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
