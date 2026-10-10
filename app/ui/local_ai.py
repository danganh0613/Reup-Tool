"""Cài / tải các phần AI chạy trên máy — dùng chung cho tab STT / TTS và Cài đặt › AI trên máy.

Mỗi hàm hỏi trước khi tải nặng, chạy nền (tiến độ ở log) rồi gọi ``on_done(ok, lỗi)``; trả True nếu đã bắt đầu."""

from __future__ import annotations

import sys
from collections.abc import Callable
from tkinter import messagebox

Done = Callable[[bool, Exception | None], None]


def _finish(on_done: Done) -> Callable[[object | None, Exception | None], None]:
    return lambda ok, error: on_done(bool(ok) and error is None, error)


def install_qwen(window, on_done: Done) -> bool:
    """Bộ nghe lời thoại Qwen3-ASR → models/qwen_asr_env (môi trường Python riêng, không đụng thư viện của app)."""
    from app.services.qwen_asr import INSTALL_GB, install, is_installed

    if is_installed():
        on_done(True, None)
        return False
    if not messagebox.askyesno("Edit Studio", "Cài bộ nghe lời thoại Qwen3-ASR (chạy trên máy)?\n\n"
                               f"Tải khoảng {INSTALL_GB} GB (thư viện + model lần đầu nhận diện), mất vài chục phút. "
                               "Thư viện của Edit Studio không bị thay đổi."):
        return False
    window.run_task("Cài Qwen3-ASR", lambda cancel: install(window.log_bg, cancel), _finish(on_done))
    return True


def install_vieneu(window, on_done: Done) -> bool:
    """Giọng đọc VieNeu (pip install vieneu; model ~300 MB tự tải lần đầu đọc)."""
    from app.services.tts.vieneu import install_vieneu as install, is_installed

    if is_installed():
        on_done(True, None)
        return False
    if getattr(sys, "frozen", False):
        on_done(False, RuntimeError("Bản đóng gói (.exe / .app) không có VieNeu — chạy bản mã nguồn (run.bat / run.command)."))
        return False
    window.run_task("Cài VieNeu", lambda _cancel: install(window.log_bg), _finish(on_done))
    return True


def install_cuda(window, on_done: Done) -> bool:
    """cuBLAS + cuDNN cho Whisper chạy GPU NVIDIA (~1,3–1,5 GB, một lần)."""
    from app.services.whisper_stt import CUDA_DOWNLOAD_GB, install_cuda_runtime

    size = f"~{CUDA_DOWNLOAD_GB} GB" if getattr(sys, "frozen", False) else "~1.5 GB"
    if not messagebox.askyesno("Edit Studio", f"Tải CUDA runtime (cuBLAS + cuDNN, {size}, một lần) để Whisper chạy "
                                              "trên GPU NVIDIA?"):
        return False
    window.run_task("Tải CUDA runtime", lambda cancel: install_cuda_runtime(window.log_bg, cancel), _finish(on_done))
    return True


def install_aligner(window, on_done: Done) -> bool:
    """Bộ căn chữ nổi (CTC tiếng Việt): chữ nổi sáng đúng từng tiếng đọc. Tải ~380 MB, lưu ~190 MB."""
    from app.services.ctc_align import DISK_MB, DOWNLOAD_MB, install, model_ready

    if model_ready():
        on_done(True, None)
        return False
    if not messagebox.askyesno("Edit Studio", "Tải bộ căn chữ nổi (chữ nổi sáng đúng từng tiếng đọc)?\n\n"
                               f"Tải ~{DOWNLOAD_MB} MB, lưu ~{DISK_MB} MB, một lần. Model wav2vec2 tiếng Việt của "
                               "nguyenvulebinh — giấy phép CC-BY-NC 4.0 (chỉ dùng phi thương mại)."):
        return False
    window.run_task("Cài bộ căn chữ nổi", lambda cancel: install(window.log_bg, cancel), _finish(on_done))
    return True


def whisper_size(nvidia: bool) -> str:
    """Model Whisper nên tải cho việc kiểm lỗi giọng đọc: GPU → large-v3-turbo (chính xác), CPU → small (nhẹ)."""
    return "large-v3-turbo" if nvidia else "small"


def download_whisper(window, size: str, on_done: Done) -> bool:
    """Model Whisper để nghe lại câu lồng tiếng (kiểm lỗi) và tách đoạn đọc liền."""
    from app.services.whisper_stt import download_whisper_model

    mb = {"large-v3-turbo": "1.6 GB", "small": "480 MB", "base": "150 MB"}.get(size, "")
    if not messagebox.askyesno("Edit Studio", f"Tải model Whisper «{size}» ({mb}, một lần)?"):
        return False
    window.run_task(f"Tải Whisper {size}", lambda _cancel: download_whisper_model(size, window.log_bg), _finish(on_done))
    return True
