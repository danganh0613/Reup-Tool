"""Bảng màu và font dùng chung cho toàn giao diện.

Có 5 theme trọn gói (THEMES). ``apply_theme(tên)`` chốt các biến module-level (BG, ACCENT, TEXT…); widget đọc màu lúc
tạo, nên đổi theme = apply_theme rồi dựng lại giao diện (MainWindow.rebuild_ui) — không cần mở lại app.
"""

from __future__ import annotations

import customtkinter as ctk

import os
import sys
from functools import lru_cache
from pathlib import Path


_MAC = sys.platform == "darwin"


def _pick_ui_font() -> str:
    """Segoe UI Variable (Win11) gần với Inter của các app AI hiện đại; máy cũ dùng Segoe UI.
    macOS: font hệ thống (SF Pro) qua tên đặc biệt của Tk."""
    if _MAC:
        return ".AppleSystemUIFont"
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if (fonts_dir / "SegUIVar.ttf").exists():
        return "Segoe UI Variable Text"
    return "Segoe UI"


UI_FONT = _pick_ui_font()
if _MAC:
    UI_FONT_MEDIUM = UI_FONT                    # Tk trên macOS không tách họ font «Semibold» riêng
    MONO_FONT = "Menlo"
    EMOJI_FONT = "Apple Color Emoji"
else:
    UI_FONT_MEDIUM = "Segoe UI Variable Text Semibold" if UI_FONT.startswith("Segoe UI Variable") else "Segoe UI Semibold"
    MONO_FONT = "Consolas"
    EMOJI_FONT = "Segoe UI Emoji"

# ── Bộ theme trọn gói ────────────────────────────────────────────────────
# Mỗi theme là một bộ màu đồng bộ (nền · panel · thẻ · chữ · màu nhấn). Ít mà chất: chọn một là xong.
THEMES: dict[str, dict[str, str]] = {
    "studio": {
        "name": "Studio", "desc": "Gần đen, nhấn lime — như các app AI hiện đại (mặc định)",
        "bg": "#0F1113", "panel": "#131517", "card": "#1C1E20", "input": "#0B0C0E", "elevated": "#26282B",
        "hover": "#303236", "border": "#2A2C30", "border_soft": "#202226",
        "text": "#F7F7F8", "dim": "#A6A8AC", "muted": "#7C7E82",
        "accent": "#D1FE17", "accent_hover": "#E4FF5A", "accent_soft": "#2B3410", "on_accent": "#151515",
    },
    "midnight": {
        "name": "Midnight", "desc": "Xanh đêm sâu, nhấn xanh điện — dịu mắt khi làm lâu",
        "bg": "#0B0F1A", "panel": "#111726", "card": "#171F33", "input": "#080B14", "elevated": "#222B47",
        "hover": "#2C3658", "border": "#27314E", "border_soft": "#1C2440",
        "text": "#EEF2FF", "dim": "#A9B4D0", "muted": "#6F7A99",
        "accent": "#5B8CFF", "accent_hover": "#7BA3FF", "accent_soft": "#1B2A55", "on_accent": "#0A0F1F",
    },
    "graphite": {
        "name": "Graphite", "desc": "Xám than trung tính, nhấn hổ phách — kiểu studio chuyên nghiệp",
        "bg": "#141517", "panel": "#1A1B1E", "card": "#232529", "input": "#0F1012", "elevated": "#2C2E33",
        "hover": "#36393F", "border": "#33363C", "border_soft": "#26282D",
        "text": "#F2F2F2", "dim": "#A8AAAE", "muted": "#7A7C80",
        "accent": "#F5A524", "accent_hover": "#FFB94D", "accent_soft": "#3A2B10", "on_accent": "#1A1200",
    },
    "orchid": {
        "name": "Orchid", "desc": "Tím than, nhấn hồng tím — phong cách creator",
        "bg": "#120F17", "panel": "#17131F", "card": "#211B2C", "input": "#0C0A10", "elevated": "#2B2438",
        "hover": "#372E47", "border": "#332B42", "border_soft": "#261F33",
        "text": "#F6F2FA", "dim": "#B3A9C4", "muted": "#7E7490",
        "accent": "#E879F9", "accent_hover": "#F0A3FF", "accent_soft": "#3A1F45", "on_accent": "#1A0B22",
    },
    "oled": {
        "name": "OLED", "desc": "Đen tuyền cho màn OLED, nhấn lục ngọc — tiết kiệm pin, tương phản cao",
        "bg": "#000000", "panel": "#0A0A0A", "card": "#141414", "input": "#050505", "elevated": "#1E1E1E",
        "hover": "#2A2A2A", "border": "#262626", "border_soft": "#1A1A1A",
        "text": "#F5F5F5", "dim": "#A3A3A3", "muted": "#737373",
        "accent": "#2DD4BF", "accent_hover": "#5EEAD4", "accent_soft": "#0F2F2A", "on_accent": "#062420",
    },
}
DEFAULT_THEME = "studio"

