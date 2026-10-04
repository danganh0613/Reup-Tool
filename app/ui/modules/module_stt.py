"""Module STT: tạo SRT từ video trong hàng đợi (Whisper local / Gemini cloud) hoặc từ file media bất kỳ."""

from __future__ import annotations

import sys
from pathlib import Path
from tkinter import filedialog, messagebox

from app.config import theme as T
from app.core.languages import language_label
from app.core.state import STT_DEVICES, STT_ENGINES, STT_LANGUAGES, STT_MODELS, SRT_SOURCES
from app.services.srt_utils import load_srt, stats
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.common import ButtonRow, OptionRow, Section, StatusLabel, SwitchRow, hint, pack_rows


class STTModule(BaseModule):
    key = "stt"
    title = "STT"
    icon = "🎙"
    section = "subtitle"

    def build(self) -> None:
        cfg = self.state.subtitle
        s = self.setter
        hint(self, "Tạo SRT từ video đang chọn trong hàng đợi.").pack(fill="x", pady=(0, 4))

        sec = Section(self, "Dịch vụ")
        sec.pack(fill="x", pady=4)
        self.engine = OptionRow(sec.body, "Nhận dạng", STT_ENGINES, on_change=self._engine_changed)
        self.engine_hint = hint(sec.body, "Model chạy trên máy, không cần mạng")
        self.model = OptionRow(sec.body, "Model Whisper", STT_MODELS, on_change=s(cfg, "stt_model", preview=False))
        self.lang = OptionRow(sec.body, "Ngôn ngữ", STT_LANGUAGES, on_change=s(cfg, "stt_language", preview=False),
                              labels={c: language_label(c) for c in STT_LANGUAGES})
        self.device = OptionRow(sec.body, "Thiết bị", STT_DEVICES, on_change=s(cfg, "stt_device", preview=False),
                                labels={"auto": "Tự động (GPU nếu có)", "cpu": "CPU (bộ xử lý)", "cuda": "GPU NVIDIA (CUDA)"})
        self.weak = SwitchRow(sec.body, "PC yếu", on_change=s(cfg, "weak_pc", preview=False))
        self.auto = SwitchRow(sec.body, "Tự nhận diện khi render nếu chưa có SRT", on_change=self._auto_changed)
        self.engine.pack(fill="x", pady=3)
        self.engine_hint.pack(fill="x")
        pack_rows(sec.body, [self.model, self.lang, self.device, self.weak, self.auto])
        hint(sec.body, "tiny/base: nhanh, hợp tiếng Anh · small: khá · large-v3-turbo: chính xác gần large mà nhanh gần small — "
                       "khuyên dùng khi có GPU, nhất là tiếng Trung/Nhật. Gemini (cloud): không cần GPU, cần API key, "
                       "nhận diện theo câu có dấu câu.").pack(fill="x")
        self.cuda_hint = hint(sec.body, "")
        self.cuda_hint.pack(fill="x", pady=(4, 0))
        cuda_label = "⬇ Tải CUDA runtime cho Whisper GPU (~1.3 GB, một lần)" if getattr(sys, "frozen", False) \
            else "⬇ Cài CUDA runtime cho Whisper GPU (~1.5 GB)"
        self.cuda_btn = ButtonRow(sec.body, [(cuda_label, self._install_cuda, None)])
        self.cuda_btn.pack(fill="x", pady=(2, 0))
        self._update_cuda_hint()

        sec = Section(self, "Chạy")
        sec.pack(fill="x", pady=4)
        ButtonRow(sec.body, [("Tạo SRT từ Video trong hàng đợi", self._run_active, T.primary_button())]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("🎞 Nhận dạng từ File Media (Audio/Video)…", self._run_file, None)]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("Xem / sửa SRT", self._open_srt, None),
                             ("⬇ Tải trước model Whisper", self._download_model, None)]).pack(fill="x", pady=2)
        self.status = StatusLabel(sec.body, "Chưa có file SRT — tạo xong rồi bấm «Xem / sửa SRT».")
        self.status.pack(fill="x", pady=(4, 0))

    def refresh(self) -> None:
        cfg = self.state.subtitle
        self._loading = True
        try:
            self.engine.set(cfg.stt_engine if cfg.stt_engine in STT_ENGINES else STT_ENGINES[0])
            self.model.set(cfg.stt_model)
            self.lang.set(cfg.stt_language)
            self.device.set(cfg.stt_device)
            self.weak.set(cfg.weak_pc)
            self.auto.set(cfg.srt_source == SRT_SOURCES[0])
        finally:
            self._loading = False
        self._layout_engine()
        self.on_active_changed()

    def on_active_changed(self) -> None:
        it = self.state.active
        if it is None:
            self.status.info("Chưa có video được chọn.")
        elif it.srt_path and Path(it.srt_path).exists():
            try:
                self.status.ok(f"SRT: {Path(it.srt_path).name} ({stats(load_srt(it.srt_path))})")
            except Exception:
                self.status.ok(f"SRT: {Path(it.srt_path).name}")
        else:
            self.status.info("Chưa có file SRT — tạo xong rồi bấm «Xem / sửa SRT».")

    def _update_cuda_hint(self, ready: bool | None = None) -> None:
        """Dò GPU (nvidia-smi/PowerShell) + nạp thử DLL CUDA trong nền — không làm đơ giao diện khi mở module."""
        def work():
            from app.services.sysinfo import gpu_name
            from app.services.whisper_stt import cuda_ready

            name = gpu_name()
            if "NVIDIA" not in name.upper():
                return name, False, False
            return name, True, (cuda_ready() if ready is None else ready)

        self.window.run_bg(work, self._show_cuda_hint)

    def _show_cuda_hint(self, result, error) -> None:
        if error or result is None:
            return
        name, nvidia, ready = result
        if not nvidia:
            self.cuda_hint.configure(text="Không có GPU NVIDIA → Whisper chạy trên CPU (int8).")
            self.cuda_btn.pack_forget()
            return
        if ready:
            self.cuda_hint.configure(text=f"✓ CUDA sẵn sàng — Whisper chạy trên {name}.")
            self.cuda_btn.pack_forget()
        elif getattr(sys, "frozen", False):
            self.cuda_hint.configure(text=f"⚠ Có {name} nhưng thiếu cuBLAS/cuDNN nên Whisper đang chạy CPU. "
                                          "Bấm nút dưới để tải về (một lần, ~1.3 GB, cần Internet) — hoặc cài bản Setup đủ (có sẵn CUDA).")
        else:
            self.cuda_hint.configure(text=f"⚠ Có {name} nhưng thiếu cuBLAS/cuDNN nên Whisper đang chạy CPU. "
                                          "Bấm nút dưới để cài (pip, không cần CUDA Toolkit).")

    def _install_cuda(self) -> None:
        from app.services.whisper_stt import install_cuda_runtime

        self.cuda_hint.configure(text="Đang tải cuBLAS + cuDNN… xem Nhật ký / thanh trạng thái (vài phút).")

        def done(result, error):
            if error:
                self.cuda_hint.configure(text=f"✖ Tải CUDA lỗi: {str(error)[:160]} — kiểm tra mạng rồi bấm lại (tải tiếp phần dở).")
                return
            self._update_cuda_hint(bool(result))
            if not result:
                self.cuda_hint.configure(text="⚠ Đã tải nhưng chưa nạp được cuBLAS/cuDNN — mở lại phần mềm rồi thử lại.")

        self.window.run_task("Tải CUDA runtime", lambda cancel: install_cuda_runtime(self.window.log_bg, cancel), done)

    def _download_model(self) -> None:
        from app.services.whisper_stt import download_whisper_model

        size = self.state.subtitle.stt_model
        self.status.info(f"Đang tải model Whisper '{size}'… (xem Nhật ký)")

        def done(result, error):
            if error:
                self.status.error(f"Tải model lỗi: {str(error)[:200]}")
            else:
                self.status.ok(f"Model '{size}' đã có sẵn trên máy — nhận dạng không cần tải nữa.")

        self.window.run_task(f"Tải model Whisper {size}", lambda _cancel: download_whisper_model(size, self.window.log_bg), done)

    def _layout_engine(self) -> None:
        cloud = self.state.subtitle.stt_engine.startswith("Gemini")
        self.engine_hint.configure(text="Gửi audio lên Gemini, không cần GPU (cần Gemini API key trong Cài đặt)"
                                   if cloud else "Model chạy trên máy, không cần mạng")
        for r in (self.model, self.device, self.weak):
            r.menu.configure(state="disabled") if hasattr(r, "menu") and cloud else None
            if hasattr(r, "menu") and not cloud:
                r.menu.configure(state="normal")

    def _engine_changed(self, v: str) -> None:
        self.set_value(self.state.subtitle, "stt_engine", v, preview=False)
        self._layout_engine()

    def _auto_changed(self, v: bool) -> None:
        self.set_value(self.state.subtitle, "srt_source", SRT_SOURCES[0] if v else SRT_SOURCES[1], preview=False)

    # ── Hành động ────────────────────────────────────────────────────────
    def _run_active(self) -> None:
        item = self.state.active
        if item is None:
            messagebox.showinfo("Edit Studio", "Hãy nạp và chọn một video trước.")
            return
        pipe = self.window.pipeline_for(item)
        self.status.warn("Đang nhận diện… (xem log)")

        def work(cancel):
            return pipe.transcribe(item, progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if error:
                self.status.error(f"Lỗi: {error}")
            else:
                self.on_active_changed()
                self.window.queue_update(item)
                self.window.preview.schedule_refresh(0)
                self.state.mark_dirty()
                self.window.add_notification("Nhận diện xong", f"{item.name}: {len(result)} cue", "ok")
                for k in ("srt", "tts"):
                    if k in self.window.modules:
                        self.window.modules[k].on_active_changed()

        self.window.run_task("Nhận diện", work, done)

    def _run_file(self) -> None:
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
                self.status.error(f"Lỗi: {error}")
            else:
                self.status.ok(f"Đã tạo {Path(tmp.srt_path).name} ({len(result)} cue) trong output/subs")
                if messagebox.askyesno("Edit Studio", "Gán SRT vừa tạo cho video đang chọn?") and self.state.active:
                    self.state.active.srt_path = tmp.srt_path
                    self.state.active.srt_translated_path = None
                    self.state.mark_dirty()
                    self.on_active_changed()
                    self.window.preview.schedule_refresh(0)

        self.window.run_task("Nhận diện file", work, done)

    def _open_srt(self) -> None:
        it = self.state.active
        if it and it.srt_path and Path(it.srt_path).exists():
            self.window.show_module("srt")          # xem / sửa ngay ở tab «Lời thoại»
        else:
            messagebox.showinfo("Edit Studio", "Chưa có file SRT cho video này.")
