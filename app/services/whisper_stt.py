"""Whisper (faster-whisper) chạy ngầm: nghe lại câu lồng tiếng để kiểm lỗi và tách đoạn đọc gộp (tts/quality.py,
tts/batch.py). Ở đây: nạp / giải phóng model, tải model, CUDA runtime cho GPU NVIDIA; ``STTResult`` dùng chung cho các
bộ nhận diện (Qwen3-ASR, OCR)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config.paths import CACHE_DIR, MODELS_DIR
from app.services.ffmpeg import RenderCancelled
from app.services.srt_utils import Cue

logger = logging.getLogger(__name__)

_model_cache: dict[tuple[str, str, str], object] = {}
_model_lock = threading.Lock()
_last_used = 0.0                    # lần cuối lấy model (monotonic) — để giải phóng khi lâu không dùng


@dataclass
class STTResult:
    cues: list[Cue]
    language: str
    language_probability: float


def _add_cuda_dll_dirs() -> None:
    """Cho ctranslate2 thấy cuBLAS/cuDNN nếu được cài qua pip (nvidia-*-cu12) hoặc CUDA Toolkit."""
    import os
    import site
    import sys

    if os.name != "nt":
        return
    roots = [Path(p) for p in site.getsitepackages() + [site.getusersitepackages()] if p]
    cands: list[Path] = []
    for root in roots:
        for name in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            cands.append(root / "nvidia" / name / "bin")
    if os.environ.get("CUDA_PATH"):
        cands.append(Path(os.environ["CUDA_PATH"]) / "bin")
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).parent / "cuda")
    for d in cands:
        if d.is_dir() and str(d) not in os.environ.get("PATH", ""):
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def cuda_ready() -> bool:
    """cuBLAS 12 + cuDNN 9 nạp được → Whisper chạy được trên GPU NVIDIA."""
    import ctypes

    _add_cuda_dll_dirs()
    try:
        ctypes.WinDLL("cublas64_12.dll")
        ctypes.WinDLL("cudnn64_9.dll")
        return True
    except OSError:
        return False


#: wheel PyPI chứa DLL CUDA 12 cho ctranslate2 — cùng phiên bản đã kiểm chứng trên máy build (tools/bundle_cuda.py)
CUDA_WHEELS = (("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.26.0.51"), ("nvidia-cuda-nvrtc-cu12", "12.9.86"))
_CUDA_SKIP = ("nvblas", ".alt.", "cudnn_adv")      # không cần cho Whisper (giống bundle_cuda.py)
CUDA_DOWNLOAD_GB = 1.3


def cuda_dir_for_exe() -> Path:
    import sys

    return Path(sys.executable).parent / "cuda"


def _wheel_url(pkg: str, ver: str) -> tuple[str, int]:
    import requests

    r = requests.get(f"https://pypi.org/pypi/{pkg}/{ver}/json", timeout=30)
    r.raise_for_status()
    for u in r.json().get("urls", []):
        if u.get("filename", "").endswith("win_amd64.whl"):
            return u["url"], int(u.get("size") or 0)
    raise RuntimeError(f"PyPI không có wheel Windows cho {pkg} {ver}")


def download_cuda_runtime(dest: Path, log: Callable[[str], None] | None = None,
                          cancel: threading.Event | None = None) -> bool:
    """Tải 3 wheel nvidia-* (~1.3 GB) từ PyPI, rút các DLL cần thiết vào ``dest`` (bản .exe không có pip)."""
    import requests

    dest.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / "cuda_dl"
    tmp.mkdir(parents=True, exist_ok=True)
    n_dll = 0
    for i, (pkg, ver) in enumerate(CUDA_WHEELS, 1):
        url, size = _wheel_url(pkg, ver)
        whl = tmp / url.rsplit("/", 1)[-1]
        if not (whl.exists() and size and whl.stat().st_size == size):
            if log:
                log(f"[{i}/{len(CUDA_WHEELS)}] Tải {pkg} {ver} ({size / 2**20:,.0f} MB)…")
            part = whl.with_suffix(".part")
            with requests.get(url, stream=True, timeout=60) as r, part.open("wb") as f:
                r.raise_for_status()
                done, last = 0, -1
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if cancel is not None and cancel.is_set():
                        raise RenderCancelled("Đã huỷ tải CUDA")
                    f.write(chunk)
                    done += len(chunk)
                    pct = int(done * 100 / size) if size else 0
                    if log and pct // 10 != last // 10:
                        last = pct
                        log(f"    {pkg}: {pct}% ({done / 2**20:,.0f} MB)")
            part.replace(whl)
        import zipfile

        with zipfile.ZipFile(whl) as z:
            for m in z.namelist():
                low = m.lower()
                if low.startswith("nvidia/") and "/bin/" in low and low.endswith(".dll") and not any(k in low for k in _CUDA_SKIP):
                    (dest / Path(m).name).write_bytes(z.read(m))
                    n_dll += 1
        whl.unlink(missing_ok=True)
    if log:
        log(f"Đã chép {n_dll} DLL vào {dest}")
    _add_cuda_dll_dirs()
    return cuda_ready()


def download_whisper_model(size: str, log: Callable[[str], None] | None = None) -> str:
    """Tải trước model Whisper (để dùng offline / khỏi chờ ở lần nhận dạng đầu). Trả về thư mục model."""
    path = _download(size, log)
    if log:
        log(f"Model Whisper '{size}' sẵn sàng: {path}")
    return path


def install_cuda_runtime(log: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> bool:
    """Cài cuBLAS + cuDNN (CUDA 12) để faster-whisper dùng GPU: bản mã nguồn dùng pip, bản .exe tải wheel từ PyPI."""
    import os
    import subprocess
    import sys

    if getattr(sys, "frozen", False):
        return download_cuda_runtime(cuda_dir_for_exe(), log, cancel)
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"]
    if log:
        log("pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 … (~1.5 GB, vài phút)")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if proc.returncode != 0:
        if log:
            log("pip lỗi: " + proc.stderr[-800:])
        return False
    return cuda_ready()


def _resolve_device(device: str, compute: str) -> list[tuple[str, str]]:
    """Danh sách (device, compute_type) sẽ thử lần lượt."""
    if device == "cpu":
        return [("cpu", compute if compute not in ("auto", "float16") else "int8")]
    if device == "cuda":
        return [("cuda", compute if compute != "auto" else "float16"), ("cuda", "int8_float16")]
    # auto: thử GPU trước, hỏng thì về CPU
    return [("cuda", "float16"), ("cpu", "int8")]


def _download(size: str, log: Callable[[str], None] | None) -> str:
    """Tải (hoặc tìm trong cache) model rồi trả về thư mục snapshot.

    Tách riêng khỏi bước nạp lên GPU/CPU để lỗi tải (mạng, symlink Windows) không bị
    nhầm thành lỗi CUDA. Windows không có quyền tạo symlink → HF Hub ném WinError 1314
    ở lần đầu, chạy lại là file đã có đủ nên thử tối đa 3 lần.
    """
    import os

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from faster_whisper import download_model

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    local = list(MODELS_DIR.glob(f"models--*faster-whisper-{size}*"))
    if not local and log:
        log(f"Đang tải model Whisper '{size}' (lần đầu, có thể mất vài phút)…")
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return download_model(size, cache_dir=str(MODELS_DIR))
        except Exception as exc:
            last_err = exc
            logger.warning("Tải model Whisper %s lỗi (lần %d): %s", size, attempt + 1, exc)
    raise RuntimeError(f"Không tải được model Whisper '{size}': {last_err}")


def release_idle(max_idle: float) -> bool:
    """Bỏ model Whisper đã nạp (0,5–2 GB RAM/VRAM) nếu ``max_idle`` giây chưa dùng. Lần nhận diện sau nạp lại."""
    with _model_lock:
        if not _model_cache or time.monotonic() - _last_used < max_idle:
            return False
        _model_cache.clear()
    import gc

    gc.collect()
    logger.info("Đã giải phóng model Whisper (lâu không dùng)")
    return True


def load_model(size: str, device: str = "auto", compute: str = "auto", log: Callable[[str], None] | None = None):
    from faster_whisper import WhisperModel

    global _last_used
    key = (size, device, compute)
    with _model_lock:
        _last_used = time.monotonic()
        if key in _model_cache:
            return _model_cache[key]
        path = _download(size, log)
        _add_cuda_dll_dirs()
        last_err: Exception | None = None
        for dev, ct in _resolve_device(device, compute):
            try:
                if log:
                    log(f"Nạp Whisper {size} ({dev}/{ct})…")
                model = WhisperModel(path, device=dev, compute_type=ct)
                if dev == "cuda":
                    # Khởi tạo xong chưa chắc chạy được: thiếu cublas/cudnn chỉ lộ khi encode → thử 1 giây im lặng
                    import numpy as np

                    model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1)
                _model_cache[key] = model
                logger.info("Whisper %s sẵn sàng trên %s (%s)", size, dev, ct)
                return model
            except Exception as exc:  # thiếu CUDA/cuDNN → thử cấu hình kế
                last_err = exc
                logger.warning("Không nạp được Whisper trên %s/%s: %s", dev, ct, exc)
                if log:
                    log(f"  ↳ {dev}/{ct} không dùng được: {str(exc)[:160]}")
        raise RuntimeError(f"Không nạp được model Whisper: {last_err}")
