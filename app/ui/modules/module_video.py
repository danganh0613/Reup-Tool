"""Module Video theo mức hay dùng: hàng đợi → khung hình → âm thanh → cấu hình riêng; thư mục theo dõi và «Nâng cao
xuất file» (bộ mã hoá, chất lượng, tên / nơi lưu file) thu gọn bên dưới."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from app.config import theme as T
from app.services.watch_folder import WATCH_ACTIONS
from app.core.state import ENCODE_PRESETS, FIT_MODES, GPU_BACKENDS, RATIO_VALUES, RATIOS, RESOLUTIONS, VIDEO_CODECS
from app.services.sysinfo import gpu_name, gpu_name_nowait
from app.ui.modules.base_module import BaseModule
from app.ui.queue_panel import QueuePanel
from app.ui.widgets.common import (ButtonRow, EntryRow, OptionRow, Section, SliderRow, StatusLabel, SwitchRow, hint,
                                  pack_rows)
from app.ui.widgets.ui import button

#: bộ mã hoá hiển thị — «cpu» đã có công tắc «Tăng tốc GPU» (tắt = CPU), không để hai cách chọn cùng một việc
SHOWN_BACKENDS = [b for b in GPU_BACKENDS if b != "cpu"]
BACKEND_LABELS = {"auto": "Tự chọn (khuyên dùng)", "nvenc": "NVIDIA (nvenc)", "qsv": "Intel (qsv)", "amf": "AMD (amf)",
                  "videotoolbox": "Apple (videotoolbox)"}

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
                         ("＋ Thêm thư mục…", self._add_folder, None)]).pack(fill="x", pady=(0, 6))
        self.queue = QueuePanel(self, self.window, compact=True, height=200)
        self.queue.pack(fill="x", pady=(0, 2))
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", pady=(0, 4))
        # «Xoá hết» tách xa nút «Nạp video» (trước: hai nút dính nhau, dễ bấm nhầm mất cả hàng đợi đã duyệt)
        button(foot, "Xoá hết hàng đợi", icon_name="trash", variant="ghost", size="sm", command=self._clear,
               tip="Bỏ mọi video khỏi hàng đợi (có hỏi trước; phụ đề / video đã xuất vẫn giữ trên ổ đĩa)").pack(side="right")
        self.review = SwitchRow(self, "Duyệt trước khi xuất (Chuẩn bị → Duyệt → Xuất)", on_change=self._review_toggled)
        self.review.pack(fill="x", pady=(2, 0))
        self.review_hint = hint(self, "«Chuẩn bị» → xem, sửa ở tab «Duyệt» → «Xuất». Tắt = xuất thẳng.")

        sec = Section(self, "Khung hình & tốc độ", info="9:16 = TikTok / Shorts · 16:9 = YouTube. Nền mờ: video nằm "
                                                          "giữa, hai bên là chính nó phóng to và làm mờ.")
        sec.pack(fill="x", pady=4)
        set_ratio, set_fit = s(cfg, "ratio"), s(cfg, "fit_mode")
        self.ratio = OptionRow(sec.body, "Tỉ lệ", RATIOS, on_change=lambda v: (set_ratio(v), self.update_fit_hint()))
        self.fit = OptionRow(sec.body, "Cách vừa khung", FIT_MODES, on_change=lambda v: (set_fit(v), self.update_fit_hint()))
        self.res = OptionRow(sec.body, "Độ phân giải", RESOLUTIONS, on_change=s(cfg, "resolution"))
        self.fps = OptionRow(sec.body, "Số hình / giây", list(FPS_LABELS.values()), on_change=self._fps_changed)
        self.zoom = OptionRow(sec.body, "Phóng to", list(ZOOM_LABELS.values()), on_change=self._zoom_changed)
        self.speed = OptionRow(sec.body, "Tốc độ", list(SPEED_LABELS.values()), on_change=self._speed_changed)
        pack_rows(sec.body, [self.ratio, self.fit, self.res, self.fps, self.zoom, self.speed])
        # chỉ hiện khi «Cách vừa khung» không có tác dụng (video đã đúng tỉ lệ xuất) — tránh tưởng chọn mà không ăn
        self.fit_hint = hint(sec.body, "")
        self.fit_hint.configure(text_color=T.INFO)
        self.flip = SwitchRow(sec.body, "Lật ngang video (soi gương)", on_change=s(cfg, "flip_h"))
        self.flip.pack(fill="x", pady=3)

        sec = Section(self, "Âm thanh", info="Lồng tiếng: bật «Khử giọng nói gốc», âm nền 60–90%, lồng tiếng 120–160%. "
                                               "«Tự hạ âm gốc»: âm gốc giảm mượt khi có lời đọc rồi lên lại.")
        sec.pack(fill="x", pady=4)
        self.keep = SwitchRow(sec.body, "Phát âm thanh gốc của video", on_change=s(cfg, "keep_original_audio", preview=False))
        self.novocal = SwitchRow(sec.body, "Khử giọng nói gốc (AI) — giữ nhạc nền & tiếng động", on_change=self._novocal_toggled)
        self.novocal_hint = hint(sec.body, "")
        self.orig_vol = SliderRow(sec.body, "Âm gốc / nền", from_=0, to=200, step=5, suffix="%",
                                  on_change=s(cfg, "original_volume", cast=float, preview=False))
        self.tts_vol = SliderRow(sec.body, "Lồng tiếng", from_=0, to=300, step=5, suffix="%",
                                 on_change=s(cfg, "tts_volume", cast=float, preview=False))
        self.duck = SwitchRow(sec.body, "Tự hạ âm gốc khi có lời đọc", on_change=s(cfg, "adaptive_ducking", preview=False))
        self.norm = SwitchRow(sec.body, "Chuẩn hoá độ to đầu ra", on_change=s(cfg, "normalize_audio", preview=False))
        self.mute = SwitchRow(sec.body, "Video câm (tắt hết: âm gốc, lồng tiếng, nhạc)", on_change=s(cfg, "mute", preview=False))
        self.loop = SwitchRow(sec.body, "Lặp video cho đủ thời lượng giọng đọc", on_change=s(cfg, "loop_to_voice", preview=False))
        pack_rows(sec.body, [self.keep, self.novocal, self.novocal_hint, self.orig_vol, self.tts_vol, self.duck,
                             self.norm, self.mute, self.loop])

        sec = Section(self, "Cấu hình của video đang chọn",
                      info="Mọi video dùng chung mẫu ở thanh trên cùng. Bật «Chỉnh riêng» khi video này cần khác (vd chữ "
                           "cứng nằm chỗ khác): chỉnh gì lúc chọn video này chỉ áp cho nó.")
        sec.pack(fill="x", pady=4)
        self.own_status = StatusLabel(sec.body, "")
        self.own_status.pack(fill="x")
        self.own = SwitchRow(sec.body, "Chỉnh riêng cho video này", on_change=self._own_toggled)
        self.own.pack(fill="x", pady=(4, 0))
        self.own_tpl = OptionRow(sec.body, "Áp mẫu riêng", ["Chọn mẫu…"], on_change=self._own_template)
        self.own_tpl.pack(fill="x", pady=3)

        from app.services.watch_folder import SHARED, WATCH_ACTIONS

        sec = self.watch_sec = Section(self, "Thư mục theo dõi", subtitle="tự nạp video mới", collapsed=True,
                                       info="Máy xem thư mục vài giây một lần, chỉ nhận file đã chép xong. «Tự chuẩn bị» "
                                            "hợp với Duyệt trước khi xuất; «Tự xuất» xuất luôn theo mẫu.")
        sec.pack(fill="x", pady=4)
        self.watch = SwitchRow(sec.body, "Theo dõi thư mục", on_change=self._watch_toggled)
        self.watch_dir = EntryRow(sec.body, "Thư mục", browse="dir", placeholder="chép video vào đây là tự vào hàng đợi",
                                  on_change=lambda v: self._watch_set("watch_dir", v.strip().strip('"').strip("'").strip()))
        self.watch_tpl = OptionRow(sec.body, "Áp mẫu", [SHARED], on_change=lambda v: self._watch_set("watch_template", v))
        self.watch_action = OptionRow(sec.body, "Sau khi nạp", WATCH_ACTIONS,
                                      on_change=lambda v: self._watch_set("watch_action", v))
        pack_rows(sec.body, [self.watch, self.watch_dir, self.watch_tpl, self.watch_action])
        self.watch_status = StatusLabel(sec.body, "")
        self.watch_status.pack(fill="x")

        # ── Tên & nơi lưu: MỘT chỗ (thư mục xuất trước nằm ở Cài đặt, tên / thư mục con ở đây) ──
        sec = self.out_sec = Section(self, "Tên & nơi lưu file xuất", collapsed=True)
        sec.pack(fill="x", pady=4)
        row = ctk.CTkFrame(sec.body, fg_color="transparent")
        row.pack(fill="x", pady=3)
        row.grid_columnconfigure(0, weight=1)
        self.out_dir_label = ctk.CTkLabel(row, text="", font=T.font(12), text_color=T.TEXT_DIM, anchor="w", justify="left")
        self.out_dir_label.grid(row=0, column=0, sticky="ew")
        button(row, "Đổi…", icon_name="openfolder", size="sm", command=self._pick_out_dir,
               tip="Chọn thư mục lưu video xuất (dùng chung cho mọi video)").grid(row=0, column=1, padx=(6, 0))
        self.suffix = EntryRow(sec.body, "Hậu tố tên file", placeholder="_edit", on_change=s(cfg, "output_suffix", preview=False))
        self.out_name = EntryRow(sec.body, "Tên file xuất", placeholder="{ten}{hauto}",
                                 on_change=lambda v: (self.set_value(self.state.video, "output_name", v.strip(), preview=False),
                                                      self._name_example()))
        self.out_sub = EntryRow(sec.body, "Thư mục con", placeholder="vd: {mau}/{ngay}",
                                on_change=lambda v: (self.set_value(self.state.video, "output_subdir", v.strip(), preview=False),
                                                     self._name_example()))
        pack_rows(sec.body, [self.suffix, self.out_name, self.out_sub])
        self.name_hint = hint(sec.body, "")
        self.name_hint.pack(fill="x")

        sec = self.enc_sec = Section(self, "Nâng cao xuất file", subtitle="GPU, độ nét, dung lượng", collapsed=True,
                                     info="H.264: chạy mọi thiết bị · HEVC: nhẹ hơn ~40% · AV1: nén tốt nhất (cần GPU đời "
                                          "mới). Mặc định đã hợp cho YouTube / TikTok — không cần chỉnh.")
        sec.pack(fill="x", pady=4)
        self.gpu = SwitchRow(sec.body, f"Tăng tốc GPU ({gpu_name_nowait() or 'đang dò…'})",
                             on_change=s(cfg, "use_gpu", preview=False))
        if gpu_name_nowait() is None:
            self.window.run_bg(gpu_name, lambda name, _err: self.gpu.switch.configure(
                text=f"Tăng tốc GPU ({name or 'GPU không xác định'})"))
        self.gpu_hint = hint(sec.body, "")
        self.backend = OptionRow(sec.body, "Bộ mã hoá GPU", SHOWN_BACKENDS, labels=BACKEND_LABELS,
                                 on_change=s(cfg, "gpu_backend", preview=False))
        self.codec = OptionRow(sec.body, "Chuẩn nén", VIDEO_CODECS, on_change=s(cfg, "video_codec", preview=False))
        self.quality = SliderRow(sec.body, "Độ nét (CRF)", from_=10, to=40, step=1,
                                 on_change=s(cfg, "quality", cast=int, preview=False),
                                 info="Số NHỎ = hình đẹp hơn, file nặng hơn · số LỚN = file nhẹ, hình mờ hơn. "
                                      "Khuyên dùng 18–23 (YouTube / TikTok).")
        self.quality_hint = hint(sec.body, "← đẹp hơn, file nặng · · · file nhẹ, mờ hơn →   (khuyên dùng 18–23)")
        self.preset = OptionRow(sec.body, "Tốc độ xuất", ENCODE_PRESETS, on_change=s(cfg, "encode_preset", preview=False),
                                info="Nhanh: xuất nhanh, file to hơn một chút · Chất lượng: chậm hơn, đẹp hơn ở cùng dung lượng.")
        self.gop = SliderRow(sec.body, "Khung chính (GOP)", from_=0, to=300, step=1,
                             on_change=s(cfg, "gop_size", cast=int, preview=False),
                             info="Khoảng cách giữa hai khung hình chính. 0 = tự động (khuyên dùng).")
        self.gpu.pack(fill="x", pady=3)
        self.gpu_hint.pack(fill="x")
        pack_rows(sec.body, [self.backend, self.codec, self.quality, self.quality_hint, self.preset, self.gop])

    def _pick_out_dir(self) -> None:
        from tkinter import filedialog

        d = filedialog.askdirectory(title="Chọn thư mục lưu video xuất", initialdir=self.state.output_dir or None)
        if not d:
            return
        self.state.output_dir = d
        self.state.mark_dirty()
        self._name_example()
        self.window.log(f"Thư mục xuất: {d}")

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
            if cfg.gpu_backend == "cpu":                 # cấu hình cũ chọn «cpu» → cùng nghĩa với tắt «Tăng tốc GPU»
                cfg.use_gpu, cfg.gpu_backend = False, "auto"
                self.state.config_changed("video", auto=True)
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
            self.mute.set(cfg.mute)
            self.loop.set(cfg.loop_to_voice)
            self.novocal.set(cfg.demucs_enable)
            self._novocal_status()
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
        self._novocal_status()

    def _name_example(self) -> None:
        """Ví dụ tên file xuất theo mẫu tên đang nhập + thư mục xuất đang dùng."""
        from app.config.paths import VIDEOS_DIR
        from app.ui.progress_panel import short_path

        out = self.state.output_dir or str(VIDEOS_DIR)
        self.out_dir_label.configure(text=f"Lưu vào: {short_path(out)}")
        self.out_sec.set_subtitle(short_path(out))
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
        elif not Path(ui["watch_dir"]).is_dir():
            self.watch_status.warn(f"Không thấy thư mục «{ui['watch_dir']}» — ổ ngoài đã rút hoặc gõ sai đường dẫn?")
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
        if key == "watch_action" and value == WATCH_ACTIONS[1] and not self.state.ui.get("review_mode"):
            self.window.set_review_mode(True)
            self.window.log("Đã bật «Duyệt trước khi xuất» — video tự chuẩn bị sẽ chờ bạn duyệt ở tab Duyệt.")
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
        self.window.set_review_mode(on)

    def layout_review(self) -> None:
        self._loading = True
        try:
            self.review.set(bool(self.state.ui.get("review_mode")))
        finally:
            self._loading = False
        self._layout_review()

    def _layout_review(self) -> None:
        if self.state.ui.get("review_mode"):
            if not self.review_hint.winfo_ismapped():
                self.review_hint.pack(fill="x", pady=(0, 4), after=self.review)
        else:
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

    def _novocal_toggled(self, v: bool) -> None:
        self.set_value(self.state.video, "demucs_enable", bool(v), preview=False)
        if v and self.state.video.original_volume < 40:
            # trước đây âm gốc để nhỏ cho đỡ lẫn giọng; giờ chỉ còn nhạc & tiếng động → nâng lên mức nền dễ nghe
            self.set_value(self.state.video, "original_volume", 70.0, preview=False)
            self.orig_vol.set(70.0)
        self._novocal_status()
        self.window.preview.ensure_no_vocals()

    def _novocal_status(self) -> None:
        from app.services.vocal_remove import cached_no_vocals, model_ready

        if not self.state.video.demucs_enable:
            text = ("Tắt: giữ nguyên âm gốc (cả giọng nói). Bật khi lồng tiếng: AI bỏ giọng Trung / Anh… của video gốc, "
                    "giữ nhạc nền & tiếng động. Chạy trên máy, ~1 phút cho mỗi phút video, làm một lần ở bước Chuẩn bị.")
        elif not self.state.video.keep_original_audio or self.state.video.mute:
            text = "Đang tắt âm gốc (hoặc tắt toàn bộ âm thanh) nên không cần khử giọng — bước này sẽ được bỏ qua."
        else:
            it = self.state.active
            if it is not None and cached_no_vocals(it.path) is not None:
                text = "✓ Video này đã khử giọng gốc."
            elif not model_ready():
                text = "Lần đầu sẽ tải model AI (~64 MB). Chưa khử xong thì xem trước tạm vặn âm gốc rất nhỏ."
            else:
                text = "Video này sẽ được khử giọng khi Chuẩn bị / Xuất (xem trước tự khử nền nếu đang mở)."
        self.novocal_hint.configure(text=text)

    # ── Hàng đợi ─────────────────────────────────────────────────────────
    def _add_files(self) -> None:
        self.queue.add_files()

    def _add_folder(self) -> None:
        self.queue.add_folder()

    def _clear(self) -> None:
        self.queue.clear()
