"""Khung xem trước (canvas): khung hình đã áp filter, kéo thả khung phụ đề / vùng che, phát video, sóng âm.

- Khung hình được FFmpeg trích trong thread nền với đúng chuỗi filter sẽ dùng khi render.
  Phụ đề của giây đang xem ghi vào một ASS tạm (bắt đầu từ 0s) vì khi -ss thì pts về 0.
- Công cụ: V (chọn/kéo canvas) · T (kéo/đổi cỡ khung phụ đề) · B (vẽ/kéo vùng che).
- Khi phát, hiển thị khung thô (không filter) + chữ phụ đề vẽ đè bằng canvas.
"""

from __future__ import annotations

import copy
import logging
import math
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk
from PIL import Image, ImageTk

from app.config import theme as T
from app.ui.widgets.common import Chip
from app.ui.widgets.empty_state import EmptyStateArt
from app.ui.widgets.ui import button, button_icon, button_width, relabel
from app.config.paths import PREVIEW_CACHE_DIR
from app.core.state import VideoItem
from app.services import render_pipeline as rp
from app.services import stages as stages_mod
from app.services.ffmpeg import MediaInfo
from app.services.player import Player, waveform_png
from app.services.srt_utils import Cue, cue_at, format_ts
from app.services import subtitle_style as sst
from app.services.subtitle_style import write_ass
from app.services.word_timing import speech_span
from app.core.platform import open_path

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

logger = logging.getLogger(__name__)

HANDLE = 7
CLIP_SECONDS = 15                 # độ dài bản render thử
ZOOM_LEVELS = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]


def new_region(x: float = 0.3, y: float = 0.3, w: float = 0.4, h: float = 0.2) -> dict:
    return {"x": x, "y": y, "w": w, "h": h, "strength": 88.0, "feather": 28.0, "opacity": 100.0,
            "style": "Mờ (Gaussian)", "start": 0.0, "end": 0.0, "rounded": 0.0}


