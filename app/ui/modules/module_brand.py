"""Module Logo: tab Logo · Chữ · Watermark · Chuyển động (+ khung viền trang trí)."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from app.config import theme as T
from app.core.state import MOTIONS, POSITIONS, SAFE_ZONES, BrandConfig
from app.services.system_fonts import list_fonts
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.common import (ButtonRow, ColorRow, ComboRow, EntryRow, OptionRow, Section, SliderRow, StatusLabel,
                                   SwitchRow, hint, pack_rows)

POS_LABELS = ["Mặc định"] + POSITIONS


class BrandModule(BaseModule):
    key = "brand"
    title = "Logo"
    icon = "🏷"
    section = "brand"

    def build(self) -> None:
        cfg = self.state.brand
        s = self.setter
        rel = lambda _v: self.window.preview.schedule_refresh()  # noqa: E731
        hint(self, "Logo, chữ cố định và watermark xem trước trực tiếp & bake vào MP4.").pack(fill="x", pady=(0, 4))
        self.tab_var = tk.StringVar(value="Logo")
        ctk.CTkSegmentedButton(self, values=["Logo", "Chữ", "Watermark", "Chuyển động"], variable=self.tab_var,
                               command=self._show_tab, font=T.font(12)).pack(fill="x", pady=(0, 6))
        self.tabs: dict[str, ctk.CTkFrame] = {k: ctk.CTkFrame(self, fg_color="transparent") for k in
                                              ("Logo", "Chữ", "Watermark", "Chuyển động")}

        # ── Logo ─────────────────────────────────────────────────────────
        tb = self.tabs["Logo"]
        self.logo_enable = SwitchRow(tb, "Bật logo", on_change=s(cfg, "logo_enable"))
        self.logo_enable.pack(fill="x", pady=3)
        ButtonRow(tb, [("Chọn ảnh logo…", self._pick_logo, T.primary_button())]).pack(fill="x", pady=2)
        self.logo_status = StatusLabel(tb, "Chưa chọn logo.")
        self.logo_status.pack(fill="x")
        self.logo_size = SliderRow(tb, "Cỡ logo", from_=2, to=60, step=1, suffix="%",
                                   on_change=s(cfg, "logo_size", cast=float, preview=False), on_release=rel)
        self.logo_opacity = SliderRow(tb, "Độ đậm", from_=5, to=100, step=5, suffix="%",
                                      on_change=s(cfg, "logo_opacity", cast=float, preview=False), on_release=rel)
        self.logo_position = OptionRow(tb, "Vị trí", POS_LABELS, on_change=lambda v: self._pos("logo_position", v))
        self.logo_motion = OptionRow(tb, "Chuyển động", MOTIONS, on_change=s(cfg, "logo_motion"))
        self.logo_speed = SliderRow(tb, "Tốc độ chạy", from_=0, to=100, step=1, suffix="%",
                                    on_change=lambda v: self._speed("logo_motion_speed", v), on_release=rel)
        self.logo_delay = EntryRow(tb, "Trễ xuất hiện (s)", placeholder="0.00",
                                  on_change=lambda v: self._float("logo_delay", v))
        pack_rows(tb, [self.logo_size, self.logo_opacity, self.logo_position, self.logo_motion, self.logo_speed, self.logo_delay])

        # ── Chữ ──────────────────────────────────────────────────────────
        tb = self.tabs["Chữ"]
        self.text_enable = SwitchRow(tb, "Bật chữ cố định", on_change=s(cfg, "text_enable"))
        self.text = EntryRow(tb, "Nội dung chữ", placeholder="vd: Tập 12 · Phần 2", on_change=s(cfg, "text"))
        self.text_font = ComboRow(tb, "Phông chữ", ["Arial"], on_change=s(cfg, "text_font"))
        self.text_size = SliderRow(tb, "Cỡ chữ (px)", from_=12, to=160, step=1,
                                   on_change=s(cfg, "text_size", cast=int, preview=False), on_release=rel)
        self.text_color = ColorRow(tb, "Màu chữ", on_change=s(cfg, "text_color"))
        self.text_opacity = SliderRow(tb, "Độ đậm", from_=5, to=100, step=5, suffix="%",
                                      on_change=s(cfg, "text_opacity", cast=float, preview=False), on_release=rel)
        self.text_bg = SwitchRow(tb, "Nền mờ sau chữ", on_change=s(cfg, "text_bg"))
        self.text_position = OptionRow(tb, "Vị trí", POS_LABELS, on_change=lambda v: self._pos("text_position", v))
        self.text_motion = OptionRow(tb, "Chuyển động", MOTIONS, on_change=s(cfg, "text_motion"))
        self.text_speed = SliderRow(tb, "Tốc độ chạy", from_=0, to=100, step=1, suffix="%",
                                    on_change=lambda v: self._speed("text_motion_speed", v), on_release=rel)
        self.text_delay = EntryRow(tb, "Trễ xuất hiện (s)", placeholder="0.00", on_change=lambda v: self._float("text_delay", v))
        pack_rows(tb, [self.text_enable, self.text, self.text_font, self.text_size, self.text_color, self.text_opacity,
                       self.text_bg, self.text_position, self.text_motion, self.text_speed, self.text_delay])

        # ── Watermark ────────────────────────────────────────────────────
        tb = self.tabs["Watermark"]
        self.wm_enable = SwitchRow(tb, "Bật watermark", on_change=s(cfg, "watermark_enable"))
        self.wm_text = EntryRow(tb, "Nội dung watermark", placeholder="@kenh_cua_ban", on_change=s(cfg, "watermark_text"))
        self.wm_font = ComboRow(tb, "Phông chữ", ["Arial"], on_change=s(cfg, "watermark_font"))
        self.wm_size = SliderRow(tb, "Cỡ chữ (px)", from_=12, to=160, step=1,
                                 on_change=s(cfg, "watermark_size", cast=int, preview=False), on_release=rel)
        self.wm_color = ColorRow(tb, "Màu", on_change=s(cfg, "watermark_color"))
        self.wm_opacity = SliderRow(tb, "Độ đậm", from_=5, to=100, step=5, suffix="%",
                                    on_change=s(cfg, "watermark_opacity", cast=float, preview=False), on_release=rel)
        self.wm_position = OptionRow(tb, "Vị trí", POS_LABELS, on_change=lambda v: self._pos("watermark_position", v))
        self.wm_motion = OptionRow(tb, "Chuyển động", MOTIONS, on_change=s(cfg, "watermark_motion"))
        self.wm_speed = SliderRow(tb, "Tốc độ chạy", from_=0, to=100, step=1, suffix="%",
                                  on_change=lambda v: self._speed("watermark_motion_speed", v), on_release=rel)
        self.wm_delay = EntryRow(tb, "Trễ xuất hiện (s)", placeholder="0.00", on_change=lambda v: self._float("watermark_delay", v))
        pack_rows(tb, [self.wm_enable, self.wm_text, self.wm_font, self.wm_size, self.wm_color, self.wm_opacity,
                       self.wm_position, self.wm_motion, self.wm_speed, self.wm_delay])

        # ── Chuyển động ──────────────────────────────────────────────────
        tb = self.tabs["Chuyển động"]
        self.margin = SliderRow(tb, "Lề an toàn", from_=0, to=200, step=2, suffix=" px",
                                on_change=s(cfg, "safe_margin", cast=int, preview=False), on_release=rel)
        self.zone = OptionRow(tb, "Vùng chạy", SAFE_ZONES, on_change=s(cfg, "safe_zone"))
        pack_rows(tb, [self.margin, self.zone])
        hint(tb, "Nhanh / chậm chỉnh ở «Tốc độ chạy» trong từng thẻ · Lề an toàn: tránh sát viền.").pack(fill="x")
        sec = Section(tb, "Khung viền trang trí")
        sec.pack(fill="x", pady=(8, 4))
        self.frame_enable = SwitchRow(sec.body, "Bật khung viền", on_change=s(cfg, "frame_enable"))
        self.frame_width = SliderRow(sec.body, "Độ dày", from_=2, to=80, step=1, suffix=" px",
                                     on_change=s(cfg, "frame_width", cast=int, preview=False), on_release=rel)
        self.frame_color = ColorRow(sec.body, "Màu viền", on_change=s(cfg, "frame_color"))
        self.frame_opacity = SliderRow(sec.body, "Độ đậm", from_=5, to=100, step=5, suffix="%",
                                       on_change=s(cfg, "frame_opacity", cast=float, preview=False), on_release=rel)
        pack_rows(sec.body, [self.frame_enable, self.frame_width, self.frame_color, self.frame_opacity])
        ButtonRow(tb, [("Đặt lại tất cả (logo, chữ, watermark, viền)", self._reset, None)]).pack(fill="x", pady=(8, 0))
        self._show_tab("Logo")

    def _show_tab(self, name: str) -> None:
        for k, f in self.tabs.items():
            f.pack_forget()
        self.tabs[name].pack(fill="x")

    # ── State → widget ────────────────────────────────────────────────────
    def refresh(self) -> None:
        cfg = self.state.brand
        fonts = list_fonts(self.window)
        self._loading = True
        try:
            self.logo_enable.set(cfg.logo_enable)
            self.logo_status.info(f"Logo: {cfg.logo_path}" if cfg.logo_path else "Chưa chọn logo.")
            self.logo_size.set(cfg.logo_size)
            self.logo_opacity.set(cfg.logo_opacity)
            self.logo_position.set(cfg.logo_position if cfg.logo_position in POS_LABELS else "Mặc định")
            self.logo_motion.set(cfg.logo_motion)
            self.logo_speed.set(self._speed_pct(cfg.logo_motion_speed))
            self.logo_delay.set(f"{cfg.logo_delay:.2f}")
            self.text_enable.set(cfg.text_enable)
            self.text.set(cfg.text)
            self.text_font.set_values(fonts); self.text_font.set(cfg.text_font)
            self.text_size.set(cfg.text_size)
            self.text_color.set(cfg.text_color)
            self.text_opacity.set(cfg.text_opacity)
            self.text_bg.set(cfg.text_bg)
            self.text_position.set(cfg.text_position if cfg.text_position in POS_LABELS else "Mặc định")
            self.text_motion.set(cfg.text_motion)
            self.text_speed.set(self._speed_pct(cfg.text_motion_speed))
            self.text_delay.set(f"{cfg.text_delay:.2f}")
            self.wm_enable.set(cfg.watermark_enable)
            self.wm_text.set(cfg.watermark_text)
            self.wm_font.set_values(fonts); self.wm_font.set(cfg.watermark_font)
            self.wm_size.set(cfg.watermark_size)
            self.wm_color.set(cfg.watermark_color)
            self.wm_opacity.set(cfg.watermark_opacity)
            self.wm_position.set(cfg.watermark_position if cfg.watermark_position in POS_LABELS else "Mặc định")
            self.wm_motion.set(cfg.watermark_motion)
            self.wm_speed.set(self._speed_pct(cfg.watermark_motion_speed))
            self.wm_delay.set(f"{cfg.watermark_delay:.2f}")
            self.margin.set(cfg.safe_margin)
            self.zone.set(cfg.safe_zone)
            self.frame_enable.set(cfg.frame_enable)
            self.frame_width.set(cfg.frame_width)
            self.frame_color.set(cfg.frame_color)
            self.frame_opacity.set(cfg.frame_opacity)
        finally:
            self._loading = False

    @staticmethod
    def _speed_pct(px_per_s: float) -> float:
        return max(0.0, min(100.0, px_per_s / 3.0))

    def _speed(self, field: str, pct: float) -> None:
        self.set_value(self.state.brand, field, max(1.0, float(pct) * 3.0), preview=False)

    def _float(self, field: str, v: str) -> None:
        try:
            self.set_value(self.state.brand, field, max(0.0, float(v.replace(",", ".") or 0)))
        except ValueError:
            pass

    def _pos(self, field: str, v: str) -> None:
        defaults = {"logo_position": POSITIONS[2], "text_position": POSITIONS[1], "watermark_position": POSITIONS[4]}
        self.set_value(self.state.brand, field, defaults[field] if v == "Mặc định" else v)

    def _pick_logo(self) -> None:
        from tkinter import filedialog

        p = filedialog.askopenfilename(title="Chọn ảnh logo", filetypes=[("Ảnh", "*.png *.jpg *.jpeg *.webp *.bmp"), ("Tất cả", "*.*")])
        if p:
            self.state.brand.logo_path = p
            self.state.brand.logo_enable = True
            self.state.config_changed("brand")
            self.refresh()
            self.window.preview.schedule_refresh(0)

    def _reset(self) -> None:
        from tkinter import messagebox

        if not messagebox.askyesno("Edit Studio", "Đặt lại tất cả logo, chữ, watermark và khung viền về mặc định?"):
            return
        self.state.brand = BrandConfig()
        self.state.config_changed("brand")
        self.refresh()
        self.window.preview.schedule_refresh(0)
