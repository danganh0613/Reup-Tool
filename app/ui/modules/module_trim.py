"""Module Cắt: tab «Cắt đầu / cuối» (kèm cắt nhanh) và «Render nhiều đoạn» (ghép liền hoặc xuất từng file)."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from app.config import theme as T
from app.core.state import TRIM_MODES
from app.services.ffmpeg import parse_hhmmss
from app.services.srt_utils import format_ts
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.ui import button
from app.ui.widgets.common import ButtonRow, OptionRow, Section, SwitchRow, TimeEntry, hint, pack_rows


class SegmentRow(ctk.CTkFrame):
    def __init__(self, master, module: TrimModule, index: int, seg: dict) -> None:
        super().__init__(master, fg_color=T.BG_ELEVATED, corner_radius=8)
        self.module, self.index, self.seg = module, index, seg
        self.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 0))
        head.grid_columnconfigure(0, weight=1)
        self.name_var = ctk.StringVar(value=seg.get("name", f"Đoạn {index + 1}"))
        e = ctk.CTkEntry(head, textvariable=self.name_var, height=32, font=T.font(12, "bold"))
        e.grid(row=0, column=0, sticky="ew")
        e.bind("<FocusOut>", lambda _e: self._set("name", self.name_var.get()))
        button(head, "", icon_name="close", variant="ghost", size="sm", command=lambda: module.remove_segment(index), icon_color=T.TEXT_DIM,
               tip="Xoá đoạn này").grid(row=0, column=1, padx=(6, 0))
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        self.start = TimeEntry(body, "Từ", value=str(seg.get("start", "0")), on_change=lambda v: self._set("start", v),
                               pick_time=lambda: module.window.preview.t, label_width=60)
        self.end = TimeEntry(body, "Đến", value=str(seg.get("end", "")), on_change=lambda v: self._set("end", v),
                             pick_time=lambda: module.window.preview.t, label_width=60)
        pack_rows(body, [self.start, self.end], pady=2)

    def _set(self, key: str, value: str) -> None:
        if self.seg.get(key) != value:
            self.seg[key] = value
            self.module.state.config_changed("trim")
            self.module.update_summary()


class TrimModule(BaseModule):
    key = "trim"
    title = "Cắt"
    icon = "✂"
    section = "trim"

    def build(self) -> None:
        cfg = self.state.trim
        s = self.setter
        self.rows: list[SegmentRow] = []
        self.tab_var = tk.StringVar(value="Cắt đầu / cuối")
        ctk.CTkSegmentedButton(self, values=["Cắt đầu / cuối", "Render nhiều đoạn"], variable=self.tab_var,
                               command=self._show_tab, font=T.font(12)).pack(fill="x", pady=(0, 6))
        self.tab_a = ctk.CTkFrame(self, fg_color="transparent")
        self.tab_b = ctk.CTkFrame(self, fg_color="transparent")

        # ── Cắt đầu/cuối ─────────────────────────────────────────────────
        ta = self.tab_a
        self.enable = SwitchRow(ta, "Bật cắt đầu / cuối", on_change=s(cfg, "enable", preview=False))
        self.enable.pack(fill="x", pady=3)
        hint(ta, "Phụ đề, giọng và nhạc tự khớp phần còn lại.").pack(fill="x")
        box = ctk.CTkFrame(ta, fg_color=T.BG_ELEVATED, corner_radius=8)
        box.pack(fill="x", pady=6)
        self.head = TimeEntry(box, "Bỏ đầu (giây)", on_change=lambda v: self._set_simple("head", v),
                              pick_time=lambda: self.window.preview.t)
        self.tail = TimeEntry(box, "Bỏ cuối (giây)", on_change=lambda v: self._set_simple("tail", v),
                              pick_time=self._tail_from_preview)
        self.head.pack(fill="x", padx=8, pady=(8, 2))
        self.tail.pack(fill="x", padx=8, pady=(2, 8))
        sec = Section(ta, "Cắt nhanh")
        sec.pack(fill="x", pady=4)
        ButtonRow(sec.body, [("Bỏ 3s đầu · 2s cuối", lambda: self._quick(3, 2), None),
                             ("Bỏ 5s đầu · 5s cuối", lambda: self._quick(5, 5), None)]).pack(fill="x")
        ButtonRow(sec.body, [("Bỏ 1s đầu · 1s cuối", lambda: self._quick(1, 1), None),
                             ("Không cắt", lambda: self._quick(0, 0), None)]).pack(fill="x", pady=(4, 0))
        hint(sec.body, "Mẹo: tua tới chỗ muốn cắt rồi bấm «Đang xem» (hoặc phím I / O). Gõ được 90 · 1:30 · 5s · 1p30.").pack(
            fill="x", pady=(4, 0))

        # ── Nhiều đoạn ───────────────────────────────────────────────────
        tb = self.tab_b
        self.mode = OptionRow(tb, "Chế độ", TRIM_MODES, on_change=s(cfg, "mode", preview=False),
                              info="Ghép liền: nối các đoạn thành 1 video. Xuất từng file: mỗi đoạn một MP4 (_p1, _p2…). "
                                   "Có đoạn thì bỏ qua cắt đầu / cuối.")
        self.mode.pack(fill="x", pady=3)
        ButtonRow(tb, [("＋ Thêm đoạn", self._add_segment, T.primary_button()),
                       ("＋ Từ mốc đang xem → hết", self._add_from_preview, None)]).pack(fill="x", pady=(0, 4))
        self.summary = ctk.CTkLabel(tb, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w")
        self.summary.pack(fill="x", pady=(0, 4))
        self.list_frame = ctk.CTkFrame(tb, fg_color="transparent")
        self.list_frame.pack(fill="x")
        self._show_tab("Cắt đầu / cuối")

    def _show_tab(self, name: str) -> None:
        self.tab_a.pack_forget()
        self.tab_b.pack_forget()
        (self.tab_a if name == "Cắt đầu / cuối" else self.tab_b).pack(fill="x")

    def refresh(self) -> None:
        cfg = self.state.trim
        self._loading = True
        try:
            self.enable.set(cfg.enable)
            self.head.set(str(cfg.head))
            self.tail.set(str(cfg.tail))
            self.mode.set(cfg.mode if cfg.mode in TRIM_MODES else TRIM_MODES[0])
        finally:
            self._loading = False
        self._rebuild()

    def _rebuild(self) -> None:
        for r in self.rows:
            r.destroy()
        self.rows.clear()
        for i, seg in enumerate(self.state.trim.segments):
            row = SegmentRow(self.list_frame, self, i, seg)
            row.pack(fill="x", pady=3)
            self.rows.append(row)
        self.update_summary()

    def update_summary(self) -> None:
        item = self.state.active
        dur = item.duration if item else 0.0
        segs = self.state.trim.segments
        if not segs:
            self.summary.configure(text="Chưa có đoạn — đang dùng cắt đầu/cuối.")
            return
        total = 0.0
        for sg in segs:
            s0 = parse_hhmmss(str(sg.get("start", "0")))
            e_raw = str(sg.get("end", "")).strip()
            e0 = parse_hhmmss(e_raw) if e_raw else dur
            total += max(0.0, e0 - s0)
        self.summary.configure(text=f"{len(segs)} đoạn · tổng {format_ts(total, '.')[:8]}" + (f" / gốc {format_ts(dur, '.')[:8]}" if dur else ""))

    def on_active_changed(self) -> None:
        self.update_summary()

    # ── Thao tác ─────────────────────────────────────────────────────────
    def _set_simple(self, key: str, value: str) -> None:
        self.set_value(self.state.trim, key, value.strip() or "0", preview=False)

    def _tail_from_preview(self) -> float:
        it = self.state.active
        dur = it.duration if it else 0.0
        return max(0.0, dur - self.window.preview.t)

    def _quick(self, head: float, tail: float) -> None:
        cfg = self.state.trim
        cfg.head, cfg.tail = f"{head:g}", f"{tail:g}"
        cfg.enable = head > 0 or tail > 0 or cfg.enable
        self.state.config_changed("trim")
        self.refresh()

    def _add_segment(self) -> None:
        segs = self.state.trim.segments
        segs.append({"start": "0", "end": "", "name": f"Đoạn {len(segs) + 1}"})
        self.state.trim.enable = True
        self.state.config_changed("trim")
        self.refresh()

    def _add_from_preview(self) -> None:
        segs = self.state.trim.segments
        segs.append({"start": f"{self.window.preview.t:.2f}", "end": "", "name": f"Đoạn {len(segs) + 1}"})
        self.state.trim.enable = True
        self.state.config_changed("trim")
        self.refresh()

    def remove_segment(self, index: int) -> None:
        segs = self.state.trim.segments
        if 0 <= index < len(segs):
            del segs[index]
            self.state.config_changed("trim")
            self.refresh()
