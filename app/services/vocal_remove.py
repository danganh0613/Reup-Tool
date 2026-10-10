"""Khử giọng nói gốc, giữ nhạc nền + tiếng động — AI chạy ngay trên máy (ONNX, không cần torch).

Model MDX-Net của UVR (Ultimate Vocal Remover) tách phần «giọng» khỏi âm thanh; phần còn lại (nhạc, tiếng động,
không khí) thay cho âm gốc khi lồng tiếng. Model ~65 MB tải một lần; kết quả lưu cache theo video nên mỗi video chỉ
tách một lần (bước Chuẩn bị / Render), xem trước dùng lại ngay.

Suy luận bằng numpy (STFT / iSTFT giống torch.stft center=True, cửa sổ Hann) + onnxruntime, chạy theo từng đoạn ~6 s.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import wave
from collections.abc import Callable
from pathlib import Path

from app.config.paths import CACHE_DIR, MODELS_DIR
from app.services.ffmpeg import FFmpeg, RenderCancelled

logger = logging.getLogger(__name__)

SR = 44100
# Kim_Vocal_2: tách GIỌNG rất sạch (kể cả lời thoại phim); nhạc nền = gốc − giọng → giữ trọn tiếng động / nhạc
MODEL_NAME = "Kim_Vocal_2.onnx"
MODEL_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/" + MODEL_NAME
MODEL_PATH = MODELS_DIR / "uvr" / MODEL_NAME
MODEL_SIZE_MB = 64
N_FFT = 7680
HOP = 1024
COMPENSATE = 1.009            # hệ số bù biên độ của model (theo UVR)


def onnx_available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def model_ready() -> bool:
    return MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 10_000_000


def ensure_model(log: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> Path:
    if model_ready():
        return MODEL_PATH
    import requests

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if log:
        log(f"Tải model khử giọng gốc (~{MODEL_SIZE_MB} MB, chỉ lần đầu)…")
    tmp = MODEL_PATH.with_suffix(".part")
    with requests.get(MODEL_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if cancel is not None and cancel.is_set():
                    raise RenderCancelled("Đã huỷ")
                f.write(chunk)
    tmp.replace(MODEL_PATH)
    return MODEL_PATH


def _key(video_path: str | Path) -> str:
    src = Path(video_path)
    return hashlib.sha1(f"{src}|{int(src.stat().st_mtime)}|{MODEL_NAME}".encode("utf-8")).hexdigest()[:12]


def cached_no_vocals(video_path: str | Path) -> Path | None:
    """Bản đã khử giọng (nếu đã tách trước đó) — xem trước dùng nếu có, không chạy model."""
    try:
        p = CACHE_DIR / "novocal" / f"{_key(video_path)}.wav"
        return p if p.exists() else None
    except OSError:
        return None


class _MDX:
    def __init__(self, model: Path) -> None:
        import numpy as np
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        # tắt bộ nhớ đệm (arena) của onnxruntime: RAM đỉnh 2,5 GB → 1,3 GB, tốc độ không đổi
        opts.enable_cpu_mem_arena = False
        opts.enable_mem_pattern = False
        provs = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider")
                 if p in ort.get_available_providers()] + ["CPUExecutionProvider"]
        try:
            self.sess = ort.InferenceSession(str(model), sess_options=opts, providers=provs)
        except Exception:
            self.sess = ort.InferenceSession(str(model), sess_options=opts, providers=["CPUExecutionProvider"])
        inp = self.sess.get_inputs()[0]
        self.input = inp.name
        self.dim_f, self.dim_t = int(inp.shape[2]), int(inp.shape[3])
        self.n_bins = N_FFT // 2 + 1
        self.chunk = HOP * (self.dim_t - 1)
        self.trim = N_FFT // 2
        self.gen = self.chunk - 2 * self.trim
        n = np.arange(N_FFT)
        self.win = (0.5 - 0.5 * np.cos(2 * np.pi * n / N_FFT)).astype(np.float32)   # Hann tuần hoàn (= torch)

    # STFT / iSTFT giống torch.stft(center=True, pad_mode="reflect") / torch.istft
    def _stft(self, x):
        import numpy as np

        pad = N_FFT // 2
        xp = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
        frames = np.lib.stride_tricks.sliding_window_view(xp, N_FFT, axis=1)[:, ::HOP]   # (2, T, N_FFT)
        spec = np.fft.rfft(frames * self.win, axis=2)                                    # (2, T, F)
        spec = spec.transpose(0, 2, 1)[:, : self.dim_f]                                  # (2, dim_f, T)
        out = np.stack([spec.real, spec.imag], axis=1)                                   # (2, 2, dim_f, T)
        return out.reshape(4, self.dim_f, -1).astype(np.float32)

    def _istft(self, s):
        import numpy as np

        s = s.reshape(2, 2, self.dim_f, -1)
        spec = np.zeros((2, self.n_bins, s.shape[-1]), dtype=np.complex64)
        spec[:, : self.dim_f] = s[:, 0] + 1j * s[:, 1]
        frames = np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=2) * self.win       # (2, T, N_FFT)
        t = frames.shape[1]
        length = N_FFT + HOP * (t - 1)
        out = np.zeros((2, length), dtype=np.float32)
        norm = np.zeros(length, dtype=np.float32)
        w2 = self.win ** 2
        for k in range(t):
            a = k * HOP
            out[:, a:a + N_FFT] += frames[:, k]
            norm[a:a + N_FFT] += w2
        pad = N_FFT // 2
        out, norm = out[:, pad:pad + self.chunk], norm[pad:pad + self.chunk]
        return out / np.maximum(norm, 1e-8)

    def separate_file(self, src: Path, dst: Path, on_progress=None, cancel=None) -> float:
        """Đọc WAV nguồn theo từng đoạn ~6 s, ghi thẳng bản đã khử giọng ra ``dst`` — RAM không phụ thuộc độ dài
        video (phim 2 giờ vẫn chỉ vài chục MB). Trả thời lượng (giây)."""
        import numpy as np

        tmp = dst.with_suffix(".part.wav")
        with wave.open(str(src), "rb") as rf, wave.open(str(tmp), "wb") as wf:
            ch, n = rf.getnchannels(), rf.getnframes()
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SR)

            def read(a: int, length: int):
                """Mẫu [a, a+length) của nguồn, ngoài phạm vi là 0 (giống đệm 0 hai đầu của UVR)."""
                out = np.zeros((2, length), dtype=np.float32)
                s0, e0 = max(0, a), min(n, a + length)
                if e0 > s0:
                    rf.setpos(s0)
                    x = np.frombuffer(rf.readframes(e0 - s0), dtype=np.int16).astype(np.float32) / 32768.0
                    x = x.reshape(-1, ch).T
                    out[:, s0 - a:s0 - a + x.shape[1]] = x[:2] if ch >= 2 else np.vstack([x, x])
                return out

            starts = list(range(0, n, self.gen))
            for k, i in enumerate(starts):
                if (cancel is not None and cancel.is_set()) or SHUTDOWN.is_set():
                    raise RenderCancelled("Đã huỷ khử giọng")
                seg = read(i - self.trim, self.chunk)
                spec = self._stft(seg)[None]
                # một lượt model (lượt «khử nhiễu» f(x) − f(−x) của UVR không khác gì về mức khử giọng mà chậm gấp đôi)
                voc = self._istft(self.sess.run(None, {self.input: spec})[0][0])
                m = min(self.gen, n - i)
                inst = seg[:, self.trim:self.trim + m] - voc[:, self.trim:self.trim + m] * COMPENSATE
                wf.writeframes((np.clip(inst.T, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
                if on_progress:
                    on_progress((k + 1) / len(starts))
        tmp.replace(dst)
        return n / SR


_RUN = threading.Lock()            # mỗi lúc chỉ khử một video (model nặng CPU/RAM; xem trước + render dùng chung)
SHUTDOWN = threading.Event()       # app đang đóng → dừng ở đoạn kế tiếp (đừng để onnxruntime chạy khi thoát)
_PROG: dict[str, float] = {}       # tiến độ lượt khử đang chạy (theo video) — luồng đang chờ cùng video đọc để báo tiếp


def shutdown(timeout: float = 8.0) -> None:
    """Gọi khi đóng app: báo dừng và chờ đoạn đang chạy xong (onnxruntime bị cắt ngang lúc thoát sẽ crash)."""
    SHUTDOWN.set()
    if _RUN.acquire(timeout=timeout):
        _RUN.release()


def separate_no_vocals(video_path: str | Path, ffmpeg: FFmpeg, *, log: Callable[[str], None] | None = None,
                       progress: Callable[[float], None] | None = None,
                       cancel: threading.Event | None = None) -> Path | None:
    """WAV đã khử giọng nói (giữ nhạc nền + tiếng động), cache theo video. None nếu không làm được.
    Hai luồng cùng xin một video (xem trước + render) → luồng sau chờ rồi dùng kết quả của luồng trước."""
    got = cached_no_vocals(video_path)
    if got is not None:
        return got
    key = _key(video_path)
    while not _RUN.acquire(timeout=0.5):
        # luồng khác đang khử (vd. xem trước) → không đứng im: báo tiến độ của lượt đó nếu cùng video
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ")
        if progress and key in _PROG:
            progress(_PROG[key])
    try:
        if SHUTDOWN.is_set():
            return None
        return _separate(video_path, ffmpeg, log=log, progress=progress, cancel=cancel)
    finally:
        _RUN.release()


def _separate(video_path, ffmpeg: FFmpeg, *, log, progress, cancel) -> Path | None:
    got = cached_no_vocals(video_path)
    if got is not None:
        return got
    if not onnx_available():
        if log:
            log("⚠ Thiếu onnxruntime — không khử được giọng gốc (pip install onnxruntime).")
        return None
    try:
        model = ensure_model(log, cancel)
    except RenderCancelled:
        raise
    except Exception as exc:
        if log:
            log(f"⚠ Không tải được model khử giọng: {exc}")
        return None
    out_dir = CACHE_DIR / "novocal"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = out_dir / f"{_key(video_path)}.wav"
    src_wav = out_dir / f"{_key(video_path)}.src.wav"
    try:
        ffmpeg.extract_audio_wav(Path(video_path), src_wav, sample_rate=SR, channels=2)
        if log:
            with wave.open(str(src_wav), "rb") as rf:
                log(f"Khử giọng gốc bằng AI ({rf.getnframes() / SR:.0f}s âm thanh)…")
        key = _key(video_path)

        def prog(x: float) -> None:
            _PROG[key] = x
            if progress:
                progress(x)

        _MDX(model).separate_file(src_wav, result, on_progress=prog, cancel=cancel)
        if log:
            log("Đã khử giọng gốc — giữ nhạc nền & tiếng động.")
        return result
    except RenderCancelled:
        raise
    except Exception as exc:
        logger.warning("Khử giọng lỗi: %s", video_path, exc_info=True)
        if log:
            log(f"⚠ Khử giọng gốc lỗi ({exc}) — dùng âm gốc vặn rất nhỏ.")
        return None
    finally:
        _PROG.pop(_key(video_path), None)
        src_wav.unlink(missing_ok=True)
        result.with_suffix(".part.wav").unlink(missing_ok=True)
