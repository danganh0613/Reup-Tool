"""Nhận diện tiếng Trung bằng FunASR SenseVoice-Small (local, chạy được CPU): sai ~7,8 % ký tự so với ~20 % của
Whisper với tiếng Trung. Mốc TỪNG CHỮ (output_timestamp) → ghép câu theo dấu câu → phụ đề.

VAD fsmn-vad không dùng để cắt đoạn: video có nhạc nền liền thì VAD trả một đoạn 60 s (đo trên video mẫu). Thay vào đó
cắt cửa sổ 60 s gối nhau 5 s, lấy chữ của mỗi cửa sổ tới giữa phần gối. Model tải về models/modelscope (~0,9 GB, một lần).
Tên riêng / tên con vật hiếm vẫn hay sai — video có phụ đề in sẵn thì dùng OCR (ocr_hardsub) chính xác hơn.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from app.config.paths import MODELS_DIR
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.srt_utils import Cue, wrap_lines
from app.services.whisper_stt import STTResult

logger = logging.getLogger(__name__)

MODEL = "iic/SenseVoiceSmall"
SR = 16000
WIN, HOP = 60.0, 55.0                 # cửa sổ 60 s, bước 55 s (gối 5 s, cắt ở giữa phần gối)
MAX_CHARS, MAX_DUR = 18, 5.0          # một câu phụ đề tối đa
_HARD_END = set("。！？!?")
_SOFT_END = set("，,、；;：:")
_LANGS = {"zh": "zh", "yue": "yue", "en": "en", "ja": "ja", "ko": "ko"}
_model = None
_lock = threading.Lock()


def _load(log: Callable[[str], None] | None = None):
    global _model
    with _lock:
        if _model is None:
            os.environ.setdefault("MODELSCOPE_CACHE", str(MODELS_DIR / "modelscope"))
            import torch
            from funasr import AutoModel

            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            if log:
                log(f"Nạp SenseVoice-Small ({device})… lần đầu tải model ~0,9 GB")
            _model = AutoModel(model=MODEL, disable_update=True, device=device)
        return _model


def build_cues(tokens: list[tuple[str, float, float]]) -> list[Cue]:
    """(chữ, giây đầu, giây cuối) → câu phụ đề: ngắt ở dấu kết câu; ở dấu phẩy khi câu đã đủ dài; ngắt cứng khi quá
    ``MAX_CHARS`` chữ / ``MAX_DUR`` giây hoặc lặng > 1 s giữa hai chữ."""
    cues: list[Cue] = []
    cur: list[tuple[str, float, float]] = []

    def flush() -> None:
        text = "".join(c for c, _s, _e in cur).strip().rstrip("".join(_SOFT_END)).strip()
        if text and any(not (c in _HARD_END or c in _SOFT_END) for c in text):
            cues.append(Cue(len(cues) + 1, cur[0][1], cur[-1][2] + 0.25, wrap_lines(text)))
        cur.clear()

    for ch, s, e in tokens:
        if cur and s - cur[-1][2] > 1.0:
            flush()
        cur.append((ch, s, e))
        text = "".join(c for c, _s, _e in cur)
        dur = e - cur[0][1]
        if ch in _HARD_END or (ch in _SOFT_END and (len(text) >= 8 or dur >= 3.0)) \
                or len(text) >= MAX_CHARS or dur >= MAX_DUR:
            flush()
    if cur:
        flush()
    for a, b in zip(cues, cues[1:]):                  # đuôi +0,25 s không được chồng câu sau
        a.end = max(a.start + 0.2, min(a.end, b.start - 0.02))
    return cues


def transcribe(video_path: str | Path, ffmpeg: FFmpeg, *, language: str = "zh", work_dir: Path | None = None,
               on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] | None = None,
               cancel: threading.Event | None = None) -> STTResult:
    tokens, detected, total = recognize_tokens(video_path, ffmpeg, language=language, work_dir=work_dir,
                                               on_progress=on_progress, log=log, cancel=cancel)
    cues = build_cues(tokens)
    if log:
        log(f"SenseVoice: {len(cues)} câu từ {len(tokens)} chữ ({total:.0f} s audio)")
    return STTResult(cues=cues, language=detected or language, language_probability=1.0)


def recognize_tokens(video_path: str | Path, ffmpeg: FFmpeg, *, language: str = "zh", work_dir: Path | None = None,
                     on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] | None = None,
                     cancel: threading.Event | None = None) -> tuple[list[tuple[str, float, float]], str, float]:
    """→ ([(chữ, giây đầu, giây cuối)], ngôn ngữ nhận ra, độ dài audio). OCR dùng để lấy mốc tiếng nói."""
    import soundfile as sf

    work_dir = Path(work_dir or Path(video_path).parent)
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = ffmpeg.extract_audio_wav(video_path, work_dir / "sensevoice_16k.wav", sample_rate=SR, channels=1)
    audio, sr = sf.read(str(wav), dtype="float32")
    model = _load(log)
    lang = _LANGS.get(language, "auto")
    total = len(audio) / sr
    starts = [0.0]
    while starts[-1] + WIN < total:
        starts.append(starts[-1] + HOP)
    tokens: list[tuple[str, float, float]] = []
    detected = ""
    for k, w0 in enumerate(starts):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ nhận diện")
        chunk = audio[int(w0 * sr):int((w0 + WIN) * sr)]
        if len(chunk) < sr // 4:
            continue
        r = model.generate(input=chunk, fs=sr, language=lang, use_itn=True, output_timestamp=True)[0]
        if not detected and r.get("text", "").startswith("<|"):
            detected = r["text"][2:].split("|>", 1)[0]
        lo = w0 + ((WIN - HOP) / 2 if k > 0 else 0.0)
        hi = w0 + HOP + (WIN - HOP) / 2 if k + 1 < len(starts) else float("inf")
        for ch, (s_ms, e_ms) in zip(r.get("words") or [], r.get("timestamp") or []):
            s, e = w0 + s_ms / 1000, w0 + e_ms / 1000
            if lo <= s < hi:
                tokens.append((ch, s, e))
        if on_progress:
            on_progress((k + 1) / len(starts))
    try:
        wav.unlink()
    except OSError:
        pass
    return tokens, detected, total
