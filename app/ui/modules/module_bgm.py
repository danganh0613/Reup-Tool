"""Module Nhạc: timeline nhạc, nhiều đoạn có vị trí bắt đầu/kết thúc, cắt đầu bài, âm lượng, fade, lặp; thư viện nhạc."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.config import theme as T
from app.config.paths import ASSETS_DIR
from app.services.ffmpeg import parse_hhmmss
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.ui import Select
from app.ui.widgets.common import ButtonRow, Section, SliderRow, SwitchRow, TimeEntry, hint, pack_rows

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}
MUSIC_LIB = ASSETS_DIR / "music"


class BGMModule(BaseModule):
    key = "bgm"
    title = "Nhạc"
    icon = "🎵"
    section = "bgm"

    def build(self) -> None:
        cfg = self.state.bgm
        s = self.setter
        hint(self, "Xếp từng đoạn nhạc đúng vị trí trên video. Chỉ dùng nhạc bạn có quyền sử dụng; timeline giúp thay "
                   "hoặc bỏ đúng vùng cần.").pack(fill="x", pady=(0, 4))
        self.enable = SwitchRow(self, "Thêm nhạc nền vào video", on_change=s(cfg, "enable", preview=False))
        self.enable.pack(fill="x", pady=(0, 4))

        sec = Section(self, "Timeline nhạc")
        sec.pack(fill="x", pady=4)
        self.timeline = tk.Canvas(sec.body, height=44, bg=T.BG_INPUT, highlightthickness=1, highlightbackground=T.BORDER_SOFT)
        self.timeline.pack(fill="x", pady=(0, 6))
        self.timeline.bind("<Configure>", lambda _e: self._draw_timeline())
        self.timeline.bind("<Button-1>", self._timeline_click)
        ctk.CTkLabel(sec.body, text="Đoạn đang chọn", font=T.font(11), text_color=T.TEXT_DIM, anchor="w").pack(fill="x")
        self.clip_var = tk.StringVar(value="Chưa có đoạn nhạc")
        self.clip_menu = Select(sec.body, ["Chưa có đoạn nhạc"], variable=self.clip_var, command=self._select_clip, height=34)
        self.clip_menu.pack(fill="x", pady=(2, 4))
        ButtonRow(sec.body, [("🎵 Thư viện nhạc", self._open_library, T.primary_button()), ("+ Thêm từ máy…", self._add_files, None)]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("− Xóa đoạn", self._remove_clip, None), ("Xóa hết", self._clear, T.danger_button())]).pack(fill="x", pady=2)
        self.status = hint(sec.body, "Chưa có nhạc · đưa playhead tới vị trí cần chèn rồi bấm + Thêm nhạc.")
        self.status.pack(fill="x")

        sec = Section(self, "Vị trí đoạn nhạc")
        sec.pack(fill="x", pady=4)
        self.start = TimeEntry(sec.body, "Bắt đầu (s)", on_change=lambda v: self._set("start", max(0.0, parse_hhmmss(v))),
                               pick_time=lambda: self.window.preview.t)
        self.end = TimeEntry(sec.body, "Kết thúc (s)", on_change=lambda v: self._set("end", max(0.0, parse_hhmmss(v))),
                             pick_time=lambda: self.window.preview.t)
        self.offset = TimeEntry(sec.body, "Cắt đầu bài (s)", on_change=lambda v: self._set("offset", max(0.0, parse_hhmmss(v))))
        pack_rows(sec.body, [self.start, self.end, self.offset])
        hint(sec.body, "Kết thúc = 0: chơi tới hết video. «⌖ Đang xem» lấy vị trí đang xem trước.").pack(fill="x")

        sec = Section(self, "Trộn âm")
        sec.pack(fill="x", pady=4)
        self.volume = SliderRow(sec.body, "Âm lượng", from_=0, to=100, step=1, suffix="%",
                                on_change=lambda v: self._set("volume", float(v)))
        self.fade_in = SliderRow(sec.body, "Fade vào (s)", from_=0, to=10, step=0.05, fmt="{:.2f}",
                                 on_change=lambda v: self._set("fade_in", float(v)))
        self.fade_out = SliderRow(sec.body, "Fade ra (s)", from_=0, to=10, step=0.05, fmt="{:.2f}",
                                  on_change=lambda v: self._set("fade_out", float(v)))
        self.loop = SwitchRow(sec.body, "Lặp nhạc để phủ kín khoảng đã chọn", on_change=lambda v: self._set("loop", bool(v)))
        self.duck = SwitchRow(sec.body, "Tự hạ nhạc khi có tiếng nói / giọng đọc", on_change=s(cfg, "ducking", preview=False))
        pack_rows(sec.body, [self.volume, self.fade_in, self.fade_out, self.loop, self.duck])
        hint(sec.body, "Bật Lặp + Kết thúc = 0: lặp tới hết video. Fade 0.2–0.5s giúp chuyển êm.").pack(fill="x")

    # ── Trạng thái ───────────────────────────────────────────────────────
    def _clips(self) -> list[dict]:
        return self.state.bgm.clips

    def _active(self) -> dict | None:
        cl = self._clips()
        i = self.state.bgm.active_index if hasattr(self.state.bgm, "active_index") else 0
        i = getattr(self, "_idx", 0)
        return cl[i] if 0 <= i < len(cl) else None

    def refresh(self) -> None:
        cfg = self.state.bgm
        self._loading = True
        try:
            self.enable.set(cfg.enable)
            self.duck.set(cfg.ducking)
            self._refresh_menu()
            c = self._active()
            if c:
                self.start.set(f"{float(c.get('start', 0)):.2f}")
                self.end.set(f"{float(c.get('end', 0)):.2f}")
                self.offset.set(f"{float(c.get('offset', 0)):.2f}")
                self.volume.set(float(c.get("volume", 15)))
                self.fade_in.set(float(c.get("fade_in", 0.35)))
                self.fade_out.set(float(c.get("fade_out", 0.35)))
                self.loop.set(bool(c.get("loop", True)))
                self.status.configure(text=f"{Path(c.get('path', '')).name}")
            else:
                self.status.configure(text="Chưa có nhạc · đưa playhead tới vị trí cần chèn rồi bấm + Thêm nhạc.")
        finally:
            self._loading = False
        self._draw_timeline()

    def on_active_changed(self) -> None:
        self._draw_timeline()

    def _refresh_menu(self) -> None:
        cl = self._clips()
        names = [f"{i + 1}. {Path(c.get('path', '')).name}" for i, c in enumerate(cl)]
        self.clip_menu.configure(values=names or ["Chưa có đoạn nhạc"])
        i = getattr(self, "_idx", 0)
        self.clip_var.set(names[i] if 0 <= i < len(names) else (names[0] if names else "Chưa có đoạn nhạc"))
        if not (0 <= i < len(names)):
            self._idx = 0

    def _draw_timeline(self) -> None:
        cv = self.timeline
        cv.delete("all")
        w = max(1, cv.winfo_width())
        item = self.state.active
        dur = item.duration if item and item.duration else 0.0
        if dur <= 0:
            cv.create_text(8, 22, text="Chọn video để xem timeline", fill=T.TEXT_MUTED, anchor="w", font=(T.UI_FONT, 10))
            return
        for i, c in enumerate(self._clips()):
            s0 = float(c.get("start", 0) or 0)
            e0 = float(c.get("end", 0) or 0) or dur
            x0, x1 = int(w * min(dur, s0) / dur), int(w * min(dur, e0) / dur)
            col = T.ACCENT if i == getattr(self, "_idx", 0) else T.ACCENT_SOFT
            cv.create_rectangle(x0, 8 + (i % 2) * 14, max(x0 + 3, x1), 22 + (i % 2) * 14, fill=col, outline="")
            cv.create_text(x0 + 4, 15 + (i % 2) * 14, text=Path(c.get("path", "")).stem[:24], fill="#0B1220" if i == getattr(self, "_idx", 0) else T.TEXT,
                           anchor="w", font=(T.UI_FONT, 9))
        t = self.window.preview.t
        x = int(w * min(dur, t) / dur)
        cv.create_line(x, 0, x, 44, fill="#F59E0B", width=2)

    def _timeline_click(self, e) -> None:
        item = self.state.active
        if item and item.duration:
            self.window.preview.seek(item.duration * e.x / max(1, self.timeline.winfo_width()))
            self._draw_timeline()

    # ── Thao tác ─────────────────────────────────────────────────────────
    def _set(self, key: str, value) -> None:
        if self._loading:
            return
        c = self._active()
        if c is None or c.get(key) == value:
            return
        c[key] = value
        self.state.config_changed("bgm")
        self._draw_timeline()

    def _select_clip(self, name: str) -> None:
        names = self.clip_menu.cget("values")
        if name in names:
            self._idx = names.index(name)
            self.refresh()

    def add_paths(self, paths: list[str]) -> None:
        added = 0
        for p in paths:
            if Path(p).suffix.lower() in AUDIO_EXTS and Path(p).exists():
                self._clips().append({"path": str(p), "volume": 15.0, "start": round(self.window.preview.t, 2), "end": 0.0,
                                      "offset": 0.0, "loop": True, "fade_in": 0.35, "fade_out": 0.35})
                added += 1
        if added:
            self.state.bgm.enable = True
            self._idx = len(self._clips()) - 1
            self.state.config_changed("bgm")
            self.refresh()

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Chọn nhạc nền",
                                            filetypes=[("Âm thanh", " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))), ("Tất cả", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def _open_library(self) -> None:
        from app.ui.widgets.music_library import MusicLibraryDialog

        MusicLibraryDialog(self.window, self)

    def _remove_clip(self) -> None:
        cl = self._clips()
        i = getattr(self, "_idx", 0)
        if 0 <= i < len(cl):
            del cl[i]
            self._idx = max(0, min(i, len(cl) - 1))
            self.state.config_changed("bgm")
            self.refresh()

    def _clear(self) -> None:
        self._clips().clear()
        self._idx = 0
        self.state.config_changed("bgm")
        self.refresh()

    def play_preview(self, path: str) -> None:
        try:
            self.window.player.play(path)
        except Exception as exc:
            self.window.log(f"Không phát được: {exc}")
