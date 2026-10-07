"""Cửa sổ Cài đặt (toàn màn hình): Hệ thống & API · Phím tắt & Cẩm nang."""

from __future__ import annotations

import json
import shutil
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.ui.icons import EMOJI_ICONS, icon
from app.config.paths import APP_VERSION, CACHE_DIR, JOBS_DIR, PRON_FILE, TTS_CACHE_DIR, PREVIEW_CACHE_DIR
from app.core.platform import exe_name
from app.core.state import GPU_BACKENDS, TRANSLATE_PROVIDERS, VIDEO_CODECS
from app.services.ffmpeg import FFmpeg
from app.services.sysinfo import gpu_name
from app.services import groq_models
from app.services.gemini_models import migrate_model, text_models
from app.services.translate_router import DEFAULT_MODELS
from app.ui.widgets.ui import Select, button
from app.ui.widgets.common import EntryRow, OptionRow, SwitchRow, SliderRow, hint
from app.ui.widgets.dialogs import SHORTCUTS
from app.core.platform import open_path

KEY_LINKS = {"Gemini": "https://aistudio.google.com/apikey", "Groq": "https://console.groq.com/keys",
             "DeepSeek": "https://platform.deepseek.com/api_keys", "OpenAI": "https://platform.openai.com/api-keys",
             "ElevenLabs": "https://elevenlabs.io/app/settings/api-keys",
             "Google": "https://console.cloud.google.com/apis/credentials", "FPT": "https://console.fpt.ai/"}
