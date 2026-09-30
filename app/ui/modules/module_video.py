"""Module Video: nạp video/hàng đợi, khung hình & tốc độ, hình ảnh & GPU, âm thanh, tách lời nâng cao."""

from __future__ import annotations

from app.config import theme as T
from app.core.state import ENCODE_PRESETS, FIT_MODES, GPU_BACKENDS, RATIOS, RESOLUTIONS, VIDEO_CODECS
from app.services.sysinfo import gpu_name
from app.ui.modules.base_module import BaseModule
from app.ui.queue_panel import QueuePanel
from app.ui.widgets.common import ButtonRow, EntryRow, OptionRow, Section, SliderRow, SwitchRow, hint, pack_rows

FPS_LABELS = {"Giữ nguyên": "Giữ nguyên", "60": "60 FPS (Mượt mà)", "30": "30 FPS (Tiêu chuẩn)",
              "24": "24 FPS (Điện ảnh)", "25": "25 FPS", "50": "50 FPS"}
_FPS_BY_LABEL = {v: k for k, v in FPS_LABELS.items()}
ZOOM_LABELS = {100: "100% (Mặc định)", 105: "105%", 110: "110%", 115: "115%", 120: "120%", 130: "130%", 150: "150%"}
SPEED_LABELS = {100: "100% (Chuẩn)", 90: "90% (Chậm)", 95: "95%", 105: "105%", 110: "110%", 115: "115%",
                120: "120%", 125: "125% (Nhanh)", 150: "150%"}


def _label_for(mapping: dict, value) -> str:
    return mapping.get(value, str(value) + "%")


