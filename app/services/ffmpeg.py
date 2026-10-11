"""Bọc FFmpeg/FFprobe: tìm binary, chạy có tiến độ, probe, trích khung hình, giải mã PCM."""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from PIL import Image

from app.config.paths import BIN_DIR
from app.core.platform import IS_MAC, NO_WINDOW, exe_name

logger = logging.getLogger(__name__)

_NO_WINDOW = NO_WINDOW


class FFmpegNotFound(RuntimeError):
    pass


class RenderCancelled(RuntimeError):
    pass


@dataclass
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    audio_channels: int = 0
    video_codec: str = ""
    audio_codec: str = ""

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width


def find_binary(name: str, preferred: str = "") -> str | None:
    """Thứ tự ưu tiên: đường dẫn trong cài đặt → bin/ cạnh app → PATH."""
    exe = exe_name(name)
    candidates: list[Path] = []
    if preferred:
        p = Path(preferred)
        candidates.append(p if p.is_file() else p / exe)       # file cụ thể, hoặc thư mục chứa nó
    candidates.append(BIN_DIR / exe)
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which(name)
    if found is None and IS_MAC:
        # app mở từ Finder không có PATH của Terminal → tự tìm chỗ Homebrew hay cài
        for d in ("/opt/homebrew/bin", "/usr/local/bin"):
            if Path(d, name).is_file():
                return str(Path(d, name))
    return found


_REASONS = [
    (re.compile(r"no space left|disk full|errno 28", re.I), "ổ đĩa đầy — giải phóng dung lượng rồi Thử lại"),
    (re.compile(r"permission denied|access is denied", re.I),
     "không ghi được file (đang mở ở trình phát khác, hoặc thư mục bị khoá) — đóng trình phát rồi Thử lại"),
    (re.compile(r"openencodesessionex|no capable devices|cannot load nvcuda|nvenc|amf|qsv|mfx|"
                r"device creation failed|hwupload", re.I),
     "bộ mã hoá GPU lỗi — tắt «Tăng tốc GPU» (tab Video › Nâng cao xuất file) rồi Thử lại"),
    (re.compile(r"invalid data found|error while decoding|moov atom", re.I), "video nguồn bị hỏng một đoạn"),
]


# lỗi mà đổi sang mã hoá CPU cũng không khỏi → không xuất lại bằng CPU
_NOT_ENCODER = re.compile(r"no space left|disk full|errno 28|permission denied|access is denied|no such file|"
                          r"invalid data found|error while decoding|moov atom", re.I)


def cpu_may_help(error_text: str) -> bool:
    """Lỗi xuất bằng GPU có thể khỏi khi mã hoá bằng CPU không (sai tuỳ chọn, driver, hết phiên NVENC…)."""
    return not _NOT_ENCODER.search(error_text)


def ffmpeg_reason(lines: list[str]) -> str:
    """Lý do dễ hiểu từ stderr của ffmpeg: lỗi hay gặp → câu tiếng Việt; còn lại → dòng lỗi cuối có nghĩa."""
    text = "\n".join(lines[-40:])
    for pat, why in _REASONS:
        if pat.search(text):
            return why
    m = re.search(r"No such file or directory", text)
    if m:
        miss = re.search(r"([^\s'\"]+\.\w{2,4})['\"]?:\s*No such file", text)
        return "không tìm thấy file" + (f" {Path(miss.group(1)).name}" if miss else "")
    useful = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith(("Last message repeated",
                                                                                       "frame=", "size="))]
    return useful[-1][:200] if useful else "không rõ lý do"


