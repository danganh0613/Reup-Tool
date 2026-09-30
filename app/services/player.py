"""Phát video xem trước: giải mã khung hình bằng PyAV trong thread, âm thanh qua winsound theo từng đoạn.

Không áp filter khi phát (quá nặng); các lớp phủ (phụ đề, vùng che, logo) do canvas vẽ đè.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from app.config.paths import PREVIEW_CACHE_DIR
from app.services.ffmpeg import FFmpeg

logger = logging.getLogger(__name__)

AUDIO_CHUNK = 90.0     # giây audio trích mỗi lần


class Player:
    def __init__(self, ffmpeg: FFmpeg, on_frame: Callable[[Image.Image, float], None],
                 on_end: Callable[[], None] | None = None) -> None:
        self.ffmpeg = ffmpeg
        self.on_frame = on_frame
        self.on_end = on_end
        self.path: str | None = None
        self.muted = False
        self.volume = 1.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._t = 0.0
        self._max_width = 960

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def position(self) -> float:
        return self._t

    def set_source(self, path: str | None) -> None:
        self.stop()
        self.path = path

    # ── Điều khiển ───────────────────────────────────────────────────────
    def play(self, start: float = 0.0, max_width: int = 960) -> None:
        if not self.path:
            return
        self.stop()
        self._stop.clear()
        self._t = max(0.0, start)
        self._max_width = max_width
        self._thread = threading.Thread(target=self._run, name="Player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=1.5)
        self._thread = None
        self._audio_stop()

    # ── Âm thanh ─────────────────────────────────────────────────────────
    def _audio_chunk(self, start: float) -> Path | None:
        """Trích [start, start+AUDIO_CHUNK] thành WAV để winsound phát."""
        if self.muted or sys.platform != "win32" or not self.path:
            return None
        PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(f"{self.path}|{int(start * 10)}|{self.volume:.2f}".encode("utf-8")).hexdigest()[:12]
        wav = PREVIEW_CACHE_DIR / f"play_{key}.wav"
        if wav.exists():
            return wav
        try:
            args = ["-ss", f"{start:.3f}", "-t", f"{AUDIO_CHUNK:.1f}", "-i", self.path, "-vn",
                    "-af", f"volume={self.volume:.2f}", "-ac", "2", "-ar", "44100", "-acodec", "pcm_s16le", str(wav)]
            res = self.ffmpeg.run_simple(args, timeout=60)
            if res.returncode != 0 or not wav.exists():
                return None
            return wav
        except Exception:
            logger.debug("audio chunk lỗi", exc_info=True)
            return None

    def _audio_play(self, wav: Path | None) -> None:
        if wav is None or sys.platform != "win32":
            return
        import winsound

        try:
            winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

    def _audio_stop(self) -> None:
        if sys.platform != "win32":
            return
        import winsound

        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    # ── Vòng giải mã ─────────────────────────────────────────────────────
    def _run(self) -> None:
        try:
            import av
        except Exception as exc:
            logger.warning("Không có PyAV để phát: %s", exc)
            if self.on_end:
                self.on_end()
            return
        start = self._t
        try:
            container = av.open(self.path)
        except Exception as exc:
            logger.warning("Không mở được video: %s", exc)
            if self.on_end:
                self.on_end()
            return
        try:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            tb = float(stream.time_base) if stream.time_base else 1 / 90000
            try:
                container.seek(int(start / tb), stream=stream, backward=True, any_frame=False)
            except Exception:
                container.seek(int(start * 1_000_000))
            audio_next = start
            wav = self._audio_chunk(start)
            wall0: float | None = None
            last_emit = 0.0
            for frame in container.decode(stream):
                if self._stop.is_set():
                    break
                pts = frame.time if frame.time is not None else (frame.pts * tb if frame.pts is not None else 0.0)
                if pts < start - 0.01:
                    continue
                if wall0 is None:
                    wall0 = time.perf_counter() - (pts - start)
                    self._audio_play(wav)
                    audio_next = start + AUDIO_CHUNK - 0.2
                # đợi đúng nhịp; bỏ khung nếu trễ nhiều
                now = time.perf_counter()
                due = wall0 + (pts - start)
                lag = now - due
                if lag < 0:
                    time.sleep(min(0.05, -lag))
                elif lag > 0.15 and (pts - last_emit) < 0.5:
                    continue
                if pts >= audio_next:
                    self._audio_play(self._audio_chunk(pts))
                    audio_next = pts + AUDIO_CHUNK - 0.2
                    wall0 = time.perf_counter() - (pts - start)
                w = frame.width
                scale = min(1.0, self._max_width / w) if w else 1.0
                img = frame.to_image()
                if scale < 0.999:
                    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
                self._t = pts
                last_emit = pts
                self.on_frame(img, pts)
        except Exception:
            logger.debug("player lỗi", exc_info=True)
        finally:
            try:
                container.close()
            except Exception:
                pass
            self._audio_stop()
            if not self._stop.is_set() and self.on_end:
                self.on_end()


def waveform_png(ffmpeg: FFmpeg, path: str | Path, width: int, height: int = 56, color: str = "#06B6D4") -> Path | None:
    """Ảnh dải sóng âm (cache theo file + kích thước)."""
    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        mtime = int(Path(path).stat().st_mtime)
    except OSError:
        return None
    key = hashlib.sha1(f"{path}|{mtime}|{width}x{height}|{color}".encode("utf-8")).hexdigest()[:12]
    out = PREVIEW_CACHE_DIR / f"wave_{key}.png"
    if out.exists():
        return out
    args = ["-i", str(path), "-filter_complex",
            f"aformat=channel_layouts=mono,compand=gain=3,showwavespic=s={width}x{height}:colors={color}:scale=sqrt",
            "-frames:v", "1", str(out)]
    try:
        res = ffmpeg.run_simple(args, timeout=180)
        return out if res.returncode == 0 and out.exists() else None
    except Exception:
        return None
