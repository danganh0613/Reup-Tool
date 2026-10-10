"""FunASR SenseVoice-Small (local, chạy được CPU) chạy ngầm cho OCR phụ đề in sẵn: mốc TỪNG CHỮ của tiếng nói
(output_timestamp) để căn thời gian câu chữ đọc được trên hình.

VAD fsmn-vad không dùng để cắt đoạn: video có nhạc nền liền thì VAD trả một đoạn 60 s (đo trên video mẫu). Thay vào đó
cắt cửa sổ 60 s gối nhau 5 s, lấy chữ của mỗi cửa sổ tới giữa phần gối. Model tải về models/modelscope (~0,9 GB, một lần).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from app.config.paths import MODELS_DIR
from app.services.ffmpeg import FFmpeg, RenderCancelled

logger = logging.getLogger(__name__)

MODEL = "iic/SenseVoiceSmall"
SR = 16000
WIN, HOP = 60.0, 55.0                 # cửa sổ 60 s, bước 55 s (gối 5 s, cắt ở giữa phần gối)
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