class PreviewPanel(ctk.CTkFrame):
    def __init__(self, master, window: MainWindow) -> None:
        super().__init__(master, fg_color=T.BG_PANEL, corner_radius=10)
        self.window = window
        self.state = window.app_state
        self.item: VideoItem | None = None
        self.info: MediaInfo | None = None
        self.t = 0.0
        self.selected: tuple[str, int] | None = None
        self._badges: list[tuple[str, int, float, float, float]] = []
        self._frame: Image.Image | None = None          # khung đã render (filter)
        self._live: Image.Image | None = None           # khung khi phát
        self._photo: ImageTk.PhotoImage | None = None
        self._img_id: int | None = None                 # item ảnh duy nhất trên canvas (đổi ảnh bằng paste/itemconfig)
        self._shown_key: tuple | None = None            # (ảnh nguồn, dw, dh) đang hiển thị → khỏi resize lại
        self._configure_after: str | None = None
        self._poll_id: str | None = None
        self._cursor = ""
        self._last_text: dict[str, str] = {}
        self._last_slider_t = -1.0
        self._frame_proc = None                         # ffmpeg đang trích khung xem trước (giết khi có yêu cầu mới)
        self._req = 0
        self._after_id: str | None = None
        self.tool = "V"
        self.show_sub = True
        self.show_blur = True
        self.zoom: float | None = None                  # None = vừa khung
        self.pan = [0, 0]
        self._fit = (0, 0, 1, 1)                        # (ox, oy, dw, dh)
        self._drag: dict | None = None
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._wave_img: Image.Image | None = None
        self._wave_photo: ImageTk.PhotoImage | None = None
        self._wave_w = 0
        self._wave_line: int | None = None
        self.player = Player(window.ffmpeg, None, self._on_play_end)
        self._cues_cache: list[Cue] = []
        self.fx_play = True                             # phát có đủ hiệu ứng như bản render
        self._clip: dict | None = None                  # đang phát bản render thử
        self._restart_id: str | None = None
        self.state.on("config_changed", self._config_changed)

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        # hàng trên: chip khung hình + thanh công cụ — cùng một hàng khi đủ rộng, hẹp thì tách 2 hàng (_layout_top)
        self.top = ctk.CTkFrame(self, fg_color="transparent")
        self.top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        self.top.grid_columnconfigure(1, weight=1)
        self._top_rows = 0
        self._build_header()
        self.canvas = tk.Canvas(self, bg="#000000", highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.grid(row=2, column=0, sticky="nsew", padx=10, pady=(4, 4))
        self._build_toolbar()
        self._layout_top()
        self.top.bind("<Configure>", lambda _e: self._layout_top())
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self._build_timeline()

    def destroy(self) -> None:
        self.state.off("config_changed", self._config_changed)    # dựng lại giao diện (đổi theme) → gỡ listener cũ
        try:
            self.player.stop()
        except Exception:
            pass
        super().destroy()

    # ── Dựng UI ──────────────────────────────────────────────────────────
    def _build_header(self) -> None:
        from app.core.state import FIT_MODES, RATIOS, RESOLUTIONS
        from app.ui.modules.module_video import FPS_LABELS

        head = ctk.CTkFrame(self.top, fg_color="transparent")
        self.head = head
        ctk.CTkLabel(head, text="Xem trước", font=T.font(14, "bold"), anchor="w").pack(side="left", padx=(0, 10))
        vcfg = self.state.video

        def setter(attr: str):
            def _set(v: str) -> None:
                setattr(self.state.video, attr, v)
                self.state.config_changed("video")
                m = self.window.modules.get("video") if hasattr(self.window, "modules") else None
                if m is not None:
                    m.refresh()
                self.schedule_refresh(0)
            return _set

        short_ratio = lambda v: "Gốc" if v == RATIOS[0] else v.split(" ")[0]          # noqa: E731
        short_res = lambda v: "Gốc" if v == RESOLUTIONS[0] else v                     # noqa: E731
        short_fps = lambda v: "Gốc" if v == "Giữ nguyên" else f"{v} fps"              # noqa: E731
        fps_keys = list(FPS_LABELS.keys())
        # Khung hình: chọn ngay trên khung xem trước (21:9 · 16:9 · 4:3 · 1:1 · 3:4 · 4:5 · 9:16)
        self.chips = {
            "ratio": Chip(head, icon_name="aspect", values=RATIOS, get_value=lambda: self.state.video.ratio,
                          set_value=setter("ratio"), short=short_ratio, labels={RATIOS[0]: "Giữ nguyên (theo video gốc)"},
                          tip="Tỉ lệ khung video xuất: 16:9 ngang (YouTube) · 9:16 dọc (TikTok)…"),
            "resolution": Chip(head, icon_name="resolution", values=RESOLUTIONS, get_value=lambda: self.state.video.resolution,
                               set_value=setter("resolution"), short=short_res, labels={RESOLUTIONS[0]: "Giữ nguyên"}, menu_width=170,
                               tip="Độ phân giải video xuất"),
            "fps": Chip(head, icon_name="fps", values=fps_keys, get_value=lambda: self.state.video.fps,
                        set_value=setter("fps"), short=short_fps, labels=FPS_LABELS, menu_width=190,
                        tip="Số khung hình mỗi giây của video xuất"),
            "fit": Chip(head, icon_name="crop", values=FIT_MODES, get_value=lambda: self.state.video.fit_mode,
                        set_value=setter("fit_mode"), menu_width=190,
                        tip="Cách đặt video vào khung mới: cắt vừa · nền mờ · đệm viền"),
        }
        for c in self.chips.values():
            c.pack(side="left", padx=(0, 6))
        # phụ đề: vị trí + cỡ chữ chỉnh nhanh ngay trên khung (kiểu chữ ở tab «Lời thoại»)
        from app.services import subtitle_presets as sp

        ctk.CTkFrame(head, width=1, height=22, fg_color=T.BORDER).pack(side="left", padx=(4, 10))
        self.sub_chips = {
            "place": Chip(head, icon_name="subtitles", values=sp.PLACES, get_value=lambda: sp.place_of(self.state.subtitle),
                          set_value=self._set_sub_place, short=lambda v: sp.PLACE_SHORT.get(v, v), labels=sp.PLACE_LABELS,
                          menu_width=250, tip="Phụ đề nằm ở đâu: dưới (chừa nút TikTok) · giữa · trên · đè lên chữ cũ"),
            "size": Chip(head, icon_name="font", values=list(sp.SIZES), get_value=lambda: sp.size_of(self.state.subtitle),
                         set_value=self._set_sub_size, short=lambda v: sp.SIZE_LABELS.get(v, v), labels=sp.SIZE_LABELS,
                         menu_width=150, tip="Cỡ chữ phụ đề"),
        }
        for c in self.sub_chips.values():
            c.pack(side="left", padx=(0, 6))
        self._meta_text = ""
        del vcfg

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self.top, fg_color="transparent")
        self.bar = bar
        self.bar_label = ctk.CTkLabel(bar, text="Che mờ + Phụ đề (kéo thả)", font=T.font(12), text_color=T.TEXT_DIM, anchor="w")
        self.bar_label.pack(side="left", padx=(0, 10))
        # công cụ chỉnh trên hình (khung phụ đề, ô che mờ): chỉ hiện ở tab Lời thoại / Che mờ — tab khác chỉ xem video
        tools = ctk.CTkFrame(bar, fg_color="transparent")
        self.tools = tools
        tools.pack(side="left")
        button(tools, "", icon_name="undo", variant="ghost", size="sm", command=self.undo, tip="Hoàn tác (Ctrl+Z)").pack(side="left", padx=1)
        button(tools, "", icon_name="redo", variant="ghost", size="sm", command=self.redo, tip="Làm lại (Ctrl+Y)").pack(side="left", padx=(1, 8))
        self.tool_btns: dict[str, ctk.CTkButton] = {}
        self._tool_icons = {"V": "pointer", "T": "font", "B": "blur"}
        self._full_w: dict[ctk.CTkButton, int] = {}      # bề rộng đầy đủ (có chữ) của từng nút → khôi phục sau chế độ gọn
        self._compact = False
        tool_tips = {"V": "Chọn / kéo hình xem trước (phím V)", "T": "Kéo, đổi cỡ khung phụ đề (phím T)",
                     "B": "Vẽ / kéo ô che mờ (phím B)"}
        for key in ("V", "T", "B"):
            b = button(tools, key, icon_name=self._tool_icons[key], size="sm", command=lambda k=key: self.set_tool(k), tip=tool_tips[key])
            b.pack(side="left", padx=1)
            self.tool_btns[key] = b
        self.center_btn = button(tools, "Căn giữa", size="sm", command=lambda: self._quick_align("center"),
                                 tip="Đưa khung phụ đề vào giữa theo chiều ngang")
        self.center_btn.pack(side="left", padx=(8, 1))
        self.full_btn = button(tools, "Rộng hết", size="sm", command=lambda: self._quick_align("full"),
                               tip="Kéo khung phụ đề rộng hết khung hình (ít xuống dòng nhất)")
        self.full_btn.pack(side="left", padx=1)
        self.del_btn = button(tools, "Xóa", icon_name="trash", variant="destructive", size="sm", command=self.delete_selection,
                              tip="Xoá ô che đang chọn · đang ở khung phụ đề thì tắt phụ đề (Delete)")
        self.del_btn.pack(side="left", padx=(8, 1))
        # Sub/Blur đổi chữ (Sub ↔ Sub ẩn): lấy bề rộng của nhãn dài hơn để nút không nhảy
        self.sub_toggle = button(tools, "Sub", icon_name="eye", size="sm", command=self._toggle_sub,
                                 width=button_width(tools, "Sub ẩn", size="sm", icon=True),
                                 tip="Ẩn / hiện phụ đề trên khung xem trước (Shift+T) — không ảnh hưởng video xuất")
        self.sub_toggle.pack(side="left", padx=(8, 1))
        self.blur_toggle = button(tools, "Blur", icon_name="eye", size="sm", command=self._toggle_blur,
                                  width=button_width(tools, "Blur ẩn", size="sm", icon=True),
                                  tip="Ẩn / hiện ô che mờ trên khung xem trước — không ảnh hưởng video xuất")
        self.blur_toggle.pack(side="left", padx=1)
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right")
        self.meta = ctk.CTkLabel(right, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="e")
        self.meta.pack(side="left", padx=(0, 10))
        button(right, "", icon_name="fit", variant="ghost", size="sm", command=lambda: self.set_zoom(None), tip="Vừa khung (Ctrl+0)").pack(side="right", padx=1)
        button(right, "", icon_name="zoomin", variant="ghost", size="sm", command=lambda: self.zoom_step(1), tip="Phóng to (Ctrl +)").pack(side="right", padx=1)
        self.zoom_label = ctk.CTkLabel(right, text="Fit", font=T.font(11), text_color=T.TEXT_DIM, width=44)
        self.zoom_label.pack(side="right")
        button(right, "", icon_name="zoomout", variant="ghost", size="sm", command=lambda: self.zoom_step(-1), tip="Thu nhỏ (Ctrl −)").pack(side="right", padx=1)
        for b in (*self.tool_btns.values(), self.center_btn, self.full_btn, self.del_btn, self.sub_toggle,
                  self.blur_toggle):
            self._full_w[b] = b.cget("width")
        self.set_tool("V")

    def _build_timeline(self) -> None:
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
        box.grid_columnconfigure(0, weight=1)
        self.slider = ctk.CTkSlider(box, from_=0, to=1, command=self._slid, height=14)
        self.slider.grid(row=0, column=0, sticky="ew", padx=4)
        self.slider.bind("<ButtonRelease-1>", lambda _e: self.seek(self.t))
        self.slider.set(0)
        self.wave = tk.Canvas(box, height=48, bg=T.BG_INPUT, highlightthickness=1, highlightbackground=T.BORDER_SOFT)
        self.wave.grid(row=1, column=0, sticky="ew", padx=4, pady=(4, 4))
        self.wave.bind("<Button-1>", self._wave_click)
        self.wave.bind("<Configure>", lambda _e: self._draw_wave())
        ctrl = ctk.CTkFrame(box, fg_color="transparent")
        ctrl.grid(row=2, column=0, sticky="ew", padx=4)
        self.ctrl = ctrl
        self.play_btn = button(ctrl, "Phát", icon_name="play", variant="primary", size="sm", command=self.toggle_play,
                               width=button_width(ctrl, "Dừng", size="sm", icon=True) + 12, tip="Phát / dừng xem trước (Space)")
        self.play_btn.pack(side="left")
        button(ctrl, "", icon_name="skipback", size="sm", command=lambda: self.seek(0.0), tip="Về đầu video (Home)").pack(side="left", padx=(6, 1))
        button(ctrl, "−3s", size="sm", width=44, command=lambda: self.seek(self.t - 3), tip="Lùi 3 giây (J / ←)").pack(side="left", padx=1)
        button(ctrl, "+3s", size="sm", width=44, command=lambda: self.seek(self.t + 3), tip="Tới 3 giây (L / →)").pack(side="left", padx=1)
        self.mute_btn = button(ctrl, "", icon_name="speaker", size="sm", command=self.toggle_mute, tip="Tắt / bật tiếng xem trước (M)")
        self.mute_btn.pack(side="left", padx=(6, 1))
        self.vol = ctk.CTkSlider(ctrl, from_=0, to=100, width=90, height=14, command=self._vol)
        self.vol.set(100)
        self.vol.pack(side="left", padx=4)
        self.time_label = ctk.CTkLabel(ctrl, text="00:00:00 / 00:00:00", font=T.mono(11), width=150, anchor="w")
        self.time_label.pack(side="left", padx=(8, 0))
        self.cue_label = ctk.CTkLabel(ctrl, text="", font=T.font(11), text_color=T.TEXT_DIM, anchor="w")
        button(ctrl, "", icon_name="fullscreen", size="sm", command=self.fullscreen, tip="Xem trước toàn màn hình (F)").pack(side="right")
        self.more_btn = button(ctrl, "", icon_name="list", variant="ghost", size="sm", command=self._more_menu,
                               tip="Thêm: hiệu ứng khi phát · vẽ lại sóng âm · mở bằng trình phát của máy")
        self.more_btn.pack(side="right", padx=(0, 4))
        # ít dùng → nằm trong menu ⋯ (giữ đối tượng nút để mã cũ đổi nhãn không lỗi, chỉ không hiện)
        self.ext_btn = button(ctrl, "", icon_name="export", size="sm", command=self._open_external)
        self.wave_btn = button(ctrl, "Sóng âm", icon_name="wave", size="sm", command=self.load_waveform)
        self.clip_btn = button(ctrl, "Render thử", icon_name="film", size="sm", command=self.render_clip,
                               tip=f"Xuất thử {CLIP_SECONDS} giây từ chỗ đang xem rồi phát ngay — giống hệt file sẽ xuất")
        self.clip_btn.pack(side="right", padx=(0, 4))
        self.fx_btn = button(ctrl, "Hiệu ứng", icon_name="eye", size="sm", command=self.toggle_fx)
        for b in (self.wave_btn, self.clip_btn, self.fx_btn):
            self._full_w[b] = b.cget("width")
        # chữ của câu phụ đề xếp sau cùng: thiếu chỗ thì nó co lại, các nút không bị đẩy ra ngoài
        self.cue_label.pack(side="left", fill="x", expand=True, padx=8)
        self._compact = False

    def _layout_top(self) -> None:
        """Đủ chỗ → chip và thanh công cụ trên cùng một hàng (nhãn «Che mờ + Phụ đề» ẩn); không đủ → 2 hàng."""
        w = self.top.winfo_width()
        if w < 50:
            return
        need = self.head.winfo_reqwidth() + self._bar_req_width() + 16
        rows = 1 if need <= w else 2
        if rows == self._top_rows:
            return
        self._top_rows = rows
        self.head.grid_forget()
        self.bar.grid_forget()
        if rows == 1:
            self.bar_label.pack_forget()
            self.head.grid(row=0, column=0, sticky="w")
            self.bar.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        else:
            if not self._compact and self.tools.winfo_ismapped():
                self.bar_label.pack(side="left", padx=(0, 10), before=self._bar_first())
            self.head.grid(row=0, column=0, columnspan=2, sticky="ew")
            self.bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _relayout_top(self) -> None:
        """Nội dung thanh (meta, nhãn) đổi → tính lại 1/2 hàng sau khi Tk cập nhật kích thước yêu cầu."""
        self._top_rows = 0
        self.after(30, self._layout_top)

    def _bar_req_width(self) -> int:
        """Bề rộng thanh công cụ cần (không tính nhãn chữ đã ẩn ở chế độ 1 hàng)."""
        total = 0
        for ch in self.bar.winfo_children():
            if ch is self.bar_label and not ch.winfo_ismapped():
                continue
            if ch is self.bar_label:
                total += ch.winfo_reqwidth() + 10
                continue
            info = ch.pack_info() if ch.winfo_manager() == "pack" else {}
            pad = info.get("padx", 0)
            pad = sum(pad) if isinstance(pad, tuple) else int(pad) * 2
            total += ch.winfo_reqwidth() + pad
        return total

    def _bar_first(self):
        """Widget đang hiện đầu tiên của thanh công cụ (sau nhãn) — để xếp nhãn «Che mờ + Phụ đề» lên trước."""
        return self.tools if self.tools.winfo_ismapped() else self.bar.winfo_children()[-1]

    def set_edit_tools(self, on: bool) -> None:
        """Công cụ chỉnh trên hình chỉ hiện ở tab Lời thoại / Che mờ (tab khác: thanh gọn, chỉ xem video)."""
        if on == self.tools.winfo_ismapped():
            return
        if on:
            self.tools.pack(side="left", before=self.bar.winfo_children()[-1])
        else:
            self.tools.pack_forget()
            self.bar_label.pack_forget()          # nhãn «Che mờ + Phụ đề (kéo thả)» đi cùng công cụ
            self.set_tool("V")
        self._relayout_top()

    def _more_menu(self) -> None:
        m = tk.Menu(self, tearoff=0, bg=T.BG_ELEVATED, fg=T.TEXT, activebackground=T.BG_HOVER,
                    activeforeground=T.TEXT, selectcolor=T.ACCENT, bd=0)
        fx = tk.BooleanVar(value=self.fx_play)
        m.add_checkbutton(label="Hiệu ứng khi phát (giống bản xuất — máy yếu bị giật thì tắt)", variable=fx,
                          command=self.toggle_fx)
        m.add_command(label="Vẽ lại sóng âm", command=self.load_waveform)
        m.add_command(label="Mở video gốc bằng trình phát của máy", command=self._open_external)
        b = self.more_btn
        try:
            m.tk_popup(b.winfo_rootx(), b.winfo_rooty() - 80)
        finally:
            m.grab_release()

    def set_compact(self, compact: bool) -> None:
        """Khung xem trước hẹp (< ~760 px): bỏ nhãn chữ, chỉ giữ icon để thanh công cụ không tràn."""
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self.bar_label.pack_forget()
        elif self._top_rows == 2 and self.tools.winfo_ismapped():
            self.bar_label.pack(side="left", padx=(0, 10), before=self._bar_first())
        labels = {self.center_btn: "Căn giữa", self.full_btn: "Rộng hết",
                  self.del_btn: "Xóa", self.sub_toggle: "Sub" if self.show_sub else "Sub ẩn",
                  self.blur_toggle: "Blur" if self.show_blur else "Blur ẩn", self.wave_btn: "Sóng âm",
                  self.clip_btn: "Render thử", self.fx_btn: "Hiệu ứng" if self.fx_play else "Hiệu ứng tắt",
                  **{b: key for key, b in self.tool_btns.items()}}
        for b, label in labels.items():
            relabel(b, "" if compact else label, width=32 if compact else self._full_w[b])
        self.time_label.configure(width=90 if compact else 150, font=T.mono(10 if compact else 11))
        self.vol.configure(width=60 if compact else 90)
        for c in (*self.chips.values(), *self.sub_chips.values()):
            c.refresh()                                   # chip tự rộng theo chữ (không ép cỡ cố định → không cắt chữ)
        self.meta.configure(text="" if compact else self._meta_text)
        self._top_rows = 0
        self.after(30, self._layout_top)
        for w in (self, self.canvas.master):
            try:
                w._draw(no_color_updates=False)
            except Exception:
                pass

    # ── Public API ───────────────────────────────────────────────────────
    def set_item(self, item: VideoItem | None) -> None:
        self.stop_play()
        self._clip = None
        self.item = item
        self.info = None
        self.t = 0.0
        self._frame = self._live = None
        self._wave_img = self._wave_photo = None
        self._wave_w = 0
        self._wave_line = None
        self.wave.delete("all")
        self.player.set_source(item.path if item else None)
        if item is None:
            self._clear_canvas()
            self._meta_text = ""
            self.meta.configure(text="")
            self._relayout_top()
            self._set_text(self.time_label, "00:00:00 / 00:00:00")
            self._set_text(self.cue_label, "")
            self._redraw()
            return
        self._draw_message("Đang tải…")
        self.window.run_bg(lambda: self.window.ffmpeg.probe(item.path), lambda i, e, it=item: self._probed(i, e, it))

    def _probed(self, info: MediaInfo | None, error: Exception | None, item: VideoItem | None = None) -> None:
        if item is not None and item is not self.item:
            return                      # đã chuyển sang video khác trong lúc đọc thông tin → bỏ kết quả cũ
        if error or info is None or self.item is None:
            self._draw_message(f"Không đọc được video: {error}")
            return
        self.info = info
        it = self.item
        it.duration, it.width, it.height, it.fps, it.has_audio = info.duration, info.width, info.height, info.fps, info.has_audio
        self.window.queue_update(it)
        m = self.window.modules.get("video")
        if m is not None:
            m.update_fit_hint()                 # giờ mới biết kích thước video
        m = self.window.modules.get("srt")
        if m is not None and m.style_panel is not None:
            m.style_panel.fill_pixels()         # ô pixel khung phụ đề tính theo khung ra thật
        self.slider.configure(to=max(0.01, info.duration))
        self.slider.set(0)
        self._meta_text = (f"{info.width}×{info.height} · {info.fps:.2f} fps · {format_ts(info.duration, '.')[:8]}"
                           + ("" if info.has_audio else " · không có audio"))
        self.meta.configure(text="" if self._compact else self._meta_text)
        self._relayout_top()
        self.schedule_refresh(0)
        if self.state.settings.show_waveform:
            self.load_waveform()
        self.ensure_hardsub()
        self.ensure_no_vocals()

    def ensure_hardsub(self) -> None:
        """«Tự dò chữ cứng» bật mà video đang xem chưa dò → dò nền rồi vẽ lại (vùng che hiện ngay trên khung xem trước)."""
        from app.services.hardsub import cached_region, ensure_region

        it, info = self.item, self.info
        if it is None or info is None or not self.state.blur.auto_detect or cached_region(it) is not False:
            return
        pending = self.__dict__.setdefault("_hs_pending", set())
        if it.path in pending:                 # đang dò video này rồi
            return
        pending.add(it.path)
        ff = self.window.ffmpeg

        def done(_r, _e):
            pending.discard(it.path)
            if self.item is it:
                self.schedule_refresh(0)
                m = self.window.modules.get("blur")
                if m is not None:
                    m._update_auto_status()
                m = self.window.modules.get("srt")
                if m is not None and m.style_panel is not None:
                    m.style_panel.fill_pixels()
                    m.style_panel.follow_status()

        self.window.run_bg(lambda: ensure_region(it, ff, info.duration, log=self.window.log_bg), done)

    def ensure_no_vocals(self) -> None:
        """«Khử giọng gốc» bật mà video đang xem chưa khử → khử nền (AI) rồi phát lại với âm đã khử.
        Trong lúc chờ, xem trước dùng âm gốc vặn rất nhỏ (giống bản render dự phòng)."""
        from app.services.vocal_remove import cached_no_vocals

        it, info = self.item, self.info
        v = self.state.video
        if it is None or info is None or not info.has_audio or not v.demucs_enable or not v.keep_original_audio \
                or v.mute or cached_no_vocals(it.path) is not None:
            return
        pending = self.__dict__.setdefault("_nv_pending", set())
        if it.path in pending:
            return
        pending.add(it.path)
        pipe = self.window.pipeline_for(it)
        self.window.log(f"{it.name}: đang khử giọng gốc nền (AI, ~1 phút cho mỗi phút video) — xong sẽ tự dùng.")

        def done(_r, _e):
            pending.discard(it.path)
            m = self.window.modules.get("video")
            if m is not None:
                m._novocal_status()
            if self.item is it and self.player.playing:
                self._restart_live()

        self.window.run_bg(lambda: pipe.remove_voice(it, log=self.window.log_bg), done)

    def schedule_refresh(self, delay_ms: int = 350) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        # tối thiểu ~60 ms: gom các yêu cầu dồn dập (giữ phím ←/→, J/L, kéo thanh trượt cài đặt) thành một lần trích khung
        self._after_id = self.after(max(60, delay_ms), self.refresh)

    def refresh(self) -> None:
        self._after_id = None
        for c in self.chips.values():
            c.refresh()
        if self.item is None or self.info is None or self.player.playing:
            self._redraw()
            return
        self._req += 1
        req = self._req
        item, info, t = self.item, self.info, self.t
        vcfg, scfg = self.state.video, self.state.subtitle
        self._update_time()
        show_sub, show_blur = self.show_sub, self.show_blur

        def work():
            ass_name = None
            cue_text = ""
            mods = stages_mod.collect(self.state, item, info, include_blur=show_blur)
            eff = mods.info
            pipe = self.window.pipeline
            cues = pipe.preview_sub_cues(item, pipe.current_voice_track(item, info.duration)[0])
            c = cue_at(cues, t)
            if c is not None:
                cue_text = c.plain + (f"  ⟂ {c.meta.get('secondary')}" if c.meta.get("secondary") else "")
            if scfg.enable_subtitle and show_sub and c is not None:
                geo = rp.output_geometry(eff, vcfg)
                PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                # chữ nổi (karaoke): vẽ tĩnh đúng chữ đang đọc tại vị trí đang xem (theo đoạn giọng thật của câu)
                a0, d = speech_span(c)
                write_ass([Cue(1, 0.0, 60.0, c.text, {**c.meta, "anim": (a0 - c.start, d)})],
                          stages_mod.subtitle_for(self.state, item, info), geo.width, geo.height,
                          PREVIEW_CACHE_DIR / "preview.ass", anim_at=max(0.0, t - c.start))
                ass_name = "preview.ass"
            speed = max(0.25, vcfg.speed / 100.0)
            vg = rp.build_video_graph(eff, vcfg, work_dir=PREVIEW_CACHE_DIR, ass_name=ass_name, stages=mods.stages,
                                      pre_stages=mods.pre_stages, src_stages=mods.src_stages, include_fps=False,
                                      t0=t / speed, preview=True,
                                      first_extra_input=1)
            if req != self._req:           # đã có yêu cầu mới hơn → khỏi chạy ffmpeg
                return req, None, cue_text, cues
            img = self.window.ffmpeg.extract_frame_graph(item.path, t, vg.graph.text(), max_width=1280,
                                                         cwd=PREVIEW_CACHE_DIR, inputs=vg.graph.extra_inputs,
                                                         on_proc=self._track_frame_proc)
            return req, img, cue_text, cues

        def done(result, error):
            if error:
                if req != self._req:        # ffmpeg của yêu cầu cũ bị dừng chủ động
                    return
                logger.warning("Preview lỗi: %s", error)
                self._draw_message(f"Xem trước lỗi:\n{str(error)[-300:]}")
                return
            r, img, cue_text, cues = result
            if r != self._req or img is None:
                return
            self._frame = img
            self._cues_cache = cues
            self._set_text(self.cue_label, cue_text)
            self._redraw()

        self._kill_frame_proc()
        self.window.run_bg(work, done)

    def _track_frame_proc(self, proc) -> None:
        self._frame_proc = proc

    def _kill_frame_proc(self) -> None:
        """Yêu cầu khung mới → dừng ffmpeg của yêu cầu cũ (kết quả của nó sẽ bị bỏ), khỏi tranh CPU."""
        proc, self._frame_proc = self._frame_proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def seek(self, t: float) -> None:
        if self.info is None:
            return
        if self._clip is not None:
            self.stop_play()
        was_playing = self.player.playing
        self.t = max(0.0, min(max(0.0, self.info.duration - 0.05), t))
        self.slider.set(self.t)
        self._update_time()
        if was_playing:
            self._play_from(self.t)
        else:
            self.schedule_refresh(0)

    # ── Công cụ / hiển thị ───────────────────────────────────────────────
    def set_tool(self, key: str) -> None:
        self.tool = key
        for k, b in self.tool_btns.items():
            b.configure(fg_color=T.ACCENT if k == key else T.BG_ELEVATED,
                        text_color=T.ON_ACCENT if k == key else T.TEXT,
                        image=button_icon(self._tool_icons[k], size="sm", color=T.ON_ACCENT if k == key else T.TEXT,
                                          with_text=not self._compact))
        self._cursor = {"V": "fleur", "T": "hand2", "B": "crosshair"}.get(key, "arrow")
        self.canvas.configure(cursor=self._cursor)
        self._redraw()

    def sub_frame_size(self) -> tuple[int, int]:
        """Cỡ khung ra của video đang xem (chưa có video: 1080×1920 — khung dọc TikTok)."""
        if self.info is None:
            return 1080, 1920
        geo = rp.output_geometry(self.info, self.state.video)
        return geo.width, geo.height

    def refresh_sub_chips(self) -> None:
        for c in self.sub_chips.values():
            c.refresh()

    def _sub_changed(self) -> None:
        """Đổi phụ đề từ thanh khung video → lưu, vẽ lại, đồng bộ phần «Phụ đề trên video» của tab Lời thoại."""
        self.state.config_changed("subtitle")
        self.refresh_sub_chips()
        m = self.window.modules.get("srt")
        if m is not None:
            m.refresh_subtitle()
        self.schedule_refresh(0)

    def _set_sub_place(self, place: str) -> None:
        from app.services.subtitle_presets import apply_place

        apply_place(self.state.subtitle, place, *self.sub_frame_size())
        if place == "hardsub":
            self.ensure_follow_hardsub()
        self._sub_changed()

    def _set_sub_size(self, key: str) -> None:
        from app.services.subtitle_presets import apply_size

        apply_size(self.state.subtitle, key, *self.sub_frame_size())
        self._sub_changed()

    def ensure_follow_hardsub(self) -> None:
        """«Đè lên chữ cũ» cần vùng chữ cứng đã dò → bật «Tự dò & che phụ đề gốc» ở Che mờ rồi dò video đang xem."""
        if not self.state.blur.auto_detect:
            self.state.blur.auto_detect = True
            self.state.config_changed("blur")
            self.window.log("Đã bật Che mờ › «Tự dò & che phụ đề gốc» để biết chỗ chữ cũ của từng video.")
        self.ensure_hardsub()

    def _quick_align(self, kind: str) -> None:
        """Căn nhanh khung phụ đề: giữa theo chiều ngang · rộng hết khung (đang đè chữ cũ thì thôi đè, giữ chỗ)."""
        cfg = self.state.subtitle
        if not cfg.enable_subtitle:
            return
        self._detach_follow()
        if kind == "center":
            cfg.box_x = round((1.0 - cfg.box_w) / 2, 4)
        else:
            cfg.box_x, cfg.box_w = 0.0, 1.0
        cfg.use_box = True
        self._sub_changed()

    def _toggle_sub(self) -> None:
        self.show_sub = not self.show_sub
        self.sub_toggle.configure(text="" if self._compact else ("Sub" if self.show_sub else "Sub ẩn"),
                                  image=button_icon("eye" if self.show_sub else "hide", size="sm", with_text=not self._compact),
                                  fg_color=T.BG_ELEVATED if self.show_sub else T.DANGER_SOFT)
        self.schedule_refresh(0)

    def _toggle_blur(self) -> None:
        self.show_blur = not self.show_blur
        self.blur_toggle.configure(text="" if self._compact else ("Blur" if self.show_blur else "Blur ẩn"),
                                   image=button_icon("eye" if self.show_blur else "hide", size="sm", with_text=not self._compact),
                                   fg_color=T.BG_ELEVATED if self.show_blur else T.DANGER_SOFT)
        self.schedule_refresh(0)

    def set_zoom(self, z: float | None) -> None:
        self.zoom = z
        self.pan = [0, 0]
        self._update_fit_box()
        self.zoom_label.configure(text="Fit" if z is None else f"{int(z * 100)}%")
        self._redraw()

    def zoom_step(self, direction: int) -> None:
        cur = self.zoom if self.zoom is not None else self._fit_scale()
        if direction > 0:
            nxt = next((z for z in ZOOM_LEVELS if z > cur + 1e-3), ZOOM_LEVELS[-1])
        else:
            nxt = next((z for z in reversed(ZOOM_LEVELS) if z < cur - 1e-3), ZOOM_LEVELS[0])
        self.set_zoom(nxt)

    def _fit_scale(self) -> float:
        img = self._current_image()
        if img is None:
            return 1.0
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        return min((cw - 8) / img.width, (ch - 8) / img.height)

    def _wheel(self, e) -> None:
        if e.state & 0x0004:      # Ctrl + lăn
            self.zoom_step(1 if e.delta > 0 else -1)

    # ── Vẽ ────────────────────────────────────────────────────────────────
    def _current_image(self) -> Image.Image | None:
        return self._live if self.player.playing and self._live is not None else self._frame

    def _clear_canvas(self) -> None:
        self.canvas.delete("all")
        self._img_id = None
        self._shown_key = None

    def _draw_message(self, text: str) -> None:
        self._clear_canvas()
        self.canvas.create_text(max(1, self.canvas.winfo_width()) // 2, max(1, self.canvas.winfo_height()) // 2,
                                text=text, fill=T.TEXT_MUTED, font=(T.UI_FONT, 12), justify="center")

    def _draw_empty(self) -> None:
        """Chưa có video: thẻ EmptyState kiểu shadcn — 3 ô icon nghiêng, di chuột thì xoè ra; 2 nút thật của app."""
        self._clear_canvas()
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        if not getattr(self, "_empty_btns", None):
            self._empty_btns = [
                button(self.canvas, "Nạp video", icon_name="add", variant="primary", bg_color=T.BG_PANEL, command=self._empty_add),
                button(self.canvas, "Xem hướng dẫn", icon_name="book", variant="secondary", bg_color=T.BG_PANEL,
                       command=lambda: self.window.open_guide("start")),
            ]
        if getattr(self, "_empty_art", None) is None:
            try:
                k = float(ctk.ScalingTracker.get_widget_scaling(self))
            except Exception:
                k = 1.0
            self._empty_art = EmptyStateArt(self.canvas, scale=k, title="Kéo thả video vào đây",
                                            description="hoặc bấm để chọn file · MP4, MOV, MKV, WebM…", buttons=self._empty_btns)
            self._empty_art.on_hover = lambda on: self.canvas.configure(cursor="hand2" if on else "arrow")
        self._empty_art.draw(cw, ch)

    def _on_canvas_configure(self, _e=None) -> None:
        """Kéo giãn cửa sổ bắn hàng chục <Configure>/giây → gom lại, vẽ một lần."""
        if self._configure_after is not None:
            try:
                self.after_cancel(self._configure_after)
            except Exception:
                pass
        self._configure_after = self.after(40, self._configured)

    def _configured(self) -> None:
        self._configure_after = None
        self._update_fit_box()
        self._redraw()

    def _update_fit_box(self) -> None:
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        self.player.fit_box = (cw - 8, ch - 8) if self.zoom is None else None

    def _redraw(self) -> None:
        """Ảnh: một item duy nhất, chỉ resize/chuyển PhotoImage khi ảnh hoặc cỡ đổi; lớp phủ (tag «ov») vẽ lại riêng."""
        img = self._current_image()
        self._badges = []
        if img is None:
            self._clear_canvas()
            if self.item is None:
                self._draw_empty()
            return
        cv = self.canvas
        if self._img_id is None:
            cv.delete("all")          # xoá chữ «Đang tải…» / thẻ trống
        else:
            cv.delete("ov")
        cw, ch = max(1, cv.winfo_width()), max(1, cv.winfo_height())
        scale = self.zoom if self.zoom is not None else self._fit_scale()
        dw, dh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        if self.zoom is None and abs(dw - img.width) <= 2 and abs(dh - img.height) <= 2:
            dw, dh = img.size         # khung phát đã được thu đúng cỡ trong thread → khỏi resize
        ox, oy = (cw - dw) // 2 + self.pan[0], (ch - dh) // 2 + self.pan[1]
        self._fit = (ox, oy, dw, dh)
        # giữ chính ảnh nguồn trong khoá (so bằng «is»): id() có thể bị tái dùng sau khi ảnh cũ bị giải phóng
        sk = self._shown_key
        if sk is None or sk[0] is not img or sk[1:] != (dw, dh) or self._img_id is None:
            shown = img.resize((dw, dh), Image.BILINEAR) if (dw, dh) != img.size else img
            if self._photo is not None and self._img_id is not None and \
                    (self._photo.width(), self._photo.height()) == (dw, dh):
                self._photo.paste(shown)
            else:
                self._photo = ImageTk.PhotoImage(shown)
                if self._img_id is not None:
                    cv.itemconfigure(self._img_id, image=self._photo)
            self._shown_key = (img, dw, dh)
        if self._img_id is None:
            self._img_id = cv.create_image(ox, oy, image=self._photo, anchor="nw")
        else:
            cv.coords(self._img_id, ox, oy)
        if self.player.playing:
            self._draw_live_subtitle(ox, oy, dw, dh)
        self._draw_overlays(ox, oy, dw, dh)

    def _ov_text(self, *a, **kw) -> int:
        return self.canvas.create_text(*a, tags="ov", **kw)

    def _ov_rectangle(self, *a, **kw) -> int:
        return self.canvas.create_rectangle(*a, tags="ov", **kw)

    def _ov_oval(self, *a, **kw) -> int:
        return self.canvas.create_oval(*a, tags="ov", **kw)

    def _draw_live_subtitle(self, ox, oy, dw, dh) -> None:
        if not (self.state.subtitle.enable_subtitle and self.show_sub):
            return
        if self._clip is not None or (self.fx_play and not self.player.effects_failed):
            return                       # khung đã có phụ đề thật (ASS) — không vẽ đè
        c = cue_at(self._cues_cache, self.t)
        if c is None:
            return
        cfg = self._sub_cfg()
        size = max(8, int(cfg.sub_size * (min(dw, dh) / 1080.0)))
        x = ox + int((cfg.box_x + cfg.box_w / 2) * dw)
        # bám chỗ chữ cứng: khối chữ căn giữa khung (như ASS \an5); còn lại chữ bám đáy khung
        y = oy + int((cfg.box_y + (cfg.box_h / 2 if cfg.box_center else cfg.box_h)) * dh)
        anchor = "center" if cfg.box_center else "s"
        font = (cfg.sub_font or "Arial", size, "bold" if cfg.sub_bold else "normal")
        self._ov_text(x + 2, y + 2, text=c.plain, fill="#000000", font=font, anchor=anchor, width=int(cfg.box_w * dw))
        self._ov_text(x, y, text=c.plain, fill=cfg.sub_color, font=font, anchor=anchor, width=int(cfg.box_w * dw))

    def _draw_overlays(self, ox, oy, dw, dh) -> None:
        cv = self.canvas
        if self._clip is not None:
            a, b = self._clip["window"][0][0], self._clip["window"][-1][1]
            self._ov_text(ox + 10, oy + 10, anchor="nw", fill=T.ACCENT, font=(T.UI_FONT, 11, "bold"),
                          text=f"BẢN RENDER THỬ · {format_ts(a, '.')[:8]} → {format_ts(b, '.')[:8]}")
            return
        # viền mảnh quanh khung video xuất: phân biệt viền đen của video với nền đen của khung xem trước
        self._ov_rectangle(ox - 1, oy - 1, ox + dw, oy + dh, outline=T.BORDER, width=1)
        self._badges: list[tuple[str, int, float, float, float]] = []
        mod = self.window.current_module_key()
        if self.tool == "B" or mod == "blur":
            for i, r in enumerate(self.state.blur.regions):
                try:
                    fx0, fy0, fx1, fy1 = self._blur_box_out(r)
                    x0, y0, x1, y1 = ox + fx0 * dw, oy + fy0 * dh, ox + fx1 * dw, oy + fy1 * dh
                except (KeyError, TypeError, ValueError):
                    continue
                active = i == self.state.blur.active_index
                col = "#F59E0B" if active else "#FDE68A"
                self._ov_rectangle(x0, y0, x1, y1, outline=col, width=3 if active else 1, dash=() if active else (4, 3))
                self._ov_text(x0 + 6, y0 + 4, text=f"Blur {i + 1}", fill=col, anchor="nw", font=(T.UI_FONT, 10, "bold"))
                if active and self.tool == "B":
                    self._draw_handles(x0, y0, x1, y1, col)
                    self._draw_delete_badge("blur", i, x1, y0)
        if self.state.subtitle.enable_subtitle and self.state.subtitle.use_box and (self.tool == "T" or mod == "srt"):
            cfg = self.state.subtitle
            bx, by, bw, bh = self._sub_box()
            x0, y0 = ox + bx * dw, oy + by * dh
            x1, y1 = x0 + bw * dw, y0 + bh * dh
            self._ov_rectangle(x0, y0, x1, y1, outline="#22D3EE", width=2, dash=(6, 3))
            self._ov_text(x0 + 6, y0 - 4, text=f"Khung phụ đề · chữ {cfg.sub_size}px", fill="#22D3EE", anchor="sw",
                          font=(T.UI_FONT, 10, "bold"))
            if self.tool == "T":
                self._draw_handles(x0, y0, x1, y1, "#22D3EE")
                self._draw_delete_badge("sub", 0, x1, y0)

    def _draw_delete_badge(self, kind: str, idx: int, x: float, y: float) -> None:
        """Nút ✕ tròn ở góc trên-phải khung đang chọn; bấm để xóa vùng / tắt phụ đề."""
        r = 11
        cx, cy = x + 4, y - 4
        self._ov_oval(cx - r, cy - r, cx + r, cy + r, fill=T.DANGER, outline="#000000")
        self._ov_text(cx, cy, text="✕", fill="#FFFFFF", font=(T.UI_FONT, 10, "bold"))
        self._badges.append((kind, idx, cx, cy, r + 3))

    def _draw_handles(self, x0, y0, x1, y1, col) -> None:
        for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1), ((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1),
                       (x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2)):
            self._ov_rectangle(hx - HANDLE, hy - HANDLE, hx + HANDLE, hy + HANDLE, fill=col, outline="#000000")

    # ── Chuột ─────────────────────────────────────────────────────────────
    def _to_frac(self, x: int, y: int) -> tuple[float, float]:
        ox, oy, dw, dh = self._fit
        return (x - ox) / max(1, dw), (y - oy) / max(1, dh)

    # Vùng che lưu theo tỉ lệ KHUNG NGUỒN; khung xem trước là khung ra → đổi qua lại
    def _src_rect(self) -> tuple[float, float, float, float]:
        if self.info is None:
            return 0.0, 0.0, 1.0, 1.0
        return rp.source_rect_in_output(self.info, self.state.video)

    def _src_to_out(self, x: float, y: float) -> tuple[float, float]:
        v = self.state.video
        zoom = max(1.0, min(4.0, v.zoom / 100.0))
        if v.flip_h:
            x = 1.0 - x
        x, y = (x - 0.5) * zoom + 0.5, (y - 0.5) * zoom + 0.5
        ox, oy, sx, sy = self._src_rect()
        return ox + x * sx, oy + y * sy

    def _out_to_src(self, fx: float, fy: float) -> tuple[float, float]:
        v = self.state.video
        zoom = max(1.0, min(4.0, v.zoom / 100.0))
        ox, oy, sx, sy = self._src_rect()
        x, y = (fx - ox) / max(1e-6, sx), (fy - oy) / max(1e-6, sy)
        x, y = (x - 0.5) / zoom + 0.5, (y - 0.5) / zoom + 0.5
        if v.flip_h:
            x = 1.0 - x
        return x, y

    def _blur_box_out(self, r: dict) -> tuple[float, float, float, float]:
        """Hộp vùng che (nguồn) → (x0, y0, x1, y1) theo tỉ lệ khung ra."""
        x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
        ax, ay = self._src_to_out(x, y)
        bx, by = self._src_to_out(x + w, y + h)
        return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)

    def _box_at(self, fx: float, fy: float) -> tuple[str, int, str] | None:
        """Tìm khung/vùng và tay cầm dưới con trỏ: (kind, index, handle)."""
        ox, oy, dw, dh = self._fit
        boxes: list[tuple[str, int, tuple[float, float, float, float]]] = []
        if self.tool == "T" and self.state.subtitle.use_box:
            boxes.append(("sub", 0, self._sub_box()))
        if self.tool == "B":
            for i, r in enumerate(self.state.blur.regions):
                try:
                    boxes.append(("blur", i, (float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"]))))
                except (KeyError, TypeError, ValueError):
                    pass
        hpx, hpy = HANDLE / max(1, dw), HANDLE / max(1, dh)
        _ox, _oy, sxr, syr = self._src_rect()
        for kind, idx, (bx, by, bw, bh) in reversed(boxes):
            if kind == "blur":
                px, py = self._out_to_src(fx, fy)
                tx, ty = hpx / max(1e-6, sxr), hpy / max(1e-6, syr)
            else:
                px, py, tx, ty = fx, fy, hpx, hpy
            corners = {"nw": (bx, by), "ne": (bx + bw, by), "sw": (bx, by + bh), "se": (bx + bw, by + bh),
                       "n": (bx + bw / 2, by), "s": (bx + bw / 2, by + bh), "w": (bx, by + bh / 2), "e": (bx + bw, by + bh / 2)}
            if kind == "blur" and self.state.video.flip_h:
                corners = {k.replace("w", "W").replace("e", "w").replace("W", "e"): v for k, v in corners.items()}
            for name, (cx, cy) in corners.items():
                if abs(px - cx) <= tx * 1.5 and abs(py - cy) <= ty * 1.5:
                    return kind, idx, name
            if bx <= px <= bx + bw and by <= py <= by + bh:
                return kind, idx, "move"
        return None

    # Khung phụ đề: chữ bám ĐÁY khung, nên khung hiển thị luôn cao vừa số dòng chữ (theo cỡ chữ) tính từ đáy —
    # kéo cạnh trên/dưới = đổi cỡ chữ, kéo cạnh trái/phải = đổi bề rộng (chỗ xuống dòng), kéo giữa = dời chỗ.
    def _sub_lines(self, dw: int, dh: int, size: float) -> int:
        """Số dòng câu phụ đề đang hiện sẽ chiếm (ước theo bề rộng khung, như libass tự xuống dòng)."""
        c = cue_at(self._cues_cache, self.t)
        if c is None:
            return sst.BOX_LINES
        cfg = self.state.subtitle
        px = max(4, int(round(size * min(dw, dh) / 1080.0)))
        key = (cfg.sub_font, px, cfg.sub_bold, cfg.sub_italic)
        if getattr(self, "_measure_key", None) != key:
            try:
                self._measure_font = tkfont.Font(family=cfg.sub_font or "Arial", size=-px,
                                                 weight="bold" if cfg.sub_bold else "normal",
                                                 slant="italic" if cfg.sub_italic else "roman")
            except tk.TclError:
                self._measure_font = None
            self._measure_key = key
        avail = max(10.0, cfg.box_w * dw)
        spacing = cfg.sub_letter_spacing * min(dw, dh) / 1080.0
        texts = [c.plain.upper() if cfg.sub_uppercase else c.plain]
        if c.meta.get("secondary"):
            texts.append(str(c.meta["secondary"]))
        lines = 0
        for text in texts:
            for part in (text.splitlines() or [""]):
                if self._measure_font is not None:
                    w = self._measure_font.measure(part) + spacing * len(part)
                else:
                    w = len(part) * px * 0.5
                lines += max(1, math.ceil(w / avail))
        return max(1, lines)

    def _sub_text_h(self, size: float | None = None) -> float:
        """Chiều cao chữ của câu đang hiện (tỉ lệ khung ra) ở cỡ ``size``."""
        cfg = self.state.subtitle
        _ox, _oy, dw, dh = self._fit
        size = cfg.sub_size if size is None else size
        return sst.box_height_px(cfg, dw, dh, self._sub_lines(dw, dh, size), size=size) / max(1, dh)

    def _sub_cfg(self):
        """Phụ đề thực dùng cho video đang xem (khung có thể đang bám chỗ chữ cứng đã dò)."""
        if self.item is None or self.info is None:
            return self.state.subtitle
        try:
            return stages_mod.subtitle_for(self.state, self.item, self.info)
        except Exception:
            logger.debug("subtitle_for lỗi", exc_info=True)
            return self.state.subtitle

    def _sub_box(self, size: float | None = None) -> tuple[float, float, float, float]:
        """Khung phụ đề đang hiển thị (tỉ lệ khung ra): đáy + bề ngang theo cấu hình, cao vừa chữ."""
        cfg = self._sub_cfg()
        _ox, _oy, dw, dh = self._fit
        # tối thiểu đủ chỗ cho tay cầm (chữ nhỏ 1 dòng thì 8 tay cầm khỏi chồng lên nhau)
        h = min(0.95, max(self._sub_text_h(size), HANDLE * 5 / max(1, dh)))
        if cfg.box_center:                             # đang bám chỗ chữ cứng: khối chữ căn giữa khung
            mid = cfg.box_y + cfg.box_h / 2
            return cfg.box_x, mid - h / 2, cfg.box_w, h
        bottom = max(0.02, min(1.0, cfg.box_y + cfg.box_h))
        return cfg.box_x, bottom - h, cfg.box_w, h

    def _detach_follow(self) -> None:
        """Kéo khung đang bám chữ cứng → đặt tay: chép vị trí đang thấy vào cấu hình và tắt «bám chữ cứng»."""
        cfg = self.state.subtitle
        eff = self._sub_cfg()
        if not cfg.follow_hardsub:
            return
        if eff is not cfg:
            cfg.box_x, cfg.box_y, cfg.box_w, cfg.box_h = eff.box_x, eff.box_y, eff.box_w, eff.box_h
        cfg.follow_hardsub = False
        self.window.log("Phụ đề giờ theo vị trí bạn kéo (muốn đè lại lên chữ cũ: chọn «Đè chữ cũ» ở nút phụ đề trên "
                        "khung video).")

    def _drag_sub(self, d: dict, dx: float, dy: float) -> None:
        cfg = self.state.subtitle
        _ox, _oy, dw, dh = self._fit
        bx, by, bw, bh = d["box"]
        h = d["handle"]
        x0, x1, bottom = bx, bx + bw, by + bh
        if h == "move":
            x0, x1, bottom = x0 + dx, x1 + dx, bottom + dy
            if x0 < 0:
                x0, x1 = 0.0, x1 - x0
            if x1 > 1:
                x0, x1 = x0 - (x1 - 1.0), 1.0
        else:
            if "w" in h:
                x0 = max(0.0, min(x1 - 0.08, x0 + dx))
            if "e" in h:
                x1 = min(1.0, max(x0 + 0.08, x1 + dx))
            if "n" in h or "s" in h:
                th = d.setdefault("text_h", self._sub_text_h(d["size"]))
                nh = max(0.005, th - dy if "n" in h else th + dy)
                # cỡ chữ tỉ lệ theo chiều cao chữ kéo được (số dòng giữ như lúc bấm)
                size = max(12, min(120, int(round(d["size"] * nh / max(1e-6, th)))))
                cfg.sub_size = size
                if "s" in h:                       # kéo cạnh dưới: giữ mép trên, đáy chạy theo chữ
                    bottom = by + self._sub_box(size)[3]
        cfg.box_x, cfg.box_w = round(max(0.0, x0), 4), round(max(0.05, min(1.0, x1) - max(0.0, x0)), 4)
        h2 = min(0.9, sst.box_height_px(cfg, dw, dh) / max(1, dh))
        bottom = max(min(1.0, h2), min(1.0, bottom))
        cfg.box_h, cfg.box_y = round(h2, 4), round(max(0.0, bottom - h2), 4)
        self._redraw()
        if "n" in h or "s" in h:
            self.schedule_refresh(150)            # dừng tay là thấy chữ thật ở cỡ mới

    def _empty_add(self) -> None:
        m = self.window.modules.get("video")
        if m is not None:
            m.queue.add_files()

    def _press(self, e) -> None:
        if self._current_image() is None:
            if self.item is None:
                self._empty_add()
            return
        self.canvas.focus_set()
        for kind, idx, bx, by, br in getattr(self, "_badges", []):
            if (e.x - bx) ** 2 + (e.y - by) ** 2 <= br ** 2:
                self.delete_selection(kind, idx)
                return
        fx, fy = self._to_frac(e.x, e.y)
        hit = self._box_at(fx, fy)
        if self.tool == "V" or (hit is None and self.tool == "T"):
            self._drag = {"kind": "pan", "x": e.x, "y": e.y, "pan": list(self.pan)}
            return
        if hit is not None:
            kind, idx, handle = hit
            self.selected = (kind, idx)
            if kind == "blur":
                self.state.blur.active_index = idx
                self.window.notify_blur_selection()
            self._snapshot()
            if kind == "sub":
                self._detach_follow()
            sfx, sfy = self._out_to_src(fx, fy) if kind == "blur" else (fx, fy)
            self._drag = {"kind": kind, "idx": idx, "handle": handle, "fx": sfx, "fy": sfy, "box": self._get_box(kind, idx),
                          "size": self.state.subtitle.sub_size}
            return
        if self.tool == "B":
            self._snapshot()
            sfx, sfy = self._out_to_src(fx, fy)
            sfx, sfy = max(0.0, min(0.99, sfx)), max(0.0, min(0.99, sfy))
            self.state.blur.regions.append(new_region(sfx, sfy, 0.0, 0.0))
            self.state.blur.active_index = len(self.state.blur.regions) - 1
            self.state.blur.enable = True
            self.selected = ("blur", self.state.blur.active_index)
            self._drag = {"kind": "blur", "idx": self.state.blur.active_index, "handle": "se", "fx": sfx, "fy": sfy,
                          "box": (sfx, sfy, 0.0, 0.0), "new": True}

    def _get_box(self, kind: str, idx: int) -> tuple[float, float, float, float]:
        if kind == "sub":
            return self._sub_box()
        r = self.state.blur.regions[idx]
        return float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])

    def _set_box(self, kind: str, idx: int, box: tuple[float, float, float, float]) -> None:
        x, y, w, h = box
        x, y = max(0.0, min(0.99, x)), max(0.0, min(0.99, y))
        w, h = max(0.01, min(1.0 - x, w)), max(0.01, min(1.0 - y, h))
        if kind == "sub":
            c = self.state.subtitle
            c.box_x, c.box_y, c.box_w, c.box_h = round(x, 4), round(y, 4), round(w, 4), round(h, 4)
        else:
            self.state.blur.regions[idx].update({"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)})

    def _motion(self, e) -> None:
        d = self._drag
        if d is None:
            return
        if d["kind"] == "pan":
            if self.zoom is not None:
                self.pan = [d["pan"][0] + e.x - d["x"], d["pan"][1] + e.y - d["y"]]
                self._redraw()
            return
        fx, fy = self._to_frac(e.x, e.y)
        if d["kind"] == "blur":
            fx, fy = self._out_to_src(fx, fy)
        dx, dy = fx - d["fx"], fy - d["fy"]
        if d["kind"] == "sub":
            self._drag_sub(d, dx, dy)
            return
        bx, by, bw, bh = d["box"]
        h = d["handle"]
        if h == "move":
            box = (bx + dx, by + dy, bw, bh)
        else:
            x0, y0, x1, y1 = bx, by, bx + bw, by + bh
            if "w" in h:
                x0 = min(x1 - 0.01, x0 + dx)
            if "e" in h:
                x1 = max(x0 + 0.01, x1 + dx)
            if "n" in h:
                y0 = min(y1 - 0.01, y0 + dy)
            if "s" in h:
                y1 = max(y0 + 0.01, y1 + dy)
            box = (x0, y0, x1 - x0, y1 - y0)
        self._set_box(d["kind"], d["idx"], box)
        self._redraw()

    def _release(self, e) -> None:
        d = self._drag
        self._drag = None
        if d is None or d["kind"] == "pan":
            return
        if d.get("new"):
            r = self.state.blur.regions[d["idx"]]
            if float(r["w"]) < 0.01 or float(r["h"]) < 0.01:
                del self.state.blur.regions[d["idx"]]
                self.state.blur.active_index = max(0, len(self.state.blur.regions) - 1)
                if self._undo:
                    self._undo.pop()
                self._redraw()
                return
        self.state.config_changed("blur" if d["kind"] == "blur" else "subtitle")
        self.window.notify_box_edit(d["kind"])
        self.schedule_refresh(0)

    def _hover(self, e) -> None:
        if self.item is None and getattr(self, "_empty_art", None) is not None:
            self._empty_art.pointer(e.x, e.y)
            return
        if self._drag is not None or self._current_image() is None:
            return
        fx, fy = self._to_frac(e.x, e.y)
        hit = self._box_at(fx, fy)
        if hit is None:
            cur = {"V": "fleur", "T": "hand2", "B": "crosshair"}.get(self.tool, "arrow")
        else:
            cur = {"move": "fleur", "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
                   "n": "size_ns", "s": "size_ns", "w": "size_we", "e": "size_we"}.get(hit[2], "fleur")
        if cur != self._cursor:
            self._cursor = cur
            self.canvas.configure(cursor=cur)

    # ── Xóa vùng chọn ─────────────────────────────────────────────────────
    def delete_selection(self, kind: str | None = None, idx: int | None = None) -> None:
        """Phím Delete / nút Xóa / nút ✕: xóa vùng che đang chọn, hoặc tắt phụ đề khi đang chọn khung phụ đề."""
        if kind is None:
            if self.selected is not None:
                kind, idx = self.selected
            elif self.tool == "B":
                kind, idx = "blur", self.state.blur.active_index
            elif self.tool == "T":
                kind, idx = "sub", 0
            else:
                kind, idx = ("blur", self.state.blur.active_index) if self.state.blur.regions else ("sub", 0)
        if kind == "blur":
            rs = self.state.blur.regions
            if idx is None or not (0 <= idx < len(rs)):
                self.window.log("Chưa chọn vùng che nào để xóa — bấm vào vùng trên khung xem trước (công cụ B).")
                return
            self._snapshot()
            del rs[idx]
            self.state.blur.active_index = max(0, min(idx, len(rs) - 1))
            self.selected = ("blur", self.state.blur.active_index) if rs else None
            self.state.config_changed("blur")
            self.window.notify_box_edit("blur")
            self.window.notify_blur_selection()
            self.window.log(f"Đã xóa vùng che (còn {len(rs)}). Ctrl+Z để hoàn tác.")
        else:
            if not self.state.subtitle.enable_subtitle:
                self.window.log("Phụ đề đang tắt.")
                return
            self._snapshot()
            self.state.subtitle.enable_subtitle = False
            self.selected = None
            self.state.config_changed("subtitle")
            self.window.notify_box_edit("sub")
            self.window.log("Đã tắt phụ đề trên video — bật lại ở module Phụ đề hoặc Ctrl+Z.")
        self.schedule_refresh(0)

    # ── Undo / redo ──────────────────────────────────────────────────────
    def _state_snap(self) -> dict:
        c = self.state.subtitle
        return {"regions": copy.deepcopy(self.state.blur.regions), "active": self.state.blur.active_index,
                "box": (c.box_x, c.box_y, c.box_w, c.box_h), "sub_on": c.enable_subtitle, "size": c.sub_size,
                "follow": c.follow_hardsub}

    def _snapshot(self) -> None:
        self._undo.append(self._state_snap())
        del self._undo[:-40]
        self._redo.clear()

    def _restore(self, snap: dict) -> None:
        self.state.blur.regions = copy.deepcopy(snap["regions"])
        self.state.blur.active_index = snap["active"]
        c = self.state.subtitle
        c.box_x, c.box_y, c.box_w, c.box_h = snap["box"]
        if "sub_on" in snap:
            c.enable_subtitle = bool(snap["sub_on"])
        if "size" in snap:
            c.sub_size = int(snap["size"])
        if "follow" in snap:
            c.follow_hardsub = bool(snap["follow"])
        self.state.config_changed("blur")
        self.state.config_changed("subtitle")
        self.window.notify_box_edit("blur")
        self.window.notify_box_edit("sub")
        self.schedule_refresh(0)

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(self._state_snap())
        self._restore(self._undo.pop())

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self._state_snap())
        self._restore(self._redo.pop())

    # ── Phát ──────────────────────────────────────────────────────────────
    def toggle_play(self) -> None:
        if self.player.playing:
            self.stop_play()
        else:
            self.start_play()

    def start_play(self) -> None:
        if self.item is None or self.info is None:
            return
        self._configure_spec(announce=True)
        self._play_from(self.t)
        self.play_btn.configure(text="Dừng", image=button_icon("stop", size="sm", color=T.ON_ACCENT))

    def _configure_spec(self, announce: bool = False) -> None:
        """Chụp cấu hình hiện tại cho lần phát: hình qua đủ filter, tiếng = âm gốc + giọng lồng + nhạc nền."""
        from app.services.live_preview import LiveSpec

        pipe = self.window.pipeline
        voice, fresh, trimmed = pipe.current_voice_track(self.item, self.info.duration)
        cues = pipe.preview_sub_cues(self.item, voice) if self.show_sub else []
        self._cues_cache = cues
        self.player.effects_failed = ""
        try:
            self.player.spec = LiveSpec(self.state, self.item, self.info, self.window.ffmpeg,
                                        cues=cues if (self.fx_play and self.state.subtitle.enable_subtitle) else [],
                                        voice=voice, voice_trimmed=trimmed,
                                        effects=self.fx_play, show_blur=self.show_blur)
        except Exception:
            logger.warning("Không dựng được cấu hình phát xem trước", exc_info=True)
            self.player.spec = None
        if not announce:
            return
        tts, vcfg = self.state.tts, self.state.video
        if tts.enable_tts and not vcfg.mute:
            if voice is None:
                self.window.progress.notice("Chưa có giọng lồng cho video này",
                                            "bấm «Render thử» để tạo giọng cho đoạn đang xem, hoặc «Tạo giọng» ở tab TTS")
            elif not fresh:
                self.window.progress.notice("Đang nghe giọng lồng cũ",
                                            "cài đặt đã đổi — bấm «Render thử» để nghe đúng bản mới")

    def _config_changed(self, section: str = "") -> None:
        """Đổi cài đặt lúc đang phát → phát lại từ chỗ đang xem với cài đặt mới (gom các thay đổi liên tiếp).
        Đổi phụ đề ở bất kỳ đâu → nút vị trí / cỡ chữ trên thanh khung video theo ngay."""
        if section in ("subtitle", "") and hasattr(self, "sub_chips"):
            self.refresh_sub_chips()
        if not self.player.playing or self._clip is not None or self.item is None:
            return
        if self._restart_id is not None:
            try:
                self.after_cancel(self._restart_id)
            except Exception:
                pass
        self._restart_id = self.after(400, self._restart_live)

    def _restart_live(self) -> None:
        self._restart_id = None
        if self.player.playing and self._clip is None and self.item is not None and self.info is not None:
            self._configure_spec()
            self._play_from(self.t)

    def toggle_fx(self) -> None:
        self.fx_play = not self.fx_play
        relabel(self.fx_btn, "" if self._compact else ("Hiệu ứng" if self.fx_play else "Hiệu ứng tắt"),
                width=32 if self._compact else self._full_w[self.fx_btn])
        self.fx_btn.configure(image=button_icon("eye" if self.fx_play else "hide", size="sm", with_text=not self._compact))
        if self.player.playing and self._clip is None:
            self._configure_spec()
            self._play_from(self.t)

    # ── Bản render thử ───────────────────────────────────────────────────
    def render_clip(self) -> None:
        if self.item is None or self.info is None:
            return
        self.stop_play()
        item, t = self.item, self.t

        def work(cancel):
            return self.window.pipeline.preview_clip(item, t, CLIP_SECONDS, progress=self.window.set_busy_progress,
                                                     log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if error or result is None or self.item is not item:
                return
            path, window = result
            self._clip = {"path": str(path), "window": window, "t": t}
            self.player.spec = None
            self.player.set_source(str(path))
            self._cues_cache = []
            self._live = None
            self._play_from(0.0)
            self.play_btn.configure(text="Dừng", image=button_icon("stop", size="sm", color=T.ON_ACCENT))
            self.window.progress.notice("Đang phát bản render thử", "giống hệt file sẽ xuất · bấm Dừng ở thanh phát để quay lại")

        self.window.run_task("Xem thử bản render", work, done)

    def _exit_clip(self) -> None:
        clip, self._clip = self._clip, None
        if clip is None:
            return
        self.player.set_source(self.item.path if self.item else None)
        self.t = clip["t"]
        self.slider.set(self.t)
        self._update_time()

    def _play_from(self, t: float) -> None:
        self._kill_frame_proc()
        self._update_fit_box()
        self.player.play(t, max_width=960)
        if self._poll_id is None:
            self._poll_id = self.after(15, self._poll_live)

    def stop_play(self) -> None:
        if self.player.playing:
            self.player.stop()
        if self._clip is not None:
            self._exit_clip()
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        self.play_btn.configure(text="Phát", image=button_icon("play", size="sm", color=T.ON_ACCENT))
        self._live = None
        if self.item is not None:
            self.schedule_refresh(0)

    def _poll_live(self) -> None:
        """Nhịp UI khi phát: lấy khung mới nhất từ player (nếu có) rồi vẽ; không có khung mới thì không làm gì."""
        self._poll_id = None
        if not self.player.playing:
            return
        f = self.player.take_frame()
        if f is not None and self._clip is not None:
            img, t = f
            self._live = img
            if abs(t - self._last_slider_t) >= 0.2 or t < self._last_slider_t:
                self._last_slider_t = t
                total = self._clip["window"]
                dur = sum(b - a for a, b in total) / max(0.25, self.state.video.speed / 100.0)
                self._set_text(self.time_label, f"{format_ts(t, '.')[:8]} / {format_ts(dur, '.')[:8]}")
            self._redraw()
        elif f is not None:
            img, t = f
            self._live = img
            self.t = t
            # thanh trượt / nhãn giờ là widget CTk (vẽ lại cả canvas) → cập nhật ~5 lần/giây là đủ
            if abs(t - self._last_slider_t) >= 0.2 or t < self._last_slider_t:
                self._last_slider_t = t
                self.slider.set(t)
                self._update_time()
            c = cue_at(self._cues_cache, t)
            self._set_text(self.cue_label, c.plain if c else "")
            self._redraw()
        self._poll_id = self.after(15, self._poll_live)

    def _on_play_end(self) -> None:
        self.window.ui_call(self.stop_play)

    def toggle_mute(self) -> None:
        self.player.muted = not self.player.muted
        self.mute_btn.configure(image=button_icon("mute" if self.player.muted else "speaker", size="sm", with_text=False))
        if self.player.playing:
            self._play_from(self.t)

    def _vol(self, v: float) -> None:
        self.player.volume = max(0.0, min(1.5, v / 100.0))

    # ── Sóng âm ──────────────────────────────────────────────────────────
    def load_waveform(self) -> None:
        if self.item is None or not self.info or not self.info.has_audio:
            self.wave.delete("all")
            self._wave_img, self._wave_w, self._wave_line = None, 0, None
            self.wave.create_text(10, 24, text="Không có dữ liệu sóng âm", fill=T.TEXT_MUTED, anchor="w", font=(T.UI_FONT, 10))
            return
        width = max(300, self.wave.winfo_width())
        path = self.item.path

        def done(result, error):
            if error or result is None:
                return
            try:
                self._wave_img = Image.open(result).convert("RGB")
                self._wave_w = 0
                self._draw_wave()
            except Exception:
                pass

        self.window.run_bg(lambda: waveform_png(self.window.ffmpeg, path, width, 48, T.ACCENT), done)

    def _draw_wave(self) -> None:
        """Ảnh sóng âm dựng lại chỉ khi đổi độ rộng; vạch vị trí chỉ dời bằng coords."""
        img = self._wave_img
        if img is None:
            self.wave.delete("all")
            self._wave_w, self._wave_line = 0, None
            self.wave.create_text(10, 24, text="Chưa có dữ liệu sóng âm (Audio Waveform) — bấm “Sóng âm”",
                                  fill=T.TEXT_MUTED, anchor="w", font=(T.UI_FONT, 10))
            return
        w = max(1, self.wave.winfo_width())
        if w != self._wave_w or self._wave_line is None:
            self.wave.delete("all")
            if img.width != w:
                img = img.resize((w, 48))
            self._wave_photo = ImageTk.PhotoImage(img)
            self.wave.create_image(0, 0, image=self._wave_photo, anchor="nw")
            self._wave_line = self.wave.create_line(0, 0, 0, 48, fill="#F59E0B", width=2)
            self._wave_w = w
        self._move_wave_line()

    def _move_wave_line(self) -> None:
        if self._wave_line is None:
            return
        x = int(self._wave_w * self.t / self.info.duration) if self.info and self.info.duration > 0 else 0
        self.wave.coords(self._wave_line, x, 0, x, 48)

    def _wave_click(self, e) -> None:
        if self.info and self.info.duration > 0:
            w = max(1, self.wave.winfo_width())
            self.seek(self.info.duration * e.x / w)

    # ── Khác ─────────────────────────────────────────────────────────────
    def _set_text(self, widget, text: str) -> None:
        """configure(text=…) của CTk vẽ lại widget → chỉ gọi khi chữ thực sự đổi."""
        key = str(widget)
        if self._last_text.get(key) != text:
            self._last_text[key] = text
            widget.configure(text=text)

    def _update_time(self) -> None:
        total = self.info.duration if self.info else 0
        self._set_text(self.time_label, f"{format_ts(self.t, '.')[:8]} / {format_ts(total, '.')[:8]}")
        if self._wave_img is not None:
            if self._wave_line is None:
                self._draw_wave()
            else:
                self._move_wave_line()

    def _slid(self, v: float) -> None:
        self.t = float(v)
        self._update_time()

    def _open_external(self) -> None:
        if self.item and Path(self.item.path).exists():
            open_path(self.item.path)

    def fullscreen(self) -> None:
        img = self._current_image()
        if img is None:
            return
        top = tk.Toplevel(self)
        top.attributes("-fullscreen", True)
        top.configure(bg="#000000")
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        scale = min(sw / img.width, sh / img.height)
        shown = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
        photo = ImageTk.PhotoImage(shown)
        lbl = tk.Label(top, image=photo, bg="#000000")
        lbl.image = photo
        lbl.pack(expand=True)
        for seq in ("<Escape>", "<Button-1>", "<F12>", "<f>"):
            top.bind(seq, lambda _e: top.destroy())
        top.focus_set()
