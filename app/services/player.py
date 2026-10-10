"""Phát video xem trước trong thread: khung hình qua đúng chuỗi filter của bản render (FFmpeg) hoặc thô (PyAV),
âm thanh (WAV, bản trộn giống bản render) theo từng đoạn — winsound / afplay.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from app.config.paths import PREVIEW_CACHE_DIR
from app.core.platform import NO_WINDOW, WavPlayer
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

    ``spec`` (``LiveSpec``) có mặt → phát giống bản render: khung qua đúng chuỗi filter (``spec.effects``),
    tiếng là bản trộn âm gốc + giọng lồng + nhạc nền, chỉ phát các đoạn giữ lại, nhịp theo tốc độ video.
    Không có ``spec`` → phát thô file ``path`` (vd. bản render thử).
    """

    def __init__(self, ffmpeg: FFmpeg, on_frame: Callable[[Image.Image, float], None] | None = None,
                 on_end: Callable[[], None] | None = None) -> None:
        self.ffmpeg = ffmpeg
        self.on_frame = on_frame
        self.on_end = on_end
        self.path: str | None = None
        self.spec = None
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
        self._proc: subprocess.Popen | None = None
        self._speed = 1.0
        self._sound = WavPlayer()
        self.effects_failed = ""          # lý do phát có hiệu ứng lỗi (đã tự chuyển sang phát thô)

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
        self._thread = threading.Thread(target=self._run, args=(stop, self.path, self._t, self.spec), name="Player",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill_proc()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=0.2)    # không chặn UI lâu: thread cũ tự thoát khi thấy cờ dừng của nó
        self._thread = None
        with self._lock:
            self._latest = None
        self._audio_stop()

    def _kill_proc(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    # ── Âm thanh ─────────────────────────────────────────────────────────
    def _audio_chunk(self, path: str, start: float, dur: float = AUDIO_CHUNK, spec=None) -> Path | None:
        """Âm thanh để phát từ mốc nguồn ``start``, dài ``dur`` giây phát (đã tính tốc độ) → WAV."""
        if self.muted or not path or not WavPlayer.available() or dur <= 0.05:
            return None
        PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sig = spec.signature() if spec is not None else ""
        key = hashlib.sha1(f"{path}|{start:.2f}|{dur:.2f}|{self.volume:.2f}|{sig}".encode("utf-8")).hexdigest()[:12]
        wav = PREVIEW_CACHE_DIR / f"chunk_{key}.wav"
        if wav.exists():
            return wav
        try:
            if spec is not None:
                args = spec.audio_cmd(start, dur, wav, self.volume)
                if args is None:          # tắt tiếng / không có nguồn âm thanh nào
                    return None
                cwd = spec.work
            else:
                args = ["-ss", f"{start:.3f}", "-t", f"{dur:.1f}", "-i", path, "-vn",
                        "-af", f"volume={self.volume:.2f}", "-ac", "2", "-ar", "44100", "-acodec", "pcm_s16le", str(wav)]
                cwd = None
            res = self.ffmpeg.run_simple(args, timeout=90, cwd=cwd)
            if res.returncode != 0 or not wav.exists():
                logger.info("Âm thanh xem trước lỗi: %s", res.stderr.decode("utf-8", "replace")[-300:])
                return None
            return wav
        except Exception:
            logger.debug("audio chunk lỗi", exc_info=True)
            return None

    def _prefetch(self, path: str, start: float, dur: float, spec=None) -> dict:
        box: dict = {"wav": None, "done": threading.Event()}

        def run() -> None:
            try:
                box["wav"] = self._audio_chunk(path, start, dur, spec)
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
            self._audio_stop()
            return
        try:
            self._sound.play(wav)
        except Exception:
            logger.debug("phát audio lỗi", exc_info=True)

    def _audio_stop(self) -> None:
        self._sound.stop()

    # ── Nguồn khung hình ─────────────────────────────────────────────────
    def _target_size(self, w: int, h: int) -> tuple[int, int]:
        box = self.fit_box
        if box is not None and box[0] > 0 and box[1] > 0:
            s = min(box[0] / w, box[1] / h)
        else:
            s = min(1.0, self._max_width / w)
        return max(2, int(w * s)), max(2, int(h * s))

    def _frames_raw(self, stop: threading.Event, path: str, a: float, b: float):
        """Khung gốc (PyAV) trong [a, b): (mốc nguồn, hàm tạo ảnh) — chỉ chuyển ảnh khi thật sự hiển thị."""
        import av

        container = av.open(path)
        try:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            tb = float(stream.time_base) if stream.time_base else 1 / 90000
            try:
                container.seek(int(a / tb), stream=stream, backward=True, any_frame=False)
            except Exception:
                container.seek(int(a * 1_000_000))
            for frame in container.decode(stream):
                if stop.is_set():
                    return
                pts = frame.time if frame.time is not None else (frame.pts * tb if frame.pts is not None else 0.0)
                if pts < a - 0.01:
                    continue
                if pts >= b:
                    return

                def make(f=frame):
                    w, h = f.width, f.height
                    tw, th = self._target_size(w, h) if w and h else (w, h)
                    if (tw, th) != (w, h):
                        return f.reformat(width=tw, height=th, format="rgb24").to_image()
                    return f.to_image()
                yield pts, make
        finally:
            try:
                container.close()
            except Exception:
                pass

    def _frames_fx(self, stop: threading.Event, spec, path: str, a: float, b: float):
        """Khung đã qua đúng chuỗi filter của bản render (FFmpeg → rawvideo qua pipe)."""
        try:
            vc = spec.video_cmd(a, b, self.fit_box, self._max_width)
        except Exception as exc:
            logger.warning("Không dựng được filter xem trước: %s", exc, exc_info=True)
            self.effects_failed = str(exc)
            yield from self._frames_raw(stop, path, a, b)
            return
        err_path = vc.cwd / "live_err.txt"
        with open(err_path, "wb") as err:
            proc = subprocess.Popen(vc.cmd, stdout=subprocess.PIPE, stderr=err, cwd=str(vc.cwd), creationflags=NO_WINDOW)
        self._proc = proc
        size = vc.width * vc.height * 3
        n = 0
        try:
            while not stop.is_set():
                buf = proc.stdout.read(size)
                if not buf or len(buf) < size:
                    break
                pts = a + n * self._speed / vc.fps
                n += 1
                yield pts, (lambda d=buf: Image.frombuffer("RGB", (vc.width, vc.height), d, "raw", "RGB", 0, 1))
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        if n == 0 and not stop.is_set():
            try:
                msg = err_path.read_text(encoding="utf-8", errors="replace").strip()[-400:]
            except OSError:
                msg = ""
            logger.warning("Phát có hiệu ứng lỗi → phát thô: %s", msg)
            self.effects_failed = msg or "FFmpeg không trả khung nào"
            yield from self._frames_raw(stop, path, a, b)

    # ── Vòng phát ────────────────────────────────────────────────────────
    def _run(self, stop: threading.Event, path: str, start: float, spec) -> None:
        try:
            import av  # noqa: F401
        except Exception as exc:
            logger.warning("Không có PyAV để phát: %s", exc)
            if self.on_end and not stop.is_set():
                self.on_end()
            return
        self._speed = spec.speed if spec is not None else 1.0
        if spec is not None and spec.segments:
            ranges = [(max(a, start), b) for a, b in spec.segments if b > start + 0.05]
        else:
            end = spec.info.duration if spec is not None and spec.info.duration > 0 else float("inf")
            ranges = [(start, end)]
        self._prune_cache()
        try:
            for a, b in ranges:
                if stop.is_set():
                    break
                self._play_range(stop, path, a, b, spec)
        except Exception:
            logger.debug("player lỗi", exc_info=True)
        finally:
            self._kill_proc()
            if not stop.is_set():
                self._audio_stop()
                if self.on_end:
                    self.on_end()

    def _play_range(self, stop: threading.Event, path: str, a: float, b: float, spec) -> None:
        speed = self._speed
        out_len = (b - a) / speed
        frames = self._frames_fx(stop, spec, path, a, b) if (spec is not None and spec.effects) \
            else self._frames_raw(stop, path, a, b)
        # thời gian «phát» (đã tính tốc độ) kể từ đầu đoạn
        wav = self._audio_chunk(path, a, min(AUDIO_FIRST_CHUNK, out_len), spec)
        audio_next = AUDIO_FIRST_CHUNK - 0.2
        nxt: dict | None = None
        wall0: float | None = None
        last_emit = -1.0
        for pts, make in frames:
            if stop.is_set():
                break
            ot = (pts - a) / speed
            if wall0 is None:
                wall0 = time.perf_counter() - ot
                self._audio_play(wav)
            if nxt is None and audio_next < out_len and ot >= audio_next - AUDIO_PREFETCH and not self.muted:
                nxt = self._prefetch(path, a + audio_next * speed, min(AUDIO_CHUNK, out_len - audio_next), spec)
            if ot >= audio_next and audio_next < out_len:
                wav_next = None
                if nxt is not None:
                    nxt["done"].wait(timeout=30)
                    wav_next = nxt["wav"]
                if stop.is_set():
                    break
                self._audio_play(wav_next)
                # đồng bộ lại đồng hồ theo điểm bắt đầu của đoạn audio mới
                wall0 = time.perf_counter() - audio_next
                audio_next += AUDIO_CHUNK - 0.2
                nxt = None
            # đợi đúng nhịp; bỏ khung nếu trễ nhiều
            now = time.perf_counter()
            lag = now - (wall0 + ot)
            if lag < 0:
                time.sleep(min(0.05, -lag))
            elif lag > 0.15 and (ot - last_emit) < 0.5:
                continue
            if stop.is_set():
                break
            img = make()
            self._t = pts
            last_emit = ot
            with self._lock:
                if stop.is_set():          # đã dừng/tua trong lúc giải mã → không để khung cũ lọt sang lần phát mới
                    break
                self._latest = (img, pts)
            if self.on_frame is not None:
                self.on_frame(img, pts)


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
