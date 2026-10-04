"""Bảng Duyệt (bước 2 của Chuẩn bị → Duyệt → Xuất) — nằm ngay trong cửa sổ chính, chỗ các tab tính năng.

Khung xem trước luôn hiện cạnh bên: chọn video ở đây = chọn video đó; bấm vào ô câu nào thì khung xem trước nhảy tới câu
đó, nút ▶ phát đúng câu đó (đủ giọng lồng, hiệu ứng); đang phát thì câu đang đọc được tô sáng. Sửa câu sai / bảo AI dịch
lại → «Lưu & tạo lại giọng» (câu không đổi lấy từ cache nên rất nhanh) → Duyệt hoặc Bỏ qua → nút «Xuất (n)» trên cùng.

Không lặp chức năng của cửa sổ chính: phát / render thử / xuất dùng nút sẵn có của khung xem trước và nút trên cùng.
"""

from __future__ import annotations

import bisect
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.core.state import VideoItem
from app.services.srt_utils import format_ts, load_srt, save_srt, wrap_lines
from app.services.translate_router import RETRANSLATE_PRESETS
from app.ui.widgets.common import StatusLabel, hint
from app.ui.widgets.ui import Select, Tooltip, button

APP = "Edit Studio"
_MARK = {"": ("○", "Chưa chuẩn bị", "TEXT_MUTED"), "pending": ("●", "Chờ duyệt", "WARNING"),
         "approved": ("✓", "Đã duyệt", "SUCCESS"), "skipped": ("–", "Bỏ qua", "TEXT_MUTED")}


def _mark(it: VideoItem) -> tuple[str, str, str]:
    if it.status == "Lỗi":
        return "✖", "Lỗi", T.DANGER
    if it.status in ("Đang chuẩn bị", "Đang xử lý"):
        return "⏳", it.status, T.INFO
    if it.status == "Xong" and it.review == "approved":
        return "★", "Đã xuất", T.SUCCESS
    sym, label, color = _MARK.get(it.review, _MARK[""])
    return sym, label, getattr(T, color)


