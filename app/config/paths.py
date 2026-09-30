"""Đường dẫn dùng chung của ứng dụng.

Mọi thư mục làm việc nằm cạnh file chạy (main.py khi dev, .exe khi đóng gói)
để người dùng copy cả thư mục đi đâu cũng chạy được.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "Edit Studio"
APP_VERSION = "1.0.0"


def _root() -> Path:
    if getattr(sys, "frozen", False):          # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


ROOT: Path = _root()
BIN_DIR = ROOT / "bin"
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


def ensure_dirs() -> None:
    for d in _ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)
