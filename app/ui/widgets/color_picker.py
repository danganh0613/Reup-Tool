"""Bộ chọn màu kiểu Apple «Colors» (thay hộp thoại Windows cũ).

Ba tab: **Lưới** (bảng 12×12 màu chuẩn Apple) · **Phổ màu** (kéo trên mặt phẳng hue/độ bão hoà) · **Thanh trượt** (R/G/B + Hex).
Ống hút màu lấy màu bất kỳ trên màn hình. Hàng «màu gần đây» nhớ qua các lần mở (cache/recent_colors.json).
Màu đổi tới đâu áp dụng tới đó (``on_change``); «Huỷ» trả về màu ban đầu. Không chặn vòng lặp Tk (không ``wait_window``).
"""

from __future__ import annotations

import colorsys
import json
import tkinter as tk
from collections.abc import Callable

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from app.config import theme as T
from app.config.paths import CACHE_DIR
from app.ui.icons import icon
from app.ui.widgets.ui import Tooltip, _scale, button

# Bảng màu «Grid» của Apple (12 cột × 12 hàng)
APPLE_GRID = [
    "#FFFFFF", "#EBEBEB", "#D6D6D6", "#C2C2C2", "#ADADAD", "#999999", "#858585", "#707070", "#5C5C5C", "#474747", "#333333", "#000000",
    "#003366", "#336699", "#3366CC", "#003399", "#000099", "#0000CC", "#000066", "#333366", "#663399", "#660099", "#330066", "#330033",
    "#006699", "#0099CC", "#0066CC", "#0033CC", "#0000FF", "#3333FF", "#333399", "#6633CC", "#9933CC", "#9900CC", "#6600CC", "#660066",
    "#0099CC", "#00CCFF", "#0099FF", "#0066FF", "#3366FF", "#6666FF", "#6666CC", "#9966CC", "#CC66FF", "#CC33FF", "#9900FF", "#990099",
    "#33CCCC", "#66FFFF", "#33CCFF", "#3399FF", "#6699FF", "#9999FF", "#9999CC", "#CC99FF", "#FF99FF", "#FF66FF", "#CC00FF", "#CC00CC",
    "#66CCCC", "#99FFFF", "#66CCFF", "#6699FF", "#99CCFF", "#CCCCFF", "#CC99CC", "#FFCCFF", "#FF99FF", "#FF66FF", "#FF33FF", "#FF00FF",
    "#99CCCC", "#CCFFFF", "#99CCFF", "#9999FF", "#CCCCFF", "#FFFFFF", "#FFCCFF", "#FF99FF", "#FF66FF", "#FF00FF", "#CC00CC", "#990099",
    "#CCFFCC", "#FFFFCC", "#FFFF99", "#FFFF66", "#FFFF33", "#FFFF00", "#FFCC00", "#FF9900", "#FF6600", "#FF3300", "#FF0000", "#CC0000",
    "#99FF99", "#CCFF99", "#CCCC66", "#CCCC33", "#CCCC00", "#CC9900", "#CC6600", "#CC3300", "#CC0000", "#990000", "#660000", "#330000",
    "#66FF66", "#99FF66", "#99CC66", "#99CC33", "#999900", "#996600", "#993300", "#990000", "#660000", "#330000", "#000000", "#000000",
    "#33FF33", "#66FF33", "#66CC33", "#669933", "#666600", "#663300", "#660000", "#330000", "#000000", "#000000", "#000000", "#000000",
    "#00FF00", "#33FF00", "#33CC00", "#339900", "#336600", "#333300", "#330000", "#000000", "#000000", "#000000", "#000000", "#000000",
]
COLS, ROWS = 12, 12
DEFAULT_RECENT = ["#000000", "#FFFFFF", "#FF3B30", "#FF9500", "#FFCC00", "#4CD964", "#5AC8FA", "#007AFF", "#5856D6", "#FF2D55"]
RECENT_FILE = CACHE_DIR / "recent_colors.json"
TABS = ["Lưới", "Phổ màu", "Thanh trượt"]


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return "#%02X%02X%02X" % (max(0, min(255, round(r))), max(0, min(255, round(g))), max(0, min(255, round(b))))


