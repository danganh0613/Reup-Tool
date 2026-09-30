"""Hướng dẫn sử dụng.

* ``render_blocks`` — vẽ nội dung một chủ đề vào frame bất kỳ (dùng chung).
* ``HelpPane`` — bảng hướng dẫn gắn cạnh bảng tính năng, tự đổi theo module đang mở.
* ``GuideWindow`` — cửa sổ đầy đủ: danh mục + ô tìm, mọi chủ đề.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import button
from app.ui.guide_content import GROUPS, TOPICS, topic
from app.ui.icons import icon
from app.ui.widgets.dialogs import SHORTCUTS

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow


# ── Vẽ nội dung ──────────────────────────────────────────────────────────

def render_blocks(parent, blocks: list, wrap: int, *, big: bool = True) -> None:
    """Đổ các khối nội dung vào ``parent`` (frame dùng grid, cột 0 co giãn)."""
    pad = {"padx": 16, "sticky": "ew"}
    f_body, f_head = (13, 14) if big else (12, 13)
    for kind, data in blocks:
        if kind == "h":
            ctk.CTkLabel(parent, text=data, font=T.font(f_head, "bold"), anchor="w", text_color=T.TEXT).grid(pady=(14, 2), **pad)
        elif kind == "p":
            ctk.CTkLabel(parent, text=data, font=T.font(f_body), anchor="w", justify="left", wraplength=wrap,
                         text_color=T.TEXT_DIM).grid(pady=(6, 2), **pad)
        elif kind == "steps":
            box = ctk.CTkFrame(parent, fg_color="transparent")
            box.grid(pady=(6, 4), **pad)
            box.grid_columnconfigure(1, weight=1)
            for i, step in enumerate(data):
                ctk.CTkLabel(box, text=str(i + 1), width=26, height=26, corner_radius=13, fg_color=T.ACCENT,
                             text_color=T.ON_ACCENT, font=T.font(12, "bold")).grid(row=i, column=0, sticky="n", pady=6)
                ctk.CTkLabel(box, text=step, font=T.font(f_body), anchor="w", justify="left", wraplength=wrap - 40,
                             text_color=T.TEXT).grid(row=i, column=1, sticky="ew", padx=(10, 0), pady=6)
        elif kind == "kv":
            box = ctk.CTkFrame(parent, fg_color=T.BG_CARD, corner_radius=T.RADIUS_CARD)
            box.grid(pady=(8, 4), **pad)
            box.grid_columnconfigure(0, weight=1)
            for i, (name, desc) in enumerate(data):
                ctk.CTkLabel(box, text=name, font=T.font(f_body - 1, "bold"), anchor="w", text_color=T.TEXT, wraplength=wrap - 36,
                             justify="left").grid(row=i * 2, column=0, sticky="ew", padx=12, pady=(10 if i == 0 else 8, 0))
                ctk.CTkLabel(box, text=desc, font=T.font(f_body - 1), anchor="w", justify="left", wraplength=wrap - 36,
                             text_color=T.TEXT_DIM).grid(row=i * 2 + 1, column=0, sticky="ew", padx=12,
                                                         pady=(0, 10 if i == len(data) - 1 else 0))
        elif kind in ("tip", "warn"):
            warn = kind == "warn"
            box = ctk.CTkFrame(parent, fg_color="#2A2410" if warn else T.ACCENT_SOFT, corner_radius=T.RADIUS)
            box.grid(pady=(10, 4), **pad)
            ctk.CTkLabel(box, text="", image=icon("warning" if warn else "bulb", 16, T.WARNING if warn else T.ACCENT)).pack(
                side="left", padx=(10, 4), pady=10, anchor="n")
            ctk.CTkLabel(box, text=("Lưu ý: " if warn else "Mẹo: ") + data, font=T.font(f_body - 1), anchor="w", justify="left",
                         wraplength=wrap - 60, text_color=T.TEXT).pack(side="left", fill="x", expand=True, padx=(0, 10), pady=10)
        elif kind == "shortcuts":
            for title, rows in SHORTCUTS:
                ctk.CTkLabel(parent, text=title, font=T.font(f_head, "bold"), anchor="w").grid(pady=(14, 4), **pad)
                box = ctk.CTkFrame(parent, fg_color=T.BG_CARD, corner_radius=T.RADIUS_CARD)
                box.grid(**pad)
                box.grid_columnconfigure(1, weight=1)
                for i, (k, desc) in enumerate(rows):
                    ctk.CTkLabel(box, text=k, font=T.mono(11), fg_color=T.BG_ELEVATED, corner_radius=6, width=150 if big else 120,
                                 text_color=T.ACCENT).grid(row=i, column=0, sticky="nw", padx=(12, 8), pady=(8 if i == 0 else 3, 8 if i == len(rows) - 1 else 3))
                    ctk.CTkLabel(box, text=desc, font=T.font(f_body - 1), anchor="w", justify="left", wraplength=max(120, wrap - 200),
                                 text_color=T.TEXT_DIM).grid(row=i, column=1, sticky="ew", padx=(0, 12))


def _text_of(t: dict) -> str:
    parts: list[str] = []
    for _kind, data in t["blocks"]:
        if isinstance(data, str):
            parts.append(data)
        elif isinstance(data, list):
            for d in data:
                parts.append(" ".join(d) if isinstance(d, tuple) else str(d))
    return " ".join(parts)


# ── Bảng hướng dẫn gắn cạnh bảng tính năng ───────────────────────────────

class HelpPane(ctk.CTkFrame):
    """Cột hướng dẫn nằm ngay bên phải bảng tính năng; đổi nội dung theo module đang mở."""

    WIDTH = 400

    def __init__(self, master, window: MainWindow) -> None:
        super().__init__(master, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD, width=self.WIDTH)
        self.window = window
        self.key = "start"
        self.pane_width = self.WIDTH
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        head.grid_columnconfigure(0, weight=1)
        self.title = ctk.CTkLabel(head, text="", image=icon("book", 16, T.ACCENT, dy=1), compound="left", font=T.font(13, "medium"),
                                  anchor="w", justify="left", wraplength=self.WIDTH - 130)
        self.title.grid(row=0, column=0, sticky="ew")
        button(head, "", icon_name="fullscreen", variant="ghost", size="sm", command=self._open_full,
               tip="Mở hướng dẫn đầy đủ (cửa sổ riêng, có tìm kiếm)").grid(row=0, column=1, padx=(4, 0))
        button(head, "", icon_name="close", variant="ghost", size="sm", command=self.window.toggle_help,
               tip="Đóng bảng hướng dẫn (Ctrl+H)").grid(row=0, column=2, padx=(2, 0))
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=2, pady=(0, 4))
        self.body.grid_columnconfigure(0, weight=1)
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.nav = ctk.CTkSegmentedButton(foot, values=["Bắt đầu", "Mục này", "Gặp lỗi"], command=self._nav, font=T.font(11), height=28)
        self.nav.pack(fill="x")

    def show(self, key: str) -> None:
        t = topic(key) or topic("start")
        assert t is not None
        self.key = t["key"]
        self.title.configure(text="  " + t["title"])
        for w in self.body.winfo_children():
            w.destroy()
        render_blocks(self.body, t["blocks"], self.pane_width - 60, big=False)
        ctk.CTkLabel(self.body, text="").grid(pady=6)
        try:
            self.body._parent_canvas.yview_moveto(0)
        except Exception:
            pass
        self.nav.set("Bắt đầu" if self.key in ("start", "flow_dub", "flow_short") else "Gặp lỗi" if self.key == "faq" else "Mục này")

    def set_width(self, w: int) -> None:
        """Đổi bề rộng bảng (responsive) và vẽ lại chữ cho vừa dòng."""
        w = int(w)
        if w == self.pane_width:
            return
        self.pane_width = w
        self.configure(width=w)
        self.title.configure(wraplength=w - 130)
        self.show(self.key)

    def _nav(self, v: str) -> None:
        if v == "Bắt đầu":
            self.show("start")
        elif v == "Gặp lỗi":
            self.show("faq")
        else:
            self.show(self.window.current_module_key() or "video")

    def _open_full(self) -> None:
        open_guide(self.window, self.key)


# ── Cửa sổ đầy đủ ───────────────────────────────────────────────────────

_open: GuideWindow | None = None


def open_guide(window: MainWindow, key: str = "start") -> GuideWindow:
    """Mở (hoặc đưa lên trước) cửa sổ hướng dẫn đầy đủ tại chủ đề ``key``."""
    global _open
    if _open is not None and _open.winfo_exists():
        _open.show(key)
        _open.lift()
        _open.focus_force()
        return _open
    _open = GuideWindow(window, key)
    return _open


class GuideWindow(ctk.CTkToplevel):
    WRAP = 640

    def __init__(self, window: MainWindow, key: str = "start") -> None:
        super().__init__(window)
        self.window = window
        self.title("Hướng dẫn sử dụng — Edit Studio")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1100, sw - 60)}x{min(760, sh - 100)}+40+40")
        self.minsize(820, 520)
        self.after(50, self.lift)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._current = key

        side = ctk.CTkFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD, width=330)
        side.grid(row=0, column=0, sticky="nsw", padx=(12, 6), pady=12)
        side.grid_propagate(False)
        side.grid_rowconfigure(2, weight=1)
        side.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(side, text=" Hướng dẫn", image=icon("book", 18, T.ACCENT, dy=1), compound="left",
                     font=T.font(15, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._rebuild_list())
        ctk.CTkEntry(side, textvariable=self.search_var, placeholder_text="Gõ để tìm…", height=36,
                     corner_radius=T.RADIUS).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.list = ctk.CTkScrollableFrame(side, fg_color="transparent")
        self.list.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.list.grid_columnconfigure(0, weight=1)
        foot = ctk.CTkFrame(side, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.startup_var = ctk.BooleanVar(value=bool(window.app_state.settings.show_guide_on_start))
        ctk.CTkCheckBox(foot, text="Tự mở hướng dẫn khi khởi động", variable=self.startup_var, font=T.font(11),
                        command=self._toggle_startup).pack(anchor="w")

        self.body = ctk.CTkScrollableFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD)
        self.body.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        self.body.grid_columnconfigure(0, weight=1)
        self.bind("<Escape>", lambda _e: self.destroy())
        self._rebuild_list()
        self.show(key)

    def _rebuild_list(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        self._buttons.clear()
        q = self.search_var.get().strip().lower()
        row = 0
        for group in GROUPS:
            items = [t for t in TOPICS if t["group"] == group and (not q or q in t["title"].lower() or q in _text_of(t).lower())]
            if not items:
                continue
            ctk.CTkLabel(self.list, text=group.upper(), font=T.font(10, "bold"), text_color=T.TEXT_MUTED, anchor="w").grid(
                row=row, column=0, sticky="ew", padx=8, pady=(10 if row else 2, 2))
            row += 1
            for t in items:
                b = ctk.CTkButton(self.list, text=t["title"], anchor="w", height=36, corner_radius=T.RADIUS,
                                  fg_color="transparent", hover_color=T.BG_HOVER, text_color=T.TEXT_DIM, font=T.font(12),
                                  command=lambda k=t["key"]: self.show(k))
                b.grid(row=row, column=0, sticky="ew", padx=4, pady=1)
                self._buttons[t["key"]] = b
                row += 1
        self._highlight()

    def _highlight(self) -> None:
        for k, b in self._buttons.items():
            active = k == self._current
            b.configure(fg_color=T.ACCENT_SOFT if active else "transparent", text_color=T.ACCENT if active else T.TEXT_DIM,
                        font=T.font(12, "bold" if active else "normal"))

    def _toggle_startup(self) -> None:
        self.window.app_state.settings.show_guide_on_start = bool(self.startup_var.get())
        self.window.app_state.save_settings()

    def show(self, key: str) -> None:
        t = topic(key) or topic("start")
        assert t is not None
        self._current = t["key"]
        self._highlight()
        for w in self.body.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.body, text=t["title"], font=T.font(20, "bold"), anchor="w", wraplength=self.WRAP + 80,
                     justify="left").grid(sticky="ew", padx=16, pady=(16, 0))
        ctk.CTkLabel(self.body, text=t["group"], font=T.font(11), text_color=T.TEXT_MUTED, anchor="w").grid(sticky="ew", padx=16)
        render_blocks(self.body, t["blocks"], self.WRAP, big=True)
        ctk.CTkLabel(self.body, text="").grid(pady=10)
        try:
            self.body._parent_canvas.yview_moveto(0)
        except Exception:
            pass
