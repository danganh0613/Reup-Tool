"""Thẻ trạng thái trống kiểu shadcn «EmptyState», vẽ lên tk.Canvas.

Ba ô icon nghiêng ±6° chồng nhẹ lên nhau (bóng đổ, viền 1 px); di chuột vào thẻ thì hai ô bên xoè ra 20 px, nghiêng
±12°, cả ba nhấc lên 2 px, nền thẻ sáng nhẹ — vào 200 ms, ra 500 ms như CSS ``transition``. Tiêu đề medium, mô tả mờ,
nút bấm là widget thật do nơi gọi đặt vào (``buttons``). Đơn vị thiết kế là px ở DPI 100 %, nhân ``scale`` khi vẽ.
"""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from app.config import theme as T
from app.ui.icons import glyph_image

_SS = 3   # vẽ ô icon to gấp 3 rồi thu nhỏ cho mịn cạnh xoay


def _mix(a: str, b: str, t: float) -> str:
    """Trộn 2 màu hex theo t ∈ [0, 1]."""
    a, b = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t):02X}" for i in (0, 2, 4))


def _ease(p: float) -> float:
    """ease-out cubic — gần với ``transition-timing-function: ease`` của CSS."""
    return 1 - (1 - p) ** 3


class EmptyStateArt:
    TILE, ICON, RADIUS = 48, 24, 12          # size-12 · icon 24 · rounded-xl
    SIDE_IN, SIDE_DROP = 10, 6               # ô bên: left/right-2.5 · top-1.5
    SPREAD, LIFT = 20, 2                     # hover: translate-x-5 · -translate-y-0.5
    TILT, TILT_HOVER = 6, 12                 # rotate-6 → rotate-12
    PAD, CARD_RADIUS, MAX_W = 56, 12, 560    # p-14 · rounded-xl · max-w
    IN_MS, OUT_MS = 200, 500

    def __init__(self, canvas: tk.Canvas, *, icons: tuple[str, str, str] = ("video", "photo", "music"), scale: float = 1.0,
                 title: str = "", description: str = "", buttons: list | None = None, gap: int = 10) -> None:
        self.c = canvas
        self.icons = icons
        self.k = scale
        self.title, self.desc = title, description
        self.buttons = buttons or []
        self.gap = gap
        self.t = 0.0                       # 0 = nghỉ · 1 = đang hover
        self._target = 0.0
        self._anim: str | None = None
        self._poll: str | None = None
        self._imgs: dict[tuple[str, float], ImageTk.PhotoImage] = {}
        self.rect = (0, 0, 0, 0)           # khung thẻ (px canvas)
        self._tiles: list[int] = []
        self._cy_tiles = 0
        self._cx = 0
        self.on_hover: Callable[[bool], None] | None = None

    # ── Ô icon ─────────────────────────────────────────────────────────────
    def _tile(self, name: str, angle: float) -> ImageTk.PhotoImage:
        key = (name, round(angle * 2) / 2)
        img = self._imgs.get(key)
        if img is not None:
            return img
        k, s = self.k, _SS
        px = round(self.TILE * k)
        m = round(18 * k)                                        # lề cho bóng + phần xoay lòi ra
        big = (px + 2 * m) * s
        r = self.RADIUS * k * s
        base = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        # bóng đổ (shadow-lg): mờ, lệch xuống dưới
        sh = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((m * s, (m + 5 * k) * s, (m + px) * s, (m + px + 5 * k) * s), radius=r, fill=(0, 0, 0, 120))
        sh = sh.filter(ImageFilter.GaussianBlur(7 * k * s))
        base.alpha_composite(sh)
        # thân ô + viền 1 px (ring-1 ring-border)
        d = ImageDraw.Draw(base)
        d.rounded_rectangle((m * s, m * s, (m + px) * s - 1, (m + px) * s - 1), radius=r, fill=T.BG_ELEVATED, outline=T.BORDER,
                            width=max(1, round(k * s)))
        ic = glyph_image(name, round(self.ICON * k) * s, T.TEXT_DIM)
        if ic is not None:
            base.alpha_composite(ic, ((big - ic.width) // 2, (big - ic.height) // 2))
        if angle:
            base = base.rotate(angle, resample=Image.BICUBIC)   # PIL: dương = ngược chiều kim đồng hồ (CSS ngược lại)
        small = base.resize((px + 2 * m, px + 2 * m), Image.LANCZOS)
        img = ImageTk.PhotoImage(small)
        self._imgs[key] = img
        return img

    def _tile_state(self, i: int, t: float) -> tuple[float, float, float]:
        """(dx, dy, góc PIL) của ô thứ i (0 trái · 1 giữa · 2 phải) theo mức hover t."""
        k = self.k
        lift = -self.LIFT * k * t
        if i == 1:
            return 0.0, lift, 0.0
        sign = -1 if i == 0 else 1
        dx = sign * (self.TILE - self.SIDE_IN + self.SPREAD * t) * k
        dy = self.SIDE_DROP * k + lift
        css_angle = sign * (self.TILT + (self.TILT_HOVER - self.TILT) * t)
        return dx, dy, -css_angle

    # ── Vẽ ─────────────────────────────────────────────────────────────────
    def draw(self, cw: int, ch: int) -> None:
        """Vẽ lại toàn bộ (sau khi canvas đã ``delete("all")``)."""
        self._stop_anim()
        k = self.k
        c = self.c
        cx, cy = cw // 2, ch // 2
        self._cx = cx
        w = int(min(self.MAX_W * k, cw - 40 * k))
        # chiều cao theo nội dung: ô icon (48+6) · mt-6 · tiêu đề · mt-1 · mô tả · mt-4 · nút · đệm 56 hai đầu
        h = int((self.PAD * 2 + self.TILE + self.SIDE_DROP + 24 + 24 + 4 + 20 + 16 + 36) * k)
        x0, y0, x1, y1 = cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2
        self.rect = (x0, y0, x1, y1)
        r = int(self.CARD_RADIUS * k)
        fill = self._fill(self.t)
        # nền bo góc = 2 hình chữ nhật + 4 hình tròn góc
        for box in ((x0 + r, y0, x1 - r, y1), (x0, y0 + r, x1, y1 - r)):
            c.create_rectangle(*box, fill=fill, outline="", tags="es_fill")
        for ox, oy in ((x0, y0), (x1 - 2 * r, y0), (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
            c.create_oval(ox, oy, ox + 2 * r, oy + 2 * r, fill=fill, outline="", tags="es_fill")
        # viền đứt nét bo góc (border-2 border-dashed rounded-xl)
        line = self._line(self.t)
        lw = max(1, round(1.5 * k))
        dash = (max(2, round(6 * k)), max(2, round(4 * k)))
        for seg in ((x0 + r, y0, x1 - r, y0), (x1, y0 + r, x1, y1 - r), (x0 + r, y1, x1 - r, y1), (x0, y0 + r, x0, y1 - r)):
            c.create_line(*seg, fill=line, width=lw, dash=dash, tags="es_line")
        for (ox, oy), start in (((x0, y0), 90), ((x1 - 2 * r, y0), 0), ((x1 - 2 * r, y1 - 2 * r), 270), ((x0, y1 - 2 * r), 180)):
            c.create_arc(ox, oy, ox + 2 * r, oy + 2 * r, start=start, extent=90, style="arc", outline=line, width=lw, dash=dash,
                         tags="es_arc")
        # ba ô icon (ô giữa vẽ sau cùng để nổi lên trên: z-10)
        self._cy_tiles = y0 + int((self.PAD + self.TILE / 2) * k)
        self._tiles = [0, 0, 0]
        for i in (0, 2, 1):
            dx, dy, ang = self._tile_state(i, self.t)
            self._tiles[i] = c.create_image(cx + dx, self._cy_tiles + dy, image=self._tile(self.icons[i], ang), tags="es_tile")
        # chữ
        y = y0 + int((self.PAD + self.TILE + self.SIDE_DROP + 24) * k)
        c.create_text(cx, y, text=self.title, fill=T.TEXT, font=(T.UI_FONT_MEDIUM, -round(16 * k)), anchor="n", tags="es_text")
        y += int(28 * k)
        c.create_text(cx, y, text=self.desc, fill=T.TEXT_MUTED, font=(T.UI_FONT, -round(13 * k)), anchor="n", width=w - int(2 * self.PAD * k),
                      justify="center", tags="es_text")
        # nút thật (widget) — mt-4
        y += int((20 + 16) * k)
        widths = [b.winfo_reqwidth() for b in self.buttons]
        x = cx - (sum(widths) + self.gap * k * (len(widths) - 1)) / 2
        for b, bw in zip(self.buttons, widths):
            try:
                b.configure(bg_color=fill)
            except Exception:
                pass
            c.create_window(x, y, window=b, anchor="nw", tags="es_btn")
            x += bw + self.gap * k
        self._ensure_poll()

    def _fill(self, t: float) -> str:
        return _mix(T.BG_PANEL, T.BG_ELEVATED, 0.55 * t)          # hover:bg-muted/50

    def _line(self, t: float) -> str:
        return _mix(T.BORDER, T.BG_PANEL, 0.25 * t)               # hover:border-border/80

    # ── Hover / animation ──────────────────────────────────────────────────
    def inside(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.rect
        return x0 <= x <= x1 and y0 <= y <= y1

    def pointer(self, x: float | None, y: float | None = None) -> None:
        """Gọi từ <Motion> của canvas; ``None`` = chuột đã rời."""
        hovered = x is not None and y is not None and self.inside(x, y)
        self.set_hover(hovered)

    def set_hover(self, on: bool) -> None:
        target = 1.0 if on else 0.0
        if target == self._target:
            return
        self._target = target
        if self.on_hover:
            self.on_hover(on)
        self._start_anim()

    def _ensure_poll(self) -> None:
        """Chuột nằm trên nút (widget con) thì canvas không nhận <Motion>/<Leave> → tự soát vị trí chuột."""
        if self._poll is None:
            self._poll = self.c.after(120, self._poll_tick)

    def _poll_tick(self) -> None:
        self._poll = None
        if not self.c.winfo_exists() or not self.c.find_withtag("es_fill"):
            return
        try:
            px, py = self.c.winfo_pointerxy()
            x, y = px - self.c.winfo_rootx(), py - self.c.winfo_rooty()
        except tk.TclError:
            return
        self.set_hover(self.inside(x, y))
        self._poll = self.c.after(120, self._poll_tick)

    def _start_anim(self) -> None:
        self._stop_anim()
        self._t0, self._from = time.perf_counter(), self.t
        self._dur = (self.IN_MS if self._target > self.t else self.OUT_MS) / 1000
        self._frame()

    def _stop_anim(self) -> None:
        if self._anim is not None:
            try:
                self.c.after_cancel(self._anim)
            except tk.TclError:
                pass
            self._anim = None

    def _frame(self) -> None:
        self._anim = None
        if not self.c.winfo_exists() or not self.c.find_withtag("es_fill"):
            return
        p = min(1.0, (time.perf_counter() - self._t0) / max(0.001, self._dur))
        self.t = self._from + (self._target - self._from) * _ease(p)
        self._apply()
        if p < 1.0:
            self._anim = self.c.after(16, self._frame)

    def _apply(self) -> None:
        c = self.c
        fill, line = self._fill(self.t), self._line(self.t)
        c.itemconfigure("es_fill", fill=fill)
        c.itemconfigure("es_line", fill=line)
        c.itemconfigure("es_arc", outline=line)
        for i, item in enumerate(self._tiles):
            dx, dy, ang = self._tile_state(i, self.t)
            c.coords(item, self._cx + dx, self._cy_tiles + dy)
            c.itemconfigure(item, image=self._tile(self.icons[i], ang))
        for b in self.buttons:
            try:
                b.configure(bg_color=fill)
            except Exception:
                pass

    def destroy(self) -> None:
        self._stop_anim()
        if self._poll is not None:
            try:
                self.c.after_cancel(self._poll)
            except tk.TclError:
                pass
            self._poll = None

