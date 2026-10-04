"""Màn hình duyệt (bước 2 của Chuẩn bị → Duyệt → Xuất).

Trái: danh sách video + trạng thái. Phải: phụ đề / bản dịch của video đang chọn (sửa câu sai → lưu → máy tạo lại
giọng, câu không đổi lấy từ cache nên rất nhanh), nút phát trên khung xem trước chính / render thử, Duyệt hoặc Bỏ qua.
Chọn video ở đây = chọn video đó trên cửa sổ chính, nên khung xem trước luôn hiện đúng video đang duyệt.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.core.state import VideoItem
from app.services.srt_utils import format_ts, load_srt, save_srt, wrap_lines
from app.services.translate_router import RETRANSLATE_PRESETS
from app.ui.widgets.common import StatusLabel, hint
from app.ui.widgets.ui import Tooltip, button

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


class ReviewWindow(ctk.CTkToplevel):
    PAGE = 40
    _is_translation = False             # đang sửa bản dịch (có câu gốc tương ứng) → hiện «AI dịch lại»

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.app_state = window.app_state
        self.title("Duyệt video trước khi xuất")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1180, sw - 60)}x{min(780, sh - 100)}+40+40")
        self.after(50, self.lift)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.item: VideoItem | None = None
        self.cues: list = []
        self.ref: list = []
        self.texts: list[str] = []
        self.edit_path: str | None = None
        self.page = 0

        # ── Trái: danh sách video ────────────────────────────────────────
        left = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=10, width=310)
        left.grid(row=0, column=0, sticky="nsw", padx=(10, 4), pady=10)
        left.grid_propagate(False)
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="Video cần duyệt", font=T.font(14, "bold"), anchor="w").grid(row=0, column=0, sticky="ew",
                                                                                           padx=12, pady=(12, 0))
        self.counts = ctk.CTkLabel(left, text="", font=T.font(11), text_color=T.TEXT_DIM, anchor="w")
        self.counts.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.list = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list.grid(row=2, column=0, sticky="nsew", padx=6)
        self.list.grid_columnconfigure(0, weight=1)
        lf = ctk.CTkFrame(left, fg_color="transparent")
        lf.grid(row=3, column=0, sticky="ew", padx=10, pady=10)
        self.export_btn = button(lf, "Xuất video đã duyệt", icon_name="play", variant="primary", command=self._export,
                                 tip="Render các video đã duyệt (theo mẫu / cấu hình riêng của từng video)")
        self.export_btn.pack(fill="x")
        button(lf, "Duyệt tất cả còn lại", icon_name="check", size="sm", command=self._approve_all).pack(fill="x", pady=(6, 0))

        # ── Phải: chi tiết ───────────────────────────────────────────────
        right = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 10), pady=10)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)
        top = ctk.CTkFrame(right, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        self.title_lbl = ctk.CTkLabel(top, text="", font=T.font(15, "bold"), anchor="w")
        self.title_lbl.pack(side="left")
        self.state_lbl = ctk.CTkLabel(top, text="", font=T.font(12, "bold"), anchor="e")
        self.state_lbl.pack(side="right")
        self.info_lbl = ctk.CTkLabel(right, text="", font=T.font(11), text_color=T.TEXT_DIM, anchor="w", justify="left")
        self.info_lbl.grid(row=1, column=0, sticky="ew", padx=14)
        acts = ctk.CTkFrame(right, fg_color="transparent")
        acts.grid(row=2, column=0, sticky="ew", padx=14, pady=(8, 4))
        button(acts, "Phát trên khung chính", icon_name="play", size="sm", command=self._play,
               tip="Phát video này ở khung xem trước của cửa sổ chính (đủ hiệu ứng, phụ đề, giọng lồng)").pack(side="left")
        button(acts, "Render thử 15 giây", icon_name="film", size="sm", command=self._clip,
               tip="Render thật 15 giây từ vị trí đang xem trên cửa sổ chính rồi phát").pack(side="left", padx=6)
        self.prep_btn = button(acts, "Chuẩn bị lại", icon_name="refresh", size="sm", command=self._reprepare,
                               tip="Làm lại phụ đề / dịch / giọng cho video này (giữ file đã có, chỉ làm phần còn thiếu)")
        self.prep_btn.pack(side="left")
        self.sub_head = ctk.CTkLabel(right, text="", font=T.font(12, "medium"), text_color=T.TEXT_DIM, anchor="w")
        self.sub_head.grid(row=3, column=0, sticky="ew", padx=14, pady=(8, 2))
        self.rows_frame = ctk.CTkScrollableFrame(right, fg_color=T.BG_INPUT, corner_radius=8)
        self.rows_frame.grid(row=4, column=0, sticky="nsew", padx=14)
        self.rows_frame.grid_columnconfigure(2, weight=1)
        self._rows: list[dict] = []
        for r in range(self.PAGE):
            idx = ctk.CTkLabel(self.rows_frame, text="", font=T.mono(10), text_color=T.TEXT_MUTED, width=70, anchor="w",
                               justify="left")
            ref = ctk.CTkLabel(self.rows_frame, text="", font=T.font(10), text_color=T.TEXT_MUTED, anchor="w",
                               justify="left", wraplength=260)
            ent = ctk.CTkEntry(self.rows_frame, height=30, font=T.font(12))
            redo = ctk.CTkButton(self.rows_frame, text="↻", width=30, height=30, command=lambda r=r: self._ai_row(r),
                                 **{**T.secondary_button(), "font": T.font(13, "bold")})
            Tooltip(redo, "AI dịch lại riêng câu này theo «Yêu cầu» bên dưới (xem câu trước/sau làm ngữ cảnh)")
            self._rows.append({"idx": idx, "ref": ref, "entry": ent, "redo": redo,
                               "widgets": [(idx, dict(row=r, column=0, sticky="nw", padx=(4, 6), pady=2)),
                                           (ref, dict(row=r, column=1, sticky="nw", padx=(0, 6), pady=2)),
                                           (ent, dict(row=r, column=2, sticky="ew", padx=(0, 4), pady=2)),
                                           (redo, dict(row=r, column=3, sticky="n", padx=(0, 4), pady=2))]})
        # AI dịch lại: bản dịch chưa hay → ghi yêu cầu, dịch lại cả video hoặc từng câu (↻); kết quả vào ô sửa,
        # xem rồi bấm «Lưu sửa & tạo lại giọng»
        self.ai_bar = ctk.CTkFrame(right, fg_color="transparent")
        self.ai_bar.grid(row=5, column=0, sticky="ew", padx=14, pady=(6, 0))
        ctk.CTkLabel(self.ai_bar, text="Yêu cầu AI", font=T.font(11), text_color=T.TEXT_DIM).pack(side="left")
        self.ai_req = ctk.CTkComboBox(self.ai_bar, values=RETRANSLATE_PRESETS, height=30, font=T.font(12))
        self.ai_req.set("")
        self.ai_req.pack(side="left", fill="x", expand=True, padx=8)
        self.ai_all_btn = button(self.ai_bar, "AI dịch lại cả video", icon_name="sparkle", size="sm", command=self._ai_all,
                                 tip="Dịch lại toàn bộ theo yêu cầu (AI xem bản dịch hiện tại để biết bạn chưa ưng chỗ "
                                     "nào). Kết quả hiện trong ô sửa — xem rồi bấm Lưu.")
        self.ai_all_btn.pack(side="right")
        nav = ctk.CTkFrame(right, fg_color="transparent")
        nav.grid(row=6, column=0, sticky="ew", padx=14, pady=(6, 0))
        self.prev_btn = button(nav, "‹ Trước", size="sm", command=lambda: self._show_page(self.page - 1))
        self.prev_btn.pack(side="left")
        self.page_lbl = ctk.CTkLabel(nav, text="", font=T.font(11), width=150)
        self.page_lbl.pack(side="left", padx=6)
        self.next_btn = button(nav, "Sau ›", size="sm", command=lambda: self._show_page(self.page + 1))
        self.next_btn.pack(side="left")
        self.save_btn = button(nav, "Lưu sửa & tạo lại giọng", icon_name="save", size="sm", command=self._save_subs,
                               tip="Lưu các câu đã sửa; nếu video có lồng tiếng thì tạo lại giọng (câu không đổi dùng lại)")
        self.save_btn.pack(side="right")
        self.status = StatusLabel(right, "")
        self.status.grid(row=7, column=0, sticky="ew", padx=14, pady=(6, 0))
        foot = ctk.CTkFrame(right, fg_color="transparent")
        foot.grid(row=8, column=0, sticky="ew", padx=14, pady=12)
        hint(foot, "Ctrl+Enter: duyệt & sang video kế · Ctrl+↓ / Ctrl+↑: video kế / trước").pack(side="left")
        button(foot, "Duyệt", icon_name="check", variant="primary", command=self._approve,
               tip="Đánh dấu video này đạt — sẽ được xuất (Ctrl+Enter)").pack(side="right")
        button(foot, "Bỏ qua", size="md", command=self._skip, tip="Không xuất video này").pack(side="right", padx=8)
        self.bind("<Control-Return>", lambda _e: self._approve())
        self.bind("<Control-Down>", lambda _e: self._step(1))
        self.bind("<Control-Up>", lambda _e: self._step(-1))
        self.app_state.on("videos_changed", self.reload)
        self.reload()

    def destroy(self) -> None:
        self.app_state.off("videos_changed", self.reload)
        super().destroy()

    # ── Danh sách ────────────────────────────────────────────────────────
    def reload(self, *_a) -> None:
        if not self.winfo_exists():
            return
        for w in self.list.winfo_children():
            w.destroy()
        vids = self.app_state.videos
        self._btns: dict[int, ctk.CTkButton] = {}
        for i, it in enumerate(vids):
            sym, label, color = _mark(it)
            b = ctk.CTkButton(self.list, text=f"{sym}  {it.name}\n     {label}", anchor="w", height=44, corner_radius=8,
                              font=T.font(12), fg_color=T.BG_INPUT, hover_color=T.BG_ELEVATED, text_color=color,
                              command=lambda i=i: self.select(i))
            b.grid(row=i, column=0, sticky="ew", pady=2)
            self._btns[i] = b
        self._update_counts()
        target = self.app_state.active
        if self.item is not None and self.item in vids:
            target = self.item
        elif target is None or target.review not in ("pending", ""):
            target = next((it for it in vids if it.review == "pending"), target)
        if target is not None and target in vids:
            self.select(vids.index(target))
        else:
            self._show_item(None)

    def _update_counts(self) -> None:
        vids = self.app_state.videos
        c = {k: sum(1 for it in vids if it.review == k) for k in ("", "pending", "approved", "skipped")}
        todo_export = sum(1 for it in vids if it.review == "approved" and it.status != "Xong")
        self.counts.configure(text=f"Chờ duyệt {c['pending']} · Đã duyệt {c['approved']} · Bỏ qua {c['skipped']}"
                                   + (f" · Chưa chuẩn bị {c['']}" if c[""] else ""))
        self.export_btn.configure(text=f"Xuất {todo_export} video đã duyệt" if todo_export else "Xuất video đã duyệt",
                                  state="normal" if todo_export else "disabled")
        for i, it in enumerate(vids):
            b = self._btns.get(i)
            if b is not None:
                sym, label, color = _mark(it)
                b.configure(text=f"{sym}  {it.name}\n     {label}", text_color=color,
                            border_width=2 if it is self.item else 0, border_color=T.ACCENT)

    def select(self, index: int) -> None:
        vids = self.app_state.videos
        if not (0 <= index < len(vids)):
            return
        if self.item is not None and self._dirty():
            if messagebox.askyesno(APP, "Lưu các câu phụ đề vừa sửa?", parent=self):
                self._save_subs()
        self.app_state.set_active(index)             # khung xem trước chính hiện video này
        self._show_item(vids[index])

    def _step(self, d: int) -> None:
        vids = self.app_state.videos
        if self.item in vids:
            self.select(max(0, min(len(vids) - 1, vids.index(self.item) + d)))

    # ── Chi tiết ─────────────────────────────────────────────────────────
    def _show_item(self, it: VideoItem | None) -> None:
        self.item = it
        self._update_counts()
        if it is None:
            self.title_lbl.configure(text="Chưa có video")
            self.info_lbl.configure(text="Nạp video vào hàng đợi rồi bấm «Chuẩn bị».")
            self.state_lbl.configure(text="")
            self.cues, self.texts = [], []
            self._is_translation = False
            self._show_page(0)
            return
        sym, label, color = _mark(it)
        self.title_lbl.configure(text=it.name)
        self.state_lbl.configure(text=f"{sym} {label}", text_color=color)
        bits = []
        if it.duration:
            bits.append(format_ts(it.duration, ".")[:8])
        if it.config is not None:
            bits.append("cấu hình riêng" + (f" · mẫu «{it.template}»" if it.template else ""))
        elif self.app_state.template_name:
            bits.append(f"mẫu «{self.app_state.template_name}»")
        bits.append(("Giọng: " + Path(it.voice_path).name) if it.voice_path else "chưa có giọng lồng")
        if it.status == "Lỗi" and it.message:
            bits.append("Lỗi: " + it.message)
        self.info_lbl.configure(text="  ·  ".join(bits))
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
            self.sub_head.configure(text=f"Bản dịch ({len(self.cues)} câu) — sửa câu dịch sai; chữ nhỏ là câu gốc")
        elif orig:
            self.edit_path, self.cues = orig, load_srt(orig)
            self.sub_head.configure(text=f"Phụ đề ({len(self.cues)} câu) — sửa câu nhận diện sai")
        else:
            self.sub_head.configure(text="Video này chưa có phụ đề (mẫu không dùng phụ đề / lồng tiếng, hoặc chưa chuẩn bị).")
        self.texts = [c.text.replace("\n", " ") for c in self.cues]
        self._orig_texts = list(self.texts)
        self._show_page(0, sync=False)

    def _range(self) -> range:
        a = self.page * self.PAGE
        return range(a, min(a + self.PAGE, len(self.cues)))

    def _sync(self) -> None:
        for row, i in zip(self._rows, self._range()):
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
                row["idx"].configure(text=f"{i + 1}\n{format_ts(c.start, '.')[:8]}")
                ref = self.ref[i].plain if i < len(self.ref) else ""
                row["ref"].configure(text=ref)
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
    def _retranslate(self, idxs: list[int] | None, label: str) -> None:
        from app.services.job_runner import Pipeline

        it = self.item
        if it is None or not self._is_translation:
            return
        self._sync()
        req = self.ai_req.get().strip()
        previous = list(self.texts)
        st = self.app_state.state_for(it)
        win = self.window
        n = len(self.cues) if idxs is None else len(idxs)
        self.status.warn(f"AI đang dịch lại {n} câu" + (f" — yêu cầu: «{req}»" if req else "") + "…")
        self.ai_all_btn.configure(state="disabled")

        def work(cancel):
            return Pipeline(st, win.ffmpeg).retranslate(it, idxs, instruction=req, previous=previous, save=False,
                                                         progress=win.set_busy_progress, log=win.log_bg, cancel=cancel)

        def done(result, error):
            if not self.winfo_exists():
                return
            self.ai_all_btn.configure(state="normal")
            if self.item is not it:
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
                               "«Lưu sửa & tạo lại giọng».")
            else:
                self.status.info("AI trả về giống bản cũ — thử ghi yêu cầu cụ thể hơn (vd: «xưng anh–em, bỏ từ thừa»).")

        win.run_task(label, work, done)

    def _ai_row(self, r: int) -> None:
        idxs = self._range()
        if r < len(idxs):
            self._retranslate([idxs[r]], f"Dịch lại câu {idxs[r] + 1}")

    def _ai_all(self) -> None:
        if self.cues:
            self._retranslate(None, "AI dịch lại phụ đề")

    # ── Hành động ────────────────────────────────────────────────────────
    def _prepare_one(self, it: VideoItem, label: str = "Chuẩn bị") -> None:
        from app.services.job_runner import Pipeline

        st = self.app_state.state_for(it)
        win = self.window

        def work(cancel):
            pipe = Pipeline(st, win.ffmpeg)
            pipe.prepare(it, on_update=lambda x: win.ui_call(lambda: win.queue_update(x)), log=win.log_bg, cancel=cancel)

        def done(_r, error):
            if not self.winfo_exists():
                return
            if error:
                it.status, it.message = "Lỗi", str(error).splitlines()[0][:160]
                self.status.error(f"Lỗi: {error}")
            else:
                self.status.ok(f"{label} xong — xem lại rồi bấm Duyệt.")
            win.queue_update(it)
            self._update_counts()
            if it is self.item:
                self._show_item(it)

        win.run_task(label, work, done)

    def _reprepare(self) -> None:
        if self.item is not None:
            self.status.warn("Đang chuẩn bị… (xem log)")
            self._prepare_one(self.item)

    def _play(self) -> None:
        if self.item is not None:
            self.window.preview.start_play()

    def _clip(self) -> None:
        if self.item is not None:
            self.window.preview.render_clip()

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
                self.select(k)
                return
        self._show_item(self.item)
        self.status.ok("Đã duyệt hết — bấm «Xuất video đã duyệt».")

    def _approve(self) -> None:
        it = self.item
        if it is None:
            return
        if not it.review:
            self.status.warn("Video này chưa chuẩn bị — bấm «Chuẩn bị» trước (hoặc vẫn duyệt nếu mẫu không cần phụ đề/giọng).")
        if self._dirty():
            self._save_subs()
        self._set_review(it, "approved")
        self._next_pending()

    def _skip(self) -> None:
        if self.item is not None:
            self._set_review(self.item, "skipped")
            self._next_pending()

    def _approve_all(self) -> None:
        pend = [it for it in self.app_state.videos if it.review == "pending"]
        if not pend:
            return
        if not messagebox.askyesno(APP, f"Duyệt luôn {len(pend)} video đang chờ (không xem từng video)?", parent=self):
            return
        for it in pend:
            self._set_review(it, "approved")
        self._show_item(self.item)

    def _export(self) -> None:
        ready = [it for it in self.app_state.videos if it.review == "approved" and it.status != "Xong"]
        if not ready:
            return
        if self._dirty():
            self._save_subs()
        self.withdraw()
        self.window.start_render(ready, force=True)
