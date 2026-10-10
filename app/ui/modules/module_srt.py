"""Module Lời thoại: sửa từng câu (+ Nhờ AI sửa), tệp phụ đề (chọn/sửa/xuất/mở/xóa cache), dịch AI, dán bản dịch ngoài."""

from __future__ import annotations

import shutil
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.config.paths import SUBS_DIR, VOICE_DIR
from app.core.state import TRANSLATE_TARGETS
from app.services.srt_utils import load_srt, stats
from app.services.translate_router import RETRANSLATE_PRESETS
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.common import ButtonRow, OptionRow, Section, StatusLabel, SwitchRow, hint, pack_rows, row_label
from app.core.platform import open_path

SOURCE_LANGS = ["Tự nhận diện", "Tiếng Việt", "English", "中文", "日本語", "한국어", "Français", "Deutsch", "Español",
                "ไทย", "Bahasa Indonesia", "Русский"]


class SRTModule(BaseModule):
    key = "srt"
    title = "Lời thoại"
    icon = "📄"
    section = "subtitle"

    def build(self) -> None:
        cfg = self.state.subtitle
        s = self.setter

        # ── Lời thoại của video đang chọn: sửa ngay, gắn với khung xem trước (nơi DUY NHẤT sửa từng câu) ──
        from app.ui.widgets.cue_editor import CueEditor

        sec = Section(self, "Lời thoại của video đang chọn")
        sec.pack(fill="x", pady=4)
        self.editor = CueEditor(sec.body, self.window, on_saved=lambda it: (self.on_active_changed(),
                                                                             self.window.queue_update(it)))
        self.editor.pack(fill="x")

        sec = Section(self, "Tệp phụ đề")
        sec.pack(fill="x", pady=4)
        ButtonRow(sec.body, [("Chọn file SRT", self._pick_srt, None), ("Xuất SRT", self._export_srt, None)]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("Sửa thời gian…", self.open_editor, None), ("Dán bản dịch ngoài…", self._manual_translate, None)]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("Mở bằng app khác", self._open_srt, None), ("Xóa cache SRT", self._clear_cache, T.danger_button())]).pack(fill="x", pady=2)
        self.file_status = StatusLabel(sec.body, "Chưa có SRT — nhập file hoặc tạo bằng tab STT.")
        self.file_status.pack(fill="x", pady=(4, 0))

        sec = Section(self, "Dịch", info="AI Gemini dịch 2 bước: tóm tắt nội dung + lấy thuật ngữ, dịch sát nghĩa, rồi "
                                         "tự đọc lại và viết cho tự nhiên. Có lồng tiếng thì tự rút gọn câu đọc không "
                                         "kịp. Khoảng 10–15 lượt gọi AI mỗi video ngắn; lỗi giữa chừng thì «Thử lại» "
                                         "dịch tiếp phần còn lại. Key và model: Cài đặt › AI dùng API key.")
        sec.pack(fill="x", pady=4)
        self.src_lang = OptionRow(sec.body, "Ngôn ngữ gốc", SOURCE_LANGS, on_change=s(cfg, "translate_source", preview=False))
        self.target = OptionRow(sec.body, "Dịch sang", TRANSLATE_TARGETS, on_change=s(cfg, "translate_target", preview=False))
        pack_rows(sec.body, [self.src_lang, self.target])
        # một ô duy nhất cho phong cách dịch (dùng chung với tab Duyệt / bảng dịch từng câu)
        row = ctk.CTkFrame(sec.body, fg_color="transparent")
        row.pack(fill="x", pady=3)
        row.grid_columnconfigure(1, weight=1)
        row_label(row, "Phong cách", "Ghi chú cho AI về giọng văn, vd «xưng anh – em, văn nói». Đổi phong cách rồi bấm "
                                     "«Dịch phụ đề» để dịch lại theo phong cách mới.").grid(row=0, column=0, sticky="w")
        self.style = ctk.CTkComboBox(row, values=RETRANSLATE_PRESETS, height=34, font=T.font(12),
                                     command=lambda _v: self._style_changed())
        self.style.grid(row=0, column=1, sticky="ew")
        self.style.bind("<FocusOut>", lambda _e: self._style_changed())
        self.style.bind("<Return>", lambda _e: self._style_changed())
        ButtonRow(sec.body, [("Dịch phụ đề", self._translate_now, T.primary_button())]).pack(fill="x", pady=(6, 2))
        hint(sec.body, "Dịch chưa ưng? Gõ yêu cầu vào ô «Nhờ AI sửa» ở trên.").pack(fill="x")
        self.tr_enable = SwitchRow(sec.body, "Tự dịch khi chuẩn bị video", on_change=s(cfg, "translate_enable", preview=False),
                                   info="Bật: bước «Chuẩn bị» tự dịch khi phụ đề hoặc lồng tiếng cần bản dịch.")
        self.tr_enable.pack(fill="x", pady=(4, 0))
        self.tr_status = StatusLabel(sec.body, "")
        self.tr_status.pack(fill="x")

    def refresh(self) -> None:
        cfg = self.state.subtitle
        self._loading = True
        try:
            self.src_lang.set(cfg.translate_source if cfg.translate_source in SOURCE_LANGS else SOURCE_LANGS[0])
            self.target.set(cfg.translate_target)
            self.style.set(cfg.translate_style or "")
            self.tr_enable.set(cfg.translate_enable)
        finally:
            self._loading = False
        self.on_active_changed()

    def on_shown(self) -> None:
        super().on_shown()
        self.editor.start_tick()

    def on_hidden(self) -> None:
        self.editor.stop_tick()
        self._ask_save()

    def _ask_save(self) -> None:
        if self.editor.dirty() and messagebox.askyesno("Edit Studio", "Lưu các câu lời thoại vừa sửa?"):
            self.editor.save()

    def on_active_changed(self) -> None:
        it = self.state.active
        if self.editor.item is not it:
            self._ask_save()
            self.editor.load(it)
        if it is None:
            self.file_status.info("Chưa có video được chọn.")
            self.tr_status.info("")
            return
        if it.srt_path and Path(it.srt_path).exists():
            try:
                self.file_status.ok(f"{Path(it.srt_path).name} · {stats(load_srt(it.srt_path))}")
            except Exception:
                self.file_status.ok(Path(it.srt_path).name)
        else:
            self.file_status.info("Chưa có SRT — nhập file hoặc tạo bằng tab STT.")
        if it.srt_translated_path and Path(it.srt_translated_path).exists():
            self.tr_status.ok(f"Bản dịch: {Path(it.srt_translated_path).name}")
        else:
            self.tr_status.info("Chưa có bản dịch.")

    # ── Tệp ──────────────────────────────────────────────────────────────
    def _need_item(self):
        it = self.state.active
        if it is None:
            messagebox.showinfo("Edit Studio", "Hãy nạp và chọn một video trước.")
        return it

    def _pick_srt(self) -> None:
        it = self._need_item()
        if it is None:
            return
        p = filedialog.askopenfilename(filetypes=[("Phụ đề", "*.srt *.vtt *.txt"), ("Tất cả", "*.*")],
                                       initialdir=str(Path(it.path).parent))
        if not p:
            return
        it.srt_path, it.srt_translated_path = p, None
        self.state.mark_dirty()
        self._after_change(it)

    def _after_change(self, it) -> None:
        if it is self.state.active and not self.editor.dirty():
            self.editor.load(it)                    # file SRT / bản dịch đổi → nạp lại danh sách câu
        self.on_active_changed()
        self.window.queue_update(it)
        self.window.preview.schedule_refresh(0)
        for k in ("stt", "tts"):
            if k in self.window.modules:
                self.window.modules[k].on_active_changed()

    def open_editor(self) -> None:
        it = self._need_item()
        if it is None:
            return
        from app.ui.widgets.srt_editor import SRTEditorDialog

        SRTEditorDialog(self.window, it, on_saved=lambda: self._after_change(it))

    def _export_srt(self) -> None:
        it = self._need_item()
        if it is None:
            return
        src = it.srt_translated_path if it.srt_translated_path and Path(it.srt_translated_path).exists() else it.srt_path
        if not src or not Path(src).exists():
            messagebox.showinfo("Edit Studio", "Chưa có SRT để xuất.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".srt", initialfile=f"{it.stem}.srt",
                                         filetypes=[("SRT", "*.srt")])
        if p:
            shutil.copyfile(src, p)
            self.window.log(f"Đã xuất {p}")

    def _open_srt(self) -> None:
        it = self.state.active
        if it and it.srt_path and Path(it.srt_path).exists():
            open_path(it.srt_path)

    def _clear_cache(self) -> None:
        it = self._need_item()
        if it is None:
            return
        if not messagebox.askyesno("Xóa cache SRT", "Xoá SRT đã nhận diện/dịch và track giọng của video này "
                                                   "(trong output/subs, output/voice)?"):
            return
        removed = 0
        for p in (it.srt_path, it.srt_translated_path, it.voice_path):
            if p and Path(p).exists() and (Path(p).parent in (SUBS_DIR, VOICE_DIR)):
                try:
                    Path(p).unlink()
                    removed += 1
                except OSError:
                    pass
        it.srt_path = it.srt_translated_path = it.voice_path = None
        self.state.mark_dirty()
        self._after_change(it)
        self.window.log(f"Đã xoá {removed} file cache của {it.name}.")

    # ── Dịch ─────────────────────────────────────────────────────────────
    def _translate_now(self) -> None:
        it = self._need_item()
        if it is None:
            return
        if not it.srt_path or not Path(it.srt_path).exists():
            messagebox.showinfo("Edit Studio", "Chưa có phụ đề gốc — nhận diện (STT) hoặc chọn file SRT trước.")
            return
        pipe = self.window.pipeline_for(it)
        self.tr_status.warn("Đang dịch… (xem log)")

        def work(cancel):
            return pipe.translate(it, progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if error:
                self.tr_status.error(f"Lỗi: {error}")
            else:
                self.state.mark_dirty()
                self._after_change(it)
                self.window.add_notification("Dịch xong", f"{it.name} → {self.state.subtitle.translate_target}", "ok")

        self.window.run_task("Dịch phụ đề", work, done)

    def _style_changed(self) -> None:
        if self._loading:
            return
        v = self.style.get().strip()
        if v != self.state.subtitle.translate_style:
            self.set_value(self.state.subtitle, "translate_style", v, preview=False)

    def _manual_translate(self) -> None:
        it = self._need_item()
        if it is None:
            return
        if not it.srt_path or not Path(it.srt_path).exists():
            messagebox.showinfo("Dịch thủ công", "Chưa có SRT để dịch.\nHãy chọn file SRT, hoặc tạo phụ đề bằng tab STT trước.")
            return
        from app.ui.widgets.srt_editor import ManualTranslateDialog

        ManualTranslateDialog(self.window, it, on_saved=lambda: self._after_change(it))
