"""Bánh xe cảm xúc vẽ trên Canvas tkinter (không dùng SVG — Canvas chạy y nhau trên CustomTkinter 5.2 và 6.0).

Góc quy ước: 0° ở ĐỈNH, tăng THUẬN chiều kim đồng hồ (khớp ``emotions.json``). Kéo chuột trong vòng:
góc kim = cảm xúc, khoảng cách từ tâm = bậc 1–5. Phát ``on_change(theta, level)`` khi đổi.
"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable

import customtkinter as ctk

from app.config import theme as T

_LEVELS = 5
_HUB = 0.30          # bán kính lỗ tâm so với bánh xe


class EmotionWheel(ctk.CTkFrame):
    def __init__(self, master, emotions: list[dict], *, size: int = 400,
                 on_change: Callable[[float, int], None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.emotions = emotions
        self.on_change = on_change
        self.theta = 0.0
        self.level = 3
        self._size = size
        self.canvas = tk.Canvas(self, width=size + 130, height=size, highlightthickness=0, bg=T.BG_PANEL,
                                cursor="hand2")      # rộng hơn cao: chừa chỗ nhãn hai bên
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._paint())
        self.canvas.bind("<Button-1>", self._on_drag)
        self.canvas.bind("<B1-Motion>", self._on_drag)

    # ── hình học: góc «thuận chiều kim đồng hồ từ đỉnh» ↔ toạ độ canvas ──
    def _geom(self) -> tuple[float, float, float]:
        w = self.canvas.winfo_width() or self._size
        h = self.canvas.winfo_height() or self._size
        return w / 2, h / 2, min(w - 130, h) / 2 - 42   # chừa chỗ nhãn trên / dưới (2 vòng so le)

    def _point(self, cx: float, cy: float, r: float, theta: float) -> tuple[float, float]:
        phi = math.radians(90 - theta)                 # canvas: 0°=3h, 90°=12h, ngược chiều kim đồng hồ
        return cx + r * math.cos(phi), cy - r * math.sin(phi)

    def _angle_at(self, x: float, y: float, cx: float, cy: float) -> float:
        theta = (90 - math.degrees(math.atan2(-(y - cy), x - cx))) % 360
        return theta

    # ── vẽ ───────────────────────────────────────────────────────────────
    def _paint(self) -> None:
        c = self.canvas
        c.delete("all")
        cx, cy, R = self._geom()
        if R <= 10:
            return
        n = len(self.emotions)
        span = 360.0 / n
        for e in self.emotions:
            start = 90 - (e["angle"] + span / 2)        # canvas start (CCW từ 3h)
            c.create_arc(cx - R, cy - R, cx + R, cy + R, start=start, extent=span, style="pieslice",
                         fill=e["color"], outline=T.BG_PANEL, width=1)
        # vòng bậc (giữa lỗ tâm và mép) + lỗ tâm đủ rộng để ghi tên cảm xúc
        rin = R * _HUB
        for lv in range(1, _LEVELS):
            r = rin + (R - rin) * lv / _LEVELS
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=T.BORDER_SOFT, width=1)
        c.create_oval(cx - rin, cy - rin, cx + rin, cy + rin, fill=T.BG_PANEL, outline=T.BORDER, width=1)
        # nhãn quanh vòng: so le 2 bán kính (đỡ đè nhau ở đỉnh / đáy), bám ra phía ngoài theo bên trái / phải
        main_id = self._main_id()
        for i, e in enumerate(self.emotions):
            # nửa trên / dưới đảo thứ tự so le → hai nhãn kề nhau ở đáy không rơi cùng độ cao
            near = (i % 2 == 0) if math.cos(math.radians(e["angle"])) >= 0 else (i % 2 == 1)
            lx, ly = self._point(cx, cy, R + (10 if near else 30), e["angle"])
            anchor = "center" if abs(lx - cx) < R * 0.12 else ("w" if lx > cx else "e")
            hot = e["id"] == main_id
            c.create_text(lx, ly, text=e["vi"], anchor=anchor, font=(T.UI_FONT, 8, "bold" if hot else "normal"),
                          fill=T.TEXT if hot else T.TEXT_MUTED)
        # kim chỉ: từ mép lỗ tâm tới chấm (bậc 1 sát lỗ tâm, bậc 5 sát mép)
        r = self._level_radius(rin, R, self.level)
        hx, hy = self._point(cx, cy, rin, self.theta)
        px, py = self._point(cx, cy, r, self.theta)
        c.create_line(hx, hy, px, py, fill=T.TEXT, width=2)
        c.create_oval(px - 8, py - 8, px + 8, py + 8, fill=T.ACCENT, outline=T.ON_ACCENT, width=2)
        # tên cảm xúc chính + bậc trong lỗ tâm
        main = self._main()
        c.create_text(cx, cy - 8, text=main["vi"], font=(T.UI_FONT, 11, "bold"), fill=T.TEXT, width=int(rin * 1.9))
        c.create_text(cx, cy + 12, text=f"Bậc {self.level}/5", font=(T.UI_FONT, 9), fill=T.TEXT_DIM)

    @staticmethod
    def _level_radius(rin: float, R: float, level: int) -> float:
        return rin + (R - rin) * (level - 0.5) / _LEVELS

    def _main(self) -> dict:
        return min(self.emotions, key=lambda e: _ang_dist(self.theta, e["angle"]))

    def _main_id(self) -> str:
        return self._main()["id"]

    # ── tương tác ─────────────────────────────────────────────────────────
    def _on_drag(self, event) -> None:
        cx, cy, R = self._geom()
        if R <= 10:
            return
        dist = math.hypot(event.x - cx, event.y - cy)
        self.theta = self._angle_at(event.x, event.y, cx, cy)
        rin = R * _HUB
        frac = max(0.0, min(1.0, (dist - rin) / (R - rin)))      # 0 = mép lỗ tâm, 1 = mép ngoài
        self.level = max(1, min(_LEVELS, int(frac * _LEVELS) + 1))
        self._paint()
        if self.on_change:
            self.on_change(self.theta, self.level)

    def set(self, theta: float, level: int) -> None:
        self.theta = float(theta) % 360
        self.level = max(1, min(_LEVELS, int(level)))
        self._paint()

    def get(self) -> tuple[float, int]:
        return self.theta, self.level


def _ang_dist(a: float, b: float) -> float:
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)
