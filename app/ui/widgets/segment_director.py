"""Cảm xúc từng câu (mốc 5): dải thời gian các câu tô màu theo cảm xúc + panel Đạo diễn âm thanh cho câu đang xem
hoặc nhiều câu cùng lúc («Áp cho câu: 3-8, 12»). Lưu vào ``<srt>.directions.json``; câu chỉnh tay đánh dấu ``manual``
(gợi ý AI lần sau không đè).

▶ Nghe thử câu: đọc đúng như lúc dựng track (cùng engine, lời dặn, khoá cache) → dựng lại giọng video chỉ gọi API
cho câu đổi cảm xúc, không chạy lại cả quy trình.
"""

from __future__ import annotations

import copy
import re
import tkinter as tk
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk

from app.config import theme as T
from app.core.state import VideoItem
from app.services.direction import blend, emotions_map, speaking_styles, suggest
from app.services.srt_utils import Cue, format_ts
from app.ui.widgets.common import StatusLabel, hint
from app.ui.widgets.direction_studio import DirectionStudio, _default_direction
from app.ui.widgets.ui import Tooltip, button

_EMPTY = "#5b5f66"


def emotion_of(d: dict | None) -> tuple[str, str]:
    """→ (nhãn ngắn «Vui tươi 2», màu) — chưa có cảm xúc → ("", "")."""
    if not d:
        return "", ""
    e = d.get("emotion") or {}
    level = max(1, min(5, int(e.get("level", 3))))
    main, _w = blend(float(e.get("theta", 0.0)), level, e.get("overrides"))
    em = emotions_map()[main]
    return f"{em['vi']} {level}", em.get("color") or T.ACCENT


def parse_targets(text: str, n: int, current: int) -> list[int]:
    """«3-8, 12» → [2..7, 11] (0-based, trong 0..n-1); trống → [current]."""
    out: set[int] = set()
    for a, b in re.findall(r"(\d+)\s*(?:[-–]\s*(\d+))?", text or ""):
        lo, hi = int(a), int(b or a)
        if lo > hi:
            lo, hi = hi, lo
        out.update(k - 1 for k in range(lo, hi + 1) if 1 <= k <= n)
    return sorted(out) or [current]


