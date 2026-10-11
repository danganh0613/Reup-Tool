"""Cửa sổ «Kiểm tra máy»: liệt kê cái có / cái thiếu và chỉ nút bổ sung (chạy nền, có nút sao chép)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from app.config import theme as T
from app.config.paths import APP_NAME
from app.services.selftest import Check, as_text, run_checks, summary
from app.ui.icons import icon
from app.ui.widgets.ui import button

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

_COLOR = {"ok": "SUCCESS", "warn": "WARNING", "bad": "DANGER"}
_ICON = {"ok": "check", "warn": "warning", "bad": "close"}


class SelfCheckWindow(ctk.CTkToplevel):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.title(f"Kiểm tra máy — {APP_NAME}")
        self.configure(fg_color=T.BG)
        w, h = 720, 600
        x = window.winfo_rootx() + max(0, (window.winfo_width() - w) // 2)
        y = window.winfo_rooty() + max(0, (window.winfo_height() - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(560, 400)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkLabel(head, text=" Kiểm tra máy", image=icon("gpu", 18, T.ACCENT, dy=1), compound="left", font=T.font(16, "medium"),
                     anchor="w").pack(side="left")
        self.sum_label = ctk.CTkLabel(head, text="Đang kiểm tra…", font=T.font(12), text_color=T.TEXT_DIM, anchor="e")
        self.sum_label.pack(side="right")
        self.body = ctk.CTkScrollableFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD)
        self.body.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        self.body.grid_columnconfigure(0, weight=1)
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 14))
        ctk.CTkLabel(foot, text="Mục «tuỳ chọn» thiếu vẫn làm được việc chính. Bấm «Mở chỗ cài» để bổ sung.",
                     font=T.font(11), text_color=T.TEXT_MUTED, anchor="w").pack(side="left")
        button(foot, "Đóng", size="sm", command=self.destroy).pack(side="right")
        self.copy_btn = button(foot, "Sao chép kết quả", icon_name="list", size="sm", command=self._copy, state="disabled")
        self.copy_btn.pack(side="right", padx=6)
        self.rerun_btn = button(foot, "Kiểm tra lại", icon_name="refresh", size="sm", command=self._start, state="disabled")
        self.rerun_btn.pack(side="right", padx=6)
        self.checks: list[Check] = []
        self._start()

    def _start(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        self.sum_label.configure(text="Đang kiểm tra…")
        self.rerun_btn.configure(state="disabled")
        self.copy_btn.configure(state="disabled")
        st = self.window.app_state.settings
        self.window.run_bg(lambda: run_checks(st, progress=lambda n: self.window.ui_call(lambda: self.sum_label.configure(text=f"Đang kiểm tra: {n}…"))),
                           self._done)

    def _done(self, result, error) -> None:
        if not self.winfo_exists():
            return
        if error or not result:
            self.sum_label.configure(text=f"Lỗi kiểm tra: {error}", text_color=T.DANGER)
            self.rerun_btn.configure(state="normal")
            return
        self.checks = list(result)
        self.sum_label.configure(text=summary(self.checks), text_color=T.TEXT)
        for i, c in enumerate(self.checks):
            self._row(i, c)
        self.rerun_btn.configure(state="normal")
        self.copy_btn.configure(state="normal")

    def _row(self, i: int, c: Check) -> None:
        row = ctk.CTkFrame(self.body, fg_color=T.BG_CARD, corner_radius=T.RADIUS)
        row.grid(row=i, column=0, sticky="ew", padx=8, pady=3)
        row.grid_columnconfigure(1, weight=1)
        color = getattr(T, _COLOR[c.status])
        ctk.CTkLabel(row, text="", image=icon(_ICON[c.status], 16, color), width=28).grid(row=0, column=0, rowspan=2, padx=(10, 4), pady=8, sticky="n")
        name = c.name + ("  · tuỳ chọn" if c.optional and c.status != "ok" else "")
        ctk.CTkLabel(row, text=name, font=T.font(13, "medium"), text_color=T.TEXT, anchor="w").grid(row=0, column=1, sticky="ew", pady=(8, 0))
        if c.page and c.status != "ok":
            # nút làm luôn: mở đúng trang Cài đặt có nút Cài / ô nhập key (trước: chỉ có chữ chỉ đường)
            button(row, "Nhập key…" if c.page == "cloud" else "Mở chỗ cài", icon_name="settings", size="sm",
                   variant="secondary" if c.optional else "primary",
                   command=lambda p=c.page: self.window.open_settings(tab=p)).grid(row=0, column=2, rowspan=2, padx=(4, 10))
        ctk.CTkLabel(row, text=c.detail, font=T.font(12), text_color=T.TEXT_DIM, anchor="w", justify="left", wraplength=600).grid(
            row=1, column=1, sticky="ew", pady=(0, 8 if not c.fix else 2))
        if c.fix:
            ctk.CTkLabel(row, text="→ " + c.fix, font=T.font(12), text_color=color, anchor="w", justify="left", wraplength=600).grid(
                row=2, column=1, sticky="ew", pady=(0, 8))

    def _copy(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(as_text(self.checks))
            self.sum_label.configure(text="Đã sao chép vào clipboard")
        except Exception:
            pass
