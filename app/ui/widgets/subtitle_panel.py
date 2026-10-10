"""Chỉnh tay phụ đề trên video (tab «Phụ đề» của mục Lời thoại): 4 khối thu gọn — Chữ · Nền & chữ nổi · Khung ·
Thời gian; bấm tiêu đề mới mở, tiêu đề tóm tắt giá trị đang dùng.

Vị trí / cỡ chữ nhanh nằm trên thanh khung xem trước; kiểu dựng sẵn ở ô «Kiểu» phía trên — ở đây chỉ là phần chỉnh tay."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from app.config import theme as T
from app.core.state import SUB_BG_STYLES, TEXT_EFFECTS
from app.services import render_pipeline as rp
from app.services import stages as stages_mod
from app.services import subtitle_style as sst
from app.services.ffmpeg import MediaInfo
from app.services.subtitle_presets import PLACE_SHORT, place_of
from app.services.system_fonts import list_fonts
from app.ui.widgets.common import ColorRow, ComboRow, OptionRow, Section, SliderRow, SwitchRow, hint, pack_rows

QUICK_COLORS = ["#FFFFFF", "#FFE45C", "#22D3EE", "#EF4444", "#22C55E", "#F472B6"]
WORD_ANIMS = ["off", "pop", "fill", "reveal"]
WORD_ANIM_LABELS = {"off": "Không", "pop": "Nổi bật (kiểu CapCut)", "fill": "Tô màu dần", "reveal": "Hiện dần từng chữ"}
WORD_ANIM_SHORT = {"pop": "chữ nổi", "fill": "tô màu dần", "reveal": "hiện dần"}
EFFECT_LABELS = {TEXT_EFFECTS[0]: "Không"}


class SubtitleStylePanel(ctk.CTkFrame):
    def __init__(self, master, module) -> None:
        super().__init__(master, fg_color="transparent")
        self.m = module                       # module chủ (setter / set_value / _loading / window / state)
        self.window, self.state = module.window, module.state
        cfg = self.state.subtitle
        s = module.setter
        rel = lambda _v: self.window.preview.schedule_refresh()  # noqa: E731

        self.text_sec = self._section("Chữ")
        b = self.text_sec.body
        self.font = ComboRow(b, "Phông chữ", ["Arial"], on_change=s(cfg, "sub_font"))
        self.size = SliderRow(b, "Cỡ chữ", from_=12, to=120, step=1, suffix="px", on_change=self._size_changed,
                              on_release=rel)
        self.color = ColorRow(b, "Màu chữ", on_change=s(cfg, "sub_color"))
        swatches = ctk.CTkFrame(b, fg_color="transparent")
        ctk.CTkLabel(swatches, text="", width=120).pack(side="left")
        for c in QUICK_COLORS:
            ctk.CTkButton(swatches, text="", width=26, height=22, fg_color=c, hover_color=c, border_width=1,
                          border_color=T.BORDER, command=lambda col=c: self._quick_color(col)).pack(side="left", padx=2)
        self.stroke = SliderRow(b, "Viền", from_=0, to=10, step=0.5, fmt="{:.1f}", suffix="px",
                                on_change=s(cfg, "sub_stroke_width", cast=float, preview=False), on_release=rel)
        self.stroke_color = ColorRow(b, "Màu viền", value="#000000", on_change=s(cfg, "sub_stroke_color"))
        self.shadow = SliderRow(b, "Bóng", from_=0, to=10, step=0.5, fmt="{:.1f}", suffix="px",
                                on_change=s(cfg, "sub_shadow", cast=float, preview=False), on_release=rel)
        self.opacity = SliderRow(b, "Độ đục", from_=10, to=100, step=5, suffix="%",
                                 on_change=s(cfg, "sub_opacity", cast=float, preview=False), on_release=rel)
        self.spacing = SliderRow(b, "Giãn chữ", from_=-2, to=10, step=0.5, fmt="{:.1f}", suffix="px",
                                 on_change=s(cfg, "sub_letter_spacing", cast=float, preview=False), on_release=rel)
        self.effect = OptionRow(b, "Hiệu ứng", TEXT_EFFECTS, labels=EFFECT_LABELS, on_change=s(cfg, "text_effect"))
        flags = ctk.CTkFrame(b, fg_color="transparent")
        self.bold = SwitchRow(flags, "Đậm", on_change=s(cfg, "sub_bold"))
        self.italic = SwitchRow(flags, "Nghiêng", on_change=s(cfg, "sub_italic"))
        self.upper = SwitchRow(flags, "IN HOA", on_change=s(cfg, "sub_uppercase"))
        for w in (self.bold, self.italic, self.upper):
            w.pack(side="left", padx=(0, 14))
        pack_rows(b, [self.font, self.size, self.color, swatches, self.stroke, self.stroke_color, self.shadow,
                      self.opacity, self.spacing, self.effect, flags])

        self.bg_sec = self._section("Nền & chữ nổi")
        b = self.bg_sec.body
        self.bg_style = OptionRow(b, "Nền chữ", SUB_BG_STYLES, on_change=self._bg_changed)
        self.bg_color = ColorRow(b, "Màu nền", value="#000000", on_change=s(cfg, "sub_bg_color"))
        self.bg_opacity = SliderRow(b, "Độ đậm nền", from_=0, to=100, step=5, suffix="%",
                                    on_change=s(cfg, "sub_bg_opacity", cast=float, preview=False), on_release=rel)
        self.karaoke = OptionRow(b, "Chữ đang đọc", WORD_ANIMS, labels=WORD_ANIM_LABELS, on_change=self._anim_changed,
                                 info="Nổi bật: chữ đang đọc đổi màu và to lên rồi trở lại (kiểu CapCut). Tô màu dần: chữ "
                                      "đã đọc giữ màu nổi. Hiện dần: chữ hiện ra đúng lúc được đọc. Khớp từng tiếng: "
                                      "cài «Căn chữ nổi» (Cài đặt › AI trên máy).")
        self.karaoke_color = ColorRow(b, "Màu chữ nổi", value="#FFE45C", on_change=s(cfg, "karaoke_color"))
        pack_rows(b, [self.bg_style, self.bg_color, self.bg_opacity, self.karaoke, self.karaoke_color])

        self.box_sec = self._section("Khung")
        b = self.box_sec.body
        grid = ctk.CTkFrame(b, fg_color="transparent")
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
        hint(b, "Đơn vị px theo video xuất. Kéo khung trên hình (phím T) cũng được.").pack(fill="x", pady=(2, 0))
        self.follow = SwitchRow(b, "Đè lên chỗ chữ cũ", on_change=self._follow_changed,
                                info="Video có phụ đề in sẵn: máy dò chỗ chữ cũ, che mờ rồi đặt phụ đề mới vào giữa "
                                     "đúng chỗ đó — mỗi video tự theo chỗ của nó.")
        self.follow.pack(fill="x", pady=(4, 0))
        self.follow_hint = hint(b, "")
        self.follow_hint.pack(fill="x")

        self.time_sec = self._section("Thời gian")
        b = self.time_sec.body
        self.delay = SliderRow(b, "Trễ phụ đề", from_=-5, to=5, step=0.05, fmt="{:+.2f}", suffix="s",
                               on_change=s(cfg, "sub_delay", cast=float, preview=False), on_release=rel,
                               info="Âm = hiện sớm hơn. Chỉ cần khi chữ lệch so với giọng.")
        self.sec_scale = SliderRow(b, "Cỡ dòng phụ", from_=40, to=100, step=5, suffix="%",
                                   on_change=lambda v: module.set_value(cfg, "secondary_scale", v / 100, preview=False),
                                   on_release=rel, info="Cỡ dòng chữ gốc khi chọn «Song ngữ».")
        pack_rows(b, [self.delay, self.sec_scale])

    def _section(self, title: str) -> Section:
        sec = Section(self, title, collapsed=True)
        sec.pack(fill="x", pady=4)
        return sec

    def summarize(self) -> None:
        """Tiêu đề từng khối tóm tắt giá trị đang dùng — đọc được cả khi khối đang thu gọn."""
        cfg = self.state.subtitle
        self.text_sec.set_subtitle(f"{cfg.sub_font} · {cfg.sub_size}px")
        bg = cfg.bg_style if cfg.bg_style in SUB_BG_STYLES else SUB_BG_STYLES[0]
        anim = WORD_ANIM_SHORT.get(cfg.karaoke_mode, "") if cfg.karaoke else ""
        self.bg_sec.set_subtitle(f"{bg} · {anim}" if anim else bg)
        self.box_sec.set_subtitle(PLACE_SHORT.get(place_of(cfg), ""))
        self.time_sec.set_subtitle(f"trễ {cfg.sub_delay:+.2f}s" if abs(cfg.sub_delay) >= 0.005 else "")

    # ── State → widget ────────────────────────────────────────────────────
    def refresh(self) -> None:
        cfg = self.state.subtitle
        self.font.set_values(list_fonts(self.window))
        self.font.set(cfg.sub_font)
        self.size.set(cfg.sub_size)
        self.color.set(cfg.sub_color)
        self.stroke.set(cfg.sub_stroke_width)
        self.stroke_color.set(cfg.sub_stroke_color)
        self.shadow.set(cfg.sub_shadow)
        self.opacity.set(cfg.sub_opacity)
        self.spacing.set(cfg.sub_letter_spacing)
        self.effect.set(cfg.text_effect if cfg.text_effect in TEXT_EFFECTS else TEXT_EFFECTS[0])
        self.bold.set(cfg.sub_bold)
        self.italic.set(cfg.sub_italic)
        self.upper.set(cfg.sub_uppercase)
        self.bg_style.set(cfg.bg_style if cfg.bg_style in SUB_BG_STYLES else SUB_BG_STYLES[0])
        self.bg_color.set(cfg.sub_bg_color)
        self.bg_opacity.set(cfg.sub_bg_opacity)
        self.karaoke.set(cfg.karaoke_mode if cfg.karaoke and cfg.karaoke_mode in WORD_ANIMS else "off")
        self.karaoke_color.set(cfg.karaoke_color)
        self.follow.set(cfg.follow_hardsub)
        self.delay.set(cfg.sub_delay)
        self.sec_scale.set(cfg.secondary_scale * 100)
        self.follow_status()
        self.fill_pixels()
        self.summarize()

    # ── Hành động ────────────────────────────────────────────────────────
    def _bg_changed(self, v: str) -> None:
        self.m.set_value(self.state.subtitle, "bg_style", v)
        self.m.set_value(self.state.subtitle, "sub_bg_enable", v != SUB_BG_STYLES[0])

    def _anim_changed(self, v: str) -> None:
        cfg = self.state.subtitle
        if v != "off":
            self.m.set_value(cfg, "karaoke_mode", v, preview=False)
        self.m.set_value(cfg, "karaoke", v != "off")

    def _quick_color(self, col: str) -> None:
        self.color.set(col)
        self.m.set_value(self.state.subtitle, "sub_color", col)

    def _size_changed(self, v) -> None:
        """Đổi cỡ chữ → khung phụ đề cao theo (giữ đáy), khung cyan trên xem trước khớp chữ ngay."""
        if self.m._loading:
            return
        cfg = self.state.subtitle
        cfg.sub_size = int(v)
        sst.fit_box_height(cfg, *self._frame_size())
        self.state.config_changed("subtitle")
        self.fill_pixels()
        self.window.preview._redraw()

    def _follow_changed(self, v: bool) -> None:
        self.m.set_value(self.state.subtitle, "follow_hardsub", bool(v))
        if v:
            self.window.preview.ensure_follow_hardsub()
        self.follow_status()
        self.fill_pixels()

    def follow_status(self) -> None:
        cfg = self.state.subtitle
        if not cfg.follow_hardsub:
            self.follow_hint.configure(text="")
            return
        from app.services.hardsub import cached_region

        it = self.state.active
        got = cached_region(it) if it is not None else False
        if got is False:
            text = "Đang dò chỗ chữ cũ của video này…"
        elif got is None:
            text = "Video này không có chữ in sẵn → dùng vị trí khung ở trên."
        else:
            text = "Phụ đề đang nằm giữa chỗ chữ cũ đã che. Kéo tay khung = tắt chế độ này."
        self.follow_hint.configure(text=text)

    # ── Khung theo pixel ─────────────────────────────────────────────────
    def _media(self) -> MediaInfo | None:
        it = self.state.active
        if it is None or not it.width:
            return None
        return MediaInfo(duration=it.duration, width=it.width, height=it.height, fps=it.fps, has_audio=it.has_audio)

    def _frame_size(self) -> tuple[int, int]:
        info = self._media()
        if info is None:
            return 1080, 1920
        geo = rp.output_geometry(info, self.state.video)
        return geo.width, geo.height

    def _effective(self):
        """Khung đang dùng thật cho video này (có thể đang bám chỗ chữ cứng)."""
        info = self._media()
        if info is None:
            return self.state.subtitle
        return stages_mod.subtitle_for(self.state, self.state.active, info)

    def fill_pixels(self) -> None:
        cfg = self._effective()
        W, H = self._frame_size()
        h = sst.box_height_px(cfg, W, H)          # khung luôn cao vừa 2 dòng chữ
        bottom = (cfg.box_y + cfg.box_h) * H
        vals = {"x": cfg.box_x * W, "y": max(0.0, bottom - h), "w": cfg.box_w * W, "h": h}
        self._shown_px = {k: int(round(v)) for k, v in vals.items()}
        for k, v in self._shown_px.items():
            self.px_vars[k].set(str(v))

    def _apply_pixels(self) -> None:
        if self.m._loading:
            return
        cfg = self.state.subtitle
        W, H = self._frame_size()
        try:
            x = float(self.px_vars["x"].get()); y = float(self.px_vars["y"].get())
            w = float(self.px_vars["w"].get()); h = float(self.px_vars["h"].get())
        except ValueError:
            self.fill_pixels()
            return
        shown = getattr(self, "_shown_px", {})
        typed = {"x": x, "y": y, "w": w, "h": h}
        changed = {k for k, v in typed.items() if int(round(v)) != shown.get(k)}
        if not changed:                            # rời ô mà không sửa gì → khỏi «đã chỉnh» vì sai số làm tròn
            return
        eff = self._effective()
        if cfg.follow_hardsub:                     # gõ số = đặt tay → thôi bám chỗ chữ cứng
            cfg.box_x, cfg.box_y, cfg.box_w, cfg.box_h = eff.box_x, eff.box_y, eff.box_w, eff.box_h
            cfg.follow_hardsub = False
            self.follow.set(False)
            self.follow_status()
        bottom = cfg.box_y + cfg.box_h
        if "h" in changed:                         # «Cao» = chiều cao 2 dòng chữ → đổi cỡ chữ theo
            cfg.sub_size = sst.size_for_height(cfg, W, H, h)
            self.size.set(cfg.sub_size)
        nh = min(0.9, sst.box_height_px(cfg, W, H) / H)
        if "y" in changed:
            bottom = max(0.0, min(0.99, y / H)) + nh
        nx = max(0.0, min(0.99, x / W))
        nw = max(0.02, min(1.0 - nx, w / W))
        bottom = max(nh, min(1.0, bottom))
        cfg.box_x, cfg.box_w = round(nx, 4), round(nw, 4)
        cfg.box_y, cfg.box_h = round(bottom - nh, 4), round(nh, 4)
        cfg.use_box = True
        self.state.config_changed("subtitle")
        self.window.preview.schedule_refresh(0)
        self.fill_pixels()
