"""Cửa sổ chính — bố cục: header · rail "Tính Năng" + panel module bên trái · xem trước lớn bên phải · tiến trình dưới."""

from __future__ import annotations

import logging
import re
import shutil
import sys
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
from app.config.paths import APP_NAME, APP_VERSION, ASSETS_DIR, JOBS_DIR, VIDEOS_DIR
from app.core.logging_setup import add_ui_sink
from app.core.state import PREPARE_UNTIL, AppState, VideoItem
from app.services.audio_player import AudioPlayer
from app.services.ffmpeg import FFmpeg
from app.services.job_runner import WAITING, JobRunner, Pipeline, TaskRunner
from app.services.notifier import notify
from app.ui.modules.base_module import BaseModule
from app.ui.modules.module_bgm import BGMModule
from app.ui.modules.module_blur import BlurModule
from app.ui.modules.module_brand import BrandModule
from app.ui.modules.module_color import ColorModule
from app.ui.modules.module_srt import SRTModule
from app.ui.modules.module_story import StoryModule
from app.ui.modules.module_trim import TrimModule
from app.ui.modules.module_tts import TTSModule
from app.ui.modules.module_video import VideoModule
from app.ui.preview_panel import PreviewPanel
from app.ui.progress_panel import ProgressPanel, friendly_error
from app.ui.widgets.dialogs import LogWindow, NotificationCenter, ShortcutsWindow, now_str
from app.ui.widgets.ui import Select, button, button_width, relabel
from app.core.platform import IS_MAC, IS_WIN, exe_name, keep_awake, open_path

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
#: tab dựng sẵn (ẩn) lúc người dùng rảnh, theo thứ tự hay dùng — mở lần đầu khỏi đứng hình 0,2–0,7 s (audit_out/perf);
#: tab nằm sau «Thêm tính năng» không dựng sẵn (ít dùng — đỡ việc lúc máy rảnh)
PREWARM = ["srt", "tts", "video", "color", "bgm", "brand", "trim"]
PREWARM_IDLE = 1.5               # giây không bấm / gõ / cuộn mới dựng sẵn
#: nhãn ngắn trên thanh bên (tiêu đề đầy đủ vẫn ở đầu bảng tính năng)
RAIL_LABELS = {"story": "Truyện", "tts": "Lồng tiếng"}
#: «Sửa ›» ở tab Duyệt: dòng tóm tắt → khối cần mở trong tab (cuộn tới đúng chỗ, không phải tìm)
EDIT_SECTIONS = {"Âm thanh gốc": "Âm thanh", "Khung hình": "Khung hình"}
# «Phụ đề» (kiểu chữ) đã gộp vào «Lời thoại»; vị trí / cỡ chữ nhanh nằm trên thanh khung xem trước; «Nhận diện» gộp vào
# Lời thoại › «Nguồn & dịch»
MODULES: list[type[BaseModule]] = [VideoModule, SRTModule, TTSModule, TrimModule, BGMModule, BrandModule, ColorModule,
                                   BlurModule, StoryModule]
#: ít dùng → nằm sau nút «Thêm tính năng» cuối thanh bên (vẫn mở bằng phím F, vẫn chạy theo mẫu)
MORE = ("blur", "story")
_FKEYS = {f"<F{i + 1}>": m.key for i, m in enumerate(MODULES)}


#: menu «Chạy đến» (nhãn đầy đủ) · câu nhắc khi đổi mức
UNTIL_MENU = {"all": "Tự động hết — lời gốc → dịch → giọng → chờ duyệt",
              "source": "Đến lời gốc — dừng để tự dịch bên ngoài rồi dán vào",
              "translation": "Đến bản dịch — dừng để soát bản dịch rồi mới tạo giọng"}
UNTIL_NOTE = {"all": "Chuẩn bị làm hết rồi chờ duyệt.",
              "source": "nhận diện xong dừng «Chờ bản dịch»: icon copy → dịch ngoài → icon dán → Tiếp tục.",
              "translation": "AI dịch xong dừng «Chờ soát bản dịch»: soát / sửa ở tab Lời thoại → Tiếp tục để tạo giọng."}

