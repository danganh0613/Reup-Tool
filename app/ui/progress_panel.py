"""Thanh tiến trình dưới cùng: 5 bước render, phần trăm, trạng thái, nút xem log."""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from app.config import theme as T

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

STAGES = ["1. Chuẩn bị", "2. Tạo TTS", "3. Ghép timeline", "4. Bake hình ảnh", "5. Ghép MP4"]


class ProgressPanel(ctk.CTkFrame):
    def __init__(self, master, window: MainWindow) -> None:
        super().__init__(master, fg_color=T.BG_PANEL, corner_radius=10)
        self.window = window
        self.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 2))
        ctk.CTkLabel(head, text="TIẾN TRÌNH", font=T.font(12, "bold"), text_color=T.TEXT_DIM).pack(side="left")
        self.percent = ctk.CTkLabel(head, text="0%", font=T.font(14, "bold"), text_color=T.ACCENT)
        self.percent.pack(side="right")
        ctk.CTkButton(head, text="Xem log", width=80, height=32, command=window.open_log, **T.secondary_button()).pack(
            side="right", padx=(0, 10))

        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.grid(row=1, column=0, sticky="ew", padx=12)
        self.chips: list[ctk.CTkLabel] = []
        for i, name in enumerate(STAGES):
            chips.grid_columnconfigure(i, weight=1)
            lbl = ctk.CTkLabel(chips, text=name, font=T.font(11), fg_color=T.BG_ELEVATED, corner_radius=6,
                               text_color=T.TEXT_DIM, height=24)
            lbl.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
            self.chips.append(lbl)

        self.bar = ctk.CTkProgressBar(self, height=8)
        self.bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 4))
        self.bar.set(0)
        self.status = ctk.CTkLabel(self, text="Sẵn sàng — bấm Render để bắt đầu", font=T.font(12, "bold"), anchor="w")
        self.status.grid(row=3, column=0, sticky="ew", padx=12)
        self.hint = ctk.CTkLabel(self, text="Chỉnh che mờ · phím B", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w")
        self.hint.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._stage = -1

    def set_stage(self, index: int) -> None:
        if index == self._stage:
            return
        self._stage = index
        for i, lbl in enumerate(self.chips):
            if i == index:
                lbl.configure(fg_color=T.ACCENT, text_color=T.ON_ACCENT)
            elif 0 <= index and i < index:
                lbl.configure(fg_color=T.ACCENT_SOFT, text_color=T.TEXT)
            else:
                lbl.configure(fg_color=T.BG_ELEVATED, text_color=T.TEXT_DIM)

    def set_progress(self, p: float) -> None:
        p = max(0.0, min(1.0, p))
        self.bar.set(p)
        self.percent.configure(text=f"{int(p * 100)}%")

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def set_hint(self, text: str) -> None:
        self.hint.configure(text=text)

    def reset(self, text: str = "Sẵn sàng — bấm Render để bắt đầu") -> None:
        self.set_stage(-1)
        self.set_progress(0.0)
        self.set_status(text)


def stage_index_for(message: str) -> int:
    """Suy ra bước từ thông điệp tiến độ của pipeline."""
    m = (message or "").lower()
    if any(k in m for k in ("đọc thông tin", "nhận diện", "dịch", "chuẩn bị")):
        return 0
    if "giọng" in m:
        return 1
    if "timeline" in m or "ghép" in m:
        return 2
    if "render" in m or "bake" in m:
        return 3
    if "xong" in m or "mp4" in m:
        return 4
    return 0
