"""Cửa sổ Cài đặt (toàn màn hình): 🖥 AI trên máy · ☁ AI dùng API key · ⚙ Chung — và trang Phím tắt & Hướng dẫn."""

from __future__ import annotations

import json
import shutil
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.ui.icons import EMOJI_ICONS, icon
from app.config.paths import APP_VERSION, CACHE_DIR, JOBS_DIR, PRON_FILE, TTS_CACHE_DIR, PREVIEW_CACHE_DIR
from app.core.platform import exe_name
from app.services.ffmpeg import FFmpeg
from app.services.sysinfo import gpu_name
from app.services.gemini_models import migrate_model, text_models
from app.services.translate_router import DEFAULT_MODEL
from app.ui.widgets.ui import Select, button
from app.ui.widgets.common import EntryRow, OptionRow, SwitchRow, SliderRow, StatusLabel, hint, info_icon
from app.ui.widgets.dialogs import SHORTCUTS
from app.core.platform import open_path

GEMINI_KEY_URL = "https://aistudio.google.com/apikey"

GUIDE = [
    ("Cài đặt", T.INFO, [
        ("🖥 AI trên máy", "Nghe lời thoại (Qwen3-ASR), giọng đọc Việt (VieNeu), kiểm lỗi giọng đọc (Whisper), căn chữ "
                           "nổi: cài một lần, miễn phí, không giới hạn lượt. Có GPU NVIDIA thì nhanh hơn nhiều."),
        ("☁ AI dùng API key", "Gemini dịch phụ đề, sửa lời thoại theo yêu cầu, rút gọn câu đọc không kịp và viết kịch "
                              "bản Dựng truyện. Hết lượt / quá tải thì máy dừng và báo để bạn chọn model khác."),
        ("Thư mục xuất & mẫu", "Tuỳ chỉnh nơi lưu MP4/MP3. Lưu thiết lập thành mẫu ở ô «Mẫu» trên cùng (xuất / nhập file mẫu để chia sẻ)."),
    ]),
    ("Bộ công cụ video (F1 – F4)", T.SUCCESS, [
        ("F1 · Video & hàng đợi", "Kéo thả video vào. Khung hình, tốc độ, âm lượng, khử giọng gốc."),
        ("F2 · Nhận diện lời (STT)", "AI nghe giọng nói ngay trên máy, hoặc đọc chữ in sẵn trên video."),
        ("F3 · Lời thoại & phụ đề", "Sửa từng câu, «Nhờ AI sửa», dịch. Kiểu phụ đề mặc định giống TikTok; chỉnh chi tiết khi cần."),
        ("F4 · Lồng tiếng (TTS)", "Giọng Việt VieNeu, khớp mềm với hình, mỗi người nói một giọng."),
    ]),
    ("Bộ công cụ video (F5 – F9)", T.WARNING, [
        ("F5 · Che mờ", "Che chữ cũ, logo… Tự dò phụ đề in sẵn của từng video."),
        ("F6 · Nhạc nền", "Nhiều đoạn nhạc, tự hạ nhạc khi có giọng."),
        ("F7 · Logo & chữ", "Logo, chữ, watermark có chuyển động, khung viền."),
        ("F8 · Cắt", "Cắt đầu / cuối, nhiều đoạn (ghép liền hoặc xuất riêng)."),
        ("F9 · Hiệu ứng", "Màu, khử rung, hạt phim, Ken Burns, texture."),
    ]),
    ("Dựng Truyện AI (F10)", "#F472B6", [
        ("Tạo kịch bản phân cảnh tự động", "Nhập ý tưởng, AI viết dàn ý → từng chương → cảnh kèm lời kể và prompt ảnh."),
        ("Sinh ảnh minh hoạ đồng nhất phong cách", "Ảnh cho từng cảnh theo preset phong cách (điện ảnh, anime, màu nước…), giữ nhân vật đồng nhất; clip Veo tuỳ chọn."),
        ("Lồng tiếng đa nhân vật & ghép video", "Gán giọng riêng từng nhân vật, ghép ảnh + giọng + SFX thành MP4, tự tạo SRT và metadata YouTube."),
    ]),
]


