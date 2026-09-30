"""Phát thử âm thanh (nghe thử giọng) bằng winsound — không cần thư viện ngoài.

winsound chỉ chơi WAV, nên mp3 được chuyển qua ffmpeg vào thư mục cache trước.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

from app.config.paths import PREVIEW_CACHE_DIR
from app.services.ffmpeg import FFmpeg

logger = logging.getLogger(__name__)


class AudioPlayer:
    def __init__(self, ffmpeg: FFmpeg) -> None:
        self.ffmpeg = ffmpeg
        self._playing = False

    def play(self, path: str | Path) -> None:
        if sys.platform != "win32":
            logger.warning("Nghe thử chỉ hỗ trợ Windows")
            return
        import winsound

        p = Path(path)
        if p.suffix.lower() != ".wav":
            PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            wav = PREVIEW_CACHE_DIR / f"play_{hashlib.sha1(str(p).encode()).hexdigest()[:10]}.wav"
            if not wav.exists():
                self.ffmpeg.to_wav(p, wav)
            p = wav
        self.stop()
        winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)
        self._playing = True

    def stop(self) -> None:
        if sys.platform != "win32":
            return
        import winsound

        if self._playing:
            winsound.PlaySound(None, winsound.SND_PURGE)
            self._playing = False
