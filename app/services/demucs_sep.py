"""Tách lời thoại gốc bằng Demucs (tuỳ chọn, cần torch). Trả về file "no_vocals" để thay tiếng gốc."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from app.config.paths import CACHE_DIR, MODELS_DIR
from app.services.ffmpeg import FFmpeg

logger = logging.getLogger(__name__)


def is_installed() -> bool:
    try:
        import demucs  # noqa: F401
        import torch  # noqa: F401

        return True
    except Exception:
        return False


def install_demucs(log: Callable[[str], None] | None = None) -> bool:
    if getattr(sys, "frozen", False):
        if log:
            log("Bản đóng gói (.exe / .app) không cài thêm được gói Python — chạy bản mã nguồn (run.bat / run.command) để cài, hoặc dùng tính năng thay thế.")
        return False
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "demucs", "torch", "torchaudio"]
    if log:
        log("pip install demucs torch torchaudio … (rất nặng, vài phút)")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if proc.returncode != 0:
        if log:
            log("pip lỗi: " + proc.stderr[-800:])
        return False
    return is_installed()


def separate_no_vocals(video_path: str | Path, ffmpeg: FFmpeg, *, log: Callable[[str], None] | None = None,
                       cancel: threading.Event | None = None) -> Path | None:
    """Tách và trả về đường dẫn WAV không lời (cache theo file). None nếu không cài / lỗi."""
    if not is_installed():
        if log:
            log("⚠ Demucs chưa cài — dùng bộ lọc khử vocal thường.")
        return None
    src = Path(video_path)
    key = hashlib.sha1(f"{src}|{int(src.stat().st_mtime)}".encode("utf-8")).hexdigest()[:12]
    out_dir = CACHE_DIR / "demucs" / key
    result = out_dir / "no_vocals.wav"
    if result.exists():
        return result
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / "input.wav"
    ffmpeg.extract_audio_wav(src, wav, sample_rate=44100)
    if log:
        log("Demucs đang tách lời thoại (có thể mất vài phút)…")
    env = dict(os.environ, TORCH_HOME=str(MODELS_DIR / "torch"))
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(out_dir), str(wav)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            return None
        if log and ("%" in line or "Separat" in line):
            log("Demucs: " + line.strip()[-100:])
    proc.wait()
    cand = next(out_dir.rglob("no_vocals.wav"), None)
    if proc.returncode != 0 or cand is None:
        if log:
            log("⚠ Demucs thất bại — dùng bộ lọc khử vocal thường.")
        return None
    if cand != result:
        cand.replace(result)
    try:
        wav.unlink()
    except OSError:
        pass
    return result