# Tương thích cấu hình cũ (mode + màu nhấn) → theme
_LEGACY_ACCENT = {"lime": "studio", "cyan": "midnight", "blue": "midnight", "purple": "orchid", "rose": "orchid",
                  "amber": "graphite", "emerald": "oled"}

# Các biến module-level được gán trong ``apply_theme`` — import module rồi đọc ``theme.ACCENT`` v.v. sau khi apply.
THEME_NAME = DEFAULT_THEME
_t = THEMES[DEFAULT_THEME]
ACCENT, ACCENT_HOVER, ACCENT_SOFT, ON_ACCENT = _t["accent"], _t["accent_hover"], _t["accent_soft"], _t["on_accent"]
BG, BG_PANEL, BG_CARD, BG_INPUT = _t["bg"], _t["panel"], _t["card"], _t["input"]
BG_ELEVATED, BG_HOVER, BORDER, BORDER_SOFT = _t["elevated"], _t["hover"], _t["border"], _t["border_soft"]
TEXT, TEXT_DIM, TEXT_MUTED = _t["text"], _t["dim"], _t["muted"]

RADIUS = 10          # bo góc chuẩn cho nút/chip
RADIUS_CARD = 14     # bo góc thẻ/panel

SUCCESS = "#22C55E"
SUCCESS_HOVER = "#16A34A"
DANGER = "#EF4444"
DANGER_HOVER = "#DC2626"
DANGER_SOFT = "#4A1D1D"
WARNING = "#F59E0B"
INFO = "#38BDF8"


def resolve_theme(name: str, accent: str = "") -> str:
    """Tên theme hợp lệ từ cấu hình mới (``theme``) hoặc cũ (``appearance_mode`` + ``accent``)."""
    if name in THEMES:
        return name
    if name == "oled":
        return "oled"
    return _LEGACY_ACCENT.get(accent, DEFAULT_THEME)


def apply_theme(name: str = DEFAULT_THEME, accent: str = "") -> None:
    """Chốt bảng màu và cấu hình CustomTkinter. Gọi trước khi dựng UI (và lại trước khi dựng lại UI khi đổi theme)."""
    global THEME_NAME, ACCENT, ACCENT_HOVER, ACCENT_SOFT, ON_ACCENT, TEXT, TEXT_DIM, TEXT_MUTED
    global BG, BG_PANEL, BG_CARD, BG_INPUT, BG_ELEVATED, BG_HOVER, BORDER, BORDER_SOFT

    THEME_NAME = resolve_theme(name, accent)
    t_ = THEMES[THEME_NAME]
    BG, BG_PANEL, BG_CARD, BG_INPUT = t_["bg"], t_["panel"], t_["card"], t_["input"]
    BG_ELEVATED, BG_HOVER, BORDER, BORDER_SOFT = t_["elevated"], t_["hover"], t_["border"], t_["border_soft"]
    TEXT, TEXT_DIM, TEXT_MUTED = t_["text"], t_["dim"], t_["muted"]
    ACCENT, ACCENT_HOVER, ACCENT_SOFT, ON_ACCENT = t_["accent"], t_["accent_hover"], t_["accent_soft"], t_["on_accent"]

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    t = ctk.ThemeManager.theme
    if isinstance(t.get("CTkFont"), dict):
        t["CTkFont"]["family"] = UI_FONT
    # Đặt màu mặc định để widget nào không khai báo màu vẫn hợp tông.
    for w in ("CTkButton", "CTkOptionMenu", "CTkComboBox", "CTkSlider", "CTkSwitch",
              "CTkCheckBox", "CTkSegmentedButton", "CTkProgressBar"):
        e = t.get(w)
        if not isinstance(e, dict):
            continue
        if w == "CTkButton":
            e["fg_color"] = [ACCENT, ACCENT]
            e["hover_color"] = [ACCENT_HOVER, ACCENT_HOVER]
            e["text_color"] = [ON_ACCENT, ON_ACCENT]
        elif w in ("CTkOptionMenu", "CTkComboBox"):
            e["fg_color"] = [BG_INPUT, BG_INPUT]
            e["button_color"] = [BG_ELEVATED, BG_ELEVATED]
            e["button_hover_color"] = [BG_HOVER, BG_HOVER]
            e["text_color"] = [TEXT, TEXT]
            if w == "CTkComboBox":
                e["border_color"] = [BORDER, BORDER]
        elif w == "CTkSlider":
            e["progress_color"] = [ACCENT, ACCENT]
            e["button_color"] = [ACCENT, ACCENT]
            e["button_hover_color"] = [ACCENT_HOVER, ACCENT_HOVER]
            e["fg_color"] = [BG_ELEVATED, BG_ELEVATED]
        elif w == "CTkSwitch":
            e["progress_color"] = [ACCENT, ACCENT]
            e["fg_color"] = [BG_ELEVATED, BG_ELEVATED]
            e["button_color"] = ["#E5E7EB", "#E5E7EB"]
            e["text_color"] = [TEXT, TEXT]
        elif w == "CTkCheckBox":
            e["fg_color"] = [ACCENT, ACCENT]
            e["hover_color"] = [ACCENT_HOVER, ACCENT_HOVER]
            e["text_color"] = [TEXT, TEXT]
            e["checkmark_color"] = [ON_ACCENT, ON_ACCENT]
        elif w == "CTkSegmentedButton":
            e["selected_color"] = [ACCENT, ACCENT]
            e["selected_hover_color"] = [ACCENT_HOVER, ACCENT_HOVER]
            e["unselected_color"] = [BG_ELEVATED, BG_ELEVATED]
            e["unselected_hover_color"] = [BG_HOVER, BG_HOVER]
            e["text_color"] = [TEXT, TEXT]
        elif w == "CTkProgressBar":
            e["progress_color"] = [ACCENT, ACCENT]
            e["fg_color"] = [BG_ELEVATED, BG_ELEVATED]
    for w in ("CTkLabel", "CTkEntry", "CTkTextbox"):
        e = t.get(w)
        if isinstance(e, dict) and "text_color" in e:
            e["text_color"] = [TEXT, TEXT]
    if isinstance(t.get("CTkEntry"), dict):
        t["CTkEntry"]["fg_color"] = [BG_INPUT, BG_INPUT]
        t["CTkEntry"]["border_color"] = [BORDER, BORDER]
        t["CTkEntry"]["placeholder_text_color"] = [TEXT_MUTED, TEXT_MUTED]
    if isinstance(t.get("CTkTextbox"), dict):
        t["CTkTextbox"]["fg_color"] = [BG_INPUT, BG_INPUT]
        t["CTkTextbox"]["border_color"] = [BORDER, BORDER]
    if isinstance(t.get("CTkFrame"), dict):
        t["CTkFrame"]["fg_color"] = [BG_PANEL, BG_PANEL]
        t["CTkFrame"]["top_fg_color"] = [BG_CARD, BG_CARD]
        t["CTkFrame"]["border_color"] = [BORDER, BORDER]
    if isinstance(t.get("CTkScrollableFrame"), dict):
        t["CTkScrollableFrame"]["label_fg_color"] = [BG_CARD, BG_CARD]
    if isinstance(t.get("CTkScrollbar"), dict):
        t["CTkScrollbar"]["button_color"] = [BG_HOVER, BG_HOVER]
        t["CTkScrollbar"]["button_hover_color"] = [ACCENT, ACCENT]
    if isinstance(t.get("CTk"), dict):
        t["CTk"]["fg_color"] = [BG, BG]
    if isinstance(t.get("CTkToplevel"), dict):
        t["CTkToplevel"]["fg_color"] = [BG, BG]