class SegmentDirector(ctk.CTkToplevel):
    def __init__(self, master, window, item: VideoItem, cues: list[Cue], ref: list[Cue], srt_path: str, *,
                 index: int = 0, on_saved: Callable[[], None] | None = None,
                 on_regenerate: Callable[[], None] | None = None) -> None:
        super().__init__(master)
        self.title("Cảm xúc từng câu — Đạo diễn âm thanh")
        self.geometry("1160x880")
        self.minsize(980, 640)
        self.configure(fg_color=T.BG)
        self.window = window
        self.item = item
        self.cues = cues
        self.ref = ref
        self.srt_path = srt_path
        self.on_saved = on_saved
        self.on_regenerate = on_regenerate
        self.st = window.app_state.state_for(item)
        self.items = suggest.load(srt_path, len(cues))
        self.i = max(0, min(len(cues) - 1, index))
        self._dirty = False
        self._total = max([c.end for c in cues] + [1.0])
        self._style_vi = {s["id"]: s["vi"] for s in speaking_styles()}
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Prior>", lambda _e: self.step(-1))
        self.bind("<Next>", lambda _e: self.step(1))
        self.show(self.i)
        self.after(150, self._raise)

    def _raise(self) -> None:
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    # ── giao diện ─────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        strip = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=8)
        strip.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))
        ctk.CTkLabel(strip, text="DÒNG THỜI GIAN — mỗi ô một câu, màu = cảm xúc (bấm để chọn câu)",
                     font=T.font(11, "bold"), text_color=T.TEXT_DIM, anchor="w").pack(fill="x", padx=10, pady=(6, 0))
        self.canvas = tk.Canvas(strip, height=46, bg=T.BG_CARD, highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(fill="x", padx=10, pady=(4, 8))
        self.canvas.bind("<Configure>", lambda _e: self._draw_strip())
        self.canvas.bind("<Button-1>", self._strip_click)

        left = ctk.CTkFrame(self, fg_color="transparent", width=380)
        left.grid(row=1, column=0, sticky="nsw", padx=(12, 6), pady=(0, 12))
        left.grid_propagate(False)
        left.grid_columnconfigure(0, weight=1)
        card = ctk.CTkFrame(left, fg_color=T.BG_CARD, corner_radius=10)
        card.grid(row=0, column=0, sticky="ew")
        self.pos_lbl = ctk.CTkLabel(card, text="", font=T.font(13, "bold"), anchor="w")
        self.pos_lbl.pack(fill="x", padx=12, pady=(10, 2))
        self.ref_lbl = ctk.CTkLabel(card, text="", font=T.font(12), text_color=T.TEXT_MUTED, anchor="w",
                                    justify="left", wraplength=340)
        self.ref_lbl.pack(fill="x", padx=12)
        self.vi_lbl = ctk.CTkLabel(card, text="", font=T.font(15, "medium"), anchor="w", justify="left", wraplength=340)
        self.vi_lbl.pack(fill="x", padx=12, pady=(4, 2))
        self.emo_lbl = ctk.CTkLabel(card, text="", font=T.font(12), anchor="w", justify="left", wraplength=340)
        self.emo_lbl.pack(fill="x", padx=12, pady=(2, 10))
        nav = ctk.CTkFrame(left, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        nav.grid_columnconfigure((0, 1), weight=1)
        button(nav, "‹ Câu trước", size="sm", command=lambda: self.step(-1),
               tip="Câu trước (Page Up) — đang chỉnh dở thì tự áp dụng").grid(row=0, column=0, sticky="ew", padx=(0, 3))
        button(nav, "Câu sau ›", size="sm", command=lambda: self.step(1),
               tip="Câu sau (Page Down) — đang chỉnh dở thì tự áp dụng").grid(row=0, column=1, sticky="ew", padx=(3, 0))

        tg = ctk.CTkFrame(left, fg_color="transparent")
        tg.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        tg.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tg, text="Áp cho câu", font=T.font(12), anchor="w").grid(row=0, column=0, padx=(0, 6))
        self.targets = ctk.CTkEntry(tg, height=30, font=T.font(12), placeholder_text="vd 3-8, 12")
        self.targets.grid(row=0, column=1, sticky="ew")
        quick = ctk.CTkFrame(left, fg_color="transparent")
        quick.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        button(quick, "Câu này", size="sm", variant="ghost", command=lambda: self._set_targets(str(self.i + 1))).pack(
            side="left")
        button(quick, "Cả video", size="sm", variant="ghost",
               command=lambda: self._set_targets(f"1-{len(self.cues)}")).pack(side="left", padx=4)
        button(quick, "Cùng cảm xúc", size="sm", variant="ghost", command=self._same_emotion,
               tip="Chọn mọi câu đang cùng cảm xúc với câu này (đổi cả loạt một lần)").pack(side="left")

        acts = ctk.CTkFrame(left, fg_color="transparent")
        acts.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        acts.grid_columnconfigure((0, 1), weight=1)
        self.play_btn = button(acts, "Nghe thử câu", icon_name="play", variant="primary", size="md",
                               command=self.preview,
                               tip="Đọc câu này với cảm xúc đang chọn — đúng như khi dựng giọng video (có cache: nghe "
                                   "lại / dựng lại không tốn lượt)")
        self.play_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        button(acts, "Áp dụng", icon_name="check", size="md", command=self.apply,
               tip="Lưu cảm xúc đang chọn cho các câu ở ô «Áp cho câu»").grid(row=0, column=1, sticky="ew", padx=(3, 0))
        acts2 = ctk.CTkFrame(left, fg_color="transparent")
        acts2.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        acts2.grid_columnconfigure((0, 1), weight=1)
        button(acts2, "Bỏ cảm xúc", icon_name="close", size="sm", variant="ghost", command=self.clear,
               tip="Các câu ở ô «Áp cho câu» đọc theo phong cách chung của video").grid(row=0, column=0, sticky="ew")
        button(acts2, "Tạo lại giọng video", icon_name="refresh", size="sm", command=self.regenerate,
               tip="Dựng lại track lồng tiếng — câu không đổi lấy từ cache, chỉ câu đổi cảm xúc gọi API").grid(
            row=0, column=1, sticky="ew", padx=(6, 0))
        self.status = StatusLabel(left, "")
        self.status.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        hint(left, "Kéo kim: góc = cảm xúc, xa tâm = bậc 1–5. Câu đã chỉnh tay có viền sáng trên dòng thời gian, "
                   "gợi ý AI lần sau không đè.").grid(row=7, column=0, sticky="ew", pady=(6, 0))

        body = ctk.CTkScrollableFrame(self, fg_color=T.BG)
        body.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        self.studio = DirectionStudio(body, settings=self.st.settings, play=self._play, wheel_size=340,
                                      segment=True, on_change=self._changed)
        self.studio.pack(fill="both", expand=True)
        self._engine_note()

    def _engine_note(self) -> None:
        t = self.st.tts
        if not t.emotion_direction:
            self.status.warn("Đang tắt «Đọc theo cảm xúc từng câu» ở tab Lồng tiếng — bật lên thì cảm xúc mới được dùng.")
        elif not (t.tts_provider.startswith("Gemini") or t.tts_provider == "Edge TTS"):
            self.status.warn(f"{t.tts_provider} không đọc theo lời dặn cảm xúc — chọn Gemini TTS (đủ cảm xúc) hoặc "
                             "Edge TTS (chỉ nhịp / cao độ) ở tab Lồng tiếng.")

    # ── dòng thời gian ────────────────────────────────────────────────────
    def _x(self, t: float, w: int) -> float:
        return 4 + (w - 8) * t / self._total

    def _draw_strip(self) -> None:
        cv = self.canvas
        cv.delete("all")
        w = max(50, cv.winfo_width())
        sel = set(parse_targets(self.targets.get(), len(self.cues), self.i)) if hasattr(self, "targets") else {self.i}
        cv.create_line(4, 23, w - 4, 23, fill=T.BORDER)
        for k, c in enumerate(self.cues):
            d = self.items.get(k)
            _lbl, color = emotion_of(d)
            x0, x1 = self._x(c.start, w), max(self._x(c.start, w) + 3, self._x(c.end, w) - 1)
            cur = k == self.i
            y0, y1 = (4, 42) if cur else (10, 36)
            outline = T.ACCENT if cur else (T.TEXT if k in sel else (T.TEXT_DIM if d and d.get("manual") else ""))
            cv.create_rectangle(x0, y0, x1, y1, fill=color or _EMPTY, outline=outline, width=2 if (cur or k in sel) else 1)

    def _strip_click(self, e) -> None:
        w = max(50, self.canvas.winfo_width())
        t = (e.x - 4) / max(1, w - 8) * self._total
        k = min(range(len(self.cues)), key=lambda j: 0 if self.cues[j].start <= t <= self.cues[j].end
                else min(abs(self.cues[j].start - t), abs(self.cues[j].end - t)))
        self.go(k)

    # ── câu đang xem ──────────────────────────────────────────────────────
    def show(self, i: int) -> None:
        self.i = max(0, min(len(self.cues) - 1, i))
        c = self.cues[self.i]
        self.pos_lbl.configure(text=f"Câu {self.i + 1} / {len(self.cues)} · {format_ts(c.start, '.')[3:10]} → "
                                    f"{format_ts(c.end, '.')[3:10]} ({c.duration:.1f}s)")
        self.ref_lbl.configure(text=self.ref[self.i].plain if self.i < len(self.ref) else "")
        self.vi_lbl.configure(text=c.plain)
        d = self.items.get(self.i)
        self._show_emotion(d)
        self.studio.set_direction(copy.deepcopy(d) if d else _default_direction(None))
        self._dirty = False
        self._set_targets(str(self.i + 1))

    def _show_emotion(self, d: dict | None) -> None:
        lbl, color = emotion_of(d)
        if not d:
            self.emo_lbl.configure(text="Chưa có cảm xúc — đọc theo phong cách chung của video", text_color=T.TEXT_MUTED)
            return
        sty = self._style_vi.get((d.get("speaking_style") or {}).get("a", ""), "")
        src = "chỉnh tay" if d.get("manual") else "gợi ý AI"
        self.emo_lbl.configure(text=f"● {lbl}/5 · {sty} ({src})", text_color=color)

    def _set_targets(self, text: str) -> None:
        self.targets.delete(0, "end")
        self.targets.insert(0, text)
        self._draw_strip()

    def _same_emotion(self) -> None:
        lbl, _c = emotion_of(self.items.get(self.i))
        ks = [k for k in range(len(self.cues)) if emotion_of(self.items.get(k))[0] == lbl]
        self._set_targets(", ".join(str(k + 1) for k in ks))
        self.status.info(f"Chọn {len(ks)} câu cùng «{lbl or 'chưa có cảm xúc'}» — chỉnh bánh xe rồi «Áp dụng».")

    def go(self, k: int) -> None:
        if self._dirty:
            self.apply()
        self.show(k)

    def step(self, d: int) -> None:
        self.go(self.i + d)

    # ── chỉnh / lưu ───────────────────────────────────────────────────────
    def _changed(self, _d: dict) -> None:
        self._dirty = True
        self.status.info("Chưa áp dụng — bấm «Áp dụng» (chuyển sang câu khác sẽ tự áp).")

    def _save(self) -> None:
        suggest.save(self.srt_path, self.items, n=len(self.cues))
        if self.on_saved:
            self.on_saved()

    def apply(self) -> None:
        d = self.studio.get_direction()
        d["manual"] = True
        ks = parse_targets(self.targets.get(), len(self.cues), self.i)
        for k in ks:
            self.items[k] = copy.deepcopy(d)
        try:
            self._save()
        except OSError as exc:
            self.status.error(f"Không lưu được: {exc}")
            return
        self._dirty = False
        self._show_emotion(self.items.get(self.i))
        self._draw_strip()
        self.status.ok(f"Đã áp cho {len(ks)} câu — «Tạo lại giọng video» để nghe cả video (chỉ câu đổi gọi API).")

    def clear(self) -> None:
        ks = parse_targets(self.targets.get(), len(self.cues), self.i)
        for k in ks:
            self.items.pop(k, None)
        try:
            self._save()
        except OSError as exc:
            self.status.error(f"Không lưu được: {exc}")
            return
        self._dirty = False
        self.show(self.i)
        self.status.ok(f"Đã bỏ cảm xúc {len(ks)} câu — đọc theo phong cách chung.")

    def regenerate(self) -> None:
        if self._dirty:
            self.apply()
        if self.on_regenerate:
            self.on_regenerate()
            self.status.ok("Đang dựng lại giọng video (xem log / thanh tiến độ).")

    def close(self) -> None:
        if self._dirty:
            self.apply()
        self.destroy()

    # ── nghe thử ──────────────────────────────────────────────────────────
    def _play(self, path: Path) -> None:
        try:
            self.window.player.play(path)
        except Exception as exc:                 # noqa: BLE001
            self.status.error(f"Không phát được: {exc}")

    def preview(self) -> None:
        from app.services.direction.apply import preview_cue

        i, d = self.i, self.studio.get_direction()
        t = self.st.tts
        speed = max(0.25, self.st.video.speed / 100.0)
        settings = self.st.settings
        cues = self.cues
        self.play_btn.configure(state="disabled")
        self.status.info(f"Đang đọc thử câu {i + 1}… (lần đầu tốn 1 lượt; nghe lại / dựng track sau lấy từ cache)")

        def done(path, err):
            if not self.winfo_exists():
                return
            self.play_btn.configure(state="normal")
            if err:
                self.status.error(f"Không đọc được: {str(err).splitlines()[0][:200]}")
                return
            self.status.ok(f"Câu {i + 1}: {emotion_of(d)[0]}/5 — đang phát.")
            self._play(path)

        self.window.run_bg(lambda: preview_cue(cues, i, d, t, settings, video_speed=speed), done)


def open_segment_director(master, window, item: VideoItem, cues: list[Cue], ref: list[Cue], srt_path: str,
                          index: int = 0, **kw) -> SegmentDirector:
    win = SegmentDirector(master, window, item, cues, ref, srt_path, index=index, **kw)
    Tooltip(win.canvas, "Mỗi ô một câu · màu = cảm xúc · viền sáng = câu đang chọn / chỉnh tay")
    return win
