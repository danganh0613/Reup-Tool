"""Module Hiệu ứng: bộ lọc & chỉnh màu (preset), xoay, khử rung, hạt phim, vignette, Ken Burns, lớp phủ texture."""

from __future__ import annotations

import os
from tkinter import filedialog

import customtkinter as ctk

from app.config import theme as T
from app.config.paths import ASSETS_DIR
from app.core.state import COLOR_PRESETS, KENBURNS, TEXTURE_BLENDS, ColorConfig
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.common import ButtonRow, EntryRow, OptionRow, Section, SliderRow, SwitchRow, hint, pack_rows

ROTATIONS = ["0°", "90° (xoay phải)", "180°", "270° (xoay trái)", "Tuỳ chỉnh"]
TEXTURE_LIB = ASSETS_DIR / "textures"


class ColorModule(BaseModule):
    key = "color"
    title = "Hiệu ứng"
    icon = "✨"
    section = "color"

    def build(self) -> None:
        cfg = self.state.color
        s = self.setter
        rel = lambda _v: self.window.preview.schedule_refresh()  # noqa: E731
        hint(self, "Chống trùng khi đăng lại video của bạn trên nhiều kênh, và tinh chỉnh màu sắc, góc quay — "
                   "xem trước trực tiếp và bake khi xuất MP4.").pack(fill="x", pady=(0, 4))
        ButtonRow(self, [("Reset toàn bộ", self._reset, None), ("Bật nhanh chống trùng", self._quick_dedup, T.primary_button())]).pack(fill="x", pady=(0, 4))
        self.enable = SwitchRow(self, "Bật chỉnh màu / hiệu ứng", on_change=s(cfg, "enable"))
        self.enable.pack(fill="x", pady=3)

        sec = Section(self, "Chống trùng bản quyền")
        sec.pack(fill="x", pady=4)
        grid = ctk.CTkFrame(sec.body, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1)
        self.dup: dict[str, SwitchRow] = {}
        items = [("dup_shift", "Dịch khung"), ("dup_noise", "Nhiễu động"), ("dup_color", "Lệch màu"),
                 ("dup_zoompan", "Phóng & trượt"), ("dup_audio", "Lệch nhịp âm"), ("dup_gop", "Đổi GOP")]
        for i, (key, label) in enumerate(items):
            sw = SwitchRow(grid, label, on_change=lambda v, k=key: self._dup_set(k, bool(v)))
            sw.grid(row=i // 2, column=i % 2, sticky="w", pady=3)
            self.dup[key] = sw
        self.dup_all = SwitchRow(sec.body, "Bật tất cả hiệu ứng chống trùng", on_change=self._dup_all)
        self.dup_all.pack(fill="x", pady=(6, 0))
        hint(sec.body, "Biến thể rất nhẹ, người xem không nhận ra: dịch khung vài pixel, nhiễu hạt, lệch màu 1–3%, phóng 2% "
                       "trượt chậm, lệch cao độ âm 2% (giữ nhịp), keyframe 47. «Đổi GOP» chỉ tác động lúc encode.").pack(fill="x")

        sec = Section(self, "Bộ lọc & chỉnh màu")
        sec.pack(fill="x", pady=4)
        ButtonRow(sec.body, [(p, (lambda name=p: self._preset(name)), None) for p in COLOR_PRESETS[:4]]).pack(fill="x", pady=(0, 4))
        self.preset = OptionRow(sec.body, "Preset", COLOR_PRESETS, on_change=self._preset)
        self.rotation = OptionRow(sec.body, "Xoay", ROTATIONS, on_change=self._rotation_changed)
        self.rotate_custom = SliderRow(sec.body, "Góc tuỳ chỉnh", from_=-180, to=180, step=1, suffix="°",
                                       on_change=s(cfg, "rotate", cast=float, preview=False), on_release=rel)
        self.brightness = SliderRow(sec.body, "Sáng", from_=-0.5, to=0.5, step=0.01, fmt="{:+.2f}",
                                    on_change=s(cfg, "brightness", cast=float, preview=False), on_release=rel)
        self.contrast = SliderRow(sec.body, "Tương phản", from_=0.5, to=2.0, step=0.01, fmt="{:.2f}",
                                  on_change=s(cfg, "contrast", cast=float, preview=False), on_release=rel)
        self.saturation = SliderRow(sec.body, "Bão hòa", from_=0.0, to=2.5, step=0.01, fmt="{:.2f}",
                                    on_change=s(cfg, "saturation", cast=float, preview=False), on_release=rel)
        self.gamma = SliderRow(sec.body, "Gamma", from_=0.5, to=2.0, step=0.01, fmt="{:.2f}",
                               on_change=s(cfg, "gamma", cast=float, preview=False), on_release=rel)
        self.temperature = SliderRow(sec.body, "Nhiệt độ màu", from_=-100, to=100, step=1, fmt="{:+.0f}",
                                     on_change=s(cfg, "temperature", cast=float, preview=False), on_release=rel)
        pack_rows(sec.body, [self.preset, self.rotation, self.rotate_custom, self.brightness, self.contrast, self.saturation,
                             self.gamma, self.temperature])
        hint(sec.body, "Hiệu ứng màu sắc và góc quay được xem trước trực tiếp trên khung xem trước.").pack(fill="x")

        sec = Section(self, "Hiệu ứng hình ảnh")
        sec.pack(fill="x", pady=4)
        self.deshake = SwitchRow(sec.body, "Khử rung (deshake)", on_change=s(cfg, "deshake"))
        self.sharpen = SliderRow(sec.body, "Làm nét", from_=0, to=2, step=0.05, fmt="{:.2f}",
                                 on_change=s(cfg, "sharpen", cast=float, preview=False), on_release=rel)
        self.grain = SliderRow(sec.body, "Hạt phim (grain)", from_=0, to=100, step=1,
                               on_change=s(cfg, "grain", cast=float, preview=False), on_release=rel)
        self.vignette = SliderRow(sec.body, "Tối góc (vignette)", from_=0, to=100, step=1,
                                  on_change=s(cfg, "vignette", cast=float, preview=False), on_release=rel)
        self.kenburns = OptionRow(sec.body, "Chuyển động Ken Burns", KENBURNS, on_change=s(cfg, "kenburns"))
        self.kb_amount = SliderRow(sec.body, "Mức zoom/pan", from_=2, to=40, step=1, suffix="%",
                                   on_change=s(cfg, "kenburns_amount", cast=float, preview=False), on_release=rel)
        pack_rows(sec.body, [self.deshake, self.sharpen, self.grain, self.vignette, self.kenburns, self.kb_amount])

        sec = Section(self, "Lớp phủ texture", subtitle="film grain, bụi, light leak…")
        sec.pack(fill="x", pady=4)
        self.tex_enable = SwitchRow(sec.body, "Phủ texture lên video", on_change=s(cfg, "texture_enable"))
        self.tex_path = EntryRow(sec.body, "File texture", placeholder="video/ảnh texture", on_change=s(cfg, "texture_path"))
        self.tex_blend = OptionRow(sec.body, "Chế độ hoà trộn", TEXTURE_BLENDS, on_change=s(cfg, "texture_blend"))
        self.tex_opacity = SliderRow(sec.body, "Độ đậm", from_=5, to=100, step=5, suffix="%",
                                     on_change=s(cfg, "texture_opacity", cast=float, preview=False), on_release=rel)
        pack_rows(sec.body, [self.tex_enable, self.tex_path, self.tex_blend, self.tex_opacity])
        ButtonRow(sec.body, [("📚 Thư viện texture", self._pick_texture, None), ("📁 Mở thư mục", self._open_lib, None)]).pack(fill="x", pady=(4, 0))
        hint(sec.body, f"Bỏ file texture (mp4/mov/png) của bạn vào {TEXTURE_LIB}. Chế độ screen hợp với light leak/grain; "
                       "multiply hợp với vết bẩn/vignette.").pack(fill="x")

    def refresh(self) -> None:
        cfg = self.state.color
        self._loading = True
        try:
            self.enable.set(cfg.enable)
            for key, sw in self.dup.items():
                sw.set(bool(getattr(cfg, key, False)))
            self.dup_all.set(all(getattr(cfg, k, False) for k in self.dup))
            self.preset.set(cfg.preset if cfg.preset in COLOR_PRESETS else COLOR_PRESETS[0])
            deg = float(cfg.rotate) % 360
            label = {0: ROTATIONS[0], 90: ROTATIONS[1], 180: ROTATIONS[2], 270: ROTATIONS[3]}.get(int(deg) if deg.is_integer() else -1, ROTATIONS[4])
            self.rotation.set(label)
            self.rotate_custom.set(cfg.rotate)
            self.brightness.set(cfg.brightness)
            self.contrast.set(cfg.contrast)
            self.saturation.set(cfg.saturation)
            self.gamma.set(cfg.gamma)
            self.temperature.set(cfg.temperature)
            self.deshake.set(cfg.deshake)
            self.sharpen.set(cfg.sharpen)
            self.grain.set(cfg.grain)
            self.vignette.set(cfg.vignette)
            self.kenburns.set(cfg.kenburns)
            self.kb_amount.set(cfg.kenburns_amount)
            self.tex_enable.set(cfg.texture_enable)
            self.tex_path.set(cfg.texture_path)
            self.tex_blend.set(cfg.texture_blend if cfg.texture_blend in TEXTURE_BLENDS else TEXTURE_BLENDS[0])
            self.tex_opacity.set(cfg.texture_opacity)
        finally:
            self._loading = False

    def _dup_set(self, key: str, value: bool) -> None:
        if self._loading:
            return
        cfg = self.state.color
        setattr(cfg, key, value)
        if key == "dup_gop":
            self.state.video.gop_size = 47 if value else 0
        self.dup_all.set(all(getattr(cfg, k, False) for k in self.dup))
        self.state.config_changed("color")
        self.window.preview.schedule_refresh()

    def _dup_all(self, v: bool) -> None:
        if self._loading:
            return
        cfg = self.state.color
        for key in self.dup:
            setattr(cfg, key, bool(v))
            self.dup[key].set(bool(v))
        self.dup_all.set(bool(v))
        self.state.video.gop_size = 47 if v else 0
        self.state.config_changed("color")
        self.window.preview.schedule_refresh()

    def _quick_dedup(self) -> None:
        self.state.color.enable = True
        self.enable.set(True)
        self._dup_all(True)

    def _preset(self, name: str) -> None:
        cfg = self.state.color
        cfg.preset = name if name in COLOR_PRESETS else COLOR_PRESETS[0]
        cfg.enable = True
        self.state.config_changed("color")
        self.refresh()
        self.window.preview.schedule_refresh(0)

    def _rotation_changed(self, v: str) -> None:
        if v == ROTATIONS[4]:
            return
        deg = {ROTATIONS[0]: 0.0, ROTATIONS[1]: 90.0, ROTATIONS[2]: 180.0, ROTATIONS[3]: 270.0}[v]
        self.rotate_custom.set(deg)
        self.set_value(self.state.color, "rotate", deg)

    def _enable_all(self) -> None:
        self.state.color.enable = True
        self.state.config_changed("color")
        self.refresh()
        self.window.preview.schedule_refresh(0)

    def _reset(self) -> None:
        self.state.color = ColorConfig(enable=self.state.color.enable)
        self.state.video.gop_size = 0
        self.state.config_changed("color")
        self.refresh()
        self.window.preview.schedule_refresh(0)

    def _pick_texture(self) -> None:
        TEXTURE_LIB.mkdir(parents=True, exist_ok=True)
        p = filedialog.askopenfilename(title="Chọn texture", initialdir=str(TEXTURE_LIB),
                                       filetypes=[("Texture", "*.mp4 *.mov *.mkv *.webm *.png *.jpg *.jpeg"), ("Tất cả", "*.*")])
        if p:
            self.state.color.texture_path = p
            self.state.color.texture_enable = True
            self.state.color.enable = True
            self.state.config_changed("color")
            self.refresh()
            self.window.preview.schedule_refresh(0)

    def _open_lib(self) -> None:
        TEXTURE_LIB.mkdir(parents=True, exist_ok=True)
        os.startfile(str(TEXTURE_LIB))