class FFmpeg:
    def __init__(self, ffmpeg_path: str = "", ffprobe_path: str = "") -> None:
        self.ffmpeg = find_binary("ffmpeg", ffmpeg_path)
        self.ffprobe = find_binary("ffprobe", ffprobe_path)
        self._encoder_cache: dict[str, bool] = {}
        self._options_cache: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    def require(self) -> None:
        if not self.available:
            raise FFmpegNotFound(f"Không tìm thấy ffmpeg/ffprobe. Đặt {exe_name('ffmpeg')} + {exe_name('ffprobe')} vào thư mục bin/ "
                                 "hoặc chọn đường dẫn trong Cài đặt.")

    def version(self) -> str:
        if not self.ffmpeg:
            return ""
        try:
            out = subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True,
                                 creationflags=_NO_WINDOW, timeout=10).stdout
            return out.splitlines()[0] if out else ""
        except Exception:
            return ""

    # ── Probe ─────────────────────────────────────────────────────────────
    def probe(self, path: str | Path) -> MediaInfo:
        self.require()
        cmd = [self.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             creationflags=_NO_WINDOW, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"ffprobe lỗi: {res.stderr.strip()[:300]}")
        data = json.loads(res.stdout or "{}")
        info = MediaInfo()
        fmt = data.get("format", {})
        try:
            info.duration = float(fmt.get("duration", 0) or 0)
        except (TypeError, ValueError):
            info.duration = 0.0
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and info.width == 0:
                info.width = int(s.get("width", 0) or 0)
                info.height = int(s.get("height", 0) or 0)
                info.video_codec = s.get("codec_name", "")
                # Xoay theo metadata (video điện thoại)
                rot = 0
                for sd in s.get("side_data_list", []) or []:
                    if "rotation" in sd:
                        try:
                            rot = int(float(sd["rotation"]))
                        except (TypeError, ValueError):
                            pass
                tags = s.get("tags", {}) or {}
                if "rotate" in tags:
                    try:
                        rot = int(float(tags["rotate"]))
                    except (TypeError, ValueError):
                        pass
                if rot % 180 != 0:
                    info.width, info.height = info.height, info.width
                info.fps = _parse_rate(s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1")
                if info.duration == 0:
                    try:
                        info.duration = float(s.get("duration", 0) or 0)
                    except (TypeError, ValueError):
                        pass
            elif s.get("codec_type") == "audio" and not info.has_audio:
                info.has_audio = True
                info.audio_channels = int(s.get("channels", 0) or 0)
                info.audio_codec = s.get("codec_name", "")
        return info

    def duration_of(self, path: str | Path) -> float:
        try:
            return self.probe(path).duration
        except Exception:
            return 0.0

    # ── Chạy có tiến độ ───────────────────────────────────────────────────
    def run(self, args: list[str], *, duration: float = 0.0,
            on_progress: Callable[[float], None] | None = None,
            cancel: threading.Event | None = None,
            cwd: str | Path | None = None,
            log_tag: str = "ffmpeg") -> None:
        """Chạy ffmpeg với ``args`` (không gồm tên binary). Ném lỗi nếu thất bại."""
        self.require()
        cmd = [self.ffmpeg, "-hide_banner", "-y", "-nostdin", "-loglevel", "error",
               "-progress", "pipe:1", "-nostats", *args]
        logger.debug("%s: %s", log_tag, " ".join(_quote(a) for a in cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=_NO_WINDOW, cwd=str(cwd) if cwd else None,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
        stderr_lines: list[str] = []

        def _drain_err() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    stderr_lines.append(line)
                    if len(stderr_lines) > 200:
                        del stderr_lines[0]

        t = threading.Thread(target=_drain_err, daemon=True)
        t.start()
        cancelled = False
        stop_watch = threading.Event()

        def _watch_cancel() -> None:
            # Bấm Dừng phải có tác dụng cả khi ffmpeg đang kẹt không in tiến độ (đọc ổ mạng, encoder chậm…)
            while not stop_watch.is_set() and proc.poll() is None:
                if cancel is not None and cancel.wait(0.25):
                    proc.kill()
                    return
                if cancel is None:
                    stop_watch.wait(0.5)

        if cancel is not None:
            threading.Thread(target=_watch_cancel, daemon=True).start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    proc.kill()
                    break
                if on_progress is None or duration <= 0:
                    continue
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        us = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    on_progress(max(0.0, min(1.0, (us / 1_000_000) / duration)))
                elif line.startswith("progress=end"):
                    on_progress(1.0)
        finally:
            proc.wait()
            stop_watch.set()
            t.join(timeout=2)
        if cancelled or (cancel is not None and cancel.is_set()):
            raise RenderCancelled("Đã huỷ")
        if proc.returncode != 0:
            tail = "\n".join(stderr_lines[-12:])
            raise RuntimeError(f"ffmpeg lỗi: {ffmpeg_reason(stderr_lines)} (mã {proc.returncode})\n{tail}")

    def run_simple(self, args: list[str], timeout: float = 600, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
        self.require()
        cmd = [self.ffmpeg, "-hide_banner", "-y", "-nostdin", "-loglevel", "error", *args]
        return subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW, timeout=timeout,
                              cwd=str(cwd) if cwd else None)

    # ── Khung hình xem trước ──────────────────────────────────────────────
    def extract_frame(self, path: str | Path, t: float, *, vf: str = "", max_width: int = 1280,
                      cwd: str | Path | None = None) -> Image.Image:
        """Lấy một khung hình PNG tại giây ``t`` (đã áp filter ``vf`` nếu có)."""
        self.require()
        filters = [vf] if vf else []
        filters.append(f"scale='min({max_width},iw)':-2")
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
               "-vf", ",".join(filters), "-f", "image2pipe", "-vcodec", "png", "-"]
        res = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW,
                             cwd=str(cwd) if cwd else None, timeout=60)
        if res.returncode != 0 or not res.stdout:
            err = res.stderr.decode("utf-8", "replace").strip()[-400:]
            raise RuntimeError(f"Không trích được khung hình: {err}")
        return Image.open(io.BytesIO(res.stdout)).convert("RGB")

    def extract_frame_graph(self, path: str | Path, t: float, graph: str, *, max_width: int = 960,
                            cwd: str | Path | None = None, inputs: list[list[str]] | None = None,
                            on_proc: Callable[[subprocess.Popen], None] | None = None) -> Image.Image:
        """Như ``extract_frame`` nhưng dùng filter_complex ``graph`` (đầu vào [0:v], đầu ra [vout]).

        ``inputs``: các input phụ (logo, mask…) đúng thứ tự chỉ số mà graph tham chiếu.
        ``on_proc``: nhận tiến trình ffmpeg để nơi gọi có thể ``kill()`` khi yêu cầu đã cũ.
        Xuất BMP qua pipe (không nén) — nhanh hơn PNG cả phía ffmpeg lẫn PIL.
        """
        self.require()
        full = f"{graph};[vout]scale='min({max_width},iw)':-2,format=rgb24[pv]"
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", f"{max(0.0, t):.3f}", "-i", str(path)]
        for extra in inputs or []:
            if "-stream_loop" in extra:      # lặp vô hạn chỉ cần khi render
                i = extra.index("-stream_loop")
                extra = extra[:i] + extra[i + 2:]
            cmd += extra
        cmd += ["-filter_complex", full, "-map", "[pv]", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "bmp", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW,
                                cwd=str(cwd) if cwd else None)
        if on_proc is not None:
            on_proc(proc)
        try:
            out, err_b = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError("Không trích được khung hình: quá thời gian (60 giây)")
        if proc.returncode != 0 or not out:
            err = err_b.decode("utf-8", "replace").strip()[-400:]
            raise RuntimeError(f"Không trích được khung hình: {err}")
        return Image.open(io.BytesIO(out)).convert("RGB")

    # ── Âm thanh ──────────────────────────────────────────────────────────
    def decode_pcm(self, path: str | Path, *, sample_rate: int = 24000, tempo: float = 1.0) -> bytes:
        """Giải mã file audio bất kỳ → PCM s16le mono, có thể đổi tempo (giữ cao độ)."""
        self.require()
        af = []
        if abs(tempo - 1.0) > 0.005:
            af.append(atempo_chain(tempo))
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path), "-map", "0:a:0"]
        if af:
            cmd += ["-af", ",".join(af)]
        cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sample_rate), "-"]
        res = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW, timeout=300)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode("utf-8", "replace")[-300:])
        return res.stdout

    def extract_audio_wav(self, src: str | Path, dst: str | Path, *, sample_rate: int = 16000, channels: int = 1) -> Path:
        """Tách audio ra WAV (mặc định mono — dùng cho Whisper)."""
        res = self.run_simple(["-i", str(src), "-map", "0:a:0", "-vn", "-ac", str(channels), "-ar", str(sample_rate),
                               "-acodec", "pcm_s16le", str(dst)])
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode("utf-8", "replace")[-300:])
        return Path(dst)

    def to_wav(self, src: str | Path, dst: str | Path) -> Path:
        res = self.run_simple(["-i", str(src), "-acodec", "pcm_s16le", "-ar", "44100", str(dst)])
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode("utf-8", "replace")[-300:])
        return Path(dst)

    # ── Encoder ───────────────────────────────────────────────────────────
    def encoder_works(self, encoder: str) -> bool:
        """Thử encode 0.2 giây màu đen để biết encoder phần cứng có chạy được không."""
        with self._lock:
            if encoder in self._encoder_cache:
                return self._encoder_cache[encoder]
        ok = False
        try:
            res = self.run_simple(["-f", "lavfi", "-i", "color=c=black:s=256x256:r=30:d=0.2",
                                   "-c:v", encoder, "-f", "null", "-"], timeout=30)
            ok = res.returncode == 0
        except Exception:
            ok = False
        with self._lock:
            self._encoder_cache[encoder] = ok
        logger.info("Encoder %s: %s", encoder, "OK" if ok else "không dùng được")
        return ok

    def mark_encoder_broken(self, encoder: str) -> None:
        """Xuất thật bằng encoder này lỗi → cả phiên không chọn nó nữa (mở lại app thì thử lại)."""
        with self._lock:
            self._encoder_cache[encoder] = False
        logger.warning("Encoder %s lỗi khi xuất — phiên này dùng bộ mã hoá khác", encoder)

    def encoder_options(self, encoder: str) -> set[str]:
        """Tên các tuỳ chọn riêng của encoder (``-h encoder=…``) — mỗi bản ffmpeg viết một kiểu
        (ffmpeg 9: ``spatial-aq``, bản cũ: ``spatial_aq``). Không đọc được → tập rỗng."""
        with self._lock:
            if encoder in self._options_cache:
                return self._options_cache[encoder]
        opts: set[str] = set()
        if self.ffmpeg:
            try:
                res = subprocess.run([self.ffmpeg, "-hide_banner", "-h", f"encoder={encoder}"], capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                                     timeout=20)
                opts = set(re.findall(r"^  -(\S+)\s+<", res.stdout, re.M))
            except Exception:
                opts = set()
        with self._lock:
            self._options_cache[encoder] = opts
        return opts