# ── Font helper ───────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def font(size: int = 13, weight: str = "normal", family: str | None = None) -> ctk.CTkFont:
    """weight: normal · medium (semibold face, dùng cho nút/nhãn nổi) · bold (tiêu đề).

    Dùng chung một CTkFont cho mỗi (cỡ, độ đậm, họ) — mỗi CTkFont là một named font của Tk; tạo mới mỗi lần gọi
    thì sau vài lần mở cửa sổ / đổi theme có hàng nghìn font. Không ``configure`` font trả về (sẽ đổi mọi nơi dùng).
    """
    if weight == "medium":
        return ctk.CTkFont(family=family or UI_FONT_MEDIUM, size=size, weight="normal")
    return ctk.CTkFont(family=family or UI_FONT, size=size, weight=weight)


@lru_cache(maxsize=None)
def mono(size: int = 12) -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO_FONT, size=size)


# ── Bộ kwargs dùng lại cho nút ────────────────────────────────────────────

# Kiểu nút theo shadcn: chữ regular 13 (không đậm), bo góc 10, không viền (trừ outline)
def primary_button() -> dict:
    return {"fg_color": ACCENT, "hover_color": ACCENT_HOVER, "text_color": ON_ACCENT,
            "font": font(13, "medium"), "corner_radius": RADIUS, "border_width": 0}


def secondary_button() -> dict:
    return {"fg_color": BG_ELEVATED, "hover_color": BG_HOVER, "text_color": TEXT,
            "border_width": 0, "font": font(13), "corner_radius": RADIUS}


def outline_button() -> dict:
    return {"fg_color": "transparent", "hover_color": BG_HOVER, "text_color": TEXT,
            "border_width": 1, "border_color": BORDER, "font": font(13), "corner_radius": RADIUS}


def ghost_button() -> dict:
    """Nút trong suốt (thanh công cụ, menu)."""
    return {"fg_color": "transparent", "hover_color": BG_HOVER, "text_color": TEXT,
            "border_width": 0, "font": font(13), "corner_radius": RADIUS}


def danger_button() -> dict:
    return {"fg_color": DANGER, "hover_color": DANGER_HOVER, "text_color": "#FFFFFF",
            "font": font(13), "corner_radius": RADIUS, "border_width": 0}


def success_button() -> dict:
    return {"fg_color": SUCCESS, "hover_color": SUCCESS_HOVER, "text_color": "#0B1220",
            "font": font(13), "corner_radius": RADIUS, "border_width": 0}