class ReviewPanel(ctk.CTkFrame):
    """Tab «Duyệt» trong bảng tính năng (cùng giao diện module: on_shown / on_hidden / on_active_changed / refresh)."""

    key = "review"
    title = "Duyệt"
    icon = "✔"
    PAGE = 30
    _is_translation = False             # đang sửa bản dịch (có câu gốc tương ứng) → hiện «AI dịch lại»

    def __init__(self, master, window) -> None:
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.window = window
        self.app_state = window.app_state
        self.item: VideoItem | None = None
        self.cues: list = []
        self.ref: list = []
        self.texts: list[str] = []
        self._orig_texts: list[str] = []
        self.edit_path: str | None = None
        self.page = 0
        self._cur = -1                  # câu đang ở vị trí khung xem trước
        self._stop_at: float | None = None
        self._tick_id: str | None = None
        self._selecting = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # ── Đầu: tiêu đề + đếm + chọn video ──────────────────────────────
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        from app.ui.icons import icon

        ctk.CTkLabel(head, text="  Duyệt video", image=icon("check", 18, T.ACCENT, dy=1), compound="left",
                     font=T.font(16, "medium"), anchor="w").pack(side="left")
        button(head, "Hướng dẫn", icon_name="info", variant="ghost", size="sm", icon_color=T.TEXT_DIM,
               command=lambda: self.window.toggle_help("review")).pack(side="right")
        cnt = ctk.CTkFrame(self, fg_color="transparent")
        cnt.grid(row=1, column=0, sticky="ew", padx=4, pady=(2, 6))
        self.counts = ctk.CTkLabel(cnt, text="", font=T.font(11), text_color=T.TEXT_DIM, anchor="w")
        self.counts.pack(side="left")
        button(cnt, "Duyệt hết", icon_name="check", variant="ghost", size="sm", command=self._approve_all,
               tip="Duyệt luôn mọi video đang chờ (không xem từng video)").pack(side="right")

        card = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=10)
        card.grid(row=2, column=0, sticky="ew", padx=2)
        card.grid_columnconfigure(1, weight=1)
        self.prev_vid = ctk.CTkButton(card, text="‹", width=32, height=32, command=lambda: self._step(-1),
                                      **T.secondary_button())
        self.prev_vid.grid(row=0, column=0, padx=(8, 4), pady=(8, 4))
        Tooltip(self.prev_vid, "Video trước (Ctrl+↑)")
        self.picker = Select(card, ["-"], value="-", command=self._picked, width=200, height=32)
        self.picker.grid(row=0, column=1, sticky="ew", pady=(8, 4))
        self.next_vid = ctk.CTkButton(card, text="›", width=32, height=32, command=lambda: self._step(1),
                                      **T.secondary_button())
        self.next_vid.grid(row=0, column=2, padx=(4, 8), pady=(8, 4))
        Tooltip(self.next_vid, "Video kế (Ctrl+↓)")
        self.info_lbl = ctk.CTkLabel(card, text="", font=T.font(11), text_color=T.TEXT_DIM, anchor="w", justify="left",
                                     wraplength=380)
        self.info_lbl.grid(row=1, column=0, columnspan=3, sticky="ew", padx=10)
        acts = ctk.CTkFrame(card, fg_color="transparent")
        acts.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=(6, 8))
        acts.grid_columnconfigure((0, 1, 2), weight=1, uniform="a")
        self.approve_btn = button(acts, "Duyệt", icon_name="check", variant="primary", size="sm", command=self._approve,
                                  tip="Video này đạt — sẽ được xuất; tự sang video chờ duyệt kế (Ctrl+Enter)")
        self.approve_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        button(acts, "Bỏ qua", size="sm", command=self._skip, tip="Không xuất video này").grid(
            row=0, column=1, sticky="ew", padx=4)
        self.prep_btn = button(acts, "Chuẩn bị lại", icon_name="refresh", size="sm", command=self._reprepare,
                               tip="Làm lại phần còn thiếu: phụ đề / dịch / giọng (giữ file đã có)")
        self.prep_btn.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        self.sub_head = ctk.CTkLabel(self, text="", font=T.font(12, "medium"), text_color=T.TEXT_DIM, anchor="w",
                                     justify="left", wraplength=400)
        self.sub_head.grid(row=3, column=0, sticky="ew", padx=4, pady=(10, 2))

        # ── Câu phụ đề: mỗi câu 2 dòng (▶ số · giờ · câu gốc) / (ô sửa · ↻) — vừa bảng tính năng hẹp ──
        self.rows_frame = ctk.CTkScrollableFrame(self, fg_color=T.BG_INPUT, corner_radius=8)
        self.rows_frame.grid(row=4, column=0, sticky="nsew", padx=2)
        self.rows_frame.grid_columnconfigure(0, weight=1)
        self._rows: list[dict] = []
        for r in range(self.PAGE):
            top = ctk.CTkFrame(self.rows_frame, fg_color="transparent")
            play = ctk.CTkButton(top, text="", width=96, height=22, anchor="w", font=T.mono(10), fg_color="transparent",
                                 hover_color=T.BG_HOVER, text_color=T.TEXT_MUTED, command=lambda r=r: self._play_row(r))
            play.pack(side="left")
            ref = ctk.CTkLabel(top, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w", justify="left",
                               wraplength=270)
            ref.pack(side="left", fill="x", expand=True, padx=(4, 0))
            ent = ctk.CTkEntry(self.rows_frame, height=30, font=T.font(12))
            ent.bind("<FocusIn>", lambda _e, r=r: self._focus_row(r))
            ent.bind("<Return>", lambda _e, r=r: self._move(r, 1))
            ent.bind("<Down>", lambda _e, r=r: self._move(r, 1))
            ent.bind("<Up>", lambda _e, r=r: self._move(r, -1))
            ent.bind("<Control-Return>", lambda _e: (self._approve(), "break")[1])
            ent.bind("<Control-Down>", lambda _e: (self._step(1), "break")[1])
            ent.bind("<Control-Up>", lambda _e: (self._step(-1), "break")[1])
            redo = ctk.CTkButton(self.rows_frame, text="↻", width=30, height=30, command=lambda r=r: self._ai_row(r),
                                 **{**T.secondary_button(), "font": T.font(13, "bold")})
            Tooltip(play, "Phát đúng câu này trên khung xem trước (có giọng lồng)")
            Tooltip(redo, "AI dịch lại riêng câu này theo «Phong cách / yêu cầu» bên dưới (xem câu trước/sau làm ngữ cảnh)")
            self._rows.append({"play": play, "ref": ref, "entry": ent, "redo": redo, "top": top,
                               "widgets": [(top, dict(row=2 * r, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 0))),
                                           (ent, dict(row=2 * r + 1, column=0, sticky="ew", padx=(4, 2), pady=(0, 2))),
                                           (redo, dict(row=2 * r + 1, column=1, padx=(2, 4), pady=(0, 2)))]})

        # ── AI dịch lại: dùng chung ô «Phong cách dịch» của tab SRT (một chỗ cấu hình duy nhất) ──
        self.ai_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.ai_bar.grid(row=5, column=0, sticky="ew", padx=2, pady=(6, 0))
        self.ai_bar.grid_columnconfigure(0, weight=1)
        self.ai_req = ctk.CTkComboBox(self.ai_bar, values=RETRANSLATE_PRESETS, height=30, font=T.font(12),
                                      command=lambda _v: self._req_changed())
        self.ai_req.grid(row=0, column=0, sticky="ew")
        self.ai_req.bind("<FocusOut>", lambda _e: self._req_changed())
        Tooltip(self.ai_req, "Phong cách / yêu cầu dịch (chung với ô «Phong cách» ở tab SRT), vd «xưng anh–em, văn nói»")
        self.ai_all_btn = button(self.ai_bar, "Dịch lại cả video", icon_name="sparkle", size="sm", command=self._ai_all,
                                 tip="AI dịch lại toàn bộ theo yêu cầu (xem bản hiện tại để biết bạn chưa ưng chỗ nào). "
                                     "Kết quả vào ô sửa — xem rồi bấm Lưu.")
        self.ai_all_btn.grid(row=0, column=1, padx=(6, 0))

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=6, column=0, sticky="ew", padx=2, pady=(6, 0))
        self.prev_btn = ctk.CTkButton(nav, text="‹", width=30, height=30, command=lambda: self._show_page(self.page - 1),
                                      **T.secondary_button())
        self.prev_btn.pack(side="left")
        self.page_lbl = ctk.CTkLabel(nav, text="", font=T.font(11), width=90)
        self.page_lbl.pack(side="left", padx=4)
        self.next_btn = ctk.CTkButton(nav, text="›", width=30, height=30, command=lambda: self._show_page(self.page + 1),
                                      **T.secondary_button())
        self.next_btn.pack(side="left")
        self.save_btn = button(nav, "Lưu & tạo lại giọng", icon_name="save", variant="primary", size="sm",
                               command=self._save_subs,
                               tip="Lưu các câu đã sửa; video có lồng tiếng thì tạo lại giọng (câu không đổi dùng lại)")
        self.save_btn.pack(side="right")
        self.status = StatusLabel(self, "")
        self.status.grid(row=7, column=0, sticky="ew", padx=4, pady=(6, 0))
        hint(self, "Bấm vào ô câu = xem câu đó trên video · ▶ phát câu · Enter/↑↓ sang câu kế · Ctrl+Enter duyệt · "
                   "Ctrl+↑↓ đổi video · xong hết bấm «Xuất (n)» trên cùng.").grid(row=8, column=0, sticky="ew", padx=4,
                                                                                pady=(2, 6))
        self.app_state.on("videos_changed", self.reload)
        self.reload()

    def destroy(self) -> None:
        self.app_state.off("videos_changed", self.reload)
        self._stop_tick()
        super().destroy()

    # ── Giao diện module ─────────────────────────────────────────────────
    def refresh(self) -> None:
        self._load_req()
        self._update_counts()

    def on_shown(self) -> None:
        self.reload()
        self._start_tick()

    def on_hidden(self) -> None:
        self._stop_tick()
        if self._dirty() and messagebox.askyesno(APP, "Lưu các câu phụ đề vừa sửa ở tab Duyệt?", parent=self.window):
            self._save_subs()

    def scroll_top(self) -> None:
        pass

    def on_active_changed(self) -> None:
        """Chọn video khác ở hàng đợi / nơi khác → bảng Duyệt theo video đó."""
        if self._selecting:
            return
        act = self.app_state.active
        if act is not self.item:
            if self.item is not None and self._dirty() and \
                    messagebox.askyesno(APP, "Lưu các câu phụ đề vừa sửa?", parent=self.window):
                self._save_subs()
            self._show_item(act)

    # ── Danh sách video ──────────────────────────────────────────────────
    def reload(self, *_a) -> None:
        if not self.winfo_exists():
            return
        vids = self.app_state.videos
        target = self.app_state.active
        if self.item is not None and self.item in vids:
            target = self.item
        elif target is None or target.review not in ("pending", ""):
            target = next((it for it in vids if it.review == "pending"), target)
        if target is not None and target in vids:
            self.select(vids.index(target), ask=False)
        else:
            self._show_item(None)

    def _update_counts(self) -> None:
        vids = self.app_state.videos
        c = {k: sum(1 for it in vids if it.review == k) for k in ("", "pending", "approved", "skipped")}
        todo_export = sum(1 for it in vids if it.review == "approved" and it.status != "Xong")
        bits = [f"Chờ {c['pending']}", f"Đã duyệt {c['approved']}"]
        if c["skipped"]:
            bits.append(f"Bỏ qua {c['skipped']}")
        if c[""]:
            bits.append(f"Chưa chuẩn bị {c['']}")
        if todo_export:
            bits.append(f"→ Xuất ({todo_export}) ở trên cùng")
        self.counts.configure(text=" · ".join(bits))
        keys = [str(i) for i in range(len(vids))] or ["-"]
        labels = {}
        for i, it in enumerate(vids):
            sym, label, _color = _mark(it)
            labels[str(i)] = f"{sym} {i + 1}. {it.name} — {label}"
        self.picker._labels = labels or {"-": "Chưa có video"}
        self.picker.set_values(keys, keep=False)
        cur = str(vids.index(self.item)) if self.item in vids else keys[0]
        self.picker.set(cur)

    def _picked(self, key: str) -> None:
        if key.isdigit():
            self.select(int(key))

    def select(self, index: int, *, ask: bool = True) -> None:
        vids = self.app_state.videos
        if not (0 <= index < len(vids)):
            return
        if ask and self.item is not None and vids[index] is not self.item and self._dirty():
            if messagebox.askyesno(APP, "Lưu các câu phụ đề vừa sửa?", parent=self.window):
                self._save_subs()
        self._selecting = True
        try:
            self.app_state.set_active(index)             # khung xem trước hiện video này
        finally:
            self._selecting = False
        self._show_item(vids[index])

    def _step(self, d: int) -> None:
        vids = self.app_state.videos
        if self.item in vids:
            self.select(max(0, min(len(vids) - 1, vids.index(self.item) + d)))

    # ── Chi tiết ─────────────────────────────────────────────────────────
    def _show_item(self, it: VideoItem | None) -> None:
        self.item = it
        self._cur = -1
        self._stop_at = None
        self._update_counts()
        self._load_req()
        if it is None:
            self.info_lbl.configure(text="Nạp video vào hàng đợi rồi bấm «Chuẩn bị» trên cùng.")
            self.cues, self.texts, self._orig_texts = [], [], []
            self._is_translation = False
            self.sub_head.configure(text="")
            self._show_page(0, sync=False)
            return
        sym, label, color = _mark(it)
        bits = [f"{sym} {label}"]
        if it.duration:
            bits.append(format_ts(it.duration, ".")[:8])
        if it.config is not None:
            bits.append("cấu hình riêng" + (f" · mẫu «{it.template}»" if it.template else ""))
        elif self.app_state.template_name:
            bits.append(f"mẫu «{self.app_state.template_name}»")
        bits.append("có giọng lồng" if it.voice_path else "chưa có giọng lồng")
        if it.status == "Lỗi" and it.message:
            bits.append("Lỗi: " + it.message)
        self.info_lbl.configure(text="  ·  ".join(bits), text_color=color if it.status == "Lỗi" else T.TEXT_DIM)
        self.prep_btn.configure(text="Chuẩn bị" if not it.review else "Chuẩn bị lại")
        self._load_subs(it)

    def _load_subs(self, it: VideoItem) -> None:
        self.cues, self.ref, self.texts, self.edit_path = [], [], [], None
        self._is_translation = False
        tr = it.srt_translated_path if it.srt_translated_path and Path(it.srt_translated_path).exists() else None
        orig = it.srt_path if it.srt_path and Path(it.srt_path).exists() else None
        if tr:
            self.edit_path, self.cues = tr, load_srt(tr)
            self.ref = load_srt(orig) if orig else []
            self._is_translation = len(self.ref) == len(self.cues)
            self.sub_head.configure(text=f"Bản dịch · {len(self.cues)} câu — sửa câu dịch sai; chữ nhỏ là câu gốc")
        elif orig:
            self.edit_path, self.cues = orig, load_srt(orig)
            self.sub_head.configure(text=f"Phụ đề · {len(self.cues)} câu — sửa câu nhận diện sai")
        else:
            self.sub_head.configure(text="Video này chưa có phụ đề (mẫu không dùng phụ đề / lồng tiếng, hoặc chưa chuẩn bị).")
        self._starts = [c.start for c in self.cues]
        self.texts = [c.text.replace("\n", " ") for c in self.cues]
        self._orig_texts = list(self.texts)
        self._show_page(0, sync=False)

    def _range(self) -> range:
        a = self.page * self.PAGE
        return range(a, min(a + self.PAGE, len(self.cues)))

    def _sync(self) -> None:
        for row, i in zip(self._rows, self._range()):
            if i < len(self.texts):
                self.texts[i] = row["entry"].get()

    def _show_page(self, page: int, sync: bool = True) -> None:
        if sync and self.cues:
            self._sync()
        pages = max(1, (len(self.cues) + self.PAGE - 1) // self.PAGE)
        self.page = max(0, min(pages - 1, page))
        idxs = self._range()
        for r, row in enumerate(self._rows):
            if r < len(idxs):
                i = idxs[r]
                c = self.cues[i]
                row["play"].configure(text=f"▶ {i + 1} · {format_ts(c.start, '.')[3:8]}")
                row["ref"].configure(text=self.ref[i].plain if i < len(self.ref) else "")
                row["entry"].delete(0, "end")
                row["entry"].insert(0, self.texts[i])
                for w, kw in row["widgets"]:
                    if w is not row["redo"] or self._is_translation:
                        w.grid(**kw)
                    else:
                        w.grid_remove()
            else:
                for w, _kw in row["widgets"]:
                    w.grid_remove()
        old = getattr(self, "_painted", -1)
        if 0 <= old < len(self._rows):
            self._rows[old]["play"].configure(text_color=T.TEXT_MUTED)
            self._rows[old]["entry"].configure(border_color=T.BORDER)
        self._painted = -1
        self._paint_current()
        if self._is_translation:
            self.ai_bar.grid()
        else:
            self.ai_bar.grid_remove()
        self.page_lbl.configure(text=f"Trang {self.page + 1}/{pages}" if self.cues else "")
        self.prev_btn.configure(state="normal" if self.page > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.page < pages - 1 else "disabled")
        self.save_btn.configure(state="normal" if self.cues else "disabled")
        try:
            self.rows_frame._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    # ── Xem câu trên khung xem trước ─────────────────────────────────────
    def _row_index(self, r: int) -> int | None:
        idxs = self._range()
        return idxs[r] if r < len(idxs) else None

    def _focus_row(self, r: int) -> None:
        i = self._row_index(r)
        pv = self.window.preview
        if i is None or pv.item is not self.item or pv.player.playing or i == self._cur:
            return
        pv.seek(self.cues[i].start + 0.05)           # sửa câu nào thì khung xem trước hiện đúng cảnh câu đó

    def _play_row(self, r: int) -> None:
        i = self._row_index(r)
        pv = self.window.preview
        if i is None or self.item is None:
            return
        if pv.item is not self.item and self.item in self.app_state.videos:
            self.select(self.app_state.videos.index(self.item))
        c = self.cues[i]
        pv.stop_play()
        pv.seek(max(0.0, c.start - 0.15))
        self._stop_at = c.end + 0.25
        pv.start_play()
        self._start_tick()

    def _move(self, r: int, d: int) -> str:
        i = self._row_index(r)
        if i is None:
            return "break"
        j = i + d
        if not (0 <= j < len(self.cues)):
            return "break"
        if j not in self._range():
            self._show_page(j // self.PAGE)
        self._rows[j - self.page * self.PAGE]["entry"].focus_set()
        return "break"

    def _start_tick(self) -> None:
        if self._tick_id is None:
            self._tick_id = self.after(200, self._tick)

    def _stop_tick(self) -> None:
        if self._tick_id is not None:
            try:
                self.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None

    def _tick(self) -> None:
        """Theo dõi khung xem trước: tô câu đang ở vị trí phát; phát một câu thì dừng ở cuối câu."""
        self._tick_id = None
        if not self.winfo_exists() or not self.winfo_ismapped():
            return
        pv = self.window.preview
        if pv.item is self.item and self.cues:
            t = pv.t
            k = bisect.bisect_right(self._starts, t) - 1
            cur = k if 0 <= k < len(self.cues) and t < self.cues[k].end + 0.3 else -1
            if cur != self._cur:
                self._cur = cur
                if cur >= 0 and pv.player.playing and cur not in self._range():
                    self._show_page(cur // self.PAGE)      # đang phát → lật trang theo câu đang đọc
                self._paint_current()
            if self._stop_at is not None:
                if not pv.player.playing:
                    self._stop_at = None
                elif t >= self._stop_at:
                    self._stop_at = None
                    pv.stop_play()
        self._tick_id = self.after(200, self._tick)

    def _paint_current(self) -> None:
        """Tô dòng của câu đang ở vị trí phát (chỉ vẽ lại 2 dòng đổi trạng thái — configure CTk vẽ lại cả widget)."""
        want = self._cur - self.page * self.PAGE if self._cur in self._range() else -1
        old = getattr(self, "_painted", -1)
        if want == old:
            return
        for r, on in ((old, False), (want, True)):
            if 0 <= r < len(self._rows):
                self._rows[r]["play"].configure(text_color=T.ACCENT if on else T.TEXT_MUTED)
                self._rows[r]["entry"].configure(border_color=T.ACCENT if on else T.BORDER)
        self._painted = want
        if want >= 0 and self.window.preview.player.playing:
            try:
                n = max(1, len(self._range()))
                self.rows_frame._parent_canvas.yview_moveto(max(0.0, (want - 1) / n))
            except Exception:
                pass

    # ── Lưu ──────────────────────────────────────────────────────────────
    def _dirty(self) -> bool:
        if not self.cues:
            return False
        self._sync()
        return self.texts != self._orig_texts

    def _save_subs(self) -> None:
        it = self.item
        if it is None or not self.cues or not self.edit_path:
            return
        self._sync()
        changed = 0
        for c, new, old in zip(self.cues, self.texts, self._orig_texts):
            if new.strip() != old.strip():
                c.text = wrap_lines(new.strip())
                changed += 1
        if not changed:
            self.status.info("Không có câu nào thay đổi.")
            return
        save_srt(self.cues, self.edit_path)
        self._orig_texts = list(self.texts)
        self.window.log(f"{it.name}: đã sửa {changed} câu phụ đề.")
        st = self.app_state.state_for(it)
        if st.tts.enable_tts:
            self.status.warn(f"Đã lưu {changed} câu — đang tạo lại giọng (câu không đổi dùng lại)…")
            self._prepare_one(it, label="Tạo lại giọng")
        else:
            self.status.ok(f"Đã lưu {changed} câu.")
        self.window.preview.schedule_refresh(0)

    # ── AI dịch lại ──────────────────────────────────────────────────────
    def _load_req(self) -> None:
        self.ai_req.set(self.app_state.subtitle.translate_style or "")

    def _req_changed(self) -> None:
        v = self.ai_req.get().strip()
        cfg = self.app_state.subtitle
        if v != cfg.translate_style:
            cfg.translate_style = v
            self.app_state.config_changed("subtitle")
            m = self.window.modules.get("srt")
            if m is not None:
                m.refresh()

    def _retranslate(self, idxs: list[int] | None, label: str) -> None:
        it = self.item
        if it is None or not self._is_translation:
            return
        self._sync()
        self._req_changed()
        req = self.ai_req.get().strip()
        previous = list(self.texts)
        pipe = self.window.pipeline_for(it)
        win = self.window
        n = len(self.cues) if idxs is None else len(idxs)
        self.status.warn(f"AI đang dịch lại {n} câu" + (f" — «{req}»" if req else "") + "…")

        def work(cancel):
            return pipe.retranslate(it, idxs, instruction=req, previous=previous, save=False,
                                    progress=win.set_busy_progress, log=win.log_bg, cancel=cancel)

        def done(result, error):
            if not self.winfo_exists() or self.item is not it:
                return                         # đã chuyển sang video khác trong lúc chờ
            if error:
                self.status.error(f"Dịch lại lỗi: {str(error).splitlines()[0][:200]}")
                return
            self._sync()
            changed = 0
            for i, t in result.items():
                if i < len(self.texts) and t.strip() and t.strip() != self.texts[i].strip():
                    self.texts[i] = t.strip()
                    changed += 1
            self._show_page(self.page, sync=False)
            if changed:
                self.status.ok(f"AI đã dịch lại {changed} câu (chưa lưu) — xem lại, sửa nếu cần rồi bấm "
                               "«Lưu & tạo lại giọng».")
            else:
                self.status.info("AI trả về giống bản cũ — thử ghi yêu cầu cụ thể hơn (vd: «xưng anh–em, bỏ từ thừa»).")

        win.run_task(label, work, done)

    def _ai_row(self, r: int) -> None:
        i = self._row_index(r)
        if i is not None:
            self._retranslate([i], f"Dịch lại câu {i + 1}")

    def _ai_all(self) -> None:
        if self.cues:
            self._retranslate(None, "AI dịch lại phụ đề")

    # ── Hành động ────────────────────────────────────────────────────────
    def _prepare_one(self, it: VideoItem, label: str = "Chuẩn bị") -> None:
        pipe = self.window.pipeline_for(it)
        win = self.window

        def work(cancel):
            pipe.prepare(it, on_update=lambda x: win.ui_call(lambda: win.queue_update(x)), log=win.log_bg, cancel=cancel)

        def done(_r, error):
            if not self.winfo_exists():
                return
            if error:
                it.status, it.message = "Lỗi", str(error).splitlines()[0][:160]
                self.status.error(f"Lỗi: {error}")
            else:
                self.status.ok(f"{label} xong — bấm ▶ ở câu bất kỳ để nghe lại, ưng thì Duyệt.")
            win.queue_update(it)
            self._update_counts()
            if it is self.item:
                self._show_item(it)
                win.preview.schedule_refresh(0)

        win.run_task(label, work, done)

    def _reprepare(self) -> None:
        if self.item is not None:
            self.status.warn("Đang chuẩn bị… (xem log)")
            self._prepare_one(self.item)

    def _set_review(self, it: VideoItem, review: str) -> None:
        it.review = review
        it.status = {"approved": "Đã duyệt", "skipped": "Bỏ qua", "pending": "Chờ duyệt"}.get(review, it.status)
        self.window.queue_update(it)
        self.app_state.mark_dirty()

    def _next_pending(self) -> None:
        vids = self.app_state.videos
        start = vids.index(self.item) + 1 if self.item in vids else 0
        for k in list(range(start, len(vids))) + list(range(0, start)):
            if vids[k].review == "pending":
                self.select(k, ask=False)
                return
        self._show_item(self.item)
        ready = sum(1 for it in vids if it.review == "approved" and it.status != "Xong")
        self.status.ok(f"Đã duyệt hết — bấm «Xuất ({ready})» trên cùng để render." if ready else "Đã duyệt hết.")

    def _approve(self) -> None:
        it = self.item
        if it is None:
            return
        if self._dirty():
            self._save_subs()
        self._set_review(it, "approved")
        self.window.preview.stop_play()
        self._next_pending()

    def _skip(self) -> None:
        if self.item is not None:
            self._set_review(self.item, "skipped")
            self.window.preview.stop_play()
            self._next_pending()

    def _approve_all(self) -> None:
        pend = [it for it in self.app_state.videos if it.review == "pending"]
        if not pend:
            self.status.info("Không còn video nào chờ duyệt.")
            return
        if not messagebox.askyesno(APP, f"Duyệt luôn {len(pend)} video đang chờ (không xem từng video)?",
                                   parent=self.window):
            return
        for it in pend:
            self._set_review(it, "approved")
        self._show_item(self.item)
        self._next_pending()
