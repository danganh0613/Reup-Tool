"""Module TTS: thư viện voice đã tạo, dịch vụ/giọng đọc, đồng bộ mềm, dự án TTS."""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.ui.icons import EMOJI_ICONS, icon
from app.config.paths import VOICE_DIR
from app.core.state import ELEVEN_MODELS, GEMINI_TTS_MODELS, SPEAKER_COUNTS, TTS_PROVIDERS, TTS_SOURCES
from app.services.tts.base import Voice
from app.services.tts.generator import make_engine, preview_text, synthesize_cached, uses_language_list, voice_id_for
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.common import ButtonRow, EntryRow, OptionRow, Section, SliderRow, StatusLabel, SwitchRow, hint, pack_rows
from app.core.languages import LocaleLabels

logger = logging.getLogger(__name__)
SPEED_OPTIONS = ["0.8", "0.9", "0.95", "1.0", "1.05", "1.1", "1.15", "1.2", "1.3", "1.5", "1.8", "2.0"]


class TTSModule(BaseModule):
    key = "tts"
    title = "TTS"
    icon = "🔊"
    section = "tts"

    def build(self) -> None:
        cfg = self.state.tts
        s = self.setter
        self._voices: list[Voice] = []
        self._filtered: list[Voice] = []
        self._voice_by_label: dict[str, Voice] = {}
        self._engine = None

        sec = Section(self, "Lồng tiếng đã tạo")
        sec.pack(fill="x", pady=4)
        lib = ctk.CTkFrame(sec.body, fg_color=T.BG_INPUT, corner_radius=8)
        lib.pack(fill="x")
        row = ctk.CTkFrame(lib, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(row, text=" THƯ VIỆN VOICE ĐÃ TẠO", image=icon(EMOJI_ICONS["🔒"], 14, dy=1), compound="left", font=T.font(11, "bold"), text_color=T.TEXT_DIM).pack(side="left")
        ctk.CTkButton(row, text="Mở thư viện…", width=100, height=32, command=self._open_library, **T.primary_button()).pack(side="right")
        ButtonRow(lib, [("📂 Chọn file voice cũ…", self._pick_voice, None), ("✕ Bỏ chọn", self._unpick_voice, None)]).pack(fill="x", padx=8, pady=(0, 4))
        self.lib_status = StatusLabel(lib, "Chưa chọn voice cũ — render sẽ tạo giọng mới.")
        self.lib_status.pack(fill="x", padx=8, pady=(0, 8))
        self.enable = SwitchRow(sec.body, "Thêm lồng tiếng vào video", on_change=s(cfg, "enable_tts", preview=False))
        self.enable.pack(fill="x", pady=(8, 0))
        hint(sec.body, "Bật để tạo giọng đọc AI hoặc dùng lại voice trộn vào video MP4.").pack(fill="x")

        sec = Section(self, "Dịch vụ")
        sec.pack(fill="x", pady=4)
        self.provider = OptionRow(sec.body, "Nhà cung cấp", TTS_PROVIDERS, on_change=self._provider_changed)
        self.source = OptionRow(sec.body, "Đọc từ", TTS_SOURCES, on_change=s(cfg, "tts_source", preview=False))
        pack_rows(sec.body, [self.provider, self.source])
        ButtonRow(sec.body, [("Tải danh sách giọng", lambda: self._load_voices(force=True), None)]).pack(fill="x", pady=(4, 0))
        self.provider_hint = hint(sec.body, "")
        self.provider_hint.pack(fill="x")
        self.vieneu_frame = ctk.CTkFrame(sec.body, fg_color="transparent")
        ButtonRow(self.vieneu_frame, [("⬇ Cài VieNeu (local)", self._install_vieneu, T.primary_button()),
                                      ("🎤 Thêm giọng clone…", self._add_clone_voice, None)]).pack(fill="x", pady=(2, 2))
        hint(self.vieneu_frame, "VieNeu chạy trên CPU, không cần mạng sau khi tải model (~300MB). Giọng clone: file WAV "
                                "5–15 giây, rõ tiếng, bỏ vào thư mục voices/.").pack(fill="x")

        sec = Section(self, "Giọng đọc")
        sec.pack(fill="x", pady=4)
        self.search = EntryRow(sec.body, "", placeholder="Tìm giọng…", label_width=0, on_change=lambda _v: self._fill_voice_lists())
        self.search.entry.bind("<KeyRelease>", lambda _e: self._fill_voice_lists())
        self.language = OptionRow(sec.body, "Ngôn ngữ", ["vi-VN"], on_change=self._language_changed, labels=LocaleLabels())
        self.voice = OptionRow(sec.body, "Giọng", ["(đang tải…)"], on_change=self._voice_changed)
        pack_rows(sec.body, [self.search, self.language, self.voice])
        ButtonRow(sec.body, [("▶ Nghe thử giọng", self._preview_voice, T.primary_button()),
                             ("🎬 Đạo diễn âm thanh…", self._open_direction, None)]).pack(fill="x", pady=(4, 2))
        self.voice_status = StatusLabel(sec.body, "")
        self.voice_status.pack(fill="x")
        self.emotion = SwitchRow(sec.body, "AI tự đặt cảm xúc cho từng câu",
                                 on_change=s(cfg, "emotion_auto", preview=False))
        self.emotion.pack(fill="x", pady=(4, 0))
        hint(sec.body, "Nên để TẮT: cả video một giọng xuyên suốt theo «Phong cách» ở trên — tự nhiên nhất, ít lượt gọi "
                       "nhất. Câu nào muốn nhấn cảm xúc thì chỉnh tay ở tab «Lời thoại» → «Cảm xúc» (luôn được dùng). "
                       "Bật: dịch xong AI gán cảm xúc cho mọi câu (giọng đổi theo từng câu).").pack(fill="x")
        self.speed = OptionRow(sec.body, "Tốc độ đọc", SPEED_OPTIONS, on_change=lambda v: self.set_value(cfg, "tts_speed", float(v), preview=False))
        self.pitch = SliderRow(sec.body, "Cao độ (Edge)", from_=-50, to=50, step=5, fmt="{:+.0f}", suffix="Hz",
                               on_change=s(cfg, "tts_pitch", cast=int, preview=False))
        pack_rows(sec.body, [self.speed, self.pitch])
        hint(sec.body, "Âm lượng lồng tiếng chỉnh ở tab «Video».").pack(fill="x")
        self.eleven_frame = ctk.CTkFrame(sec.body, fg_color="transparent")
        self.eleven_model = OptionRow(self.eleven_frame, "Model", ELEVEN_MODELS, on_change=s(cfg, "eleven_model", preview=False))
        self.stability = SliderRow(self.eleven_frame, "Stability", from_=0, to=1, step=0.05, fmt="{:.2f}",
                                   on_change=s(cfg, "eleven_stability", cast=float, preview=False))
        self.similarity = SliderRow(self.eleven_frame, "Similarity", from_=0, to=1, step=0.05, fmt="{:.2f}",
                                    on_change=s(cfg, "eleven_similarity", cast=float, preview=False))
        self.style = SliderRow(self.eleven_frame, "Style", from_=0, to=1, step=0.05, fmt="{:.2f}",
                               on_change=s(cfg, "eleven_style", cast=float, preview=False))
        self.boost = SwitchRow(self.eleven_frame, "Speaker boost", on_change=s(cfg, "eleven_speaker_boost", preview=False))
        pack_rows(self.eleven_frame, [self.eleven_model, self.stability, self.similarity, self.style, self.boost])
        self.eleven_quota = StatusLabel(self.eleven_frame, "")
        self.eleven_quota.pack(fill="x")
        hint(self.eleven_frame, "Danh sách có cả giọng 🌐 do cộng đồng ElevenLabs chia sẻ (tiếng Việt, xếp theo lượt dùng) — "
                                "lần đầu đọc máy tự thêm giọng vào tài khoản của bạn.").pack(fill="x")
        from app.services.tts.gemini_tts import STYLES

        self.gemini_frame = ctk.CTkFrame(sec.body, fg_color="transparent")
        self.gemini_style = OptionRow(self.gemini_frame, "Phong cách", list(STYLES), on_change=self._gemini_style_changed)
        self.gemini_custom = EntryRow(self.gemini_frame, "Lời dặn", value=cfg.gemini_style_custom,
                                      placeholder="vd: Đọc giọng miền Tây, chậm rãi, hài hước",
                                      on_change=s(cfg, "gemini_style_custom", preview=False))
        self.gemini_model = OptionRow(self.gemini_frame, "Model", GEMINI_TTS_MODELS,
                                      on_change=s(cfg, "gemini_tts_model", preview=False))
        pack_rows(self.gemini_frame, [self.gemini_style, self.gemini_custom, self.gemini_model])
        hint(self.gemini_frame, "Gemini TTS dùng key Gemini trong Cài đặt. Gói miễn phí chỉ vài lượt/ngày (mỗi câu một lượt) "
                                "— video dài nên bật thanh toán cho key (rất rẻ) hoặc nhập nhiều key.").pack(fill="x")

        sec = Section(self, "Nhiều giọng theo người nói")
        sec.pack(fill="x", pady=4)
        self.multi = SwitchRow(sec.body, "Mỗi người nói một giọng", on_change=self._multi_changed)
        self.multi.pack(fill="x")
        hint(sec.body, "Video có nhiều người nói: máy nhận ra ai nói câu nào rồi lồng tiếng mỗi người một giọng. "
                       "Chọn giọng cho từng người bên dưới; người chưa chọn dùng giọng chính ở trên.").pack(fill="x")
        self.multi_frame = ctk.CTkFrame(sec.body, fg_color="transparent")
        self.speaker_count = OptionRow(self.multi_frame, "Số người nói", SPEAKER_COUNTS,
                                       on_change=s(cfg, "speaker_count", preview=False))
        self.speaker_count.pack(fill="x", pady=(6, 3))
        ButtonRow(self.multi_frame, [("🎙 Nhận diện người nói", self._detect_speakers, T.primary_button()),
                                     ("📝 Gán từng câu…", self._assign_speakers, None)]).pack(fill="x", pady=(4, 2))
        self.speaker_status = StatusLabel(self.multi_frame, "")
        self.speaker_status.pack(fill="x")
        from app.ui.widgets.speaker_panel import SpeakerPanel

        self.speakers = SpeakerPanel(self.multi_frame, self)
        self.speakers.pack(fill="x", pady=(4, 0))

        sec = Section(self, "Đồng bộ mềm")
        sec.pack(fill="x", pady=4)
        self.soft = SwitchRow(sec.body, "Bật Soft Timing (dời câu vào khoảng lặng, nén ≤1.10×)", on_change=s(cfg, "soft_timing", preview=False))
        self.natural = SwitchRow(sec.body, "Voice tự nhiên (không nén, phụ đề dời theo giọng)", on_change=s(cfg, "natural_sync", preview=False))
        # trần TỔNG (tốc độ đọc × nén cho vừa khung) — quá 1,25× giọng méo, nuốt chữ (câu còn tràn: AI rút gọn bản dịch)
        self.max_tempo = SliderRow(sec.body, "Nén tối đa (tổng)", from_=1.0, to=1.25, step=0.05, fmt="{:.2f}", suffix="×",
                                   on_change=s(cfg, "max_tempo", cast=float, preview=False))
        self.min_gap = SliderRow(sec.body, "Nghỉ giữa câu", from_=0, to=1, step=0.02, fmt="{:.2f}", suffix="s",
                                 on_change=s(cfg, "min_gap", cast=float, preview=False))
        pack_rows(sec.body, [self.soft, self.natural, self.max_tempo, self.min_gap])
        hint(sec.body, "💡 Gợi ý:\n• Video dài (> 1 phút), kể chuyện, review: nên bật Soft Timing.\n"
                       "• Video ngắn TikTok / Shorts: giữ nguyên (tắt Soft Timing, bật Voice tự nhiên).").pack(fill="x")

        sec = Section(self, "Dự án TTS")
        sec.pack(fill="x", pady=4)
        ButtonRow(sec.body, [("🎧 Tạo giọng cho video đang chọn", self._generate_now, T.primary_button()),
                             ("Quản lý dự án TTS…", self._open_library, None)]).pack(fill="x", pady=2)
        self.track_status = StatusLabel(sec.body, "")
        self.track_status.pack(fill="x")
        hint(sec.body, "Xem các track đã tạo, dùng lại hoặc nghe thử. Cue chưa đổi sẽ lấy từ cache nên tạo lại rất nhanh.").pack(fill="x")

    # ── State → widget ────────────────────────────────────────────────────
    def refresh(self) -> None:
        cfg = self.state.tts
        self._loading = True
        try:
            self.enable.set(cfg.enable_tts)
            self.provider.set(cfg.tts_provider)
            self.source.set(cfg.tts_source)
            self.eleven_model.set(cfg.eleven_model)
            self.stability.set(cfg.eleven_stability)
            self.similarity.set(cfg.eleven_similarity)
            self.style.set(cfg.eleven_style)
            self.boost.set(cfg.eleven_speaker_boost)
            sp = f"{cfg.tts_speed:g}"
            if sp not in SPEED_OPTIONS:
                sp = min(SPEED_OPTIONS, key=lambda v: abs(float(v) - cfg.tts_speed))
            self.speed.set(sp)
            self.pitch.set(cfg.tts_pitch)
            self.soft.set(cfg.soft_timing)
            self.natural.set(cfg.natural_sync)
            self.max_tempo.set(cfg.max_tempo)
            self.min_gap.set(cfg.min_gap)
            self.multi.set(cfg.multi_voice)
            self.emotion.set(cfg.emotion_auto)
            self.gemini_style.set(cfg.gemini_style)
            self.gemini_custom.set(cfg.gemini_style_custom)
            self.gemini_model.set(cfg.gemini_tts_model)
            self.speaker_count.set(cfg.speaker_count)
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
        if item is None:
            self.track_status.info("")
            self.lib_status.info("Chưa chọn voice cũ — render sẽ tạo giọng mới.")
            return
        if item.voice_path and Path(item.voice_path).exists():
            self.track_status.ok(f"Đã có track: {Path(item.voice_path).name}")
            self.lib_status.ok(f"Dùng voice: {Path(item.voice_path).name} — render sẽ KHÔNG gọi TTS.")
        else:
            self.track_status.info("Chưa có track giọng cho video này.")
            self.lib_status.info("Chưa chọn voice cũ — render sẽ tạo giọng mới.")

    def _layout_provider(self) -> None:
        p = self.state.tts.tts_provider
        self.eleven_frame.pack_forget()
        self.vieneu_frame.pack_forget()
        self.gemini_frame.pack_forget()
        hints = {
            "Edge TTS": "Edge TTS: miễn phí, cần mạng. Tiếng Việt: HoaiMy (nữ), NamMinh (nam) + các giọng 🌐 đa ngôn ngữ.",
            "ElevenLabs": "ElevenLabs: cần API key trong Cài đặt (nhiều key sẽ tự xoay khi hết quota). Có thư viện giọng Việt cộng đồng.",
            "Google Cloud TTS": "Google Chirp3-HD (30 giọng tiếng Việt chất lượng cao) / Neural2 / Wavenet: cần API key hoặc "
                                "Service Account JSON trong Cài đặt.",
            "FPT.AI": "FPT.AI: giọng Việt 3 miền (Bắc / Trung / Nam), cần key console.fpt.ai trong Cài đặt.",
        }
        if p.startswith("Gemini Live"):
            hint_text = ("Gemini Live: cùng 30 giọng Gemini, gói miễn phí KHÔNG giới hạn lượt (Gemini TTS chỉ 10 lượt/ngày "
                         "mỗi model). Đọc từng câu, vài câu song song; câu đọc sai tự đọc lại. Dùng key Gemini trong Cài đặt.")
        elif p.startswith("Gemini"):
            hint_text = ("Gemini TTS: 30 giọng đọc tiếng Việt tự nhiên, chỉnh phong cách & giọng miền bằng lời dặn. Gói miễn "
                         "phí: 3 lượt/phút · 10 lượt/ngày mỗi model (máy tự gộp ~25 câu một lượt).")
        elif p.startswith("VieNeu"):
            hint_text = "VieNeu: 25 giọng tiếng Việt chạy local (Bắc / Trung / Nam), có clone giọng."
        else:
            hint_text = hints.get(p, "")
        self.provider_hint.configure(text=hint_text)
        if p == "ElevenLabs":
            self.eleven_frame.pack(fill="x", pady=(4, 0))
        elif p.startswith("VieNeu"):
            self.vieneu_frame.pack(fill="x", pady=(4, 0))
        elif p.startswith("Gemini"):
            self.gemini_frame.pack(fill="x", pady=(4, 0))
            self._layout_gemini()
        if not uses_language_list(p):
            self.language.pack_forget()
        else:
            if not self.language.winfo_ismapped():
                self.language.pack(fill="x", pady=3, before=self.voice)
        self.pitch.slider.configure(state="normal" if p == "Edge TTS" else "disabled")

    def _layout_gemini(self) -> None:
        if self.state.tts.gemini_style == "Tự viết…":
            if not self.gemini_custom.winfo_ismapped():
                self.gemini_custom.pack(fill="x", pady=3, after=self.gemini_style)
        else:
            self.gemini_custom.pack_forget()
        if self.state.tts.tts_provider.startswith("Gemini Live"):      # Live tự chọn model Live (không phải model TTS)
            self.gemini_model.pack_forget()
        elif not self.gemini_model.winfo_manager():
            prev = self.gemini_custom if self.gemini_custom.winfo_manager() else self.gemini_style
            self.gemini_model.pack(fill="x", pady=3, after=prev)

    def _gemini_style_changed(self, v: str) -> None:
        self.set_value(self.state.tts, "gemini_style", v, preview=False)
        self._layout_gemini()

    # ── Voices ────────────────────────────────────────────────────────────
    def _provider_changed(self, v: str) -> None:
        self.set_value(self.state.tts, "tts_provider", v, preview=False)
        self._engine = None
        self._layout_provider()
        self._load_voices()

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
            if cfg.tts_provider == "ElevenLabs":
                self._load_quota()

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
                want = cfg.edge_language if p == "Edge TTS" else cfg.google_language
                if want not in loc_list:
                    want = loc_list[0]
                self.language.set_values(loc_list, keep=False)
                self.language.set(want)
                subset = [v for v in self._voices if v.locale == want]
                wanted = cfg.edge_voice if p == "Edge TTS" else cfg.google_voice
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
        if cfg.tts_provider == "ElevenLabs":
            cfg.eleven_voice_id, cfg.eleven_voice_name = v.id, v.name
        elif cfg.tts_provider.startswith("VieNeu"):
            cfg.vieneu_voice = v.id
        elif cfg.tts_provider.startswith("Gemini"):
            cfg.gemini_voice = v.id
        elif cfg.tts_provider.startswith("FPT"):
            cfg.fpt_voice = v.id
        elif cfg.tts_provider == "Google Cloud TTS":
            cfg.google_voice, cfg.google_language = v.id, v.locale
        else:
            cfg.edge_voice, cfg.edge_language = v.id, v.locale
        self.state.config_changed("tts", auto=auto)

    def _language_changed(self, loc: str) -> None:
        cfg = self.state.tts
        if cfg.tts_provider == "Google Cloud TTS":
            cfg.google_language = loc
        else:
            cfg.edge_language = loc
        self._fill_voice_lists()
        self.state.config_changed("tts")

    def _voice_changed(self, label: str) -> None:
        v = self._voice_by_label.get(label)
        if v:
            self._store_voice(v)

    def _load_quota(self) -> None:
        engine = self._engine
        if not hasattr(engine, "subscription"):
            return

        def done(result, error):
            if error or not result:
                self.eleven_quota.info("")
                return
            self.eleven_quota.info(f"Ký tự đã dùng: {result['used']:,}/{result['limit']:,} ({result['tier']})")

        self.window.run_bg(engine.subscription, done)

    # ── Nhiều giọng ───────────────────────────────────────────────────────
    def _layout_multi(self) -> None:
        if self.state.tts.multi_voice:
            if not self.multi_frame.winfo_ismapped():
                self.multi_frame.pack(fill="x")
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
            messagebox.showinfo("Edit Studio", "Video này chưa có phụ đề gốc.\n\nVào mục STT bấm «Nhận diện ngay» "
                                               "(hoặc chọn file SRT) trước — máy dựa vào từng câu phụ đề để biết ai nói.")
            return
        if not onnx_available():
            self.speaker_status.error("Thiếu thư viện onnxruntime — chạy: pip install onnxruntime  (hoặc dùng «Gán từng câu…»)")
            return
        pipe = self.window.pipeline_for(item)
        self.speaker_status.warn("Đang nhận diện người nói… (xem log)")

        def work(cancel):
            return pipe.diarize(item, progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if error:
                self.speaker_status.error(f"Lỗi: {error}")
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
            messagebox.showinfo("Edit Studio", "Video này chưa có phụ đề gốc — nhận diện (STT) hoặc chọn file SRT trước.")
            return
        from app.ui.widgets.speaker_panel import SpeakerAssignDialog

        def saved() -> None:
            self.speaker_status.ok("Đã lưu người nói từng câu — chọn giọng cho từng người bên dưới.")
            self.speakers.refresh(force=True)

        SpeakerAssignDialog(self.window, item, on_saved=saved)

    # ── VieNeu ────────────────────────────────────────────────────────────
    def _install_vieneu(self) -> None:
        from app.services.tts.vieneu import install_vieneu, is_installed

        if is_installed():
            self.voice_status.ok("VieNeu đã được cài.")
            self._load_voices(force=True)
            return
        if getattr(sys, "frozen", False):
            self.voice_status.error("Bản đóng gói (.exe / .app) không có VieNeu — chọn Edge TTS / ElevenLabs / Google, hoặc chạy bản mã nguồn (run.bat / run.command).")
            return
        self.voice_status.warn("Đang cài VieNeu (vài phút)… xem log")

        def done(result, error):
            if error or not result:
                self.voice_status.error(f"Cài VieNeu thất bại: {error or 'xem log'}")
            else:
                self.voice_status.ok("Đã cài VieNeu — đang nạp giọng…")
                self._load_voices(force=True)

        self.window.run_bg(lambda: install_vieneu(self.window.log_bg), done)

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
    def _open_direction(self) -> None:
        """Cửa sổ Đạo diễn âm thanh: chọn giọng + bánh xe cảm xúc, thử đọc (cache, không tốn lượt khi nghe lại)."""
        from app.ui.widgets.direction_studio import open_studio

        open_studio(self.window, self.state.settings, play=self.window.player.play)

    def _preview_voice(self) -> None:
        cfg = self.state.tts
        engine = self._engine
        if engine is None:
            return
        vid = voice_id_for(cfg)
        v = self._voice_by_label.get(self.voice.get())
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
        pipe = self.window.pipeline_for(item)
        self.track_status.warn("Đang tạo track giọng… (xem log)")

        def work(cancel):
            return pipe.generate_voice(item, progress=self.window.set_busy_progress, log=self.window.log_bg,
                                       cancel=cancel, force=True)

        def done(result, error):
            if error:
                self.track_status.error(f"Lỗi: {error}")
            else:
                self.on_active_changed()
                self.state.mark_dirty()
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
        self.state.tts.enable_tts = True
        self.state.config_changed("tts")
        self.refresh()
        self.window.queue_update(item)
        self.window.log(f"Dùng voice: {Path(path).name}")

    def _unpick_voice(self) -> None:
        item = self.state.active
        if item is None:
            return
        item.voice_path = None
        self.state.mark_dirty()
        self.on_active_changed()
        self.window.queue_update(item)

    def _open_library(self) -> None:
        from app.ui.widgets.voice_library import VoiceLibraryDialog

        VoiceLibraryDialog(self.window, self)