# ── Hàm phụ ──────────────────────────────────────────────────────────────

def _parse_rate(s: str) -> float:
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            b = float(b)
            return float(a) / b if b else 0.0
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _quote(a: str) -> str:
    return f'"{a}"' if " " in a else a


def atempo_chain(tempo: float) -> str:
    """atempo chấp nhận 0.5–100; chia nhỏ khi cần để giữ chất lượng."""
    tempo = max(0.25, min(4.0, tempo))
    parts: list[str] = []
    while tempo < 0.5:
        parts.append("atempo=0.5")
        tempo /= 0.5
    while tempo > 2.0:
        parts.append("atempo=2.0")
        tempo /= 2.0
    parts.append(f"atempo={tempo:.4f}")
    return ",".join(parts)


def ffescape_path(p: str | Path) -> str:
    """Escape đường dẫn để nhúng vào filter (ass=, subtitles=, movie=)."""
    s = str(p).replace("\\", "/")
    s = s.replace(":", "\\:").replace("'", "\\'")
    return f"'{s}'"


_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$")   # h:mm:ss(.ms) hoặc m:ss(.ms)


_HUMAN_RE = re.compile(r"^(?:(\d+)\s*(?:h|giờ)\s*)?(?:(\d+)\s*(?:m|p|ph|phút)\s*)?(?:(\d+(?:[.,]\d+)?)\s*(?:s|giây)?)?$",
                       re.I)


def parse_time(s: str) -> float | None:
    """Mốc thời gian người dùng gõ → giây; None nếu không đọc được. Nhận: 90 · 1:30 · 0:01:30.5 · 5s · 1p30 · 1m30s."""
    s = (s or "").strip()
    if not s:
        return 0.0
    m = _TIME_RE.match(s)
    if m:
        h, mi, se, ms = m.groups()
        return int(h or 0) * 3600 + int(mi) * 60 + int(se) + (int(ms.ljust(3, "0")) / 1000 if ms else 0)
    m = _HUMAN_RE.match(s)
    if m and any(m.groups()):
        h, mi, se = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float((se or "0").replace(",", "."))
    return None


def parse_hhmmss(s: str) -> float:
    t = parse_time(s)
    return 0.0 if t is None else t
