"""Module Video: nạp video/hàng đợi, khung hình & tốc độ, hình ảnh & GPU, âm thanh, tách lời nâng cao."""

from __future__ import annotations

from app.config import theme as T
from app.core.state import ENCODE_PRESETS, FIT_MODES, GPU_BACKENDS, RATIO_VALUES, RATIOS, RESOLUTIONS, VIDEO_CODECS
from app.services.sysinfo import gpu_name, gpu_name_nowait
from app.ui.modules.base_module import BaseModule
from app.ui.queue_panel import QueuePanel
from app.ui.widgets.common import (ButtonRow, EntryRow, OptionRow, Section, SliderRow, StatusLabel, SwitchRow, hint,
                                  pack_rows)

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
        self.review = SwitchRow(self, "Duyệt trước khi xuất (Chuẩn bị → Duyệt → Xuất)", on_change=self._review_toggled)
        self.review.pack(fill="x", pady=(2, 0))
        self.review_row = ButtonRow(self, [("✔ Mở màn hình duyệt", lambda: self.window.open_review(), None)])
        self.review_hint = hint(self, "Bấm nút Chuẩn bị (thay cho Render) → máy làm phụ đề, dịch, giọng cho cả hàng đợi → "
                                      "bạn xem / sửa / duyệt từng video → Xuất. Tắt = render một mạch như cũ.")

        from app.services.watch_folder import SHARED, WATCH_ACTIONS

        sec = Section(self, "Thư mục theo dõi (tự nạp video mới)")
        sec.pack(fill="x", pady=4)
        self.watch = SwitchRow(sec.body, "Theo dõi thư mục", on_change=self._watch_toggled)
        self.watch_dir = EntryRow(sec.body, "Thư mục", browse="dir", placeholder="chép video vào đây là tự vào hàng đợi",
                                  on_change=lambda v: self._watch_set("watch_dir", v.strip()))
        self.watch_tpl = OptionRow(sec.body, "Áp mẫu", [SHARED], on_change=lambda v: self._watch_set("watch_template", v))
        self.watch_action = OptionRow(sec.body, "Sau khi nạp", WATCH_ACTIONS,
                                      on_change=lambda v: self._watch_set("watch_action", v))
        pack_rows(sec.body, [self.watch, self.watch_dir, self.watch_tpl, self.watch_action])
        self.watch_status = StatusLabel(sec.body, "")
        self.watch_status.pack(fill="x")
        hint(sec.body, "Máy kiểm tra thư mục vài giây một lần, chỉ nhận file đã chép xong. «Tự chuẩn bị» hợp với chế độ "
                       "Duyệt trước khi xuất; «Tự xuất» render luôn theo mẫu.").pack(fill="x")

        sec = Section(self, "Cấu hình của video đang chọn")
        sec.pack(fill="x", pady=4)
        self.own_status = StatusLabel(sec.body, "")
        self.own_status.pack(fill="x")
        self.own = SwitchRow(sec.body, "Chỉnh riêng cho video này", on_change=self._own_toggled)
        self.own.pack(fill="x", pady=(4, 0))
        self.own_tpl = OptionRow(sec.body, "Áp mẫu riêng", ["Chọn mẫu…"], on_change=self._own_template)
        self.own_tpl.pack(fill="x", pady=3)
        hint(sec.body, "Mặc định mọi video dùng chung mẫu chọn ở thanh trên cùng. Bật «Chỉnh riêng» khi video này cần khác "
                       "(vd. chữ cứng nằm chỗ khác → che mờ chỗ khác): chỉnh gì lúc chọn video này chỉ áp cho nó.").pack(fill="x")

        sec = Section(self, "Khung hình & tốc độ")
        sec.pack(fill="x", pady=4)
        set_ratio, set_fit = s(cfg, "ratio"), s(cfg, "fit_mode")
        self.ratio = OptionRow(sec.body, "Tỉ lệ", RATIOS, on_change=lambda v: (set_ratio(v), self.update_fit_hint()))
        self.fit = OptionRow(sec.body, "Cách vừa khung", FIT_MODES, on_change=lambda v: (set_fit(v), self.update_fit_hint()))
        self.res = OptionRow(sec.body, "Độ phân giải", RESOLUTIONS, on_change=s(cfg, "resolution"))
        self.fps = OptionRow(sec.body, "FPS", list(FPS_LABELS.values()), on_change=self._fps_changed)
        self.zoom = OptionRow(sec.body, "Phóng to", list(ZOOM_LABELS.values()), on_change=self._zoom_changed)
        self.speed = OptionRow(sec.body, "Tốc độ", list(SPEED_LABELS.values()), on_change=self._speed_changed)
        pack_rows(sec.body, [self.ratio, self.fit, self.res, self.fps, self.zoom, self.speed])
        # chỉ hiện khi «Cách vừa khung» không có tác dụng (video đã đúng tỉ lệ xuất) — tránh tưởng chọn mà không ăn
        self.fit_hint = hint(sec.body, "")
        self.fit_hint.configure(text_color=T.INFO)
        hint(sec.body, "9:16 (Shorts/TikTok) · 16:9 (YouTube). Nền mờ: video nằm giữa, hai bên là chính nó phóng to "
                       "và làm mờ.").pack(fill="x", pady=(2, 0))

        sec = Section(self, "Hình ảnh & GPU")
        sec.pack(fill="x", pady=4)
        self.flip = SwitchRow(sec.body, "Lật ngang video (mirror)", on_change=s(cfg, "flip_h"))
        self.gpu = SwitchRow(sec.body, f"Tăng tốc GPU ({gpu_name_nowait() or 'đang dò…'})",
                             on_change=s(cfg, "use_gpu", preview=False))
        if gpu_name_nowait() is None:
            self.window.run_bg(gpu_name, lambda name, _err: self.gpu.switch.configure(
                text=f"Tăng tốc GPU ({name or 'GPU không xác định'})"))
        self.gpu_hint = hint(sec.body, "")
        self.backend = OptionRow(sec.body, "Bộ mã hóa", GPU_BACKENDS, on_change=s(cfg, "gpu_backend", preview=False))
        self.codec = OptionRow(sec.body, "Chuẩn nén", VIDEO_CODECS, on_change=s(cfg, "video_codec", preview=False))
        self.quality = SliderRow(sec.body, "Chất lượng (CRF/CQ)", from_=10, to=40, step=1,
                                 on_change=s(cfg, "quality", cast=int, preview=False))
        self.preset = OptionRow(sec.body, "Tốc độ encode", ENCODE_PRESETS, on_change=s(cfg, "encode_preset", preview=False))
        self.suffix = EntryRow(sec.body, "Hậu tố tên file", placeholder="_edit", on_change=s(cfg, "output_suffix", preview=False))
        self.out_name = EntryRow(sec.body, "Tên file xuất", placeholder="{ten}{hauto}",
                                 on_change=lambda v: (self.set_value(self.state.video, "output_name", v.strip(), preview=False),
                                                      self._name_example()))
        self.out_sub = EntryRow(sec.body, "Thư mục con", placeholder="vd: {mau}/{ngay}",
                                on_change=lambda v: (self.set_value(self.state.video, "output_subdir", v.strip(), preview=False),
                                                     self._name_example()))
        self.name_hint = hint(sec.body, "")
        self.gop = SliderRow(sec.body, "Khoảng keyframe (GOP)", from_=0, to=300, step=1,
                             on_change=s(cfg, "gop_size", cast=int, preview=False))
        self.flip.pack(fill="x", pady=3)
        self.gpu.pack(fill="x", pady=3)
        self.gpu_hint.pack(fill="x")
        pack_rows(sec.body, [self.backend, self.codec, self.quality, self.preset, self.gop, self.suffix, self.out_name,
                             self.out_sub])
        self.name_hint.pack(fill="x")
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
            self.out_name.set(cfg.output_name)
            self.out_sub.set(cfg.output_subdir)
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
            self.review.set(bool(self.state.ui.get("review_mode")))
        finally:
            self._loading = False
        self.update_fit_hint()
        self.update_own()
        self._layout_review()
        self._update_gpu_hint()
        self._name_example()
        self._refresh_watch()

    def on_active_changed(self) -> None:
        self.update_fit_hint()
        self.update_own()
        self._name_example()

    def _name_example(self) -> None:
        """Ví dụ tên file xuất theo mẫu tên đang nhập."""
        it = self.state.active
        try:
            p = self.window.pipeline.output_path(it, ext=".mp4", create=False) if it is not None else None
        except Exception:
            p = None
        ex = ""
        if p is not None:
            try:
                ex = str(p.relative_to(self.state.output_dir))
            except ValueError:
                ex = p.name
        self.name_hint.configure(text="Mã dùng được: {ten} tên gốc · {hauto} hậu tố · {mau} tên mẫu · {ngay} · {gio} · "
                                      "{stt} số thứ tự" + (f"\nVí dụ: {ex}" if ex else ""))

    # ── Thư mục theo dõi ─────────────────────────────────────────────────
    def _refresh_watch(self) -> None:
        from app.services.templates import list_templates
        from app.services.watch_folder import SHARED, WATCH_ACTIONS

        ui = self.state.ui
        self._loading = True
        try:
            self.watch.set(bool(ui.get("watch_enable")))
            self.watch_dir.set(str(ui.get("watch_dir") or ""))
            self.watch_tpl.set_values([SHARED] + [t.name for t in list_templates()], keep=False)
            self.watch_tpl.set(ui.get("watch_template") or SHARED)
            self.watch_action.set(ui.get("watch_action") or WATCH_ACTIONS[0])
        finally:
            self._loading = False
        self.update_watch_status()

    def update_watch_status(self, added: int | None = None) -> None:
        ui = self.state.ui
        if not ui.get("watch_enable"):
            self.watch_status.info("")
        elif not ui.get("watch_dir"):
            self.watch_status.warn("Chọn thư mục để theo dõi.")
        else:
            n = ui.get("watch_added", 0)
            self.watch_status.ok(f"Đang theo dõi «{ui['watch_dir']}» · đã tự nạp {n} video.")

    def _watch_set(self, key: str, value) -> None:
        if self._loading:
            return
        self.state.ui[key] = value
        self.state.mark_dirty()
        if key == "watch_dir" and self.state.ui.get("watch_enable"):
            self.window.watch_started()
        self.update_watch_status()

    def _watch_toggled(self, on: bool) -> None:
        if self._loading:
            return
        self.state.ui["watch_enable"] = bool(on)
        self.state.mark_dirty()
        if on:
            self.window.watch_started()
        self.update_watch_status()

    def _review_toggled(self, on: bool) -> None:
        if self._loading:
            return
        self.state.ui["review_mode"] = bool(on)
        self.state.mark_dirty()
        self._layout_review()
        self.window.update_render_button()

    def _layout_review(self) -> None:
        if self.state.ui.get("review_mode"):
            if not self.review_row.winfo_ismapped():
                self.review_row.pack(fill="x", pady=(2, 0), after=self.review)
                self.review_hint.pack(fill="x", pady=(0, 4), after=self.review_row)
        else:
            self.review_row.pack_forget()
            self.review_hint.pack_forget()

    def update_own(self) -> None:
        from app.services.templates import list_templates

        it = self.state.active
        self._loading = True
        try:
            self.own.set(bool(it is not None and it.config is not None))
            self.own_tpl.set_values(["Chọn mẫu…"] + [t.name for t in list_templates()], keep=False)
            self.own_tpl.set(it.template if it is not None and it.config is not None and it.template else "Chọn mẫu…")
        finally:
            self._loading = False
        state = "normal" if it is not None else "disabled"
        self.own.switch.configure(state=state)
        self.own_tpl.menu.configure(state=state)
        if it is None:
            self.own_status.info("Chưa chọn video.")
        elif it.config is not None:
            self.own_status.ok(f"✎ {it.name}: cấu hình riêng" + (f" · mẫu «{it.template}»" if it.template else ""))
        else:
            tpl = self.state.template_name
            self.own_status.info(f"{it.name}: theo cấu hình chung" + (f" · mẫu «{tpl}»" if tpl else ""))

    def _own_toggled(self, on: bool) -> None:
        if self._loading:
            return
        it = self.state.active
        if it is None:
            return
        if on:
            self.state.make_own(it)
            self.window.log(f"{it.name}: bật cấu hình riêng — chỉnh gì lúc chọn video này chỉ áp cho nó.")
        else:
            from tkinter import messagebox

            if not messagebox.askyesno("Edit Studio", f"Bỏ cấu hình riêng của «{it.name}»?\nVideo sẽ dùng lại cấu hình chung."):
                self.update_own()
                return
            self.state.drop_own(it)
        self.window.queue_update(it)
        self.update_own()
        self.window.preview.schedule_refresh(0)

    def _own_template(self, name: str) -> None:
        if self._loading or name == "Chọn mẫu…":
            return
        from app.services import templates as TP

        it, t = self.state.active, TP.get_template(name)
        if it is None or t is None:
            return
        TP.apply_to_item(self.state, it, t)
        self.window.queue_update(it)
        self.window.log(f"{it.name}: dùng mẫu riêng «{t.name}».")
        self.update_own()
        self.window.preview.schedule_refresh(0)

    def update_fit_hint(self) -> None:
        """Video đang chọn đã đúng tỉ lệ xuất (vd. 1080×1920 → 9:16) thì khung vừa khít, không có chỗ cho viền / nền mờ."""
        cfg = self.state.video
        it = self.state.active
        w, h = (it.width, it.height) if it is not None else (0, 0)
        target = RATIO_VALUES.get(cfg.ratio)
        text = ""
        if it is not None and target is None:
            text = ("Đang giữ tỉ lệ gốc của video → «Cách vừa khung» không có tác dụng. Chọn một tỉ lệ khác "
                    "(vd. 9:16) để thêm viền đen / nền mờ.")
        elif w and h and target and abs(w / h - target) < 0.02:
            text = (f"Video đang chọn ({w}×{h}) đã đúng tỉ lệ {cfg.ratio.split(' ')[0]} → vừa khít khung, «Cách vừa "
                    "khung» không thay đổi gì. Viền đen / nền mờ chỉ xuất hiện khi tỉ lệ video khác tỉ lệ xuất "
                    "(vd. video ngang 16:9 → 9:16).")
        self.fit_hint.configure(text=text)
        if text and not self.fit_hint.winfo_ismapped():
            self.fit_hint.pack(fill="x", pady=(0, 3), after=self.fit)
        elif not text and self.fit_hint.winfo_ismapped():
            self.fit_hint.pack_forget()

    def _update_gpu_hint(self) -> None:
        """Dò thật NVENC/QSV/AMF bằng ffmpeg (chạy nền, có cache) để báo máy này sẽ mã hóa bằng gì."""
        from app.services.render_pipeline import BACKEND_LABEL, available_encoders

        ff = getattr(self.window, "ffmpeg", None)
        if ff is None or not ff.available:
            self.gpu_hint.configure(text=f"⚡ {gpu_name_nowait() or 'GPU'} · chưa tìm thấy ffmpeg nên chưa kiểm tra được bộ mã hóa.")
            return
        if getattr(self, "_gpu_probe_running", False):
            return
        self._gpu_probe_running = True
        self.gpu_hint.configure(text=f"⚡ {gpu_name_nowait() or 'GPU'} · đang kiểm tra bộ mã hóa phần cứng…")

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

        def work():
            gpu_name()               # dò tên GPU luôn trong nền → done() lấy từ cache, không chặn UI
            return available_encoders(ff)

        self.window.run_bg(work, done)

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
