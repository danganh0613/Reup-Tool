"""Sửa lời thoại của video đang chọn — danh sách câu gắn với khung xem trước (dùng trong tab «Lời thoại»).

Bấm vào ô câu → khung xem trước nhảy tới cảnh câu đó · ▶ phát đúng câu đó (có giọng lồng) rồi tự dừng · đang phát thì
câu đang đọc được tô sáng · ↻ AI dịch lại một câu · ô «Nhờ AI sửa» (gõ yêu cầu, AI chỉ sửa đúng các câu liên quan — cả
video). Câu AI sửa tô vàng, ↶ trả lại từng câu, «Hoàn tác» trả lại hết · ⊘ «không đọc câu này» (lời bài hát, câu
trích: giữ âm gốc, vẫn hiện phụ đề) · «Tìm & thay» cho cả video · «Lưu & tạo lại giọng» (câu không đổi dùng lại giọng cũ
nên rất nhanh; không sửa chữ mà giọng đã cũ thì vẫn tạo lại).

Danh sách chia trang (không có thanh cuộn riêng) để nằm gọn trong bảng tính năng vốn đã cuộn được.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.core.state import VideoItem
from app.services.job_runner import error_line
from app.services.srt_utils import format_ts, load_srt, save_srt, wrap_lines
from app.ui.widgets.common import StatusLabel, auto_wrap, info_icon
from app.ui.widgets.ui import Tooltip, button
from app.ui.progress_panel import friendly_error


class CueEditor(ctk.CTkFrame):
    PAGE = 12

    def __init__(self, master, window, *, on_saved: Callable[[VideoItem], None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.window = window
        self.app_state = window.app_state
        self.on_saved = on_saved
        self.item: VideoItem | None = None
        self.cues: list = []
        self.ref: list = []
        self.texts: list[str] = []
        self._orig_texts: list[str] = []
        self._starts: list[float] = []
        self.edit_path: str | None = None
        self.is_translation = False
        self.page = 0
        self._cur = -1                  # câu đang ở vị trí khung xem trước
        self._painted = -1
        self._stop_at: float | None = None
        self._tick_id: str | None = None
        self._ai_marks: set[int] = set()          # câu AI vừa sửa (tô vàng tới khi lưu / đổi video)
        self._ai_before: dict[int, str] = {}      # câu trước khi AI sửa (↶ / Hoàn tác)
        self._src_sig: tuple = ()                 # file phụ đề lúc nạp — đổi (Chuẩn bị / dịch lại xong) → nạp lại
        self._ticks = 0
        self._noread: list[float] = []            # mốc bắt đầu các câu «không đọc» (lưu riêng, không nằm trong SRT)
        self._dirty_mark = False
        self.grid_columnconfigure(0, weight=1)

        self.head = auto_wrap(ctk.CTkLabel(self, text="", font=T.font(12, "medium"), text_color=T.TEXT_DIM, anchor="w",
                                           justify="left", wraplength=400))
        self.head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.rows_frame = ctk.CTkFrame(self, fg_color=T.BG_INPUT, corner_radius=8)
        self.rows_frame.grid(row=1, column=0, sticky="ew")
        self.rows_frame.grid_columnconfigure(0, weight=1)
        self._rows: list[dict] = []
        for r in range(self.PAGE):
            top = ctk.CTkFrame(self.rows_frame, fg_color="transparent")
            play = ctk.CTkButton(top, text="", width=96, height=22, anchor="w", font=T.mono(10), fg_color="transparent",
                                 hover_color=T.BG_HOVER, text_color=T.TEXT_MUTED, command=lambda r=r: self._play_row(r))
            play.pack(side="left")
            ref = auto_wrap(ctk.CTkLabel(top, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w",
                                         justify="left", wraplength=300))
            ref.pack(side="left", fill="x", expand=True, padx=(4, 0))
            ent = ctk.CTkEntry(self.rows_frame, height=30, font=T.font(12))
            ent.bind("<FocusIn>", lambda _e, r=r: self._focus_row(r))
            ent.bind("<KeyRelease>", lambda _e: self._update_dirty_mark(), add="+")
            ent.bind("<Return>", lambda _e, r=r: self._move(r, 1))
            ent.bind("<Down>", lambda _e, r=r: self._move(r, 1))
            ent.bind("<Up>", lambda _e, r=r: self._move(r, -1))
            # Ctrl+Enter / Ctrl+↑↓ (duyệt, đổi video) — ô nhập tự bắt Enter/↑↓ nên chuyển tiếp lên cửa sổ chính
            ent.bind("<Control-Return>", lambda _e: (self.window._review_key("approve"), "break")[1])
            ent.bind("<Control-Down>", lambda _e: (self.window._review_key("next"), "break")[1])
            ent.bind("<Control-Up>", lambda _e: (self.window._review_key("prev"), "break")[1])
            redo = ctk.CTkButton(self.rows_frame, text="↻", width=30, height=30, command=lambda r=r: self._ai_row(r),
                                 **{**T.secondary_button(), "font": T.font(13, "bold")})
            back = ctk.CTkButton(self.rows_frame, text="↶", width=30, height=30, command=lambda r=r: self._revert_row(r),
                                 **{**T.secondary_button(), "font": T.font(13, "bold"), "text_color": T.WARNING})
            mute = ctk.CTkButton(self.rows_frame, text="⊘", width=30, height=30, command=lambda r=r: self._toggle_noread(r),
                                 **{**T.secondary_button(), "font": T.font(13, "bold")})
            Tooltip(play, "Phát đúng câu này trên khung xem trước (có giọng lồng)")
            Tooltip(redo, "AI dịch lại riêng câu này (theo «Phong cách dịch», xem câu trước/sau làm ngữ cảnh)")
            Tooltip(back, "Trả lại câu này như trước khi AI sửa")
            Tooltip(mute, "Không lồng tiếng câu này — giữ âm gốc (lời bài hát, câu trích…), phụ đề vẫn hiện. Bấm lần nữa "
                          "để đọc lại.")
            self._rows.append({"play": play, "ref": ref, "entry": ent, "redo": redo, "back": back, "mute": mute, "top": top,
                               "widgets": [(top, dict(row=2 * r, column=0, columnspan=4, sticky="ew", padx=4, pady=(4, 0))),
                                           (ent, dict(row=2 * r + 1, column=0, sticky="ew", padx=(4, 2), pady=(0, 2))),
                                           (redo, dict(row=2 * r + 1, column=1, padx=(2, 2), pady=(0, 2))),
                                           (back, dict(row=2 * r + 1, column=2, padx=(0, 2), pady=(0, 2))),
                                           (mute, dict(row=2 * r + 1, column=3, padx=(0, 4), pady=(0, 2)))]})

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.prev_btn = ctk.CTkButton(nav, text="‹", width=30, height=30, command=lambda: self.show_page(self.page - 1),
                                      **T.secondary_button())
        self.prev_btn.pack(side="left")
        self.page_lbl = ctk.CTkLabel(nav, text="", font=T.font(11), width=90)
        self.page_lbl.pack(side="left", padx=4)
        self.next_btn = ctk.CTkButton(nav, text="›", width=30, height=30, command=lambda: self.show_page(self.page + 1),
                                      **T.secondary_button())
        self.next_btn.pack(side="left")
        self.save_btn = button(nav, "Lưu & tạo lại giọng", icon_name="save", variant="primary", size="sm",
                               command=self.save,
                               tip="Lưu các câu đã sửa; video có lồng tiếng thì tạo lại giọng (câu không đổi dùng lại)")
        self.save_btn.pack(side="right")

        # ── Nhờ AI sửa (kiểu chat): AI tự tìm câu liên quan tới yêu cầu và chỉ sửa đúng những câu đó ──
        ai = ctk.CTkFrame(self, fg_color="transparent")
        ai.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ai.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(ai, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 2))
        ctk.CTkLabel(head, text="Nhờ AI sửa", font=T.font(11), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        info_icon(head, "Gõ yêu cầu bằng lời, vd «xưng anh – em», «văn nói tự nhiên hơn», «sửa tên Lý Tiểu Long». AI tự "
                        "tìm đúng các câu cần sửa trong cả video, câu khác giữ nguyên. Câu sửa tô vàng, chưa lưu — "
                        "↶ trả lại từng câu.").pack(side="left", padx=(4, 0))
        self.find_btn = button(head, "Tìm & thay", icon_name="edit", variant="ghost", size="sm", command=self.find_replace,
                               tip="Đổi một chữ / tên riêng ở mọi câu của video (chưa lưu — xem rồi bấm Lưu)")
        self.find_btn.pack(side="right")
        self.ai_req = ctk.CTkEntry(ai, height=32, font=T.font(12),
                                   placeholder_text="vd: xưng anh – em, văn nói tự nhiên hơn")
        self.ai_req.grid(row=1, column=0, sticky="ew")
        self.ai_req.bind("<Return>", lambda _e: (self.ai_edit(), "break")[1])
        self.ai_send = button(ai, "Gửi", icon_name="sparkle", size="sm", command=self.ai_edit,
                              tip="Gửi yêu cầu (Enter). Câu AI sửa được tô vàng, chưa lưu — xem rồi bấm «Lưu & tạo lại giọng».")
        self.ai_send.grid(row=1, column=1, padx=(6, 0))
        self.ai_undo = button(ai, "Hoàn tác", icon_name="undo", variant="ghost", size="sm", command=self.ai_revert,
                              tip="Trả lại mọi câu AI đã sửa (chưa lưu) như cũ")
        self.ai_undo.grid(row=1, column=2, padx=(4, 0))
        self.ai_undo.grid_remove()
        self.status = StatusLabel(self, "")
        self.status.grid(row=4, column=0, sticky="ew", pady=(6, 0))

    def destroy(self) -> None:
        self.stop_tick()
        super().destroy()

    # ── Nạp ──────────────────────────────────────────────────────────────
    @staticmethod
    def _file_sig(it: VideoItem | None) -> tuple:
        out = []
        for p in ((it.srt_translated_path, it.srt_path) if it is not None else ()):
            try:
                out.append((p, Path(p).stat().st_mtime_ns) if p else None)
            except OSError:
                out.append((p, 0))
        return tuple(out)

    def stale(self) -> bool:
        """File phụ đề của video đang mở đã đổi từ lúc nạp (máy vừa nhận diện / dịch lại)."""
        return self.item is not None and self._file_sig(self.item) != self._src_sig

    def reload_if_stale(self) -> None:
        """Nạp lại khi file đổi mà người dùng chưa sửa dở câu nào (đang sửa thì giữ nguyên, không mất chữ)."""
        if self.stale() and not self.dirty() and not self._ai_marks:
            page = self.page
            self.load(self.item)
            self.show_page(page, sync=False)

    def load(self, it: VideoItem | None) -> None:
        self.item = it
        self._src_sig = self._file_sig(it)
        self._cur = -1
        self._stop_at = None
        self._clear_ai_marks()
        self.cues, self.ref, self.texts, self.edit_path = [], [], [], None
        self.is_translation = False
        if it is None:
            self.head.configure(text="Chưa chọn video.")
        else:
            tr = it.srt_translated_path if it.srt_translated_path and Path(it.srt_translated_path).exists() else None
            orig = it.srt_path if it.srt_path and Path(it.srt_path).exists() else None
            if tr:
                self.edit_path, self.cues = tr, load_srt(tr)
                self.ref = load_srt(orig) if orig else []
                self.is_translation = len(self.ref) == len(self.cues)
                self.head.configure(text=f"Bản dịch · {len(self.cues)} câu — chữ nhỏ là câu gốc")
            elif orig:
                self.edit_path, self.cues = orig, load_srt(orig)
                self.head.configure(text=f"Phụ đề · {len(self.cues)} câu")
            else:
                self.head.configure(text="Video này chưa có lời thoại — nhận diện ở tab Nhận diện, chọn file SRT bên dưới, "
                                         "hoặc bấm «Chuẩn bị».")
        self._starts = [c.start for c in self.cues]
        self.texts = [c.text.replace("\n", " ") for c in self.cues]
        self._orig_texts = list(self.texts)
        self._noread = self.window.pipeline.load_noread(it) if it is not None else []
        self.show_page(0, sync=False)
        self._update_dirty_mark()
        self.status.info("")

    def _range(self) -> range:
        a = self.page * self.PAGE
        return range(a, min(a + self.PAGE, len(self.cues)))

    def _sync(self) -> None:
        for row, i in zip(self._rows, self._range()):
            if i < len(self.texts):
                self.texts[i] = row["entry"].get()

    def show_page(self, page: int, sync: bool = True) -> None:
        if sync and self.cues:
            self._sync()
        pages = max(1, (len(self.cues) + self.PAGE - 1) // self.PAGE)
        self.page = max(0, min(pages - 1, page))
        idxs = self._range()
        for r, row in enumerate(self._rows):
            if r < len(idxs):
                i = idxs[r]
                c = self.cues[i]
                off = self._is_noread(i)
                row["play"].configure(text=f"{'⊘' if off else '▶'} {i + 1} · {format_ts(c.start, '.')[3:8]}",
                                      text_color=T.TEXT_MUTED)
                self._paint_ref(row["ref"], i)
                row["entry"].delete(0, "end")
                row["entry"].insert(0, self.texts[i])
                row["entry"].configure(border_color=self._base_border(i), text_color=T.TEXT_MUTED if off else T.TEXT)
                row["mute"].configure(text_color=T.WARNING if off else T.TEXT_DIM)
                for w, kw in row["widgets"]:
                    show = (w is not row["redo"] or self.is_translation) and (w is not row["back"] or i in self._ai_before)
                    if show:
                        w.grid(**kw)
                    else:
                        w.grid_remove()
            else:
                for w, _kw in row["widgets"]:
                    w.grid_remove()
        if not idxs:
            self.rows_frame.grid_remove()
        else:
            self.rows_frame.grid()
        self._painted = -1
        self._paint_current()
        self.page_lbl.configure(text=f"Trang {self.page + 1}/{pages}" if self.cues else "")
        self.prev_btn.configure(state="normal" if self.page > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.page < pages - 1 else "disabled")
        self.save_btn.configure(state="normal" if self.cues else "disabled")
        self.find_btn.configure(state="normal" if self.cues else "disabled")
        self.ai_send.configure(state="normal" if self.cues else "disabled")

    def _paint_ref(self, label: ctk.CTkLabel, i: int) -> None:
        """Dòng chữ nhỏ trên ô sửa: câu gốc; câu AI đã sửa thì thêm câu cũ (tô vàng)."""
        base = self.ref[i].plain if i < len(self.ref) else ""
        if self._is_noread(i):
            base = ("⊘ không đọc — giữ âm gốc · " + base).strip(" ·")
        if i not in self._ai_before:
            label.configure(text=base, text_color=T.TEXT_MUTED)
            return
        old = self._ai_before[i]
        note = f"✎ trước: «{old if len(old) <= 90 else old[:89] + '…'}»"
        label.configure(text=f"{base}\n{note}" if base else note, text_color=T.WARNING)

    # ── Gắn với khung xem trước ──────────────────────────────────────────
    def _row_index(self, r: int) -> int | None:
        idxs = self._range()
        return idxs[r] if 0 <= r < len(idxs) else None

    def _focus_row(self, r: int) -> None:
        i = self._row_index(r)
        pv = self.window.preview
        if i is None or pv.item is not self.item or pv.player.playing or i == self._cur:
            return
        pv.seek(self.cues[i].start + 0.05)           # sửa câu nào thì khung xem trước hiện đúng cảnh câu đó

    def _play_row(self, r: int) -> None:
        i = self._row_index(r)
        pv = self.window.preview
        if i is None or self.item is None or pv.item is not self.item:
            return
        c = self.cues[i]
        pv.stop_play()
        pv.seek(max(0.0, c.start - 0.15))
        self._stop_at = c.end + 0.25
        pv.start_play()
        self.start_tick()

    def _move(self, r: int, d: int) -> str:
        i = self._row_index(r)
        if i is None:
            return "break"
        j = i + d
        if not (0 <= j < len(self.cues)):
            return "break"
        if j not in self._range():
            self.show_page(j // self.PAGE)
        self._rows[j - self.page * self.PAGE]["entry"].focus_set()
        return "break"

    def start_tick(self) -> None:
        if self._tick_id is None:
            self._tick_id = self.after(200, self._tick)

    def stop_tick(self) -> None:
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
        self._ticks += 1
        if self._ticks % 5 == 0:
            self.reload_if_stale()
        if pv.item is self.item and self.cues:
            t = pv.t
            k = bisect.bisect_right(self._starts, t) - 1
            cur = k if 0 <= k < len(self.cues) and t < self.cues[k].end + 0.3 else -1
            if cur != self._cur:
                self._cur = cur
                if cur >= 0 and pv.player.playing and cur not in self._range():
                    self.show_page(cur // self.PAGE)       # đang phát → lật trang theo câu đang đọc
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
        old = self._painted
        if want == old:
            return
        for r, on in ((old, False), (want, True)):
            if 0 <= r < len(self._rows):
                self._rows[r]["play"].configure(text_color=T.ACCENT if on else T.TEXT_MUTED)
                self._rows[r]["entry"].configure(border_color=T.ACCENT if on else self._base_border(self._row_index(r)))
        self._painted = want

    # ── Lưu ──────────────────────────────────────────────────────────────
    def _text_dirty(self) -> bool:
        if not self.cues:
            return False
        self._sync()
        return self.texts != self._orig_texts

    def dirty(self) -> bool:
        return self._text_dirty()

    def save(self, regen: bool = True) -> None:
        """``regen``: tạo lại giọng ngay (False khi sắp chạy Chuẩn bị / Render — lượt chạy tự tạo lại)."""
        it = self.item
        if it is None or not self.cues or not self.edit_path:
            return
        self._sync()
        if self.stale() and self._text_dirty():
            # máy vừa dịch lại / nhận diện lại file này trong lúc bạn sửa → không lặng lẽ ghi đè bản mới
            ans = messagebox.askyesnocancel(
                "Lời thoại", "File lời thoại vừa được cập nhật (dịch lại / nhận diện lại) trong lúc bạn sửa.\n\n"
                             "Có = lưu chữ bạn đang sửa (ghi đè bản mới)\nKhông = bỏ chữ đang sửa, nạp bản mới",
                icon="warning", parent=self.winfo_toplevel())
            if ans is None:
                return
            if not ans:
                self.load(it)
                self.status.info("Đã nạp bản mới.")
                return
        changed = 0
        edited: list[int] = []
        for k, (c, new, old) in enumerate(zip(self.cues, self.texts, self._orig_texts)):
            if new.strip() != old.strip():
                c.text = wrap_lines(new.strip())
                changed += 1
                edited.append(k)
        if not changed:
            st = self.app_state.state_for(it)
            if regen and st.tts.enable_tts and self._voice_stale(it):
                # không sửa chữ nhưng giọng là bản cũ (đổi giọng / tốc độ / câu «không đọc») → tạo lại theo cấu hình
                self.status.warn("Giọng đang là bản cũ — đang tạo lại theo cấu hình hiện tại…")
                self._regenerate(it)
            else:
                self.status.info("Không có câu nào thay đổi.")
            return
        save_srt(self.cues, self.edit_path)
        self._src_sig = self._file_sig(it)
        if self.is_translation:
            self._remember(it, edited)
        self._orig_texts = list(self.texts)
        self._update_dirty_mark()
        if it.status == "Xong":                        # đã xuất rồi mới sửa → bản xuất cũ: tính vào «Xuất (n)» / Render
            it.status = "Cần xuất lại"
            self.window.queue_update(it)
            self.window.update_render_button()
        if self._ai_marks or self._ai_before:          # đã lưu → bỏ tô vàng
            self._clear_ai_marks()
            self.show_page(self.page, sync=False)
        self.window.log(f"{it.name}: đã sửa {changed} câu lời thoại.")
        st = self.app_state.state_for(it)
        if st.tts.enable_tts and regen:
            self.status.warn(f"Đã lưu {changed} câu — đang tạo lại giọng (câu không đổi dùng lại)…")
            self._regenerate(it)
        else:
            self.status.ok(f"Đã lưu {changed} câu — bấm ▶ Phát để xem lại.")
        self.window.preview.schedule_refresh(0)
        if self.on_saved:
            self.on_saved(it)

    def _voice_stale(self, it: VideoItem) -> bool:
        """Video có giọng lồng mà track đang có không khớp cấu hình / lời thoại hiện tại (giọng có sẵn đang khoá: không)."""
        if it.voice_locked:
            return False
        try:
            path, fresh, _cut = self.window.pipeline_for(it).current_voice_track(it)
        except Exception:                            # noqa: BLE001
            return False
        return path is not None and not fresh

    def _update_dirty_mark(self) -> None:
        """Nút Lưu ghi «●» khi còn câu sửa chưa lưu — bấm Xuất / Chuẩn bị app cũng tự lưu trước."""
        from app.ui.widgets.ui import relabel

        dirty = bool(self.cues) and self._text_dirty()
        if dirty != self._dirty_mark:
            self._dirty_mark = dirty
            relabel(self.save_btn, ("● " if dirty else "") + "Lưu & tạo lại giọng")

    # ── Câu «không đọc» ──────────────────────────────────────────────────
    def _is_noread(self, i: int) -> bool:
        return bool(self._noread) and 0 <= i < len(self.cues) and \
            any(abs(self.cues[i].start - s) <= 0.05 for s in self._noread)

    def _toggle_noread(self, r: int) -> None:
        i = self._row_index(r)
        it = self.item
        if i is None or it is None:
            return
        self._sync()
        start = self.cues[i].start
        if self._is_noread(i):
            self._noread = [s for s in self._noread if abs(start - s) > 0.05]
            note = f"Câu {i + 1}: đọc lại như thường."
        else:
            self._noread = sorted(self._noread + [start])
            note = f"Câu {i + 1}: không lồng tiếng — giữ âm gốc, phụ đề vẫn hiện."
        try:
            self.window.pipeline.save_noread(it, self._noread)
        except OSError as exc:
            self.status.error(f"Không lưu được: {exc}")
            return
        self.show_page(self.page, sync=False)
        st = self.app_state.state_for(it)
        if st.tts.enable_tts:
            self.status.warn(note + " Bấm «Lưu & tạo lại giọng» để áp vào giọng.")
        else:
            self.status.info(note + " (Áp khi bật lồng tiếng.)")
        if it.status == "Xong":
            it.status = "Cần xuất lại"
            self.window.queue_update(it)
            self.window.update_render_button()

    # ── Tìm & thay ───────────────────────────────────────────────────────
    def find_replace(self) -> None:
        """Đổi một chữ / tên riêng ở mọi câu (vd «Lý Tiểu Long» → «Lý Tiểu Long»): điền vào ô sửa, chưa lưu."""
        if not self.cues:
            return
        dlg = ctk.CTkToplevel(self)
        dlg.title("Tìm & thay")
        dlg.resizable(False, False)
        dlg.transient(self.winfo_toplevel())
        dlg.grid_columnconfigure(1, weight=1)
        find_var, repl_var, case_var = ctk.StringVar(), ctk.StringVar(), ctk.BooleanVar(value=False)
        for row, (label, var) in enumerate((("Tìm", find_var), ("Thay bằng", repl_var))):
            ctk.CTkLabel(dlg, text=label, font=T.font(12), text_color=T.TEXT_DIM, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(14, 8), pady=(12 if row == 0 else 4, 4))
            ctk.CTkEntry(dlg, textvariable=var, width=300, height=32).grid(row=row, column=1, sticky="ew", padx=(0, 14),
                                                                          pady=(12 if row == 0 else 4, 4))
        ctk.CTkCheckBox(dlg, text="Phân biệt chữ hoa / thường", variable=case_var, font=T.font(12)).grid(
            row=2, column=1, sticky="w", pady=4)
        note = ctk.CTkLabel(dlg, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w")
        note.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14)

        def run() -> None:
            needle = find_var.get()
            if not needle:
                note.configure(text="Gõ chữ cần tìm.")
                return
            self._sync()
            pat = re.compile(re.escape(needle), 0 if case_var.get() else re.IGNORECASE)
            hits = 0
            for k, t in enumerate(self.texts):
                new, n = pat.subn(lambda _m: repl_var.get(), t)
                if n:
                    self.texts[k] = new
                    hits += n
            if not hits:
                note.configure(text="Không thấy chữ này trong lời thoại.")
                return
            self.show_page(self.page, sync=False)
            self._update_dirty_mark()
            self.status.warn(f"Đã thay {hits} chỗ (chưa lưu) — xem lại rồi bấm «Lưu & tạo lại giọng».")
            dlg.destroy()

        foot = ctk.CTkFrame(dlg, fg_color="transparent")
        foot.grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 14))
        button(foot, "Đóng", variant="ghost", size="sm", command=dlg.destroy).pack(side="right")
        button(foot, "Thay tất cả", variant="primary", size="sm", command=run).pack(side="right", padx=(0, 8))
        dlg.bind("<Return>", lambda _e: run())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.after(60, dlg.lift)

    def _remember(self, it: VideoItem, idxs: list[int]) -> None:
        """Câu dịch sửa tay → bộ nhớ dịch (cùng khoá với bước dịch), lần «Dịch phụ đề» sau không trả lại bản AI cũ."""
        from app.services.translate_router import TranslationMemory
        from app.services.translate_vl import memory_style

        try:
            cfg = self.app_state.state_for(it).subtitle
            mem, key = TranslationMemory(), memory_style(cfg.translate_style, self.app_state.settings.glossary,
                                                         self.app_state.settings.translate_model)
            for k in idxs:
                if k < len(self.ref) and self.ref[k].plain.strip() and self.cues[k].plain.strip():
                    mem.put(cfg.translate_target, self.ref[k].plain.replace("\n", " ").strip(),
                            self.cues[k].plain.replace("\n", " ").strip(), key)
            mem.save()
            # câu đã tự sửa → bước «AI rút gọn câu đọc tràn khung» không ghi đè chữ của bạn
            self.window.pipeline.add_line_locks(self.edit_path, "edited",
                                                [self.ref[k].plain for k in idxs if k < len(self.ref)])
        except Exception:                            # noqa: BLE001 — bộ nhớ dịch là phụ, không làm hỏng việc lưu
            pass

    def _regenerate(self, it: VideoItem) -> None:
        pipe = self.window.pipeline_for(it)
        win = self.window

        keep = (it.review, it.status)            # tạo lại giọng không được làm mất trạng thái duyệt (vd vừa bấm Duyệt)

        def upd(x) -> None:
            win.queue_update(x)
            win.progress.update_item(x, 1, 1, x.progress)

        def work(cancel):
            pipe.prepare(it, on_update=lambda x: win.ui_call(lambda: upd(x)), log=win.log_bg, cancel=cancel)
            if keep[0] in ("approved", "skipped") or not win.review_mode:
                it.review, it.status = keep

        def done(_r, error):
            if error and it.status in ("Đang chuẩn bị", "Đang xử lý"):     # lỗi / dừng giữa chừng: trả trạng thái cũ
                it.review, it.status = keep
            if not self.winfo_exists():
                return
            if error:
                self.status.error(f"Tạo lại giọng lỗi: {friendly_error(error_line(error, 200))}")
            else:
                self.status.ok("Đã tạo lại giọng — bấm ▶ Phát (hoặc ▶ ở câu vừa sửa) để nghe lại.")
            win.queue_update(it)
            win.preview.schedule_refresh(0)

        win.run_task("Tạo lại giọng", work, done)

    # ── AI ───────────────────────────────────────────────────────────────
    def _base_border(self, i: int | None) -> str:
        return T.WARNING if i is not None and i in self._ai_marks else T.BORDER

    def _clear_ai_marks(self) -> None:
        self._ai_marks = set()
        self._ai_before = {}
        self.ai_undo.grid_remove()

    def _apply_ai(self, edits: dict[int, str], expect: list[str] | None = None) -> list[int]:
        """Điền câu AI sửa vào ô (chưa lưu), nhớ câu cũ cho ↶ / Hoàn tác. ``expect``: chữ lúc gửi AI — câu người dùng
        đã sửa tiếp trong lúc chờ thì không ghi đè."""
        self._sync()
        changed = []
        for i, t in sorted(edits.items()):
            t = (t or "").strip()
            if not (0 <= i < len(self.texts)) or not t or t == self.texts[i].strip():
                continue
            if expect is not None and (i >= len(expect) or expect[i] != self.texts[i]):
                continue
            self._ai_before.setdefault(i, self.texts[i])
            self.texts[i] = t
            self._ai_marks.add(i)
            changed.append(i)
        if self._ai_marks:
            self.ai_undo.grid()
        return changed

    def _revert_row(self, r: int) -> None:
        i = self._row_index(r)
        if i is None or i not in self._ai_before:
            return
        self._sync()
        self.texts[i] = self._ai_before.pop(i)
        self._ai_marks.discard(i)
        self._update_dirty_mark()
        if not self._ai_before:
            self.ai_undo.grid_remove()
        self.show_page(self.page, sync=False)
        self.status.info(f"Đã trả lại câu {i + 1} như trước khi AI sửa (chưa lưu).")

    def ai_edit(self) -> None:
        """Gửi yêu cầu tự do cho AI; AI trả về đúng các câu cần đổi → điền vào ô sửa, tô vàng, chưa lưu."""
        it = self.item
        req = self.ai_req.get().strip()
        if it is None or not self.cues:
            return
        if not req:
            self.status.info("Gõ yêu cầu trước, vd «câu hỏi bắt về nuôi thêm 'Con này' ở đầu», «sửa tên Lý Tiểu Long».")
            return
        from app.services.translate_router import ai_edit

        self._sync()
        current = list(self.texts)
        source = [c.plain for c in self.ref] if self.is_translation else None
        st = self.app_state.state_for(it)
        win = self.window
        self.status.warn(f"AI đang đọc {len(current)} câu để sửa theo «{req}»…")
        self.ai_send.configure(state="disabled")

        def work(cancel):
            return ai_edit(current, source, req, settings=st.settings, target=st.subtitle.translate_target,
                           style=st.subtitle.translate_style, log=win.log_bg, cancel=cancel)

        def done(result, error):
            if not self.winfo_exists():
                return
            self.ai_send.configure(state="normal")
            if self.item is not it:
                return
            if error:
                self.status.error(f"AI sửa lỗi: {friendly_error(error_line(error, 200))}")
                return
            edits, note = result
            changed = self._apply_ai(edits, current)     # câu người dùng sửa tiếp trong lúc chờ: giữ chữ của họ
            if not changed:
                self.status.info(f"AI không thấy câu nào cần sửa. {note}".strip())
                return
            self.show_page(changed[0] // self.PAGE, sync=False)
            self._update_dirty_mark()
            nums = ", ".join(f"#{i + 1}" for i in changed[:12]) + ("…" if len(changed) > 12 else "")
            self.status.ok(f"AI sửa {len(changed)} câu ({nums}) — tô vàng, chưa lưu. {note} Xem rồi bấm «Lưu & tạo lại giọng»."
                           .replace("  ", " "))
            self.ai_req.delete(0, "end")

        win.run_task("AI sửa lời thoại", work, done)

    def ai_revert(self) -> None:
        if not self._ai_before:
            return
        self._sync()
        for i, t in self._ai_before.items():
            if i < len(self.texts):
                self.texts[i] = t
        n = len(self._ai_before)
        self._clear_ai_marks()
        self.show_page(self.page, sync=False)
        self.status.info(f"Đã hoàn tác {n} câu AI đã sửa (chưa lưu).")

    def _retranslate(self, idxs: list[int]) -> None:
        it = self.item
        if it is None or not self.is_translation:
            return
        self._sync()
        previous = list(self.texts)
        pipe = self.window.pipeline_for(it)
        win = self.window
        self.status.warn(f"AI đang dịch lại {len(idxs)} câu…")

        def work(cancel):
            return pipe.retranslate(it, idxs, instruction="", previous=previous, save=False,
                                    progress=win.set_busy_progress, log=win.log_bg, cancel=cancel)

        def done(result, error):
            if not self.winfo_exists() or self.item is not it:
                return
            if error:
                self.status.error(f"Dịch lại lỗi: {friendly_error(error_line(error, 200))}")
                return
            changed = len(self._apply_ai(result, previous))
            self.show_page(self.page, sync=False)
            if changed:
                self.status.ok(f"AI đã dịch lại {changed} câu (tô vàng, chưa lưu) — xem rồi bấm «Lưu & tạo lại giọng».")
            else:
                self.status.info("AI trả về giống bản cũ — thử ô «Nhờ AI sửa» với yêu cầu cụ thể hơn.")

        win.run_task(f"Dịch lại câu {idxs[0] + 1}", work, done)

    def _ai_row(self, r: int) -> None:
        i = self._row_index(r)
        if i is not None:
            self._retranslate([i])
