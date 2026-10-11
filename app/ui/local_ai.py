"""Cài / tải các phần AI chạy trên máy — dùng chung cho tab STT / TTS và Cài đặt › AI trên máy.

Mỗi hàm hỏi trước khi tải nặng, chạy nền (tiến độ ở log) rồi gọi ``on_done(ok, lỗi)``; trả True nếu đã bắt đầu."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from tkinter import messagebox

from app.services.ffmpeg import RenderCancelled

Done = Callable[[bool, Exception | None], None]


def _finish(on_done: Done) -> Callable[[object | None, Exception | None], None]:
    return lambda ok, error: on_done(bool(ok) and error is None, error)


def _must(task: Callable[[threading.Event], object], what: str) -> Callable[[threading.Event], object]:
    """Hàm cài trả False (pip lỗi, mất mạng…) → thành lỗi thật: thanh trạng thái báo «… lỗi» + Thử lại, không báo
    «xong» xanh trong khi Cài đặt vẫn ghi «Chưa cài». Bấm Dừng → «đã dừng»."""
    def run(cancel: threading.Event) -> object:
        result = task(cancel)
        if cancel.is_set():
            raise RenderCancelled(f"Đã huỷ {what[0].lower() + what[1:]}")
        if result is False:
            raise RuntimeError(f"{what} không thành công — xem dòng log ngay trên để biết lý do.")
        return result
    return run


def install_qwen(window, on_done: Done) -> bool:
    """Bộ nghe lời thoại Qwen3-ASR → models/qwen_asr_env (môi trường Python riêng, không đụng thư viện của app)."""
    import shutil

    from app.config.paths import MODELS_DIR
    from app.services.qwen_asr import INSTALL_GB, _has_nvidia, install, is_installed

    if is_installed():
        on_done(True, None)
        return False
    if getattr(sys, "frozen", False):            # báo ngay, không hỏi «tải 11 GB» rồi mới lỗi
        messagebox.showinfo("Edit Studio", "Bản .exe chưa tự cài được bộ nghe lời thoại.\n\nChép thư mục "
                                           "models\\qwen_asr_env và models\\hf từ máy đã cài sang thư mục app này. Video có "
                                           "phụ đề in sẵn thì vẫn lấy được lời từ chữ trên hình.")
        on_done(False, RuntimeError("Bản .exe chưa tự cài được Qwen3-ASR"))
        return False
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        free = shutil.disk_usage(MODELS_DIR).free / 2 ** 30
    except OSError:
        free = INSTALL_GB + 1.0
    if free < INSTALL_GB + 1:
        messagebox.showwarning("Edit Studio", f"Ổ chứa app chỉ còn {free:.1f} GB — cần khoảng {INSTALL_GB + 1} GB để cài "
                                              "bộ nghe lời thoại. Giải phóng dung lượng rồi cài lại.")
        return False
    cpu = "" if _has_nvidia() else ("\n\n⚠ Máy không có card NVIDIA: nhận diện chạy bằng CPU, rất chậm (video 2 phút "
                                    "có thể mất 10–20 phút) và cần RAM ≥ 16 GB. Video có phụ đề in sẵn thì chọn «Chữ in "
                                    "sẵn trên video» sẽ nhanh hơn nhiều.")
    if not messagebox.askyesno("Edit Studio", "Cài bộ nghe lời thoại Qwen3-ASR (chạy trên máy)?\n\n"
                               f"Tải khoảng {INSTALL_GB} GB (thư viện + model lần đầu nhận diện), mất vài chục phút. "
                               "Thư viện của Edit Studio không bị thay đổi." + cpu):
        return False

    def step(k: int, n: int, what: str) -> None:            # «Đang chạy…» đứng yên cả chục phút → tên bước + tiến độ
        window.ui_call(lambda: (window.progress.set_detail(f"Bước {k}/{n}: {what}…"),
                                window.progress.set_progress(k / (n + 1))))

    window.run_task("Cài Qwen3-ASR", _must(lambda cancel: install(window.log_bg, cancel, on_step=step), "Cài Qwen3-ASR"),
                    _finish(on_done))
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
    window.run_task("Cài VieNeu", _must(lambda _cancel: install(window.log_bg), "Cài VieNeu"), _finish(on_done))
    return True


def install_cuda(window, on_done: Done) -> bool:
    """cuBLAS + cuDNN cho Whisper chạy GPU NVIDIA (~1,3–1,5 GB, một lần)."""
    from app.services.whisper_stt import CUDA_DOWNLOAD_GB, install_cuda_runtime

    size = f"~{CUDA_DOWNLOAD_GB} GB" if getattr(sys, "frozen", False) else "~1.5 GB"
    if not messagebox.askyesno("Edit Studio", f"Tải CUDA runtime (cuBLAS + cuDNN, {size}, một lần) để Whisper chạy "
                                              "trên GPU NVIDIA?"):
        return False
    window.run_task("Tải CUDA runtime", _must(lambda cancel: install_cuda_runtime(window.log_bg, cancel),
                                              "Tải CUDA runtime"), _finish(on_done))
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
    window.run_task("Cài bộ căn chữ nổi", _must(lambda cancel: install(window.log_bg, cancel), "Cài bộ căn chữ nổi"),
                    _finish(on_done))
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