class MainWindow(_Root):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.app_state = state
        self.ffmpeg = FFmpeg(state.settings.ffmpeg_path, state.settings.ffprobe_path)
        self.pipeline = Pipeline(state, self.ffmpeg)
        self._waiting: list[tuple[str, Callable[[], None]]] = []   # việc xếp hàng chờ tác vụ / render đang chạy
        self.runner = JobRunner(self.pipeline, self.ui_call)
        self.tasks = TaskRunner(self.ui_call)
        # model AI đang chọn hết lượt / quá tải (không tự đổi model) → báo + nút mở đúng chỗ chọn model
        from app.services import translate_router

        self._unavailable_shown: set[str] = set()
        translate_router.on_unavailable = lambda model, msg: self.ui_call(lambda: self.model_unavailable(model, msg))
        # việc vừa lỗi (chạy lại bằng «Thử lại») + model lúc lỗi «hết lượt» (đổi model xong → nút thành «Thử lại»)
        self._last_retry: Callable[[], None] | None = None
        self._failed_model: str | None = None
        self._failed_key: tuple | None = None          # key lúc lỗi «chưa có / sai key» — Lưu key khác → «Thử lại»
        self._resume = ("Thử lại", "Bấm «Thử lại» để chạy tiếp phần lỗi")     # nút + câu sau khi lưu key
        self.player = AudioPlayer(self.ffmpeg)
        self.modules: dict[str, BaseModule] = {}
        self._rail_buttons: dict[str, ctk.CTkButton] = {}
        self._current_module: str | None = None
        self._save_after: str | None = None
        self._render_started = 0.0
        self.log_lines: list[str] = []
        self.notifications: list[dict] = []
        self._unread = 0                                 # thông báo chưa đọc (mở Trung tâm thông báo → 0)
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
        self.report_callback_exception = self._tk_error

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
        self.after(60000, self._release_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_control()
        self._bind_shortcuts()
        self.bind("<Configure>", self._on_resize)
        self.after(400, self._apply_layout)
        self.dnd_enabled = bool(_HAS_DND and getattr(self, "TkdndVersion", None))
        if self.dnd_enabled:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)

        self.show_module(self.app_state.ui.get("active_module", "video"))
        self._last_input = time.monotonic()
        for seq in ("<KeyPress>", "<ButtonPress>", "<MouseWheel>"):
            self.bind_all(seq, self._mark_input, add="+")
        self.after(3000, self._prewarm)
        if self.app_state.settings.show_guide_on_start:
            self.after(600, lambda: self.open_guide("start"))
        elif self.app_state.ui.get("help_open"):
            self.after(600, lambda: self.open_guide())
        self._active_changed()
        if self.app_state.missing_videos:            # video bị dời / xoá / ổ ngoài chưa cắm
            names = self.app_state.missing_videos
            self.after(700, lambda: self.progress.notice(
                f"{len(names)} video không thấy trên ổ đĩa — tạm ẩn khỏi hàng đợi",
                ", ".join(names[:3]) + ("…" if len(names) > 3 else "") + " · ổ ngoài chưa cắm? cắm lại rồi mở lại app "
                "(hoặc nạp lại video) là có lại phụ đề, cấu hình, trạng thái duyệt"))
        if self.app_state.settings_notice:           # settings.json hỏng → đã dùng bản sao lưu
            self.after(900, lambda: self.progress.result(
                "stopped", "Cài đặt đã khôi phục từ bản sao lưu", self.app_state.settings_notice,
                ("Mở Cài đặt", self.open_settings, "settings", False)))
        if not self.ffmpeg.available:
            self.after(300, lambda: messagebox.showwarning(
                APP_NAME, f"Không tìm thấy ffmpeg/ffprobe.\nĐặt {exe_name('ffmpeg')} + {exe_name('ffprobe')} vào thư mục bin/ "
                          "hoặc chọn đường dẫn trong Cài đặt."))
        self.after(1500, self._startup_check)
        dnd = " · kéo thả: bật" if _HAS_DND and getattr(self, "TkdndVersion", None) else ""
        self.run_bg(self.ffmpeg.version,          # chạy `ffmpeg -version` trong nền, không chặn lúc mở app
                    lambda v, _e: self.log(f"{APP_NAME} sẵn sàng · {(v or '')[:40] or 'chưa có ffmpeg'}{dnd}"))

    def _startup_check(self) -> None:
        """Mở app mà còn thiếu bộ nghe lời thoại / API key → một dòng trên thanh trạng thái + nút «Kiểm tra máy»
        (trước: chỉ biết khi bấm chạy, hoặc phải tự tìm cửa sổ Kiểm tra máy trong Cài đặt)."""
        from app.services.qwen_asr import is_installed

        if self.runner.running or self.tasks.running or self.progress._mode != "idle":
            return
        miss = (["bộ nghe lời thoại"] if not is_installed() else []) + \
               ([f"API key {self.app_state.settings.translate_provider}"]
                if not self.app_state.settings.translate_keys() else [])
        if miss:
            self.progress.result("stopped", "Còn thiếu để làm video tiếng nước ngoài: " + " và ".join(miss),
                                 "Video tiếng Việt / chỉ ghép, che, nhạc vẫn làm được",
                                 ("Kiểm tra máy", self.open_selfcheck, "gpu", False))

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
        self._hbtn(left, "Cài đặt", "settings", self.open_settings, tip="Cài đặt: AI trên máy, API key, giao diện, thư mục xuất").pack(side="left", padx=(24, 4))
        self._hbtn(left, "Hướng dẫn", "book", lambda: self.toggle_help(), tip="Bật / tắt bảng hướng dẫn bên cạnh (Ctrl+H)").pack(side="left", padx=4)
        self._hbtn(left, "Phím tắt", "keyboard", self.open_shortcuts, tip="Danh sách phím tắt").pack(side="left", padx=4)
        self.notif_btn = self._hbtn(left, "Thông báo", "bell", self.open_notifications, tip="Trung tâm thông báo (kết quả xuất, lỗi)")
        self.notif_btn.pack(side="left", padx=4)

        from app.ui.widgets.template_dialogs import TemplateBar

        self.template_bar = TemplateBar(head, self)
        self.template_bar.grid(row=0, column=1, pady=8)
        right = ctk.CTkFrame(head, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e", padx=(0, 10), pady=8)
        self._hbtn(right, "Thư mục xuất", "folder", self.open_output_dir, tip="Mở thư mục chứa video đã xuất").pack(side="left", padx=4)
        self.clean_btn = self._hbtn(right, "Dọn dẹp", "trash", self.clean_workspace,
                                    tip="Xoá file đang làm (phụ đề, giọng lồng, file tạm) và làm trống hàng đợi")
        self.clean_btn.pack(side="left", padx=4)
        # «Chạy đến» ngay cạnh nút chính: Chuẩn bị tự làm tới đâu rồi dừng chờ bạn (nhớ chung cho mọi video)
        self.until_select = Select(right, list(PREPARE_UNTIL), value=self.app_state.settings.prepare_until,
                                   labels=UNTIL_MENU, short=lambda v: PREPARE_UNTIL.get(v, v), width=146, height=36,
                                   menu_width=380, command=self._set_prepare_until, font=T.font(12),
                                   tip="Chuẩn bị chạy đến đâu rồi dừng chờ bạn — bấm Chuẩn bị lần nữa là chạy tiếp từ "
                                       "chỗ dừng. Áp cho mọi video.")
        self.until_select.pack(side="left", padx=(10, 2))
        self.render_btn = button(right, "Xuất video", icon_name="play", variant="primary", size="lg",
                                 command=lambda: self.start_render(list(self.app_state.videos)),
                                 tip="Xuất các video chưa xuất trong hàng đợi (Ctrl+R)")
        self.render_btn.pack(side="left", padx=(8, 4))
        self.stop_btn = self._hbtn(right, "Dừng", "stop", self.stop_all, variant="destructive", state="disabled", tip="Dừng mọi việc đang chạy (Esc)")
        self.stop_btn.pack(side="left", padx=4)
        self._running = False
        self.after(300, self.update_render_button)

    def _hbtn(self, parent, text: str, icon_name: str, command, *, variant: str = "secondary", **kw) -> ctk.CTkButton:
        """Nút header (md, có icon); ghi nhớ nhãn + bề rộng để thu gọn/khôi phục khi cửa sổ đổi cỡ."""
        b = button(parent, text, icon_name=icon_name, variant=variant, command=command, **kw)
        self.header_btns.append([b, text, b.cget("width")])
        return b

    def _compact_text(self, b: ctk.CTkButton) -> str:
        """Header gọn chỉ còn icon — riêng nút Thông báo vẫn hiện số chưa đọc."""
        return str(self._unread) if b is self.notif_btn and self._unread else ""

    def _compact_width(self, b: ctk.CTkButton) -> int:
        text = self._compact_text(b)
        return button_width(b.master, text, icon=True) if text else 36

    def _set_header_label(self, b: ctk.CTkButton, text: str) -> None:
        """Đổi nhãn một nút header (vd. số thông báo) và đo lại bề rộng; tôn trọng chế độ header gọn."""
        for entry in self.header_btns:
            if entry[0] is b:
                entry[1], entry[2] = text, button_width(b.master, text, icon=True)
                if not self._compact_header:
                    relabel(b, text, width=entry[2])
                else:                                # header gọn: còn icon + số thông báo chưa đọc
                    relabel(b, self._compact_text(b), width=self._compact_width(b))
                return

    def _build_body(self) -> None:
        self.rail = ctk.CTkFrame(self, fg_color=T.BG_PANEL, corner_radius=T.RADIUS_CARD, width=RAIL_W)
        self.rail.grid(row=1, column=0, sticky="ns", padx=(10, 4))
        self.rail.grid_propagate(False)
        self.rail.pack_propagate(False)          # giữ đúng bề rộng RAIL_W dù nhãn nút dài
        ctk.CTkFrame(self.rail, fg_color="transparent", height=4, width=1).pack()     # lề trên nhỏ, không tiêu đề
        self._add_rail_buttons()
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

    def _add_rail_buttons(self) -> None:
        """Mục chính trên thanh bên + nút «Thêm tính năng» (mở các mục ít dùng)."""
        for cls in MODULES:
            if cls.key not in MORE:
                self._add_rail_button(cls)
        compact = self._compact_rail
        b = ctk.CTkButton(self.rail, text="Thêm", width=RAIL_W - 10, height=30 if compact else 48, corner_radius=T.RADIUS,
                          fg_color="transparent", hover_color=T.BG_HOVER, text_color=T.TEXT_DIM, font=T.font(8 if compact else 10),
                          image=icon("grid", 14 if compact else 18, T.TEXT_DIM), compound="top", command=self._more_menu)
        b.pack(pady=(0 if compact else 2, 0), padx=5)
        from app.ui.widgets.ui import Tooltip

        Tooltip(b, "Thêm tính năng: " + " · ".join(c.title for c in MODULES if c.key in MORE))
        self._rail_buttons["more"] = b

    def _more_menu(self) -> None:
        from app.ui.widgets.ui import Popover

        keys = [c.key for c in MODULES if c.key in MORE]
        labels = {c.key: c.title for c in MODULES if c.key in MORE}
        cur = self._current_module if self._current_module in keys else ""
        Popover(self._rail_buttons["more"], keys, cur, self.show_module, labels=labels, width=200)

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
        self.template_bar.set_compact(w < 1320)              # ô «Chạy đến» cạnh nút chính chiếm thêm ~40 px
        if compact_header != self._compact_header:
            self._compact_header = compact_header
            for b, label, width in self.header_btns:
                if b is self.render_btn:
                    continue
                relabel(b, self._compact_text(b) if compact_header else label,
                        width=self._compact_width(b) if compact_header else width)
            self.version_label.configure(text="" if compact_header else f"v{APP_VERSION}")
            self.until_select.configure(width=124 if compact_header else 146)
        compact_rail = h < 800
        if compact_rail != self._compact_rail:
            self._compact_rail = compact_rail
            cur = self._current_module
            for b in self._rail_buttons.values():
                b.destroy()
            self._rail_buttons.clear()
            self._add_rail_buttons()
            self.sync_review_rail()
            if cur:
                self.show_module(cur)
        panel_w, help_w = self._side_widths(w)
        if getattr(self, "_panel_help_w", None) != (panel_w, help_w):     # configure(width) vẽ lại cả khung CTk
            self._panel_help_w = (panel_w, help_w)
            self.panel.configure(width=panel_w)
            self.help.set_width(help_w)
        room_for_help = self._room_for_help(w)
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

    @staticmethod
    def _side_widths(w: float) -> tuple[int, int]:
        """(bảng tính năng, bảng hướng dẫn) theo bề rộng cửa sổ."""
        return int(max(360, min(470, w * 0.30))), int(max(300, min(400, w * 0.26)))

    def _room_for_help(self, w: float) -> bool:
        """Đủ chỗ đặt bảng hướng dẫn cạnh bên mà khung xem trước vẫn ≥ 560."""
        panel_w, help_w = self._side_widths(w)
        return w - RAIL_W - panel_w - help_w - 60 >= 560

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
        if key == "subtitle":                   # mục «Phụ đề» cũ (cấu hình / tab Duyệt «Sửa ›») → phần phụ đề ở Lời thoại
            self.show_module("srt")
            self.modules["srt"].open_style()
            return
        if key == "stt":                        # tab «Nhận diện» cũ → khối «Lời gốc» ở Lời thoại › «Nguồn & dịch»
            from app.ui.modules.module_srt import TAB_TR

            self.show_module("srt")
            self.modules["srt"].show_tab(TAB_TR)
            return
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
        prev = "more" if self._current_module in MORE else self._current_module
        on = "more" if key in MORE else key            # mục ít dùng đang mở → sáng nút «Thêm»
        for k, b in self._rail_buttons.items():
            if prev and prev in self._rail_buttons and k not in (prev, on):
                continue              # chỉ vẽ lại 2 nút đổi trạng thái (mỗi configure vẽ lại cả nút CTk)
            b.configure(fg_color=T.ACCENT_SOFT if k == on else "transparent",
                        text_color=T.ACCENT if k == on else T.TEXT_DIM,
                        image=icon(MODULE_ICONS.get(k, "grid"), 20, T.ACCENT if k == on else T.TEXT_DIM))
        self._current_module = key
        self._update_scope_strip()
        self.app_state.ui["active_module"] = key
        self.app_state.mark_dirty()
        if self._help_open and self.help.key not in ("start", "flow_dub", "flow_short", "faq", "settings", "shortcuts"):
            self.help.show(key)
        self.preview.set_edit_tools(key in ("srt", "blur"))
        if key == "blur":
            self.preview.set_tool("B")
        elif key == "srt":
            self.preview.set_tool("T")
        else:
            self.preview._redraw()

    def current_module_key(self) -> str:
        return self._current_module or ""

    def _mark_input(self, _e=None) -> None:
        self._last_input = time.monotonic()

    def _prewarm(self) -> None:
        """Dựng sẵn (ẩn) một tab chưa mở khi người dùng rảnh — dựng widget tốn 0,2–0,7 s, hiện tab đã dựng chỉ 0,1–0,27 s.
        Đang bấm / gõ / cuộn hoặc đang chạy tác vụ thì hoãn; mỗi lần một tab."""
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        todo = [k for k in PREWARM if k not in self.modules]
        if not todo:
            return
        if time.monotonic() - self._last_input < PREWARM_IDLE or self._running:
            self.after(1000, self._prewarm)
            return
        key = todo[0]
        cls = next((c for c in MODULES if c.key == key), None)
        try:
            if cls is not None:
                m = self.modules[key] = cls(self.panel, self)
                m._fresh = False                 # dựng từ lâu → lần hiện đầu vẫn đổ lại giá trị mới nhất
        except Exception:
            logger.exception("Không dựng sẵn được tab %s", key)
            PREWARM.remove(key)                  # lỗi thì để lúc mở tab dựng như cũ, không thử lại liên tục
        self.after(600, self._prewarm)

    def clean_workspace(self, parent=None) -> None:
        """Nút «Dọn dẹp»: xoá file đang làm + làm trống hàng đợi (hỏi trước). Video đã xuất, mẫu, cài đặt giữ nguyên."""
        from app.services import cleanup

        if self.runner.running or self.tasks.running:
            messagebox.showinfo(APP_NAME, "Đang chạy — bấm Dừng trước khi dọn dẹp.", parent=parent or self)
            return
        mb = sum(cleanup.sizes().values())
        n = len(self.app_state.videos)
        if not messagebox.askyesno(
                "Dọn dẹp", f"Xoá hết file đang làm ({mb:.1f} MB)?\n\n"
                           "• Phụ đề, bản dịch, giọng lồng, file tạm\n"
                           + (f"• {n} video trong hàng đợi\n" if n else "")
                           + "\nGiữ lại: video đã xuất, mẫu, cài đặt, model AI.", icon="warning", default="no", parent=parent or self):
            return
        self.preview.stop_play()
        self.app_state.clear_videos()
        freed, stuck = cleanup.clean(keep=[self.app_state.output_dir or VIDEOS_DIR])
        self.refresh_modules()
        note = f" · {stuck} file đang mở, chưa xoá được" if stuck else ""
        self.log(f"Đã dọn dẹp: giải phóng {freed:.1f} MB{note}.")
        self.progress.result("ok", "Đã dọn dẹp", f"Giải phóng {freed:.1f} MB{note}")

    def refresh_modules(self) -> None:
        for m in self.modules.values():
            m.refresh()
        self.preview.schedule_refresh(0)

    def notify_blur_selection(self) -> None:
        m = self.modules.get("blur")
        if m is not None:
            m.refresh()

    def notify_box_edit(self, kind: str) -> None:
        if kind == "blur":
            m = self.modules.get("blur")
            if m is not None:
                m.refresh()
            return
        m = self.modules.get("srt")
        if m is not None:
            m.refresh_subtitle()
        self.preview.refresh_sub_chips()

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
                self.log("Kéo SRT: hãy nạp / chọn video trước.", notice=True)
            else:
                self.pipeline.attach_srt(item, srts[0])
                self.mark_changed(item)
                self.app_state.emit("active_changed")
                self.app_state.mark_dirty()
                self.log(f"Đã gắn phụ đề {Path(srts[0]).name} cho {item.name} (bản dịch cũ bỏ — dịch lại theo phụ đề "
                         "mới).", notice=True)

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
        self._offer_retry_after_model()

    def open_settings(self, tab: str | None = None) -> None:
        from app.ui.widgets.settings_window import SettingsWindow

        win = getattr(self, "_settings_win", None)
        if win is not None and win.winfo_exists():       # đang mở → đưa lên trước, khỏi dựng lại cả cửa sổ
            win.deiconify()
            win.lift()
            win.focus_force()
            if tab == "model":
                win.focus_model()
            elif tab:
                win.show_page({"shortcuts": "guide"}.get(tab, tab))
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
        self.after(150, lambda: self.open_settings(tab="look"))    # mở lại đúng trang Giao diện

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
        win = getattr(self, "_shortcuts_win", None)
        if win is not None and win.winfo_exists():       # đã mở → đưa lên trước, không mở thêm bản nữa
            win.deiconify()
            win.lift()
            win.focus_force()
            return
        self._shortcuts_win = ShortcutsWindow(self)

    def open_guide(self, key: str | None = None) -> None:
        """Hiện bảng hướng dẫn cạnh bảng tính năng, đúng chủ đề (mặc định: module đang xem)."""
        key = key or self.current_module_key() or "start"
        k = self._scaling()
        w = self.winfo_width() / k
        if w > 50 and not self._room_for_help(w) and not self._help_open:
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
            self.close_help()
            return
        self.open_guide(key)

    def help_hotkey(self) -> None:
        """Ctrl+H: đang mở thì đóng (bất kể chủ đề), đang đóng thì mở đúng chủ đề tab đang xem."""
        if self._help_open:
            self.close_help()
        else:
            self.open_guide()

    def close_help(self) -> None:
        """Nút ✕ của bảng hướng dẫn: luôn đóng."""
        if not self._help_open:
            return
        self.help.grid_forget()
        self._help_open = False
        self._help_wanted = False
        self.app_state.ui["help_open"] = False
        self.app_state.mark_dirty()
        self._apply_layout()
        if self.app_state.settings.show_guide_on_start:      # đã tự đóng một lần → lần sau không tự bật nữa
            self.app_state.settings.show_guide_on_start = False
            self.app_state.save_settings()

    def open_guide_window(self, key: str | None = None) -> None:
        from app.ui.widgets.guide_window import open_guide

        open_guide(self, key or self.current_module_key() or "start")

    def open_notifications(self) -> None:
        if self._notif_win is None or not self._notif_win.winfo_exists():
            self._notif_win = NotificationCenter(self)
        else:
            self._notif_win.refresh()
            self._notif_win.lift()
        self._unread = 0
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

    def open_output_dir(self, folder: str | None = None) -> None:
        d = folder or self.app_state.output_dir or str(VIDEOS_DIR)
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except OSError:
            messagebox.showerror(APP_NAME, f"Không mở được thư mục xuất:\n{d}\n\nỔ đĩa đã rút hoặc đường dẫn sai — chọn "
                                           "lại ở tab Video › «Tên & nơi lưu file xuất».")
            return
        open_path(d)

    def _tk_error(self, exc, val, tb) -> None:
        """Lỗi trong lệnh nút bấm: bản .exe không có cửa sổ console nên trước đây bấm xong không thấy gì — ghi log +
        báo một câu trên thanh trạng thái."""
        logger.error("Lỗi giao diện", exc_info=(exc, val, tb))
        if isinstance(val, (KeyboardInterrupt, SystemExit)):
            return
        try:
            msg = friendly_error(str(val) or exc.__name__)
            self.add_notification("Thao tác lỗi", msg, "error")
            if not (self.runner.running or self.tasks.running):
                self.progress.result("error", "Thao tác vừa rồi bị lỗi", msg + " — chi tiết ở log")
        except Exception:                            # noqa: BLE001 — đang báo lỗi thì không được lỗi thêm
            pass

    def model_unavailable(self, model: str, msg: str) -> None:
        """Model dịch / nhận diện đang chọn hết lượt hôm nay hoặc quá tải lâu: máy KHÔNG tự đổi model — báo một lần
        mỗi model (hộp nhỏ, không chặn) kèm nút «Chọn model…» mở đúng chỗ đổi model."""
        self.add_notification("Model AI không dùng được", msg, "error")
        if model in self._unavailable_shown:
            return
        self._unavailable_shown.add(model)
        from app.ui.widgets.ui import button

        dlg = ctk.CTkToplevel(self)
        dlg.title("Model AI không dùng được")
        dlg.resizable(False, False)
        dlg.transient(self)
        ctk.CTkLabel(dlg, text=msg, font=T.font(12), wraplength=420, justify="left", anchor="w").pack(
            fill="x", padx=16, pady=(16, 10))
        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 14))
        button(row, "Để sau", variant="ghost", command=dlg.destroy).pack(side="right")
        button(row, "Chọn model…", icon_name="settings", variant="primary",
               command=lambda: (dlg.destroy(), self.open_settings(tab="model"))).pack(side="right", padx=(0, 8))
        dlg.after(50, dlg.lift)

    def _error_action(self, message: str | None, retry: Callable[[], None] | None = None) -> tuple | None:
        """Nút nên bấm cho câu lỗi: model hết lượt hôm nay → «Chọn model…» (chọn xong nút thành «Thử lại»); chưa cài AI
        trên máy → «Cài…»; còn lại (cả model quá tải tạm thời) → «Thử lại» chạy lại đúng việc vừa lỗi."""
        from app.services.qwen_asr import MISSING_MARK
        from app.services.translate_router import UNAVAILABLE_MARK

        self._last_retry = retry
        self._failed_model = None
        self._failed_key = None
        self._resume = ("Thử lại", "Bấm «Thử lại» để chạy tiếp phần lỗi")
        msg = message or ""
        if UNAVAILABLE_MARK in msg and "quá tải" not in msg:
            self._failed_model = self.app_state.settings.active_model()
            return ("Chọn model…", lambda: self.open_settings(tab="model"), "settings", True)
        if MISSING_MARK in msg:
            return ("Cài…", lambda: self.open_settings(tab="local"), "download", True)
        low = msg.lower()
        if re.search(r"chưa có (gemini|deepseek) api key", low) or ("api key" in low and ("401" in low or "403" in low)):
            # key nhập xong + Lưu → nút thành «Thử lại» như khi đổi model
            self._failed_key = tuple(self.app_state.settings.translate_keys())
            return ("Nhập key…", lambda: self.open_settings(tab="cloud"), "settings", True)
        return ("Thử lại", retry, "refresh", True) if retry else None

    def _start_control(self) -> None:
        """Cổng lệnh cục bộ cho CLI / MCP / agent (``app.agent``) — chỉ nghe 127.0.0.1, có mã bí mật."""
        self._control = None
        try:
            from app.agent.control import ControlServer
            from app.agent.ops import GuiHost, dispatch

            host = GuiHost(self)
            self._control = ControlServer(lambda op, args: dispatch(host, op, args))
            self._control.start()
        except Exception:                               # noqa: BLE001 — không mở được cổng thì app vẫn chạy bình thường
            logger.warning("Không mở được cổng lệnh cục bộ", exc_info=True)
            self._control = None

    def preflight_problem(self, items: list[VideoItem]) -> str | None:
        """Như ``_preflight`` nhưng không bật hộp thoại — câu lý do (lệnh từ CLI / MCP), None = chạy được."""
        from app.services.ocr_hardsub import available as ocr_ok
        from app.services.qwen_asr import is_installed

        need = [self.pipeline_for(it).needs(it) for it in items]
        st = self.app_state.settings
        if not st.translate_keys() and any("gemini" in n for n in need):
            return f"chưa có API key {st.translate_provider} để dịch — nhập ở Cài đặt › AI dùng API key"
        if any("qwen" in n for n in need) and not is_installed() and not ocr_ok():
            return "chưa cài bộ nghe lời thoại (Qwen3-ASR) — Cài đặt › AI trên máy"
        return None

    def _preflight(self, items: list[VideoItem], rerun: Callable[[], None]) -> bool:
        """Trước khi chạy: cần dịch mà chưa có API key Gemini / cần nghe lời thoại mà chưa cài Qwen3-ASR → báo ngay
        (trước đây nhận diện xong cả chục phút mới lỗi ở bước dịch). False = không chạy."""
        from app.services.qwen_asr import is_installed

        need = [self.pipeline_for(it).needs(it) for it in items]
        no_key = not self.app_state.settings.translate_keys() and any("gemini" in n for n in need)
        from app.services.ocr_hardsub import available as ocr_ok

        # chưa cài Qwen nhưng đọc được chữ in sẵn → video có phụ đề vẫn chạy được, video không có thì báo riêng từng video
        no_qwen = any("qwen" in n for n in need) and not is_installed() and not ocr_ok()
        if not (no_key or no_qwen):
            return True
        provider = self.app_state.settings.translate_provider
        lines = ([f"• API key {provider} — để dịch phụ đề (model dịch đang chọn: "
                  f"{self.app_state.settings.active_model()})"] if no_key else []) + \
                (["• Bộ nghe lời thoại (Qwen3-ASR) — để nhận diện"] if no_qwen else [])
        page = "cloud" if no_key else "local"
        if no_key:                                  # nhập key + Lưu → nút trên thanh trạng thái thành «Chạy tiếp»
            self._error_action(None)
            self._last_retry, self._failed_key = rerun, ()
            self._resume = ("Chạy tiếp", "Bấm «Chạy tiếp» để chạy")
            self.progress.result("error", f"Chưa nhập API key {provider}", "Cần để dịch phụ đề — Cài đặt › AI dùng API key",
                                 ("Nhập key…", lambda: self.open_settings(tab="cloud"), "settings", True))
        else:
            self.progress.result("error", "Chưa cài bộ nghe lời thoại", "Cài ở Cài đặt › AI trên máy (một lần)",
                                 ("Cài…", lambda: self.open_settings(tab="local"), "download", True))
        if messagebox.askyesno(APP_NAME, "Còn thiếu để chạy:\n\n" + "\n".join(lines) + "\n\nMở Cài đặt?"):
            self.open_settings(tab=page)
        return False

    def check_gemini_keys(self, on_done: Callable[[str], None] | None = None) -> None:
        """Gọi thử từng key Gemini (models.get — không tốn lượt dịch) sau khi lưu Cài đặt: key sai / model không có →
        báo ngay trên thanh trạng thái, khỏi đợi tới lúc dịch mới lỗi. ``on_done(câu tóm tắt)``: cho trang Cài đặt."""
        from app.services.gemini_models import check_key
        from app.services.translate_router import DEFAULT_MODEL

        st = self.app_state.settings
        keys, model = st.gemini_keys(), st.translate_model or DEFAULT_MODEL
        if not keys:
            return

        def done(result, error) -> None:
            if error or not result:
                return
            bad = [(i + 1, r.reason) for i, r in enumerate(result) if r.ok is False]
            good = [r for r in result if r.ok]
            no_model = bool(good) and all(r.has_model is False for r in good)
            n = len(result)
            if bad:
                which = ", ".join(str(i) for i, _ in bad)
                title = f"Key Gemini thứ {which} không dùng được" if n > 1 else "Key Gemini không dùng được"
                detail = bad[0][1] + (f" — {len(good)} key còn lại vẫn dùng được" if good else "")
                summary = f"⚠ {title}: {detail}"
                self._show_key_problem(title, detail, ("Nhập key…", lambda: self.open_settings(tab="cloud"),
                                                       "settings", True))
            elif no_model:
                title = f"Model «{model}» không có với key của bạn"
                summary = f"⚠ {title} — chọn model khác (bấm ↻ để lấy danh sách)."
                self._show_key_problem(title, "Chọn model khác ở Cài đặt › AI dùng API key",
                                       ("Chọn model…", lambda: self.open_settings(tab="model"), "settings", True))
            elif not good:
                summary = f"Chưa kiểm được key ({result[0].reason}) — sẽ thử lại khi dịch."
            else:
                summary = f"✓ Key Gemini dùng được ({len(good)}/{n}) · model «{model}» có sẵn."
            self.log(summary)
            if on_done is not None:
                on_done(summary)

        self.run_bg(lambda: [check_key(k, model) for k in keys], done)

    def _show_key_problem(self, title: str, detail: str, action: tuple) -> None:
        """Key / model có vấn đề: báo trên thanh trạng thái (đang chạy việc khác thì chỉ ghi thông báo, không đè)."""
        self.add_notification(title, detail, "error")
        if not (self.runner.running or self.tasks.running):
            self.progress.result("error", title, detail, action)

    def _offer_retry_after_model(self) -> None:
        """Lưu Cài đặt sau lỗi «model hết lượt»: đã chọn model khác → nút trên thanh trạng thái thành «Thử lại».
        Tương tự lỗi «chưa có / sai key»: đã nhập key khác."""
        keys = tuple(self.app_state.settings.translate_keys())
        if self._failed_key is not None and self._last_retry is not None and keys and keys != self._failed_key:
            self._failed_key = None
            label, detail = self._resume
            self.progress.result("ok", "Đã lưu key", detail, (label, self._last_retry, "refresh", True))
            return
        model = self.app_state.settings.active_model()
        if self._failed_model is None or self._last_retry is None or model == self._failed_model:
            return
        self._failed_model = None
        self.progress.result("ok", "Đã đổi model", f"Dùng «{model}» — bấm «Thử lại» để chạy tiếp phần lỗi",
                             ("Thử lại", self._last_retry, "refresh", True))

    def add_notification(self, title: str, body: str, kind: str = "info") -> None:
        self.notifications.append({"title": title, "body": body, "kind": kind, "time": now_str()})
        del self.notifications[:-100]
        self._unread += 1
        self._set_header_label(self.notif_btn, f"{self._unread} Thông báo")
        if self._notif_win is not None and self._notif_win.winfo_exists():
            self._notif_win.refresh()

    # ── Phím tắt ─────────────────────────────────────────────────────────
    def _bind_shortcuts(self) -> None:
        for seq, key in _FKEYS.items():
            self.bind(seq, lambda _e, k=key: self.show_module(k))
        mod_keys = {
            "s": lambda _e: (self.app_state.save_config(), self.log("Đã lưu cấu hình.", notice=True)),
            "r": lambda _e: self.start_render(list(self.app_state.videos)),
            "z": lambda e: self._if_free(e, self.preview.undo),
            "y": lambda e: self._if_free(e, self.preview.redo),
            "Shift-Z": lambda e: self._if_free(e, self.preview.redo),
            "e": lambda e: self._if_free(e, self._open_srt_editor),
            "o": lambda e: self._if_free(e, self._add_videos),
            "h": lambda e: self._if_free(e, self.help_hotkey),
            "slash": lambda e: self._if_free(e, self.help_hotkey),
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
        self.bind("<Escape>", self._on_escape)

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
            "<Delete>": self.preview.delete_key,
            "<BackSpace>": self.preview.delete_key,
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
        self.log(f"Cắt: {'bỏ đầu' if which == 'head' else 'bỏ đuôi'} tại {t:.2f}s", notice=True)

    def _add_videos(self) -> None:
        """Ctrl+O: nạp video từ tab nào cũng được."""
        self.show_module("video")
        self.modules["video"].queue.add_files()

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
                retry = None if stopped else (lambda: self.run_task(name, fn, done))
                self.progress.result("stopped" if stopped else "error", f"{name}: đã dừng" if stopped else f"{name} lỗi",
                                     "" if stopped else friendly_error(str(error)),
                                     None if stopped else self._error_action(str(error), retry))
                self.log(f"✖ {name}: {error}")
                if not stopped:
                    self.add_notification(f"{name} lỗi", str(error)[:300], "error")
            else:
                self.progress.result("ok", f"{name} xong")
            done(result, error)

        self.tasks.start(name, fn, on_done=_done)

    # ── Hàng chờ việc: bấm khi đang bận → hỏi xếp hàng, xong việc trước tự chạy ──
    def _offer_wait(self, name: str, action: Callable[[], None]) -> None:
        busy = "hàng đợi" if self.runner.running else f"«{self.tasks.name}»"
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
        """Nút ↻ của video lỗi trong hàng đợi: chuẩn bị lại (chưa duyệt) hoặc render lại đúng video đó."""
        if item not in self.app_state.videos:
            return
        self.log(("Xuất lại: " if item.status in ("Xong", "Cần xuất lại") else "Thử lại: ") + item.name)
        if item.review != "approved":
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
        keep_awake(running)                          # lô dài 1–3 giờ: máy không tự ngủ giữa chừng
        self.render_btn.configure(state="disabled" if running else "normal")
        self.clean_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _build_progress(self) -> None:
        self.progress = ProgressPanel(self, self)
        self.progress.grid(row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=(4, 10))

    def _show_result(self, items: list[VideoItem], ok: int, fail: int, elapsed: float, prep: bool) -> None:
        """Kết quả một lượt chạy trên thanh trạng thái: một câu + đúng một việc nên bấm tiếp."""
        from app.ui.progress_panel import _clock, short_path

        took = f"mất {_clock(elapsed)}"
        skipped = sum(1 for it in items if it.status == "Đã bỏ qua")
        skip_note = f" · bỏ qua {skipped}" if skipped else ""
        if fail:
            first = next((it for it in items if it.status == "Lỗi"), items[0])
            self.progress.result("error", f"{fail} video lỗi" + (f" · {ok} xong" if ok else "") + skip_note,
                                 f"{first.name}: {friendly_error(first.message, first)}",
                                 self._error_action(first.message, self.retry_failed))
        elif any(it.status == "Đã huỷ" for it in items):
            self.progress.result("stopped", "Đã dừng", f"{ok}/{len(items)} video đã xong{skip_note}")
        elif skipped and not ok:                    # bỏ qua hết (vd máy đang bận chạy game) → mời chạy lại
            again = [it for it in items if it.status == "Đã bỏ qua"]
            self.progress.result("stopped", f"Đã bỏ qua {skipped} video", "Bấm «Chạy lại» khi máy rảnh",
                                 ("Chạy lại", lambda: self._run_queue(again, mode="prepare" if prep else "render"),
                                  "refresh", True))
        elif prep and any(it.status in WAITING for it in items):
            # «Chạy đến» dừng chờ bạn: mở đúng chỗ làm tiếp (tab Lời thoại của video đầu tiên đang chờ)
            first = next(it for it in items if it.status in WAITING)
            src = sum(1 for it in items if it.status == WAITING[0])
            rev = sum(1 for it in items if it.status == WAITING[1])
            what = " · ".join(x for x in (f"{src} chờ bản dịch" if src else "", f"{rev} chờ soát bản dịch" if rev else "")
                              if x)
            todo = ("copy lời gốc → dịch ngoài → dán bản dịch" if src else "soát / sửa bản dịch") + \
                ", xong bấm «Tiếp tục» trên cùng để tạo giọng"
            self.progress.result("ok", (f"Chuẩn bị xong {ok} video · " if ok else "") + what + skip_note,
                                 f"{took} · {todo}", ("Mở Lời thoại", lambda: self.open_text_of(first), "subtitles", True))
        elif prep:
            self.progress.result("ok", f"Chuẩn bị xong {ok} video" + skip_note,
                                 f"{took} · xem từng video ở tab «Duyệt», ưng thì bấm «Duyệt», xong bấm «Xuất» trên cùng")
        else:
            done = next((it.output_path for it in items if it.status == "Xong" and it.output_path), None)
            out = str(Path(done).parent) if done else (self.app_state.output_dir or str(VIDEOS_DIR))
            self.progress.result("ok", f"Đã xuất {ok} video" + skip_note, f"{took} · lưu ở {short_path(out)}",
                                 ("Mở thư mục", lambda: self.open_output_dir(out), "openfolder", False))

    def retry_failed(self) -> None:
        """Nút «Thử lại» ở thanh trạng thái: chạy lại mọi video lỗi (chưa duyệt → chuẩn bị lại, đã duyệt → xuất lại)."""
        failed = [it for it in self.app_state.videos if it.status == "Lỗi"]
        if not failed:
            self.progress.result("ok", "Không còn video lỗi", "Video lỗi đã được xoá hoặc đã chạy lại")
            return
        prep = [it for it in failed if it.review != "approved"]
        ren = [it for it in failed if it not in prep]
        for it in prep:
            it.review = ""
        if prep:
            self._run_queue(prep, mode="prepare")
            if ren:
                self._waiting.append((f"Xuất {len(ren)} video", lambda: self._run_queue(ren, mode="render")))
                self._show_waiting()
        elif ren:
            self._run_queue(ren, mode="render")

    # ── Log ───────────────────────────────────────────────────────────────
    def log(self, msg: str, notice: bool = False) -> None:
        """``notice``: thao tác người dùng vừa làm (Ctrl+S, đặt mốc cắt, chọn mẫu…) → cũng hiện một dòng trên thanh
        trạng thái (log là cửa sổ ẩn, trước đây bấm xong không thấy gì)."""
        logger.info(msg)
        self._append_log(time.strftime("%H:%M:%S") + "  " + msg)
        if notice and hasattr(self, "progress"):
            self.progress.notice(msg)

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
    def _set_prepare_until(self, value: str) -> None:
        """Ô «Chạy đến» cạnh nút chính — nhớ chung cho mọi video (settings.json)."""
        st = self.app_state.settings
        if value not in PREPARE_UNTIL or value == st.prepare_until:
            return
        st.prepare_until = value
        self.app_state.save_settings()
        self.update_render_button()
        for m in self.modules.values():                # tab Dịch / Lời thoại nhắc đúng cách làm của mức mới
            if getattr(m, "key", "") == "srt" and hasattr(m, "on_active_changed"):
                m.on_active_changed()
        self.log(f"Chuẩn bị chạy đến: {PREPARE_UNTIL[value]} — {UNTIL_NOTE[value]}", notice=True)

    def open_text_of(self, item: VideoItem) -> None:
        """Mở tab Lời thoại của ``item`` (video đang dừng chờ bản dịch / chờ soát bản dịch)."""
        if item in self.app_state.videos and self.app_state.active is not item:
            self.app_state.set_active(self.app_state.videos.index(item))
        self.edit_in("srt")

    def start_render(self, items: list[VideoItem], audio_only: bool = False, *, force: bool = False) -> None:
        """Nút chính: video chưa chuẩn bị (hoặc đang dừng chờ bạn ở «Chạy đến») → Chuẩn bị / chạy tiếp; đã duyệt →
        xuất; còn lại → mở tab Duyệt. ``force``: xuất đúng các video được đưa vào (đã duyệt)."""
        if not items:
            messagebox.showinfo(APP_NAME, "Hàng đợi trống — bấm Nạp video trước.")
            return
        srt = self.modules.get("srt")
        if srt is not None and srt.editor.item in items and srt.editor.dirty():
            srt.editor.save(regen=False)            # câu sửa dở → lưu trước khi lọc video đã xuất (sửa xong là «Cần xuất lại»)
        if not force:
            failed = [it for it in items if not it.review and it.status == "Lỗi"]
            todo = [it for it in items if not it.review and it.status != "Lỗi"]
            ready = [it for it in items if it.review == "approved" and it.status != "Xong"]
            if not todo and not ready and failed:
                todo = failed                        # chỉ còn video lỗi → chuẩn bị lại các video đó
            if todo and ready:
                # có video mới chưa chuẩn bị lẫn video đã duyệt → cho chọn, không ép chuẩn bị trước
                ans = messagebox.askyesnocancel(APP_NAME, f"Có {len(ready)} video đã duyệt và {len(todo)} video mới chưa "
                                                          f"chuẩn bị.\n\nCó = Xuất {len(ready)} video đã duyệt\n"
                                                          f"Không = Chuẩn bị {len(todo)} video mới")
                if ans is None:
                    return
                if not ans:
                    self._run_queue(todo, mode="prepare")
                    return
            elif todo:
                self._run_queue(todo, mode="prepare")
                return
            if not ready:
                self.open_review()
                return
            pending = sum(1 for it in items if it.review == "pending")
            if pending and not messagebox.askyesno(APP_NAME, f"Còn {pending} video chờ duyệt (chưa xuất). "
                                                             f"Vẫn xuất {len(ready)} video đã duyệt?"):
                self.open_review()
                return
            items = ready
        self._run_queue(items, mode="render", audio_only=audio_only)

    def _run_queue(self, items: list[VideoItem], *, mode: str = "render", audio_only: bool = False,
                   quiet: bool = False) -> str | None:
        """``quiet`` (lệnh từ CLI / MCP): không bật hộp thoại nào — trả câu lý do không chạy được; None = đã chạy."""
        if not self.ffmpeg.available:
            if not quiet:
                messagebox.showerror(APP_NAME, "Không có ffmpeg — xem Cài đặt.")
            return "không có ffmpeg"
        if quiet:
            why = self.preflight_problem(items)
            if why:
                return why
        elif not self._preflight(items, lambda: self._run_queue(items, mode=mode, audio_only=audio_only)):
            return "thiếu cấu hình"
        if self.tasks.running or self.runner.running:   # không đổi audio_only của pipeline khi hàng đợi đang chạy
            if quiet:
                return "đang chạy việc khác — run(action='wait') rồi gọi lại"
            label = "Chuẩn bị" if mode == "prepare" else ("Xuất MP3" if audio_only else "Xuất")
            self._offer_wait(f"{label} {len(items)} video",
                             lambda: self._run_queue(items, mode=mode, audio_only=audio_only))
            return "đang chạy việc khác"
        self.preview.stop_play()
        out_dir = Path(self.app_state.output_dir or VIDEOS_DIR)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            if not quiet:
                messagebox.showerror(APP_NAME, f"Không tạo được thư mục xuất:\n{out_dir}\n\nỔ đĩa đã rút hoặc đường dẫn "
                                               "sai — chọn lại ở Cài đặt › Xuất video.")
            return f"không tạo được thư mục xuất {out_dir}"
        low = []
        for d in {out_dir, JOBS_DIR}:
            try:
                free = shutil.disk_usage(d).free / 2 ** 30
            except OSError:
                continue
            if free < 3:
                low.append(f"{d.anchor or d} còn {free:.1f} GB")
        if low and quiet:
            self.log("⚠ Ổ đĩa sắp đầy: " + ", ".join(sorted(set(low))) + " — vẫn chạy theo lệnh agent.")
        elif low and not messagebox.askyesno(APP_NAME, "Ổ đĩa sắp đầy: " + ", ".join(sorted(set(low))) + ".\n\n"
                                             "Mỗi giờ video cần khoảng 3–4 GB file tạm khi xuất — có thể lỗi giữa chừng. "
                                             "Bấm «Dọn dẹp» trên cùng để giải phóng. Vẫn chạy?"):
            return "người dùng huỷ (ổ đĩa sắp đầy)"
        srt = self.modules.get("srt")
        if srt is not None and srt.editor.item in items and srt.editor.dirty():
            srt.editor.save(regen=False)        # câu sửa dở ở Lời thoại → lưu trước (giọng tạo lại trong lượt chạy)
        for it in items:
            it.status, it.progress, it.message = "Chờ", 0.0, ""
            self.queue_update(it)
        self._render_started = time.time()
        total = len(items)
        finished = ("Xong", "Lỗi", "Đã huỷ", "Chờ duyệt", "Đã bỏ qua", *WAITING)
        self.pipeline.audio_only = audio_only
        prep = mode == "prepare"

        def on_update(it: VideoItem) -> None:
            self.queue_update(it)
            if it.status in ("Đang xử lý", "Đang chuẩn bị"):
                # số thứ tự theo hàng đợi + tiến độ cộng dồn mọi video (bước nghe lời thoại chạy trước cho cả lượt)
                pos = next((i for i, x in enumerate(items) if x is it), 0) + 1
                overall = sum(1.0 if x.status in finished else x.progress for x in items) / total
                self.progress.update_item(it, pos, total, overall)
            elif it.status in finished:
                self.app_state.mark_dirty()          # mỗi video xong lưu ngay — máy sập giữa lô không mất phụ đề / duyệt

        def on_done(ok: int, fail: int) -> None:
            stopped = self.runner.cancel.is_set()
            self.set_running(False)
            self.after(400, self._run_waiting)
            self.update_render_button()
            elapsed = time.time() - self._render_started
            self._show_result(items, ok, fail, elapsed, prep)
            if stopped:                              # bấm Dừng: không báo «xong», không bật toast, không tự nhảy tab
                msg = f"Đã dừng: {ok} video xong · {elapsed / 60:.1f} phút"
                self.log(msg)
                self.add_notification("Đã dừng", msg, "info")
                self.app_state.save_config()
                self.pipeline.audio_only = False
                return
            skipped = sum(1 for it in items if it.status == "Đã bỏ qua")
            skip_note = f", bỏ qua {skipped}" if skipped else ""
            waiting = sum(1 for it in items if it.status in WAITING)
            src = sum(1 for it in items if it.status == WAITING[0])
            if src:                                  # «Chạy đến: Đến lời gốc»: nhận diện xong, chờ dán bản dịch
                skip_note += f", {src} chờ bản dịch (copy lời gốc → dịch ngoài → dán)"
            if waiting - src:                        # «Chạy đến: Đến bản dịch»: AI dịch xong, chờ soát rồi tạo giọng
                skip_note += f", {waiting - src} chờ soát bản dịch"
            if prep:
                msg = f"Chuẩn bị xong: {ok} video chờ duyệt, {fail} lỗi{skip_note} · {elapsed / 60:.1f} phút"
                self.log(msg)
                if ok or fail or waiting:                # bỏ qua hết → không báo «chờ duyệt»
                    self.add_notification("Chuẩn bị xong — chờ bạn duyệt", msg, "ok" if not fail else "error")
                    notify("Edit Studio — Chờ duyệt", msg, enabled=self.app_state.settings.windows_toast)
                self.app_state.save_config()
                if ok and (self._current_module in (None, "video", "review")):
                    self.open_review()
                elif ok:                             # đang làm ở tab khác → không giật tab, chỉ báo
                    self.progress.result("ok", f"Chuẩn bị xong {ok} video", "Mở tab Duyệt khi sẵn sàng",
                                         ("Mở Duyệt", self.open_review, "check", True))
                return
            msg = f"Hoàn tất: {ok} xong, {fail} lỗi{skip_note} · {elapsed / 60:.1f} phút"
            self.log(msg)
            if ok or fail:
                self.add_notification("Xuất xong" if fail == 0 else "Xuất có lỗi", msg, "ok" if fail == 0 else "error")
                notify("Edit Studio — Xuất xong", msg, enabled=self.app_state.settings.windows_toast)
            self.app_state.save_config()
            self.pipeline.audio_only = False

        if not self.runner.start(items, on_update=on_update, on_log=self.log, on_done=on_done, mode=mode):
            return "không bắt đầu được"
        self.set_running(True)
        self.progress.start_queue(items, mode, on_skip=self.skip_current if total > 1 else None)
        if prep:
            self.log(f"Chuẩn bị {total} video (phụ đề, dịch, giọng lồng) — xong sẽ mở tab «Duyệt».")
        else:
            self.log(f"Bắt đầu xuất {'MP3' if audio_only else 'MP4'} {total} video → {self.app_state.output_dir}")
        return None

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

    MODEL_IDLE_S = 180                  # model AI không dùng quá 3 phút → trả RAM/VRAM cho máy

    def _release_tick(self) -> None:
        """Whisper / VieNeu nặng 0,5–2 GB; xong việc mà lâu không dùng thì giải phóng (lần sau tự nạp lại)."""
        if not (self.runner.running or self.tasks.running):
            freed = []
            for mod, name in (("app.services.whisper_stt", "Whisper"), ("app.services.tts.vieneu", "VieNeu")):
                m = sys.modules.get(mod)              # chưa từng dùng thì module chưa nạp → không có gì để bỏ
                try:
                    if m is not None and m.release_idle(self.MODEL_IDLE_S):
                        freed.append(name)
                except Exception:
                    logger.warning("Giải phóng model %s lỗi", name, exc_info=True)
            if freed:
                self.log(f"Đã giải phóng model {', '.join(freed)} khỏi bộ nhớ (lâu không dùng) — lần sau tự nạp lại.")
        self.after(60000, self._release_tick)

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
        from app.ui.widgets.common import auto_wrap

        self.scope_label = auto_wrap(ctk.CTkLabel(self.scope_strip, text="", font=T.font(11), anchor="w",
                                                  justify="left", text_color=T.TEXT, wraplength=210))
        self.scope_label.grid(row=0, column=0, sticky="ew", padx=(10, 4), pady=6)
        self.scope_all_btn = button(self.scope_strip, "Áp cho tất cả", size="sm", variant="ghost",
                                    command=self._apply_to_all,
                                    tip="Dùng cách chỉnh của video này làm cấu hình chung cho các video chưa chỉnh riêng")
        self.scope_all_btn.grid(row=0, column=1, padx=2)
        self.scope_back_btn = button(self.scope_strip, "‹ Về Duyệt", size="sm", variant="secondary",
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
        # chỉ khi video đã chuẩn bị (chưa chuẩn bị thì chỉnh là chỉnh chung cả hàng đợi); tab Truyện không thuộc video
        show = (it is not None and bool(it.review)
                and self._current_module not in (None, "review", "story"))
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

    def mark_changed(self, item: VideoItem | None = None) -> None:
        """Video đã xuất bị sửa (cấu hình, lời thoại, giọng) → «Cần xuất lại»: nút Xuất tính nó vào, không bỏ qua."""
        it = item or self.app_state.active
        if it is not None and it.status == "Xong":
            it.status = "Cần xuất lại"
            self.queue_update(it)
            self.app_state.mark_dirty()

    def _scope_changed(self, _section: str = "") -> None:
        if not self.app_state.last_change_auto and _section != "story":
            self.mark_changed()
        if not self.preview.player.playing and not self.app_state.last_change_auto:
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

    def edit_in(self, key: str, title: str = "") -> None:
        """«Sửa ›» ở bảng Duyệt: mở đúng tab tính năng cho video đang duyệt — cuộn tới đúng khối (vd «Âm thanh» nằm
        cuối tab Video), dòng «Lời thoại» mở trang sửa câu."""
        self._scope_note = ""
        self.show_module(key)
        m = self.modules.get(key)
        if key == "srt" and m is not None:
            from app.services.line_qa import QA_TITLE
            from app.ui.modules.module_srt import TAB_TEXT

            m.show_tab(TAB_TEXT)
            if title == QA_TITLE:
                m.editor.show_flagged(True)
        section = EDIT_SECTIONS.get(title)
        if section and m is not None and hasattr(m, "scroll_to_section"):
            self.after(80, lambda: m.scroll_to_section(section))

    def review_panel(self):
        """Bảng Duyệt (tạo nếu chưa có — để phím tắt duyệt dùng được ở mọi tab)."""
        if "review" not in self.modules:
            from app.ui.widgets.review_window import ReviewPanel

            self.modules["review"] = ReviewPanel(self.panel, self)
        return self.modules["review"]

    def _review_key(self, action: str) -> None:
        if not self.app_state.videos:
            return
        rv = self.review_panel()
        if action == "approve":
            rv.approve()
        else:
            rv._step(1 if action == "next" else -1)
        self._scope_note = ""
        self._update_scope_strip()

    def sync_review_rail(self) -> None:
        """Nút «Duyệt» trên thanh tính năng (luôn có: Chuẩn bị → Duyệt → Xuất), ngay sau nút Video."""
        self.app_state.set_review_scope(True)
        self._update_scope_strip()
        if self._rail_buttons.get("review") is None:
            from app.ui.widgets.review_window import ReviewPanel

            self._add_rail_button(ReviewPanel)
            first = self._rail_buttons.get("video")
            if first is not None:
                self._rail_buttons["review"].pack_configure(after=first)

    def update_render_button(self) -> None:
        """Nhãn nút chính theo bước: Chuẩn bị (n) / Tiếp tục (n) (đang dừng chờ bạn ở «Chạy đến») → Mở Duyệt → Xuất (n)."""
        vids = self.app_state.videos
        todo = sum(1 for it in vids if not it.review and it.status != "Lỗi")
        waiting = sum(1 for it in vids if not it.review and it.status in WAITING)
        failed = sum(1 for it in vids if not it.review and it.status == "Lỗi")
        ready = sum(1 for it in vids if it.review == "approved" and it.status != "Xong")
        text = (f"Tiếp tục ({todo})" if todo == waiting else f"Chuẩn bị ({todo})") if todo else (
            f"Xuất ({ready})" if ready else (f"Chuẩn bị lại ({failed})" if failed else ("Mở Duyệt" if vids else "Chuẩn bị")))
        if getattr(self, "_render_label", None) != text:
            self._render_label = text
            relabel(self.render_btn, text)
            if hasattr(self, "progress"):
                self.progress.refresh_idle()

    def stop_all(self) -> None:
        """Nút Dừng (Esc): dừng TẤT CẢ — video đang chạy, các video còn lại và việc xếp hàng chờ."""
        if self._waiting:
            self.log(f"Đã huỷ {len(self._waiting)} việc trong hàng chờ.")
            self._waiting.clear()
            self._show_waiting()
        busy = self.runner.running or self.tasks.running
        self.runner.stop()
        self.tasks.stop()
        self.log("Đã yêu cầu dừng…")
        if busy:                                  # bước đang chạy có thể cần vài giây mới dừng được — báo ngay
            self.stop_btn.configure(state="disabled")
            self.progress.stopping()

    def skip_item(self, item: VideoItem) -> None:
        """Bỏ qua một video trong lượt đang chạy; các video khác chạy tiếp."""
        if not self.runner.running:
            return
        self.runner.skip(item)
        if item is not self.runner.current and item.status == "Chờ":
            item.status, item.message = "Đã bỏ qua", ""
            self.queue_update(item)
        self.log(f"⏭ Bỏ qua {item.name}" + (" — đang dừng video này…" if item is self.runner.current else "."))

    def skip_current(self) -> None:
        """Nút «Bỏ qua video này» trên thanh trạng thái: bỏ video đang chạy, sang video kế."""
        if self.runner.current is not None:
            self.skip_item(self.runner.current)

    def _on_escape(self, _e=None) -> None:
        """Esc = Dừng tất cả — trừ khi đang gõ trong ô nhập (Esc lúc gõ chữ không được huỷ cả lô)."""
        if not (self.runner.running or self.tasks.running):
            return
        focus = self.focus_get()
        if isinstance(focus, (tk.Entry, tk.Text)):
            return
        now = time.monotonic()
        if now - getattr(self, "_esc_at", 0.0) > 3.0:
            self._esc_at = now
            self.progress.flash("Bấm Esc lần nữa (trong 3 giây) để dừng tất cả")
            return
        self._esc_at = 0.0
        self.stop_all()

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
        srt = self.modules.get("srt")
        if srt is not None and srt.editor.item is not None and srt.editor.dirty():
            srt.editor.save(regen=False, quiet=True)  # lời thoại tự lưu — chữ còn chờ tự lưu thì lưu luôn, không hỏi
        self.preview.stop_play()
        self.app_state.save_config()
        if self._control is not None:
            self._control.stop()
        self.player.stop()
        from app.services import qwen_asr

        qwen_asr.kill_all()                         # tiến trình nhận diện / cài đặt không chạy mồ côi sau khi đóng app
        self.destroy()
