"""Cửa sổ chính — bố cục: header · rail "Tính Năng" + panel module bên trái · xem trước lớn bên phải · tiến trình dưới."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.ui import ctk_patches
from app.ui.icons import MODULE_ICONS, icon
from app.config.paths import APP_NAME, APP_VERSION, ASSETS_DIR, VIDEOS_DIR
from app.core.logging_setup import add_ui_sink
from app.core.state import AppState, VideoItem
from app.services.audio_player import AudioPlayer
from app.services.ffmpeg import FFmpeg
from app.services.job_runner import JobRunner, Pipeline, TaskRunner
from app.services.notifier import notify
from app.ui.modules.base_module import BaseModule
from app.ui.modules.module_bgm import BGMModule
from app.ui.modules.module_blur import BlurModule
from app.ui.modules.module_brand import BrandModule
from app.ui.modules.module_color import ColorModule
from app.ui.modules.module_srt import SRTModule
from app.ui.modules.module_story import StoryModule
from app.ui.modules.module_stt import STTModule
from app.ui.modules.module_subtitle import SubtitleModule
from app.ui.modules.module_trim import TrimModule
from app.ui.modules.module_tts import TTSModule
from app.ui.modules.module_video import VideoModule
from app.ui.preview_panel import PreviewPanel
from app.ui.progress_panel import ProgressPanel, friendly_error
from app.ui.widgets.dialogs import LogWindow, NotificationCenter, ShortcutsWindow, now_str
from app.ui.widgets.ui import button, button_width, relabel
from app.core.platform import IS_MAC, IS_WIN, exe_name, open_path

ctk_patches.apply()      # vá CTk: hết vạch hở 1 px mép phải/dưới ở DPI lẻ

logger = logging.getLogger(__name__)

try:  # kéo thả file từ Explorer / Finder
    from tkinterdnd2 import DND_FILES, TkinterDnD

    class _Root(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self) -> None:
            super().__init__()
            try:
                self.TkdndVersion = TkinterDnD._require(self)
            except Exception:                    # thư viện tkdnd không nạp được trên máy này → vẫn mở app, chỉ tắt kéo thả
                logger.warning("Không bật được kéo thả file (tkdnd)", exc_info=True)
                self.TkdndVersion = None

    _HAS_DND = True
except Exception:  # pragma: no cover
    _Root = ctk.CTk  # type: ignore[misc,assignment]
    _HAS_DND = False

RAIL_W = 72                      # thanh tính năng bên trái (gọn: chỉ icon + nhãn ngắn)
RAIL_LABELS = {"story": "Truyện"}  # nhãn ngắn trên thanh bên (tiêu đề đầy đủ vẫn ở đầu bảng tính năng)
MODULES: list[type[BaseModule]] = [VideoModule, SubtitleModule, STTModule, SRTModule, TTSModule, BlurModule,
                                   BGMModule, BrandModule, TrimModule, ColorModule, StoryModule]
_FKEYS = {f"<F{i + 1}>": m.key for i, m in enumerate(MODULES)}


class MainWindow(_Root):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.app_state = state
        self.ffmpeg = FFmpeg(state.settings.ffmpeg_path, state.settings.ffprobe_path)
        self.pipeline = Pipeline(state, self.ffmpeg)
        self._waiting: list[tuple[str, Callable[[], None]]] = []   # việc xếp hàng chờ tác vụ / render đang chạy
        self.runner = JobRunner(self.pipeline, self.ui_call)
        self.tasks = TaskRunner(self.ui_call)
        self.player = AudioPlayer(self.ffmpeg)
        self.modules: dict[str, BaseModule] = {}
        self._rail_buttons: dict[str, ctk.CTkButton] = {}
        self._current_module: str | None = None
        self._save_after: str | None = None
        self._render_started = 0.0
        self.log_lines: list[str] = []
        self.notifications: list[dict] = []
        self._log_win: LogWindow | None = None
        self._notif_win: NotificationCenter | None = None

        self.title(f"{APP_NAME} v{APP_VERSION} · Tự động dựng video")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1600, sw - 40)}x{min(940, sh - 80)}+10+10")
        self.minsize(1000, 620)
        self._resize_after: str | None = None
        self._compact_header = False
        self._compact_rail = False
        self._help_wanted = False
        self.after(50, self._maximize)
        self.configure(fg_color=T.BG)
        self._set_icon()
        state.set_save_scheduler(self._schedule_save)

        self.grid_columnconfigure(3, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_body()
        self._build_progress()

        add_ui_sink(self._log_sink)
        self.app_state.on("active_changed", self._active_changed)
        self.app_state.on("config_loaded", self.refresh_modules)
        self.app_state.on("videos_changed", lambda: (self.update_render_button(), self.progress.refresh_idle()))
        from app.services.watch_folder import FolderWatcher

        self._watcher = FolderWatcher()
        self._watch_pending: list[VideoItem] = []
        self.after(4000, self._watch_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._bind_shortcuts()
        self.bind("<Configure>", self._on_resize)
        self.after(400, self._apply_layout)
        if _HAS_DND and getattr(self, "TkdndVersion", None):
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)

        self.show_module(self.app_state.ui.get("active_module", "video"))
        if self.app_state.settings.show_guide_on_start:
            self.after(600, lambda: self.open_guide("start"))
        elif self.app_state.ui.get("help_open"):
            self.after(600, lambda: self.open_guide())
        self._active_changed()
        if not self.ffmpeg.available:
            self.after(300, lambda: messagebox.showwarning(
                APP_NAME, f"Không tìm thấy ffmpeg/ffprobe.\nĐặt {exe_name('ffmpeg')} + {exe_name('ffprobe')} vào thư mục bin/ "
                          "hoặc chọn đường dẫn trong Cài đặt."))
        dnd = " · kéo thả: bật" if _HAS_DND and getattr(self, "TkdndVersion", None) else ""
        self.run_bg(self.ffmpeg.version,          # chạy `ffmpeg -version` trong nền, không chặn lúc mở app
                    lambda v, _e: self.log(f"{APP_NAME} sẵn sàng · {(v or '')[:40] or 'chưa có ffmpeg'}{dnd}"))

    # ── Dựng giao diện ────────────────────────────────────────────────────
    def _maximize(self) -> None:
        try:
            self.wm_state("zoomed")              # Windows + macOS
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)  # Linux (X11)
            except tk.TclError:
                pass

    def _set_icon(self) -> None:
        ico, png = ASSETS_DIR / "icon.ico", ASSETS_DIR / "icon.png"
        try:
            if IS_WIN and ico.exists():
                self.iconbitmap(str(ico))
            elif png.exists():                   # macOS: icon Dock khi chạy mã nguồn (bản .app lấy từ .icns)
                self._icon_photo = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _build_header(self) -> None:
        head = ctk.CTkFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD, height=58)
        head.grid(row=0, column=0, columnspan=4, sticky="ew", padx=10, pady=(10, 4))
        self.header_frame = head
        head.grid_propagate(False)
        head.grid_columnconfigure(1, weight=1)
        left = ctk.CTkFrame(head, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=(14, 0), pady=10)
        ctk.CTkLabel(left, text="", image=icon("sparkle", 18, T.ACCENT)).pack(side="left")
        self.brand_label = ctk.CTkLabel(left, text=f" {APP_NAME}", font=T.font(17, "bold"), text_color=T.TEXT)
        self.brand_label.pack(side="left")
        self.version_label = ctk.CTkLabel(left, text=f"v{APP_VERSION}", font=T.font(11), text_color=T.TEXT_MUTED)
        self.version_label.pack(side="left", padx=(8, 0))
        self.header_btns: list[list] = []   # [nút, nhãn đầy đủ, bề rộng đầy đủ] — cửa sổ hẹp thì chỉ còn icon

        from app.ui.widgets.template_dialogs import TemplateBar

        self.template_bar = TemplateBar(head, self)
        self.template_bar.grid(row=0, column=1, pady=8)
        right = ctk.CTkFrame(head, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e", padx=(0, 10), pady=8)
        # một nút chính duy nhất (Render / Chuẩn bị / Xuất); việc phụ gom vào menu ☰; «Dừng» nằm ở thanh trạng thái dưới
        self.notif_btn = self._hbtn(right, "", "bell", self.open_notifications, variant="ghost",
                                    tip="Thông báo (kết quả render, lỗi)")
        self.notif_btn.pack(side="left", padx=2)
        self.menu_btn = self._hbtn(right, "", "list", self._app_menu, variant="ghost",
                                   tip="Menu: cài đặt, hướng dẫn, phím tắt, thư mục xuất, xuất MP3, kiểm tra máy")
        self.menu_btn.pack(side="left", padx=(2, 6))
        self.render_btn = button(right, "Render MP4", icon_name="play", variant="primary", size="lg",
                                 command=lambda: self.start_render(list(self.app_state.videos)), tip="Dựng toàn bộ hàng đợi thành MP4 (Ctrl+R)")
        self.render_btn.pack(side="left", padx=(4, 4))
        self._running = False
        self.after(300, self.update_render_button)

    def _hbtn(self, parent, text: str, icon_name: str, command, *, variant: str = "secondary", **kw) -> ctk.CTkButton:
        """Nút header (md, có icon); ghi nhớ nhãn + bề rộng để thu gọn/khôi phục khi cửa sổ đổi cỡ."""
        b = button(parent, text, icon_name=icon_name, variant=variant, command=command, **kw)
        self.header_btns.append([b, text, b.cget("width")])
        return b

    def _set_header_label(self, b: ctk.CTkButton, text: str) -> None:
        """Đổi nhãn một nút header (vd. số thông báo) và đo lại bề rộng; tôn trọng chế độ header gọn."""
        for entry in self.header_btns:
            if entry[0] is b:
                entry[1], entry[2] = text, button_width(b.master, text, icon=True)
                if not self._compact_header:
                    relabel(b, text, width=entry[2])
                return

    def _build_body(self) -> None:
        self.rail = ctk.CTkFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD, width=RAIL_W)
        self.rail.grid(row=1, column=0, sticky="ns", padx=(10, 4))
        self.rail.grid_propagate(False)
        self.rail.pack_propagate(False)          # giữ đúng bề rộng RAIL_W dù nhãn nút dài
        ctk.CTkFrame(self.rail, fg_color="transparent", height=4, width=1).pack()     # lề trên nhỏ, không tiêu đề
        for cls in MODULES:
            self._add_rail_button(cls)
        self.sync_review_rail()

        self.panel = ctk.CTkFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD, width=470)
        self.panel.grid(row=1, column=1, sticky="ns", padx=4)
        self.panel.grid_propagate(False)
        self.panel.grid_columnconfigure(0, weight=1)
        self.panel.grid_rowconfigure(1, weight=1)
        self._build_scope_strip()

        from app.ui.widgets.guide_window import HelpPane

        self.help = HelpPane(self, self)          # cột hướng dẫn cạnh bảng tính năng (bật/tắt)
        self._help_open = False
        self.preview = PreviewPanel(self, self)
        self.preview.grid(row=1, column=3, sticky="nsew", padx=(4, 10))

    def _add_rail_button(self, cls: type[BaseModule]) -> None:
        compact = self._compact_rail
        b = ctk.CTkButton(self.rail, text=RAIL_LABELS.get(cls.key, cls.title), width=RAIL_W - 10, height=30 if compact else 48, corner_radius=T.RADIUS,
                          fg_color="transparent", hover_color=T.BG_HOVER, text_color=T.TEXT_DIM, font=T.font(8 if compact else 10),
                          image=icon(MODULE_ICONS.get(cls.key, "grid"), 14 if compact else 18, T.TEXT_DIM), compound="top",
                          command=lambda k=cls.key: self.show_module(k))
        b.pack(pady=(0 if compact else 2, 0), padx=5)
        self._rail_buttons[cls.key] = b

    # ── Responsive ────────────────────────────────────────────────────────
    def _scaling(self) -> float:
        try:
            return float(ctk.ScalingTracker.get_window_scaling(self)) or 1.0
        except Exception:
            return 1.0

    def _on_resize(self, event) -> None:
        if event.widget is not self:
            return
        if self._resize_after is not None:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.after(120, self._apply_layout)

    def _apply_layout(self) -> None:
        """Bố cục theo kích thước cửa sổ (đơn vị logic, đã chia DPI):
        header gọn < 1500 · rail gọn khi thấp < 800 · bảng tính năng 360–470 · hướng dẫn 300–400, hẹp quá thì ẩn ·
        thanh công cụ xem trước gọn khi khung < 760."""
        self._resize_after = None
        k = self._scaling()
        w, h = self.winfo_width() / k, self.winfo_height() / k
        if w < 50:
            return
        compact_header = w < 1740               # thanh «Mẫu» ở giữa cần chỗ → nút đầu trang chỉ còn icon sớm hơn
        self.template_bar.set_compact(w < 1250)
        if compact_header != self._compact_header:
            self._compact_header = compact_header
            for b, label, width in self.header_btns:
                if b is self.render_btn:
                    continue
                relabel(b, "" if compact_header else label, width=36 if compact_header else width)
            self.version_label.configure(text="" if compact_header else f"v{APP_VERSION}")
        compact_rail = h < 800
        if compact_rail != self._compact_rail:
            self._compact_rail = compact_rail
            cur = self._current_module
            for b in self._rail_buttons.values():
                b.destroy()
            self._rail_buttons.clear()
            for cls in MODULES:
                self._add_rail_button(cls)
            self.sync_review_rail()
            if cur:
                self.show_module(cur)
        panel_w = int(max(360, min(470, w * 0.30)))
        help_w = int(max(300, min(400, w * 0.26)))
        if getattr(self, "_panel_help_w", None) != (panel_w, help_w):     # configure(width) vẽ lại cả khung CTk
            self._panel_help_w = (panel_w, help_w)
            self.panel.configure(width=panel_w)
            self.help.set_width(help_w)
        room_for_help = w - RAIL_W - panel_w - help_w - 60 >= 560
        if self._help_open and not room_for_help:
            self._help_wanted = True
            self.help.grid_forget()
            self._help_open = False
            self.log("Cửa sổ hẹp — bảng hướng dẫn tạm ẩn (bấm Hướng dẫn để mở cửa sổ riêng).")
        elif self._help_wanted and room_for_help and not self._help_open:
            self._help_wanted = False
            self.open_guide(self.help.key)
        preview_w = w - RAIL_W - panel_w - (help_w if self._help_open else 0) - 60
        self.preview.set_compact(preview_w < 760)
        # ẩn/hiện lại các khung (vẽ lại toàn bộ) chỉ khi bố cục thực sự đổi, không phải mỗi lần _apply_layout được gọi
        sig = (round(w), round(h), panel_w, help_w, self._help_open)
        if sig != getattr(self, "_layout_sig", None):
            self._layout_sig = sig
            self.after(60, self._repaint_all)

    def _repaint_all(self) -> None:
        """Widget CTk co lại để lại vệt vẽ cũ trên nền frame cha → ẩn/hiện lại các khung để Tk vẽ lại toàn bộ."""
        for w in (self.header_frame, self.rail, getattr(self.preview, "bar", None), getattr(self.preview, "ctrl", None)):
            if w is None:
                continue
            try:
                w.grid_remove()
                w.grid()
            except Exception:
                pass
        self.update_idletasks()

    # ── Module ────────────────────────────────────────────────────────────
    def show_module(self, key: str) -> None:
        if key == "review" and not self.review_mode:
            key = "video"                       # tab Duyệt chỉ có khi bật «Duyệt trước khi xuất»
        if key == "review":
            from app.ui.widgets.review_window import ReviewPanel

            cls = ReviewPanel
        else:
            cls = next((c for c in MODULES if c.key == key), MODULES[0])
        key = cls.key
        if key not in self.modules:
            self.modules[key] = cls(self.panel, self)
        for k, m in self.modules.items():
            if k != key and m.winfo_ismapped():
                m.grid_forget()
                m.on_hidden()
        self.modules[key].grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        self.modules[key].on_shown()
        self.after(30, self.modules[key].scroll_top)
        prev = self._current_module
        for k, b in self._rail_buttons.items():
            if prev and prev in self._rail_buttons and k not in (prev, key):
                continue              # chỉ vẽ lại 2 nút đổi trạng thái (mỗi configure vẽ lại cả nút CTk)
            b.configure(fg_color=T.ACCENT_SOFT if k == key else "transparent",
                        text_color=T.ACCENT if k == key else T.TEXT_DIM,
                        image=icon(MODULE_ICONS.get(k, "grid"), 20, T.ACCENT if k == key else T.TEXT_DIM))
        self._current_module = key
        self._update_scope_strip()
        self.app_state.ui["active_module"] = key
        self.app_state.mark_dirty()
        if self._help_open and self.help.key not in ("start", "flow_dub", "flow_short", "faq", "settings", "shortcuts"):
            self.help.show(key)
        self.preview.set_edit_tools(key in ("subtitle", "blur"))
        if key == "blur":
            self.preview.set_tool("B")
        elif key == "subtitle":
            self.preview.set_tool("T")
        else:
            self.preview._redraw()

    def current_module_key(self) -> str:
        return self._current_module or ""

    def refresh_modules(self) -> None:
        for m in self.modules.values():
            m.refresh()
        self.preview.schedule_refresh(0)

    def notify_blur_selection(self) -> None:
        m = self.modules.get("blur")
        if m is not None:
            m.refresh()

    def notify_box_edit(self, kind: str) -> None:
        m = self.modules.get("blur" if kind == "blur" else "subtitle")
        if m is not None:
            m.refresh()

    def queue_update(self, item: VideoItem) -> None:
        m = self.modules.get("video")
        if m is not None:
            m.queue.update_row(item)
        self.update_render_button()
        r = self.modules.get("review")
        if r is not None and getattr(self, "_review_counts_after", None) is None:
            # trạng thái đổi liên tục khi render → gom lại, cập nhật đếm / ô chọn video của tab Duyệt tối đa 2 lần/giây
            def _upd() -> None:
                self._review_counts_after = None
                if r.winfo_exists():
                    r._update_counts()
            self._review_counts_after = self.after(500, _upd)

    # ── Trạng thái / sự kiện ──────────────────────────────────────────────
    def _active_changed(self) -> None:
        item = self.app_state.active
        self.preview.set_item(item)
        for m in self.modules.values():
            m.on_active_changed()
        self._scope_note = ""
        self._update_scope_strip()

    def _on_drop(self, event) -> None:
        try:
            paths = list(self.tk.splitlist(event.data))
        except Exception:
            paths = [event.data]
        paths = [p.strip("{}") for p in paths]
        from app.ui.modules.module_bgm import AUDIO_EXTS

        audio = [p for p in paths if Path(p).suffix.lower() in AUDIO_EXTS]
        if audio:
            self.show_module("bgm")
            self.modules["bgm"].add_paths(audio)
        srts = [p for p in paths if Path(p).suffix.lower() == ".srt"]
        rest = [p for p in paths if p not in audio and p not in srts]
        if rest:
            self.show_module("video")
            self.modules["video"].queue.add_paths(rest)
        if srts:
            item = self.app_state.active
            if item is None:
                self.log("Kéo SRT: hãy nạp/chọn video trước.")
            else:
                item.srt_path = srts[0]
                self.app_state.emit("active_changed")
                self.app_state.mark_dirty()
                self.log(f"Đã gắn SRT {Path(srts[0]).name} cho {item.name}.")

    def _schedule_save(self, fn: Callable[[], None]) -> None:
        if self._save_after is not None:
            try:
                self.after_cancel(self._save_after)
            except Exception:
                pass
        self._save_after = self.after(800, fn)

    def apply_settings(self) -> None:
        st = self.app_state.settings
        self.ffmpeg = FFmpeg(st.ffmpeg_path, st.ffprobe_path)
        self.pipeline.ffmpeg = self.ffmpeg
        self.player.ffmpeg = self.ffmpeg
        self.preview.player.ffmpeg = self.ffmpeg
        self.log("Đã lưu cài đặt." + ("" if self.ffmpeg.available else " ⚠ Chưa tìm thấy ffmpeg."))
        for key in ("tts", "video", "srt"):
            if key in self.modules:
                self.modules[key].refresh()

    def open_settings(self, tab: str | None = None) -> None:
        from app.ui.widgets.settings_window import SettingsWindow

        win = getattr(self, "_settings_win", None)
        if win is not None and win.winfo_exists():       # đang mở → đưa lên trước, khỏi dựng lại cả cửa sổ
            win.deiconify()
            win.lift()
            win.focus_force()
            if tab == "shortcuts":
                win._switch_tab("📖 Phím Tắt & Cẩm Nang Hướng Dẫn")
            return
        self._settings_win = SettingsWindow(self, tab=tab)

    def apply_theme_live(self, name: str) -> None:
        """Đổi theme ngay lập tức: chốt bảng màu mới rồi dựng lại toàn bộ giao diện (state giữ nguyên)."""
        st = self.app_state.settings
        st.theme = T.resolve_theme(name)
        st.appearance_mode = "oled" if st.theme == "oled" else "dark"
        self.app_state.save_settings()
        T.apply_theme(st.theme)
        self.rebuild_ui()
        self.log(f"Đã đổi giao diện: {T.THEMES[st.theme]['name']}.")
        self.after(150, lambda: self.open_settings())

    def rebuild_ui(self) -> None:
        """Hủy mọi widget và dựng lại (dùng khi đổi theme). Job/preview state nằm trong AppState nên không mất gì."""
        current = self.current_module_key() or "video"
        help_open, help_key = self._help_open, self.help.key
        t_pos = self.preview.t
        try:
            self.preview.stop_play()
        except Exception:
            pass
        for w in list(self.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        self.modules.clear()
        self._rail_buttons.clear()
        self._current_module = None
        self._panel_help_w = self._layout_sig = None     # khung mới dựng lại → phải đặt lại kích thước / vẽ lại
        self._help_open = False
        self._compact_header = False
        self._render_label = None
        self.configure(fg_color=T.BG)
        self._build_header()
        self._build_body()
        self._build_progress()
        self.show_module(current)
        self._active_changed()
        try:
            self.preview.seek(t_pos)
        except Exception:
            pass
        if help_open:
            self.open_guide(help_key)
        if self.runner.running or self.tasks.running:
            self.set_running(True)
        self.after(50, self._apply_layout)

    def open_shortcuts(self) -> None:
        ShortcutsWindow(self)

    def open_guide(self, key: str | None = None) -> None:
        """Hiện bảng hướng dẫn cạnh bảng tính năng, đúng chủ đề (mặc định: module đang xem)."""
        key = key or self.current_module_key() or "start"
        k = self._scaling()
        w = self.winfo_width() / k
        if w > 50 and w < 1250 and not self._help_open:
            self.open_guide_window(key)          # quá hẹp để đặt bảng cạnh bên
            return
        if not self._help_open:
            self.help.grid(row=1, column=2, sticky="ns", padx=4)
            self._help_open = True
            self.app_state.ui["help_open"] = True
        self.help.show(key)
        self._apply_layout()

    def toggle_help(self, key: str | None = None) -> None:
        """Nút Hướng dẫn: đang mở đúng chủ đề này thì đóng, còn lại thì mở."""
        key = key or self.current_module_key() or "start"
        if self._help_open and self.help.key == key:
            self.help.grid_forget()
            self._help_open = False
            self._help_wanted = False
            self.app_state.ui["help_open"] = False
            self.app_state.mark_dirty()
            self._apply_layout()
            if self.app_state.settings.show_guide_on_start:      # đã tự đóng một lần → lần sau không tự bật nữa
                self.app_state.settings.show_guide_on_start = False
                self.app_state.save_settings()
            return
        self.open_guide(key)

    def open_guide_window(self, key: str | None = None) -> None:
        from app.ui.widgets.guide_window import open_guide

        open_guide(self, key or self.current_module_key() or "start")

    def open_notifications(self) -> None:
        if self._notif_win is None or not self._notif_win.winfo_exists():
            self._notif_win = NotificationCenter(self)
        else:
            self._notif_win.refresh()
            self._notif_win.lift()
        self._set_header_label(self.notif_btn, "")

    def open_selfcheck(self) -> None:
        from app.ui.widgets.selfcheck_window import SelfCheckWindow

        w = getattr(self, "_selfcheck_win", None)
        if w is None or not w.winfo_exists():
            self._selfcheck_win = SelfCheckWindow(self)
        else:
            w.lift()

    def open_log(self) -> None:
        if self._log_win is None or not self._log_win.winfo_exists():
            self._log_win = LogWindow(self)
        else:
            self._log_win.lift()

    def open_output_dir(self) -> None:
        d = self.app_state.output_dir or str(VIDEOS_DIR)
        Path(d).mkdir(parents=True, exist_ok=True)
        open_path(d)

    def add_notification(self, title: str, body: str, kind: str = "info") -> None:
        self.notifications.append({"title": title, "body": body, "kind": kind, "time": now_str()})
        del self.notifications[:-100]
        self._set_header_label(self.notif_btn, f"{len(self.notifications)}")
        if self._notif_win is not None and self._notif_win.winfo_exists():
            self._notif_win.refresh()

    # ── Phím tắt ─────────────────────────────────────────────────────────
    def _bind_shortcuts(self) -> None:
        for seq, key in _FKEYS.items():
            self.bind(seq, lambda _e, k=key: self.show_module(k))
        mod_keys = {
            "s": lambda _e: (self.app_state.save_config(), self.log("Đã lưu cấu hình.")),
            "r": lambda _e: self.start_render(list(self.app_state.videos)),
            "z": lambda e: self._if_free(e, self.preview.undo),
            "y": lambda e: self._if_free(e, self.preview.redo),
            "Shift-Z": lambda e: self._if_free(e, self.preview.redo),
            "e": lambda e: self._if_free(e, self._open_srt_editor),
            "h": lambda e: self._if_free(e, lambda: self.toggle_help()),
            "slash": lambda e: self._if_free(e, lambda: self.toggle_help()),
            "plus": lambda _e: self.preview.zoom_step(1),
            "equal": lambda _e: self.preview.zoom_step(1),
            "minus": lambda _e: self.preview.zoom_step(-1),
            "0": lambda _e: self.preview.set_zoom(None),
            "Return": lambda _e: self._review_key("approve"),
            "Down": lambda _e: self._review_key("next"),
            "Up": lambda _e: self._review_key("prev"),
        }
        for k, fn in mod_keys.items():
            self.bind(f"<Control-{k}>", fn)
            if IS_MAC:                           # macOS quen dùng ⌘ (Command) thay Ctrl
                self.bind(f"<Command-{k}>", fn)
        self.bind("<Escape>", lambda _e: self.stop_all() if (self.runner.running or self.tasks.running) else None)

        def fps() -> float:
            return max(1.0, self.preview.info.fps if self.preview.info and self.preview.info.fps else 30.0)

        keys = {
            "<space>": self.preview.toggle_play,
            "<j>": lambda: self.preview.seek(self.preview.t - 3),
            "<k>": self.preview.stop_play,
            "<l>": lambda: self.preview.seek(self.preview.t + 3),
            "<Left>": lambda: self.preview.seek(self.preview.t - 3),
            "<Right>": lambda: self.preview.seek(self.preview.t + 3),
            "<comma>": lambda: self.preview.seek(self.preview.t - 1 / fps()),
            "<period>": lambda: self.preview.seek(self.preview.t + 1 / fps()),
            "<m>": self.preview.toggle_mute,
            "<Home>": lambda: self.preview.seek(0.0),
            "<End>": lambda: self.preview.seek((self.preview.info.duration - 0.1) if self.preview.info else 0.0),
            "<F12>": self.preview.fullscreen, "<f>": self.preview.fullscreen,
            "<v>": lambda: self.preview.set_tool("V"), "<t>": lambda: self.preview.set_tool("T"),
            "<b>": lambda: self.preview.set_tool("B"), "<Shift-T>": self.preview._toggle_sub,
            "<i>": lambda: self._trim_mark("head"), "<o>": lambda: self._trim_mark("tail"),
            "<bracketleft>": lambda: self._trim_mark("head"), "<bracketright>": lambda: self._trim_mark("tail"),
            "<Delete>": self.preview.delete_selection,
            "<BackSpace>": self.preview.delete_selection,
        }
        for seq, fn in keys.items():
            self.bind(seq, lambda e, f=fn: self._if_free(e, f))

    def _if_free(self, event, fn: Callable[[], None]) -> None:
        """Bỏ qua phím tắt khi đang gõ trong ô nhập."""
        w = event.widget
        try:
            cls = w.winfo_class()
        except Exception:
            cls = ""
        if cls in ("Entry", "Text", "TEntry", "Spinbox", "TCombobox"):
            return
        fn()

    def _trim_mark(self, which: str) -> None:
        cfg = self.app_state.trim
        t = self.preview.t
        if which == "head":
            cfg.head = f"{t:.2f}"
        else:
            dur = self.preview.info.duration if self.preview.info else 0
            cfg.tail = f"{max(0.0, dur - t):.2f}"
        cfg.enable = True
        self.app_state.config_changed("trim")
        if "trim" in self.modules:
            self.modules["trim"].refresh()
        self.log(f"Cắt: {'bỏ đầu' if which == 'head' else 'bỏ đuôi'} tại {t:.2f}s")

    def _open_srt_editor(self) -> None:
        self.show_module("srt")
        self.modules["srt"].open_editor()

    # ── Thread helpers ────────────────────────────────────────────────────
    def ui_call(self, fn: Callable[[], None]) -> None:
        """Chạy ``fn`` trên luồng UI. Cửa sổ/hộp thoại đích có thể đã đóng khi tác vụ nền xong →
        TclError từ widget đã hủy chỉ ghi log, không làm hỏng gì."""
        def safe() -> None:
            try:
                fn()
            except tk.TclError as exc:
                logger.debug("ui_call: widget đã đóng: %s", exc)

        try:
            self.after(0, safe)
        except RuntimeError:
            pass

    def run_bg(self, fn: Callable[[], object], done: Callable[[object | None, Exception | None], None]) -> None:
        def run() -> None:
            result: object | None = None
            error: Exception | None = None
            try:
                result = fn()
            except Exception as exc:
                error = exc
                logger.debug("run_bg lỗi", exc_info=True)
            self.ui_call(lambda: done(result, error))

        threading.Thread(target=run, daemon=True).start()

    def run_task(self, name: str, fn: Callable[[threading.Event], object],
                 done: Callable[[object | None, Exception | None], None]) -> None:
        if self.runner.running or self.tasks.running:
            self._offer_wait(name, lambda: self.run_task(name, fn, done))
            return
        self.progress.start_task(name)
        self.set_running(True)

        def _done(result, error):
            self.set_running(False)
            self.after(400, self._run_waiting)
            if error:
                stopped = "huỷ" in str(error).lower()
                self.progress.result("stopped" if stopped else "error", f"{name}: đã dừng" if stopped else f"{name} lỗi",
                                     "" if stopped else friendly_error(str(error)))
                self.log(f"✖ {name}: {error}")
                if not stopped:
                    self.add_notification(f"{name} lỗi", str(error)[:300], "error")
            else:
                self.progress.result("ok", f"{name} xong")
            done(result, error)

        self.tasks.start(name, fn, on_done=_done)

    # ── Hàng chờ việc: bấm khi đang bận → hỏi xếp hàng, xong việc trước tự chạy ──
    def _offer_wait(self, name: str, action: Callable[[], None]) -> None:
        busy = "render hàng đợi" if self.runner.running else f"«{self.tasks.name}»"
        if any(n == name for n, _a in self._waiting):
            messagebox.showinfo(APP_NAME, f"«{name}» đã nằm trong hàng chờ — sẽ tự chạy sau {busy}.")
            return
        if not messagebox.askyesno(APP_NAME, f"Đang chạy {busy}.\n\nXếp «{name}» vào hàng chờ? Nó sẽ tự chạy ngay khi "
                                             "việc đang làm xong (bấm Dừng để huỷ cả hàng chờ)."):
            return
        self._waiting.append((name, action))
        self.log(f"Đã xếp «{name}» vào hàng chờ — {len(self._waiting)} việc đang chờ.")
        self._show_waiting()

    def _show_waiting(self) -> None:
        self.progress.set_waiting([n for n, _a in self._waiting])

    def _run_waiting(self) -> None:
        if not self._waiting or self.runner.running or self.tasks.running:
            return
        name, action = self._waiting.pop(0)
        self.log(f"▶ Chạy việc trong hàng chờ: {name}")
        try:
            action()
        except Exception:
            logger.exception("Việc trong hàng chờ lỗi: %s", name)
        self._show_waiting()

    def retry_item(self, item: VideoItem) -> None:
        """Nút ↻ của video lỗi trong hàng đợi: chuẩn bị lại (chế độ Duyệt) hoặc render lại đúng video đó."""
        if item not in self.app_state.videos:
            return
        self.log(f"Thử lại: {item.name}")
        if self.review_mode and item.review != "approved":
            item.review = ""
            self._run_queue([item], mode="prepare")
        else:
            self._run_queue([item], mode="render")

    def pipeline_for(self, item: VideoItem) -> Pipeline:
        """Pipeline với bản chụp cấu hình của ``item`` — tác vụ nền (nhận diện, dịch, tạo giọng…) không bị đổi cấu hình
        giữa chừng khi người dùng chọn sang video khác (có cấu hình riêng) hoặc chỉnh tiếp trong lúc chờ."""
        return Pipeline(self.app_state.state_for(item), self.ffmpeg)

    def set_busy_progress(self, p: float) -> None:
        self.ui_call(lambda: self.progress.set_progress(p))

    def set_running(self, running: bool) -> None:
        self._running = running
        self.render_btn.configure(state="disabled" if running else "normal")
        self.progress.set_running_ui(running)            # «Dừng» chỉ hiện khi có việc đang chạy

    def _build_progress(self) -> None:
        self.progress = ProgressPanel(self, self)
        self.progress.grid(row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=(4, 10))
        self.stop_btn = self.progress.stop_btn
        self.progress.set_running_ui(getattr(self, "_running", False))

    def _show_result(self, items: list[VideoItem], ok: int, fail: int, elapsed: float, prep: bool) -> None:
        """Kết quả một lượt chạy trên thanh trạng thái: một câu + đúng một việc nên bấm tiếp."""
        from app.ui.progress_panel import _clock, short_path

        took = f"mất {_clock(elapsed)}"
        if fail:
            first = next(it for it in items if it.status == "Lỗi")
            self.progress.result("error", f"{fail} video lỗi" + (f" · {ok} xong" if ok else ""),
                                 f"{first.name}: {friendly_error(first.message, first)}",
                                 ("Thử lại", self.retry_failed, "refresh", True))
        elif any(it.status == "Đã huỷ" for it in items):
            self.progress.result("stopped", "Đã dừng", f"{ok}/{len(items)} video đã xong")
        elif prep:
            self.progress.result("ok", f"Chuẩn bị xong {ok} video",
                                 f"{took} · xem từng video ở tab «Duyệt», ưng thì bấm «Duyệt», xong bấm «Xuất» trên cùng")
        else:
            out = self.app_state.output_dir or str(VIDEOS_DIR)
            self.progress.result("ok", f"Đã xuất {ok} video", f"{took} · lưu ở {short_path(out)}",
                                 ("Mở thư mục", self.open_output_dir, "openfolder", False))

    def retry_failed(self) -> None:
        """Nút «Thử lại» ở thanh trạng thái: chạy lại mọi video lỗi (chưa duyệt → chuẩn bị lại, đã duyệt → xuất lại)."""
        failed = [it for it in self.app_state.videos if it.status == "Lỗi"]
        prep = [it for it in failed if self.review_mode and it.review != "approved"]
        ren = [it for it in failed if it not in prep]
        for it in prep:
            it.review = ""
        if prep:
            self._run_queue(prep, mode="prepare")
            if ren:
                self._waiting.append((f"Render {len(ren)} video", lambda: self._run_queue(ren, mode="render")))
                self._show_waiting()
        elif ren:
            self._run_queue(ren, mode="render")

    def _app_menu(self) -> None:
        m = tk.Menu(self, tearoff=0, bg=T.BG_ELEVATED, fg=T.TEXT, activebackground=T.BG_HOVER,
                    activeforeground=T.TEXT, bd=0)
        m.add_command(label="Cài đặt (API key, GPU, giao diện)…", command=self.open_settings)
        m.add_command(label="Hướng dẫn  (Ctrl+H)", command=lambda: self.toggle_help())
        m.add_command(label="Phím tắt", command=self.open_shortcuts)
        m.add_separator()
        m.add_command(label="Mở thư mục xuất", command=self.open_output_dir)
        m.add_command(label="Chỉ xuất âm thanh (MP3)", state="disabled" if self._running else "normal",
                      command=lambda: self.start_render(list(self.app_state.videos), audio_only=True))
        m.add_separator()
        m.add_command(label="Xem log", command=self.open_log)
        m.add_command(label="Kiểm tra máy…", command=self.open_selfcheck)
        b = self.menu_btn
        try:
            m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
        finally:
            m.grab_release()

    # ── Log ───────────────────────────────────────────────────────────────
    def log(self, msg: str) -> None:
        logger.info(msg)
        self._append_log(time.strftime("%H:%M:%S") + "  " + msg)

    def log_bg(self, msg: str) -> None:
        logger.info(msg)
        self.ui_call(lambda: self._append_log(time.strftime("%H:%M:%S") + "  " + msg))

    def _log_sink(self, level: str, msg: str) -> None:
        if level in ("WARNING", "ERROR", "CRITICAL"):
            self.ui_call(lambda: self._append_log(("⚠ " if level == "WARNING" else "✖ ") + msg))

    def _append_log(self, line: str) -> None:
        self.log_lines.append(line)
        del self.log_lines[:-2000]
        if self._log_win is not None and self._log_win.winfo_exists():
            self._log_win.append(line)

    # ── Render ────────────────────────────────────────────────────────────
    @property
    def review_mode(self) -> bool:
        return bool(self.app_state.ui.get("review_mode"))

    def start_render(self, items: list[VideoItem], audio_only: bool = False, *, force: bool = False) -> None:
        """Nút Render. Bật «Duyệt trước khi xuất»: video chưa chuẩn bị → Chuẩn bị; đã duyệt → xuất; còn lại → mở tab Duyệt.
        ``force``: xuất đúng các video được đưa vào (đã duyệt)."""
        if not items:
            messagebox.showinfo(APP_NAME, "Hàng đợi trống — bấm Nạp video trước.")
            return
        if self.review_mode and not force:
            todo = [it for it in items if not it.review]
            if todo:
                self._run_queue(todo, mode="prepare")
                return
            ready = [it for it in items if it.review == "approved" and it.status != "Xong"]
            if not ready:
                self.open_review()
                return
            items = ready
        self._run_queue(items, mode="render", audio_only=audio_only)

    def _run_queue(self, items: list[VideoItem], *, mode: str = "render", audio_only: bool = False) -> None:
        if not self.ffmpeg.available:
            messagebox.showerror(APP_NAME, "Không có ffmpeg — xem Cài đặt.")
            return
        if self.tasks.running or self.runner.running:   # không đổi audio_only của pipeline khi hàng đợi đang chạy
            label = "Chuẩn bị" if mode == "prepare" else ("Xuất MP3" if audio_only else "Render")
            self._offer_wait(f"{label} {len(items)} video",
                             lambda: self._run_queue(items, mode=mode, audio_only=audio_only))
            return
        self.preview.stop_play()
        Path(self.app_state.output_dir or VIDEOS_DIR).mkdir(parents=True, exist_ok=True)
        for it in items:
            it.status, it.progress, it.message = "Chờ", 0.0, ""
            self.queue_update(it)
        self._render_started = time.time()
        total = len(items)
        done_count = [0]
        self.pipeline.audio_only = audio_only
        prep = mode == "prepare"

        def on_update(it: VideoItem) -> None:
            self.queue_update(it)
            if it.status in ("Đang xử lý", "Đang chuẩn bị"):
                self.progress.update_item(it, done_count[0] + 1, total, (done_count[0] + it.progress) / total)
            elif it.status in ("Xong", "Lỗi", "Đã huỷ", "Chờ duyệt"):
                done_count[0] = min(total, done_count[0] + 1)

        def on_done(ok: int, fail: int) -> None:
            self.set_running(False)
            self.after(400, self._run_waiting)
            self.update_render_button()
            elapsed = time.time() - self._render_started
            self._show_result(items, ok, fail, elapsed, prep)
            if prep:
                msg = f"Chuẩn bị xong: {ok} video chờ duyệt, {fail} lỗi · {elapsed / 60:.1f} phút"
                self.log(msg)
                self.add_notification("Chuẩn bị xong — chờ bạn duyệt", msg, "ok" if not fail else "error")
                notify("Edit Studio — Chờ duyệt", msg, enabled=self.app_state.settings.windows_toast)
                self.app_state.save_config()
                if ok:
                    self.open_review()
                return
            msg = f"Hoàn tất: {ok} xong, {fail} lỗi · {elapsed / 60:.1f} phút"
            self.log(msg)
            self.add_notification("Render xong" if fail == 0 else "Render có lỗi", msg, "ok" if fail == 0 else "error")
            notify("Edit Studio — Render xong", msg, enabled=self.app_state.settings.windows_toast)
            self.app_state.save_config()
            self.pipeline.audio_only = False

        if self.runner.start(items, on_update=on_update, on_log=self.log, on_done=on_done, mode=mode):
            self.set_running(True)
            self.progress.start_queue(items, mode)
            if prep:
                self.log(f"Chuẩn bị {total} video (phụ đề, dịch, giọng lồng) — xong sẽ mở tab «Duyệt».")
            else:
                self.log(f"Bắt đầu render {'MP3' if audio_only else 'MP4'} {total} video → {self.app_state.output_dir}")

    # ── Thư mục theo dõi ─────────────────────────────────────────────────
    def watch_started(self) -> None:
        """Vừa bật / đổi thư mục theo dõi: hỏi có lấy luôn các video đang có sẵn trong thư mục không."""
        ui = self.app_state.ui
        folder = ui.get("watch_dir")
        if not folder or not Path(folder).is_dir():
            return
        known = {v.path.lower() for v in self.app_state.videos} | {p.lower() for p in ui.get("watch_seen", [])}
        old = [p for p in self._watcher.existing(folder) if p.lower() not in known]
        if old and not messagebox.askyesno(APP_NAME, f"Thư mục đang có sẵn {len(old)} video.\n\nNạp luôn các video này? "
                                                     "(Không = chỉ nạp video chép vào từ giờ.)"):
            ui["watch_seen"] = (list(ui.get("watch_seen", [])) + old)[-3000:]
            self.app_state.mark_dirty()

    def _watch_tick(self) -> None:
        try:
            self._watch_scan()
        except Exception:
            logger.warning("Thư mục theo dõi lỗi", exc_info=True)
        self.after(4000, self._watch_tick)

    def _watch_scan(self) -> None:
        from app.services import templates as TP
        from app.services.watch_folder import SHARED, WATCH_ACTIONS

        ui = self.app_state.ui
        folder = ui.get("watch_dir")
        if ui.get("watch_enable") and folder and Path(folder).is_dir():
            skip = {v.path.lower() for v in self.app_state.videos} | {p.lower() for p in ui.get("watch_seen", [])}
            new = self._watcher.scan(folder, skip)
            if new:
                added = self.app_state.add_videos(new)
                ui["watch_seen"] = (list(ui.get("watch_seen", [])) + new)[-3000:]
                ui["watch_added"] = int(ui.get("watch_added", 0)) + len(added)
                t = TP.get_template(ui.get("watch_template") or "") if ui.get("watch_template") not in (None, "", SHARED) else None
                for it in added:
                    if t is not None:
                        TP.apply_to_item(self.app_state, it, t)
                    self.queue_update(it)
                self.log(f"Thư mục theo dõi: nạp {len(added)} video mới" + (f" (mẫu «{t.name}»)" if t else "") + ".")
                if (ui.get("watch_action") or WATCH_ACTIONS[0]) != WATCH_ACTIONS[0]:
                    self._watch_pending += added
                m = self.modules.get("video")
                if m is not None:
                    m.update_watch_status()
                self.app_state.mark_dirty()
        if self._watch_pending and not self.runner.running and not self.tasks.running:
            items = [it for it in self._watch_pending if it in self.app_state.videos]
            self._watch_pending = []
            if not items:
                return
            if (ui.get("watch_action") or "") == WATCH_ACTIONS[1]:
                self._run_queue(items, mode="prepare")
            else:
                self._run_queue(items, mode="render")

    # ── Duyệt ─────────────────────────────────────────────────────────────
    def open_review(self) -> None:
        """Tab «Duyệt» trong bảng tính năng — khung xem trước vẫn hiện cạnh bên để vừa xem vừa sửa."""
        self.sync_review_rail()
        self.show_module("review")

    # ── Đang duyệt: sửa ở tab tính năng = sửa riêng video đó; dải nhắc ở đầu bảng tính năng ──
    def _build_scope_strip(self) -> None:
        self.scope_strip = ctk.CTkFrame(self.panel, fg_color=T.ACCENT_SOFT, corner_radius=8)
        self.scope_strip.grid_columnconfigure(0, weight=1)
        self.scope_label = ctk.CTkLabel(self.scope_strip, text="", font=T.font(11), anchor="w", justify="left",
                                        text_color=T.TEXT, wraplength=210)
        self.scope_label.grid(row=0, column=0, sticky="ew", padx=(10, 4), pady=6)
        self.scope_all_btn = button(self.scope_strip, "Áp cho tất cả", size="sm", variant="ghost",
                                    command=self._apply_to_all,
                                    tip="Dùng cách chỉnh của video này làm cấu hình chung cho các video chưa chỉnh riêng")
        self.scope_all_btn.grid(row=0, column=1, padx=2)
        self.scope_back_btn = button(self.scope_strip, "‹ Về Duyệt", size="sm", variant="primary",
                                     command=lambda: self.show_module("review"),
                                     tip="Quay lại bảng duyệt video (Ctrl+Enter: duyệt luôn video này)")
        self.scope_back_btn.grid(row=0, column=2, padx=(2, 8))
        self._scope_note = ""
        if not getattr(self, "_scope_listening", False):        # dựng lại giao diện (đổi theme) → không đăng ký lặp
            self._scope_listening = True
            self.app_state.on("scoped", self._on_scoped)
            self.app_state.on("config_changed", self._scope_changed)

    def _update_scope_strip(self) -> None:
        strip = getattr(self, "scope_strip", None)
        if strip is None or not strip.winfo_exists():
            return
        it = self.app_state.active
        show = self.review_mode and it is not None and self._current_module not in (None, "review")
        if not show:
            self.scope_strip.grid_remove()
            return
        own = it.config is not None
        name = it.name if len(it.name) <= 22 else it.name[:20] + "…"
        text = (f"✎ Chỉnh riêng: {name}" if own else f"Đang duyệt {name} · chỉnh ở đây chỉ áp cho video này")
        if self._scope_note:
            text += f"\n{self._scope_note}"
        self.scope_label.configure(text=text)
        if own:
            self.scope_all_btn.grid()
        else:
            self.scope_all_btn.grid_remove()
        self.scope_strip.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 0))

    def _on_scoped(self, item: VideoItem) -> None:
        self.log(f"{item.name}: tách cấu hình riêng (video khác không bị đổi).")
        self.queue_update(item)

    def _scope_changed(self, _section: str = "") -> None:
        if self.review_mode and not self.preview.player.playing and not self.app_state.last_change_auto:
            self._scope_note = "✓ Đã chỉnh — ▶ Phát để xem lại"
        self._update_scope_strip()

    def _apply_to_all(self) -> None:
        it = self.app_state.active
        if it is None or it.config is None:
            return
        if not messagebox.askyesno(APP_NAME, f"Dùng cách chỉnh của «{it.name}» cho mọi video chưa chỉnh riêng?"):
            return
        self.app_state.apply_to_all(it)
        self.queue_update(it)
        self._scope_note = "✓ Đã áp cho tất cả video chưa chỉnh riêng"
        self._update_scope_strip()
        self.log(f"Đã dùng cấu hình của {it.name} làm cấu hình chung.")

    def edit_in(self, key: str) -> None:
        """«Sửa ›» ở bảng Duyệt: mở đúng tab tính năng cho video đang duyệt."""
        self._scope_note = ""
        self.show_module(key)

    def review_panel(self):
        """Bảng Duyệt (tạo nếu chưa có — để phím tắt duyệt dùng được ở mọi tab)."""
        if "review" not in self.modules:
            from app.ui.widgets.review_window import ReviewPanel

            self.modules["review"] = ReviewPanel(self.panel, self)
        return self.modules["review"]

    def _review_key(self, action: str) -> None:
        if not self.review_mode or not self.app_state.videos:
            return
        rv = self.review_panel()
        if action == "approve":
            rv.approve()
        else:
            rv._step(1 if action == "next" else -1)
        self._scope_note = ""
        self._update_scope_strip()

    def sync_review_rail(self) -> None:
        """Nút «Duyệt» trên thanh tính năng chỉ hiện khi bật chế độ Duyệt trước khi xuất."""
        self.app_state.set_review_scope(self.review_mode)
        self._update_scope_strip()
        b = self._rail_buttons.get("review")
        if self.review_mode and b is None:
            from app.ui.widgets.review_window import ReviewPanel

            self._add_rail_button(ReviewPanel)
            first = self._rail_buttons.get("video")
            if first is not None:
                self._rail_buttons["review"].pack_configure(after=first)
        elif not self.review_mode and b is not None:
            if self._current_module == "review":
                self.show_module("video")
            b.destroy()
            del self._rail_buttons["review"]

    def update_render_button(self) -> None:
        """Nhãn nút Render theo bước duyệt: Chuẩn bị (n) → Duyệt → Xuất (n)."""
        vids = self.app_state.videos
        if not self.review_mode:
            text = "Render MP4"
        else:
            todo = sum(1 for it in vids if not it.review)
            ready = sum(1 for it in vids if it.review == "approved" and it.status != "Xong")
            text = f"Chuẩn bị ({todo})" if todo else (f"Xuất ({ready})" if ready else "Duyệt")
        if getattr(self, "_render_label", None) != text:
            self._render_label = text
            relabel(self.render_btn, text)
            if hasattr(self, "progress"):
                self.progress.refresh_idle()

    def stop_all(self) -> None:
        if self._waiting:
            self.log(f"Đã huỷ {len(self._waiting)} việc trong hàng chờ.")
            self._waiting.clear()
        self.runner.stop()
        self.tasks.stop()
        self.log("Đã yêu cầu dừng…")

    def destroy(self) -> None:
        # khử giọng AI đang chạy nền (onnxruntime) phải dừng gọn trước khi thoát, không thì tiến trình bị abort
        from app.services import vocal_remove

        vocal_remove.shutdown()
        super().destroy()

    def _on_close(self) -> None:
        if self.runner.running or self.tasks.running:
            if not messagebox.askyesno(APP_NAME, "Đang xử lý. Thoát và huỷ?"):
                return
            self.stop_all()
        self.preview.stop_play()
        self.app_state.save_config()
        self.player.stop()
        self.destroy()