class SettingsWindow(ctk.CTkToplevel):
    TAB_SETTINGS, TAB_GUIDE = "⚙ Cài đặt", "📖 Phím tắt & Hướng dẫn"

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
        self._nvidia = False

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self.tabs = ctk.CTkSegmentedButton(top, values=[self.TAB_SETTINGS, self.TAB_GUIDE], command=self._switch_tab,
                                           font=T.font(12, "bold"))
        self.tabs.pack(side="left")
        ctk.CTkLabel(top, text=f"v{APP_VERSION}", font=T.font(12, "bold"), fg_color=T.BG_CARD, corner_radius=6,
                     width=90, height=30).pack(side="right")

        self.page_settings = ctk.CTkFrame(self, fg_color="transparent")
        self.page_guide: ctk.CTkScrollableFrame | None = None     # dựng khi mở tab hướng dẫn lần đầu (~150 widget)
        self._build_settings(self.page_settings, st)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 12))
        ctk.CTkLabel(foot, text=" Bấm «Lưu tất cả» để áp dụng. Key chỉ lưu trên máy này (settings.json).",
                     image=icon(EMOJI_ICONS["💡"], 14, dy=1), compound="left", font=T.font(11),
                     text_color=T.TEXT_MUTED).pack(side="left")
        ctk.CTkButton(foot, text="Đóng", width=110, height=36, command=self.destroy, **T.secondary_button()).pack(side="right")
        button(foot, "Lưu tất cả", icon_name="save", variant="primary", command=self._save).pack(side="right", padx=8)
        self._switch_tab(self.TAB_GUIDE if tab == "shortcuts" else self.TAB_SETTINGS)
        if tab == "model":
            self.after(150, self.focus_model)

    def focus_model(self) -> None:
        """Từ nút «Chọn model…» (model đang chọn hết lượt / quá tải): chỉ đúng chỗ đổi model."""
        self._switch_tab(self.TAB_SETTINGS)
        self.models_status.configure(text="⬆ Model đang chọn hết lượt / quá tải — chọn model khác ở ô «Model» rồi bấm "
                                          "«Lưu tất cả».", text_color=T.WARNING)
        try:
            self.tr_model.focus_set()
        except Exception:                          # noqa: BLE001 — chỉ là đưa con trỏ tới, không quan trọng
            pass

    def _switch_tab(self, name: str) -> None:
        guide = name == self.TAB_GUIDE
        self.tabs.set(name)
        if guide and self.page_guide is None:
            self.page_guide = ctk.CTkScrollableFrame(self, fg_color="transparent")
            self._build_guide(self.page_guide)
        self.page_settings.grid_forget()
        if self.page_guide is not None:
            self.page_guide.grid_forget()
        (self.page_guide if guide else self.page_settings).grid(row=1, column=0, sticky="nsew", padx=14)

    # ── Khung ────────────────────────────────────────────────────────────
    def _card(self, parent, title: str, *, sub: str = "", info: str = "") -> ctk.CTkFrame:
        """Thẻ có tiêu đề (``sub``: tên thành phần, chữ nhỏ; ``info``: giải thích dài trong ⓘ)."""
        card = ctk.CTkFrame(parent, fg_color=T.BG_CARD, corner_radius=10, border_width=1, border_color=T.BORDER_SOFT)
        card.pack(fill="x", pady=6)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text=title, font=T.font(13, "bold"), text_color=T.ACCENT, anchor="w").pack(side="left")
        if sub:
            ctk.CTkLabel(head, text=sub, font=T.font(11), text_color=T.TEXT_MUTED, anchor="w").pack(side="left", padx=(6, 0))
        if info:
            info_icon(head, info).pack(side="left", padx=(4, 0))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=12, pady=(4, 12))
        return body

    def _col_title(self, parent, text: str, sub: str) -> None:
        ctk.CTkLabel(parent, text=text, font=T.font(14, "bold"), text_color=T.TEXT, anchor="w").pack(fill="x", padx=4, pady=(6, 0))
        ctk.CTkLabel(parent, text=sub, font=T.font(11), text_color=T.TEXT_MUTED, anchor="w", justify="left",
                     wraplength=440).pack(fill="x", padx=4, pady=(0, 2))

    @staticmethod
    def _status_row(body) -> tuple[StatusLabel, ctk.CTkFrame]:
        """Dòng trạng thái + chỗ đặt nút (Cài / Tải) bên phải."""
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(2, 0))
        status = StatusLabel(row, "Đang kiểm tra…")
        status.pack(side="left", fill="x", expand=True)
        return status, row

    def _build_settings(self, page, st) -> None:
        page.grid_columnconfigure((0, 1, 2), weight=1, uniform="col")
        page.grid_rowconfigure(0, weight=1)
        cols = [ctk.CTkScrollableFrame(page, fg_color="transparent") for _ in range(3)]
        for i, c in enumerate(cols):
            c.grid(row=0, column=i, sticky="nsew", padx=4)
        self._build_local(cols[0], st)
        self._build_cloud(cols[1], st)
        self._build_general(cols[2], st)
        self.after(200, self._scan_machine)

    # ── Cột 1: AI trên máy ───────────────────────────────────────────────
    def _build_local(self, c, st) -> None:
        self._col_title(c, "🖥  AI trên máy", "Miễn phí, không giới hạn lượt, không cần mạng sau khi cài. Có GPU NVIDIA "
                                             "thì nhanh hơn nhiều.")
        b = self._card(c, "Máy này", info="Card đồ hoạ quyết định tốc độ của mọi phần AI trên máy. GPU NVIDIA cần thêm "
                                          "CUDA runtime (một lần) để Whisper chạy trên GPU.")
        self.gpu_status = StatusLabel(b, "Đang kiểm tra card đồ hoạ…")
        self.gpu_status.pack(fill="x")
        self.cuda_status, row = self._status_row(b)
        self.cuda_btn = button(row, "Cài CUDA", icon_name="download", size="sm", command=self._install_cuda)
        row2 = ctk.CTkFrame(b, fg_color="transparent")
        row2.pack(fill="x", pady=(8, 0))
        button(row2, "Kiểm tra máy", icon_name="gpu", size="sm", command=lambda: self.window.open_selfcheck(),
               tip="Liệt kê cái có / cái thiếu trên máy này và cách bổ sung").pack(side="left")

        b = self._card(c, "Nghe lời thoại", sub="Qwen3-ASR",
                       info="AI nghe giọng nói trong video rồi chép thành phụ đề (mốc thời gian từng chữ, tách câu theo "
                            "nghĩa). Dùng khi tab STT chọn «Giọng nói trong video». Cài một lần ~11 GB.")
        self.qwen_status, row = self._status_row(b)
        self.qwen_btn = button(row, "Cài", icon_name="download", size="sm", command=self._install_qwen)

        b = self._card(c, "Giọng đọc tiếng Việt", sub="VieNeu",
                       info="25 giọng Việt (Bắc / Trung / Nam) chạy trên máy, có clone giọng. Câu đọc lỗi tự đọc lại; "
                            "đọc lại vẫn hỏng thì đọc tạm bằng Edge (cần mạng) thay vì để im lặng.")
        self.vieneu_status, row = self._status_row(b)
        self.vieneu_btn = button(row, "Cài", icon_name="download", size="sm", command=self._install_vieneu)
        self.vieneu = OptionRow(b, "Chất lượng", ["int8", "fp32"], value=st.vieneu_precision,
                                labels={"int8": "Nhanh", "fp32": "Cao (chậm hơn)"},
                                info="Nhanh: đủ tốt cho hầu hết video. Cao: giọng mượt hơn một chút nhưng đọc chậm hơn.")
        self.vieneu.pack(fill="x", pady=(6, 0))

        b = self._card(c, "Kiểm lỗi giọng đọc", sub="Whisper",
                       info="Máy nghe lại từng câu vừa lồng tiếng để bắt câu đọc sai, đọc thiếu rồi đọc lại; cũng dùng "
                            "để đọc liền vài câu ngắn rồi tách lại cho tự nhiên. Luôn bật khi đã có model.")
        self.whisper_status, row = self._status_row(b)
        self.whisper_btn = button(row, "Tải", icon_name="download", size="sm", command=self._download_whisper)

        b = self._card(c, "Căn chữ nổi", sub="CTC tiếng Việt",
                       info="Phụ đề «Nổi chữ đang đọc»: căn từng chữ theo đúng tiếng của giọng lồng, như CapCut. Chưa "
                            "cài thì chữ nổi chia theo khoảng lặng trong câu — gần đúng. Chạy bằng CPU.")
        self.align_status, row = self._status_row(b)
        self.align_btn = button(row, "Tải", icon_name="download", size="sm", command=self._install_aligner)

        b = self._card(c, "Khử giọng gốc & nhận ra người nói",
                       info="Khử giọng nói gốc (giữ nhạc nền, tiếng động) và nhận ra ai nói câu nào để lồng mỗi người một "
                            "giọng. Model nhỏ, tự tải ở lần dùng đầu.")
        self.small_status = StatusLabel(b, "")
        self.small_status.pack(fill="x")

    # ── Cột 2: AI dùng API key ───────────────────────────────────────────
    def _build_cloud(self, c, st) -> None:
        self._col_title(c, "☁  AI dùng API key", "Cần mạng. Gói miễn phí có giới hạn lượt mỗi ngày.")
        b = self._card(c, "Gemini", info="Dùng để: dịch phụ đề (2 bước, sát nghĩa rồi tự nhiên), tách câu nhận diện quá "
                                         "dài, «Nhờ AI sửa», rút gọn câu đọc không kịp, viết kịch bản Dựng truyện. "
                                         "Gói miễn phí: Google có thể dùng chữ bạn gửi để cải thiện sản phẩm — đừng gửi "
                                         "nội dung nhạy cảm.")
        r = ctk.CTkFrame(b, fg_color="transparent")
        r.pack(fill="x")
        ctk.CTkLabel(r, text="API key", font=T.font(12), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        info_icon(r, "Mỗi dòng một key. Key lỗi thì tự thử key kế tiếp. Lấy miễn phí ở Google AI Studio. Key được che "
                     "khi không sửa — bấm «Hiện / sửa» để xem và sửa.").pack(side="left", padx=(4, 0))
        ctk.CTkButton(r, text="Lấy key ↗", width=80, height=32, command=lambda: webbrowser.open(GEMINI_KEY_URL),
                      **T.secondary_button()).pack(side="right")
        self.keys_toggle = ctk.CTkButton(r, text="", width=90, height=32, command=self._toggle_keys,
                                         **T.secondary_button())
        self.keys_toggle.pack(side="right", padx=(0, 6))
        self.gemini_keys = ctk.CTkTextbox(b, height=86, font=T.mono(11))
        self.gemini_keys.pack(fill="x", pady=(2, 6))
        # key che sẵn (chia sẻ màn hình / chụp ảnh không lộ key); chưa có key thì mở sẵn để dán
        self._keys_real = st.gemini_api_keys
        self._keys_shown = not st.gemini_keys()
        self._paint_keys()
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text="Model", font=T.font(12), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        info_icon(row, "Model Gemini dùng cho mọi việc ở trên. Hết lượt hôm nay hoặc quá tải lâu: máy dừng và báo để bạn "
                       "chọn model khác — không tự đổi model giữa chừng (đổi giữa chừng làm bản dịch kém hay, lệch giọng "
                       "văn). Bấm ↻ để lấy danh sách model mới nhất bằng key của bạn.").pack(side="left", padx=(4, 8))
        self.tr_model = Select(row, text_models() or [DEFAULT_MODEL], height=34, placeholder="")
        self.tr_model.set(st.translate_model or DEFAULT_MODEL)
        self.tr_model.pack(side="left", fill="x", expand=True)
        self.models_btn = button(row, "", icon_name="refresh", size="sm", width=36, command=self._refresh_models,
                                 tip="Lấy danh sách model mới nhất bằng API key ở trên")
        self.models_btn.pack(side="left", padx=(6, 0))
        self.models_status = hint(b, "")
        self.models_status.pack(fill="x")

        b = self._card(c, "Từ điển tên riêng", info="Mỗi dòng: chữ gốc=nghĩa, vd 周彦淑=Chu Ngạn Thục. AI luôn dịch đúng "
                                                    "như vậy (cả khi dịch lại / Nhờ AI sửa).")
        self.glossary = ctk.CTkTextbox(b, height=200, font=T.mono(11))
        self.glossary.insert("1.0", st.glossary)
        self.glossary.pack(fill="x")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(row, text="Nhập TXT…", width=90, height=32, command=self._import_glossary,
                      **T.secondary_button()).pack(side="left")

    @staticmethod
    def _mask(key: str) -> str:
        return key if len(key) <= 12 else f"{key[:4]}{'•' * 8}{key[-4:]}"

    def _paint_keys(self) -> None:
        box = self.gemini_keys
        box.configure(state="normal")
        box.delete("1.0", "end")
        if self._keys_shown:
            box.insert("1.0", self._keys_real)
        else:
            box.insert("1.0", "\n".join(self._mask(k) for k in self.app_state.settings.split_keys(self._keys_real)))
            box.configure(state="disabled")
        self.keys_toggle.configure(text="Ẩn" if self._keys_shown else "Hiện / sửa")

    def _toggle_keys(self) -> None:
        if self._keys_shown:
            self._keys_real = self.gemini_keys.get("1.0", "end").strip()
        self._keys_shown = not self._keys_shown
        self._paint_keys()

    def _key_text(self) -> str:
        """Key thật (ô đang che thì lấy bản đã lưu, không lấy chữ đã che)."""
        return self.gemini_keys.get("1.0", "end").strip() if self._keys_shown else self._keys_real

    # ── Cột 3: Chung ─────────────────────────────────────────────────────
    def _build_general(self, c, st) -> None:
        self._col_title(c, "⚙  Chung", "Thư mục xuất, âm thanh, giao diện, bộ nhớ tạm.")
        b = self._card(c, "Xuất video", info="Bộ mã hoá GPU, chuẩn nén, tốc độ encode chỉnh ở tab «Video» (theo từng "
                                             "mẫu / video).")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        self.out_var = ctk.StringVar(value=self.app_state.output_dir)
        ctk.CTkEntry(row, textvariable=self.out_var, height=32, font=T.font(12)).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Chọn", width=60, height=32, command=self._browse_out, **T.secondary_button()).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Mở", width=50, height=32, command=self._open_out, **T.primary_button()).pack(side="left")
        self.loudness = SliderRow(b, "Độ to file xuất", from_=-23, to=-9, step=1, value=st.loudness_target, suffix=" LUFS",
                                  info="Dùng khi bật «Chuẩn hoá âm lượng» ở tab «Video»: đo độ to cả bài rồi chỉnh đúng "
                                       "một lần, đỉnh luôn ≤ −1 dBTP. TikTok · Facebook · YouTube: −14. Truyền hình: −23.")
        self.loudness.pack(fill="x", pady=(6, 3))
        self.toast = SwitchRow(b, "Báo khi xuất xong", value=st.windows_toast,
                               info="Hiện thông báo của Windows / macOS khi render xong.")
        self.toast.pack(fill="x", pady=3)

        b = self._card(c, "Giọng đọc")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        button(row, "Từ điển phát âm…", icon_name="book", size="sm", command=self._open_pron).pack(side="left")
        info_icon(row, "Mở pronunciation.json: «chữ viết»: «cách đọc», vd \"ANC\": \"ây en xi\". Áp cho mọi giọng — lưu file "
                       "rồi tạo lại giọng.").pack(side="left", padx=(6, 0))

        b = self._card(c, "Giao diện", info="Chọn một bộ màu — đổi ngay, không cần mở lại.")
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
        self.waveform = SwitchRow(b, "Dải sóng âm dưới thanh tua", value=st.show_waveform)
        self.waveform.pack(fill="x", pady=3)

        b = self._card(c, "Bộ nhớ tạm")
        self.cache_label = ctk.CTkLabel(b, text="", font=T.font(11), text_color=T.TEXT_DIM, anchor="w")
        self.cache_label.pack(fill="x")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(row, text="Làm mới", width=80, height=32, command=self._cache_sizes, **T.secondary_button()).pack(side="left")
        ctk.CTkButton(row, text="Dọn cache tạm", width=110, height=32, command=self._clean_cache, **T.secondary_button()).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Xóa job & Project", width=130, height=32, command=self._clean_jobs, **T.danger_button()).pack(side="right")
        self._cache_sizes()

        b = self._card(c, "Nâng cao", info="Chỉ cần khi máy không tự tìm thấy ffmpeg (để trống = dùng bản đi kèm).")
        ffm, ffp = exe_name("ffmpeg"), exe_name("ffprobe")
        self.ffmpeg = EntryRow(b, ffm, value=st.ffmpeg_path, browse="exe", placeholder=f"để trống = bin/{ffm} hoặc PATH")
        self.ffprobe = EntryRow(b, ffp, value=st.ffprobe_path, browse="exe", placeholder=f"để trống = bin/{ffp} hoặc PATH")
        for r in (self.ffmpeg, self.ffprobe):
            r.pack(fill="x", pady=3)

    # ── Trang phím tắt & cẩm nang ────────────────────────────────────────
    def _build_guide(self, page) -> None:
        page.grid_columnconfigure((0, 1), weight=1)
        head = ctk.CTkFrame(page, fg_color=T.BG_CARD, corner_radius=10)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(head, text="ⓘ Trợ giúp, phím tắt & cẩm nang", font=T.font(15, "bold"), text_color=T.ACCENT, anchor="w").pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text="Tra cứu nhanh phím tắt và các tính năng chính.",
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

    # ── Trạng thái AI trên máy ───────────────────────────────────────────
    def _scan_machine(self) -> None:
        """Dò GPU / encoder / CUDA / các model trong nền (nạp DLL CUDA, gọi ffmpeg…) rồi điền dòng trạng thái."""
        ff = FFmpeg(self.ffmpeg.get(), self.ffprobe.get())
        main_ff = getattr(self.window, "ffmpeg", None)
        if main_ff is not None and (ff.ffmpeg, ff.ffprobe) == (main_ff.ffmpeg, main_ff.ffprobe):
            ff = main_ff             # cùng ffmpeg với cửa sổ chính → dùng lại kết quả dò encoder đã cache

        def work():
            from app.services.whisper_stt import cuda_ready

            name = gpu_name()
            nvidia = "NVIDIA" in name.upper()
            enc = [b for b in ("nvenc", "qsv", "amf") if ff.available and ff.encoder_works(f"h264_{b}")]
            return name, nvidia, enc, (cuda_ready() if nvidia else False)

        def done(result, error):
            if not self.winfo_exists():
                return
            if error or result is None:
                self.gpu_status.warn(f"Không dò được card đồ hoạ: {error}")
                result = ("", False, [], False)
            name, nvidia, enc, cuda = result
            self._nvidia = nvidia
            if name:
                names = {"nvenc": "NVIDIA NVENC", "qsv": "Intel QSV", "amf": "AMD AMF"}
                if enc:
                    self.gpu_status.ok(f"✓ {name} · xuất video bằng {names[enc[0]]}")
                else:
                    self.gpu_status.info(f"{name} · xuất video bằng CPU (GPU không dùng được — cập nhật driver?)")
            self._show_cuda(nvidia, cuda)
            self._refresh_local()

        self.window.run_bg(work, done)

    def _show_cuda(self, nvidia: bool, cuda: bool) -> None:
        self.cuda_btn.pack_forget()
        if not nvidia:
            self.cuda_status.info("Không có GPU NVIDIA → AI trên máy chạy bằng CPU (chậm hơn).")
        elif cuda:
            self.cuda_status.ok("✓ CUDA sẵn sàng — Whisper chạy trên GPU.")
        else:
            self.cuda_status.warn("Thiếu CUDA runtime → Whisper chạy CPU, bỏ bước nghe lại giọng đọc.")
            self.cuda_btn.pack(side="right")

    def _refresh_local(self) -> None:
        """Trạng thái Qwen3-ASR / VieNeu / Whisper / model nhỏ (đọc đĩa, nhanh)."""
        from app.services import diarize, vocal_remove
        from app.services.ctc_align import DOWNLOAD_MB, model_ready as align_ok
        from app.services.qwen_asr import INSTALL_GB, is_installed as qwen_ok
        from app.services.tts.quality import local_whisper
        from app.services.tts.vieneu import is_installed as vieneu_ok

        for btn in (self.qwen_btn, self.vieneu_btn, self.whisper_btn, self.align_btn):
            btn.pack_forget()
        if align_ok():
            self.align_status.ok("✓ Đã cài — chữ nổi khớp từng tiếng.")
        else:
            self.align_status.warn(f"Chưa cài — chữ nổi đang chia gần đúng (tải ~{DOWNLOAD_MB} MB, một lần).")
            self.align_btn.pack(side="right")
        if qwen_ok():
            self.qwen_status.ok("✓ Đã cài.")
        else:
            self.qwen_status.warn(f"Chưa cài — cần để nhận diện giọng nói (~{INSTALL_GB} GB, một lần).")
            self.qwen_btn.pack(side="right")
        if vieneu_ok():
            self.vieneu_status.ok("✓ Đã cài.")
        else:
            self.vieneu_status.warn("Chưa cài — đang dùng Edge (cần mạng) thay thế.")
            self.vieneu_btn.pack(side="right")
        size = local_whisper()
        if size:
            self.whisper_status.ok(f"✓ Đã có model «{size}»" + ("." if self._nvidia else " (chạy CPU — chỉ dùng để đọc liền)."))
        else:
            self.whisper_status.warn("Chưa có model — bỏ bước nghe lại và đọc liền.")
            self.whisper_btn.pack(side="right")
        uvr = "đã có" if vocal_remove.model_ready() else f"tự tải lần đầu (~{vocal_remove.MODEL_SIZE_MB} MB)"
        spk = "đã có" if diarize.MODEL_PATH.exists() else f"tự tải lần đầu (~{diarize.MODEL_SIZE_MB} MB)"
        self.small_status.info(f"Khử giọng gốc: {uvr} · Nhận ra người nói: {spk}.")

    def _after_install(self, what: str) -> Callable:
        def done(ok: bool, error: Exception | None) -> None:
            if not self.winfo_exists():
                return
            if not ok:
                self.window.log(f"✖ {what} thất bại: {error or 'xem log'}")
            self._scan_machine()
        return done

    def _install_qwen(self) -> None:
        from app.ui.local_ai import install_qwen

        if install_qwen(self.window, self._after_install("Cài Qwen3-ASR")):
            self.qwen_status.warn("Đang cài (vài chục phút)… xem log")

    def _install_vieneu(self) -> None:
        from app.ui.local_ai import install_vieneu

        if install_vieneu(self.window, self._after_install("Cài VieNeu")):
            self.vieneu_status.warn("Đang cài (vài phút)… xem log")

    def _install_cuda(self) -> None:
        from app.ui.local_ai import install_cuda

        if install_cuda(self.window, self._after_install("Tải CUDA runtime")):
            self.cuda_status.warn("Đang tải CUDA runtime… xem log")

    def _download_whisper(self) -> None:
        from app.ui.local_ai import download_whisper, whisper_size

        if download_whisper(self.window, whisper_size(self._nvidia), self._after_install("Tải Whisper")):
            self.whisper_status.warn("Đang tải model… xem log")

    def _install_aligner(self) -> None:
        from app.ui.local_ai import install_aligner

        if install_aligner(self.window, self._after_install("Cài bộ căn chữ nổi")):
            self.align_status.warn("Đang tải model… xem log")

    # ── Hành động ────────────────────────────────────────────────────────
    def _pick_theme(self, key: str) -> None:
        """Đổi theme ngay: lưu cài đặt, dựng lại giao diện chính, mở lại cửa sổ Cài đặt."""
        if key == T.THEME_NAME:
            return
        self._save(close=False, silent=True)
        self.window.apply_theme_live(key)

    def _refresh_models(self) -> None:
        """Hỏi Google các model Gemini key này dùng được → cập nhật danh sách (lưu cache, Dựng Truyện cũng dùng)."""
        from app.services.gemini_models import fetch_models

        keys = self.app_state.settings.split_keys(self._key_text())
        if not keys:
            self.models_status.configure(text="Nhập Gemini API key trước rồi bấm lại.", text_color=T.TEXT_MUTED)
            return
        self.models_status.configure(text="Đang hỏi Google danh sách model…", text_color=T.TEXT_MUTED)
        self.models_btn.configure(state="disabled")

        def done(result, error):
            self.models_btn.configure(state="normal")
            if error:
                self.models_status.configure(text=f"Không lấy được danh sách model: {error}")
                return
            cur = self.tr_model.get()
            self.tr_model.configure(values=text_models())
            self.tr_model.set(migrate_model(cur) or DEFAULT_MODEL)
            self.models_status.configure(text=f"✓ Đã cập nhật: {len(result['text'])} model văn bản · "
                                              f"{len(result['image'])} model ảnh · {len(result['video'])} model video.")

        self.window.run_bg(lambda: fetch_models(keys[0]), done)

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
        st.windows_toast = self.toast.get()
        st.ffmpeg_path, st.ffprobe_path = self.ffmpeg.get().strip(), self.ffprobe.get().strip()
        st.vieneu_precision = self.vieneu.get()
        st.loudness_target = float(self.loudness.get())
        st.theme = T.THEME_NAME
        st.show_waveform = self.waveform.get()
        st.translate_model = self.tr_model.get().strip() or DEFAULT_MODEL
        st.gemini_api_keys = "\n".join(st.split_keys(self._key_text()))
        st.glossary = self.glossary.get("1.0", "end").strip()
        out = self.out_var.get().strip()
        if out:
            self.app_state.output_dir = out
        self.app_state.save_settings()
        if silent:
            return
        self.window.apply_settings()
        self.window.refresh_modules()
        if close:
            self.destroy()
