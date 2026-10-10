"""Khác biệt giữa Windows và macOS gom một chỗ: mở file/thư mục, cờ ẩn cửa sổ console, phát WAV.

Code còn lại gọi các hàm ở đây thay vì ``os.startfile`` / ``winsound`` (chỉ có trên Windows).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

#: truyền vào ``creationflags`` của subprocess: Windows không bật cửa sổ console đen; macOS/Linux = 0
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0

#: nhãn phím tắt hiển thị: Ctrl (Windows) · ⌘ (macOS)
MOD_LABEL = "⌘" if IS_MAC else "Ctrl"


def exe_name(name: str) -> str:
    """"ffmpeg" → "ffmpeg.exe" trên Windows, giữ nguyên trên macOS/Linux."""
    if IS_WIN and not name.lower().endswith(".exe"):
        return f"{name}.exe"
    return name


def open_path(path: str | Path) -> None:
    """Mở file/thư mục bằng ứng dụng mặc định (Explorer / Finder)."""
    p = str(path)
    try:
        if IS_WIN:
            os.startfile(p)          # type: ignore[attr-defined]
        elif IS_MAC:
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception:
        logger.warning("Không mở được %s", p, exc_info=True)


def reveal_path(path: str | Path) -> None:
    """Mở thư mục chứa file và chọn sẵn file đó."""
    p = Path(path)
    try:
        if IS_WIN:
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif IS_MAC:
            subprocess.Popen(["open", "-R", str(p)])
        else:
            open_path(p.parent)
    except Exception:
        open_path(p.parent if p.is_file() else p)


class WavPlayer:
    """Phát một file WAV không chặn luồng gọi; ``stop()`` dừng ngay.

    Windows: ``winsound`` (có sẵn). macOS: lệnh ``afplay`` (có sẵn). Linux: ``paplay``/``aplay`` nếu có.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        if IS_WIN or IS_MAC:
            return True
        import shutil

        return bool(shutil.which("paplay") or shutil.which("aplay"))

    def play(self, wav: str | Path) -> None:
        self.stop()
        if IS_WIN:
            import winsound

            winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        if IS_MAC:
            cmd = ["afplay", str(wav)]
        else:
            import shutil

            tool = shutil.which("paplay") or shutil.which("aplay")
            if not tool:
                return
            cmd = [tool, str(wav)]
        with self._lock:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if IS_WIN:
            import winsound

            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
            return
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass


def beep() -> None:
    """Tiếng «ding» báo xong việc."""
    try:
        if IS_WIN:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif IS_MAC:
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
