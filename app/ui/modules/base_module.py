"""Lớp nền cho các module trong bảng điều khiển bên phải."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import button

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow


class BaseModule(ctk.CTkScrollableFrame):
    key: str = "base"
    title: str = "Module"
    icon: str = "▣"
    #: tên section trong AppState mà module ghi vào (để preview biết có cần vẽ lại không)
    section: str = ""

    def __init__(self, master, window: MainWindow) -> None:
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.window = window
        self.state = window.app_state
        self._loading = False
        self.grid_columnconfigure(0, weight=1)
        from app.ui.icons import MODULE_ICONS, icon

        img = icon(MODULE_ICONS.get(self.key, "grid"), 18, T.ACCENT, dy=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", pady=(4, 8), padx=4)
        ctk.CTkLabel(head, text=f"  {self.title}" if img else f"{self.icon}  {self.title}", image=img, compound="left",
                     font=T.font(16, "medium"), anchor="w").pack(side="left", fill="x", expand=True)
        button(head, "Hướng dẫn", icon_name="info", variant="ghost", size="sm", command=lambda: self.window.toggle_help(self.key), icon_color=T.TEXT_DIM).pack(side="right")
        self.build()
        self.refresh()
        self._fresh = True        # vừa refresh trong __init__ → lần on_shown đầu tiên khỏi refresh lại

    # ── Ghi đè ────────────────────────────────────────────────────────────
    def build(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Đổ giá trị từ state ra widget (sau khi nạp preset/config)."""

    def on_active_changed(self) -> None:
        """Video đang chọn thay đổi."""

    def on_shown(self) -> None:
        """Module vừa được hiện lên bảng điều khiển: đổ lại giá trị (module khác có thể đã đổi cấu hình)."""
        if getattr(self, "_fresh", False):
            self._fresh = False
            return
        self.refresh()

    def on_hidden(self) -> None:
        """Module vừa bị ẩn (chuyển sang module khác)."""

    def scroll_top(self) -> None:
        try:
            self._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    # ── Tiện ích ──────────────────────────────────────────────────────────
    def set_value(self, cfg: Any, field: str, value: Any, *, preview: bool = True) -> None:
        if self._loading:
            return
        # Nạp preset thay thế cả object cấu hình → luôn lấy object hiện hành theo tên section.
        target = getattr(self.state, self.section, None) if self.section else None
        if target is None or not hasattr(target, field):
            target = cfg
        if getattr(target, field, None) == value:
            return
        setattr(target, field, value)
        self.state.config_changed(self.section)
        if preview:
            self.window.preview.schedule_refresh()

    def setter(self, cfg: Any, field: str, *, cast=None, preview: bool = True):
        def _set(v):
            self.set_value(cfg, field, cast(v) if cast else v, preview=preview)
        return _set
