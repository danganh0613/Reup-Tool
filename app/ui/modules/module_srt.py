"""Module SRT: tệp phụ đề (chọn/sửa/xuất/mở/xóa cache), dịch AI (xoay key), dự án dịch dang dở, dịch thủ công, tuỳ chọn."""

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
from app.ui.widgets.common import ButtonRow, OptionRow, Section, StatusLabel, SwitchRow, hint, pack_rows
from app.core.platform import open_path

SOURCE_LANGS = ["Tự nhận diện", "Tiếng Việt", "English", "中文", "日本語", "한국어", "Français", "Deutsch", "Español",
                "ไทย", "Bahasa Indonesia", "Русский"]


class SRTModule(BaseModule):
    key = "srt"
    title = "SRT"
    icon = "📄"
    section = "subtitle"

    def build(self) -> None:
        cfg = self.state.subtitle
        s = self.setter

        sec = Section(self, "Tệp phụ đề")
        sec.pack(fill="x", pady=4)
        ButtonRow(sec.body, [("Chọn SRT", self._pick_srt, None), ("Sửa SRT", self.open_editor, T.primary_button())]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("Xuất SRT", self._export_srt, T.primary_button()), ("Mở SRT", self._open_srt, None)]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("Xóa cache SRT", self._clear_cache, T.danger_button())]).pack(fill="x", pady=2)
        self.file_status = StatusLabel(sec.body, "Chưa có SRT — nhập file hoặc tạo bằng tab STT.")
        self.file_status.pack(fill="x", pady=(4, 0))

        sec = Section(self, "Dịch")
        sec.pack(fill="x", pady=4)
        hint(sec.body, "Giữ nguyên timestamp của SRT đã import hoặc SRT do STT tạo.").pack(fill="x")
        self.src_lang = OptionRow(sec.body, "Ngôn ngữ nguồn", SOURCE_LANGS, on_change=s(cfg, "translate_source", preview=False))
        self.target = OptionRow(sec.body, "Dịch sang", TRANSLATE_TARGETS, on_change=s(cfg, "translate_target", preview=False))
        pack_rows(sec.body, [self.src_lang, self.target])
        # một ô duy nhất cho phong cách dịch + yêu cầu khi dịch lại (dùng chung với tab Duyệt / bảng dịch từng câu)
        row = ctk.CTkFrame(sec.body, fg_color="transparent")
        row.pack(fill="x", pady=3)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text="Phong cách dịch", font=T.font(12), text_color=T.TEXT_DIM, anchor="w", width=120).grid(
            row=0, column=0, sticky="w")
        self.style = ctk.CTkComboBox(row, values=RETRANSLATE_PRESETS, height=34, font=T.font(12),
                                     command=lambda _v: self._style_changed())
        self.style.grid(row=0, column=1, sticky="ew")
        self.style.bind("<FocusOut>", lambda _e: self._style_changed())
        self.style.bind("<Return>", lambda _e: self._style_changed())
        ButtonRow(sec.body, [("Dịch phụ đề", self._translate_now, T.primary_button()),
                             ("Dịch lại (bản mới)", self._retranslate_all, None)]).pack(fill="x", pady=(6, 2))
        hint(sec.body, "Dịch chưa hay? Chọn / gõ yêu cầu vào ô trên (vd «xưng anh–em, văn nói») rồi «Dịch lại» — AI xem "
                       "bản cũ để biết bạn chưa ưng chỗ nào («Dịch phụ đề» lần nữa chỉ lấy lại bản cũ đã nhớ). Giọng lồng "
                       "tự tạo lại theo bản mới. Xoay vòng key & nhà cung cấp khi hết quota; mỗi lô dịch đều được lưu.").pack(fill="x")
        ButtonRow(sec.body, [("Dự án dịch dang dở", self._pending_projects, None)]).pack(fill="x", pady=(4, 2))
        self.tr_enable = SwitchRow(sec.body, "Tự dịch khi render (nếu chọn ghi Sub đã dịch)", on_change=s(cfg, "translate_enable", preview=False))
        self.tr_enable.pack(fill="x", pady=(4, 0))
        self.tr_status = StatusLabel(sec.body, "")
        self.tr_status.pack(fill="x")

        sec = Section(self, "Sửa / dịch từng câu")
        sec.pack(fill="x", pady=4)
        ButtonRow(sec.body, [("Mở bảng dịch từng câu", self._manual_translate, T.primary_button())]).pack(fill="x", pady=2)
        hint(sec.body, "Gõ bản dịch từng câu, dán bản dịch từ nơi khác, hoặc tick vài câu dở rồi «AI dịch / dịch lại đã "
                       "chọn». Timestamp giữ nguyên. Đang dùng chế độ Duyệt thì sửa ngay ở tab Duyệt (vừa xem video).").pack(fill="x")

        sec = Section(self, "Tùy chọn nâng cao")
        sec.pack(fill="x", pady=4)
        self.reflow = SwitchRow(sec.body, "AI tối ưu câu + gộp timeline", on_change=s(cfg, "ai_reflow", preview=False))
        self.reflow.pack(fill="x", pady=2)
        hint(sec.body, "Thử nghiệm — AI được phép diễn đạt lại để câu liền mạch hơn (số cue giữ nguyên).").pack(fill="x")
        hint(sec.body, "Cấu hình Nhà cung cấp, Model và API key tại: Cài đặt (góc trên) → API Dịch Thuật.").pack(fill="x", pady=(4, 0))

    def refresh(self) -> None:
        cfg = self.state.subtitle
        self._loading = True
        try:
            self.src_lang.set(cfg.translate_source if cfg.translate_source in SOURCE_LANGS else SOURCE_LANGS[0])
            self.target.set(cfg.translate_target)
            self.style.set(cfg.translate_style or "")
            self.tr_enable.set(cfg.translate_enable)
            self.reflow.set(cfg.ai_reflow)
        finally:
            self._loading = False
        self.on_active_changed()

    def on_active_changed(self) -> None:
        it = self.state.active
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

    def _retranslate_all(self) -> None:
        it = self._need_item()
        if it is None:
            return
        if not it.srt_path or not Path(it.srt_path).exists():
            messagebox.showinfo("Edit Studio", "Chưa có phụ đề gốc — nhận diện (STT) hoặc chọn file SRT trước.")
            return
        self._style_changed()
        req = self.state.subtitle.translate_style.strip()
        pipe = self.window.pipeline_for(it)
        self.tr_status.warn("Đang dịch lại… (xem log)" + (f" — «{req}»" if req else ""))

        def work(cancel):
            return pipe.retranslate(it, instruction=req, progress=self.window.set_busy_progress,
                                    log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if error:
                self.tr_status.error(f"Lỗi: {str(error).splitlines()[0][:200]}")
                return
            self.state.mark_dirty()
            self._after_change(it)
            self.window.add_notification("Dịch lại xong", f"{it.name}: {len(result)} câu", "ok")

        self.window.run_task("Dịch lại phụ đề", work, done)

    def _style_changed(self) -> None:
        if self._loading:
            return
        v = self.style.get().strip()
        if v != self.state.subtitle.translate_style:
            self.set_value(self.state.subtitle, "translate_style", v, preview=False)

    def _pending_projects(self) -> None:
        from app.ui.widgets.srt_editor import PendingTranslationsDialog

        PendingTranslationsDialog(self.window, self)

    def _manual_translate(self) -> None:
        it = self._need_item()
        if it is None:
            return
        if not it.srt_path or not Path(it.srt_path).exists():
            messagebox.showinfo("Dịch thủ công", "Chưa có SRT để dịch.\nHãy chọn file SRT, hoặc tạo phụ đề bằng tab STT trước.")
            return
        from app.ui.widgets.srt_editor import ManualTranslateDialog

        ManualTranslateDialog(self.window, it, on_saved=lambda: self._after_change(it))
