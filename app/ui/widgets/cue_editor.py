"""Sửa lời thoại của video đang chọn — danh sách câu gắn với khung xem trước (dùng trong tab «Lời thoại»).

Bấm vào ô câu → khung xem trước nhảy tới cảnh câu đó · ▶ phát đúng câu đó (có giọng lồng) rồi tự dừng · đang phát thì
câu đang đọc được tô sáng · ↻ AI dịch lại một câu · ô «Nhờ AI sửa» (gõ yêu cầu, AI chỉ sửa đúng các câu liên quan — cả
video). Câu AI sửa tô vàng, ↶ trả lại từng câu, «Hoàn tác» trả lại hết · ⊘ «không đọc câu này» (lời bài hát, câu
trích: giữ âm gốc, vẫn hiện phụ đề) · «Tìm & thay» cho cả video · icon loa = tạo lại giọng (câu không đổi dùng lại giọng
cũ nên rất nhanh; không sửa chữ mà giọng đã cũ thì vẫn tạo lại).

Bố cục kiểu chatbot: thanh icon nhỏ trên cùng (copy cho AI ngoài · dán bản dịch · tìm & thay · tạo lại giọng), danh
sách TẤT CẢ câu chiếm hết chiều cao còn lại (một thanh cuộn, không chia trang), ô «Nhờ AI sửa» neo dưới cùng kèm lịch
sử. Chữ sửa TỰ LƯU (ngừng gõ ~1 s / rời ô / AI sửa xong) — tạo giọng luôn đọc đúng chữ đang thấy. Danh sách ẢO: chỉ có ``POOL`` dòng widget (đủ lấp khung nhìn), cuộn tới đâu đổ
chữ câu đó vào — video 300 câu nhẹ như 30 câu (dựng mỗi dòng CTk ~45 ms: dựng đủ 300 dòng mất ~14 s). Nơi đặt editor phải cho nó một chiều cao (tab «Lời thoại»
đặt trang cao đúng bằng khung nhìn).
"""

from __future__ import annotations

import bisect
import logging
import re
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.core.state import VideoItem
from app.services.job_runner import error_line
from app.services.srt_utils import Cue, format_ts, load_srt, save_srt, wrap_lines
from app.ui.widgets.common import StatusLabel, auto_wrap, info_icon
from app.ui.widgets.ui import Tooltip, button
from app.ui.progress_panel import friendly_error

logger = logging.getLogger(__name__)


class _AutoHideStatus(StatusLabel):
    """Dòng trạng thái chỉ chiếm chỗ khi có chữ (không để khoảng trống giữa danh sách và ô chat)."""

    def configure(self, require_redraw=False, **kwargs):
        super().configure(require_redraw=require_redraw, **kwargs)
        if "text" in kwargs:
            if kwargs["text"]:
                self.grid()
            else:
                self.grid_remove()


