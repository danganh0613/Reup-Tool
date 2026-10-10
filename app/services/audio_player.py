"""Phát thử âm thanh (nghe thử giọng, nhạc nền) — winsound (Windows) / afplay (macOS), không cần thư viện ngoài.

Chỉ phát WAV cho đồng nhất, nên mp3… được chuyển qua ffmpeg vào thư mục cache trước (trong thread nền).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

from app.config.paths import PREVIEW_CACHE_DIR
from app.core.platform import WavPlayer
from app.services.ffmpeg import FFmpeg

logger = logging.getLogger(__name__)


class AudioPlayer:
    def __init__(self, ffmpeg: FFmpeg) -> None:
        self.ffmpeg = ffmpeg
        self._sound = WavPlayer()
        self._token = 0            # tăng mỗi lần play/stop: bản chuyển đổi chạy nền của lần bấm cũ sẽ không phát

    def play(self, path: str | Path) -> None:
        """Phát ngay nếu là WAV / đã có trong cache; mp3… được chuyển sang WAV trong thread nền (không đơ giao diện)."""
        if not WavPlayer.available():
            logger.warning("Máy này không có trình phát âm thanh để nghe thử")
            return
        self.stop()
        self._token += 1
        token = self._token
        p = Path(path)
        if p.suffix.lower() == ".wav":
            self._sound.play(p)
            return
        PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            mtime = int(p.stat().st_mtime)
        except OSError:
            mtime = 0
        wav = PREVIEW_CACHE_DIR / f"play_{hashlib.sha1(f'{p}|{mtime}'.encode()).hexdigest()[:10]}.wav"
        if wav.exists():
            self._sound.play(wav)
            return

        def convert() -> None:
            tmp = wav.with_name(f"{wav.stem}.{token}.part.wav")     # ghi tạm rồi đổi tên → không để lại WAV dở
            try:
                self.ffmpeg.to_wav(p, tmp)
                tmp.replace(wav)
            except Exception:
                tmp.unlink(missing_ok=True)
                logger.warning("Không chuyển được %s sang WAV để nghe thử", p.name, exc_info=True)
                return
            if token == self._token and wav.exists():
                self._sound.play(wav)

        threading.Thread(target=convert, name="AudioPreview", daemon=True).start()

    def stop(self) -> None:
        self._token += 1
        self._sound.stop()
