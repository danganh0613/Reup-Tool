"""Tab «Duyệt» — trung tâm của bước Chuẩn bị → Duyệt → Xuất, lấy VIDEO làm trung tâm.

Một bảng tóm tắt «video này sẽ ra sao»: lời thoại (bao nhiêu câu, đã dịch chưa), phụ đề, mấy người nói & ai lồng tiếng,
âm thanh gốc, che mờ, nhạc nền, logo, cắt, hiệu ứng — mỗi dòng có «Sửa ›» mở đúng tab tính năng để chỉnh RIÊNG cho
video này (khung xem trước bên phải vẫn hiện video đó; bấm ▶ Phát để xem lại sau khi chỉnh). Ưng thì «Duyệt» (Ctrl+Enter)
→ tự sang video chờ kế; duyệt xong bấm «Xuất (n)» trên cùng.

Không có trình sửa riêng ở đây (sửa lời thoại ở tab «Lời thoại», che mờ ở «Che mờ»…) để không trùng tính năng.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.core.state import VideoItem
from app.services.srt_utils import format_ts
from app.ui.icons import icon
from app.ui.widgets.common import StatusLabel, hint
from app.ui.widgets.ui import Select, Tooltip, button
from app.ui.widgets.video_summary import OFF, WARN, summarize

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

    def __init__(self, master, window) -> None:
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.window = window
        self.app_state = window.app_state
        self.item: VideoItem | None = None
        self._selecting = False
        self._refresh_after: str | None = None
        self._row_widgets: list[ctk.CTkFrame] = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # ── Đầu: tiêu đề + đếm ───────────────────────────────────────────
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        ctk.CTkLabel(head, text="  Duyệt video", image=icon("check", 18, T.ACCENT, dy=1), compound="left",
                     font=T.font(16, "medium"), anchor="w").pack(side="left")
        button(head, "Hướng dẫn", icon_name="info", variant="ghost", size="sm", icon_color=T.TEXT_DIM,
               command=lambda: self.window.toggle_help("review")).pack(side="right")
        self.counts = ctk.CTkLabel(self, text="", font=T.font(11), text_color=T.TEXT_DIM, anchor="w")
        self.counts.grid(row=1, column=0, sticky="ew", padx=6, pady=(2, 6))

        # ── Video đang duyệt: chọn video · trạng thái · MỘT nút chính «Duyệt» ──
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
        acts.grid_columnconfigure(0, weight=3)
        acts.grid_columnconfigure(1, weight=2)
        self.approve_btn = button(acts, "Duyệt", icon_name="check", variant="primary", size="md", command=self._approve,
                                  tip="Video này đạt — sẽ được xuất; tự sang video chờ duyệt kế (Ctrl+Enter)")
        self.approve_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.skip_btn = button(acts, "Bỏ qua", size="md", command=self._skip, tip="Không xuất video này")
        self.skip_btn.grid(row=0, column=1, sticky="ew", padx=4)
        self.more_btn = button(acts, "", icon_name="list", variant="ghost", size="md", command=self._more,
                               tip="Thêm: chuẩn bị lại video này · duyệt hết video đang chờ")
        self.more_btn.grid(row=0, column=2, padx=(4, 0))

        # ── Video này sẽ ra sao: mỗi dòng một tính năng, «Sửa ›» mở đúng tab ──
        sub = ctk.CTkFrame(self, fg_color="transparent")
        sub.grid(row=3, column=0, sticky="ew", padx=4, pady=(12, 4))
        ctk.CTkLabel(sub, text="Video này sẽ ra sao", font=T.font(13, "bold"), anchor="w").pack(side="left")
        ctk.CTkLabel(sub, text="  chỗ nào chưa ổn → «Sửa ›»", font=T.font(11), text_color=T.TEXT_MUTED,
                     anchor="w").pack(side="left")
        self.rows = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.rows.grid(row=4, column=0, sticky="nsew", padx=0)
        self.rows.grid_columnconfigure(0, weight=1)
        self.status = StatusLabel(self, "")
        self.status.grid(row=5, column=0, sticky="ew", padx=4, pady=(6, 0))
        hint(self, "Thấy lỗi → «Sửa ›» → chỉnh → «Duyệt» (Ctrl+Enter). Duyệt xong bấm «Xuất» trên cùng.").grid(
            row=6, column=0, sticky="ew", padx=4, pady=(2, 6))
        self.app_state.on("videos_changed", self.reload)
        self.app_state.on("config_changed", self._config_changed)
        self.reload()

    def destroy(self) -> None:
        self.app_state.off("videos_changed", self.reload)
        self.app_state.off("config_changed", self._config_changed)
        super().destroy()

    # ── Giao diện module ─────────────────────────────────────────────────
    def refresh(self) -> None:
        self._update_counts()
        self._fill_summary()

    def on_shown(self) -> None:
        self.reload()

    def on_hidden(self) -> None:
        pass

    def scroll_top(self) -> None:
        try:
            self.rows._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    def on_active_changed(self) -> None:
        """Chọn video khác ở hàng đợi / nơi khác → bảng Duyệt theo video đó."""
        if not self._selecting and self.app_state.active is not self.item:
            self._show_item(self.app_state.active)

    def _config_changed(self, _section: str = "") -> None:
        if self._refresh_after is None and self.winfo_exists():
            self._refresh_after = self.after(400, self._deferred_refresh)

    def _deferred_refresh(self) -> None:
        self._refresh_after = None
        if self.winfo_exists() and self.winfo_ismapped():
            self._fill_summary()

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
            self.select(vids.index(target))
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
        self.picker.set(str(vids.index(self.item)) if self.item in vids else keys[0])

    def _picked(self, key: str) -> None:
        if key.isdigit():
            self.select(int(key))

    def select(self, index: int) -> None:
        vids = self.app_state.videos
        if not (0 <= index < len(vids)):
            return
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
        self._update_counts()
        state = "normal" if it is not None else "disabled"
        for b in (self.approve_btn, self.skip_btn, self.more_btn):
            b.configure(state=state)
        if it is None:
            self.info_lbl.configure(text="Nạp video vào hàng đợi rồi bấm «Chuẩn bị» trên cùng.", text_color=T.TEXT_DIM)
            self._fill_summary()
            return
        sym, label, color = _mark(it)
        bits = [f"{sym} {label}"]
        if it.duration:
            bits.append(format_ts(it.duration, ".")[:8])
        if it.config is not None:
            bits.append("✎ chỉnh riêng" + (f" · mẫu «{it.template}»" if it.template else ""))
        elif self.app_state.template_name:
            bits.append(f"mẫu «{self.app_state.template_name}»")
        if it.status == "Lỗi" and it.message:
            bits.append("Lỗi: " + it.message)
        self.info_lbl.configure(text="  ·  ".join(bits), text_color=color if it.status == "Lỗi" else T.TEXT_DIM)
        self._fill_summary()

    def _fill_summary(self) -> None:
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets = []
        it = self.item
        if it is None:
            return
        try:
            rows = summarize(self.window, it)
        except Exception as exc:                     # tóm tắt lỗi không được làm hỏng cả tab
            self.status.error(f"Không đọc được cấu hình video: {exc}")
            return
        for i, r in enumerate(rows):
            self._row_widgets.append(self._row(i, r))
        warns = sum(1 for r in rows if r.level == WARN)
        if warns:
            self.status.warn(f"⚠ {warns} mục cần xem lại (tô vàng) — bấm «Sửa ›» để chỉnh.")
        else:
            self.status.info("")

    def _row(self, i: int, r) -> ctk.CTkFrame:
        off, warn = r.level == OFF, r.level == WARN
        fr = ctk.CTkFrame(self.rows, fg_color=T.BG_CARD, corner_radius=8,
                          border_width=1 if warn else 0, border_color=T.WARNING)
        fr.grid(row=i, column=0, sticky="ew", padx=2, pady=3)
        fr.grid_columnconfigure(1, weight=1)
        col = T.TEXT_MUTED if off else (T.WARNING if warn else T.ACCENT)
        ctk.CTkLabel(fr, text="", image=icon(r.icon, 18, col), width=28).grid(row=0, column=0, rowspan=3, padx=(10, 4),
                                                                              pady=8, sticky="n")
        ctk.CTkLabel(fr, text=r.title, font=T.font(12, "bold"), anchor="w",
                     text_color=T.TEXT_DIM if off else T.TEXT).grid(row=0, column=1, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(fr, text=("⚠ " if warn else "") + r.value, font=T.font(12), anchor="w", justify="left",
                     wraplength=300, text_color=T.WARNING if warn else (T.TEXT_MUTED if off else T.TEXT_DIM)).grid(
            row=1, column=1, sticky="ew", pady=(0, 0 if r.detail else 8))
        if r.detail:
            ctk.CTkLabel(fr, text=r.detail, font=T.font(11), anchor="w", justify="left", wraplength=300,
                         text_color=T.TEXT_MUTED).grid(row=2, column=1, sticky="ew", pady=(0, 8))
        go = button(fr, "Sửa ›", variant="ghost", size="sm", command=lambda m=r.module: self.window.edit_in(m),
                    tip=f"Mở tab chỉnh «{r.title}» cho riêng video này")
        go.grid(row=0, column=2, rowspan=3, padx=(4, 8))
        for w in (fr, *fr.winfo_children()):
            if w is not go:
                w.bind("<Button-1>", lambda _e, m=r.module: self.window.edit_in(m))
        return fr

    # ── Hành động ────────────────────────────────────────────────────────
    def _more(self) -> None:
        m = tk.Menu(self, tearoff=0, bg=T.BG_ELEVATED, fg=T.TEXT, activebackground=T.BG_HOVER,
                    activeforeground=T.TEXT, bd=0)
        m.add_command(label="Chuẩn bị lại video này (làm lại phần còn thiếu)", command=self._reprepare)
        m.add_command(label="Duyệt hết các video đang chờ", command=self._approve_all)
        b = self.more_btn
        try:
            m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
        finally:
            m.grab_release()

    def _reprepare(self) -> None:
        it = self.item
        if it is None:
            return
        pipe = self.window.pipeline_for(it)
        win = self.window
        self.status.warn("Đang chuẩn bị lại… (xem log)")

        def work(cancel):
            pipe.prepare(it, on_update=lambda x: win.ui_call(lambda: win.queue_update(x)), log=win.log_bg, cancel=cancel)

        def done(_r, error):
            if not self.winfo_exists():
                return
            if error:
                it.status, it.message = "Lỗi", str(error).splitlines()[0][:160]
                self.status.error(f"Lỗi: {error}")
            else:
                self.status.ok("Chuẩn bị xong — bấm ▶ Phát để xem, ưng thì Duyệt.")
            win.queue_update(it)
            if it is self.item:
                self._show_item(it)
                win.preview.schedule_refresh(0)

        win.run_task("Chuẩn bị", work, done)

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
        ready = sum(1 for it in vids if it.review == "approved" and it.status != "Xong")
        self.status.ok(f"Đã duyệt hết — bấm «Xuất ({ready})» trên cùng để render." if ready else "Đã duyệt hết.")

    def approve(self) -> None:
        """Ctrl+Enter ở bất kỳ tab nào khi đang duyệt."""
        self._approve()

    def _approve(self) -> None:
        it = self.item
        if it is None:
            return
        self._set_review(it, "approved")
        m = self.window.modules.get("srt")
        if m is not None and getattr(m, "editor", None) is not None and m.editor.item is it and m.editor.dirty():
            m.editor.save()                      # còn câu lời thoại sửa dở → lưu (và tạo lại giọng, giữ «Đã duyệt»)
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
        self._next_pending()
