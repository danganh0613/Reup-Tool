"""Các cửa sổ phụ nhỏ: Nhật ký, Trung tâm thông báo, Phím tắt."""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import button
from app.ui.icons import EMOJI_ICONS, icon

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow


class LogWindow(ctk.CTkToplevel):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.title("Nhật ký — Edit Studio")
        self.geometry("980x600")
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.box = ctk.CTkTextbox(self, font=T.mono(11), wrap="word")
        self.box.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        button(foot, "Copy", icon_name="list", size="sm", command=self._copy).pack(side="left")
        button(foot, "Xoá", icon_name="trash", size="sm", command=self._clear).pack(side="left", padx=6)
        ctk.CTkButton(foot, text="Đóng", width=90, height=32, command=self.destroy, **T.primary_button()).pack(side="right")
        self.box.insert("1.0", "\n".join(window.log_lines) + "\n")
        self.box.see("end")
        self.box.configure(state="disabled")

    def append(self, line: str) -> None:
        try:
            self.box.configure(state="normal")
            self.box.insert("end", line + "\n")
            self.box.see("end")
            self.box.configure(state="disabled")
        except Exception:
            pass

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append("\n".join(self.window.log_lines))

    def _clear(self) -> None:
        self.window.log_lines.clear()
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.configure(state="disabled")


class NotificationCenter(ctk.CTkToplevel):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.title("Thông báo — Edit Studio")
        self.geometry("640x700")
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=10)
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        ctk.CTkLabel(head, text=" Trung tâm thông báo", image=icon(EMOJI_ICONS["🔔"], 14, dy=1), compound="left", font=T.font(15, "bold"), anchor="w").pack(side="left", padx=12, pady=10)
        ctk.CTkButton(head, text="Xoá hết", width=80, height=32, command=self._clear, **T.secondary_button()).pack(side="right", padx=10)
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew", padx=10)
        self.list.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(self, text="Đóng", width=100, height=32, command=self.destroy, **T.primary_button()).grid(
            row=2, column=0, sticky="e", padx=10, pady=10)
        self.refresh()

    def refresh(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        items = list(reversed(self.window.notifications))
        if not items:
            ctk.CTkLabel(self.list, text="Chưa có thông báo. Kết quả render, lỗi và cảnh báo sẽ hiện ở đây.",
                         text_color=T.TEXT_MUTED, font=T.font(12)).pack(pady=30)
            return
        for n in items:
            card = ctk.CTkFrame(self.list, fg_color=T.BG_CARD, corner_radius=8)
            card.pack(fill="x", pady=4)
            color = {"ok": T.SUCCESS, "error": T.DANGER, "warn": T.WARNING}.get(n.get("kind", "info"), T.INFO)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(8, 0))
            ctk.CTkLabel(top, text=n.get("title", ""), font=T.font(12, "bold"), text_color=color, anchor="w").pack(side="left")
            ctk.CTkLabel(top, text=n.get("time", ""), font=T.font(10), text_color=T.TEXT_MUTED).pack(side="right")
            ctk.CTkLabel(card, text=n.get("body", ""), font=T.font(11), text_color=T.TEXT_DIM, anchor="w", justify="left",
                         wraplength=560).pack(fill="x", padx=10, pady=(2, 8))

    def _clear(self) -> None:
        self.window.notifications.clear()
        self.refresh()


SHORTCUTS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Điều khiển timeline & dựng video", [
        ("Space", "Phát / Dừng video xem trước"),
        ("J / K / L", "Tua lùi 3s · Dừng · Tua tới 3s"),
        ("I / O  hoặc  [ / ]", "Cắt bỏ phần trước / sau chỗ đang xem (đặt điểm đầu · điểm cuối)"),
        (", / .", "Lùi / tiến 1 khung hình"),
        ("← / →", "Tua lùi / tiến 3 giây"),
        ("M", "Bật / tắt âm thanh xem trước"),
        ("Home / End", "Về đầu · tới cuối video"),
        ("F12 / F", "Xem trước toàn màn hình"),
        ("Ctrl + / − / 0", "Phóng to · thu nhỏ · vừa khung"),
    ]),
    ("Biên tập & công cụ", [
        ("V", "Công cụ chọn / kéo canvas"),
        ("T", "Công cụ khung phụ đề (kéo, đổi cỡ)"),
        ("Shift + T", "Bật / tắt hiển thị phụ đề trên xem trước"),
        ("B", "Công cụ che mờ (vẽ / kéo vùng)"),
        ("Del / Backspace", "Xoá vùng đang chọn trên khung (vùng che, khung phụ đề) · Ctrl+Z hoàn tác"),
        ("Ctrl + E", "Mở trình sửa SRT"),
        ("Ctrl + H", "Mở / đóng bảng Hướng dẫn (đúng chủ đề tab đang xem)"),
    ]),
    ("Lịch sử & xuất bản", [
        ("Ctrl + Z", "Hoàn tác (vùng che / khung phụ đề / xóa)"),
        ("Ctrl + Shift + Z / Ctrl + Y", "Làm lại"),
        ("Ctrl + S", "Lưu cấu hình ngay (app vẫn tự lưu)"),
        ("Ctrl + R", "Nút chính trên cùng: Chuẩn bị / Tiếp tục / Xuất"),
        ("Ctrl + O", "Nạp video (ở tab nào cũng được)"),
        ("Esc ×2", "Dừng tất cả (bấm 2 lần trong 3 giây — tránh lỡ tay)"),
    ]),
    ("Duyệt", [
        ("Ctrl + Enter", "Duyệt video đang xem, sang video chờ duyệt kế tiếp"),
        ("Ctrl + ↑ / ↓", "Video trước / sau"),
    ]),
    ("Chuyển module nhanh", [
        ("F1 – F3", "Video · Lời thoại · Lồng tiếng"),
        ("F4 – F7", "Cắt · Nhạc · Logo · Hiệu ứng"),
        ("F8 – F9", "Che mờ · Dựng Truyện AI (nút «Thêm» trên thanh bên)"),
    ]),
]
if sys.platform == "darwin":                 # macOS: phím ⌘ thay Ctrl (Ctrl vẫn dùng được)
    SHORTCUTS = [(t, [(k.replace("Ctrl", "⌘"), d) for k, d in rows]) for t, rows in SHORTCUTS]


class ShortcutsWindow(ctk.CTkToplevel):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.title("Phím tắt — Edit Studio")
        self.geometry("980x720")
        self.after(50, self.lift)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grid_columnconfigure((0, 1), weight=1)
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=10)
        body.grid_columnconfigure((0, 1), weight=1)
        for i, (title, rows) in enumerate(SHORTCUTS):
            card = ctk.CTkFrame(body, fg_color=T.BG_CARD, corner_radius=10)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            ctk.CTkLabel(card, text=title, font=T.font(13, "bold"), text_color=T.ACCENT, anchor="w").pack(fill="x", padx=12, pady=(10, 4))
            for key, desc in rows:
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=12, pady=2)
                ctk.CTkLabel(row, text=key, font=T.mono(11), fg_color=T.BG_ELEVATED, corner_radius=6, width=150,
                             text_color=T.INFO).pack(side="left")
                ctk.CTkLabel(row, text=desc, font=T.font(11), anchor="w", text_color=T.TEXT_DIM).pack(side="left", padx=10)
            ctk.CTkLabel(card, text="").pack(pady=2)


def now_str() -> str:
    return time.strftime("%d/%m/%Y %H:%M")
