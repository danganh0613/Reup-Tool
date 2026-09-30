"""Hộp thoại phụ đề: Xem/Sửa SRT (tìm & thay), Dịch thủ công (bảng cue), Dự án dịch dang dở."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import button
from app.config.paths import SUBS_DIR
from app.core.state import VideoItem
from app.services.srt_utils import Cue, format_ts, load_srt, parse_srt, save_srt, to_srt
from app.services.translate_router import Checkpoint


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
        self.bind("<Control-s>", lambda _e: self._save())
        self.bind("<Control-f>", lambda _e: self._find(1))
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
    """Bảng dịch tay: mỗi cue một ô nhập bản dịch; có thể dịch AI các cue đã chọn."""

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
                self.translated[i] = c.text
        self.checks: list[ctk.BooleanVar] = []
        self.entries: list[ctk.CTkEntry] = []

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
        for i, c in enumerate(self.cues):
            var = ctk.BooleanVar(value=False)
            self.checks.append(var)
            ctk.CTkCheckBox(self.list, text="", variable=var, width=24).grid(row=i, column=0, padx=(0, 4), pady=2)
            ctk.CTkLabel(self.list, text=f"{i + 1}\n{format_ts(c.start, '.')[:8]}", font=T.mono(10), text_color=T.TEXT_MUTED,
                         width=80, anchor="w", justify="left").grid(row=i, column=1, sticky="w", pady=2)
            ctk.CTkLabel(self.list, text=c.plain, font=T.font(11), anchor="w", justify="left", wraplength=460,
                         text_color=T.TEXT_DIM).grid(row=i, column=2, sticky="ew", padx=(0, 8), pady=2)
            e = ctk.CTkEntry(self.list, height=32, font=T.font(12))
            e.insert(0, self.translated[i].replace("\n", " "))
            e.grid(row=i, column=3, sticky="ew", pady=2)
            self.entries.append(e)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        self.status = ctk.CTkLabel(foot, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        button(foot, "Lưu bản dịch", icon_name="save", variant="primary", command=self._save).pack(side="right")
        button(foot, "Dịch AI đã chọn", icon_name="sparkle", command=self._ai_selected).pack(side="right", padx=8)
        ctk.CTkButton(foot, text="Đóng", width=90, height=36, command=self.destroy, **T.secondary_button()).pack(side="right")

    def _check_all(self, v: bool) -> None:
        for c in self.checks:
            c.set(v)

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
        n = 0
        for i, ln in enumerate(lines[:len(self.entries)]):
            self.entries[i].delete(0, "end")
            self.entries[i].insert(0, ln)
            n += 1
        self.status.configure(text=f"Đã dán {n} dòng.")

    def _ai_selected(self) -> None:
        idxs = [i for i, v in enumerate(self.checks) if v.get()]
        if not idxs:
            messagebox.showinfo("Dịch AI", "Tick các cue cần dịch trước.")
            return
        from app.services.translate_router import translate_cues

        subset = [self.cues[i] for i in idxs]
        cfg = self.window.app_state.subtitle
        st = self.window.app_state.settings
        self.status.configure(text=f"Đang dịch {len(idxs)} cue…")

        def work(cancel):
            return translate_cues(subset, settings=st, target=cfg.translate_target, source_hint=cfg.translate_source,
                                  style=cfg.translate_style, log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if error:
                self.status.configure(text=f"Lỗi: {error}")
                return
            for i, c in zip(idxs, result):
                self.entries[i].delete(0, "end")
                self.entries[i].insert(0, c.plain)
            self.status.configure(text=f"Đã dịch AI {len(idxs)} cue — kiểm tra rồi bấm Lưu.")

        self.window.run_task("Dịch AI đã chọn", work, done)

    def _save(self) -> None:
        out: list[Cue] = []
        for i, c in enumerate(self.cues):
            t = self.entries[i].get().strip() or c.text
            out.append(Cue(c.index, c.start, c.end, t, dict(c.meta)))
        p = self.item.srt_translated_path or str(SUBS_DIR / f"{self.item.stem}.dich.srt")
        save_srt(out, p)
        self.item.srt_translated_path = p
        self.window.app_state.mark_dirty()
        self.status.configure(text=f"Đã lưu {Path(p).name}")
        if self.on_saved:
            self.on_saved()


class PendingTranslationsDialog(ctk.CTkToplevel):
    def __init__(self, window, module) -> None:
        super().__init__(window)
        self.window = window
        self.module = module
        self.title("Dự án dịch dang dở")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(900, sw - 60)}x{min(600, sh - 100)}+40+40")
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=8)
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        ctk.CTkLabel(head, text="Dự án dịch dang dở", font=T.font(15, "bold"), text_color=T.ACCENT, anchor="w").pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(head, text="Các batch đã dịch được lưu vào checkpoint. Chọn một dự án để chỉ dịch tiếp những cue còn thiếu.",
                     font=T.font(11), text_color=T.TEXT_DIM, anchor="w").pack(fill="x", padx=12, pady=(2, 10))
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew", padx=10)
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        self.count = ctk.CTkLabel(foot, text="", font=T.font(11), text_color=T.TEXT_MUTED)
        self.count.pack(side="left")
        ctk.CTkButton(foot, text="Dọn log cũ", width=100, height=32, command=self._clean, **T.secondary_button()).pack(side="left", padx=8)
        ctk.CTkButton(foot, text="Đóng", width=90, height=32, command=self.destroy, **T.primary_button()).pack(side="right")
        self.refresh()

    def refresh(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        items = Checkpoint.list_pending(SUBS_DIR)
        self.count.configure(text=f"{len(items)} dự án có thể tiếp tục")
        if not items:
            ctk.CTkLabel(self.list, text="Không có dự án nào đang dang dở.\nMột dự án xuất hiện ở đây khi còn cue chưa dịch "
                                         "trong file *.srt.progress.json.", font=T.font(12), text_color=T.TEXT_DIM,
                         justify="left").pack(pady=30)
            return
        for it in items:
            card = ctk.CTkFrame(self.list, fg_color=T.BG_CARD, corner_radius=8)
            card.pack(fill="x", pady=4)
            ctk.CTkLabel(card, text=f"{it['srt'].name} → {it['target']} · {it['done']}/{it['total']} cue · "
                                    f"{time.strftime('%d/%m %H:%M', time.localtime(it['updated']))}",
                         font=T.font(12, "bold"), anchor="w").pack(side="left", padx=10, pady=10)
            ctk.CTkButton(card, text="Tiếp tục dịch", width=110, height=32, command=lambda i=it: self._resume(i),
                          **T.primary_button()).pack(side="right", padx=10)

    def _resume(self, it: dict) -> None:
        # gán SRT này cho video đang chọn (nếu có) rồi dịch tiếp
        item = self.window.app_state.active
        if item is None:
            messagebox.showinfo("Edit Studio", "Chọn video trong hàng đợi trước.")
            return
        item.srt_path = str(it["srt"])
        self.window.app_state.subtitle.translate_target = it["target"]
        self.destroy()
        self.module.refresh()
        self.module._translate_now()

    def _clean(self) -> None:
        n = 0
        for p in SUBS_DIR.glob("*.progress.json"):
            try:
                if time.time() - p.stat().st_mtime > 7 * 86400:
                    p.unlink()
                    n += 1
            except OSError:
                pass
        self.refresh()
        self.count.configure(text=f"Đã dọn {n} checkpoint cũ hơn 7 ngày")
