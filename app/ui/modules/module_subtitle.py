"""Module Phụ đề: kiểu chữ, hiệu ứng nền & karaoke, khung phụ đề (kéo trên xem trước hoặc nhập pixel)."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from app.config import theme as T
from app.core.state import SRT_MODES, SUB_BG_STYLES, TEXT_EFFECTS
from app.services import render_pipeline as rp
from app.services.ffmpeg import MediaInfo
from app.services.system_fonts import list_fonts
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.common import (ButtonRow, ColorRow, ComboRow, OptionRow, Section, SliderRow, SwitchRow, hint,
                                   pack_rows)

QUICK_COLORS = ["#FFFFFF", "#FFD400", "#22D3EE", "#EF4444", "#22C55E", "#F472B6"]


class SubtitleModule(BaseModule):
    key = "subtitle"
    title = "Phụ đề"
    icon = "📝"
    section = "subtitle"

    def build(self) -> None:
        cfg = self.state.subtitle
        s = self.setter
        rel = lambda _v: self.window.preview.schedule_refresh()  # noqa: E731

        self.enable = SwitchRow(self, "Hiện phụ đề SRT", on_change=self._enable_changed)
        self.enable.pack(fill="x", pady=(0, 2))
        hint(self, "Tắt = ẩn khỏi xem trước và không đốt chữ khi xuất. Kéo khung cyan trên vùng xem trước để đặt vị trí "
                   "(công cụ T).").pack(fill="x", pady=(0, 6))

        sec = Section(self, "Kiểu chữ")
        sec.pack(fill="x", pady=4)
        self.font = ComboRow(sec.body, "Phông chữ", ["Arial"], on_change=s(cfg, "sub_font"))
        self.size = SliderRow(sec.body, "Cỡ chữ", from_=12, to=120, step=1, suffix="px",
                              on_change=s(cfg, "sub_size", cast=int, preview=False), on_release=rel)
        self.opacity = SliderRow(sec.body, "Độ đục", from_=10, to=100, step=5, suffix="%",
                                 on_change=s(cfg, "sub_opacity", cast=float, preview=False), on_release=rel)
        self.color = ColorRow(sec.body, "Màu chữ", on_change=s(cfg, "sub_color"))
        swatches = ctk.CTkFrame(sec.body, fg_color="transparent")
        ctk.CTkLabel(swatches, text="", width=120).pack(side="left")
        for c in QUICK_COLORS:
            ctk.CTkButton(swatches, text="", width=26, height=22, fg_color=c, hover_color=c, border_width=1,
                          border_color=T.BORDER, command=lambda col=c: self._quick_color(col)).pack(side="left", padx=2)
        self.spacing = SliderRow(sec.body, "Dãn cách chữ", from_=-2, to=10, step=0.5, fmt="{:.1f}", suffix="px",
                                 on_change=s(cfg, "sub_letter_spacing", cast=float, preview=False), on_release=rel)
        self.effect = OptionRow(sec.body, "Hiệu ứng chữ", TEXT_EFFECTS, on_change=s(cfg, "text_effect"))
        self.stroke = SliderRow(sec.body, "Độ dày stroke", from_=0, to=10, step=0.5, fmt="{:.1f}", suffix="px",
                                on_change=s(cfg, "sub_stroke_width", cast=float, preview=False), on_release=rel)
        self.stroke_color = ColorRow(sec.body, "Màu viền", value="#000000", on_change=s(cfg, "sub_stroke_color"))
        self.shadow = SliderRow(sec.body, "Bóng đổ", from_=0, to=10, step=0.5, fmt="{:.1f}", suffix="px",
                                on_change=s(cfg, "sub_shadow", cast=float, preview=False), on_release=rel)
        flags = ctk.CTkFrame(sec.body, fg_color="transparent")
        self.bold = SwitchRow(flags, "Đậm", on_change=s(cfg, "sub_bold"))
        self.italic = SwitchRow(flags, "Nghiêng", on_change=s(cfg, "sub_italic"))
        self.upper = SwitchRow(flags, "IN HOA", on_change=s(cfg, "sub_uppercase"))
        self.bold.pack(side="left", padx=(0, 14))
        self.italic.pack(side="left", padx=(0, 14))
        self.upper.pack(side="left")
        pack_rows(sec.body, [self.font, self.size, self.opacity, self.color, swatches, self.spacing, self.effect,
                             self.stroke, self.stroke_color, self.shadow, flags])

        sec = Section(self, "Hiệu ứng nền & karaoke")
        sec.pack(fill="x", pady=4)
        self.bg_enable = SwitchRow(sec.body, "Bật hiệu ứng nền", on_change=s(cfg, "sub_bg_enable"))
        self.karaoke = SwitchRow(sec.body, "Phụ đề Karaoke / Highlight từng từ", on_change=s(cfg, "karaoke"))
        self.bg_style = OptionRow(sec.body, "Kiểu nền", SUB_BG_STYLES, on_change=s(cfg, "bg_style"))
        self.bg_color = ColorRow(sec.body, "Màu nền", value="#000000", on_change=s(cfg, "sub_bg_color"))
        self.bg_opacity = SliderRow(sec.body, "Độ đậm nền", from_=0, to=100, step=5, suffix="%",
                                    on_change=s(cfg, "sub_bg_opacity", cast=float, preview=False), on_release=rel)
        self.karaoke_color = ColorRow(sec.body, "Màu highlight", value="#FFD400", on_change=s(cfg, "karaoke_color"))
        pack_rows(sec.body, [self.bg_enable, self.karaoke, self.bg_style, self.bg_color, self.bg_opacity, self.karaoke_color])
        hint(sec.body, "Karaoke chia thời gian cue cho từng từ theo độ dài — từ đang đọc đổi sang màu highlight.").pack(fill="x")

        sec = Section(self, "Khung phụ đề")
        sec.pack(fill="x", pady=4)
        grid = ctk.CTkFrame(sec.body, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((1, 3), weight=1)
        self.px_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate((("x", "X"), ("y", "Y"), ("w", "Rộng"), ("h", "Cao"))):
            r, c = divmod(i, 2)
            ctk.CTkLabel(grid, text=label, font=T.font(12), text_color=T.TEXT_DIM, width=44, anchor="w").grid(
                row=r, column=c * 2, sticky="w", pady=3)
            var = tk.StringVar(value="0")
            e = ctk.CTkEntry(grid, textvariable=var, height=32, font=T.mono(12), justify="right")
            e.grid(row=r, column=c * 2 + 1, sticky="ew", padx=(0, 10), pady=3)
            e.bind("<Return>", lambda _e: self._apply_pixels())
            e.bind("<FocusOut>", lambda _e: self._apply_pixels())
            self.px_vars[key] = var
        ButtonRow(sec.body, [
            ("Căn ngang", lambda: self._preset_box("center_h"), T.primary_button()),
            ("Chạm 2 mép", lambda: self._preset_box("full_w"), T.primary_button()),
            ("Vừa chữ", lambda: self._preset_box("fit"), T.primary_button()),
            ("Căn dọc", lambda: self._preset_box("center_v"), None),
        ]).pack(fill="x", pady=(6, 2))
        self.use_box = SwitchRow(sec.body, "Dùng khung (tắt = căn theo vị trí đơn giản)", on_change=s(cfg, "use_box"))
        self.use_box.pack(fill="x", pady=(4, 0))
        hint(sec.body, "Đơn vị pixel theo khung hình xuất. Chữ tự xuống dòng theo bề rộng khung này — khung càng rộng "
                       "thì càng ít dòng.").pack(fill="x", pady=(2, 0))

        sec = Section(self, "Hiển thị")
        sec.pack(fill="x", pady=4)
        self.mode = OptionRow(sec.body, "Ghi lên video", SRT_MODES, on_change=s(cfg, "srt_mode"))
        self.delay = SliderRow(sec.body, "Trễ phụ đề", from_=-5, to=5, step=0.05, fmt="{:+.2f}", suffix="s",
                               on_change=s(cfg, "sub_delay", cast=float, preview=False), on_release=rel)
        self.sec_scale = SliderRow(sec.body, "Cỡ dòng phụ", from_=40, to=100, step=5, suffix="%",
                                   on_change=lambda v: self.set_value(cfg, "secondary_scale", v / 100, preview=False),
                                   on_release=rel)
        pack_rows(sec.body, [self.mode, self.delay, self.sec_scale])
        hint(sec.body, "Nhận diện / chọn / dịch phụ đề ở tab STT và SRT.").pack(fill="x")

    # ── State → widget ────────────────────────────────────────────────────
    def refresh(self) -> None:
        cfg = self.state.subtitle
        self._loading = True
        try:
            self.enable.set(cfg.enable_subtitle)
            fonts = list_fonts(self.window)
            self.font.set_values(fonts)
            self.font.set(cfg.sub_font)
            self.size.set(cfg.sub_size)
            self.opacity.set(cfg.sub_opacity)
            self.color.set(cfg.sub_color)
            self.spacing.set(cfg.sub_letter_spacing)
            self.effect.set(cfg.text_effect if cfg.text_effect in TEXT_EFFECTS else TEXT_EFFECTS[0])
            self.stroke.set(cfg.sub_stroke_width)
            self.stroke_color.set(cfg.sub_stroke_color)
            self.shadow.set(cfg.sub_shadow)
            self.bold.set(cfg.sub_bold)
            self.italic.set(cfg.sub_italic)
            self.upper.set(cfg.sub_uppercase)
            self.bg_enable.set(cfg.sub_bg_enable)
            self.karaoke.set(cfg.karaoke)
            self.bg_style.set(cfg.bg_style if cfg.bg_style in SUB_BG_STYLES else SUB_BG_STYLES[0])
            self.bg_color.set(cfg.sub_bg_color)
            self.bg_opacity.set(cfg.sub_bg_opacity)
            self.karaoke_color.set(cfg.karaoke_color)
            self.use_box.set(cfg.use_box)
            self.mode.set(cfg.srt_mode)
            self.delay.set(cfg.sub_delay)
            self.sec_scale.set(cfg.secondary_scale * 100)
            self._fill_pixels()
        finally:
            self._loading = False

    def on_active_changed(self) -> None:
        self._fill_pixels()

    def _enable_changed(self, v: bool) -> None:
        self.set_value(self.state.subtitle, "enable_subtitle", bool(v))

    def _quick_color(self, col: str) -> None:
        self.color.set(col)
        self.set_value(self.state.subtitle, "sub_color", col)

    # ── Khung theo pixel ─────────────────────────────────────────────────
    def _frame_size(self) -> tuple[int, int]:
        it = self.state.active
        if it is None or not it.width:
            return 1920, 1080
        geo = rp.output_geometry(MediaInfo(duration=it.duration, width=it.width, height=it.height, fps=it.fps,
                                           has_audio=it.has_audio), self.state.video)
        return geo.width, geo.height

    def _fill_pixels(self) -> None:
        cfg = self.state.subtitle
        W, H = self._frame_size()
        vals = {"x": cfg.box_x * W, "y": cfg.box_y * H, "w": cfg.box_w * W, "h": cfg.box_h * H}
        for k, v in vals.items():
            self.px_vars[k].set(str(int(round(v))))

    def _apply_pixels(self) -> None:
        if self._loading:
            return
        cfg = self.state.subtitle
        W, H = self._frame_size()
        try:
            x = float(self.px_vars["x"].get()); y = float(self.px_vars["y"].get())
            w = float(self.px_vars["w"].get()); h = float(self.px_vars["h"].get())
        except ValueError:
            self._fill_pixels()
            return
        nx, ny = max(0.0, min(0.99, x / W)), max(0.0, min(0.99, y / H))
        nw, nh = max(0.02, min(1.0 - nx, w / W)), max(0.02, min(1.0 - ny, h / H))
        if (nx, ny, nw, nh) != (cfg.box_x, cfg.box_y, cfg.box_w, cfg.box_h):
            cfg.box_x, cfg.box_y, cfg.box_w, cfg.box_h = round(nx, 4), round(ny, 4), round(nw, 4), round(nh, 4)
            cfg.use_box = True
            self.state.config_changed("subtitle")
            self.window.preview.schedule_refresh(0)
        self._fill_pixels()

    def _preset_box(self, kind: str) -> None:
        cfg = self.state.subtitle
        W, H = self._frame_size()
        if kind == "center_h":
            cfg.box_x = round((1.0 - cfg.box_w) / 2, 4)
        elif kind == "full_w":
            cfg.box_x, cfg.box_w = 0.0, 1.0
        elif kind == "fit":
            # cao vừa 2 dòng chữ theo cỡ hiện tại
            scale = min(W, H) / 1080.0
            line_px = cfg.sub_size * scale * 1.35
            h = min(0.6, (line_px * 2 + 12 * scale) / H)
            bottom = cfg.box_y + cfg.box_h
            cfg.box_h = round(h, 4)
            cfg.box_y = round(max(0.0, bottom - h), 4)
        elif kind == "center_v":
            cfg.box_y = round((1.0 - cfg.box_h) / 2, 4)
        cfg.use_box = True
        self.state.config_changed("subtitle")
        self._fill_pixels()
        self.window.preview.schedule_refresh(0)
