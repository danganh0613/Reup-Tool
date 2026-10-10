"""Sửa lời thoại của video đang chọn — danh sách câu gắn với khung xem trước (dùng trong tab «Lời thoại»).

Bấm vào ô câu → khung xem trước nhảy tới cảnh câu đó · ▶ phát đúng câu đó (có giọng lồng) rồi tự dừng · đang phát thì
câu đang đọc được tô sáng · ↻ AI dịch lại một câu · «AI rà soát» (AI đọc lại cả bản dịch, sửa câu chưa ổn kèm lý do — dịch
xong cũng tự chạy, xem Cài đặt) · ô «Nhờ AI sửa» (gõ yêu cầu, AI chỉ sửa đúng các câu liên quan). Câu AI sửa tô vàng, ↶ trả
lại từng câu, «Hoàn tác» trả lại hết · «Lưu & tạo lại giọng» (câu không đổi dùng lại giọng cũ nên rất nhanh).

Danh sách chia trang (không có thanh cuộn riêng) để nằm gọn trong bảng tính năng vốn đã cuộn được.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk

from app.config import theme as T
from app.core.state import VideoItem
from app.services.srt_utils import format_ts, load_srt, save_srt, wrap_lines
from app.ui.widgets.common import StatusLabel
from app.ui.widgets.ui import Tooltip, button


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
        self._ai_notes: dict[int, str] = {}       # lý do AI sửa (rà soát)
        self.grid_columnconfigure(0, weight=1)

        self.head = ctk.CTkLabel(self, text="", font=T.font(12, "medium"), text_color=T.TEXT_DIM, anchor="w",
                                 justify="left", wraplength=400)
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
            ref = ctk.CTkLabel(top, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w", justify="left",
                               wraplength=300)
            ref.pack(side="left", fill="x", expand=True, padx=(4, 0))
            ent = ctk.CTkEntry(self.rows_frame, height=30, font=T.font(12))
            ent.bind("<FocusIn>", lambda _e, r=r: self._focus_row(r))
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
            Tooltip(play, "Phát đúng câu này trên khung xem trước (có giọng lồng)")
            Tooltip(redo, "AI dịch lại riêng câu này (theo «Phong cách dịch», xem câu trước/sau làm ngữ cảnh)")
            Tooltip(back, "Trả lại câu này như trước khi AI sửa")
            self._rows.append({"play": play, "ref": ref, "entry": ent, "redo": redo, "back": back, "top": top,
                               "widgets": [(top, dict(row=2 * r, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))),
                                           (ent, dict(row=2 * r + 1, column=0, sticky="ew", padx=(4, 2), pady=(0, 2))),
                                           (redo, dict(row=2 * r + 1, column=1, padx=(2, 2), pady=(0, 2))),
                                           (back, dict(row=2 * r + 1, column=2, padx=(0, 4), pady=(0, 2)))]})

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
        self.review_btn = button(nav, "AI rà soát", icon_name="sparkle", size="sm", command=self.ai_review,
                                 tip="AI đọc lại cả bản dịch, sửa câu dịch sai nghĩa / cứng / lệch xưng hô / dài quá khung. "
                                     "Câu sửa tô vàng kèm lý do, chưa lưu — ↶ trả lại từng câu.")
        self.review_btn.pack(side="right", padx=(0, 6))

        # ── Nhờ AI sửa (kiểu chat): AI tự tìm câu liên quan tới yêu cầu và chỉ sửa đúng những câu đó ──
        ai = ctk.CTkFrame(self, fg_color="transparent")
        ai.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ai.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(ai, text="Nhờ AI sửa — AI tự tìm đúng câu cần sửa, câu khác giữ nguyên", font=T.font(11),
                     text_color=T.TEXT_DIM, anchor="w").grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 2))
        self.ai_req = ctk.CTkEntry(ai, height=32, font=T.font(12),
                                   placeholder_text="vd: câu hỏi «bắt về nuôi» thêm «Con này» ở đầu")
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
    def load(self, it: VideoItem | None) -> None:
        self.item = it
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
                self.head.configure(text="Video này chưa có lời thoại — nhận diện ở tab STT, chọn file SRT bên dưới, "
                                         "hoặc bấm «Chuẩn bị».")
        self._starts = [c.start for c in self.cues]
        self.texts = [c.text.replace("\n", " ") for c in self.cues]
        self._orig_texts = list(self.texts)
        n = self._load_review()
        self.show_page(min(self._ai_marks) // self.PAGE if n else 0, sync=False)
        if n:
            self.status.warn(f"AI rà soát bản dịch đã sửa {n} câu (tô vàng, kèm lý do) — ↶ trả lại câu không ưng, "
                             "bấm «Lưu & tạo lại giọng» để chấp nhận.")
        else:
            self.status.info("")

    def _load_review(self) -> int:
        """Câu AI rà soát đã sửa lúc dịch (``<srt>.review.json``) mà chữ hiện tại vẫn là bản AI sửa → tô vàng + lý do."""
        from app.services.translate_router import load_review

        if not self.is_translation:
            return 0
        for x in load_review(self.edit_path):
            i = x["i"]
            if i < len(self.texts) and " ".join(self.texts[i].split()) == x.get("new") and x.get("old"):
                self._ai_before[i] = str(x["old"])
                self._ai_marks.add(i)
                self._ai_notes[i] = str(x.get("why") or "")
        if self._ai_marks:
            self.ai_undo.grid()
        return len(self._ai_marks)

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
                row["play"].configure(text=f"▶ {i + 1} · {format_ts(c.start, '.')[3:8]}", text_color=T.TEXT_MUTED)
                self._paint_ref(row["ref"], i)
                row["entry"].delete(0, "end")
                row["entry"].insert(0, self.texts[i])
                row["entry"].configure(border_color=self._base_border(i))
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
        self.ai_send.configure(state="normal" if self.cues else "disabled")
        self.review_btn.configure(state="normal" if self.cues else "disabled")

    def _paint_ref(self, label: ctk.CTkLabel, i: int) -> None:
        """Dòng chữ nhỏ trên ô sửa: câu gốc; câu AI đã sửa thì thêm lý do + câu cũ (tô vàng)."""
        base = self.ref[i].plain if i < len(self.ref) else ""
        if i not in self._ai_before:
            label.configure(text=base, text_color=T.TEXT_MUTED)
            return
        why = self._ai_notes.get(i, "")
        old = self._ai_before[i]
        old = old if len(old) <= 90 else old[:89] + "…"
        note = f"✎ {why} — trước: «{old}»" if why else f"✎ trước: «{old}»"
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

    def save(self) -> None:
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
            if self._ai_marks:                   # câu AI rà soát lúc dịch (đã nằm trong SRT) → chấp nhận, bỏ tô vàng
                n = len(self._ai_marks)
                self._accept_ai()
                self.status.ok(f"Đã chấp nhận {n} câu AI sửa.")
            else:
                self.status.info("Không có câu nào thay đổi.")
            return
        save_srt(self.cues, self.edit_path)
        self._orig_texts = list(self.texts)
        if self._ai_marks or self._ai_before:
            self._accept_ai()
        self.window.log(f"{it.name}: đã sửa {changed} câu lời thoại.")
        st = self.app_state.state_for(it)
        if st.tts.enable_tts:
            self.status.warn(f"Đã lưu {changed} câu — đang tạo lại giọng (câu không đổi dùng lại)…")
            self._regenerate(it)
        else:
            self.status.ok(f"Đã lưu {changed} câu — bấm ▶ Phát để xem lại.")
        self.window.preview.schedule_refresh(0)
        if self.on_saved:
            self.on_saved(it)

    def _regenerate(self, it: VideoItem) -> None:
        pipe = self.window.pipeline_for(it)
        win = self.window

        keep = (it.review, it.status)            # tạo lại giọng không được làm mất trạng thái duyệt (vd vừa bấm Duyệt)

        def work(cancel):
            pipe.prepare(it, on_update=lambda x: win.ui_call(lambda: win.queue_update(x)), log=win.log_bg, cancel=cancel)
            if keep[0] in ("approved", "skipped"):
                it.review, it.status = keep

        def done(_r, error):
            if not self.winfo_exists():
                return
            if error:
                self.status.error(f"Tạo lại giọng lỗi: {str(error).splitlines()[0][:200]}")
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
        self._ai_notes = {}
        self.ai_undo.grid_remove()

    def _accept_ai(self) -> None:
        """Lưu xong / chấp nhận: bỏ tô vàng, xoá danh sách câu rà soát kèm SRT (mở lại không tô nữa)."""
        from app.services.translate_router import load_review, save_review

        self._clear_ai_marks()
        if self.edit_path and load_review(self.edit_path):
            save_review(self.edit_path, [])
        self.show_page(self.page, sync=False)

    def _apply_ai(self, edits: dict[int, str], notes: dict[int, str] | None = None,
                  expect: list[str] | None = None) -> list[int]:
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
            if notes and notes.get(i):
                self._ai_notes[i] = notes[i]
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
        self._ai_notes.pop(i, None)
        if not self._ai_before:
            self.ai_undo.grid_remove()
        self.show_page(self.page, sync=False)
        self.status.info(f"Đã trả lại câu {i + 1} như trước khi AI sửa (chưa lưu).")

    def ai_review(self) -> None:
        """AI đọc lại cả bản dịch (kèm câu gốc + thời lượng), chỉ sửa câu chưa ổn — tô vàng, kèm lý do, chưa lưu."""
        it = self.item
        if it is None or not self.cues:
            return
        from app.services.translate_router import review_translation

        self._sync()
        current = list(self.texts)
        source = [c.plain for c in self.ref] if self.is_translation else []
        durations = [c.end - c.start for c in self.cues]
        st = self.app_state.state_for(it)
        win = self.window
        self.status.warn(f"AI đang rà soát {len(current)} câu (sai nghĩa, câu cứng, xưng hô, dài quá khung)…")
        self.review_btn.configure(state="disabled")

        def work(cancel):
            return review_translation(source, current, durations, settings=st.settings,
                                      target=st.subtitle.translate_target, style=st.subtitle.translate_style,
                                      log=win.log_bg, cancel=cancel)

        def done(result, error):
            if not self.winfo_exists():
                return
            self.review_btn.configure(state="normal")
            if self.item is not it:
                return
            if error:
                self.status.error(f"AI rà soát lỗi: {str(error).splitlines()[0][:200]}")
                return
            items, note = result
            changed = self._apply_ai({x["i"]: x["new"] for x in items}, {x["i"]: x["why"] for x in items}, current)
            if not changed:
                self.status.ok(f"AI rà soát: bản dịch ổn, không câu nào cần sửa. {note}".strip())
                return
            self.show_page(changed[0] // self.PAGE, sync=False)
            self.status.ok(f"AI rà soát sửa {len(changed)} câu (tô vàng, kèm lý do, chưa lưu) — ↶ trả lại câu không ưng "
                           f"rồi bấm «Lưu & tạo lại giọng». {note}".strip())

        win.run_task("AI rà soát bản dịch", work, done)

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
                self.status.error(f"AI sửa lỗi: {str(error).splitlines()[0][:200]}")
                return
            edits, note = result
            changed = self._apply_ai(edits)
            if not changed:
                self.status.info(f"AI không thấy câu nào cần sửa. {note}".strip())
                return
            self.show_page(changed[0] // self.PAGE, sync=False)
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
                self.status.error(f"Dịch lại lỗi: {str(error).splitlines()[0][:200]}")
                return
            changed = len(self._apply_ai(result))
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
