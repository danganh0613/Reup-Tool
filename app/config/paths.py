"""Đường dẫn dùng chung của ứng dụng.

Windows / chạy mã nguồn: mọi thư mục làm việc nằm cạnh file chạy (main.py khi dev, .exe khi đóng gói)
để người dùng copy cả thư mục đi đâu cũng chạy được.

macOS bản đóng gói (.app): gói ứng dụng chỉ đọc (nằm trong /Applications, có chữ ký) → dữ liệu người dùng
(cài đặt, output, cache, model, log, preset, thư viện nhạc/sfx) ở ``~/Library/Application Support/Edit Studio``;
ffmpeg đi kèm nằm trong gói. Lần chạy đầu chép preset + assets mẫu từ gói ra thư mục dữ liệu.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Edit Studio"
APP_VERSION = "1.0.0"

FROZEN = bool(getattr(sys, "frozen", False))                 # PyInstaller
MAC_BUNDLE = FROZEN and sys.platform == "darwin"


def _root() -> Path:
    if MAC_BUNDLE:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _resources() -> Path:
    """Nơi chứa file đi kèm app (bin/, app/assets, presets mẫu)."""
    if MAC_BUNDLE:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return _root()


ROOT: Path = _root()
RESOURCES: Path = _resources()
BIN_DIR = RESOURCES / "bin"
ASSETS_DIR = ROOT / "app" / "assets"
OUTPUT_DIR = ROOT / "output"
VIDEOS_DIR = OUTPUT_DIR / "videos"
JOBS_DIR = OUTPUT_DIR / "jobs"
VOICE_DIR = OUTPUT_DIR / "voice"
SUBS_DIR = OUTPUT_DIR / "subs"
PRESETS_DIR = ROOT / "presets"
CACHE_DIR = ROOT / "cache"
TTS_CACHE_DIR = CACHE_DIR / "tts"
PREVIEW_CACHE_DIR = CACHE_DIR / "preview"
MODELS_DIR = ROOT / "models"
LOGS_DIR = ROOT / "logs"

CONFIG_FILE = OUTPUT_DIR / "last_config.json"     # trạng thái làm việc (autosave)
SETTINGS_FILE = ROOT / "settings.json"            # cài đặt máy (API key, ffmpeg, theme)

_ALL_DIRS = (
    BIN_DIR, OUTPUT_DIR, VIDEOS_DIR, JOBS_DIR, VOICE_DIR, SUBS_DIR,
    PRESETS_DIR, CACHE_DIR, TTS_CACHE_DIR, PREVIEW_CACHE_DIR, MODELS_DIR, LOGS_DIR,
)


def _seed(src: Path, dst: Path) -> None:
    """Chép file mẫu còn thiếu từ gói app ra thư mục dữ liệu (không ghi đè file người dùng đã sửa)."""
    if not src.is_dir():
        return
    for dirpath, _dirs, files in os.walk(src, followlinks=True):      # PyInstaller có thể đặt dữ liệu qua symlink
        for name in files:
            f = Path(dirpath) / name
            t = dst / f.relative_to(src)
            if not t.exists():
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, t)


def ensure_dirs() -> None:
    for d in _ALL_DIRS:
        if d == BIN_DIR and MAC_BUNDLE:
            continue                                   # bin/ nằm trong gói .app (chỉ đọc)
        d.mkdir(parents=True, exist_ok=True)
    if MAC_BUNDLE:
        _seed(RESOURCES / "presets", PRESETS_DIR)
        _seed(RESOURCES / "app" / "assets", ASSETS_DIR)
