"""Khối «Lời gốc» ở tab Lời thoại › «Nguồn & dịch» (trước là tab «Nhận diện» riêng): ngôn ngữ video, bộ nghe lời thoại
(Qwen3-ASR, chạy trên máy — video có phụ đề in sẵn thì máy tự đối chiếu chữ trên hình), nhận diện lại video đang chọn,
nhận diện từ file khác. «Ngôn ngữ video» dùng chung cho bước dịch. Video chưa có lời gốc thì bước Chuẩn bị tự nhận diện."""

from __future__ import annotations

import sys
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.core.languages import language_label
from app.core.state import SOURCE_BY_CODE, STT_LANGUAGES
from app.services.qwen_asr import INSTALL_GB
from app.services.srt_utils import load_srt, stats
from app.ui.progress_panel import friendly_error
from app.ui.widgets.common import ButtonRow, OptionRow, Section, StatusLabel


class SourcePanel(ctk.CTkFrame):
    """Dùng trong ``SRTModule`` (``module`` = module chủ: state, window, set_value, _loading)."""

    def __init__(self, master, module) -> None:
        super().__init__(master, fg_color="transparent")
        self.module = module
        self.window = module.window
        self.state = module.state
        sec = Section(self, "Lời gốc", info="Video chưa có lời gốc thì «Chuẩn bị» tự nghe lời thoại (Qwen3-ASR, chạy trên "
                                            "máy, không tốn lượt AI); video có phụ đề in sẵn thì máy tự đối chiếu chữ "
                                            "trên hình để sửa tên riêng nghe sai.")
        sec.pack(fill="x", pady=4)
        self.lang = OptionRow(sec.body, "Ngôn ngữ video", STT_LANGUAGES, on_change=self._lang_changed,
                              labels={c: language_label(c) for c in STT_LANGUAGES},
                              info="Ngôn ngữ người nói trong video — dùng cho cả nhận diện lẫn bước dịch. Lẫn hai thứ "
                                   "tiếng (vd Trung + Anh) thì để «Tự nhận diện»; chỉ chọn hẳn khi cả video một thứ "
                                   "tiếng mà máy nhận sai.")
        self.lang.pack(fill="x", pady=3)
        self.engine_status = StatusLabel(sec.body, "")
        self.engine_status.pack(fill="x", pady=(4, 0))
        self.qwen_btn = ButtonRow(sec.body, [(f"⬇ Cài bộ nghe lời thoại (một lần, ~{INSTALL_GB} GB)", self._install_qwen,
                                              T.primary_button())])
        ButtonRow(sec.body, [("Nhận diện lại video này", self._run_active, None),
                             ("Từ file khác…", self._run_file, None)]).pack(fill="x", pady=(4, 2))
        self.status = StatusLabel(sec.body, "")
        self.status.pack(fill="x", pady=(2, 0))

    # ── State → widget ────────────────────────────────────────────────────
    def refresh(self) -> None:
        self.lang.set(self.state.subtitle.stt_language)
        self._layout_engine()
        self.on_active_changed()

    def on_active_changed(self) -> None:
        it = self.state.active
        if it is None:
            self.status.info("Chưa có video được chọn.")
        elif it.srt_path and Path(it.srt_path).exists():
            try:
                self.status.ok(f"Lời gốc: {Path(it.srt_path).name} ({stats(load_srt(it.srt_path))})")
            except Exception:
                self.status.ok(f"Lời gốc: {Path(it.srt_path).name}")
        else:
            self.status.info("Chưa có lời gốc — bấm «Chuẩn bị» trên cùng (tự nhận diện) hoặc «Nhận diện lại video này».")

    def _layout_engine(self) -> None:
        from app.services.qwen_asr import is_installed

        if is_installed():
            self.engine_status.ok("✓ Bộ nghe lời thoại (Qwen3-ASR) đã cài — chạy trên máy, không tốn lượt AI.")
            self.qwen_btn.pack_forget()
        elif getattr(sys, "frozen", False):
            # bản .exe không tự cài được Qwen (cần Python) → không mời bấm nút vô tác dụng
            self.engine_status.warn("Chưa có bộ nghe lời thoại (Qwen3-ASR). Bản .exe chưa tự cài được — chép thư mục "
                                    "models\\qwen_asr_env + models\\hf từ máy đã cài.")
            self.qwen_btn.pack_forget()
        else:
            self.engine_status.warn("Chưa cài bộ nghe lời thoại (Qwen3-ASR) — cài một lần rồi dùng mãi, không cần mạng.")
            if not self.qwen_btn.winfo_ismapped():
                self.qwen_btn.pack(fill="x", pady=(4, 0), after=self.engine_status)

    def _install_qwen(self) -> None:
        from app.ui.local_ai import install_qwen

        def done(ok: bool, error: Exception | None) -> None:
            if not self.winfo_exists():
                return
            if not ok:
                self.engine_status.error(f"Cài Qwen3-ASR thất bại: {error or 'xem log'}")
                return
            self._layout_engine()

        if install_qwen(self.window, done):
            self.engine_status.warn("Đang cài bộ nghe lời thoại (vài chục phút)… xem log")

    def _lang_changed(self, code: str) -> None:
        """Một ô ngôn ngữ cho cả nhận diện lẫn dịch."""
        m = self.module
        m.set_value(self.state.subtitle, "stt_language", code, preview=False)
        m.set_value(self.state.subtitle, "translate_source", SOURCE_BY_CODE.get(code, "Tự nhận diện"), preview=False)
        m.refresh_source_hint()

    # ── Hành động ────────────────────────────────────────────────────────
    def _run_active(self) -> None:
        item = self.state.active
        if item is None:
            messagebox.showinfo("Edit Studio", "Hãy nạp và chọn một video trước.")
            return
        has_srt = bool(item.srt_path and Path(item.srt_path).exists())
        if has_srt and not messagebox.askyesno(
                "Edit Studio", "Video này đã có lời thoại. Nhận diện lại sẽ thay bản cũ (cả chỗ đã sửa tay) và phải "
                               "dịch lại. Tiếp tục?"):
            return
        pipe = self.window.pipeline_for(item)
        self.status.warn("Đang nhận diện… (xem log)")

        def work(cancel):
            return pipe.transcribe(item, progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=cancel,
                                   force=True)

        def done(result, error):
            if error:
                self.status.error(f"Lỗi: {friendly_error(str(error))}")
            else:
                self.status.ok(f"Xong {len(result)} câu — tiếp: «Dịch phụ đề» bên dưới (hoặc bấm nút chính trên cùng).")
                self.window.mark_changed(item)
                self.window.queue_update(item)
                self.window.preview.schedule_refresh(0)
                self.state.mark_dirty()
                self.window.add_notification("Nhận diện xong", f"{item.name}: {len(result)} câu", "ok")
                for k in ("srt", "tts"):
                    if k in self.window.modules:
                        self.window.modules[k].on_active_changed()

        self.window.run_task("Nhận diện", work, done)

    def _run_file(self) -> None:
        """Nhận diện một file audio / video bất kỳ → SRT trong output/subs, hỏi gán cho video đang chọn."""
        p = filedialog.askopenfilename(title="Chọn file audio/video",
                                       filetypes=[("Media", "*.mp4 *.mkv *.mov *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.webm"),
                                                  ("Tất cả", "*.*")])
        if not p:
            return
        from app.core.state import VideoItem

        tmp = VideoItem(path=p)
        pipe = self.window.pipeline
        self.status.warn(f"Đang nhận diện {Path(p).name}…")

        def work(cancel):
            return pipe.transcribe(tmp, progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=cancel,
                                   source_path=p)

        def done(result, error):
            if error:
                self.status.error(f"Lỗi: {friendly_error(str(error))}")
            else:
                self.status.ok(f"Đã tạo {Path(tmp.srt_path).name} ({len(result)} câu) trong output/subs")
                if messagebox.askyesno("Edit Studio", "Gán phụ đề vừa tạo cho video đang chọn?") and self.state.active:
                    self.window.pipeline.attach_srt(self.state.active, tmp.srt_path)
                    self.window.mark_changed(self.state.active)
                    self.state.mark_dirty()
                    self.module.on_active_changed()
                    self.window.preview.schedule_refresh(0)

        self.window.run_task("Nhận diện file", work, done)
