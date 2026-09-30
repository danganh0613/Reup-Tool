"""Kiểm tra máy: cái gì có, cái gì thiếu và bấm đâu để bổ sung — dùng cho nút «Kiểm tra máy» (Cài đặt) và ``--selftest``.

Mỗi mục là ``Check(name, status, detail, fix)``: status ``ok`` · ``warn`` (thiếu nhưng có cách khác) · ``bad`` (không chạy được).
Không cài/tải gì ở đây — chỉ báo và chỉ đường (nút trong module tương ứng).
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
        from app.services.render_pipeline import available_encoders

        enc = available_encoders(ff)
        ok = [k for k, v in enc.items() if v]
        names = {"h264_nvenc": "NVIDIA NVENC", "h264_qsv": "Intel QSV", "h264_amf": "AMD AMF"}
        if ok:
            out.append(Check("Render GPU", "ok", f"{gpu} · dùng {', '.join(names.get(k, k) for k in ok)}"))
        else:
            out.append(Check("Render GPU", "warn", f"{gpu} · không encoder GPU nào dùng được → render bằng CPU (chậm hơn, vẫn đúng)",
                             "Cập nhật driver card màn hình (NVIDIA cần driver ≥ 610)." if nvidia else "Máy không có card hỗ trợ — bình thường."))

    # 4. Whisper (STT)
    step("Whisper")
    try:
        import faster_whisper  # noqa: F401

        from app.services.whisper_stt import cuda_ready

        if nvidia:
            if cuda_ready():
                out.append(Check("Whisper GPU", "ok", f"cuBLAS/cuDNN sẵn sàng — nhận dạng chạy trên {gpu}"))
            else:
                out.append(Check("Whisper GPU", "warn", f"Có {gpu} nhưng thiếu CUDA runtime → nhận dạng chạy CPU (chậm 5–10 lần)",
                                 "Mục STT → nút «Tải CUDA runtime» (~1.3 GB, một lần)" if _frozen()
                                 else "Mục STT → nút «Cài CUDA runtime»"))
        else:
            out.append(Check("Whisper", "ok", "Không có GPU NVIDIA → nhận dạng bằng CPU (int8)"))
        models = sorted(re.sub(r"^models--.*?--faster-whisper-", "", p.name) for p in MODELS_DIR.glob("models--*faster-whisper-*"))
        if models:
            out.append(Check("Model Whisper", "ok", "Đã có trên máy: " + ", ".join(models)))
        else:
            out.append(Check("Model Whisper", "warn", "Chưa tải model nào — lần nhận dạng đầu sẽ tự tải (cần Internet)",
                             "Mục STT → nút «Tải trước model Whisper» để tải sẵn."))
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
                             "" if has_model else "Mục TTS → chọn VieNeu → «Nghe thử giọng» để tải."))
        else:
            out.append(Check("VieNeu (giọng Việt offline)", "warn", "Chưa có — dùng Edge TTS / ElevenLabs / Google thay thế",
                             "Bản đóng gói: cài lại bản mới nhất." if _frozen() else "Mục TTS → nút «Cài VieNeu»."))
    except Exception as exc:
        out.append(Check("VieNeu", "warn", f"Không kiểm tra được: {exc}"))

    # 6. API key
    step("API key")
    if settings is not None:
        g = len(settings.gemini_keys())
        out.append(Check("Gemini API key", "ok" if g else "warn", f"{g} key" if g else "Chưa có → không dịch AI / STT cloud / dựng truyện được",
                         "" if g else "Cài đặt → cột giữa «API keys Gemini» (miễn phí tại aistudio.google.com)."))
        e = len(settings.split_keys(settings.elevenlabs_api_keys)) + bool(settings.elevenlabs_api_key)
        gg = len(settings.google_keys())
        out.append(Check("Key giọng cloud", "ok" if (e or gg) else "warn",
                         f"ElevenLabs {e} key · Google {gg} key" if (e or gg) else "Chưa có — chỉ Edge TTS (miễn phí) và VieNeu dùng được",
                         "" if (e or gg) else "Chỉ cần khi muốn giọng ElevenLabs/Google."))

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
        out.append(Check("Internet", "warn", "Không kết nối được lúc này", "Không có mạng vẫn render/STT (đã có model)/VieNeu được."))

    # 9. Demucs (tuỳ chọn)
    try:
        from app.services.demucs_sep import is_installed as demucs_ok

        if demucs_ok():
            out.append(Check("Demucs (tách giọng/nhạc)", "ok", "Sẵn sàng"))
        else:
            out.append(Check("Demucs (tách giọng/nhạc)", "warn", "Chưa có — tính năng tách nhạc nền tắt, còn lại bình thường",
                             "Chỉ bản mã nguồn cài được (cần torch ~2 GB): mục Video → «Cài Demucs»." if not _frozen()
                             else "Bản đóng gói không hỗ trợ (cần torch ~2 GB)."))
    except Exception:
        pass
    return out


def summary(checks: list[Check]) -> str:
    n_bad = sum(c.status == "bad" for c in checks)
    n_warn = sum(c.status == "warn" for c in checks)
    if n_bad:
        return f"✖ {n_bad} mục không chạy được, {n_warn} mục thiếu tuỳ chọn"
    if n_warn:
        return f"⚠ Chạy được · {n_warn} mục có thể bổ sung (xem gợi ý)"
    return "✔ Máy đã đủ mọi thứ"


def as_text(checks: list[Check]) -> str:
    lines = [summary(checks), ""]
    for c in checks:
        lines.append(f"{c.icon} {c.name}: {c.detail}")
        if c.fix:
            lines.append(f"     → {c.fix}")
    return os.linesep.join(lines)

