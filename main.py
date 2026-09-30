"""Điểm vào Edit Studio.

    python main.py                       mở giao diện
    python main.py --render a.mp4 b.mp4  render không mở cửa sổ, dùng cấu hình đã lưu (output/last_config.json)
        --mp3        xuất MP3 thay vì MP4
        --stt        nhận diện phụ đề (Whisper/Gemini theo cấu hình) trước khi render
        --out DIR    thư mục xuất
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
    state.load_config()
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


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest_cli()
    if "--render" in sys.argv[1:]:
        return render_cli(sys.argv[1:])

    from app.config.paths import APP_NAME, ensure_dirs
    ensure_dirs()

    from app.core.logging_setup import setup_logging
    setup_logging()
    logger = logging.getLogger("main")
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
