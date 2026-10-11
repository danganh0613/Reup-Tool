"""Kiểm tra máy: cái gì có, cái gì thiếu và bấm đâu để bổ sung — dùng cho nút «Kiểm tra máy» (Cài đặt) và ``--selftest``.

Mỗi mục là ``Check(name, status, detail, fix)``: status ``ok`` · ``warn`` (thiếu nhưng có cách khác) · ``bad`` (không chạy được).
``page``: trang Cài đặt có nút cài / nhập (cửa sổ «Kiểm tra máy» hiện nút mở thẳng trang đó) · ``optional``: thiếu vẫn
làm được việc chính (không tính vào số «cần cài»). Không cài/tải gì ở đây — chỉ báo và chỉ đường.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass

from app.config.paths import APP_VERSION, MODELS_DIR, ROOT


@dataclass
class Check:
    name: str
    status: str            # ok | warn | bad
    detail: str
    fix: str = ""
    page: str = ""         # trang Cài đặt để bổ sung: local · cloud · export
    optional: bool = False

    @property
    def icon(self) -> str:
        return {"ok": "✔", "warn": "⚠", "bad": "✖"}[self.status]


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def run_checks(settings=None, progress: Callable[[str], None] | None = None) -> list[Check]:
    """Chạy tuần tự (vài giây: có gọi ffmpeg/nvidia-smi). ``settings`` để đếm API key."""
    out: list[Check] = []

    def step(name: str) -> None:
        if progress:
            progress(name)

    # 1. Môi trường
    step("Môi trường")
    import platform

    os_name = f"macOS {platform.mac_ver()[0]}" if sys.platform == "darwin" else f"{platform.system()} {platform.release()}"
    out.append(Check("Phiên bản", "ok", f"Edit Studio {APP_VERSION} · {'bản đóng gói' if _frozen() else 'bản mã nguồn'} · "
                                        f"{os_name} ({platform.machine()}) · Python {platform.python_version()}"))
    try:
        free = shutil.disk_usage(ROOT).free / 2**30
        out.append(Check("Ổ đĩa", "ok" if free > 10 else "warn", f"Còn trống {free:,.1f} GB tại {ROOT}",
                         "" if free > 10 else "Video xuất + model cần vài GB — dọn bớt ổ đĩa."))
    except OSError:
        pass

    # 2. FFmpeg
    step("FFmpeg")
    from app.services.ffmpeg import FFmpeg

    ff = FFmpeg(settings.ffmpeg_path if settings else "", settings.ffprobe_path if settings else "")
    if ff.available:
        out.append(Check("FFmpeg", "ok", ff.version()))
    else:
        out.append(Check("FFmpeg", "bad", "Không tìm thấy ffmpeg/ffprobe", "Cài lại bộ cài (ffmpeg có sẵn trong thư mục bin\\)."))

    # 3. GPU + bộ mã hoá
    step("GPU / bộ mã hoá")
    from app.services.sysinfo import gpu_name

    gpu = gpu_name()
    nvidia = "NVIDIA" in gpu.upper()
    if ff.available:
        from app.core.state import VideoConfig
        from app.services.render_pipeline import BACKEND_LABEL, available_encoders, hw_encoder_of, pick_encoder

        enc = available_encoders(ff)
        ok = [k for k, v in enc.items() if v]
        if ok:
            # xuất thử đúng bộ tham số khi xuất thật (encoder_works chỉ thử tên encoder — ffmpeg mới đổi tên tuỳ chọn
            # thì chỉ lộ ra ở đây)
            args = pick_encoder(ff, VideoConfig(use_gpu=True, gpu_backend="auto"))
            res = ff.run_simple(["-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=0.3", *args, "-f", "null", "-"],
                                timeout=30)
            hw = hw_encoder_of(args) or ""
            if res.returncode == 0:
                out.append(Check("Render GPU", "ok", f"{gpu} · dùng {', '.join(BACKEND_LABEL.get(k, k) for k in ok)}"))
            else:
                out.append(Check("Render GPU", "warn", f"{gpu} · xuất thử bằng {hw} lỗi → app tự xuất bằng CPU (chậm hơn)",
                                 "Cập nhật driver card màn hình, hoặc tắt «Tăng tốc GPU» (tab Video › Nâng cao xuất file)."))
        else:
            out.append(Check("Render GPU", "warn", f"{gpu} · không encoder GPU nào dùng được → render bằng CPU (chậm hơn, vẫn đúng)",
                             "Cập nhật driver card màn hình (NVIDIA cần driver ≥ 610)." if nvidia else "Máy không có card hỗ trợ — bình thường."))

    # 4. Nghe lời thoại (Qwen3-ASR) + Whisper kiểm lỗi giọng đọc
    step("Nhận diện lời")
    try:
        from app.services.qwen_asr import INSTALL_GB, is_installed as qwen_installed

        if qwen_installed():
            out.append(Check("Nghe lời thoại (Qwen3-ASR)", "ok", "Đã cài — chạy trên máy" + (f" ({gpu})" if nvidia else " (CPU, chậm)")))
        elif _frozen():
            out.append(Check("Nghe lời thoại (Qwen3-ASR)", "warn", "Chưa có → video tiếng nước ngoài chưa nhận diện được "
                             "(trừ video có chữ in sẵn)",
                             "Bản .exe chưa tự cài được: chép models\\qwen_asr_env + models\\hf từ máy đã cài.", "local"))
        else:
            out.append(Check("Nghe lời thoại (Qwen3-ASR)", "warn", "Chưa cài → video tiếng nước ngoài chưa nhận diện được "
                             "(trừ video có chữ in sẵn)", f"Cài một lần, ~{INSTALL_GB} GB.", "local"))
    except Exception as exc:
        out.append(Check("Nghe lời thoại (Qwen3-ASR)", "warn", f"Không kiểm tra được: {exc}"))
    try:
        from app.services.ocr_hardsub import available as ocr_ok

        out.append(Check("Đọc chữ in sẵn (OCR)", "ok" if ocr_ok() else "warn",
                         "Sẵn sàng (PaddleOCR)" if ocr_ok() else "Thiếu PaddleOCR → không đối chiếu được chữ in trên video "
                         "(tên riêng có thể nghe sai)", "" if ocr_ok() else "Không bắt buộc.", optional=True))
    except Exception:
        pass
    try:
        import faster_whisper  # noqa: F401

        from app.services.whisper_stt import cuda_ready

        if nvidia:
            if cuda_ready():
                out.append(Check("Whisper GPU", "ok", f"cuBLAS/cuDNN sẵn sàng — kiểm lỗi giọng đọc chạy trên {gpu}"))
            else:
                out.append(Check("Whisper GPU", "warn", f"Có {gpu} nhưng thiếu CUDA runtime → bỏ bước Whisper nghe lại giọng đọc",
                                 "«Cài CUDA» (một lần).", "local", optional=True))
        models = sorted(re.sub(r"^models--.*?--faster-whisper-", "", p.name) for p in MODELS_DIR.glob("models--*faster-whisper-*"))
        if models:
            out.append(Check("Model Whisper", "ok", "Đã có trên máy: " + ", ".join(models)))
        else:
            out.append(Check("Model Whisper", "warn", "Chưa có → không nghe lại được câu lồng tiếng, không đọc liền câu",
                             "«Tải» ở mục kiểm lỗi giọng đọc.", "local", optional=True))
    except Exception as exc:
        out.append(Check("Whisper", "bad", f"Không nạp được faster-whisper: {exc}", "Cài lại bộ cài."))

    # 5. Giọng đọc
    step("Giọng đọc")
    try:
        import edge_tts  # noqa: F401

        out.append(Check("Edge TTS", "ok", "Giọng đọc miễn phí (cần Internet khi tạo giọng)"))
    except Exception as exc:
        out.append(Check("Edge TTS", "bad", f"Thiếu edge-tts: {exc}", "Cài lại bộ cài."))
    try:
        from app.services.tts.vieneu import is_installed

        if is_installed():
            hf = MODELS_DIR / "hf"
            has_model = hf.exists() and any(hf.rglob("*.onnx"))
            out.append(Check("VieNeu (giọng Việt offline)", "ok" if has_model else "warn",
                             "Sẵn sàng, model đã tải" if has_model else "Có sẵn — lần dùng đầu sẽ tự tải model (~300 MB, cần Internet)",
                             "" if has_model else "Tab Lồng tiếng → chọn VieNeu → «Nghe thử giọng» để tải."))
        else:
            out.append(Check("VieNeu (giọng Việt offline)", "warn", "Chưa có — dùng Edge TTS thay thế",
                             "Bản đóng gói: cài lại bản mới nhất." if _frozen() else "«Cài» (một lần).",
                             "" if _frozen() else "local", optional=True))
    except Exception as exc:
        out.append(Check("VieNeu", "warn", f"Không kiểm tra được: {exc}"))

    # 6. API key
    step("API key")
    if settings is not None:
        g = len(settings.gemini_keys())
        out.append(Check("Gemini API key", "ok" if g else "warn", f"{g} key" if g else "Chưa có → không dịch AI / dựng truyện được",
                         "" if g else "Miễn phí tại aistudio.google.com.", "cloud"))

    # 7. Font icon
    step("Font")
    from app.ui.icons import ICON_FONT

    out.append(Check("Font icon", "ok" if ICON_FONT else "warn", ICON_FONT or "Không có Segoe Fluent Icons / MDL2 → icon hiện bằng chữ",
                     "" if ICON_FONT else "Windows 10/11 có sẵn; máy thiếu font thì vẫn dùng được."))

    # 8. Internet
    step("Internet")
    try:
        import requests

        requests.head("https://pypi.org", timeout=4)
        out.append(Check("Internet", "ok", "Kết nối được (cần cho dịch AI, giọng cloud, tải model lần đầu)"))
    except Exception:
        out.append(Check("Internet", "warn", "Không kết nối được lúc này", "Không có mạng vẫn xuất video, nhận diện (đã có model), đọc VieNeu được."))

    # 9. Khử giọng nói gốc (AI, ONNX)
    try:
        from app.services.vocal_remove import MODEL_SIZE_MB as NV_MB, model_ready
        from app.services.vocal_remove import onnx_available as nv_onnx

        if not nv_onnx():
            out.append(Check("Khử giọng gốc (AI)", "warn", "Thiếu onnxruntime / numpy — không khử được giọng gốc",
                             "pip install onnxruntime numpy"))
        elif model_ready():
            out.append(Check("Khử giọng gốc (AI)", "ok", "Sẵn sàng"))
        else:
            out.append(Check("Khử giọng gốc (AI)", "ok", f"Model (~{NV_MB} MB) tự tải lần đầu dùng"))
    except Exception:
        pass

    # 10. Nhận diện người nói (lồng tiếng nhiều giọng)
    try:
        from app.services.diarize import MODEL_PATH, MODEL_SIZE_MB, onnx_available

        if not onnx_available():
            out.append(Check("Nhận diện người nói", "warn", "Thiếu onnxruntime — «Mỗi người nói một giọng» phải gán tay",
                             "Chạy: pip install onnxruntime" if not _frozen() else "Bản đóng gói thiếu onnxruntime."))
        elif MODEL_PATH.exists():
            out.append(Check("Nhận diện người nói", "ok", "Sẵn sàng"))
        else:
            out.append(Check("Nhận diện người nói", "ok", f"Sẵn sàng (model ~{MODEL_SIZE_MB} MB tải ở lần dùng đầu)"))
    except Exception:
        pass
    return out


def summary(checks: list[Check]) -> str:
    n_bad = sum(c.status == "bad" for c in checks)
    need = sum(c.status == "warn" and not c.optional for c in checks)
    extra = sum(c.status == "warn" and c.optional for c in checks)
    if n_bad:
        return f"✖ {n_bad} mục không chạy được" + (f", {need} mục cần cài" if need else "")
    if need:
        return f"⚠ Còn {need} mục cần cho việc chính" + (f" · {extra} mục tuỳ chọn" if extra else "")
    if extra:
        return f"✔ Đủ cho việc chính · {extra} mục tuỳ chọn có thể bổ sung"
    return "✔ Máy đã đủ mọi thứ"


def as_text(checks: list[Check]) -> str:
    lines = [summary(checks), ""]
    for c in checks:
        lines.append(f"{c.icon} {c.name}: {c.detail}")
        if c.fix:
            lines.append(f"     → {c.fix}")
    return os.linesep.join(lines)

