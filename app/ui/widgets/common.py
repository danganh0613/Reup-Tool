"""Các widget ghép sẵn dùng lại trong mọi module: hàng slider, option, switch, entry, màu."""

from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Callable
from tkinter import filedialog

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import Popover, Select, button


class Section(ctk.CTkFrame):
    """Khối có tiêu đề; nội dung đặt vào ``self.body``."""

    def __init__(self, master, title: str, *, subtitle: str = "") -> None:
        super().__init__(master, fg_color=T.BG_CARD, corner_radius=T.RADIUS_CARD, border_width=0)
        self.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        ctk.CTkLabel(head, text=title, font=T.font(12, "medium"), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        if subtitle:
            ctk.CTkLabel(head, text=subtitle, font=T.font(11), text_color=T.TEXT_MUTED, anchor="w").pack(
                side="left", padx=(8, 0))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.body.grid_columnconfigure(0, weight=1)


class SliderRow(ctk.CTkFrame):
    def __init__(self, master, label: str, *, from_: float, to: float, step: float = 1, value: float = 0,
                 fmt: str = "{:.0f}", suffix: str = "", on_change: Callable[[float], None] | None = None,
                 on_release: Callable[[float], None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        self.fmt, self.suffix, self.step = fmt, suffix, step
        self.on_change, self.on_release = on_change, on_release
        self._value = value
        ctk.CTkLabel(self, text=label, font=T.font(12), text_color=T.TEXT_DIM, anchor="w", width=120).grid(
            row=0, column=0, sticky="w")
        steps = int(round((to - from_) / step)) if step else None
        self.slider = ctk.CTkSlider(self, from_=from_, to=to, number_of_steps=steps, command=self._slid, height=16)
        self.slider.grid(row=0, column=1, sticky="ew", padx=6)
        self.slider.bind("<ButtonRelease-1>", self._released)
        self.var = tk.StringVar(value=self._text(value))
        self.entry = ctk.CTkEntry(self, textvariable=self.var, width=64, height=32, justify="right", font=T.font(12))
        self.entry.grid(row=0, column=2)
        self.entry.bind("<Return>", self._typed)
        self.entry.bind("<FocusOut>", self._typed)
        self.slider.set(value)

    def _text(self, v: float) -> str:
        return self.fmt.format(v) + self.suffix

    def _slid(self, v: float) -> None:
        v = round(v / self.step) * self.step if self.step else v
        self._value = v
        self.var.set(self._text(v))
        if self.on_change:
            self.on_change(v)

    def _released(self, _e=None) -> None:
        if self.on_release:
            self.on_release(self._value)

    def _typed(self, _e=None) -> None:
        raw = self.var.get().replace(self.suffix, "").replace(",", ".").strip()
        try:
            v = float(raw)
        except ValueError:
            self.var.set(self._text(self._value))
            return
        lo, hi = self.slider.cget("from_"), self.slider.cget("to")
        v = max(lo, min(hi, v))
        self.set(v)
        if self.on_change:
            self.on_change(v)
        if self.on_release:
            self.on_release(v)

    def set(self, v: float) -> None:
        self._value = v
        self.slider.set(v)
        self.var.set(self._text(v))

    def get(self) -> float:
        return self._value


class OptionRow(ctk.CTkFrame):
    def __init__(self, master, label: str, values: list[str], *, value: str | None = None,
                 on_change: Callable[[str], None] | None = None, width: int = 200) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=label, font=T.font(12), text_color=T.TEXT_DIM, anchor="w", width=120).grid(
            row=0, column=0, sticky="w")
        self.menu = Select(self, values or [""], value=value, command=on_change, width=width, height=34)
        self.var = self.menu.var
        self.menu.grid(row=0, column=1, sticky="ew")

    def set(self, v: str) -> None:
        self.var.set(v)

    def get(self) -> str:
        return self.var.get()

    def set_values(self, values: list[str], keep: bool = True) -> None:
        self.menu.set_values(values, keep=keep)


class ComboRow(ctk.CTkFrame):
    """Chọn từ danh sách dài (font, giọng, model): popover có ô lọc; giữ API set/get/set_values."""

    def __init__(self, master, label: str, values: list[str], *, value: str = "",
                 on_change: Callable[[str], None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        self.on_change = on_change
        ctk.CTkLabel(self, text=label, font=T.font(12), text_color=T.TEXT_DIM, anchor="w", width=120).grid(
            row=0, column=0, sticky="w")
        self.combo = Select(self, values or [""], value=value, command=self._changed, width=200, height=34, placeholder="")
        self.var = self.combo.var
        self.combo.grid(row=0, column=1, sticky="ew")
        self._last = value

    def _changed(self, v: str) -> None:
        if v == self._last:
            return
        self._last = v
        if self.on_change:
            self.on_change(v)

    def set(self, v: str) -> None:
        self._last = v
        self.var.set(v)

    def get(self) -> str:
        return self.var.get()

    def set_values(self, values: list[str]) -> None:
        self.combo.set_values(values, keep=True)


class SwitchRow(ctk.CTkFrame):
    def __init__(self, master, label: str, *, value: bool = False, on_change: Callable[[bool], None] | None = None,
                 hint: str = "") -> None:
        super().__init__(master, fg_color="transparent")
        self.var = tk.BooleanVar(value=value)
        self.on_change = on_change
        self.switch = ctk.CTkSwitch(self, text=label, variable=self.var, command=self._toggled, font=T.font(12),
                                    onvalue=True, offvalue=False)
        self.switch.pack(side="left")
        if hint:
            ctk.CTkLabel(self, text=hint, font=T.font(11), text_color=T.TEXT_MUTED).pack(side="left", padx=(8, 0))

    def _toggled(self) -> None:
        if self.on_change:
            self.on_change(bool(self.var.get()))

    def set(self, v: bool) -> None:
        self.var.set(bool(v))

    def get(self) -> bool:
        return bool(self.var.get())


class EntryRow(ctk.CTkFrame):
    def __init__(self, master, label: str, *, value: str = "", placeholder: str = "",
                 on_change: Callable[[str], None] | None = None, browse: str | None = None,
                 secret: bool = False, label_width: int = 120) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        self.on_change = on_change
        self.browse_mode = browse
        if label:
            ctk.CTkLabel(self, text=label, font=T.font(12), text_color=T.TEXT_DIM, anchor="w",
                         width=label_width).grid(row=0, column=0, sticky="w")
        self.var = tk.StringVar(value=value)
        self.entry = ctk.CTkEntry(self, textvariable=self.var, placeholder_text=placeholder, height=32,
                                  font=T.font(12), show="•" if secret else "")
        self.entry.grid(row=0, column=1, sticky="ew")
        self.entry.bind("<Return>", self._changed)
        self.entry.bind("<FocusOut>", self._changed)
        self._last = value
        if browse:
            button(self, "", icon_name="openfolder" if browse in ("dir", "folder") else "folder", size="sm", command=self._browse,
                   tip="Chọn thư mục…" if browse in ("dir", "folder") else "Chọn file…").grid(row=0, column=2, padx=(4, 0))

    def _changed(self, _e=None) -> None:
        v = self.var.get()
        if v == self._last:
            return
        self._last = v
        if self.on_change:
            self.on_change(v)

    def _browse(self) -> None:
        if self.browse_mode == "dir":
            p = filedialog.askdirectory()
        elif self.browse_mode == "exe":
            # macOS/Linux: chương trình không có đuôi .exe → không lọc
            p = filedialog.askopenfilename(filetypes=[("Chương trình", "*.exe"), ("Tất cả", "*.*")]) if sys.platform == "win32" \
                else filedialog.askopenfilename()
        else:
            p = filedialog.askopenfilename()
        if p:
            self.var.set(p)
            self._changed()

    def set(self, v: str) -> None:
        self._last = v
        self.var.set(v)

    def get(self) -> str:
        return self.var.get()


class ColorRow(ctk.CTkFrame):
    def __init__(self, master, label: str, *, value: str = "#FFFFFF",
                 on_change: Callable[[str], None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        self.on_change = on_change
        ctk.CTkLabel(self, text=label, font=T.font(12), text_color=T.TEXT_DIM, anchor="w", width=120).grid(
            row=0, column=0, sticky="w")
        self.var = tk.StringVar(value=value)
        self.entry = ctk.CTkEntry(self, textvariable=self.var, height=32, width=90, font=T.mono(12))
        self.entry.grid(row=0, column=1, sticky="w")
        self.entry.bind("<Return>", lambda _e: self._typed())
        self.entry.bind("<FocusOut>", lambda _e: self._typed())
        self.swatch = ctk.CTkButton(self, text="", width=36, height=32, fg_color=value, hover_color=value,
                                    border_width=1, border_color=T.BORDER, command=self._pick)
        self.swatch.grid(row=0, column=2, padx=(6, 0))
        self._last = value

    def _apply(self, v: str) -> None:
        v = v.strip()
        if not v.startswith("#"):
            v = "#" + v
        if len(v) != 7:
            self.var.set(self._last)
            return
        try:
            int(v[1:], 16)
        except ValueError:
            self.var.set(self._last)
            return
        v = v.upper()
        self._last = v
        self.var.set(v)
        self.swatch.configure(fg_color=v, hover_color=v)
        if self.on_change:
            self.on_change(v)

    def _typed(self) -> None:
        if self.var.get().upper() != self._last:
            self._apply(self.var.get())

    def _pick(self) -> None:
        """Mở bộ chọn màu kiểu Apple; màu đổi tới đâu áp dụng tới đó, Huỷ thì trả về màu cũ."""
        from app.ui.widgets.color_picker import ColorPicker

        self._picker = ColorPicker(self.swatch, self._last, on_change=self._apply)

    def set(self, v: str) -> None:
        self._last = v.upper()
        self.var.set(self._last)
        self.swatch.configure(fg_color=self._last, hover_color=self._last)

    def get(self) -> str:
        return self._last


class ButtonRow(ctk.CTkFrame):
    """Hàng nút chia đều. Mỗi phần tử: (chữ, callback, style) — style là dict từ T.*_button() hoặc None."""

    def __init__(self, master, buttons: list[tuple[str, Callable[[], None], dict | None]]) -> None:
        super().__init__(master, fg_color="transparent")
        from app.ui.icons import split_icon

        for i, (text, cmd, style) in enumerate(buttons):
            self.grid_columnconfigure(i, weight=1)
            label, ic = split_icon(text)
            st = dict(style or T.secondary_button())
            variant = "primary" if st.get("fg_color") == T.ACCENT else "destructive" if st.get("fg_color") in (T.DANGER, T.DANGER_SOFT) \
                else "ghost" if st.get("fg_color") == "transparent" else "secondary"
            b = button(self, label.strip() if ic else text, icon_name=ic, variant=variant, size="sm", command=cmd, width=10)
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0))


class StatusLabel(ctk.CTkLabel):
    def __init__(self, master, text: str = "") -> None:
        super().__init__(master, text=text, font=T.font(11), text_color=T.TEXT_MUTED, anchor="w",
                         justify="left", wraplength=360)

    def info(self, text: str) -> None:
        self.configure(text=text, text_color=T.TEXT_DIM)

    def ok(self, text: str) -> None:
        self.configure(text=text, text_color=T.SUCCESS)

    def warn(self, text: str) -> None:
        self.configure(text=text, text_color=T.WARNING)

    def error(self, text: str) -> None:
        self.configure(text=text, text_color=T.DANGER)


def hint(master, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text, font=T.font(11), text_color=T.TEXT_MUTED, anchor="w", justify="left",
                        wraplength=370)


def pack_rows(parent, rows: list, pady: int = 3) -> None:
    for r in rows:
        r.pack(fill="x", pady=pady)


class TimeEntry(ctk.CTkFrame):
    """Ô nhập mốc thời gian (giây hoặc HH:MM:SS) + nút lấy mốc đang xem trước."""

    def __init__(self, master, label: str, *, value: str = "0", on_change: Callable[[str], None] | None = None,
                 pick_time: Callable[[], float] | None = None, label_width: int = 120) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        self.on_change = on_change
        self.pick_time = pick_time
        if label:
            ctk.CTkLabel(self, text=label, font=T.font(12), text_color=T.TEXT_DIM, anchor="w",
                         width=label_width).grid(row=0, column=0, sticky="w")
        self.var = tk.StringVar(value=value)
        self.entry = ctk.CTkEntry(self, textvariable=self.var, height=32, font=T.mono(12), width=110)
        self.entry.grid(row=0, column=1, sticky="w")
        self.entry.bind("<Return>", self._changed)
        self.entry.bind("<FocusOut>", self._changed)
        self._last = value
        if pick_time is not None:
            button(self, "Đang xem", icon_name="clock", size="sm", command=self._pick, width=100).grid(row=0, column=2, padx=(6, 0))

    def _changed(self, _e=None) -> None:
        v = self.var.get().strip()
        if v == self._last:
            return
        self._last = v
        if self.on_change:
            self.on_change(v)

    def _pick(self) -> None:
        if self.pick_time is None:
            return
        t = self.pick_time()
        self.var.set(f"{t:.2f}")
        self._changed()

    def set(self, v: str) -> None:
        self._last = v
        self.var.set(v)

    def get(self) -> str:
        return self.var.get().strip()


# ── Chip (khung xem trước) = Select kiểu chip ────────────────────────────
PopupMenu = Popover


class Chip(Select):
    """Chip trên khung xem trước: icon · giá trị ngắn · chevron; ``get_value``/``set_value`` nối với state."""

    def __init__(self, master, *, icon_name: str, values: list[str], get_value: Callable[[], str],
                 set_value: Callable[[str], None], short: Callable[[str], str] | None = None,
                 labels: dict[str, str] | None = None, menu_width: int = 220, tip: str | None = None) -> None:
        self.get_value, self.set_value = get_value, set_value
        super().__init__(master, values, value=get_value(), command=set_value, width=120, height=34, labels=labels,
                         short=short, icon_name=icon_name, chip=True, menu_width=menu_width, tip=tip)

    def refresh(self) -> None:
        v = self.get_value()
        if v != self.var.get():
            self.var.set(v)
        else:
            self._render()

    def _open(self) -> None:      # tương thích mã cũ
        self.open()
