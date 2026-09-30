"""Icon đơn sắc từ font Segoe Fluent Icons (Win11) / Segoe MDL2 Assets (Win10) — vẽ thành CTkImage.

Dùng ``icon("settings", 16, color)`` cho nút, ``ICON_FONT`` + ``glyph("video")`` khi vẽ thẳng lên Canvas.
Máy không có font (hiếm) → trả None, nơi gọi tự dùng ký hiệu chữ.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
_CANDIDATES = [("Segoe Fluent Icons", _FONT_DIR / "SegoeIcons.ttf"), ("Segoe MDL2 Assets", _FONT_DIR / "segmdl2.ttf")]

GLYPHS: dict[str, int] = {
    "settings": 0xE713, "keyboard": 0xE765, "bell": 0xEA8F, "folder": 0xE838, "music": 0xEC4F, "play": 0xE768,
    "stop": 0xE71A, "video": 0xE714, "subtitles": 0xED1E, "mic": 0xE720, "doc": 0xE8A5, "speaker": 0xE767,
    "blur": 0xE9A9, "font": 0xE8D2, "cut": 0xE8C6, "color": 0xE790, "book": 0xE82D, "photo": 0xEB9F,
    "aspect": 0xE799, "stopwatch": 0xE916, "zoomin": 0xE8A3, "zoomout": 0xE71F, "fit": 0xE73F, "undo": 0xE7A7,
    "redo": 0xE7A6, "eye": 0xE890, "hide": 0xED1A, "pointer": 0xE7C9, "sparkle": 0xE945, "check": 0xE73E,
    "chevron": 0xE70D, "add": 0xE710, "close": 0xE711, "trash": 0xE74D, "link": 0xE71B, "fullscreen": 0xE740,
    "wave": 0xE9D9, "resolution": 0xE9A6, "fps": 0xEC58, "grid": 0xE80A, "film": 0xE8B2, "sound": 0xE995,
    "story": 0xE736, "logo": 0xE91B, "crop": 0xE7A8, "clock": 0xE823, "star": 0xE734, "gpu": 0xE950,
    "skipback": 0xE892, "back10": 0xED3C, "fwd10": 0xED3D, "mute": 0xE74F, "export": 0xEDE1, "info": 0xE946,
    "save": 0xE74E, "download": 0xE896, "lock": 0xE72E, "headphone": 0xE7F6, "edit": 0xE70F, "tag": 0xE8EC,
    "bulb": 0xEA80, "warning": 0xE7BA, "refresh": 0xE72C, "openfolder": 0xE8DA, "list": 0xEA37, "translate": 0xE8C1,
    "eyedropper": 0xEF3C,
}

#: Emoji màu trong nhãn cũ → icon đơn sắc (giữ giao diện thống nhất, không dùng emoji làm icon)
EMOJI_ICONS: dict[str, str] = {
    "🎵": "music", "📚": "book", "📖": "book", "📁": "folder", "📂": "openfolder", "💾": "save", "🗑": "trash",
    "⬇": "download", "🎞": "film", "🔒": "lock", "🎤": "mic", "🎧": "headphone", "▶": "play", "✨": "sparkle",
    "🖼": "photo", "📝": "edit", "🚀": "sparkle", "🏷": "tag", "➕": "add", "＋": "add", "✕": "close", "✖": "close",
    "✓": "check", "✔": "check", "⚙": "settings", "🔔": "bell", "💡": "bulb", "⚠": "warning", "⚡": "sparkle",
    "🔄": "refresh", "↻": "refresh", "📋": "list", "🌐": "translate", "🎬": "video", "🎙": "mic", "📄": "doc",
    "🔊": "speaker", "♫": "music", "✂": "cut", "🔗": "link", "🧹": "trash", "⏹": "stop", "■": "stop",
}

#: icon cho từng module (rail + tiêu đề bảng điều khiển)
MODULE_ICONS = {"video": "video", "subtitle": "subtitles", "stt": "mic", "srt": "doc", "tts": "speaker", "blur": "blur",
                "bgm": "music", "brand": "logo", "trim": "cut", "color": "color", "story": "story"}


def split_icon(text: str) -> tuple[str, str | None]:
    """"💾 Lưu" → (" Lưu", "save"); chữ thường → (text, None)."""
    if not text:
        return text, None
    head = text[0]
    if head in EMOJI_ICONS and (len(text) == 1 or text[1] in " \u00a0"):
        return (" " + text[1:].strip()) if len(text) > 1 else "", EMOJI_ICONS[head]
    return text, None

ICON_FONT: str | None = next((name for name, p in _CANDIDATES if p.exists()), None)
_FONT_FILE: Path | None = next((p for _n, p in _CANDIDATES if p.exists()), None)


def glyph(name: str) -> str:
    """Ký tự icon để vẽ lên Canvas/Label với font ``ICON_FONT``."""
    return chr(GLYPHS[name]) if name in GLYPHS and ICON_FONT else ""


@lru_cache(maxsize=512)
def _render(name: str, size: int, color: str, dy: int = 0) -> Image.Image | None:
    """Glyph căn giữa ô ``size×size``; ``dy`` > 0 thêm ``2·dy`` dòng trong suốt phía trên → tâm icon hạ xuống ``dy`` px
    (khi đứng cạnh chữ: phần trên của chữ cao hơn phần dưới nên tâm nhìn của chữ thấp hơn tâm ô)."""
    if _FONT_FILE is None or name not in GLYPHS:
        return None
    scale = 4                                   # vẽ to rồi thu nhỏ cho nét mịn
    px = size * scale
    top = 2 * dy * scale
    font = ImageFont.truetype(str(_FONT_FILE), int(px * 0.92))
    img = Image.new("RGBA", (px, px + top), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ch = chr(GLYPHS[name])
    l, t, r, b = font.getbbox(ch)
    d.text(((px - (r - l)) / 2 - l, top + (px - (b - t)) / 2 - t), ch, font=font, fill=color)
    return img.resize((size, size + 2 * dy), Image.LANCZOS)


def glyph_image(name: str, size: int, color: str = "#F7F7F8") -> Image.Image | None:
    """Ảnh PIL RGBA ``size×size`` của icon (để ghép vào ảnh khác, vd. ô icon trên canvas)."""
    return _render(name, size, color)


def icon(name: str, size: int = 16, color: str = "#F7F7F8", dy: int = 0) -> ctk.CTkImage | None:
    """``dy``: hạ icon xuống ``dy`` px logic — dùng cho icon đứng trước chữ (nút, ô chọn, tiêu đề)."""
    img = _render(name, size, color, dy)
    if img is None:
        return None
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size + 2 * dy))
