"""Module Che mờ: nhiều vùng, preset vị trí, khung theo pixel, cường độ, khoảng thời gian; vẽ bằng công cụ B trên xem trước."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from app.config import theme as T
from app.core.state import BLUR_STYLES
from app.services import render_pipeline as rp
from app.services.ffmpeg import MediaInfo, parse_hhmmss
from app.ui.modules.base_module import BaseModule
from app.ui.preview_panel import new_region
from app.ui.widgets.ui import Select, button
from app.ui.widgets.common import (ButtonRow, OptionRow, Section, SliderRow, StatusLabel, SwitchRow, TimeEntry, hint,
                                  pack_rows)
from app.ui.progress_panel import friendly_error


class BlurModule(BaseModule):
    key = "blur"
    title = "Che mờ"
    icon = "🟦"
    section = "blur"

    def build(self) -> None:
        cfg = self.state.blur
        self.enable = SwitchRow(self, "Bật che mờ (vùng vẽ tay + tự dò)", on_change=self.setter(cfg, "enable"))
        self.enable.pack(fill="x", pady=(0, 2))
        hint(self, "Làm mờ chữ in sẵn, logo… trên video gốc.").pack(fill="x", pady=(0, 6))

        sec = Section(self, "Tự dò chữ cứng")
        sec.pack(fill="x", pady=4)
        self.auto = SwitchRow(sec.body, "Tự dò & che chữ cứng cho từng video", on_change=self._auto_toggled,
                              info="Mỗi video chữ in sẵn nằm một chỗ: máy tự tìm dải chữ đổi liên tục và che mờ (bỏ qua "
                                   "logo / watermark đứng yên). Lưu vào mẫu được.")
        self.auto.pack(fill="x")
        ButtonRow(sec.body, [("🔍 Dò lại cho video đang chọn", self._detect_now, None)]).pack(fill="x", pady=(4, 2))
        self.auto_status = StatusLabel(sec.body, "")
        self.auto_status.pack(fill="x")

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=2)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(row, text="− Xóa", width=64, height=32, command=self._remove_region, **T.danger_button()).grid(row=0, column=0)
        self.region_var = tk.StringVar(value="")
        self.region_menu = Select(row, ["(chưa có vùng)"], variable=self.region_var, command=self._select_region, height=34)
        self.region_menu.grid(row=0, column=1, sticky="ew", padx=6)
        button(row, "Thêm", icon_name="add", variant="primary", size="sm", command=self._add_region).grid(row=0, column=2)
        ButtonRow(self, [("Vùng đáy", lambda: self._preset("bottom"), None), ("Chạm 2 mép", lambda: self._preset("full"), None)]).pack(fill="x", pady=2)
        ButtonRow(self, [("Giữa khung", lambda: self._preset("center"), None), ("Làm mới", lambda: self._preset("reset"), None)]).pack(fill="x", pady=2)

        sec = Section(self, "Khung che (pixels)")
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
        hint(sec.body, "Kéo khung cam trên hình (phím B) · ✕ hoặc Delete để xoá.").pack(fill="x", pady=(2, 0))

        sec = Section(self, "Cường độ")
        sec.pack(fill="x", pady=4)
        rel = lambda _v: self.window.preview.schedule_refresh()  # noqa: E731
        self.style = OptionRow(sec.body, "Kiểu", BLUR_STYLES, on_change=lambda v: self._set("style", v))
        self.strength = SliderRow(sec.body, "Độ mờ", from_=0, to=100, step=1, suffix="%",
                                  on_change=lambda v: self._set("strength", float(v), preview=False), on_release=rel)
        self.feather = SliderRow(sec.body, "Mềm mép", from_=0, to=100, step=1, suffix="%",
                                 on_change=lambda v: self._set("feather", float(v), preview=False), on_release=rel)
        self.opacity = SliderRow(sec.body, "Độ đục", from_=10, to=100, step=5, suffix="%",
                                 on_change=lambda v: self._set("opacity", float(v), preview=False), on_release=rel)
        self.rounded = SliderRow(sec.body, "Bo góc", from_=0, to=100, step=5, suffix="%",
                                 on_change=lambda v: self._set("rounded", float(v), preview=False), on_release=rel)
        pack_rows(sec.body, [self.style, self.strength, self.feather, self.opacity, self.rounded])

        sec = Section(self, "Thời gian hiệu lực")
        sec.pack(fill="x", pady=4)
        self.t_start = TimeEntry(sec.body, "Bắt đầu", on_change=lambda v: self._set("start", parse_hhmmss(v)),
                                 pick_time=lambda: self.window.preview.t)
        self.t_end = TimeEntry(sec.body, "Kết thúc", on_change=lambda v: self._set("end", parse_hhmmss(v)),
                               pick_time=lambda: self.window.preview.t)
        pack_rows(sec.body, [self.t_start, self.t_end])
        hint(sec.body, "Để 0 = suốt video. Nhập giây hoặc HH:MM:SS.").pack(fill="x")

    # ── Trạng thái ───────────────────────────────────────────────────────
    def _regions(self) -> list[dict]:
        return self.state.blur.regions

    def _active(self) -> dict | None:
        rs = self._regions()
        i = self.state.blur.active_index
        return rs[i] if 0 <= i < len(rs) else None

    def _frame_size(self) -> tuple[int, int]:
        it = self.state.active
        if it is None or not it.width:
            return 1920, 1080
        geo = rp.output_geometry(MediaInfo(duration=it.duration, width=it.width, height=it.height, fps=it.fps,
                                           has_audio=it.has_audio), self.state.video)
        return geo.width, geo.height

    def refresh(self) -> None:
        cfg = self.state.blur
        self._loading = True
        try:
            self.enable.set(cfg.enable)
            self.auto.set(cfg.auto_detect)
            self._refresh_menu()
            r = self._active()
            W, H = self._frame_size()
            if r:
                self.style.set(r.get("style", BLUR_STYLES[0]))
                self.strength.set(float(r.get("strength", 88)))
                self.feather.set(float(r.get("feather", 28)))
                self.opacity.set(float(r.get("opacity", 100)))
                self.rounded.set(float(r.get("rounded", 0)))
                for k, scale in (("x", W), ("y", H), ("w", W), ("h", H)):
                    self.px_vars[k].set(str(int(round(float(r.get(k, 0)) * scale))))
                self.t_start.set(f"{float(r.get('start', 0)):.2f}")
                self.t_end.set(f"{float(r.get('end', 0)):.2f}")
            else:
                for k in self.px_vars:
                    self.px_vars[k].set("0")
        finally:
            self._loading = False
        self._update_auto_status()

    def on_active_changed(self) -> None:
        self.refresh()

    # ── Tự dò chữ cứng ───────────────────────────────────────────────────
    def _auto_toggled(self, on: bool) -> None:
        if self._loading:
            return
        self.set_value(self.state.blur, "auto_detect", bool(on))
        if on and not self.state.blur.enable:         # công tắc «Bật che chữ» tắt thì tự dò cũng không che → bật luôn
            self.state.blur.enable = True
            self.enable.set(True)
            self.state.config_changed("blur")
        self._update_auto_status()
        if on:
            self.window.preview.ensure_hardsub()

    def _update_auto_status(self) -> None:
        from app.services.hardsub import cached_region

        it = self.state.active
        if not self.state.blur.auto_detect:
            self.auto_status.info("")
        elif it is None:
            self.auto_status.info("Chọn một video.")
        else:
            r = cached_region(it)
            if r is False:
                self.auto_status.info("Chưa dò video này — máy sẽ tự dò khi xem trước / chuẩn bị / render.")
            elif r is None:
                self.auto_status.warn(f"{it.name}: không thấy chữ cứng (không che thêm).")
            else:
                self.auto_status.ok(f"{it.name}: che dải {r['y'] * 100:.0f}–{(r['y'] + r['h']) * 100:.0f}% chiều cao, "
                                    f"rộng {r['w'] * 100:.0f}% (chữ có ở {r.get('confidence', 0) * 100:.0f}% khung).")

    def _detect_now(self) -> None:
        from app.services.hardsub import ensure_region, forget

        it = self.state.active
        if it is None:
            return
        forget(it)
        self.auto_status.warn("Đang dò chữ cứng…")
        ff, win = self.window.ffmpeg, self.window

        def work():
            info = ff.probe(it.path)
            return ensure_region(it, ff, info.duration, log=win.log_bg)

        def done(_r, error):
            if error:
                self.auto_status.error(f"Lỗi: {friendly_error(str(error))}")
                return
            if not self.state.blur.auto_detect:
                self.state.blur.auto_detect = True
                self.state.config_changed("blur")
                self.refresh()
            self._update_auto_status()
            win.preview.schedule_refresh(0)

        win.run_bg(work, done)

    def _refresh_menu(self) -> None:
        rs = self._regions()
        names = [f"Blur {i + 1}" for i in range(len(rs))]
        self.region_menu.configure(values=names or ["(chưa có vùng)"])
        i = self.state.blur.active_index
        self.region_var.set(names[i] if 0 <= i < len(names) else (names[0] if names else "(chưa có vùng)"))

    def on_shown(self) -> None:
        super().on_shown()
        self.window.preview.set_tool("B")

    def on_hidden(self) -> None:
        if self.window.preview.tool == "B":
            self.window.preview.set_tool("V")

    # ── Thao tác ─────────────────────────────────────────────────────────
    def _set(self, key: str, value, *, preview: bool = True) -> None:
        if self._loading:
            return
        r = self._active()
        if r is None or r.get(key) == value:
            return
        r[key] = value
        self.state.config_changed("blur")
        if preview:
            self.window.preview.schedule_refresh()
        else:
            self.window.preview._redraw()

    def _apply_pixels(self) -> None:
        if self._loading:
            return
        r = self._active()
        if r is None:
            return
        W, H = self._frame_size()
        try:
            x = float(self.px_vars["x"].get()) / W; y = float(self.px_vars["y"].get()) / H
            w = float(self.px_vars["w"].get()) / W; h = float(self.px_vars["h"].get()) / H
        except ValueError:
            self.refresh()
            return
        x, y = max(0.0, min(0.99, x)), max(0.0, min(0.99, y))
        w, h = max(0.01, min(1.0 - x, w)), max(0.01, min(1.0 - y, h))
        r.update({"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)})
        self.state.config_changed("blur")
        self.window.preview.schedule_refresh(0)

    def _select_region(self, name: str) -> None:
        names = self.region_menu.cget("values")
        if name in names:
            self.state.blur.active_index = names.index(name)
            self.refresh()
            self.window.preview._redraw()

    def _add_region(self) -> None:
        self._regions().append(new_region())
        self.state.blur.active_index = len(self._regions()) - 1
        self.state.blur.enable = True
        self.state.config_changed("blur")
        self.refresh()
        self.window.preview.schedule_refresh(0)

    def _remove_region(self) -> None:
        i = self.state.blur.active_index
        rs = self._regions()
        if 0 <= i < len(rs):
            del rs[i]
            self.state.blur.active_index = max(0, min(i, len(rs) - 1))
            self.state.config_changed("blur")
            self.refresh()
            self.window.preview.schedule_refresh(0)

    def _preset(self, kind: str) -> None:
        r = self._active()
        if r is None:
            self._add_region()
            r = self._active()
            if r is None:
                return
        if kind == "bottom":
            r.update({"x": 0.05, "y": 0.84, "w": 0.90, "h": 0.10})
        elif kind == "full":
            r.update({"x": 0.0, "w": 1.0})
        elif kind == "center":
            r.update({"x": 0.25, "y": 0.4, "w": 0.5, "h": 0.2})
        else:
            r.update(new_region())
        self.state.blur.enable = True
        self.state.config_changed("blur")
        self.refresh()
        self.window.preview.schedule_refresh(0)
