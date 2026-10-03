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
from app.ui.progress_panel import ProgressPanel, stage_index_for
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

MODULES: list[type[BaseModule]] = [VideoModule, SubtitleModule, STTModule, SRTModule, TTSModule, BlurModule,
                                   BGMModule, BrandModule, TrimModule, ColorModule, StoryModule]
_FKEYS = {f"<F{i + 1}>": m.key for i, m in enumerate(MODULES)}


class MainWindow(_Root):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.app_state = state
        self.ffmpeg = FFmpeg(state.settings.ffmpeg_path, state.settings.ffprobe_path)
        self.pipeline = Pipeline(state, self.ffmpeg)
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
        self.progress = ProgressPanel(self, self)
        self.progress.grid(row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=(4, 10))

        add_ui_sink(self._log_sink)
        self.app_state.on("active_changed", self._active_changed)
        self.app_state.on("config_loaded", self.refresh_modules)
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
        self._hbtn(left, "Cài đặt", "settings", self.open_settings, tip="Cài đặt: API key, GPU, giao diện, thư mục xuất").pack(side="left", padx=(24, 4))
        self._hbtn(left, "Hướng dẫn", "book", lambda: self.toggle_help(), tip="Bật / tắt bảng hướng dẫn bên cạnh (Ctrl+H)").pack(side="left", padx=4)
        self._hbtn(left, "Phím tắt", "keyboard", self.open_shortcuts, tip="Danh sách phím tắt").pack(side="left", padx=4)
        self.notif_btn = self._hbtn(left, "Thông báo", "bell", self.open_notifications, tip="Trung tâm thông báo (kết quả render, lỗi)")
        self.notif_btn.pack(side="left", padx=4)
        self.busy_label = ctk.CTkLabel(left, text="", font=T.font(12), text_color=T.WARNING)
        self.busy_label.pack(side="left", padx=12)

        from app.ui.widgets.template_dialogs import TemplateBar

        self.template_bar = TemplateBar(head, self)
        self.template_bar.grid(row=0, column=1, pady=8)
        right = ctk.CTkFrame(head, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e", padx=(0, 10), pady=8)
        self._hbtn(right, "Thư mục xuất", "folder", self.open_output_dir, tip="Mở thư mục chứa video đã xuất").pack(side="left", padx=4)
        self.mp3_btn = self._hbtn(right, "Render MP3", "music", lambda: self.start_render(list(self.app_state.videos), audio_only=True),
                                  tip="Chỉ xuất âm thanh (lồng tiếng + nhạc) thành MP3")
        self.mp3_btn.pack(side="left", padx=4)
        self.render_btn = button(right, "Render MP4", icon_name="play", variant="primary", size="lg",
                                 command=lambda: self.start_render(list(self.app_state.videos)), tip="Dựng toàn bộ hàng đợi thành MP4 (Ctrl+R)")
        self.render_btn.pack(side="left", padx=(8, 4))
        self.stop_btn = self._hbtn(right, "Dừng", "stop", self.stop_all, variant="destructive", state="disabled", tip="Dừng mọi việc đang chạy (Esc)")
        self.stop_btn.pack(side="left", padx=4)

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
        self.rail = ctk.CTkFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD, width=94)
        self.rail.grid(row=1, column=0, sticky="ns", padx=(10, 4))
        self.rail.grid_propagate(False)
        self.rail_label = ctk.CTkLabel(self.rail, text="Tính Năng", font=T.font(11, "bold"), text_color=T.ACCENT)
        self.rail_label.pack(pady=(10, 2))
        for cls in MODULES:
            self._add_rail_button(cls)

        self.panel = ctk.CTkFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD, width=470)
        self.panel.grid(row=1, column=1, sticky="ns", padx=4)
        self.panel.grid_propagate(False)
        self.panel.grid_columnconfigure(0, weight=1)
        self.panel.grid_rowconfigure(0, weight=1)

        from app.ui.widgets.guide_window import HelpPane

        self.help = HelpPane(self, self)          # cột hướng dẫn cạnh bảng tính năng (bật/tắt)
        self._help_open = False
        self.preview = PreviewPanel(self, self)
        self.preview.grid(row=1, column=3, sticky="nsew", padx=(4, 10))

    def _add_rail_button(self, cls: type[BaseModule]) -> None:
        compact = self._compact_rail
        b = ctk.CTkButton(self.rail, text=cls.title, width=82, height=32 if compact else 56, corner_radius=T.RADIUS,
                          fg_color="transparent", hover_color=T.BG_HOVER, text_color=T.TEXT_DIM, font=T.font(8 if compact else 11),
                          image=icon(MODULE_ICONS.get(cls.key, "grid"), 14 if compact else 20, T.TEXT_DIM), compound="top",
                          command=lambda k=cls.key: self.show_module(k))
        b.pack(pady=(0 if compact else 4, 0), padx=6)
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
        header gọn < 1500 · rail gọn khi thấp < 880 · bảng tính năng 360–470 · hướng dẫn 300–400, hẹp quá thì ẩn ·
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
        compact_rail = h < 880
        if compact_rail != self._compact_rail:
            self._compact_rail = compact_rail
            cur = self._current_module
            for b in self._rail_buttons.values():
                b.destroy()
            self._rail_buttons.clear()
            if compact_rail:
                self.rail_label.pack_forget()
            elif not self.rail_label.winfo_ismapped():
                self.rail_label.pack(pady=(10, 2))
            for cls in MODULES:
                self._add_rail_button(cls)
            if cur:
                self.show_module(cur)
        panel_w = int(max(360, min(470, w * 0.30)))
        help_w = int(max(300, min(400, w * 0.26)))
        if getattr(self, "_panel_help_w", None) != (panel_w, help_w):     # configure(width) vẽ lại cả khung CTk
            self._panel_help_w = (panel_w, help_w)
            self.panel.configure(width=panel_w)
            self.help.set_width(help_w)
        room_for_help = w - 94 - panel_w - help_w - 60 >= 560
        if self._help_open and not room_for_help:
            self._help_wanted = True
            self.help.grid_forget()
            self._help_open = False
            self.log("Cửa sổ hẹp — bảng hướng dẫn tạm ẩn (bấm Hướng dẫn để mở cửa sổ riêng).")
        elif self._help_wanted and room_for_help and not self._help_open:
            self._help_wanted = False
            self.open_guide(self.help.key)
        preview_w = w - 94 - panel_w - (help_w if self._help_open else 0) - 60
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
        cls = next((c for c in MODULES if c.key == key), MODULES[0])
        key = cls.key
        if key not in self.modules:
            self.modules[key] = cls(self.panel, self)
        for k, m in self.modules.items():
            if k != key and m.winfo_ismapped():
                m.grid_forget()
                m.on_hidden()
        self.modules[key].grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
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
        self.app_state.ui["active_module"] = key
        self.app_state.mark_dirty()
        if self._help_open and self.help.key not in ("start", "flow_dub", "flow_short", "faq", "settings", "shortcuts"):
            self.help.show(key)
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

    # ── Trạng thái / sự kiện ──────────────────────────────────────────────
    def _active_changed(self) -> None:
        item = self.app_state.active
        self.preview.set_item(item)
        for m in self.modules.values():
            m.on_active_changed()

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
        self.configure(fg_color=T.BG)
        self._build_header()
        self._build_body()
        self.progress = ProgressPanel(self, self)
        self.progress.grid(row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=(4, 10))
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
        self._set_header_label(self.notif_btn, "Thông báo")

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
        self._set_header_label(self.notif_btn, f"{len(self.notifications)} Thông báo")
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
        if self.runner.running:
            messagebox.showinfo(APP_NAME, "Đang render hàng đợi — hãy đợi xong hoặc bấm Dừng.")
            return
        if self.tasks.running:
            messagebox.showinfo(APP_NAME, f"Đang chạy tác vụ “{self.tasks.name}” — hãy đợi xong.")
            return
        self.busy_label.configure(text=f"⏳ {name}…")
        self.progress.set_status(f"{name}…")
        self.progress.set_progress(0.0)
        self.progress.set_stage(0)
        self.set_running(True)

        def _done(result, error):
            self.busy_label.configure(text="")
            self.set_running(False)
            if error:
                self.progress.set_status(f"✖ {name}: {str(error).splitlines()[0][:120]}")
                self.log(f"✖ {name}: {error}")
                self.add_notification(f"{name} lỗi", str(error)[:300], "error")
            else:
                self.progress.set_status(f"✔ {name} xong.")
                self.progress.set_progress(1.0)
            done(result, error)

        self.tasks.start(name, fn, on_done=_done)

    def set_busy_progress(self, p: float) -> None:
        self.ui_call(lambda: self.progress.set_progress(p))

    def set_running(self, running: bool) -> None:
        self.render_btn.configure(state="disabled" if running else "normal")
        self.mp3_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

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
    def start_render(self, items: list[VideoItem], audio_only: bool = False) -> None:
        if not items:
            messagebox.showinfo(APP_NAME, "Hàng đợi trống — bấm Nạp video trước.")
            return
        if not self.ffmpeg.available:
            messagebox.showerror(APP_NAME, "Không có ffmpeg — xem Cài đặt.")
            return
        if self.tasks.running:
            messagebox.showinfo(APP_NAME, f"Đang chạy tác vụ “{self.tasks.name}” — hãy đợi xong.")
            return
        if self.runner.running:   # không được đổi audio_only của pipeline khi hàng đợi đang chạy
            messagebox.showinfo(APP_NAME, "Đang render hàng đợi — hãy đợi xong hoặc bấm Dừng.")
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

        def on_update(it: VideoItem) -> None:
            self.queue_update(it)
            if it.status == "Đang xử lý":
                self.progress.set_stage(stage_index_for(it.message))
                self.progress.set_status(f"[{done_count[0] + 1}/{total}] {it.name} — {it.message}")
                self.progress.set_progress((done_count[0] + it.progress) / total)
            elif it.status in ("Xong", "Lỗi", "Đã huỷ"):
                done_count[0] = min(total, done_count[0] + 1)

        def on_done(ok: int, fail: int) -> None:
            self.set_running(False)
            self.busy_label.configure(text="")
            elapsed = time.time() - self._render_started
            msg = f"Hoàn tất: {ok} xong, {fail} lỗi · {elapsed / 60:.1f} phút"
            self.progress.set_stage(4 if fail == 0 else -1)
            self.progress.set_progress(1.0 if fail == 0 else 0.0)
            self.progress.set_status(msg)
            self.log(msg)
            self.add_notification("Render xong" if fail == 0 else "Render có lỗi", msg, "ok" if fail == 0 else "error")
            notify("Edit Studio — Render xong", msg, enabled=self.app_state.settings.windows_toast)
            self.app_state.save_config()
            self.pipeline.audio_only = False

        if self.runner.start(items, on_update=on_update, on_log=self.log, on_done=on_done):
            self.set_running(True)
            self.busy_label.configure(text="⏳ Đang render…")
            self.progress.set_stage(0)
            self.log(f"Bắt đầu render {'MP3' if audio_only else 'MP4'} {total} video → {self.app_state.output_dir}")

    def stop_all(self) -> None:
        self.runner.stop()
        self.tasks.stop()
        self.log("Đã yêu cầu dừng…")

    def _on_close(self) -> None:
        if self.runner.running or self.tasks.running:
            if not messagebox.askyesno(APP_NAME, "Đang xử lý. Thoát và huỷ?"):
                return
            self.stop_all()
        self.preview.stop_play()
        self.app_state.save_config()
        self.player.stop()
        self.destroy()