MODEL_CHOICES = {"Gemini": [],          # lấy từ gemini_models.text_models() (có thể cập nhật từ Google)
                 "Groq": [],            # lấy từ groq_models.text_models() (có thể cập nhật từ Groq)
                 "DeepSeek": ["deepseek-chat", "deepseek-reasoner"],
                 "OpenAI": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o"]}

def _model_choices(provider: str) -> list[str]:
    if provider == "Groq":
        return groq_models.text_models()
    return text_models() if provider == "Gemini" else MODEL_CHOICES.get(provider, [])


GUIDE = [
    ("Cấu hình cài đặt & tăng tốc hệ thống", T.INFO, [
        ("Tăng tốc GPU (NVENC / QSV / AMF)", "Hệ thống tự quét card đồ hoạ (NVIDIA / AMD / Intel). Chọn «auto» để tự ưu tiên, "
                                             "hoặc chọn trực tiếp bộ mã hóa. Không dùng được sẽ tự chuyển sang CPU."),
        ("Chuẩn nén video (Codec)", "H.264 tương thích 100% thiết bị; HEVC nén sâu giảm ~40% dung lượng; AV1 chuẩn mới nhất."),
        ("Dịch thuật AI & từ điển thuật ngữ", "Kết nối Gemini, Groq, DeepSeek hoặc OpenAI, nhiều key xoay vòng khi hết quota. "
                                              "Nạp bảng thuật ngữ để dịch chuẩn tên riêng, xưng hô theo ngữ cảnh."),
        ("Giọng đọc cloud & local", "Edge TTS miễn phí, ElevenLabs/Google Cloud chất lượng cao (cần key), VieNeu tiếng Việt chạy local có clone giọng."),
        ("Thư mục xuất & mẫu", "Tuỳ chỉnh nơi lưu MP4/MP3. Lưu thiết lập thành mẫu ở ô «Mẫu» trên cùng (xuất / nhập file mẫu để chia sẻ)."),
    ]),
    ("Bộ công cụ video (F1 – F5)", T.SUCCESS, [
        ("F1 · Nạp video & hàng đợi", "Kéo thả hoặc mở video (MP4, MKV, MOV…). Ứng dụng tự đo độ phân giải, fps, kiểm tra luồng âm thanh."),
        ("F2 · Phụ đề & kiểu dáng", "Font, cỡ, màu, viền, bóng, nền, karaoke từng từ; khung phụ đề kéo thả trực tiếp trên xem trước."),
        ("F3 · Nhận diện giọng nói (STT)", "Whisper cục bộ (GPU/CPU) hoặc Gemini cloud; tự cắt cue ngắn gọn, video dài vẫn ổn định."),
        ("F4 · Dịch phụ đề đa ngữ", "Dịch SRT sang tiếng Việt và 10 ngôn ngữ khác, giữ timestamp; có bảng dịch tay và checkpoint tiếp tục."),
        ("F5 · Lồng tiếng AI (TTS)", "Đọc lời thoại theo phụ đề, tốc độ 0.8–2.0×, đồng bộ mềm hoặc voice tự nhiên dời phụ đề theo giọng."),
    ]),
    ("Bộ công cụ video (F6 – F10)", T.WARNING, [
        ("F6 · Che mờ thông minh", "Vẽ nhiều vùng che (mờ / pixel / đen), bo góc, mềm mép, theo khoảng thời gian."),
        ("F7 · Nhạc nền & tách vocal", "Nhiều đoạn nhạc trên timeline, tự hạ nhạc khi có giọng (ducking), khử giọng gốc bằng AI."),
        ("F8 · Logo, khung viền & watermark", "Logo/chữ/watermark có chuyển động, vùng an toàn TikTok, khung viền trang trí."),
        ("F9 · Cắt gọt & tỉ lệ khung hình", "Cắt đầu/cuối, nhiều đoạn (ghép liền hoặc xuất từng file); đổi 16:9 ↔ 9:16 ↔ 1:1 với nền mờ."),
        ("F10 · Hiệu ứng hình ảnh", "Lật gương, preset màu, sáng/tương phản/bão hoà, khử rung, hạt phim, vignette, Ken Burns, texture."),
    ]),
    ("Module Dựng Truyện AI (F11)", "#F472B6", [
        ("Tạo kịch bản phân cảnh tự động", "Nhập ý tưởng, AI viết dàn ý → từng chương → cảnh kèm lời kể và prompt ảnh."),
        ("Sinh ảnh minh hoạ đồng nhất phong cách", "Ảnh cho từng cảnh theo preset phong cách (điện ảnh, anime, màu nước…), giữ nhân vật đồng nhất; clip Veo tuỳ chọn."),
        ("Lồng tiếng đa nhân vật & ghép video", "Gán giọng riêng từng nhân vật, ghép ảnh + giọng + SFX thành MP4, tự tạo SRT và metadata YouTube."),
    ]),
]


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, window, tab: str | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.app_state = window.app_state
        st = self.app_state.settings
        self.title(f"Cài đặt — Edit Studio v{APP_VERSION}")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1700, sw - 60)}x{min(1000, sh - 100)}+30+30")
        self.minsize(1100, 700)
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self.tab_var = ctk.StringVar(value="Cài Đặt Hệ Thống & API")
        ctk.CTkSegmentedButton(top, values=["⚙ Cài Đặt Hệ Thống & API", "📖 Phím Tắt & Cẩm Nang Hướng Dẫn"],
                               command=self._switch_tab, font=T.font(12, "bold")).pack(side="left")
        ctk.CTkLabel(top, text=f"v{APP_VERSION}", font=T.font(12, "bold"), fg_color=T.BG_CARD, corner_radius=6,
                     width=90, height=30).pack(side="right")

        self.page_settings = ctk.CTkFrame(self, fg_color="transparent")
        self.page_guide: ctk.CTkScrollableFrame | None = None     # dựng khi mở tab hướng dẫn lần đầu (~150 widget)
        self._build_settings(self.page_settings, st)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 12))
        ctk.CTkLabel(foot, text=" Mẹo: Các cấu hình API, đường dẫn thư mục và chế độ tăng tốc được tự động ghi nhớ.", image=icon(EMOJI_ICONS["💡"], 14, dy=1), compound="left",
                     font=T.font(11), text_color=T.TEXT_MUTED).pack(side="left")
        ctk.CTkButton(foot, text="Đóng", width=110, height=36, command=self.destroy, **T.secondary_button()).pack(side="right")
        button(foot, "Lưu tất cả", icon_name="save", variant="primary", command=self._save).pack(side="right", padx=8)
        self._switch_tab("📖 Phím Tắt & Cẩm Nang Hướng Dẫn" if tab == "shortcuts" else "⚙ Cài Đặt Hệ Thống & API")

    def _switch_tab(self, name: str) -> None:
        guide = "Phím" in name
        if guide and self.page_guide is None:
            self.page_guide = ctk.CTkScrollableFrame(self, fg_color="transparent")
            self._build_guide(self.page_guide)
        self.page_settings.grid_forget()
        if self.page_guide is not None:
            self.page_guide.grid_forget()
        (self.page_guide if guide else self.page_settings).grid(row=1, column=0, sticky="nsew", padx=14)

    # ── Trang cài đặt ────────────────────────────────────────────────────
    def _card(self, parent, title: str, subtitle: str = "") -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=T.BG_CARD, corner_radius=10, border_width=1, border_color=T.BORDER_SOFT)
        card.pack(fill="x", pady=6)
        ctk.CTkLabel(card, text=title, font=T.font(13, "bold"), text_color=T.ACCENT, anchor="w").pack(fill="x", padx=14, pady=(10, 0))
        if subtitle:
            ctk.CTkLabel(card, text=subtitle, font=T.font(11), text_color=T.TEXT_MUTED, anchor="w", wraplength=520,
                         justify="left").pack(fill="x", padx=14, pady=(0, 4))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=12, pady=(2, 12))
        return body

    def _col_title(self, parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=T.font(12, "bold"), text_color=T.TEXT_DIM, anchor="w").pack(fill="x", padx=4, pady=(6, 0))

    def _build_settings(self, page, st) -> None:
        page.grid_columnconfigure((0, 1, 2), weight=1, uniform="col")
        page.grid_rowconfigure(0, weight=1)
        cols = [ctk.CTkScrollableFrame(page, fg_color="transparent") for _ in range(3)]
        for i, c in enumerate(cols):
            c.grid(row=0, column=i, sticky="nsew", padx=4)

        # ── Cột 1 ────────────────────────────────────────────────────────
        c1 = cols[0]
        self._col_title(c1, "⚡ HIỆU NĂNG, GIAO DIỆN & CACHE")
        b = self._card(c1, "Tăng tốc phần cứng & hiệu năng", "Bộ mã hóa GPU đồ hoạ & tối ưu pipeline render video MP4.")
        self.encoder = OptionRow(b, "Bộ mã hóa", GPU_BACKENDS, value=st.gpu_encoder if st.gpu_encoder in GPU_BACKENDS else "auto")
        self.codec = OptionRow(b, "Chuẩn nén", VIDEO_CODECS, value=self.app_state.video.video_codec)
        self.encoder.pack(fill="x", pady=3)
        self.codec.pack(fill="x", pady=3)
        self.enc_status = ctk.CTkLabel(b, text="Đang kiểm tra bộ mã hóa…", font=T.font(12, "bold"), fg_color="#123524",
                                       text_color=T.SUCCESS, corner_radius=8, height=34, wraplength=360, justify="left")
        self.enc_status.pack(fill="x", pady=6)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        button(row, "Kiểm tra máy", icon_name="gpu", size="sm", command=lambda: self.window.open_selfcheck(),
               tip="Liệt kê cái có / cái thiếu trên máy này (GPU, CUDA, model, giọng, key) và cách bổ sung").pack(side="left")
        ctk.CTkButton(row, text="Quét lại", width=90, height=32, command=self._scan_encoders, **T.secondary_button()).pack(side="right")
        self.ffmpeg_fast = SwitchRow(b, "Tăng tốc FFmpeg (thử nghiệm)", value=st.ffmpeg_fast)
        self.ffmpeg_fast.pack(fill="x", pady=3)
        hint(b, "Dùng preset encode nhanh nhất, ưu tiên tốc độ hơn dung lượng.").pack(fill="x")
        self.toast = SwitchRow(b, "Thông báo hệ thống khi hoàn tất", value=st.windows_toast)
        self.toast.pack(fill="x", pady=3)
        hint(b, "Gửi thông báo vào Trung tâm thông báo (Windows Action Center / macOS Notification Center) khi render hoàn tất.").pack(fill="x")
        ffm, ffp = exe_name("ffmpeg"), exe_name("ffprobe")
        self.ffmpeg = EntryRow(b, ffm, value=st.ffmpeg_path, browse="exe", placeholder=f"để trống = bin/{ffm} hoặc PATH")
        self.ffprobe = EntryRow(b, ffp, value=st.ffprobe_path, browse="exe", placeholder=f"để trống = bin/{ffp} hoặc PATH")
        self.workers = SliderRow(b, "Luồng TTS", from_=1, to=8, step=1, value=st.tts_workers)
        self.compute = OptionRow(b, "Whisper compute", ["auto", "int8", "float16", "float32"], value=st.whisper_compute)
        self.vieneu = OptionRow(b, "VieNeu precision", ["int8", "fp32"], value=st.vieneu_precision)
        for r in (self.ffmpeg, self.ffprobe, self.workers, self.compute, self.vieneu):
            r.pack(fill="x", pady=3)

        b = self._card(c1, "Giao diện", "Chọn một bộ màu — đổi ngay lập tức, không cần mở lại.")
        grid = ctk.CTkFrame(b, fg_color="transparent")
        grid.pack(fill="x", pady=(2, 6))
        grid.grid_columnconfigure((0, 1, 2), weight=1)
        self.theme_cards: dict[str, ctk.CTkFrame] = {}
        for i, (key, th) in enumerate(T.THEMES.items()):
            card = ctk.CTkFrame(grid, fg_color=T.BG_INPUT, corner_radius=T.RADIUS, border_width=2,
                                border_color=T.ACCENT if key == T.THEME_NAME else T.BORDER_SOFT, cursor="hand2")
            card.grid(row=i // 3, column=i % 3, sticky="ew", padx=4, pady=4)
            # ô xem trước: nền · panel · thẻ · chấm màu nhấn
            sw = ctk.CTkFrame(card, fg_color=th["bg"], corner_radius=6, height=44)
            sw.pack(fill="x", padx=8, pady=(8, 4))
            sw.pack_propagate(False)
            ctk.CTkFrame(sw, fg_color=th["panel"], corner_radius=4, width=26).pack(side="left", fill="y", padx=(4, 3), pady=4)
            inner = ctk.CTkFrame(sw, fg_color=th["card"], corner_radius=4)
            inner.pack(side="left", fill="both", expand=True, padx=(0, 4), pady=4)
            ctk.CTkFrame(inner, fg_color=th["accent"], corner_radius=6, width=34, height=12).place(relx=0.08, rely=0.3)
            ctk.CTkFrame(inner, fg_color=th["dim"], corner_radius=2, width=60, height=4).place(relx=0.5, rely=0.42)
            ctk.CTkLabel(card, text=th["name"], font=T.font(12, "bold"), anchor="w").pack(fill="x", padx=10)
            ctk.CTkLabel(card, text=th["desc"], font=T.font(10), text_color=T.TEXT_MUTED, anchor="w", justify="left",
                         wraplength=150).pack(fill="x", padx=10, pady=(0, 8))
            for w in (card, sw, inner, *card.winfo_children()):
                w.bind("<Button-1>", lambda _e, k=key: self._pick_theme(k))
            self.theme_cards[key] = card
        self.waveform = SwitchRow(b, "Hiển thị dải sóng âm (Audio Waveform) dưới thanh tua", value=st.show_waveform)
        self.waveform.pack(fill="x", pady=3)

        b = self._card(c1, "Trung tâm cache & dữ liệu tạm")
        self.cache_label = ctk.CTkLabel(b, text="", font=T.font(11), text_color=T.TEXT_DIM, anchor="w")
        self.cache_label.pack(fill="x")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(row, text="Làm mới", width=80, height=32, command=self._cache_sizes, **T.secondary_button()).pack(side="left")
        ctk.CTkButton(row, text="Dọn cache tạm", width=110, height=32, command=self._clean_cache, **T.secondary_button()).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Xóa job & Project", width=130, height=32, command=self._clean_jobs, **T.danger_button()).pack(side="right")
        self._cache_sizes()

        # ── Cột 2 ────────────────────────────────────────────────────────
        c2 = cols[1]
        self._col_title(c2, "🌐 DỊCH THUẬT AI & TỪ ĐIỂN")
        b = self._card(c2, "API Dịch thuật & từ điển AI", "Cấu hình nhà cung cấp, model và API key dịch phụ đề (tự xoay vòng key & model).")
        self.tr_provider = OptionRow(b, "Nhà cung cấp", TRANSLATE_PROVIDERS, value=st.translate_provider, on_change=self._provider_changed)
        self.tr_provider.pack(fill="x", pady=3)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text="Model", font=T.font(12), text_color=T.TEXT_DIM, width=120, anchor="w").pack(side="left")
        self.tr_model = Select(row, _model_choices(st.translate_provider) or [""], height=34, placeholder="")
        self.tr_model.set(st.translate_model or DEFAULT_MODELS.get(st.translate_provider, ""))
        self.tr_model.pack(side="left", fill="x", expand=True)
        self.models_btn = button(row, "", icon_name="refresh", size="sm", width=36, command=self._refresh_models,
                                 tip="Lấy danh sách model mới nhất của nhà cung cấp đang chọn (Gemini / Groq) "
                                     "bằng API key bên dưới")
        self.models_btn.pack(side="left", padx=(6, 0))
        self.models_status = hint(b, "")
        self.models_status.pack(fill="x")
        self.key_boxes: dict[str, ctk.CTkTextbox] = {}
        for prov, value in (("Gemini", st.gemini_api_keys or st.gemini_api_key), ("Groq", st.groq_api_key),
                            ("DeepSeek", st.deepseek_api_key), ("OpenAI", st.openai_api_key)):
            r = ctk.CTkFrame(b, fg_color="transparent")
            r.pack(fill="x", pady=(6, 0))
            ctk.CTkLabel(r, text=f"API keys {prov}", font=T.font(12), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
            ctk.CTkButton(r, text="Lấy key ↗", width=80, height=32, command=lambda p=prov: webbrowser.open(KEY_LINKS[p]),
                          **T.secondary_button()).pack(side="right")
            box = ctk.CTkTextbox(b, height=52, font=T.mono(11))
            box.insert("1.0", value or "")
            box.pack(fill="x")
            self.key_boxes[prov] = box
        hint(b, "Mỗi dòng một key. Tự xoay vòng: nhà cung cấp chính → các nhà cung cấp khác có key. "
                "Gemini/Groq có gói miễn phí · DeepSeek/OpenAI cần key trả phí · Edge-TTS không cần key.").pack(fill="x", pady=(4, 0))
        r = ctk.CTkFrame(b, fg_color="transparent")
        r.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(r, text="Từ điển tên riêng (Thuật ngữ):", font=T.font(12, "bold"), anchor="w").pack(side="left")
        ctk.CTkButton(r, text="Nhập TXT…", width=90, height=32, command=self._import_glossary, **T.secondary_button()).pack(side="right")
        self.glossary = ctk.CTkTextbox(b, height=160, font=T.mono(11))
        self.glossary.insert("1.0", st.glossary)
        self.glossary.pack(fill="x", pady=(4, 0))
        hint(b, "Mỗi dòng: từ nguồn=nghĩa tiếng Việt (ví dụ 周彦淑=Chu Ngạn Thục). Áp dụng cho prompt và bộ nhớ dịch.").pack(fill="x", pady=(4, 0))

        # ── Cột 3 ────────────────────────────────────────────────────────
        c3 = cols[2]
        self._col_title(c3, "🎙 GIỌNG CLOUD & CẤU HÌNH")
        b = self._card(c3, "Giọng đọc cloud (ElevenLabs · Google · FPT.AI)",
                       "Key lưu tại settings.json trên máy này. Gemini TTS dùng key Gemini ở cột giữa.")
        r = ctk.CTkFrame(b, fg_color="transparent")
        r.pack(fill="x")
        ctk.CTkLabel(r, text="ElevenLabs API keys (mỗi dòng một key)", font=T.font(12), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        ctk.CTkButton(r, text="Lấy key ↗", width=80, height=32, command=lambda: webbrowser.open(KEY_LINKS["ElevenLabs"]), **T.secondary_button()).pack(side="right")
        self.eleven_keys = ctk.CTkTextbox(b, height=52, font=T.mono(11))
        self.eleven_keys.insert("1.0", st.elevenlabs_api_keys or st.elevenlabs_api_key)
        self.eleven_keys.pack(fill="x", pady=(0, 6))
        r = ctk.CTkFrame(b, fg_color="transparent")
        r.pack(fill="x")
        ctk.CTkLabel(r, text="Google API keys (mỗi dòng một key · bật Text-to-Speech API)", font=T.font(12), text_color=T.TEXT_DIM,
                     anchor="w").pack(side="left")
        ctk.CTkButton(r, text="Lấy key ↗", width=80, height=32, command=lambda: webbrowser.open(KEY_LINKS["Google"]), **T.secondary_button()).pack(side="right")
        self.google_key = ctk.CTkTextbox(b, height=52, font=T.mono(11))
        self.google_key.insert("1.0", st.google_api_key)
        self.google_key.pack(fill="x", pady=(0, 6))
        self.google_json = EntryRow(b, "Google JSON", value=st.google_sa_json, browse="file", placeholder="Service Account JSON (tuỳ chọn)")
        self.google_json.pack(fill="x", pady=3)
        r = ctk.CTkFrame(b, fg_color="transparent")
        r.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(r, text="FPT.AI API keys (giọng Việt 3 miền)", font=T.font(12), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        ctk.CTkButton(r, text="Lấy key ↗", width=80, height=32, command=lambda: webbrowser.open(KEY_LINKS["FPT"]), **T.secondary_button()).pack(side="right")
        self.fpt_keys = ctk.CTkTextbox(b, height=52, font=T.mono(11))
        self.fpt_keys.insert("1.0", st.fpt_api_key)
        self.fpt_keys.pack(fill="x", pady=(0, 6))
        hint(b, "Mọi ô key đều nhận nhiều key, mỗi dòng một key: hết hạn mức hoặc key lỗi thì tự chuyển sang key kế tiếp.").pack(fill="x", pady=(2, 0))

        b = self._card(c3, "Chất lượng giọng đọc (TTS)",
                       "Kiểm từng câu sau khi đọc (đọc lặp, nói thêm, cắt cụt, «rẹt» cuối câu…) → tự đọc lại câu hỏng.")
        self.tts_qc = SwitchRow(b, "Kiểm tra & tự đọc lại câu lỗi", value=st.tts_qc)
        self.qc_retries = SliderRow(b, "Đọc lại tối đa", from_=1, to=5, step=1, value=st.tts_qc_retries, suffix=" lần")
        self.qc_asr = OptionRow(b, "Whisper nghe lại", ["auto", "on", "off"], value=st.tts_qc_asr,
                                labels={"auto": "Tự động (máy có GPU)", "on": "Luôn bật", "off": "Tắt"})
        self.tts_fallback = OptionRow(b, "Engine dự phòng", ["Không", "Edge TTS", "VieNeu (local, tiếng Việt)"],
                                      value=st.tts_fallback or "Không")
        self.auto_shorten = SwitchRow(b, "Câu đọc tràn khung → AI tự rút gọn bản dịch", value=st.tts_auto_shorten)
        for r in (self.tts_qc, self.qc_retries, self.qc_asr, self.tts_fallback, self.auto_shorten):
            r.pack(fill="x", pady=3)
        hint(b, "VieNeu đọc lại miễn phí (chạy trên máy) · Gemini đọc lại tối đa 1 lần (tốn lượt) · engine trả phí khác "
                "không đọc lại. Whisper nghe lại bắt thêm chữ đọc méo, dùng model Whisper đã có trên máy. Câu lỗi hẳn "
                "(hết lượt, mất mạng) thì đọc tạm bằng engine dự phòng thay vì để im lặng.").pack(fill="x")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        button(row, "Từ điển phát âm…", icon_name="book", size="sm", command=self._open_pron,
               tip="Mở pronunciation.json: «chữ viết»: «cách đọc» (vd. \"ANC\": \"ây en xi\") — áp cho mọi giọng, "
                   "lưu file rồi tạo lại giọng").pack(side="left")

        b = self._card(c3, "Âm thanh đầu ra", "Khi bật «Chuẩn hoá âm lượng» (mục Video): đo độ to cả bài rồi chỉnh "
                                              "đúng một lần, đỉnh luôn giới hạn ≤ −1 dBTP.")
        self.loudness = SliderRow(b, "Độ to file xuất", from_=-23, to=-9, step=1, value=st.loudness_target,
                                  suffix=" LUFS")
        self.loudness.pack(fill="x", pady=3)
        hint(b, "TikTok · Facebook · YouTube: −14 LUFS. Phát thanh / truyền hình (EBU R128): −23.").pack(fill="x")

        b = self._card(c3, "Thư mục xuất MP4", "Mẫu dựng video (trước đây là Preset) nằm ở ô «Mẫu» trên cùng cửa sổ chính.")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        self.out_var = ctk.StringVar(value=self.app_state.output_dir)
        ctk.CTkEntry(row, textvariable=self.out_var, height=32, font=T.font(12)).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Chọn", width=60, height=32, command=self._browse_out, **T.secondary_button()).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Mở", width=50, height=32, command=self._open_out, **T.primary_button()).pack(side="left")
        self.after(200, self._scan_encoders)

    # ── Trang phím tắt & cẩm nang ────────────────────────────────────────
    def _build_guide(self, page) -> None:
        page.grid_columnconfigure((0, 1), weight=1)
        head = ctk.CTkFrame(page, fg_color=T.BG_CARD, corner_radius=10)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(head, text="ⓘ Trợ giúp, phím tắt & cẩm nang render", font=T.font(15, "bold"), text_color=T.ACCENT, anchor="w").pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text="Tra cứu nhanh các tổ hợp phím tắt bàn phím và cẩm nang lựa chọn chế độ render tối ưu.",
                     font=T.font(11), text_color=T.TEXT_DIM, anchor="w").pack(fill="x", padx=14, pady=(0, 6))
        button(head, "Mở Hướng dẫn sử dụng đầy đủ (từng bước, theo module)", icon_name="book", variant="primary", command=lambda: self.window.open_guide_window("start")).pack(anchor="w", padx=14, pady=(0, 12))
        for i, (title, rows) in enumerate(SHORTCUTS):
            card = ctk.CTkFrame(page, fg_color=T.BG_CARD, corner_radius=10)
            card.grid(row=1 + i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            ctk.CTkLabel(card, text="⌨ " + title, font=T.font(13, "bold"), text_color=T.ACCENT, anchor="w").pack(fill="x", padx=12, pady=(10, 4))
            for key, desc in rows:
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=12, pady=2)
                ctk.CTkLabel(row, text=key, font=T.mono(11), fg_color=T.BG_ELEVATED, corner_radius=6, width=170, text_color=T.INFO).pack(side="left")
                ctk.CTkLabel(row, text=desc, font=T.font(11), anchor="w", text_color=T.TEXT_DIM).pack(side="left", padx=10)
            ctk.CTkLabel(card, text="").pack(pady=2)
        base = 1 + (len(SHORTCUTS) + 1) // 2
        for i, (title, color, rows) in enumerate(GUIDE):
            card = ctk.CTkFrame(page, fg_color=T.BG_CARD, corner_radius=10, border_width=1, border_color=color)
            card.grid(row=base + i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            ctk.CTkLabel(card, text=title, font=T.font(13, "bold"), text_color=color, anchor="w").pack(fill="x", padx=12, pady=(10, 4))
            for name, desc in rows:
                ctk.CTkLabel(card, text="• " + name, font=T.font(12, "bold"), anchor="w", text_color=T.TEXT).pack(fill="x", padx=12, pady=(4, 0))
                ctk.CTkLabel(card, text=desc, font=T.font(11), anchor="w", justify="left", wraplength=640, text_color=T.TEXT_DIM).pack(fill="x", padx=26)
            ctk.CTkLabel(card, text="").pack(pady=2)

    # ── Hành động ────────────────────────────────────────────────────────
    def _scan_encoders(self) -> None:
        ff = FFmpeg(self.ffmpeg.get(), self.ffprobe.get())
        main_ff = getattr(self.window, "ffmpeg", None)
        if main_ff is not None and (ff.ffmpeg, ff.ffprobe) == (main_ff.ffmpeg, main_ff.ffprobe):
            ff = main_ff             # cùng ffmpeg với cửa sổ chính → dùng lại kết quả dò encoder đã cache
        if not ff.available:
            self.enc_status.configure(text="✖ Không tìm thấy ffmpeg/ffprobe", fg_color="#3B1D1D", text_color=T.DANGER)
            return

        def work():
            gpu_name()               # dò trong nền → done() lấy từ cache
            ok = [b for b in ("nvenc", "qsv", "amf") if ff.encoder_works(f"h264_{b}")]
            return ok

        def done(result, error):
            if error or not result:
                self.enc_status.configure(text=f"CPU (libx264) · {gpu_name()} — GPU không dùng được (cập nhật driver?)",
                                          fg_color="#3B2A12", text_color=T.WARNING)
            else:
                names = {"nvenc": "NVIDIA NVENC", "qsv": "Intel QSV", "amf": "AMD AMF"}
                note = ""
                if "nvenc" not in result and "NVIDIA" in gpu_name().upper():
                    note = " · NVENC chưa dùng được (cần driver NVIDIA ≥ 610)"
                self.enc_status.configure(text=f"✔ {names[result[0]]} sẵn sàng · GPU: {gpu_name()}{note}",
                                          fg_color="#123524", text_color=T.SUCCESS)

        self.window.run_bg(work, done)

    def _pick_theme(self, key: str) -> None:
        """Đổi theme ngay: lưu cài đặt, dựng lại giao diện chính, mở lại cửa sổ Cài đặt."""
        if key == T.THEME_NAME:
            return
        self._save(close=False, silent=True)
        self.window.apply_theme_live(key)

    def _provider_changed(self, prov: str) -> None:
        self.tr_model.configure(values=_model_choices(prov))
        self.tr_model.set(DEFAULT_MODELS.get(prov, ""))

    def _refresh_models(self) -> None:
        """Hỏi Google các model Gemini key này dùng được → cập nhật danh sách (lưu cache, Dựng Truyện cũng dùng).
        Đang chọn Groq thì hỏi Groq."""
        from app.services.gemini_models import fetch_models

        if self.tr_provider.get() == "Groq":
            self._refresh_groq_models()
            return
        keys = self.app_state.settings.split_keys(self.key_boxes["Gemini"].get("1.0", "end")) + \
            self.app_state.settings.split_keys(self.google_key.get("1.0", "end"))
        if not keys:
            self.models_status.configure(text="Nhập Gemini API key (hoặc Google API key) trước rồi bấm lại.")
            return
        self.models_status.configure(text="Đang hỏi Google danh sách model…")
        self.models_btn.configure(state="disabled")

        def done(result, error):
            self.models_btn.configure(state="normal")
            if error:
                self.models_status.configure(text=f"Không lấy được danh sách model: {error}")
                return
            if self.tr_provider.get() == "Gemini":
                cur = self.tr_model.get()
                self.tr_model.configure(values=_model_choices("Gemini"))
                self.tr_model.set(migrate_model(cur) or DEFAULT_MODELS["Gemini"])
            self.models_status.configure(text=f"✓ Đã cập nhật: {len(result['text'])} model văn bản · "
                                              f"{len(result['image'])} model ảnh · {len(result['video'])} model video.")

        self.window.run_bg(lambda: fetch_models(keys[0]), done)

    def _refresh_groq_models(self) -> None:
        keys = self.app_state.settings.split_keys(self.key_boxes["Groq"].get("1.0", "end"))
        if not keys:
            self.models_status.configure(text="Nhập Groq API key trước rồi bấm lại.")
            return
        self.models_status.configure(text="Đang hỏi Groq danh sách model…")
        self.models_btn.configure(state="disabled")

        def done(result, error):
            self.models_btn.configure(state="normal")
            if error:
                self.models_status.configure(text=f"Không lấy được danh sách model: {error}")
                return
            if self.tr_provider.get() == "Groq":
                cur = groq_models.migrate_model(self.tr_model.get())
                choices = groq_models.text_models()
                self.tr_model.configure(values=choices)
                self.tr_model.set(cur if cur in choices else choices[0])
            self.models_status.configure(text=f"✓ Đã cập nhật: {len(result)} model Groq dịch được.")

        self.window.run_bg(lambda: groq_models.fetch_models(keys[0]), done)

    def _import_glossary(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("Text", "*.txt *.csv"), ("Tất cả", "*.*")])
        if p:
            try:
                text = Path(p).read_text(encoding="utf-8", errors="replace")
                self.glossary.insert("end", ("\n" if self.glossary.get("1.0", "end").strip() else "") + text.strip())
            except Exception as exc:
                messagebox.showerror("Nhập TXT", str(exc))

    def _cache_sizes(self) -> None:
        def size(p: Path) -> float:
            try:
                return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1_048_576
            except Exception:
                return 0.0
        tts, pv, jobs = size(TTS_CACHE_DIR), size(PREVIEW_CACHE_DIR), size(JOBS_DIR)
        self.cache_label.configure(text=f"Cache TTS {tts:.1f} MB · Xem trước {pv:.1f} MB · Job tạm {jobs:.1f} MB · "
                                        f"Tổng: {tts + pv + jobs:.1f} MB")

    def _clean_cache(self) -> None:
        for d in (PREVIEW_CACHE_DIR, CACHE_DIR / "demucs", CACHE_DIR / "novocal"):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
        self._cache_sizes()
        self.window.log("Đã dọn cache xem trước.")

    def _clean_jobs(self) -> None:
        if not messagebox.askyesno("Xóa job & Project", "Xoá thư mục job tạm và cache TTS? (Video/SRT/voice đã xuất vẫn giữ.)"):
            return
        for d in (JOBS_DIR, TTS_CACHE_DIR):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
        self._cache_sizes()
        self.window.log("Đã xoá job tạm và cache TTS.")

    def _open_pron(self) -> None:
        """Mở từ điển phát âm TTS (chưa có thì tạo file mẫu — khoá bắt đầu bằng «_» là ghi chú, không áp)."""
        if not PRON_FILE.exists():
            PRON_FILE.write_text(json.dumps({
                "_ghi_chú": "Mỗi dòng: \"chữ viết\": \"cách đọc\". Khớp nguyên từ, không phân biệt hoa/thường. "
                            "Lưu file rồi bấm tạo lại giọng.",
                "_ví_dụ": "\"ANC\": \"ây en xi\", \"Xiaomi\": \"xao mi\", \"Shopee\": \"sóp pi\""},
                ensure_ascii=False, indent=2), encoding="utf-8")
        open_path(str(PRON_FILE))

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(title="Thư mục xuất")
        if d:
            self.out_var.set(d)

    def _open_out(self) -> None:
        d = self.out_var.get().strip()
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)
            open_path(d)

    def _save(self, close: bool = True, silent: bool = False) -> None:
        st = self.app_state.settings
        st.gpu_encoder = self.encoder.get()
        self.app_state.video.gpu_backend = st.gpu_encoder
        self.app_state.video.use_gpu = st.gpu_encoder != "cpu"
        self.app_state.video.video_codec = self.codec.get()
        st.ffmpeg_fast = self.ffmpeg_fast.get()
        if st.ffmpeg_fast:
            self.app_state.video.encode_preset = "Nhanh"
        st.windows_toast = self.toast.get()
        st.ffmpeg_path, st.ffprobe_path = self.ffmpeg.get().strip(), self.ffprobe.get().strip()
        st.tts_workers = int(self.workers.get())
        st.whisper_compute = self.compute.get()
        st.vieneu_precision = self.vieneu.get()
        st.tts_qc = self.tts_qc.get()
        st.tts_qc_retries = int(self.qc_retries.get())
        st.tts_qc_asr = self.qc_asr.get()
        fb = self.tts_fallback.get()
        st.tts_fallback = "" if fb == "Không" else fb
        st.tts_auto_shorten = self.auto_shorten.get()
        st.loudness_target = float(self.loudness.get())
        st.theme = T.THEME_NAME
        st.show_waveform = self.waveform.get()
        st.translate_provider = self.tr_provider.get()
        st.translate_model = self.tr_model.get().strip() or DEFAULT_MODELS.get(st.translate_provider, "")
        st.gemini_api_keys = self.key_boxes["Gemini"].get("1.0", "end").strip()
        st.gemini_api_key = st.gemini_api_keys.splitlines()[0].strip() if st.gemini_api_keys else ""
        st.groq_api_key = self.key_boxes["Groq"].get("1.0", "end").strip()
        st.deepseek_api_key = self.key_boxes["DeepSeek"].get("1.0", "end").strip()
        st.openai_api_key = self.key_boxes["OpenAI"].get("1.0", "end").strip()
        st.glossary = self.glossary.get("1.0", "end").strip()
        st.elevenlabs_api_keys = self.eleven_keys.get("1.0", "end").strip()
        st.elevenlabs_api_key = st.elevenlabs_api_keys.splitlines()[0].strip() if st.elevenlabs_api_keys else ""
        st.google_api_key = self.google_key.get("1.0", "end").strip()
        st.fpt_api_key = self.fpt_keys.get("1.0", "end").strip()
        st.google_sa_json = self.google_json.get().strip()
        out = self.out_var.get().strip()
        if out:
            self.app_state.output_dir = out
        self.app_state.save_settings()
        self.app_state.config_changed("video")
        if silent:
            return
        self.window.apply_settings()
        self.window.refresh_modules()
        if close:
            self.destroy()
