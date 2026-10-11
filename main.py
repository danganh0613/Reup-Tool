"""Điểm vào Edit Studio.

    python main.py                       mở giao diện
    python main.py --render a.mp4 b.mp4  render không mở cửa sổ, dùng cấu hình đã lưu (output/last_config.json)
        --mp3        xuất MP3 thay vì MP4
        --stt        nhận diện phụ đề (Whisper/Gemini theo cấu hình) trước khi render
        --out DIR    thư mục xuất
        --config F   đọc cấu hình làm việc từ file F (vd của bản cài) thay cho output/last_config.json — chỉ đọc
    python main.py --selftest            kiểm tra máy (GPU, CUDA, model, giọng, key) → logs/selftest.txt
    Kết quả + nhật ký ghi vào logs/edit_studio.log (bản .exe không có console).
"""

from __future__ import annotations

import logging
import sys
import traceback


def render_cli(argv: list[str]) -> int:
    """Render hàng loạt không cần giao diện (đường tắt để kiểm tra bản đóng gói / chạy theo lịch)."""
    import argparse
    import threading
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="EditStudio", add_help=True)
    ap.add_argument("--render", nargs="+", metavar="VIDEO", required=True)
    ap.add_argument("--mp3", action="store_true")
    ap.add_argument("--stt", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--config", default="")

    from app.config.paths import VIDEOS_DIR, ensure_dirs
    ensure_dirs()
    from app.core.logging_setup import setup_logging
    setup_logging()
    logger = logging.getLogger("cli")
    try:
        ns = ap.parse_args(argv)
    except SystemExit as exc:          # bản .exe không có console: argparse không in được → ghi log
        logger.error("Tham số dòng lệnh không hợp lệ: %s (xem cú pháp ở đầu main.py)", " ".join(argv))
        return int(exc.code or 2)

    from app.core.state import AppState, VideoItem
    from app.services.ffmpeg import FFmpeg
    from app.services.job_runner import Pipeline

    state = AppState()
    state.load_settings()
    state.load_config(Path(ns.config) if ns.config else None)
    state.set_save_scheduler(lambda _save: None)   # chỉ đọc: --out không ghi đè cấu hình làm việc của app
    state.output_dir = ns.out or state.output_dir or str(VIDEOS_DIR)
    ffmpeg = FFmpeg(state.settings.ffmpeg_path, state.settings.ffprobe_path)
    ffmpeg.require()
    pipe = Pipeline(state, ffmpeg)
    pipe.audio_only = bool(ns.mp3)
    cancel = threading.Event()
    failed = 0
    for path in ns.render:
        item = VideoItem(path=str(Path(path).resolve()))
        try:
            if ns.stt:
                cues = pipe.transcribe(item, log=logger.info, cancel=cancel)
                logger.info("STT: %d cue → %s", len(cues), item.srt_path)
            out = pipe.process(item, on_update=lambda _it: None, log=logger.info, cancel=cancel)
            logger.info("XONG: %s → %s", path, out)
        except Exception:
            failed += 1
            logger.exception("LỖI: %s", path)
    return 1 if failed else 0


def selftest_cli() -> int:
    """In kết quả «Kiểm tra máy» ra console + logs (dùng khi hỗ trợ từ xa: EditStudio.exe --selftest)."""
    from app.config.paths import ensure_dirs
    ensure_dirs()
    from app.core.logging_setup import setup_logging
    setup_logging()
    from app.core.state import AppState
    from app.services.selftest import as_text, run_checks

    state = AppState()
    state.load_settings()
    text = as_text(run_checks(state.settings))
    logging.getLogger("selftest").info("\n%s", text)
    try:
        sys.stdout.reconfigure(errors="replace")
        print(text)
    except Exception:
        pass
    from app.config.paths import LOGS_DIR
    (LOGS_DIR / "selftest.txt").write_text(text, encoding="utf-8")
    return 0


_MUTEX = None


def _single_instance() -> bool:
    """Windows: khoá tên theo thư mục app (named mutex) — bấm đúp exe hai lần chỉ mở một cửa sổ."""
    global _MUTEX
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        import hashlib

        from app.config.paths import ROOT

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        name = "EditStudio_" + hashlib.sha1(str(ROOT).lower().encode("utf-8")).hexdigest()[:16]
        _MUTEX = k32.CreateMutexW(None, False, name)          # giữ handle tới lúc thoát app
        return ctypes.get_last_error() != 183                  # ERROR_ALREADY_EXISTS
    except Exception:                                          # noqa: BLE001 — không khoá được thì cứ mở
        return True


def _no_dll_dialogs() -> None:
    """Windows: thư viện thiếu / sai bản DLL thì báo lỗi cho code xử lý (bỏ qua bước phụ), không bật hộp «Entry Point
    Not Found» đứng chờ bấm OK — hộp đó treo cả lượt xuất (lần chạy không giao diện thì không ai bấm)."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x8000)   # SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX
        except Exception:
            pass


def main() -> int:
    _no_dll_dialogs()
    if "--selftest" in sys.argv[1:]:
        return selftest_cli()
    if "--render" in sys.argv[1:]:
        return render_cli(sys.argv[1:])

    from app.config.paths import APP_NAME, ensure_dirs
    ensure_dirs()

    from app.core.logging_setup import setup_logging
    setup_logging()
    logger = logging.getLogger("main")
    if not _single_instance():
        logger.info("%s đang chạy — không mở thêm cửa sổ", APP_NAME)
        try:
            from tkinter import messagebox
            messagebox.showinfo(APP_NAME, f"{APP_NAME} đang mở rồi — dùng cửa sổ đang mở (xem thanh tác vụ).\n\n"
                                          "Mở hai cửa sổ cùng lúc sẽ ghi đè hàng đợi và cài đặt của nhau.")
        except Exception:
            pass
        return 0
    logger.info("Khởi động %s…", APP_NAME)

    from app.services.sysinfo import prewarm_gpu_name
    prewarm_gpu_name()

    from app.core.state import AppState
    state = AppState()
    state.load_settings()
    state.load_config()
    if not state.output_dir:
        from app.config.paths import VIDEOS_DIR
        state.output_dir = str(VIDEOS_DIR)

    from app.config import theme
    theme.apply_theme(state.settings.theme or state.settings.appearance_mode, state.settings.accent)

    from app.ui.main_window import MainWindow
    try:
        win = MainWindow(state)
    except Exception:
        logger.exception("Không dựng được cửa sổ chính")
        raise
    win.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        try:
            from tkinter import messagebox
            messagebox.showerror("Edit Studio", traceback.format_exc()[-1500:])
        except Exception:
            pass
        sys.exit(1)