def normalize_hex(v: str, fallback: str = "#FFFFFF") -> str:
    v = (v or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        int(v, 16)
        return ("#" + v.upper()) if len(v) == 6 else fallback
    except ValueError:
        return fallback


def load_recent() -> list[str]:
    try:
        data = json.loads(RECENT_FILE.read_text(encoding="utf-8"))
        out = [normalize_hex(c) for c in data if isinstance(c, str)]
        return out[:12] or list(DEFAULT_RECENT)
    except Exception:
        return list(DEFAULT_RECENT)


def save_recent(colors: list[str]) -> None:
    try:
        RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECENT_FILE.write_text(json.dumps(colors[:12]), encoding="utf-8")
    except Exception:
        pass


class ColorPicker(ctk.CTkToplevel):
    """``ColorPicker(master, "#FF0000", on_change=fn)`` — ``fn(hex)`` gọi mỗi khi màu đổi; ``on_close(hex | None)`` khi đóng."""

    W = 300          # bề rộng nội dung (px logic)
    AREA_H = 200

    def __init__(self, master, initial: str = "#007AFF", *, on_change: Callable[[str], None] | None = None,
                 on_close: Callable[[str | None], None] | None = None, title: str = "Màu") -> None:
        super().__init__(master)
        self.k = _scale(master)
        self.on_change, self.on_close = on_change, on_close
        self.initial = normalize_hex(initial)
        self.hex = self.initial
        self.recent = load_recent()
        self._images: dict[str, ImageTk.PhotoImage] = {}
        self._drag = False
        self._closed = False
        self.title(title)
        self.configure(fg_color=T.BG_PANEL)
        self.resizable(False, False)
        try:
            self.attributes("-toolwindow", True)      # thanh tiêu đề gọn, chỉ có nút đóng
        except tk.TclError:
            pass
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._build()
        self._place(master)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        self.after(50, self._grab)

    # ── Dựng ────────────────────────────────────────────────────────────
    def _build(self) -> None:
        pad = 14
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(padx=pad, pady=(10, pad))
        head = ctk.CTkFrame(root, fg_color="transparent", width=self.W)
        head.pack(fill="x", pady=(0, 8))
        button(head, "", icon_name="eyedropper", variant="ghost", size="sm", command=self._eyedrop, icon_color=T.ACCENT,
               tip="Ống hút: lấy màu bất kỳ trên màn hình (Esc để thôi)").pack(side="left")
        ctk.CTkLabel(head, text="Màu", font=T.font(15, "medium")).pack(side="left", expand=True)
        button(head, "", icon_name="close", variant="ghost", size="sm", command=self._cancel, icon_color=T.TEXT_DIM,
               tip="Đóng, giữ màu ban đầu (Esc)").pack(side="right")

        self.tabs = ctk.CTkSegmentedButton(root, values=TABS, command=self._switch, font=T.font(12, "medium"), height=30,
                                           corner_radius=9, border_width=2, fg_color=T.BG_INPUT, unselected_color=T.BG_INPUT,
                                           unselected_hover_color=T.BG_HOVER, selected_color=T.BG_ELEVATED,
                                           selected_hover_color=T.BG_ELEVATED, text_color=T.TEXT, width=self.W)
        self.tabs.pack(fill="x", pady=(0, 10))
        self.area = ctk.CTkFrame(root, fg_color="transparent", width=self.W, height=self.AREA_H)
        self.area.pack(fill="x")
        self.area.pack_propagate(False)
        self.pages = {"Lưới": self._build_grid(), "Phổ màu": self._build_spectrum(), "Thanh trượt": self._build_sliders()}
        self._build_recent(root)
        foot = ctk.CTkFrame(root, fg_color="transparent")
        foot.pack(fill="x", pady=(12, 0))
        button(foot, "Xong", variant="primary", size="sm", command=self._ok, tip="Dùng màu này (Enter)").pack(side="right")
        button(foot, "Huỷ", variant="ghost", size="sm", command=self._cancel).pack(side="right", padx=(0, 6))
        self.tabs.set("Lưới")
        self._switch("Lưới")
        self._sync(source="init")

    def _canvas(self, parent, h: int) -> tk.Canvas:
        k = self.k
        c = tk.Canvas(parent, width=round(self.W * k), height=round(h * k), bg=T.BG_PANEL, highlightthickness=0, bd=0)
        return c

    # Lưới
    def _build_grid(self) -> tk.Canvas:
        c = self._canvas(self.area, self.AREA_H)
        c.configure(cursor="hand2")
        self._grid_cells: list[int] = []
        k = self.k
        W, H = round(self.W * k), round(self.AREA_H * k)
        gap = max(1, round(k))
        cw, ch = (W - gap * (COLS + 1)) / COLS, (H - gap * (ROWS + 1)) / ROWS
        self._grid_geom = (gap, cw, ch)
        c.create_rectangle(0, 0, W, H, fill=T.BORDER_SOFT, outline="")
        for i, col in enumerate(APPLE_GRID[: COLS * ROWS]):
            r, q = divmod(i, COLS)
            x0, y0 = gap + q * (cw + gap), gap + r * (ch + gap)
            self._grid_cells.append(c.create_rectangle(x0, y0, x0 + cw, y0 + ch, fill=col, outline=""))
        self._grid_sel = c.create_rectangle(0, 0, 0, 0, outline="#FFFFFF", width=max(2, round(2 * k)), state="hidden")
        c.bind("<Button-1>", self._grid_click)
        c.bind("<B1-Motion>", self._grid_click)
        return c

    def _grid_click(self, e) -> None:
        gap, cw, ch = self._grid_geom
        q, r = int((e.x - gap) // (cw + gap)), int((e.y - gap) // (ch + gap))
        if 0 <= q < COLS and 0 <= r < ROWS:
            self._set_hex(APPLE_GRID[r * COLS + q], source="grid")

    def _grid_mark(self) -> None:
        c = self.pages["Lưới"]
        gap, cw, ch = self._grid_geom
        try:
            i = [x.upper() for x in APPLE_GRID].index(self.hex)
        except ValueError:
            c.itemconfigure(self._grid_sel, state="hidden")
            return
        r, q = divmod(i, COLS)
        x0, y0 = gap + q * (cw + gap), gap + r * (ch + gap)
        c.coords(self._grid_sel, x0 + 1, y0 + 1, x0 + cw - 1, y0 + ch - 1)
        # viền trắng trên ô sáng khó thấy → đổi sang đen
        rr, gg, bb = _hex_to_rgb(self.hex)
        c.itemconfigure(self._grid_sel, state="normal", outline="#000000" if (rr * 299 + gg * 587 + bb * 114) / 1000 > 186 else "#FFFFFF")
        c.tag_raise(self._grid_sel)

    # Phổ màu
    def _build_spectrum(self) -> tk.Canvas:
        c = self._canvas(self.area, self.AREA_H)
        c.configure(cursor="crosshair")
        k = self.k
        W, H = round(self.W * k), round(self.AREA_H * k)
        # HSV với V = 1: màu = trắng pha dần sang màu hue theo độ bão hoà (tuyến tính) → 1 dải hue rồi blend từng dòng
        strip = Image.new("RGB", (W, 1))
        sp = strip.load()
        for x in range(W):
            r, g, b = colorsys.hsv_to_rgb(x / max(1, W - 1), 1.0, 1.0)
            sp[x, 0] = (int(r * 255), int(g * 255), int(b * 255))
        white = Image.new("RGB", (W, 1), (255, 255, 255))
        img = Image.new("RGB", (W, H))
        for y in range(H):
            img.paste(Image.blend(white, strip, 1 - y / max(1, H - 1)), (0, y))
        # bo góc: mặt nạ
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), radius=round(12 * k), fill=255)
        bg = Image.new("RGB", (W, H), T.BG_PANEL)
        bg.paste(img, (0, 0), mask)
        self._images["spectrum"] = ImageTk.PhotoImage(bg)
        c.create_image(0, 0, image=self._images["spectrum"], anchor="nw")
        r = round(8 * k)
        self._spec_marker = c.create_oval(0, 0, 2 * r, 2 * r, outline="#FFFFFF", width=max(2, round(2 * k)), fill=self.hex)
        self._spec_r = r
        c.bind("<Button-1>", self._spec_move)
        c.bind("<B1-Motion>", self._spec_move)
        return c

    def _spec_move(self, e) -> None:
        k = self.k
        W, H = round(self.W * k), round(self.AREA_H * k)
        x, y = max(0, min(W - 1, e.x)), max(0, min(H - 1, e.y))
        hue, s = x / (W - 1), 1 - y / (H - 1)
        r, g, b = colorsys.hsv_to_rgb(hue, s, 1.0)
        self._set_hex(_rgb_to_hex(r * 255, g * 255, b * 255), source="spectrum")

    def _spec_mark(self) -> None:
        c = self.pages["Phổ màu"]
        k = self.k
        W, H = round(self.W * k), round(self.AREA_H * k)
        r, g, b = (v / 255 for v in _hex_to_rgb(self.hex))
        hue, s, _v = colorsys.rgb_to_hsv(r, g, b)
        x, y = hue * (W - 1), (1 - s) * (H - 1)
        rr = self._spec_r
        c.coords(self._spec_marker, x - rr, y - rr, x + rr, y + rr)
        c.itemconfigure(self._spec_marker, fill=self.hex)

    # Thanh trượt
    def _build_sliders(self) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self.area, fg_color="transparent", width=self.W, height=self.AREA_H)
        self._slider_vars: dict[str, tk.StringVar] = {}
        self._slider_canvas: dict[str, tk.Canvas] = {}
        for key, name, end in (("r", "Đỏ", "#FF0000"), ("g", "Lục", "#00FF00"), ("b", "Lam", "#0000FF")):
            row = ctk.CTkFrame(f, fg_color="transparent")
            row.pack(fill="x", pady=(6, 6))
            ctk.CTkLabel(row, text=name, width=36, anchor="w", font=T.font(11, "medium"), text_color=T.TEXT_DIM).pack(side="left")
            var = tk.StringVar(value="0")
            ent = ctk.CTkEntry(row, textvariable=var, width=52, height=26, font=T.mono(11), justify="center")
            ent.pack(side="right")
            ent.bind("<Return>", lambda _e, kk=key: self._slider_typed(kk))
            ent.bind("<FocusOut>", lambda _e, kk=key: self._slider_typed(kk))
            self._slider_vars[key] = var
            c = tk.Canvas(row, height=round(22 * self.k), bg=T.BG_PANEL, highlightthickness=0, bd=0, cursor="hand2")
            c.pack(side="left", fill="x", expand=True, padx=(8, 10))
            c.bind("<Configure>", lambda e, kk=key, end_=end: self._slider_draw(kk, end_))
            c.bind("<Button-1>", lambda e, kk=key: self._slider_move(kk, e))
            c.bind("<B1-Motion>", lambda e, kk=key: self._slider_move(kk, e))
            self._slider_canvas[key] = c
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(row, text="Hex #", font=T.font(12), text_color=T.ACCENT, anchor="w").pack(side="left")
        self.hex_var = tk.StringVar(value=self.hex[1:])
        ent = ctk.CTkEntry(row, textvariable=self.hex_var, width=96, height=28, font=T.mono(12), justify="right")
        ent.pack(side="right")
        ent.bind("<Return>", lambda _e: self._hex_typed())
        ent.bind("<FocusOut>", lambda _e: self._hex_typed())
        return f

    def _slider_draw(self, key: str, end: str) -> None:
        c = self._slider_canvas[key]
        W, H = c.winfo_width(), c.winfo_height()
        if W < 10:
            return
        k = self.k
        img = Image.new("RGB", (W, H), T.BG_PANEL)
        er, eg, eb = _hex_to_rgb(end)
        grad = Image.new("RGB", (W, 1))
        px = grad.load()
        for x in range(W):
            t = x / max(1, W - 1)
            px[x, 0] = (int(er * t), int(eg * t), int(eb * t))
        grad = grad.resize((W, H))
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=255)
        img.paste(grad, (0, 0), mask)
        self._images["slider_" + key] = ImageTk.PhotoImage(img)
        c.delete("all")
        c.create_image(0, 0, image=self._images["slider_" + key], anchor="nw")
        r = round(9 * k)
        c.create_oval(0, 0, 2 * r, 2 * r, fill="#FFFFFF", outline=T.BORDER_SOFT, tags="knob")
        c.create_oval(0, 0, 2 * r, 2 * r, fill="", outline="", tags="knob_in")
        self._slider_mark(key)

    def _slider_mark(self, key: str) -> None:
        c = self._slider_canvas[key]
        W, H = c.winfo_width(), c.winfo_height()
        if W < 10 or not c.find_withtag("knob"):
            return
        k = self.k
        r = round(9 * k)
        v = _hex_to_rgb(self.hex)["rgb".index(key)]
        x = r + (W - 2 * r) * v / 255
        c.coords("knob", x - r, H / 2 - r, x + r, H / 2 + r)
        c.coords("knob_in", x - r + 3 * k, H / 2 - r + 3 * k, x + r - 3 * k, H / 2 + r - 3 * k)
        comp = {"r": (v, 0, 0), "g": (0, v, 0), "b": (0, 0, v)}[key]
        c.itemconfigure("knob_in", fill=_rgb_to_hex(*comp), outline="")

    def _slider_move(self, key: str, e) -> None:
        c = self._slider_canvas[key]
        W = c.winfo_width()
        r = round(9 * self.k)
        v = round(max(0.0, min(1.0, (e.x - r) / max(1, W - 2 * r))) * 255)
        rgb = list(_hex_to_rgb(self.hex))
        rgb["rgb".index(key)] = v
        self._set_hex(_rgb_to_hex(*rgb), source="slider")

    def _slider_typed(self, key: str) -> None:
        try:
            v = max(0, min(255, int(self._slider_vars[key].get().strip() or 0)))
        except ValueError:
            self._slider_vars[key].set(str(_hex_to_rgb(self.hex)["rgb".index(key)]))
            return
        rgb = list(_hex_to_rgb(self.hex))
        rgb["rgb".index(key)] = v
        self._set_hex(_rgb_to_hex(*rgb), source="slider")

    def _hex_typed(self) -> None:
        v = normalize_hex(self.hex_var.get(), fallback="")
        if v and v != self.hex:
            self._set_hex(v, source="hex")
        else:
            self.hex_var.set(self.hex[1:])

    # Màu gần đây
    def _build_recent(self, root) -> None:
        sep = ctk.CTkFrame(root, fg_color=T.BORDER_SOFT, height=1)
        sep.pack(fill="x", pady=(12, 10))
        row = ctk.CTkFrame(root, fg_color="transparent")
        row.pack(fill="x")
        self.swatch = ctk.CTkFrame(row, fg_color=self.hex, width=44, height=44, corner_radius=10, border_width=1, border_color=T.BORDER)
        self.swatch.pack(side="left", padx=(0, 12))
        self.swatch.pack_propagate(False)
        self.recent_box = ctk.CTkFrame(row, fg_color="transparent")
        self.recent_box.pack(side="left", fill="x", expand=True)
        self._draw_recent()

    def _draw_recent(self) -> None:
        for w in self.recent_box.winfo_children():
            w.destroy()
        for i, col in enumerate(self.recent[:12]):
            b = ctk.CTkButton(self.recent_box, text="", width=24, height=24, corner_radius=12, fg_color=col, hover_color=col,
                              border_width=1, border_color=T.BORDER, command=lambda c=col: self._set_hex(c, source="recent"))
            b.grid(row=i // 6, column=i % 6, padx=3, pady=3)
            Tooltip(b, col)
        if len(self.recent) < 12:
            i = len(self.recent)
            b = ctk.CTkButton(self.recent_box, text="", image=icon("add", 12, T.TEXT_DIM), width=24, height=24, corner_radius=12,
                              fg_color=T.BG_ELEVATED, hover_color=T.BG_HOVER, border_width=1, border_color=T.BORDER, command=self._add_recent)
            b.grid(row=i // 6, column=i % 6, padx=3, pady=3)
            Tooltip(b, "Lưu màu hiện tại vào hàng màu gần đây")

    def _add_recent(self) -> None:
        if self.hex not in self.recent:
            self.recent = [self.hex, *self.recent][:12]
            save_recent(self.recent)
            self._draw_recent()

    # ── Đồng bộ ─────────────────────────────────────────────────────────
    def _set_hex(self, v: str, *, source: str) -> None:
        v = normalize_hex(v, fallback=self.hex)
        if v == self.hex and source != "init":
            return
        self.hex = v
        self._sync(source=source)
        if self.on_change:
            try:
                self.on_change(v)
            except Exception:
                pass

    def _sync(self, *, source: str) -> None:
        r, g, b = _hex_to_rgb(self.hex)
        for key, val in zip("rgb", (r, g, b)):
            if self._slider_vars[key].get() != str(val):
                self._slider_vars[key].set(str(val))
            self._slider_mark(key)
        if self.hex_var.get().upper() != self.hex[1:]:
            self.hex_var.set(self.hex[1:])
        self.swatch.configure(fg_color=self.hex)
        self._grid_mark()
        self._spec_mark()

    def _switch(self, name: str) -> None:
        for key, page in self.pages.items():
            if key == name:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        if name == "Thanh trượt":
            self.after(30, lambda: [self._slider_mark(kk) for kk in "rgb"])

    # ── Ống hút màu ──────────────────────────────────────────────────────
    def _eyedrop(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        ov = tk.Toplevel(self)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        try:
            ov.attributes("-alpha", 0.01)
        except tk.TclError:
            pass
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        vx, vy = self.winfo_vrootx(), self.winfo_vrooty()
        ov.geometry(f"{self.winfo_vrootwidth() or sw}x{self.winfo_vrootheight() or sh}+{vx}+{vy}")
        ov.configure(cursor="crosshair", bg="#000000")
        ov.focus_force()

        def finish(e=None) -> None:
            x, y = (e.x_root, e.y_root) if e is not None else (None, None)
            ov.destroy()
            self.after(80, lambda: self._eyedrop_pick(x, y))

        ov.bind("<Button-1>", finish)
        ov.bind("<Escape>", lambda _e: finish(None))

    def _eyedrop_pick(self, x, y) -> None:
        if x is not None:
            try:
                from PIL import ImageGrab

                img = ImageGrab.grab(bbox=(x, y, x + 1, y + 1), all_screens=True).convert("RGB")
                self._set_hex(_rgb_to_hex(*img.getpixel((0, 0))), source="eyedrop")
            except Exception:
                pass
        self._grab()

    # ── Vòng đời ─────────────────────────────────────────────────────────
    def _place(self, master) -> None:
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            x = master.winfo_rootx() + master.winfo_width() + 8
            y = master.winfo_rooty() - h // 2
        except tk.TclError:
            x, y = 100, 100
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(8, min(x, sw - w - 8))
        y = max(8, min(y, sh - h - 48))
        self.geometry(f"+{x}+{y}")

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _finish(self, value: str | None) -> None:
        if self._closed:
            return
        self._closed = True
        if value is not None and value not in self.recent:
            self.recent = [value, *self.recent][:12]
            save_recent(self.recent)
        try:
            self.grab_release()
            self.destroy()
        except tk.TclError:
            pass
        if self.on_close:
            self.on_close(value)

    def _ok(self) -> None:
        self._finish(self.hex)

    def _cancel(self) -> None:
        if self.hex != self.initial and self.on_change:
            try:
                self.on_change(self.initial)
            except Exception:
                pass
        self._finish(None)