class CueEditor(ctk.CTkFrame):
    POOL = 18                   # số dòng widget (đủ lấp khung nhìn cao ~1100 px) — dùng lại khi cuộn, mọi video
    WHEEL = 2                   # số câu cuộn mỗi nấc chuột

    AUTOSAVE_MS = 1000          # ngừng gõ bấy lâu thì tự lưu

    def __init__(self, master, window, *, on_saved: Callable[[VideoItem], None] | None = None,
                 on_import: Callable[[str], bool] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.window = window
        self.app_state = window.app_state
        self.on_saved = on_saved
        self.on_import = on_import      # dán SRT bản dịch (AI ngoài trả về) → nhập, giữ mốc thời gian gốc
        self.item: VideoItem | None = None
        self.cues: list = []
        self.ref: list = []
        self.texts: list[str] = []
        self._orig_texts: list[str] = []
        self._starts: list[float] = []
        self.edit_path: str | None = None
        self.is_translation = False
        self.page = 0                   # giữ cho nơi gọi cũ — danh sách không còn chia trang
        self._first = 0                 # câu ở dòng trên cùng của khung nhìn
        self._visible = self.POOL       # số dòng thấy trọn trong khung (đo sau khi vẽ)
        self._cur = -1                  # câu đang ở vị trí khung xem trước
        self._painted = -1
        self._stop_at: float | None = None
        self._tick_id: str | None = None
        self._ai_marks: set[int] = set()          # câu AI vừa sửa (tô vàng tới khi lưu / đổi video)
        self._ai_before: dict[int, str] = {}      # câu trước khi AI sửa (↶ / Hoàn tác)
        self._ai_after: dict[int, str] = {}       # chữ AI đặt vào câu — người dùng gõ đè thì tính là tự sửa
        #: các lượt «Nhờ AI sửa» của video đang sửa (yêu cầu, trả lời của AI kèm ví dụ câu đã đổi) — gửi kèm lượt sau
        #: để AI hiểu ý người dùng đang theo hướng nào (nhắn tiếp kiểu chat «còn câu 37 nữa» vẫn hiểu)
        self._ai_history: list[tuple[str, str]] = []
        self._src_sig: tuple = ()                 # file phụ đề lúc nạp — đổi (Chuẩn bị / dịch lại xong) → nạp lại
        self._ticks = 0
        self._noread: list[float] = []            # mốc bắt đầu các câu «không đọc» (lưu riêng, không nằm trong SRT)
        self._dirty_mark = False
        self._autosave_id: str | None = None
        self._base_texts: list[str] = []          # chữ lúc mở video — mốc «đã đổi gì» cho ngữ cảnh Nhờ AI sửa
        self.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        bar.grid_columnconfigure(0, weight=1)
        tools = ctk.CTkFrame(bar, fg_color="transparent")
        tools.grid(row=0, column=1, sticky="e", padx=(6, 0))
        self.copy_btn = button(tools, "", icon_name="copy", variant="ghost", size="sm", width=34, command=self._copy_menu,
                               tip="Copy để dán cho AI bên ngoài (ChatGPT, Gemini web…): lời gốc / song ngữ kèm lời dặn, "
                                   "hoặc SRT đang sửa")
        self.paste_btn = button(tools, "", icon_name="paste", variant="ghost", size="sm", width=34,
                                command=self._paste_import,
                                tip="Dán SRT bản dịch AI bên ngoài trả về (vừa copy) — giữ mốc thời gian gốc")
        self.find_btn = button(tools, "", icon_name="search", variant="ghost", size="sm", width=34,
                               command=self.find_replace, tip="Tìm & thay một chữ / tên riêng ở mọi câu của video")
        self.voice_btn = button(tools, "", icon_name="speaker", variant="ghost", size="sm", width=34,
                                command=self.regen_voice,
                                tip="Tạo lại giọng theo lời thoại đang thấy (câu không đổi dùng lại giọng cũ — nhanh). Nút "
                                    "sáng màu = giọng chưa khớp chữ vừa sửa.")
        for b in (self.copy_btn, self.paste_btn, self.find_btn, self.voice_btn):
            b.pack(side="left", padx=1)
        self.head = auto_wrap(ctk.CTkLabel(bar, text="", font=T.font(12, "medium"), text_color=T.TEXT_DIM, anchor="w",
                                           justify="left", wraplength=400))
        self.head.grid(row=0, column=0, sticky="ew")
        # danh sách câu: chiếm hết chiều cao còn lại, tự cuộn bên trong
        self.grid_rowconfigure(1, weight=1)
        self.rows_frame = ctk.CTkFrame(self, fg_color=T.BG_INPUT, corner_radius=8)
        self.rows_frame.grid(row=1, column=0, sticky="nsew")
        self.rows_frame.grid_columnconfigure(0, weight=1)
        self.rows_frame.grid_rowconfigure(0, weight=1)
        self.rows_area = ctk.CTkFrame(self.rows_frame, fg_color="transparent")
        self.rows_area.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        self.rows_area.grid_propagate(False)          # cao theo chỗ được chia, dòng thừa bị khuất (không đẩy khung)
        self.rows_area.grid_columnconfigure(0, weight=1)
        self.vbar = ctk.CTkScrollbar(self.rows_frame, command=self._vscroll)
        self.vbar.grid(row=0, column=1, sticky="ns", padx=2, pady=4)
        tk.Misc.bind(self.rows_area, "<Configure>", lambda _e: self.after_idle(self._update_bar), add="+")
        for w in (self.rows_area, self.rows_frame):              # lăn ở khoảng trống giữa các dòng cũng cuộn
            for x in (w, getattr(w, "_canvas", None)):
                if x is not None:
                    tk.Misc.bind(x, "<MouseWheel>", self._on_wheel, add="+")
        self._rows: list[dict] = []
        self._build_rows(self.POOL)

        self.status = _AutoHideStatus(self, "")
        self.status.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.status.grid_remove()

        # ── Nhờ AI sửa (kiểu chatbot, neo dưới cùng): lịch sử hỏi – đáp + ô nhập; AI tự tìm câu liên quan ──
        ai = ctk.CTkFrame(self, fg_color=T.BG_INPUT, corner_radius=8)
        ai.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        ai.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(ai, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(6, 2))
        ctk.CTkLabel(head, text="Nhờ AI sửa", font=T.font(11), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        info_icon(head, "Gõ yêu cầu bằng lời, vd «xưng anh – em», «sửa bạn mày thành chó nhà mày», «rà lại cả kịch bản cho "
                        "thống nhất». AI đọc cả video, tự tìm mọi câu cần sửa (câu hỏi lẫn câu trả lời), câu khác giữ "
                        "nguyên. Nhắn tiếp được như chat («còn câu 37 nữa») — AI nhớ các yêu cầu trước và các câu đã đổi "
                        "(cả câu bạn tự sửa). Câu AI sửa tô vàng (tự lưu) — ↶ trả lại từng câu, «Hoàn tác» trả lại hết.").pack(
            side="left", padx=(4, 0))
        self.ai_undo = button(head, "Hoàn tác", icon_name="undo", variant="ghost", size="sm", command=self.ai_revert,
                              tip="Trả lại mọi câu AI đã sửa như cũ")
        self.ai_undo.pack(side="right", padx=(0, 4))
        self.ai_undo.pack_forget()
        # lịch sử hỏi – đáp của video đang sửa (hiện khi đã nhắn) — như khung chat
        self.chat = ctk.CTkTextbox(ai, height=96, font=T.font(12), wrap="word", fg_color="transparent",
                                   activate_scrollbars=True)
        self.chat.tag_config("me", foreground=T.ACCENT)
        self.chat.tag_config("ai", foreground=T.TEXT)
        self.chat.tag_config("wait", foreground=T.TEXT_MUTED)
        self.chat.configure(state="disabled")
        self.chat.grid(row=1, column=0, columnspan=3, sticky="ew", padx=6)
        self.chat.grid_remove()
        self.ai_req = ctk.CTkEntry(ai, height=34, font=T.font(12),
                                   placeholder_text="Nhắn AI sửa lời thoại… vd: sửa câu trả lời cho khớp câu hỏi")
        self.ai_req.grid(row=2, column=0, sticky="ew", padx=(8, 0), pady=(4, 8))
        self.ai_req.bind("<Return>", lambda _e: (self.ai_edit(), "break")[1])
        self.ai_send = button(ai, "Gửi", icon_name="sparkle", size="sm", command=self.ai_edit,
                              tip="Gửi (Enter). Câu AI sửa được tô vàng, chưa lưu — xem rồi bấm Lưu.")
        self.ai_send.grid(row=2, column=1, padx=(6, 8), pady=(4, 8))

    def _build_rows(self, n: int) -> None:
        """Dựng thêm dòng (ô câu + nút) tới đủ ``n`` dòng."""
        for r in range(len(self._rows), n):
            top = ctk.CTkFrame(self.rows_area, fg_color="transparent")
            play = ctk.CTkButton(top, text="", width=96, height=22, anchor="w", font=T.mono(10), fg_color="transparent",
                                 hover_color=T.BG_HOVER, text_color=T.TEXT_MUTED, command=lambda r=r: self._play_row(r))
            play.pack(side="left")
            ref = auto_wrap(ctk.CTkLabel(top, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w",
                                         justify="left", wraplength=300))
            ref.pack(side="left", fill="x", expand=True, padx=(4, 0))
            ent = ctk.CTkEntry(self.rows_area, height=30, font=T.font(12))
            ent.bind("<FocusIn>", lambda _e, r=r: self._focus_row(r))
            ent.bind("<KeyRelease>", lambda _e: self._update_dirty_mark(), add="+")
            ent.bind("<FocusOut>", lambda _e: self._schedule_autosave(250), add="+")
            ent.bind("<Return>", lambda _e, r=r: self._move(r, 1))
            ent.bind("<Down>", lambda _e, r=r: self._move(r, 1))
            ent.bind("<Up>", lambda _e, r=r: self._move(r, -1))
            # Ctrl+Enter / Ctrl+↑↓ (duyệt, đổi video) — ô nhập tự bắt Enter/↑↓ nên chuyển tiếp lên cửa sổ chính
            ent.bind("<Control-Return>", lambda _e: (self.window._review_key("approve"), "break")[1])
            ent.bind("<Control-Down>", lambda _e: (self.window._review_key("next"), "break")[1])
            ent.bind("<Control-Up>", lambda _e: (self.window._review_key("prev"), "break")[1])
            redo = ctk.CTkButton(self.rows_area, text="↻", width=30, height=30, command=lambda r=r: self._ai_row(r),
                                 **{**T.secondary_button(), "font": T.font(13, "bold")})
            back = ctk.CTkButton(self.rows_area, text="↶", width=30, height=30, command=lambda r=r: self._revert_row(r),
                                 **{**T.secondary_button(), "font": T.font(13, "bold"), "text_color": T.WARNING})
            mute = ctk.CTkButton(self.rows_area, text="⊘", width=30, height=30, command=lambda r=r: self._toggle_noread(r),
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
            for w in (top, ent, redo, back, mute):
                self._bind_wheel(w)


    def _bind_wheel(self, w) -> None:
        """Lăn chuột trên dòng nào cũng cuộn danh sách (không cuộn cả bảng tính năng)."""
        tk.Misc.bind(w, "<MouseWheel>", self._on_wheel, add="+")
        for ch in w.winfo_children():
            self._bind_wheel(ch)

    def _on_wheel(self, e) -> str:
        self._scroll(-self.WHEEL if e.delta > 0 else self.WHEEL)
        return "break"

    def destroy(self) -> None:
        if self._autosave_id is not None:
            try:
                self.after_cancel(self._autosave_id)
            except Exception:                          # noqa: BLE001
                pass
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

    def _chat_add(self, who: str, text: str) -> None:
        """Thêm một dòng vào khung chat (``who``: "me" / "ai" / "wait")."""
        self.chat.configure(state="normal")
        if who == "ai" or who == "wait":
            self._chat_drop_wait()
        prefix = {"me": "Bạn ›  ", "ai": "AI ›  ", "wait": "AI ›  "}[who]
        start = self.chat.index("end-1c")
        self.chat.insert("end", ("\n" if self.chat.get("1.0", "end-1c") else "") + prefix + text)
        self.chat.tag_add(who, start, "end-1c")
        self.chat.configure(state="disabled")
        self.chat.grid()
        self.chat.see("end")

    def _chat_drop_wait(self) -> None:
        """Bỏ dòng «AI đang đọc…» khi có trả lời."""
        ranges = self.chat.tag_ranges("wait")
        if ranges:
            self.chat.configure(state="normal")
            self.chat.delete(ranges[0], ranges[-1])      # dòng chờ gồm cả dấu xuống dòng phía trước
            self.chat.configure(state="disabled")

    def _chat_clear(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self.chat.grid_remove()

    def load(self, it: VideoItem | None) -> None:
        self._flush_autosave()                          # chữ đang gõ dở của video trước → lưu trước khi đổi
        if it is not self.item:
            self._ai_history = []
            self._chat_clear()
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
                self.head.configure(text=f"Bản dịch · {len(self.cues)} câu")       # chữ nhỏ trên mỗi ô = câu gốc
            elif orig:
                self.edit_path, self.cues = orig, load_srt(orig)
                ext = self.app_state.settings.prepare_until == "source"
                self.head.configure(text=f"Lời gốc · {len(self.cues)} câu" + (
                    " — chưa dịch: icon copy → dịch ngoài → icon dán" if ext else ""))
            else:
                self.head.configure(text="Chưa có lời thoại — nhận diện ở tab Nhận diện, nạp SRT ở tab «Dịch», hoặc bấm "
                                         "«Chuẩn bị».")
        self._starts = [c.start for c in self.cues]
        self.texts = [c.text.replace("\n", " ") for c in self.cues]
        self._orig_texts = list(self.texts)
        self._base_texts = list(self.texts)
        self._noread = self.window.pipeline.load_noread(it) if it is not None else []
        self._first = 0                                 # video mới → về đầu danh sách
        self.show_page(0, sync=False)
        self._update_dirty_mark()
        self.status.info("")

    def _range(self) -> range:
        """Câu đang nằm trên các dòng của khung nhìn."""
        return range(self._first, min(self._first + len(self._rows), len(self.cues)))

    def _sync(self) -> None:
        """Chữ đang gõ ở các dòng đang hiện → ``texts`` (gọi trước khi cuộn / lưu / gửi AI)."""
        for row, i in zip(self._rows, self._range()):
            if i < len(self.texts):
                self.texts[i] = row["entry"].get()

    @staticmethod
    def _set(row: dict, name: str, **kw) -> None:
        """configure chỉ phần thật sự đổi — mỗi lần configure, widget CTk vẽ lại cả khung (cuộn danh sách ảo vẽ lại
        18 dòng mỗi nấc chuột: bỏ được phần lớn chi phí)."""
        cache = row.setdefault("_cache", {}).setdefault(name, {})
        diff = {k: v for k, v in kw.items() if cache.get(k) != v}
        if diff:
            row[name].configure(**diff)
            cache.update(diff)

    def _paint_row(self, r: int, i: int) -> None:
        row, c = self._rows[r], self.cues[i]
        off = self._is_noread(i)
        self._set(row, "play", text=f"{'⊘' if off else '▶'} {i + 1} · {format_ts(c.start, '.')[3:8]}",
                  text_color=T.TEXT_MUTED)
        self._paint_ref(row, i)
        if row["entry"].get() != self.texts[i]:
            row["entry"].delete(0, "end")
            row["entry"].insert(0, self.texts[i])
        self._set(row, "entry", border_color=self._base_border(i), text_color=T.TEXT_MUTED if off else T.TEXT)
        self._set(row, "mute", text_color=T.WARNING if off else T.TEXT_DIM)
        shown = row.setdefault("_shown", {})
        for w, kw in row["widgets"]:
            show = (w is not row["redo"] or self.is_translation) and (w is not row["back"] or i in self._ai_before)
            if shown.get(id(w)) != show:
                (w.grid(**kw) if show else w.grid_remove())
                shown[id(w)] = show

    def show_page(self, page: int = 0, sync: bool = True) -> None:
        """Vẽ lại các dòng đang hiện (tên cũ — không còn chia trang; ``page`` bỏ qua)."""
        if sync and self.cues:
            self._sync()
        self._first = max(0, min(self._first, len(self.cues) - 1))
        idxs = self._range()
        for r, row in enumerate(self._rows):
            if r < len(idxs):
                self._paint_row(r, idxs[r])
            else:
                shown = row.setdefault("_shown", {})
                for w, _kw in row["widgets"]:
                    if shown.get(id(w)) is not False:
                        w.grid_remove()
                        shown[id(w)] = False
        if not self.cues:
            self.rows_frame.grid_remove()
        else:
            self.rows_frame.grid()
        self._painted = -1
        self._paint_current(follow=False)
        self.after_idle(self._update_bar)
        for b in (self.copy_btn, self.paste_btn, self.find_btn, self.voice_btn, self.ai_send):
            b.configure(state="normal" if self.cues else "disabled")

    # ── Cuộn (danh sách ảo) ──────────────────────────────────────────────
    def _update_bar(self) -> None:
        """Đo số dòng thấy trọn trong khung → thanh cuộn đúng tỉ lệ."""
        try:
            h = self.rows_area.winfo_height()
            if h > 1:
                n = 0
                for row, _i in zip(self._rows, self._range()):
                    e = row["entry"]
                    if not e.winfo_ismapped() or e.winfo_y() + e.winfo_height() > h:
                        break
                    n += 1
                self._visible = max(1, n)
            total = max(1, len(self.cues))
            self.vbar.set(self._first / total, min(1.0, (self._first + self._visible) / total))
        except tk.TclError:
            pass

    def _scroll(self, n: int) -> None:
        if not self.cues:
            return
        last = max(0, len(self.cues) - self._visible)
        first = max(0, min(self._first + n, last))
        if first == self._first:
            return
        self._sync()
        self._first = first
        self.show_page(sync=False)

    def _vscroll(self, *args) -> None:
        """Thanh cuộn: kéo («moveto») hoặc bấm mũi tên / rãnh («scroll»)."""
        if not self.cues or not args:
            return
        if args[0] == "moveto":
            self._scroll(int(float(args[1]) * len(self.cues)) - self._first)
        elif args[0] == "scroll":
            step = max(1, self._visible - 1) if len(args) > 2 and args[2] == "pages" else 1
            self._scroll(int(float(args[1])) * step)

    def _see_row(self, i: int) -> None:
        """Cuộn để câu ``i`` nằm trong khung nhìn (đang phát / bấm ↑↓ / AI vừa sửa câu khuất)."""
        if not (0 <= i < len(self.cues)) or self._first <= i < self._first + self._visible:
            return
        self._scroll(max(0, i - 1) - self._first)

    def _paint_ref(self, row: dict, i: int) -> None:
        """Dòng chữ nhỏ trên ô sửa: câu gốc; câu AI đã sửa thì thêm câu cũ (tô vàng)."""
        base = self.ref[i].plain if i < len(self.ref) else ""
        if self._is_noread(i):
            base = ("⊘ không đọc — giữ âm gốc · " + base).strip(" ·")
        if i not in self._ai_before:
            self._set(row, "ref", text=base, text_color=T.TEXT_MUTED)
            return
        old = self._ai_before[i]
        note = f"✎ trước: «{old if len(old) <= 90 else old[:89] + '…'}»"
        self._set(row, "ref", text=f"{base}\n{note}" if base else note, text_color=T.WARNING)

    # ── Gắn với khung xem trước ──────────────────────────────────────────
    def _row_index(self, r: int) -> int | None:
        i = self._first + r
        return i if 0 <= r < len(self._rows) and i < len(self.cues) else None

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
        self._see_row(j)
        if self._first <= j < self._first + len(self._rows):
            self._rows[j - self._first]["entry"].focus_set()
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
                self._paint_current()                      # tô + cuộn tới câu đang đọc
            if self._stop_at is not None:
                if not pv.player.playing:
                    self._stop_at = None
                elif t >= self._stop_at:
                    self._stop_at = None
                    pv.stop_play()
        self._tick_id = self.after(200, self._tick)

    def _paint_current(self, follow: bool = True) -> None:
        """Tô dòng của câu đang ở vị trí phát (chỉ vẽ lại 2 dòng đổi trạng thái — configure CTk vẽ lại cả widget);
        đang phát mà câu nằm ngoài khung → cuộn theo."""
        pv = self.window.preview
        if follow and self._cur >= 0 and pv.player.playing and not (self._first <= self._cur < self._first + self._visible):
            self._see_row(self._cur)                       # vẽ lại cả khung → gọi lại hàm này (follow=False)
            return
        want = self._cur - self._first if self._cur in self._range() else -1
        old = self._painted
        if want == old:
            return
        for r, on in ((old, False), (want, True)):
            if 0 <= r < len(self._rows) and self._row_index(r) is not None:
                self._set(self._rows[r], "play", text_color=T.ACCENT if on else T.TEXT_MUTED)
                self._set(self._rows[r], "entry",
                          border_color=T.ACCENT if on else self._base_border(self._row_index(r)))
        self._painted = want

    # ── Lưu ──────────────────────────────────────────────────────────────
    def _text_dirty(self) -> bool:
        if not self.cues:
            return False
        self._sync()
        return self.texts != self._orig_texts

    def dirty(self) -> bool:
        return self._text_dirty()

    def save(self, regen: bool = True, quiet: bool = False) -> None:
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
        if quiet:                                      # tự lưu: giữ tô vàng + ↶ của câu AI sửa tới khi đổi video
            logger.debug("%s: tự lưu %d câu lời thoại", it.name, changed)
            st = self.app_state.state_for(it)
            self.status.info("✓ Đã tự lưu" + (" — bấm 🔊 (icon loa) để tạo lại giọng, hoặc Chuẩn bị / Xuất tự tạo lại."
                                               if st.tts.enable_tts else "."))
            self._refresh_voice_btn()
            self.window.preview.schedule_refresh(0)
            if self.on_saved:
                self.on_saved(it)
            return
        if self._ai_marks or self._ai_before:          # đã lưu → bỏ tô vàng
            self._clear_ai_marks()
            self.show_page(self.page, sync=False)
        self.window.log(f"{it.name}: đã sửa {changed} câu lời thoại.")
        st = self.app_state.state_for(it)
        if st.tts.enable_tts and regen:
            self.status.warn(f"Đã lưu {changed} câu — đang tạo lại giọng (câu không đổi dùng lại)…")
            self._regenerate(it)
        elif st.tts.enable_tts:
            self.status.ok(f"Đã lưu {changed} câu phụ đề — giọng chưa tạo lại: bấm 🔊 khi muốn nghe, hoặc Chuẩn bị / "
                           "Xuất sẽ tự tạo lại.")
            self._refresh_voice_btn()
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
        """Có câu sửa chưa lưu → hẹn tự lưu (ngừng gõ ``AUTOSAVE_MS``)."""
        self._dirty_mark = bool(self.cues) and self._text_dirty()
        if self._dirty_mark:
            self._schedule_autosave()

    # ── Tự lưu ───────────────────────────────────────────────────────────
    def _schedule_autosave(self, ms: int | None = None) -> None:
        if self._autosave_id is not None:
            try:
                self.after_cancel(self._autosave_id)
            except Exception:                          # noqa: BLE001
                pass
        self._autosave_id = self.after(self.AUTOSAVE_MS if ms is None else ms, self._autosave)

    def _autosave(self) -> None:
        self._autosave_id = None
        if self.item is not None and self.winfo_exists() and self.cues and self._text_dirty():
            self.save(regen=False, quiet=True)

    def _flush_autosave(self) -> None:
        """Lưu ngay chữ đang chờ tự lưu (đổi video / đóng)."""
        if self._autosave_id is not None:
            try:
                self.after_cancel(self._autosave_id)
            except Exception:                          # noqa: BLE001
                pass
            self._autosave_id = None
        if self.item is not None and self.cues and self._text_dirty():
            self.save(regen=False, quiet=True)

    # ── Giọng ────────────────────────────────────────────────────────────
    def _voice_state(self, it: VideoItem) -> tuple[bool, bool]:
        """(có track giọng, track khớp chữ + cấu hình hiện tại)."""
        try:
            path, fresh, _cut = self.window.pipeline_for(it).current_voice_track(it)
        except Exception:                            # noqa: BLE001
            return False, False
        return path is not None, bool(fresh)

    def _refresh_voice_btn(self) -> None:
        """Icon loa sáng màu khi video có lồng tiếng mà giọng chưa khớp chữ vừa sửa."""
        from app.ui.widgets.ui import set_variant

        it = self.item
        stale = False
        if it is not None and self.cues and not it.voice_locked and self.app_state.state_for(it).tts.enable_tts:
            has, fresh = self._voice_state(it)
            stale = has and not fresh
        set_variant(self.voice_btn, "primary" if stale else "ghost")

    def regen_voice(self) -> None:
        """Icon loa: lưu chữ (nếu còn dở) rồi tạo lại giọng — câu không đổi dùng lại giọng cũ."""
        it = self.item
        if it is None or not self.cues:
            return
        self._flush_autosave()
        st = self.app_state.state_for(it)
        if not st.tts.enable_tts:
            self.status.info("Video này chưa bật lồng tiếng — bật ở tab Lồng tiếng.")
            return
        if it.voice_locked:
            self.status.info("Giọng của video này đang khoá (giọng có sẵn) — mở khoá ở tab Lồng tiếng để tạo lại.")
            return
        has, fresh = self._voice_state(it)
        if has and fresh:
            self.status.info("Giọng đã khớp lời thoại hiện tại — không cần tạo lại.")
            return
        self.status.warn("Đang tạo lại giọng (câu không đổi dùng lại)…")
        self._regenerate(it)

    # ── Copy / dán cho AI bên ngoài ──────────────────────────────────────
    def _copy_menu(self) -> None:
        import tkinter as tkm

        has_tr = self.is_translation
        m = tkm.Menu(self, tearoff=0, bg=T.BG_ELEVATED, fg=T.TEXT, activebackground=T.BG_HOVER, activeforeground=T.TEXT,
                     bd=0)
        m.add_command(label="Lời gốc + lời dặn dịch — dán cho AI dịch", command=lambda: self._copy("translate"))
        if has_tr:
            m.add_command(label="Song ngữ + lời dặn rà soát — dán cho AI soát bản dịch", command=lambda: self._copy("review"))
        m.add_separator()
        m.add_command(label="Chỉ SRT đang sửa" + (" (bản dịch)" if has_tr else ""), command=lambda: self._copy("srt"))
        m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _copy(self, kind: str) -> None:
        """Chép vào clipboard để dán thẳng vào khung chat AI: lời dặn + SRT trong khối ```srt (hoặc chỉ SRT)."""
        from app.services import external_ai as X

        it = self.item
        if it is None or not self.cues:
            return
        self._sync()
        st = self.app_state.state_for(it)
        cur = [Cue(c.index, c.start, c.end, t) for c, t in zip(self.cues, self.texts)]
        if kind == "srt":
            text = X.export_source(cur).lstrip(X.BOM)
            msg = f"Đã copy SRT đang sửa ({len(cur)} câu)."
        else:
            src = self.ref if self.is_translation else self.cues
            srt = (X.export_bilingual(src, cur) if kind == "review" else X.export_source(src)).lstrip(X.BOM)
            ask = X.prompt(kind, len(src), X.source_name(st.subtitle.stt_language, " ".join(c.plain for c in src[:20])),
                           X.target_name(st.subtitle.translate_target), st.subtitle.translate_style,
                           st.settings.glossary, inline=True)
            text = f"{ask}\n```srt\n{srt.strip()}\n```\n"
            msg = (f"Đã copy {'lời gốc' if kind == 'translate' else 'song ngữ'} + lời dặn ({len(src)} câu) — dán vào khung "
                   "chat AI; AI trả về thì copy phần SRT rồi bấm icon dán.")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.ok(msg)

    def _paste_import(self) -> None:
        """Icon dán: SRT bản dịch vừa copy từ khung chat AI → nhập (giữ mốc thời gian gốc của từng câu)."""
        try:
            text = self.clipboard_get()
        except Exception:                              # noqa: BLE001
            text = ""
        if "-->" not in text and not re.search(r"^\s*\[?\d+\]?\s*[.:|)\]-]\s*\S", text, re.M):
            self.status.warn("Clipboard chưa có SRT — copy phần SRT AI trả về (cả số thứ tự và mốc thời gian) rồi bấm lại.")
            return
        if self.on_import is None or self.item is None:
            return
        self._flush_autosave()
        self.on_import(text)

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
                                                         self.app_state.settings.active_model())
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
            pipe.prepare(it, on_update=lambda x: win.ui_call(lambda: upd(x)), log=win.log_bg, cancel=cancel,
                         stops=False)
            if keep[0] in ("approved", "skipped"):
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
        self._ai_after = {}
        self.ai_undo.pack_forget()

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
            self._ai_after[i] = t
            self._ai_marks.add(i)
            changed.append(i)
        if self._ai_marks:
            self.ai_undo.pack(side="right", padx=(0, 4))
        return changed

    def _revert_row(self, r: int) -> None:
        i = self._row_index(r)
        if i is None or i not in self._ai_before:
            return
        self._sync()
        self.texts[i] = self._ai_before.pop(i)
        self._ai_after.pop(i, None)
        self._ai_marks.discard(i)
        self._update_dirty_mark()
        if not self._ai_before:
            self.ai_undo.pack_forget()
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
        from app.services.translate_router import ai_edit, edit_context

        self._sync()
        current = list(self.texts)
        # câu đã đổi mà chưa lưu (AI hay người dùng tự gõ) + các lượt trước → AI hiểu hướng sửa đang theo
        changes = [(i, o, t, "AI" if self._ai_after.get(i, "").strip() == t.strip() else "user")
                   for i, (t, o) in enumerate(zip(self.texts, self._base_texts)) if t.strip() != o.strip()]
        context = edit_context(self._ai_history, changes)
        brief = self.window.pipeline_for(self.item).load_brief(self.item)     # hồ sơ kịch bản: xưng hô, tên, câu lặp
        source = [c.plain for c in self.ref] if self.is_translation else None
        st = self.app_state.state_for(it)
        win = self.window
        self.status.info("")
        self._chat_add("me", req)
        self._chat_add("wait", f"đang đọc {len(current)} câu…")
        self.ai_req.delete(0, "end")
        self.ai_send.configure(state="disabled")

        def work(cancel):
            return ai_edit(current, source, req, settings=st.settings, target=st.subtitle.translate_target,
                           style=st.subtitle.translate_style, context=context, brief=brief, log=win.log_bg,
                           cancel=cancel)

        def done(result, error):
            if not self.winfo_exists():
                return
            self.ai_send.configure(state="normal")
            if self.item is not it:
                return
            if error:
                self._chat_add("ai", f"✖ Lỗi: {friendly_error(error_line(error, 200))}")
                return
            edits, note = result
            changed = self._apply_ai(edits, current)     # câu người dùng sửa tiếp trong lúc chờ: giữ chữ của họ
            done_ex = "; ".join(f"câu {i + 1}: «{current[i]}» → «{self.texts[i]}»" for i in changed[:2])
            self._ai_history.append((req, (note + (f" (vd {done_ex})" if done_ex else "")).strip()))
            if not changed:
                self._chat_add("ai", f"Không thấy câu nào cần sửa. {note}".strip())
                return
            self.show_page(0, sync=False)
            self._see_row(changed[0])
            self._update_dirty_mark()
            nums = ", ".join(f"#{i + 1}" for i in changed[:12]) + ("…" if len(changed) > 12 else "")
            self._chat_add("ai", f"{note} — sửa {len(changed)} câu ({nums}), tô vàng (↶ để trả lại).".strip(" —"))
            self._schedule_autosave(300)

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
        self.status.info(f"Đã hoàn tác {n} câu AI đã sửa.")
        self._schedule_autosave(300)

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
                self.status.ok(f"AI đã dịch lại {changed} câu (tô vàng, ↶ để trả lại).")
                self._schedule_autosave(300)
            else:
                self.status.info("AI trả về giống bản cũ — thử ô «Nhờ AI sửa» với yêu cầu cụ thể hơn.")

        win.run_task(f"Dịch lại câu {idxs[0] + 1}", work, done)

    def _ai_row(self, r: int) -> None:
        i = self._row_index(r)
        if i is not None:
            self._retranslate([i])
