"""Module Video theo mức hay dùng: hàng đợi → khung hình (phóng to, canh nội dung, tốc độ — tỉ lệ / kiểu khung / độ
phân giải / số hình chọn trên thanh khung xem trước) → cấu hình riêng; thư mục theo dõi và «Nâng cao xuất file» (bộ mã
hoá, chất lượng, tên / nơi lưu file) thu gọn bên dưới. Âm thanh: tab Lồng tiếng."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from app.config import theme as T
from app.core.state import ENCODE_PRESETS, GPU_BACKENDS, RATIO_VALUES, VIDEO_CODECS
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

        sec = Section(self, "Khung hình", info="Tỉ lệ (giữ nguyên / 9:16…), kiểu khung (nền mờ trên-dưới, viền đen, cắt "
                                                 "vừa khung), độ phân giải, số hình / giây: chọn trên thanh của khung xem "
                                                 "trước. Ở đây: phóng to, canh nội dung trong khung, tốc độ.")
        sec.pack(fill="x", pady=4)
        # chỉ hiện khi kiểu khung không có tác dụng (giữ tỉ lệ gốc / video đã đúng tỉ lệ xuất) — tránh tưởng chọn mà không ăn
        self.fit_hint = hint(sec.body, "")
        self.fit_hint.configure(text_color=T.INFO)
        self.zoom = OptionRow(sec.body, "Phóng to", list(ZOOM_LABELS.values()), on_change=self._zoom_changed)
        self.off_x = SliderRow(sec.body, "Canh ngang", from_=-50, to=50, step=1, suffix="%",
                               on_change=s(cfg, "offset_x", cast=int),
                               info="Dời nội dung sang trái (âm) / phải (dương). Cắt vừa khung: chọn phần giữ lại; phóng "
                                    "to: chọn vùng phóng. 0 = giữa.")
        self.off_y = SliderRow(sec.body, "Canh dọc", from_=-50, to=50, step=1, suffix="%",
                               on_change=s(cfg, "offset_y", cast=int),
                               info="Dời nội dung lên (âm) / xuống (dương) — vd nền mờ trên-dưới mà muốn video nằm cao "
                                    "hơn để chừa chỗ phụ đề. 0 = giữa.")
        self.speed = OptionRow(sec.body, "Tốc độ", list(SPEED_LABELS.values()), on_change=self._speed_changed)
        pack_rows(sec.body, [self.zoom, self.off_x, self.off_y, self.speed])
        self.flip = SwitchRow(sec.body, "Lật ngang video (soi gương)", on_change=s(cfg, "flip_h"))
        self.flip.pack(fill="x", pady=3)

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
                                            "làm tới ô «Chạy đến» rồi chờ bạn duyệt; «Tự xuất» xuất luôn theo mẫu.")
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
            self.zoom.set(_label_for(ZOOM_LABELS, int(cfg.zoom)))
            self.off_x.set(int(getattr(cfg, "offset_x", 0)))
            self.off_y.set(int(getattr(cfg, "offset_y", 0)))
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
        finally:
            self._loading = False
        self.update_fit_hint()
        self.update_own()
        self._update_gpu_hint()
        self._name_example()
        self._refresh_watch()

    def on_active_changed(self) -> None:
        self.update_fit_hint()
        self.update_own()
        self._name_example()

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
        self.update_watch_status()

    def _watch_toggled(self, on: bool) -> None:
        if self._loading:
            return
        self.state.ui["watch_enable"] = bool(on)
        self.state.mark_dirty()
        if on:
            self.window.watch_started()
        self.update_watch_status()

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
            text = ("Đang giữ tỉ lệ gốc của video → kiểu khung (nền mờ / viền đen) và canh nội dung không có tác dụng. "
                    "Chọn tỉ lệ khác (vd. 9:16) trên thanh khung xem trước.")
        elif w and h and target and abs(w / h - target) < 0.02:
            text = (f"Video đang chọn ({w}×{h}) đã đúng tỉ lệ {cfg.ratio.split(' ')[0]} → vừa khít khung, «Cách vừa "
                    "khung» không thay đổi gì. Viền đen / nền mờ chỉ xuất hiện khi tỉ lệ video khác tỉ lệ xuất "
                    "(vd. video ngang 16:9 → 9:16).")
        self.fit_hint.configure(text=text)
        if text and not self.fit_hint.winfo_ismapped():
            self.fit_hint.pack(fill="x", pady=(0, 3), before=self.zoom)
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

    def _zoom_changed(self, label: str) -> None:
        val = next((k for k, v in ZOOM_LABELS.items() if v == label), 100)
        self.set_value(self.state.video, "zoom", int(val))

    def _speed_changed(self, label: str) -> None:
        val = next((k for k, v in SPEED_LABELS.items() if v == label), 100)
        self.set_value(self.state.video, "speed", int(val), preview=False)

    # ── Hàng đợi ─────────────────────────────────────────────────────────
    def _add_files(self) -> None:
        self.queue.add_files()

    def _add_folder(self) -> None:
        self.queue.add_folder()

    def _clear(self) -> None:
        self.queue.clear()
