"""Kiểu phụ đề dựng sẵn (bấm một lần) + vị trí / cỡ chữ nhanh cho thanh khung xem trước.

Mặc định theo phụ đề video viral TikTok: chữ đậm (Segoe UI Black — đủ dấu tiếng Việt), trắng viền đen dày + bóng nhẹ,
tối đa 2 dòng, đáy ở 76% chiều cao — trên vùng tên kênh / chú thích / nút của TikTok · Reels · Shorts. «Hộp nền che chữ
cũ» giống cách VideoLingo đốt phụ đề: hộp đen mờ sau chữ, che luôn chữ gốc nằm bên dưới."""

from __future__ import annotations

from app.core.state import SUB_BG_STYLES, SUB_BOLD_DEFAULT, SUB_FONT_DEFAULT, TEXT_EFFECTS, SubtitleConfig
from app.services.subtitle_style import box_height_px

_TIKTOK = {"sub_font": SUB_FONT_DEFAULT, "sub_bold": SUB_BOLD_DEFAULT, "sub_italic": False, "sub_uppercase": False,
           "sub_size": 66, "sub_color": "#FFFFFF", "sub_opacity": 100.0, "sub_stroke_width": 5.0,
           "sub_stroke_color": "#000000", "sub_shadow": 2.0, "sub_letter_spacing": 0.0, "text_effect": TEXT_EFFECTS[0],
           "sub_bg_enable": False, "bg_style": SUB_BG_STYLES[0], "karaoke": False}
#: tên kiểu → các trường kiểu chữ (không đụng vị trí khung)
STYLES: dict[str, dict] = {
    "TikTok trắng": dict(_TIKTOK),
    "TikTok vàng": {**_TIKTOK, "sub_color": "#FFE45C"},
    "Nổi chữ đang đọc": {**_TIKTOK, "karaoke": True, "karaoke_mode": "pop", "karaoke_color": "#FFE45C"},
    "Hộp nền che chữ cũ": {**_TIKTOK, "sub_size": 56, "sub_stroke_width": 0.0, "sub_shadow": 0.0, "sub_bg_enable": True,
                           "bg_style": SUB_BG_STYLES[1], "sub_bg_color": "#000000", "sub_bg_opacity": 72.0},
    "Phim (chữ nhỏ)": {**_TIKTOK, "sub_font": "Arial", "sub_bold": True, "sub_size": 46, "sub_stroke_width": 3.0,
                       "sub_shadow": 1.0},
}
CUSTOM = "Tuỳ chỉnh"
_MATCH = ("sub_font", "sub_bold", "sub_size", "sub_color", "sub_stroke_width", "sub_shadow", "bg_style", "karaoke")


def style_of(cfg: SubtitleConfig) -> str:
    """Tên kiểu dựng sẵn khớp cấu hình hiện tại, không khớp kiểu nào → «Tuỳ chỉnh»."""
    for name, st in STYLES.items():
        keys = (*_MATCH, "karaoke_mode") if st["karaoke"] else _MATCH
        if all(getattr(cfg, k) == st[k] for k in keys):
            return name
    return CUSTOM


def apply_style(cfg: SubtitleConfig, name: str) -> bool:
    st = STYLES.get(name)
    if st is None:
        return False
    for k, v in st.items():
        setattr(cfg, k, v)
    return True


# ── Vị trí nhanh ─────────────────────────────────────────────────────────
PLACES = ["bottom", "middle", "top", "hardsub", "off"]
PLACE_LABELS = {"bottom": "Dưới — chừa chỗ nút TikTok", "middle": "Giữa màn hình", "top": "Trên cùng",
                "hardsub": "Đè lên chữ cũ (tự dò, che mờ)", "off": "Ẩn phụ đề"}
PLACE_SHORT = {"bottom": "Dưới", "middle": "Giữa", "top": "Trên", "hardsub": "Đè chữ cũ", "off": "Ẩn"}
SAFE_BOTTOM = 0.76            # đáy khung «Dưới»: trên tên kênh / chú thích / thanh nút của TikTok · Reels · Shorts
SAFE_TOP = 0.10               # đỉnh khung «Trên»: dưới thanh tìm kiếm / tab của app


def place_of(cfg: SubtitleConfig) -> str:
    if not cfg.enable_subtitle:
        return "off"
    if cfg.follow_hardsub:
        return "hardsub"
    mid = cfg.box_y + cfg.box_h / 2
    return "top" if mid < 0.35 else "middle" if mid < 0.62 else "bottom"


def _height(cfg: SubtitleConfig, width: int, height: int) -> float:
    return min(0.9, box_height_px(cfg, width, height) / max(1, height))


def apply_place(cfg: SubtitleConfig, place: str, width: int, height: int) -> None:
    """Đặt khung phụ đề theo vị trí nhanh (``hardsub``: bám chỗ chữ cứng — người gọi bật «tự dò & che» ở Che mờ)."""
    cfg.enable_subtitle = place != "off"
    if place == "off":
        return
    cfg.use_box = True
    cfg.follow_hardsub = place == "hardsub"
    if place == "hardsub":
        return
    h = _height(cfg, width, height)
    cfg.box_x, cfg.box_w, cfg.box_h = 0.08, 0.84, round(h, 4)
    top = SAFE_BOTTOM - h if place == "bottom" else (0.5 - h / 2 if place == "middle" else SAFE_TOP)
    cfg.box_y = round(max(0.0, min(1.0 - h, top)), 4)


# ── Cỡ chữ nhanh ─────────────────────────────────────────────────────────
SIZES = {"S": 52, "M": 66, "L": 80, "XL": 96}
SIZE_LABELS = {"S": "Nhỏ", "M": "Vừa", "L": "Lớn", "XL": "Rất lớn"}


def size_of(cfg: SubtitleConfig) -> str:
    return min(SIZES, key=lambda k: abs(SIZES[k] - cfg.sub_size))


def refit(cfg: SubtitleConfig, width: int, height: int, size: int | None = None) -> None:
    """Đổi cỡ chữ (``size``) / kiểu chữ xong: khung cao lại vừa 2 dòng — giữ đỉnh (khung ở trên), giữ tâm (ở giữa)
    hoặc giữ đáy (ở dưới) cho chữ khỏi nhảy chỗ."""
    place = place_of(cfg)
    top, mid, bottom = cfg.box_y, cfg.box_y + cfg.box_h / 2, cfg.box_y + cfg.box_h
    if size is not None:
        cfg.sub_size = size
    h = _height(cfg, width, height)
    y = top if place == "top" else (mid - h / 2 if place == "middle" else bottom - h)
    cfg.box_y, cfg.box_h = round(max(0.0, min(1.0 - h, y)), 4), round(h, 4)


def apply_size(cfg: SubtitleConfig, key: str, width: int, height: int) -> None:
    if key in SIZES:
        refit(cfg, width, height, SIZES[key])
