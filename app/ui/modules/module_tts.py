"""Module Lồng tiếng: bật + giọng đọc (VieNeu / Edge) ở đầu, nhiều giọng theo người nói, giọng của video đang chọn;
khớp thời gian và giọng đã tạo (dùng lại) thu gọn bên dưới."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.config.paths import VOICE_DIR
from app.core.state import SPEAKER_COUNTS, TTS_LABELS, TTS_PROVIDERS, TTS_SOURCE_LABELS, TTS_SOURCES
from app.services.tts.base import Voice
from app.services.tts.generator import make_engine, preview_text, synthesize_cached, uses_language_list, voice_id_for
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.common import ButtonRow, EntryRow, OptionRow, Section, SliderRow, StatusLabel, SwitchRow, hint, pack_rows
from app.ui.widgets.ui import button, relabel
from app.core.languages import LocaleLabels
from app.ui.progress_panel import friendly_error

logger = logging.getLogger(__name__)
SPEED_OPTIONS = ["0.8", "0.9", "0.95", "1.0", "1.05", "1.1", "1.15", "1.2", "1.3", "1.5", "1.8", "2.0"]


class TTSModule(BaseModule):
    key = "tts"
    title = "Lồng tiếng"
    icon = "🔊"
    section = "tts"

    def build(self) -> None:
        cfg = self.state.tts
        s = self.setter
        self._voices: list[Voice] = []
        self._filtered: list[Voice] = []
        self._voice_by_label: dict[str, Voice] = {}
        self._engine = None

        sec = Section(self, "Lồng tiếng")
        sec.pack(fill="x", pady=4)
        self.enable = SwitchRow(sec.body, "Thêm lồng tiếng vào video", on_change=s(cfg, "enable_tts", preview=False))
        self.enable.pack(fill="x", pady=(0, 2))
        self.source = OptionRow(sec.body, "Đọc từ", TTS_SOURCES, labels=TTS_SOURCE_LABELS,
                                on_change=s(cfg, "tts_source", preview=False))
        self.provider = OptionRow(sec.body, "Bộ đọc", TTS_PROVIDERS, labels=TTS_LABELS, on_change=self._provider_changed,
                                  info="VieNeu (khuyên dùng): giọng Việt chạy trên máy, miễn phí, không giới hạn lượt. "
                                       "Edge TTS: cần mạng, nhiều thứ tiếng. Câu VieNeu không đọc được (lỗi) thì đọc "
                                       "tạm bằng Edge thay vì để im lặng; câu đọc chưa đạt thì VieNeu tự đọc lại.")
        pack_rows(sec.body, [self.source, self.provider])
        self.provider_hint = hint(sec.body, "")
        self.provider_hint.pack(fill="x")
        self.vieneu_frame = ctk.CTkFrame(sec.body, fg_color="transparent")
        self.vieneu_install = ButtonRow(self.vieneu_frame, [("⬇ Cài VieNeu (một lần)", self._install_vieneu,
                                                             T.primary_button())])
        self.clone_row = ButtonRow(self.vieneu_frame, [("🎤 Thêm giọng clone…", self._add_clone_voice, None)])
        self.clone_row.pack(fill="x", pady=(2, 2))
        hint(self.vieneu_frame, "Giọng clone: file WAV 5–15 giây, rõ tiếng (bỏ vào thư mục voices/).").pack(fill="x")

        sec = Section(self, "Giọng đọc")
        sec.pack(fill="x", pady=4)
        self.search = EntryRow(sec.body, "", placeholder="Tìm giọng…", label_width=0, on_change=lambda _v: self._fill_voice_lists())
        self.search.entry.bind("<KeyRelease>", lambda _e: self._fill_voice_lists())
        self.language = OptionRow(sec.body, "Ngôn ngữ", ["vi-VN"], on_change=self._language_changed, labels=LocaleLabels())
        self.voice = OptionRow(sec.body, "Giọng", ["(đang tải…)"], on_change=self._voice_changed)
        pack_rows(sec.body, [self.search, self.language, self.voice])
        ButtonRow(sec.body, [("▶ Nghe thử giọng", self._preview_voice, T.primary_button()),
                             ("↻ Tải lại danh sách", lambda: self._load_voices(force=True), None)]).pack(fill="x", pady=(4, 2))
        self.voice_status = StatusLabel(sec.body, "")
        self.voice_status.pack(fill="x")
        self.speed = OptionRow(sec.body, "Tốc độ đọc", SPEED_OPTIONS, on_change=lambda v: self.set_value(cfg, "tts_speed", float(v), preview=False))
        self.speed.pack(fill="x", pady=3)
        # cao độ chỉ Edge đổi được — VieNeu (mặc định) thì ẩn hẳn, không để thanh trượt bị khoá
        self.pitch = SliderRow(sec.body, "Cao độ", from_=-50, to=50, step=5, fmt="{:+.0f}", suffix="Hz",
                               on_change=s(cfg, "tts_pitch", cast=int, preview=False))
        self.volume_hint = hint(sec.body, "Âm lượng lồng tiếng, âm gốc / nền: khối «Âm thanh» bên dưới.")
        self.volume_hint.pack(fill="x")

        sec = Section(self, "Nhiều giọng theo người nói")
        sec.pack(fill="x", pady=4)
        self.multi = SwitchRow(sec.body, "Mỗi người nói một giọng", on_change=self._multi_changed,
                               info="Máy nhận ra ai nói câu nào (tự chạy ở bước «Chuẩn bị», hoặc bấm «Nhận diện người "
                                    "nói»), đoán Nam / Nữ rồi lồng mỗi người một giọng theo «Giọng ưu tiên».")
        self.multi.pack(fill="x")
        self.multi_frame = ctk.CTkFrame(sec.body, fg_color="transparent")
        self.speaker_count = OptionRow(self.multi_frame, "Số người nói", SPEAKER_COUNTS,
                                       on_change=s(cfg, "speaker_count", preview=False))
        self.speaker_count.pack(fill="x", pady=(6, 3))
        from app.ui.widgets.voice_priority import VoicePriority

        # giọng ưu tiên Nam 1–4 / Nữ 1–4 (lưu theo mẫu) — đổi thì thẻ người nói gán lại ngay
        self.priority = VoicePriority(self.multi_frame, self, on_change=lambda: self.speakers.refresh())
        self.priority.pack(fill="x", pady=(2, 4))
        ButtonRow(self.multi_frame, [("🎙 Nhận diện người nói", self._detect_speakers, None),
                                     ("📝 Gán từng câu…", self._assign_speakers, None)]).pack(fill="x", pady=(4, 2))
        self.speaker_status = StatusLabel(self.multi_frame, "")
        self.speaker_status.pack(fill="x")
        from app.ui.widgets.speaker_panel import SpeakerPanel

        self.speakers = SpeakerPanel(self.multi_frame, self)
        self.speakers.pack(fill="x", pady=(4, 0))

        sec = Section(self, "Giọng của video đang chọn")
        sec.pack(fill="x", pady=4)
        self.track_status = StatusLabel(sec.body, "")
        self.track_status.pack(fill="x")
        self.gen_btn = button(sec.body, "Tạo giọng cho video này", icon_name="sparkle", size="sm",
                              command=self._generate_now,
                              tip="Tạo giọng theo cấu hình hiện tại để nghe trước (Chuẩn bị / xuất video cũng tự tạo)")
        self.gen_btn.pack(fill="x", pady=(4, 2))
        hint(sec.body, "Câu không đổi lấy lại từ bộ nhớ nên tạo lại rất nhanh.").pack(fill="x")

        # âm thanh cả video (âm gốc / nền, khử giọng gốc, to nhỏ lồng tiếng) — trước nằm ở tab Video
        from app.ui.widgets.audio_section import AudioSection

        self.audio = AudioSection(self)

        sec = self.timing_sec = Section(self, "Khớp thời gian", collapsed=True,
                                        info="Giọng luôn khớp mềm với hình: câu được vào sớm một chút khi câu trước "
                                             "đã đọc xong, câu dài đọc nhanh dần (tối đa mức «Nén tối đa»), câu "
                                             "bị trễ đọc nhanh hơn để đuổi kịp hình. Câu vẫn tràn thì AI tự rút gọn "
                                             "bản dịch rồi đọc lại. Phụ đề dời theo đúng chỗ giọng đọc.")
        sec.pack(fill="x", pady=4)
        # trần TỔNG (tốc độ đọc × nén cho vừa khung) — quá 1,25× giọng méo, nuốt chữ (câu còn tràn: AI rút gọn bản dịch)
        self.max_tempo = SliderRow(sec.body, "Nén tối đa", from_=1.0, to=1.25, step=0.05, fmt="{:.2f}", suffix="×",
                                   on_change=self._timing_changed("max_tempo"),
                                   info="Câu dài được đọc nhanh hơn tối đa bao nhiêu lần (tính cả tốc độ đọc). Quá 1,25× "
                                        "giọng dễ méo, nuốt chữ.")
        self.min_gap = SliderRow(sec.body, "Nghỉ giữa câu", from_=0, to=1, step=0.02, fmt="{:.2f}", suffix="s",
                                 on_change=self._timing_changed("min_gap"),
                                 info="Khoảng lặng tối thiểu giữa hai câu thoại.")
        pack_rows(sec.body, [self.max_tempo, self.min_gap])

        sec = Section(self, "Giọng đã tạo", subtitle="dùng lại, không đọc lại", collapsed=True)
        sec.pack(fill="x", pady=4)
        hint(sec.body, "Giữ nguyên giọng đã có của video, chỉ đổi nhạc / phụ đề / hiệu ứng: chọn một giọng đã tạo — xuất "
                       "video sẽ dùng thẳng file đó, không tạo giọng mới.").pack(fill="x", pady=(0, 4))
        ButtonRow(sec.body, [("Mở danh sách giọng đã tạo…", self._open_library, None)]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("📂 Chọn file giọng…", self._pick_voice, None),
                             ("✕ Thôi dùng", self._unpick_voice, None)]).pack(fill="x", pady=2)
        self.lib_status = StatusLabel(sec.body, "")
        self.lib_status.pack(fill="x", pady=(2, 0))

    def _timing_changed(self, field: str):
        setter = self.setter(self.state.tts, field, cast=float, preview=False)

        def cb(v: float) -> None:
            setter(v)
            self._timing_summary()
        return cb

    def _timing_summary(self) -> None:
        cfg = self.state.tts
        self.timing_sec.set_subtitle(f"nén tối đa {min(cfg.max_tempo, 1.25):.2f}× · nghỉ {cfg.min_gap:.2f} s")

    # ── State → widget ────────────────────────────────────────────────────
    def refresh(self) -> None:
        cfg = self.state.tts
        self._loading = True
        try:
            self.enable.set(cfg.enable_tts)
            self.provider.set(cfg.tts_provider)
            self.source.set(cfg.tts_source)
            sp = f"{cfg.tts_speed:g}"
            if sp not in SPEED_OPTIONS:
                sp = min(SPEED_OPTIONS, key=lambda v: abs(float(v) - cfg.tts_speed))
            self.speed.set(sp)
            self.pitch.set(cfg.tts_pitch)
            self.max_tempo.set(min(cfg.max_tempo, 1.25))
            self.min_gap.set(cfg.min_gap)
            self._timing_summary()
            self.multi.set(cfg.multi_voice)
            self.speaker_count.set(cfg.speaker_count)
            self.audio.refresh()
        finally:
            self._loading = False
        self._layout_multi()
        self._layout_provider()
        self._load_voices()
        self.on_active_changed()

    def on_active_changed(self) -> None:
        item = self.state.active
        if self.state.tts.multi_voice:
            self.speakers.refresh()
        sub = self.state.subtitle
        self.audio.novocal_status()
        if (self.state.tts.tts_provider.startswith("VieNeu") and self.state.tts.tts_source == TTS_SOURCES[0]
                and sub.translate_target != "Tiếng Việt"):
            self.voice_status.warn(f"VieNeu chỉ đọc tiếng Việt — đang dịch sang {sub.translate_target}: chọn "
                                   "«Edge TTS» + giọng đúng ngôn ngữ đó.")
        self._show_track(item)

    def _show_track(self, item) -> None:
        """Giọng của video đang chọn: khớp cấu hình hiện tại / bản cũ (đã đổi giọng, tốc độ, lời thoại) / chưa có."""
        if item is None:
            self.track_status.info("Chọn một video trong hàng đợi.")
            self.lib_status.info("")
            return
        if item.voice_locked and item.voice_path and Path(item.voice_path).exists():
            name = Path(item.voice_path).name
            self.lib_status.ok(f"Đang dùng giọng có sẵn: {name} — xuất video không tạo giọng mới.")
            self.track_status.ok(f"Dùng giọng có sẵn: {name} (đổi ở «Giọng đã tạo» bên dưới).")
            relabel(self.gen_btn, "Tạo giọng cho video này")
            return
        self.lib_status.info("Chưa chọn — xuất video tự tạo giọng theo cấu hình ở trên.")
        if not self.state.tts.enable_tts:
            self.track_status.info("Lồng tiếng đang tắt.")
            return
        try:
            path, fresh, _cut = self.window.pipeline_for(item).current_voice_track(item)
        except Exception:                                  # noqa: BLE001 — chỉ là dòng trạng thái
            path, fresh = None, False
        if path is not None and fresh:
            self.track_status.ok(f"Giọng khớp cấu hình hiện tại: {path.name}")
            relabel(self.gen_btn, "Tạo lại giọng")
        elif path is not None:
            self.track_status.warn("Giọng đang nghe là bản cũ (đã đổi giọng, tốc độ hoặc lời thoại) — bấm «Tạo lại "
                                   "giọng» để nghe bản mới. Xuất video cũng tự tạo lại.")
            relabel(self.gen_btn, "Tạo lại giọng")
        else:
            self.track_status.info("Chưa có giọng cho video này — bấm «Tạo giọng» để nghe trước, hoặc «Chuẩn bị».")
            relabel(self.gen_btn, "Tạo giọng cho video này")

    def _layout_provider(self) -> None:
        p = self.state.tts.tts_provider
        self.vieneu_frame.pack_forget()
        if p.startswith("VieNeu"):
            from app.services.tts.vieneu import is_installed

            hint_text = "25 giọng Việt (Bắc / Trung / Nam), có clone giọng. Câu đọc lỗi tự đọc lại."
            self.vieneu_frame.pack(fill="x", pady=(4, 0))
            if is_installed():                    # đã cài → chỉ còn nút thêm giọng clone
                self.vieneu_install.pack_forget()
            elif not self.vieneu_install.winfo_ismapped():
                self.vieneu_install.pack(fill="x", pady=(2, 0), before=self.clone_row)
        else:
            hint_text = "Tiếng Việt: HoaiMy (nữ), NamMinh (nam) + các giọng 🌐 đa ngôn ngữ."
        self.provider_hint.configure(text=hint_text)
        if not uses_language_list(p):
            self.language.pack_forget()
        else:
            if not self.language.winfo_ismapped():
                self.language.pack(fill="x", pady=3, before=self.voice)
        if p == "Edge TTS":
            if not self.pitch.winfo_ismapped():
                self.pitch.pack(fill="x", pady=3, before=self.volume_hint)
        else:
            self.pitch.pack_forget()

    # ── Voices ────────────────────────────────────────────────────────────
    def _provider_changed(self, v: str) -> None:
        self.set_value(self.state.tts, "tts_provider", v, preview=False)
        self._engine = None
        self._layout_provider()
        self._load_voices()
        self._layout_multi()                    # giọng ưu tiên chọn trong danh sách giọng của engine mới

    def _load_voices(self, force: bool = False) -> None:
        cfg = self.state.tts
        # mỗi lần mở tab TTS đều refresh → chỉ tải lại (mạng / đọc cache) khi đổi nhà cung cấp hoặc cài đặt (API key…)
        sig = (cfg.tts_provider, repr(self.state.settings))
        if not force and self._voices and getattr(self, "_voices_sig", None) == sig:
            self._fill_voice_lists()
            return
        self._voices_sig = None
        self.voice_status.info("Đang tải danh sách giọng…")
        self.voice.set_values(["Đang tải danh sách giọng…"], keep=False)
        self.voice.menu.configure(state="disabled")
        try:
            engine = make_engine(cfg, self.state.settings)
        except Exception as exc:
            self.voice_status.error(str(exc))
            return
        if force and hasattr(engine, "_voices"):
            engine._voices = None
        self._engine = engine
        if hasattr(engine, "log"):
            engine.log = self.window.log_bg

        def done(result, error):
            self.voice.menu.configure(state="normal")
            if error:
                self.voice_status.error(f"Không tải được giọng: {error} — bấm «Tải danh sách giọng» để thử lại.")
                self.voice.set_values(["(lỗi — bấm Tải danh sách giọng)"], keep=False)
                return
            self._voices = result or []
            self._voices_sig = sig
            self._fill_voice_lists()
            self.voice_status.info(f"{len(self._voices)} giọng khả dụng.")

        self.window.run_bg(engine.list_voices, done)

    def _fill_voice_lists(self) -> None:
        cfg = self.state.tts
        p = cfg.tts_provider
        q = self.search.get().strip().lower()
        self._loading = True
        try:
            if not uses_language_list(p):
                subset = self._voices
                wanted = voice_id_for(cfg)
            else:
                locales: dict[str, None] = {}
                for v in self._voices:
                    locales.setdefault(v.locale, None)
                loc_list = list(locales) or ["vi-VN"]
                want = cfg.edge_language
                if want not in loc_list:
                    want = loc_list[0]
                self.language.set_values(loc_list, keep=False)
                self.language.set(want)
                subset = [v for v in self._voices if v.locale == want]
                wanted = cfg.edge_voice
            if q:
                subset = [v for v in subset if q in v.label.lower() or q in v.id.lower()]
            self._filtered = subset
            self._voice_by_label = {v.label: v for v in subset}
            labels = list(self._voice_by_label) or ["(không có giọng)"]
            self.voice.set_values(labels, keep=False)
            cur = next((v.label for v in subset if v.id == wanted), labels[0])
            self.voice.set(cur)
            if subset and wanted not in {v.id for v in subset} and not q:
                self._store_voice(subset[0], auto=True)        # máy tự chọn giọng đầu danh sách
        finally:
            self._loading = False

    def _store_voice(self, v: Voice, *, auto: bool = False) -> None:
        cfg = self.state.tts
        if cfg.tts_provider.startswith("VieNeu"):
            cfg.vieneu_voice = v.id
        else:
            cfg.edge_voice, cfg.edge_language = v.id, v.locale
        self.state.config_changed("tts", auto=auto)

    def _language_changed(self, loc: str) -> None:
        self.state.tts.edge_language = loc
        self._fill_voice_lists()
        self.state.config_changed("tts")
        self._layout_multi()

    def _voice_changed(self, label: str) -> None:
        v = self._voice_by_label.get(label)
        if v:
            self._store_voice(v)

    # ── Nhiều giọng ───────────────────────────────────────────────────────
    def _layout_multi(self) -> None:
        if self.state.tts.multi_voice:
            if not self.multi_frame.winfo_ismapped():
                self.multi_frame.pack(fill="x")
            self.priority.refresh()
            self.speakers.refresh()
        else:
            self.multi_frame.pack_forget()

    def _multi_changed(self, v: bool) -> None:
        self.set_value(self.state.tts, "multi_voice", bool(v), preview=False)
        self._layout_multi()

    def _detect_speakers(self) -> None:
        from app.services.diarize import onnx_available

        item = self.state.active
        if item is None:
            messagebox.showinfo("Edit Studio", "Hãy nạp và chọn một video trước.")
            return
        if not item.srt_path or not Path(item.srt_path).exists():
            messagebox.showinfo("Edit Studio", "Video này chưa có phụ đề gốc.\n\nVào Lời thoại › «Nguồn & dịch» bấm «Nhận diện lại video này» "
                                               "(hoặc chọn file SRT) trước — máy dựa vào từng câu phụ đề để biết ai nói.")
            return
        if not onnx_available():
            self.speaker_status.error("Thiếu thư viện onnxruntime (bản mã nguồn: pip install onnxruntime) — tạm dùng «Gán từng câu…»")
            return
        pipe = self.window.pipeline_for(item)
        self.speaker_status.warn("Đang nhận diện người nói… (xem log)")

        def work(cancel):
            return pipe.diarize(item, progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if error:
                self.speaker_status.error(f"Lỗi: {friendly_error(str(error))}")
                return
            n = len(result.speakers)
            self.speaker_status.ok(f"Tìm thấy {n} người nói. Nghe thử «Nghe giọng gốc» rồi chọn giọng cho từng người; "
                                   "nhận sai câu nào thì sửa ở «Gán từng câu…».")
            self.speakers.refresh(force=True)

        self.window.run_task("Nhận diện người nói", work, done)

    def _assign_speakers(self) -> None:
        item = self.state.active
        if item is None:
            messagebox.showinfo("Edit Studio", "Hãy nạp và chọn một video trước.")
            return
        if not item.srt_path or not Path(item.srt_path).exists():
            messagebox.showinfo("Edit Studio", "Video này chưa có phụ đề gốc — nhận diện (Lời thoại › «Nguồn & dịch») hoặc chọn file SRT trước.")
            return
        from app.ui.widgets.speaker_panel import SpeakerAssignDialog

        def saved() -> None:
            self.speaker_status.ok("Đã lưu người nói từng câu — chọn giọng cho từng người bên dưới.")
            self.speakers.refresh(force=True)

        SpeakerAssignDialog(self.window, item, on_saved=saved)

    # ── VieNeu ────────────────────────────────────────────────────────────
    def _install_vieneu(self) -> None:
        from app.ui.local_ai import install_vieneu

        def done(ok: bool, error: Exception | None) -> None:
            if not self.winfo_exists():
                return
            if not ok:
                self.voice_status.error(f"Cài VieNeu thất bại: {error or 'xem log'}")
                return
            self.voice_status.ok("VieNeu đã cài — đang nạp giọng…")
            self._layout_provider()
            self._load_voices(force=True)

        if install_vieneu(self.window, done):
            self.voice_status.warn("Đang cài VieNeu (vài phút)… xem log")

    def _add_clone_voice(self) -> None:
        from app.services.tts.vieneu import CLONE_DIR

        p = filedialog.askopenfilename(title="Chọn file giọng mẫu (WAV/MP3, 5–15 giây)",
                                       filetypes=[("Âm thanh", "*.wav *.mp3"), ("Tất cả", "*.*")])
        if not p:
            return
        CLONE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, CLONE_DIR / Path(p).name)
        self.window.log(f"Đã thêm giọng clone: {Path(p).name}")
        self._load_voices(force=True)

    # ── Hành động ────────────────────────────────────────────────────────
    def _preview_voice(self) -> None:
        cfg = self.state.tts
        engine = self._engine
        if engine is None:
            return
        v = self._voice_by_label.get(self.voice.get())
        vid = getattr(v, "id", None) or voice_id_for(cfg)
        text = preview_text(v.locale if v else cfg.edge_language)
        self.voice_status.info("Đang tạo mẫu…")

        def done(result, error):
            if error:
                self.voice_status.error(f"Không tạo được mẫu: {error}")
                return
            self.voice_status.info("Đang phát mẫu…")
            try:
                self.window.player.play(result)
            except Exception as exc:
                self.voice_status.error(f"Không phát được: {exc}")

        self.window.run_bg(lambda: synthesize_cached(engine, vid, text, speed=cfg.tts_speed, pitch=cfg.tts_pitch), done)

    def _generate_now(self) -> None:
        item = self.state.active
        if item is None:
            messagebox.showinfo("Edit Studio", "Hãy nạp và chọn một video trước.")
            return
        if not self.state.tts.enable_tts:
            messagebox.showinfo("Edit Studio", "Bật «Thêm lồng tiếng vào video» ở đầu tab trước.")
            return
        pipe = self.window.pipeline_for(item)
        self.track_status.warn("Đang tạo giọng… (xem tiến độ ở thanh dưới)")

        def work(cancel):
            # cùng đường với Chuẩn bị (tính cả Cắt, khớp thời lượng) → xuất video dùng lại đúng track này
            return pipe.voice_now(item, progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if error:
                self.track_status.error(f"Lỗi: {friendly_error(str(error))}")
            else:
                self.on_active_changed()
                self.state.mark_dirty()
                self.window.queue_update(item)
                self.window.preview.schedule_refresh(0)
                self.window.add_notification("Tạo giọng xong", f"{item.name}: {Path(str(result)).name}", "ok")

        self.window.run_task("Tạo giọng", work, done)

    def _pick_voice(self) -> None:
        item = self.state.active
        if item is None:
            messagebox.showinfo("Edit Studio", "Chọn video trong hàng đợi trước.")
            return
        p = filedialog.askopenfilename(title="Chọn file voice", initialdir=str(VOICE_DIR),
                                       filetypes=[("Âm thanh", "*.wav *.mp3 *.m4a"), ("Tất cả", "*.*")])
        if p:
            self.use_voice(p)

    def use_voice(self, path: str, use_srt: bool = True) -> None:
        item = self.state.active
        if item is None:
            return
        item.voice_path = path
        meta = Path(path).with_suffix(".json")
        if use_srt and meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                srt = data.get("synced_srt") or data.get("srt")
                if srt and Path(srt).exists():
                    if data.get("synced_srt"):
                        item.srt_translated_path = srt if self.state.tts.tts_source == TTS_SOURCES[0] else item.srt_translated_path
                        if self.state.tts.tts_source != TTS_SOURCES[0]:
                            item.srt_path = srt
                    elif not item.srt_path:
                        item.srt_path = srt
            except Exception:
                pass
        item.voice_locked = True                      # xuất video dùng thẳng file này, không tạo giọng mới
        self.state.tts.enable_tts = True
        self.state.config_changed("tts")
        self.refresh()
        self.window.queue_update(item)
        self.window.log(f"Dùng giọng có sẵn: {Path(path).name}")

    def _unpick_voice(self) -> None:
        item = self.state.active
        if item is None:
            return
        item.voice_path, item.voice_locked = None, False
        self.state.mark_dirty()
        self.on_active_changed()
        self.window.queue_update(item)
        self.window.preview.schedule_refresh(0)

    def _open_library(self) -> None:
        from app.ui.widgets.voice_library import VoiceLibraryDialog

        VoiceLibraryDialog(self.window, self)