class VideoModule(BaseModule):
    key = "video"
    title = "Video"
    icon = "🎬"
    section = "video"

    def build(self) -> None:
        cfg = self.state.video
        s = self.setter

        ButtonRow(self, [("＋ Nạp video", self._add_files, T.primary_button()),
                         ("Xóa hết", self._clear, T.danger_button())]).pack(fill="x", pady=(0, 6))
        self.queue = QueuePanel(self, self.window, compact=True, height=200)
        self.queue.pack(fill="x", pady=(0, 6))
        ButtonRow(self, [("＋ Thêm từ thư mục…", self._add_folder, None)]).pack(fill="x", pady=(0, 4))

        sec = Section(self, "Khung hình & tốc độ")
        sec.pack(fill="x", pady=4)
        self.ratio = OptionRow(sec.body, "Tỉ lệ", RATIOS, on_change=s(cfg, "ratio"))
        self.fit = OptionRow(sec.body, "Cách vừa khung", FIT_MODES, on_change=s(cfg, "fit_mode"))
        self.res = OptionRow(sec.body, "Độ phân giải", RESOLUTIONS, on_change=s(cfg, "resolution"))
        self.fps = OptionRow(sec.body, "FPS", list(FPS_LABELS.values()), on_change=self._fps_changed)
        self.zoom = OptionRow(sec.body, "Phóng to", list(ZOOM_LABELS.values()), on_change=self._zoom_changed)
        self.speed = OptionRow(sec.body, "Tốc độ", list(SPEED_LABELS.values()), on_change=self._speed_changed)
        pack_rows(sec.body, [self.ratio, self.fit, self.res, self.fps, self.zoom, self.speed])
        hint(sec.body, "9:16 (Shorts/TikTok) · 16:9 (YouTube). Nền mờ: video nằm giữa, hai bên là chính nó phóng to "
                       "và làm mờ.").pack(fill="x", pady=(2, 0))

        sec = Section(self, "Hình ảnh & GPU")
        sec.pack(fill="x", pady=4)
        self.flip = SwitchRow(sec.body, "Lật ngang video (mirror)", on_change=s(cfg, "flip_h"))
        self.gpu = SwitchRow(sec.body, f"Tăng tốc GPU ({gpu_name()})", on_change=s(cfg, "use_gpu", preview=False))
        self.gpu_hint = hint(sec.body, "")
        self.backend = OptionRow(sec.body, "Bộ mã hóa", GPU_BACKENDS, on_change=s(cfg, "gpu_backend", preview=False))
        self.codec = OptionRow(sec.body, "Chuẩn nén", VIDEO_CODECS, on_change=s(cfg, "video_codec", preview=False))
        self.quality = SliderRow(sec.body, "Chất lượng (CRF/CQ)", from_=10, to=40, step=1,
                                 on_change=s(cfg, "quality", cast=int, preview=False))
        self.preset = OptionRow(sec.body, "Tốc độ encode", ENCODE_PRESETS, on_change=s(cfg, "encode_preset", preview=False))
        self.suffix = EntryRow(sec.body, "Hậu tố tên file", placeholder="_edit", on_change=s(cfg, "output_suffix", preview=False))
        self.gop = SliderRow(sec.body, "Khoảng keyframe (GOP)", from_=0, to=300, step=1,
                             on_change=s(cfg, "gop_size", cast=int, preview=False))
        self.flip.pack(fill="x", pady=3)
        self.gpu.pack(fill="x", pady=3)
        self.gpu_hint.pack(fill="x")
        pack_rows(sec.body, [self.backend, self.codec, self.quality, self.preset, self.gop, self.suffix])
        hint(sec.body, "H.264: tương thích mọi thiết bị · HEVC: nhỏ hơn ~40% · AV1: chuẩn mới, nén tốt nhất "
                       "(cần GPU đời mới hoặc encode CPU chậm). CRF 18–23 hợp lý cho YouTube.").pack(fill="x", pady=(2, 0))

        sec = Section(self, "Âm thanh")
        sec.pack(fill="x", pady=4)
        self.keep = SwitchRow(sec.body, "Phát âm thanh gốc của video", on_change=s(cfg, "keep_original_audio", preview=False))
        self.orig_vol = SliderRow(sec.body, "Âm gốc", from_=0, to=200, step=5, suffix="%",
                                  on_change=s(cfg, "original_volume", cast=float, preview=False))
        self.tts_vol = SliderRow(sec.body, "Lồng tiếng", from_=0, to=300, step=5, suffix="%",
                                 on_change=s(cfg, "tts_volume", cast=float, preview=False))
        self.duck = SwitchRow(sec.body, "Tự hạ âm nền khi có lời đọc", on_change=s(cfg, "adaptive_ducking", preview=False))
        self.norm = SwitchRow(sec.body, "Chuẩn hóa âm lượng đầu ra", on_change=s(cfg, "normalize_audio", preview=False))
        self.vocal = SwitchRow(sec.body, "Lọc giọng nói gốc (khử ồn/vocal)", on_change=s(cfg, "vocal_filter", preview=False))
        self.mute = SwitchRow(sec.body, "Tắt toàn bộ âm thanh", on_change=s(cfg, "mute", preview=False))
        self.loop = SwitchRow(sec.body, "Lặp video cho đủ thời lượng giọng đọc", on_change=s(cfg, "loop_to_voice", preview=False))
        pack_rows(sec.body, [self.keep, self.orig_vol, self.tts_vol, self.duck, self.norm, self.vocal, self.mute, self.loop])
        hint(sec.body, "Gợi ý: âm gốc 20–40% làm nền · lồng tiếng 120–160%. Khi bật tự hạ nền, âm gốc sẽ giảm mượt "
                       "lúc TTS phát rồi tăng lại sau lời nói.").pack(fill="x", pady=(2, 0))

        sec = Section(self, "Tách lời nâng cao (AI)")
        sec.pack(fill="x", pady=4)
        self.demucs = SwitchRow(sec.body, "Dùng Demucs để giảm lời thoại gốc", on_change=self._demucs_toggled)
        self.demucs.pack(fill="x", pady=3)
        self.demucs_hint = hint(sec.body, "Chỉ áp dụng khi Render MP4 và đang giữ âm gốc. Dùng AI tách riêng lời thoại "
                                          "gốc khỏi nhạc nền để video lồng tiếng trong trẻo nhất.")
        self.demucs_hint.pack(fill="x")
        ButtonRow(sec.body, [("⬇ Cài Demucs (cần ~2GB, có torch)", self._install_demucs, None)]).pack(fill="x", pady=(4, 0))

    # ── State → widget ────────────────────────────────────────────────────
    def refresh(self) -> None:
        cfg = self.state.video
        self._loading = True
        try:
            self.ratio.set(cfg.ratio)
            self.fit.set(cfg.fit_mode)
            self.res.set(cfg.resolution)
            self.fps.set(FPS_LABELS.get(cfg.fps, FPS_LABELS["Giữ nguyên"]))
            self.zoom.set(_label_for(ZOOM_LABELS, int(cfg.zoom)))
            self.speed.set(_label_for(SPEED_LABELS, int(cfg.speed)))
            self.flip.set(cfg.flip_h)
            self.gpu.set(cfg.use_gpu)
            self.backend.set(cfg.gpu_backend)
            self.codec.set(cfg.video_codec)
            self.quality.set(cfg.quality)
            self.preset.set(cfg.encode_preset)
            self.suffix.set(cfg.output_suffix)
            self.gop.set(cfg.gop_size)
            self.keep.set(cfg.keep_original_audio)
            self.orig_vol.set(cfg.original_volume)
            self.tts_vol.set(cfg.tts_volume)
            self.duck.set(cfg.adaptive_ducking)
            self.norm.set(cfg.normalize_audio)
            self.vocal.set(cfg.vocal_filter)
            self.mute.set(cfg.mute)
            self.loop.set(cfg.loop_to_voice)
            self.demucs.set(cfg.demucs_enable)
        finally:
            self._loading = False
        self._update_gpu_hint()

    def _update_gpu_hint(self) -> None:
        """Dò thật NVENC/QSV/AMF bằng ffmpeg (chạy nền, có cache) để báo máy này sẽ mã hóa bằng gì."""
        from app.services.render_pipeline import BACKEND_LABEL, available_encoders

        ff = getattr(self.window, "ffmpeg", None)
        if ff is None or not ff.available:
            self.gpu_hint.configure(text=f"⚡ {gpu_name()} · chưa tìm thấy ffmpeg nên chưa kiểm tra được bộ mã hóa.")
            return
        if getattr(self, "_gpu_probe_running", False):
            return
        self._gpu_probe_running = True
        self.gpu_hint.configure(text=f"⚡ {gpu_name()} · đang kiểm tra bộ mã hóa phần cứng…")

        def done(result, error):
            self._gpu_probe_running = False
            if error or not result:
                self.gpu_hint.configure(text=f"⚡ {gpu_name()} · không kiểm tra được bộ mã hóa ({error}).")
                return
            ok = [BACKEND_LABEL[b] for b, v in result.items() if v]
            bad = [BACKEND_LABEL[b] for b, v in result.items() if not v]
            first = next((BACKEND_LABEL[b] for b, v in result.items() if v), "CPU (libx264)")
            text = f"⚡ {gpu_name()} · «auto» sẽ dùng {first}."
            if ok:
                text += " Dùng được: " + ", ".join(ok) + "."
            if bad:
                text += " Không có: " + ", ".join(bad) + "."
            text += " Không có GPU phù hợp sẽ về CPU."
            self.gpu_hint.configure(text=text)

        self.window.run_bg(lambda: available_encoders(ff), done)

    def _fps_changed(self, label: str) -> None:
        self.set_value(self.state.video, "fps", _FPS_BY_LABEL.get(label, "Giữ nguyên"), preview=False)

    def _zoom_changed(self, label: str) -> None:
        val = next((k for k, v in ZOOM_LABELS.items() if v == label), 100)
        self.set_value(self.state.video, "zoom", int(val))

    def _speed_changed(self, label: str) -> None:
        val = next((k for k, v in SPEED_LABELS.items() if v == label), 100)
        self.set_value(self.state.video, "speed", int(val), preview=False)

    def _demucs_toggled(self, v: bool) -> None:
        from app.services.demucs_sep import is_installed

        if v and not is_installed():
            self.demucs_hint.configure(text="⚠ Chưa cài Demucs — bấm “Cài Demucs” (tải torch + model, ~2GB). "
                                            "Khi chưa cài, render sẽ dùng bộ lọc khử vocal thường.")
        self.set_value(self.state.video, "demucs_enable", bool(v), preview=False)

    def _install_demucs(self) -> None:
        from app.services.demucs_sep import install_demucs, is_installed

        if is_installed():
            self.demucs_hint.configure(text="Demucs đã được cài — bật công tắc ở trên để dùng.")
            return
        self.demucs_hint.configure(text="Đang cài Demucs… xem Nhật ký (vài phút).")
        self.window.run_bg(lambda: install_demucs(self.window.log_bg),
                           lambda r, e: self.demucs_hint.configure(
                               text="Đã cài Demucs." if r and not e else f"Cài Demucs thất bại: {e or 'xem Nhật ký'}"))

    # ── Hàng đợi ─────────────────────────────────────────────────────────
    def _add_files(self) -> None:
        self.queue.add_files()

    def _add_folder(self) -> None:
        self.queue.add_folder()

    def _clear(self) -> None:
        self.queue.clear()
