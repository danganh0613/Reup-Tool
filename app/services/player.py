"""Phát video xem trước: giải mã khung hình bằng PyAV trong thread, âm thanh (WAV) theo từng đoạn — winsound / afplay.

Không áp filter khi phát (quá nặng); các lớp phủ (phụ đề, vùng che, logo) do canvas vẽ đè.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from app.config.paths import PREVIEW_CACHE_DIR
from app.core.platform import WavPlayer
from app.services.ffmpeg import FFmpeg

logger = logging.getLogger(__name__)

AUDIO_CHUNK = 90.0       # giây audio trích mỗi lần
AUDIO_FIRST_CHUNK = 12.0  # đoạn đầu ngắn để bấm Phát là có tiếng ngay; đoạn sau trích trước trong nền
AUDIO_PREFETCH = 8.0      # trích đoạn kế tiếp trước khi đoạn hiện tại hết bấy nhiêu giây
AUDIO_KEEP = 24           # số file chunk_*.wav giữ lại trong cache


class Player:
    """Giải mã trong thread nền; khung mới nhất nằm ở một «ô» duy nhất để UI lấy bằng ``take_frame()``.

    Thread giải mã không gọi Tk (gọi Tk từ thread khác có thể chặn cả hai luồng); UI tự hỏi khung
    theo nhịp ``after`` khi đang phát, khung cũ chưa kịp vẽ thì bị thay — không dồn hàng đợi.
    """

    def __init__(self, ffmpeg: FFmpeg, on_frame: Callable[[Image.Image, float], None] | None = None,
                 on_end: Callable[[], None] | None = None) -> None:
        self.ffmpeg = ffmpeg
        self.on_frame = on_frame
        self.on_end = on_end
        self.path: str | None = None
        self.muted = False
        self.volume = 1.0
        # (rộng, cao) vùng hiển thị: khung được thu thẳng về cỡ này trong thread (swscale) → UI khỏi resize
        self.fit_box: tuple[int, int] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._t = 0.0
        self._max_width = 960
        self._lock = threading.Lock()
        self._latest: tuple[Image.Image, float] | None = None
        self._sound = WavPlayer()

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def position(self) -> float:
        return self._t

    def set_source(self, path: str | None) -> None:
        self.stop()
        self.path = path

    def take_frame(self) -> tuple[Image.Image, float] | None:
        """Khung mới nhất chưa lấy (hoặc None)."""
        with self._lock:
            f, self._latest = self._latest, None
        return f

    # ── Điều khiển ───────────────────────────────────────────────────────
    def play(self, start: float = 0.0, max_width: int = 960) -> None:
        if not self.path:
            return
        self.stop()
        self._stop = stop = threading.Event()     # mỗi lần phát một cờ riêng: thread cũ chưa thoát không bị «hồi sinh»
        self._t = max(0.0, start)
        self._max_width = max_width
        with self._lock:
            self._latest = None
        self._thread = threading.Thread(target=self._run, args=(stop, self.path, self._t), name="Player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=0.2)    # không chặn UI lâu: thread cũ tự thoát khi thấy cờ dừng của nó
        self._thread = None
        with self._lock:
            self._latest = None
        self._audio_stop()

    # ── Âm thanh ─────────────────────────────────────────────────────────
    def _audio_chunk(self, path: str, start: float, dur: float = AUDIO_CHUNK) -> Path | None:
        """Trích [start, start+dur] thành WAV để phát."""
        if self.muted or not path or not WavPlayer.available():
            return None
        PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(f"{path}|{start:.2f}|{dur:.1f}|{self.volume:.2f}".encode("utf-8")).hexdigest()[:12]
        wav = PREVIEW_CACHE_DIR / f"chunk_{key}.wav"
        if wav.exists():
            return wav
        try:
            args = ["-ss", f"{start:.3f}", "-t", f"{dur:.1f}", "-i", path, "-vn",
                    "-af", f"volume={self.volume:.2f}", "-ac", "2", "-ar", "44100", "-acodec", "pcm_s16le", str(wav)]
            res = self.ffmpeg.run_simple(args, timeout=60)
            if res.returncode != 0 or not wav.exists():
                return None
            return wav
        except Exception:
            logger.debug("audio chunk lỗi", exc_info=True)
            return None

    def _prefetch(self, path: str, start: float, dur: float) -> dict:
        box: dict = {"wav": None, "done": threading.Event()}

        def run() -> None:
            try:
                box["wav"] = self._audio_chunk(path, start, dur)
            finally:
                box["done"].set()

        threading.Thread(target=run, name="PlayerAudio", daemon=True).start()
        return box

    @staticmethod
    def _prune_cache() -> None:
        """Xoá bớt chunk_*.wav cũ (mỗi lần tua sinh một file ~2–16 MB)."""
        try:
            files = sorted(PREVIEW_CACHE_DIR.glob("chunk_*.wav"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files[AUDIO_KEEP:]:
                f.unlink(missing_ok=True)
        except OSError:
            pass

    def _audio_play(self, wav: Path | None) -> None:
        if wav is None:
            return
        try:
            self._sound.play(wav)
        except Exception:
            logger.debug("phát audio lỗi", exc_info=True)

    def _audio_stop(self) -> None:
        self._sound.stop()

    # ── Vòng giải mã ─────────────────────────────────────────────────────
    def _target_size(self, w: int, h: int) -> tuple[int, int]:
        box = self.fit_box
        if box is not None and box[0] > 0 and box[1] > 0:
            s = min(box[0] / w, box[1] / h)
        else:
            s = min(1.0, self._max_width / w)
        return max(2, int(w * s)), max(2, int(h * s))

    def _run(self, stop: threading.Event, path: str, start: float) -> None:
        try:
            import av
        except Exception as exc:
            logger.warning("Không có PyAV để phát: %s", exc)
            if self.on_end and not stop.is_set():
                self.on_end()
            return
        try:
            container = av.open(path)
        except Exception as exc:
            logger.warning("Không mở được video: %s", exc)
            if self.on_end and not stop.is_set():
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
            self._prune_cache()
            wav = self._audio_chunk(path, start, AUDIO_FIRST_CHUNK)
            audio_next = start + AUDIO_FIRST_CHUNK - 0.2
            nxt: dict | None = None
            wall0: float | None = None
            last_emit = 0.0
            for frame in container.decode(stream):
                if stop.is_set():
                    break
                pts = frame.time if frame.time is not None else (frame.pts * tb if frame.pts is not None else 0.0)
                if pts < start - 0.01:
                    continue
                if wall0 is None:
                    wall0 = time.perf_counter() - (pts - start)
                    self._audio_play(wav)
                if nxt is None and pts >= audio_next - AUDIO_PREFETCH and not self.muted:
                    nxt = self._prefetch(path, audio_next, AUDIO_CHUNK)
                if pts >= audio_next:
                    wav_next = None
                    if nxt is not None:
                        nxt["done"].wait(timeout=30)
                        wav_next = nxt["wav"]
                    if stop.is_set():
                        break
                    self._audio_play(wav_next)
                    # đồng bộ lại đồng hồ theo điểm bắt đầu của đoạn audio mới
                    wall0 = time.perf_counter() - (audio_next - start)
                    audio_next += AUDIO_CHUNK - 0.2
                    nxt = None
                # đợi đúng nhịp; bỏ khung nếu trễ nhiều
                now = time.perf_counter()
                due = wall0 + (pts - start)
                lag = now - due
                if lag < 0:
                    time.sleep(min(0.05, -lag))
                elif lag > 0.15 and (pts - last_emit) < 0.5:
                    continue
                if stop.is_set():
                    break
                w, h = frame.width, frame.height
                tw, th = self._target_size(w, h) if w and h else (w, h)
                if (tw, th) != (w, h):
                    img = frame.reformat(width=tw, height=th, format="rgb24").to_image()
                else:
                    img = frame.to_image()
                self._t = pts
                last_emit = pts
                with self._lock:
                    if stop.is_set():          # đã dừng/tua trong lúc giải mã → không để khung cũ lọt sang lần phát mới
                        break
                    self._latest = (img, pts)
                if self.on_frame is not None:
                    self.on_frame(img, pts)
        except Exception:
            logger.debug("player lỗi", exc_info=True)
        finally:
            try:
                container.close()
            except Exception:
                pass
            if not stop.is_set():
                self._audio_stop()
                if self.on_end:
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
