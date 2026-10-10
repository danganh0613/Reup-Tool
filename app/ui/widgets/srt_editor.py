"""Hộp thoại phụ đề: Xem/Sửa SRT (tìm & thay), Dịch thủ công (bảng cue)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import button
from app.config.paths import SUBS_DIR
from app.core.state import VideoItem
from app.services.srt_utils import Cue, format_ts, load_srt, parse_srt, save_srt, to_srt
from app.services.translate_router import RETRANSLATE_PRESETS


class SRTEditorDialog(ctk.CTkToplevel):
    """Sửa trực tiếp SRT gốc / bản dịch với tìm-thay."""

    def __init__(self, window, item: VideoItem, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.item = item
        self.on_saved = on_saved
        self.title("Xem / Sửa SRT")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1000, sw - 60)}x{min(780, sh - 100)}+40+40")
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=8)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="Tìm", font=T.font(12), text_color=T.TEXT_DIM).grid(row=0, column=0, padx=(10, 6), pady=6)
        self.find_var = ctk.StringVar()
        ctk.CTkEntry(top, textvariable=self.find_var, height=32).grid(row=0, column=1, sticky="ew", pady=6)
        ctk.CTkButton(top, text="Trước", width=70, height=32, command=lambda: self._find(-1), **T.secondary_button()).grid(row=0, column=2, padx=4)
        ctk.CTkButton(top, text="Tiếp", width=70, height=32, command=lambda: self._find(1), **T.secondary_button()).grid(row=0, column=3, padx=(0, 10))
        ctk.CTkLabel(top, text="Thay", font=T.font(12), text_color=T.TEXT_DIM).grid(row=1, column=0, padx=(10, 6), pady=(0, 6))
        self.repl_var = ctk.StringVar()
        ctk.CTkEntry(top, textvariable=self.repl_var, height=32).grid(row=1, column=1, sticky="ew", pady=(0, 6))
        ctk.CTkButton(top, text="Thay vị trí này", width=110, height=32, command=self._replace_one, **T.secondary_button()).grid(row=1, column=2, padx=4, pady=(0, 6))
        ctk.CTkButton(top, text="Thay tất cả", width=100, height=32, command=self._replace_all, **T.primary_button()).grid(row=1, column=3, padx=(0, 10), pady=(0, 6))

        self.target = ctk.StringVar(value="Gốc")
        seg = ctk.CTkSegmentedButton(self, values=["Gốc", "Bản dịch"], variable=self.target, command=lambda _v: self._load(),
                                     font=T.font(12))
        seg.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 4))
        self.box = ctk.CTkTextbox(self, font=T.mono(12), wrap="word", undo=True)
        self.box.grid(row=2, column=0, sticky="nsew", padx=10)
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=10, pady=10)
        self.status = ctk.CTkLabel(foot, text="Chỉ cue đã sửa sẽ được tạo lại giọng ở lần render kế tiếp", font=T.font(11),
                                   text_color=T.TEXT_MUTED, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(foot, text="Lưu  Ctrl+S", width=160, height=36, command=self._save, **T.primary_button()).pack(side="right")
        ctk.CTkButton(foot, text="Đóng", width=100, height=36, command=self.destroy, **T.secondary_button()).pack(side="right", padx=(0, 8))
        for mod in ("Control", "Command") if sys.platform == "darwin" else ("Control",):
            self.bind(f"<{mod}-s>", lambda _e: self._save())
            self.bind(f"<{mod}-f>", lambda _e: self._find(1))
        self._load()

    def _path(self) -> str | None:
        return self.item.srt_translated_path if self.target.get() == "Bản dịch" else self.item.srt_path

    def _load(self) -> None:
        self.box.delete("1.0", "end")
        p = self._path()
        if p and Path(p).exists():
            try:
                self.box.insert("1.0", to_srt(load_srt(p)))
                self.status.configure(text=f"{Path(p).name} — chỉ cue đã sửa sẽ được tạo lại giọng ở lần render kế tiếp")
            except Exception as exc:
                self.status.configure(text=f"Không đọc được: {exc}")
        else:
            self.status.configure(text="Chưa có file — nội dung lưu sẽ tạo file mới trong output/subs")

    def _find(self, direction: int) -> None:
        needle = self.find_var.get()
        if not needle:
            return
        start = self.box.index("insert")
        if direction > 0:
            pos = self.box.search(needle, f"{start}+1c", stopindex="end", nocase=True)
            if not pos:
                pos = self.box.search(needle, "1.0", stopindex="end", nocase=True)
        else:
            pos = self.box.search(needle, start, stopindex="1.0", backwards=True, nocase=True)
            if not pos:
                pos = self.box.search(needle, "end", stopindex="1.0", backwards=True, nocase=True)
        if pos:
            end = f"{pos}+{len(needle)}c"
            self.box.tag_remove("sel", "1.0", "end")
            self.box.tag_add("sel", pos, end)
            self.box.mark_set("insert", pos)
            self.box.see(pos)
            self.box.focus_set()

    def _replace_one(self) -> None:
        try:
            sel = self.box.get("sel.first", "sel.last")
        except Exception:
            self._find(1)
            return
        if sel.lower() == self.find_var.get().lower():
            self.box.delete("sel.first", "sel.last")
            self.box.insert("insert", self.repl_var.get())
        self._find(1)

    def _replace_all(self) -> None:
        needle, repl = self.find_var.get(), self.repl_var.get()
        if not needle:
            return
        text = self.box.get("1.0", "end")
        import re

        new_text, n = re.subn(re.escape(needle), repl, text, flags=re.IGNORECASE)
        if n:
            self.box.delete("1.0", "end")
            self.box.insert("1.0", new_text.rstrip("\n") + "\n")
        self.status.configure(text=f"Đã thay {n} chỗ.")

    def _save(self) -> None:
        cues = parse_srt(self.box.get("1.0", "end"))
        if not cues:
            self.status.configure(text="Nội dung không có cue hợp lệ.")
            return
        p = self._path()
        if not p:
            suffix = ".dich.srt" if self.target.get() == "Bản dịch" else ".srt"
            p = str(SUBS_DIR / f"{self.item.stem}{suffix}")
            if self.target.get() == "Bản dịch":
                self.item.srt_translated_path = p
            else:
                self.item.srt_path = p
        save_srt(cues, p)
        self.status.configure(text=f"Đã lưu {Path(p).name} · {len(cues)} cue")
        self.window.app_state.mark_dirty()
        if self.on_saved:
            self.on_saved()


class ManualTranslateDialog(ctk.CTkToplevel):
    """Bảng dịch tay: mỗi cue một ô nhập bản dịch; có thể dịch AI các cue đã chọn.

    SRT dài (hàng nghìn cue) → chia trang ``PAGE`` cue; các dòng widget dựng một lần rồi đổ dữ liệu trang mới vào
    (dựng 4 widget CTk × mọi cue một lúc làm cửa sổ mở mất nhiều giây). Dữ liệu thật nằm ở ``translated`` / ``checked``.
    """

    PAGE = 50

    def __init__(self, window, item: VideoItem, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.item = item
        self.on_saved = on_saved
        self.title("Dịch thủ công")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1200, sw - 60)}x{min(800, sh - 100)}+40+40")
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.cues = load_srt(item.srt_path)
        self.translated: list[str] = [""] * len(self.cues)
        if item.srt_translated_path and Path(item.srt_translated_path).exists():
            tr = load_srt(item.srt_translated_path)
            for i, c in enumerate(tr[:len(self.cues)]):
                self.translated[i] = c.text.replace("\n", " ")
        self.checked: list[bool] = [False] * len(self.cues)
        self.page = 0
        self.pages = max(1, (len(self.cues) + self.PAGE - 1) // self.PAGE)

        head = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=8)
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        ctk.CTkLabel(head, text=f"{item.name} · {len(self.cues)} cue — gõ bản dịch, hoặc tick cue rồi «Dịch AI đã chọn»",
                     font=T.font(12), anchor="w").pack(side="left", padx=10, pady=8)
        ctk.CTkButton(head, text="Chọn tất cả", width=100, height=32, command=lambda: self._check_all(True), **T.secondary_button()).pack(side="right", padx=4)
        ctk.CTkButton(head, text="Bỏ chọn", width=90, height=32, command=lambda: self._check_all(False), **T.secondary_button()).pack(side="right", padx=4)
        button(head, "Copy văn bản gốc", icon_name="list", size="sm", command=self._copy_source).pack(side="right", padx=4)
        button(head, "Dán bản dịch (theo dòng)", icon_name="download", size="sm", command=self._paste_lines).pack(side="right", padx=4)

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew", padx=10)
        self.list.grid_columnconfigure(2, weight=1)
        self.list.grid_columnconfigure(3, weight=1)
        self._rows: list[dict] = []
        for r in range(min(self.PAGE, len(self.cues))):
            var = ctk.BooleanVar(value=False)
            chk = ctk.CTkCheckBox(self.list, text="", variable=var, width=24)
            idx = ctk.CTkLabel(self.list, text="", font=T.mono(10), text_color=T.TEXT_MUTED, width=80, anchor="w", justify="left")
            src = ctk.CTkLabel(self.list, text="", font=T.font(11), anchor="w", justify="left", wraplength=460,
                               text_color=T.TEXT_DIM)
            e = ctk.CTkEntry(self.list, height=32, font=T.font(12))
            self._rows.append({"var": var, "widgets": [(chk, dict(row=r, column=0, padx=(0, 4), pady=2)),
                                                       (idx, dict(row=r, column=1, sticky="w", pady=2)),
                                                       (src, dict(row=r, column=2, sticky="ew", padx=(0, 8), pady=2)),
                                                       (e, dict(row=r, column=3, sticky="ew", pady=2))],
                               "idx": idx, "src": src, "entry": e})

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        nav = ctk.CTkFrame(foot, fg_color="transparent")
        nav.pack(side="left")
        self.prev_btn = ctk.CTkButton(nav, text="‹ Trước", width=80, height=32, command=lambda: self._show_page(self.page - 1),
                                      **T.secondary_button())
        self.prev_btn.pack(side="left")
        self.page_label = ctk.CTkLabel(nav, text="", font=T.font(11), width=170)
        self.page_label.pack(side="left", padx=6)
        self.next_btn = ctk.CTkButton(nav, text="Sau ›", width=80, height=32, command=lambda: self._show_page(self.page + 1),
                                      **T.secondary_button())
        self.next_btn.pack(side="left")
        if self.pages <= 1:
            nav.pack_forget()
        self.status = ctk.CTkLabel(foot, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=(10, 0))
        button(foot, "Lưu bản dịch", icon_name="save", variant="primary", command=self._save).pack(side="right")
        button(foot, "AI dịch / dịch lại đã chọn", icon_name="sparkle", command=self._ai_selected,
               tip="Câu chưa dịch → dịch mới · câu đã dịch → AI dịch lại theo yêu cầu bên dưới.").pack(side="right", padx=8)
        ctk.CTkButton(foot, text="Đóng", width=90, height=36, command=self.destroy, **T.secondary_button()).pack(side="right")
        req = ctk.CTkFrame(self, fg_color="transparent")
        req.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        ctk.CTkLabel(req, text="Yêu cầu khi dịch lại câu đã chọn", font=T.font(11), text_color=T.TEXT_DIM).pack(side="left")
        self.ai_req = ctk.CTkComboBox(req, values=RETRANSLATE_PRESETS, height=30, font=T.font(12))
        self.ai_req.set("")        # chỉ cho lần dịch lại này; phong cách lâu dài nằm ở tab SRT
        self.ai_req.pack(side="left", fill="x", expand=True, padx=8)
        self._show_page(0, sync=False)

    # ── Trang ─────────────────────────────────────────────────────────────
    def _page_range(self) -> range:
        a = self.page * self.PAGE
        return range(a, min(a + self.PAGE, len(self.cues)))

    def _sync_page(self) -> None:
        """Ghi ô nhập / ô tick của trang đang hiện về dữ liệu."""
        for row, i in zip(self._rows, self._page_range()):
            self.translated[i] = row["entry"].get()
            self.checked[i] = bool(row["var"].get())

    def _show_page(self, page: int, *, sync: bool = True) -> None:
        if sync:
            self._sync_page()
        self.page = max(0, min(self.pages - 1, page))
        idxs = self._page_range()
        for r, row in enumerate(self._rows):
            if r < len(idxs):
                i = idxs[r]
                c = self.cues[i]
                row["var"].set(self.checked[i])
                row["idx"].configure(text=f"{i + 1}\n{format_ts(c.start, '.')[:8]}")
                row["src"].configure(text=c.plain)
                row["entry"].delete(0, "end")
                row["entry"].insert(0, self.translated[i])
                for w, kw in row["widgets"]:
                    w.grid(**kw)
            else:
                for w, _kw in row["widgets"]:
                    w.grid_remove()
        if idxs:
            self.page_label.configure(text=f"Trang {self.page + 1}/{self.pages} · cue {idxs[0] + 1}–{idxs[-1] + 1}")
        self.prev_btn.configure(state="normal" if self.page > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.page < self.pages - 1 else "disabled")
        try:
            self.list._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    def _check_all(self, v: bool) -> None:
        self.checked = [v] * len(self.cues)
        for row in self._rows:
            row["var"].set(v)

    def _copy_source(self) -> None:
        self.clipboard_clear()
        self.clipboard_append("\n".join(f"{i + 1}. {c.plain}" for i, c in enumerate(self.cues)))
        self.status.configure(text="Đã copy toàn bộ văn bản gốc (đánh số) — dán vào công cụ dịch bên ngoài.")

    def _paste_lines(self) -> None:
        try:
            text = self.clipboard_get()
        except Exception:
            return
        import re

        lines = [re.sub(r"^\s*\d+[.)]\s*", "", ln).strip() for ln in text.splitlines() if ln.strip()]
        self._sync_page()
        n = 0
        for i, ln in enumerate(lines[:len(self.cues)]):
            self.translated[i] = ln
            n += 1
        self._show_page(self.page, sync=False)
        self.status.configure(text=f"Đã dán {n} dòng.")

    def _ai_selected(self) -> None:
        self._sync_page()
        idxs = [i for i, v in enumerate(self.checked) if v]
        if not idxs:
            messagebox.showinfo("Dịch AI", "Tick các cue cần dịch trước.")
            return
        from app.services.translate_router import retranslate

        cfg = self.window.app_state.subtitle
        st = self.window.app_state.settings
        previous = list(self.translated)
        redo = any(previous[i].strip() for i in idxs)     # đã có bản dịch → AI xem bản cũ để dịch lại
        req = self.ai_req.get().strip()
        style = cfg.translate_style
        self.status.configure(text=f"Đang {'dịch lại' if redo else 'dịch'} {len(idxs)} cue…")

        def work(cancel):
            return retranslate(self.cues, previous, idxs, settings=st, target=cfg.translate_target,
                               source_hint=cfg.translate_source, style=style, instruction=req,
                               log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if not self.winfo_exists():
                return
            if error:
                self.status.configure(text=f"Lỗi: {error}")
                return
            self._sync_page()           # giữ những gì người dùng gõ trong lúc chờ dịch
            for i, t in result.items():
                self.translated[i] = t
            self._show_page(self.page, sync=False)
            self.status.configure(text=f"Đã {'dịch lại' if redo else 'dịch'} AI {len(result)} cue — kiểm tra rồi bấm Lưu.")

        self.window.run_task("Dịch AI đã chọn", work, done)

    def _save(self) -> None:
        self._sync_page()
        out: list[Cue] = []
        for i, c in enumerate(self.cues):
            t = self.translated[i].strip() or c.text
            out.append(Cue(c.index, c.start, c.end, t, dict(c.meta)))
        p = self.item.srt_translated_path or str(SUBS_DIR / f"{self.item.stem}.dich.srt")
        save_srt(out, p)
        self.item.srt_translated_path = p
        self.window.app_state.mark_dirty()
        self.status.configure(text=f"Đã lưu {Path(p).name}")
        if self.on_saved:
            self.on_saved()
