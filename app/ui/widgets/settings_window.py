"""Cửa sổ Cài đặt (toàn màn hình): 🖥 AI trên máy · ☁ AI dùng API key · ⚙ Chung — và trang Phím tắt & Hướng dẫn."""

from __future__ import annotations

import json
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.ui.icons import EMOJI_ICONS, icon
from app.config.paths import APP_VERSION, PRON_FILE
from app.core.platform import exe_name
from app.services.ffmpeg import FFmpeg
from app.services.sysinfo import gpu_name
from app.services.gemini_models import migrate_model, text_models
from app.services.translate_router import DEEPSEEK_KEY_URL, DEEPSEEK_MODELS, DEFAULT_MODEL
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
        ("Thư mục xuất & mẫu", "Tuỳ chỉnh nơi lưu video. Lưu thiết lập thành mẫu ở ô «Mẫu» trên cùng (xuất / nhập file mẫu để chia sẻ)."),
    ]),
    ("Bộ công cụ video (F1 – F4)", T.SUCCESS, [
        ("F1 · Video & hàng đợi", "Kéo thả video vào. Khung hình, tốc độ, âm lượng, khử giọng gốc."),
        ("F2 · Nhận diện lời thoại", "AI nghe giọng nói ngay trên máy; video có phụ đề in sẵn thì tự đối chiếu chữ."),
        ("F3 · Lời thoại & phụ đề", "Sửa từng câu, «Nhờ AI sửa», dịch. Kiểu phụ đề mặc định giống TikTok; chỉnh chi tiết khi cần."),
        ("F4 · Lồng tiếng", "Giọng Việt VieNeu, khớp mềm với hình, mỗi người nói một giọng."),
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


#: trang (khoá, tên, icon) — menu bên trái, mỗi lần hiện một trang
PAGES = [("local", "AI trên máy", "gpu"), ("cloud", "AI dùng API key", "lock"), ("export", "Xuất video", "export"),
         ("look", "Giao diện", "color"), ("storage", "Bộ nhớ & nâng cao", "folder"),
         ("guide", "Phím tắt & hướng dẫn", "keyboard")]


class SettingsWindow(ctk.CTkToplevel):
    NAV_W = 224

    def __init__(self, window, tab: str | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.app_state = window.app_state
        st = self.app_state.settings
        self.title(f"Cài đặt — Edit Studio v{APP_VERSION}")
        w, h = min(1060, self.winfo_screenwidth() - 80), min(800, self.winfo_screenheight() - 120)
        try:                                       # giữa cửa sổ chính
            x = window.winfo_rootx() + max(0, (window.winfo_width() - w) // 2)
            y = window.winfo_rooty() + max(0, (window.winfo_height() - h) // 3)
        except Exception:                          # noqa: BLE001
            x, y = 40, 40
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(900, 600)
        self.after(50, self.lift)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._nvidia = False
        self.page = ""

        # ── Menu trái ──
        nav = ctk.CTkFrame(self, fg_color=T.BG_PANEL, corner_radius=0, width=self.NAV_W)
        nav.grid(row=0, column=0, rowspan=2, sticky="nsw")
        nav.pack_propagate(False)
        ctk.CTkLabel(nav, text="Cài đặt", font=T.font(17, "bold"), anchor="w").pack(fill="x", padx=20, pady=(18, 12))
        self.nav_items: dict[str, tuple] = {}
        for key, label, ic in PAGES:
            self._nav_item(nav, key, label, ic)
        ctk.CTkLabel(nav, text=f"Edit Studio v{APP_VERSION}", font=T.font(11), text_color=T.TEXT_MUTED,
                     anchor="w").pack(side="bottom", fill="x", padx=20, pady=14)

        # ── Các trang (Phím tắt & hướng dẫn dựng khi mở lần đầu — ~150 widget) ──
        self.pages: dict[str, ctk.CTkScrollableFrame] = {
            k: ctk.CTkScrollableFrame(self, fg_color="transparent") for k, *_ in PAGES if k != "guide"}
        self._build_local(self.pages["local"], st)
        self._build_cloud(self.pages["cloud"], st)
        self._build_export(self.pages["export"], st)
        self._build_look(self.pages["look"], st)
        self._build_storage(self.pages["storage"], st)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=1, column=1, sticky="ew", padx=(18, 16), pady=(6, 12))
        ctk.CTkLabel(foot, text=" Bấm «Lưu tất cả» để áp dụng. Key chỉ lưu trên máy này.",
                     image=icon(EMOJI_ICONS["💡"], 14, dy=1), compound="left", font=T.font(11),
                     text_color=T.TEXT_MUTED).pack(side="left")
        ctk.CTkButton(foot, text="Đóng", width=100, height=36, command=self._close, **T.secondary_button()).pack(side="right")
        button(foot, "Lưu tất cả", icon_name="save", variant="primary", command=self._save).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._badge("cloud", "" if st.translate_keys() else "!")
        self.show_page({"shortcuts": "guide", "model": "cloud"}.get(tab or "", tab or "local"))
        self._initial = self._values()
        self.after(200, self._scan_machine)
        if tab == "model":
            self.after(150, self.focus_model)

    def focus_model(self) -> None:
        """Từ nút «Chọn model…» (model đang chọn hết lượt / quá tải): chỉ đúng chỗ đổi model."""
        self.show_page("cloud")
        self.models_status.configure(text="⬆ Model đang chọn hết lượt / quá tải — chọn model khác ở ô «Model» rồi bấm "
                                          "«Lưu tất cả».", text_color=T.WARNING)
        try:
            self.tr_model.focus_set()
        except Exception:                          # noqa: BLE001 — chỉ là đưa con trỏ tới, không quan trọng
            pass

    # ── Menu trái ────────────────────────────────────────────────────────
    def _nav_item(self, nav, key: str, label: str, icon_name: str) -> None:
        row = ctk.CTkFrame(nav, fg_color="transparent", corner_radius=T.RADIUS, height=40)
        row.pack(fill="x", padx=10, pady=1)
        row.pack_propagate(False)
        ic = ctk.CTkLabel(row, text="", width=22, image=icon(icon_name, 16, T.TEXT_DIM, dy=1))
        ic.pack(side="left", padx=(10, 8))
        txt = ctk.CTkLabel(row, text=label, font=T.font(13), text_color=T.TEXT_DIM, anchor="w")
        txt.pack(side="left", fill="x", expand=True)
        badge = ctk.CTkLabel(row, text="", font=T.font(11, "bold"), text_color=T.WARNING, width=10)
        badge.pack(side="right", padx=(0, 12))
        for w in (row, ic, txt, badge):
            w.bind("<Button-1>", lambda _e, k=key: self.show_page(k))
            w.bind("<Enter>", lambda _e, k=key: self._nav_paint(k, hover=True))
            w.bind("<Leave>", lambda _e, k=key: self._nav_paint(k))
            try:
                w.configure(cursor="hand2")
            except Exception:                      # noqa: BLE001
                pass
        self.nav_items[key] = (row, ic, txt, badge, icon_name)

    def _nav_paint(self, key: str, hover: bool = False) -> None:
        row, ic, txt, _badge, name = self.nav_items[key]
        on = key == self.page
        row.configure(fg_color=T.ACCENT_SOFT if on else (T.BG_HOVER if hover else "transparent"))
        txt.configure(text_color=T.ACCENT if on else (T.TEXT if hover else T.TEXT_DIM),
                      font=T.font(13, "medium" if on else "normal"))
        ic.configure(image=icon(name, 16, T.ACCENT if on else (T.TEXT if hover else T.TEXT_DIM), dy=1))

    def _badge(self, key: str, text: str) -> None:
        """Nhãn nhắc trên menu (vd số phần AI chưa cài, «!» khi chưa có key)."""
        self.nav_items[key][3].configure(text=text)

    def show_page(self, key: str) -> None:
        key = key if key in self.nav_items else "local"
        if key == "guide" and "guide" not in self.pages:
            self.pages["guide"] = ctk.CTkScrollableFrame(self, fg_color="transparent")
            self._build_guide(self.pages["guide"])
        for k, p in self.pages.items():
            if k != key:
                p.grid_forget()
        self.pages[key].grid(row=0, column=1, sticky="nsew", padx=(18, 12), pady=(14, 0))
        self.page = key
        for k in self.nav_items:
            self._nav_paint(k)

    # ── Đóng / thay đổi chưa lưu ─────────────────────────────────────────
    def _values(self) -> tuple:
        """Giá trị các ô cần «Lưu tất cả» (giao diện đổi là lưu ngay nên không tính) — đóng mà khác thì hỏi lưu."""
        return (self.toast.get(), self.ffmpeg.get().strip(), self.ffprobe.get().strip(), self.vieneu.get(),
                float(self.loudness.get()), self.waveform.get(), self.tr_model.get().strip(),
                tuple(self.app_state.settings.split_keys(self._key_text())), self.ds_key.get().strip(),
                self.glossary.get("1.0", "end").strip(), self.out_var.get().strip())

    def _close(self) -> None:
        try:
            dirty = self._values() != self._initial
        except Exception:                          # noqa: BLE001 — cửa sổ đang huỷ dở
            dirty = False
        if dirty:
            ans = messagebox.askyesnocancel("Cài đặt", "Lưu các thay đổi trước khi đóng?", parent=self)
            if ans is None:
                return
            if ans:
                self._save()
                return
        self.destroy()

    # ── Khung ────────────────────────────────────────────────────────────
    def _card(self, parent, title: str, *, sub: str = "", info: str = "") -> ctk.CTkFrame:
        """Thẻ có tiêu đề (``sub``: tên thành phần, chữ nhỏ; ``info``: giải thích dài trong ⓘ)."""
        card = ctk.CTkFrame(parent, fg_color=T.BG_CARD, corner_radius=10, border_width=1, border_color=T.BORDER_SOFT)
        card.pack(fill="x", pady=5)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text=title, font=T.font(13, "bold"), text_color=T.TEXT, anchor="w").pack(side="left")
        if sub:
            ctk.CTkLabel(head, text=sub, font=T.font(11), text_color=T.TEXT_MUTED, anchor="w").pack(side="left", padx=(6, 0))
        if info:
            info_icon(head, info).pack(side="left", padx=(4, 0))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=12, pady=(4, 12))
        return body

    def _page_head(self, parent, title: str, sub: str) -> None:
        ctk.CTkLabel(parent, text=title, font=T.font(18, "bold"), text_color=T.TEXT, anchor="w").pack(fill="x", padx=4)
        ctk.CTkLabel(parent, text=sub, font=T.font(12), text_color=T.TEXT_MUTED, anchor="w", justify="left",
                     wraplength=760).pack(fill="x", padx=4, pady=(0, 6))

    @staticmethod
    def _status_row(body) -> tuple[StatusLabel, ctk.CTkFrame]:
        """Dòng trạng thái + chỗ đặt nút (Cài / Tải) bên phải."""
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(2, 0))
        status = StatusLabel(row, "Đang kiểm tra…")
        status.pack(side="left", fill="x", expand=True)
        return status, row

    # ── Trang: AI trên máy ───────────────────────────────────────────────
    def _build_local(self, c, st) -> None:
        self._page_head(c, "AI trên máy", "Miễn phí, không giới hạn lượt, không cần mạng sau khi cài. Có GPU NVIDIA thì "
                                          "nhanh hơn nhiều.")
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
                            "nghĩa). Cài một lần.")
        self.qwen_status, row = self._status_row(b)
        self.qwen_btn = button(row, "Cài", icon_name="download", size="sm", command=self._install_qwen)

        b = self._card(c, "Giọng đọc tiếng Việt", sub="VieNeu",
                       info="25 giọng Việt (Bắc / Trung / Nam) chạy trên máy, có clone giọng. Câu đọc chưa đạt tự đọc "
                            "lại; câu không đọc được (lỗi) thì đọc tạm bằng Edge (cần mạng) thay vì để im lặng.")
        self.vieneu_status, row = self._status_row(b)
        self.vieneu_btn = button(row, "Cài", icon_name="download", size="sm", command=self._install_vieneu)
        self.vieneu = OptionRow(b, "Chất lượng", ["int8", "fp32"], value=st.vieneu_precision,
                                labels={"int8": "Nhanh", "fp32": "Cao (chậm hơn)"},
                                info="Nhanh: đủ tốt cho hầu hết video. Cao: giọng mượt hơn một chút nhưng đọc chậm hơn.")
        self.vieneu.pack(fill="x", pady=(6, 0))
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        button(row, "Từ điển phát âm (khi đọc)…", icon_name="book", size="sm", command=self._open_pron,
               tip="Cách đọc tên hãng / tên riêng cho giọng lồng, vd «Xiaomi» → «xao mi». Khác «Từ điển tên riêng» "
                   "(dùng khi dịch) ở trang AI dùng API key.").pack(side="left")
        info_icon(row, "Mở pronunciation.json: «chữ viết»: «cách đọc», vd \"ANC\": \"ây en xi\". Áp cho mọi giọng — lưu file "
                       "rồi tạo lại giọng.").pack(side="left", padx=(6, 0))

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

    # ── Trang: AI dùng API key ───────────────────────────────────────────
    def _build_cloud(self, c, st) -> None:
        self._page_head(c, "AI dùng API key", "Cần mạng. Gemini miễn phí có giới hạn lượt mỗi ngày; DeepSeek trả phí "
                                                  "theo lượng dùng.")
        b = self._card(c, "Model dịch", info="Dùng để: dịch phụ đề, tách câu nhận diện quá dài, «Nhờ AI sửa», khớp độ dài "
                                             "câu đọc (Dựng truyện chọn model riêng ở tab Truyện). Cách dịch (cả video một "
                                             "lượt, phóng tác, lời dặn của mẫu) giống nhau với mọi model.")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text="Model", font=T.font(12), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        info_icon(row, "gemini-…: dùng key Gemini (miễn phí có hạn mức). deepseek-flash: nhanh, rẻ, đủ cho clip đời "
                       "thường · deepseek-v4-pro: hay hơn với tiếng lóng, biệt danh, ẩn dụ — dùng key DeepSeek. Hết lượt "
                       "hoặc quá tải lâu: máy dừng và báo để bạn chọn model khác — không tự đổi model giữa chừng. Bấm ↻ "
                       "để lấy danh sách model Gemini mới nhất.").pack(side="left", padx=(4, 8))
        self.tr_model = Select(row, (text_models() or [DEFAULT_MODEL]) + DEEPSEEK_MODELS, height=34, placeholder="")
        self.tr_model.set(st.translate_model or DEFAULT_MODEL)
        self.tr_model.pack(side="left", fill="x", expand=True)
        self.models_btn = button(row, "", icon_name="refresh", size="sm", width=36, command=self._refresh_models,
                                 tip="Lấy danh sách model Gemini mới nhất bằng API key Gemini")
        self.models_btn.pack(side="left", padx=(6, 0))
        self.models_status = hint(b, "")
        self.models_status.pack(fill="x")

        b = self._card(c, "Gemini", info="Gói miễn phí: Google có thể dùng chữ bạn gửi để cải thiện sản phẩm — đừng gửi "
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

        b = self._card(c, "DeepSeek", info="Trả phí theo lượng dùng (nạp trước ở platform.deepseek.com). Dùng khi chọn "
                                           "model «deepseek-…» ở trên. Máy tắt chế độ suy nghĩ khi dịch (nhanh, rẻ — lời "
                                           "nhắc đã bắt dịch sát rồi mới phóng tác); phần luật cố định được DeepSeek "
                                           "nhớ giữa các lượt nên giá đầu vào rẻ hơn nhiều.")
        r = ctk.CTkFrame(b, fg_color="transparent")
        r.pack(fill="x")
        ctk.CTkLabel(r, text="API key", font=T.font(12), text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        info_icon(r, "Một key. Che khi không sửa — bấm «Hiện» để xem. Lưu xong máy hỏi thử số dư (không tốn tiền).").pack(
            side="left", padx=(4, 0))
        ctk.CTkButton(r, text="Lấy key ↗", width=80, height=32, command=lambda: webbrowser.open(DEEPSEEK_KEY_URL),
                      **T.secondary_button()).pack(side="right")
        self.ds_toggle = ctk.CTkButton(r, text="Hiện", width=90, height=32, command=self._toggle_ds,
                                       **T.secondary_button())
        self.ds_toggle.pack(side="right", padx=(0, 6))
        self.ds_key = ctk.CTkEntry(b, height=32, font=T.mono(11), show="•")
        self.ds_key.insert(0, st.deepseek_api_key)
        self.ds_key.pack(fill="x", pady=(2, 4))
        self.ds_status = hint(b, "")
        self.ds_status.pack(fill="x")

        b = self._card(c, "Từ điển tên riêng (khi dịch)",
                       info="Mỗi dòng: chữ gốc=nghĩa, vd 周彦淑=Chu Ngạn Thục. AI dịch đúng như vậy (cả khi dịch lại / "
                            "Nhờ AI sửa). Cách ĐỌC tên (giọng lồng) ở «Từ điển phát âm» trang AI trên máy.")
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

    def _toggle_ds(self) -> None:
        shown = self.ds_key.cget("show") == ""
        self.ds_key.configure(show="•" if shown else "")
        self.ds_toggle.configure(text="Hiện" if shown else "Ẩn")

    def _ds_checked(self, result, error) -> None:
        if not self.winfo_exists():
            return
        ok, why = result if result and not error else (None, str(error or "không rõ"))
        text = (f"✓ Key DeepSeek dùng được — {why}" if ok else f"⚠ Key DeepSeek không dùng được: {why}" if ok is False
                else f"Chưa kiểm được key DeepSeek ({why}) — sẽ thử lại khi dịch.")
        self.ds_status.configure(text=text, text_color=T.WARNING if not ok else T.TEXT_MUTED)
        self.window.log(text)

    def _key_text(self) -> str:
        """Key thật (ô đang che thì lấy bản đã lưu, không lấy chữ đã che)."""
        return self.gemini_keys.get("1.0", "end").strip() if self._keys_shown else self._keys_real

    # ── Trang: Xuất video ────────────────────────────────────────────────
    def _build_export(self, c, st) -> None:
        self._page_head(c, "Xuất video", "Nơi lưu file và độ to âm thanh. Bộ mã hoá, chuẩn nén chỉnh ở mục «Video» (theo "
                                         "từng mẫu / video).")
        b = self._card(c, "Thư mục xuất")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        self.out_var = ctk.StringVar(value=self.app_state.output_dir)
        ctk.CTkEntry(row, textvariable=self.out_var, height=32, font=T.font(12)).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Chọn", width=60, height=32, command=self._browse_out, **T.secondary_button()).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Mở", width=50, height=32, command=self._open_out, **T.secondary_button()).pack(side="left")
        b = self._card(c, "Âm thanh & thông báo")
        self.loudness = SliderRow(b, "Độ to file xuất", from_=-23, to=-9, step=1, value=st.loudness_target, suffix=" LUFS",
                                  info="Dùng khi bật «Chuẩn hoá âm lượng» ở mục «Video»: đo độ to cả bài rồi chỉnh đúng "
                                       "một lần, đỉnh luôn ≤ −1 dBTP. TikTok · Facebook · YouTube: −14. Truyền hình: −23.")
        self.loudness.pack(fill="x", pady=(2, 3))
        self.toast = SwitchRow(b, "Báo khi xuất xong", value=st.windows_toast,
                               info="Hiện thông báo của Windows / macOS khi render xong.")
        self.toast.pack(fill="x", pady=3)

    # ── Trang: Giao diện ─────────────────────────────────────────────────
    def _build_look(self, c, st) -> None:
        self._page_head(c, "Giao diện", "Bấm một bộ màu là đổi ngay, không cần mở lại app.")
        b = self._card(c, "Bộ màu")
        grid = ctk.CTkFrame(b, fg_color="transparent")
        grid.pack(fill="x", pady=(2, 6))
        grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="theme")
        self.theme_cards: dict[str, ctk.CTkFrame] = {}
        for i, (key, th) in enumerate(T.THEMES.items()):
            card = ctk.CTkFrame(grid, fg_color=T.BG_INPUT, corner_radius=T.RADIUS, border_width=2,
                                border_color=T.ACCENT if key == T.THEME_NAME else T.BORDER_SOFT, cursor="hand2")
            card.grid(row=i // 3, column=i % 3, sticky="nsew", padx=4, pady=4)
            # ô xem trước: nền · panel · thẻ · chấm màu nhấn
            sw = ctk.CTkFrame(card, fg_color=th["bg"], corner_radius=6, height=48)
            sw.pack(fill="x", padx=8, pady=(8, 4))
            sw.pack_propagate(False)
            ctk.CTkFrame(sw, fg_color=th["panel"], corner_radius=4, width=26).pack(side="left", fill="y", padx=(4, 3), pady=4)
            inner = ctk.CTkFrame(sw, fg_color=th["card"], corner_radius=4)
            inner.pack(side="left", fill="both", expand=True, padx=(0, 4), pady=4)
            ctk.CTkFrame(inner, fg_color=th["accent"], corner_radius=6, width=34, height=12).place(relx=0.08, rely=0.3)
            ctk.CTkFrame(inner, fg_color=th["dim"], corner_radius=2, width=60, height=4).place(relx=0.5, rely=0.42)
            name = th["name"] + ("  ✓" if key == T.THEME_NAME else "")
            ctk.CTkLabel(card, text=name, font=T.font(12, "bold"), anchor="w").pack(fill="x", padx=10)
            ctk.CTkLabel(card, text=th["desc"], font=T.font(11), text_color=T.TEXT_MUTED, anchor="w", justify="left",
                         wraplength=220).pack(fill="x", padx=10, pady=(0, 8))
            for w in (card, sw, inner, *card.winfo_children()):
                w.bind("<Button-1>", lambda _e, k=key: self._pick_theme(k))
            self.theme_cards[key] = card
        b = self._card(c, "Khung xem trước")
        self.waveform = SwitchRow(b, "Dải sóng âm dưới thanh tua", value=st.show_waveform)
        self.waveform.pack(fill="x", pady=3)

    # ── Trang: Bộ nhớ & nâng cao ─────────────────────────────────────────
    def _build_storage(self, c, st) -> None:
        self._page_head(c, "Bộ nhớ & nâng cao", "Dọn file đang làm khi ổ đầy. Video đã xuất không bị xoá.")
        b = self._card(c, "File đang làm")
        self.cache_label = ctk.CTkLabel(b, text="", font=T.font(12), text_color=T.TEXT_DIM, anchor="w")
        self.cache_label.pack(fill="x")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        button(row, "Làm mới", icon_name="refresh", command=self._cache_sizes).pack(side="left")
        button(row, "Dọn dẹp…", icon_name="trash", variant="destructive", command=self._clean,
               tip="Như nút «Dọn dẹp» trên cùng: xoá phụ đề, giọng lồng, file tạm và làm trống hàng đợi").pack(side="right")
        self._cache_sizes()

        b = self._card(c, "Đường dẫn ffmpeg", info="Chỉ cần khi máy không tự tìm thấy ffmpeg (để trống = dùng bản đi kèm).")
        ffm, ffp = exe_name("ffmpeg"), exe_name("ffprobe")
        self.ffmpeg = EntryRow(b, ffm, value=st.ffmpeg_path, browse="exe", placeholder=f"để trống = bin/{ffm} hoặc PATH")
        self.ffprobe = EntryRow(b, ffp, value=st.ffprobe_path, browse="exe", placeholder=f"để trống = bin/{ffp} hoặc PATH")
        for r in (self.ffmpeg, self.ffprobe):
            r.pack(fill="x", pady=3)

    # ── Trang phím tắt & cẩm nang ────────────────────────────────────────
    def _build_guide(self, page) -> None:
        page.grid_columnconfigure((0, 1), weight=1, uniform="col")
        head = ctk.CTkFrame(page, fg_color=T.BG_CARD, corner_radius=10)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(head, text="Phím tắt & hướng dẫn", font=T.font(18, "bold"), text_color=T.TEXT, anchor="w").pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text="Tra cứu nhanh phím tắt và các tính năng chính.",
                     font=T.font(11), text_color=T.TEXT_DIM, anchor="w").pack(fill="x", padx=14, pady=(0, 6))
        button(head, "Mở Hướng dẫn sử dụng đầy đủ (từng bước, theo module)", icon_name="book", variant="primary", command=lambda: self.window.open_guide_window("start")).pack(anchor="w", padx=14, pady=(0, 12))
        for i, (title, rows) in enumerate(SHORTCUTS):
            card = ctk.CTkFrame(page, fg_color=T.BG_CARD, corner_radius=10)
            card.grid(row=1 + i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            ctk.CTkLabel(card, text=title, font=T.font(13, "bold"), text_color=T.TEXT, anchor="w").pack(fill="x", padx=12, pady=(10, 4))
            for key, desc in rows:
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=12, pady=2)
                ctk.CTkLabel(row, text=key, font=T.mono(11), fg_color=T.BG_ELEVATED, corner_radius=6, width=140,
                             text_color=T.INFO).pack(side="left", anchor="n")
                ctk.CTkLabel(row, text=desc, font=T.font(11), anchor="w", justify="left", wraplength=190,
                             text_color=T.TEXT_DIM).pack(side="left", padx=10, fill="x", expand=True)
            ctk.CTkLabel(card, text="").pack(pady=2)
        base = 1 + (len(SHORTCUTS) + 1) // 2
        for i, (title, color, rows) in enumerate(GUIDE):
            card = ctk.CTkFrame(page, fg_color=T.BG_CARD, corner_radius=10, border_width=1, border_color=color)
            card.grid(row=base + i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            ctk.CTkLabel(card, text=title, font=T.font(13, "bold"), text_color=color, anchor="w").pack(fill="x", padx=12, pady=(10, 4))
            for name, desc in rows:
                ctk.CTkLabel(card, text="• " + name, font=T.font(12, "bold"), anchor="w", text_color=T.TEXT).pack(fill="x", padx=12, pady=(4, 0))
                ctk.CTkLabel(card, text=desc, font=T.font(11), anchor="w", justify="left", wraplength=340, text_color=T.TEXT_DIM).pack(fill="x", padx=26)
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
        elif getattr(sys, "frozen", False):
            self.qwen_status.warn("Chưa có. Bản .exe chưa tự cài được — chép thư mục models\\qwen_asr_env + "
                                  "models\\hf từ máy đã cài sang thư mục app này.")
        else:
            self.qwen_status.warn(f"Chưa cài — cần để nhận diện giọng nói (~{INSTALL_GB} GB, một lần).")
            self.qwen_btn.pack(side="right")
        if vieneu_ok():
            self.vieneu_status.ok("✓ Đã cài.")
        elif getattr(sys, "frozen", False):
            self.vieneu_status.warn("Bản .exe này chưa kèm VieNeu — đang dùng Edge (cần mạng). Muốn VieNeu: cài bản mới "
                                    "có VieNeu hoặc chạy bản mã nguồn.")
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
        # số trên menu trái: chỉ phần CẦN cho việc chính (nghe lời thoại) — Whisper, căn chữ nổi, VieNeu là tuỳ chọn
        self._badge("local", "" if qwen_ok() else "1")

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
        self._save(close=False, silent=self._values() == self._initial)
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
        from app.services.cleanup import sizes

        mb = sizes()
        self.cache_label.configure(text=f"Phụ đề & giọng {mb['work']:.1f} MB · File tạm {mb['temp']:.1f} MB · "
                                        f"Cache {mb['cache']:.1f} MB · Tổng: {sum(mb.values()):.1f} MB")

    def _clean(self) -> None:
        self.window.clean_workspace(parent=self)
        if self.winfo_exists():
            self._cache_sizes()

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
        d = self.out_var.get().strip().strip('"')
        if d:
            try:
                Path(d).mkdir(parents=True, exist_ok=True)
            except OSError:
                messagebox.showerror("Cài đặt", f"Không mở được thư mục:\n{d}\n\nỔ đĩa đã rút hoặc đường dẫn sai.",
                                     parent=self)
                return
            open_path(d)

    def _save(self, close: bool = True, silent: bool = False) -> None:
        st = self.app_state.settings
        before = (st.gemini_api_keys, st.translate_model)
        before_ds = st.deepseek_api_key
        st.windows_toast = self.toast.get()
        st.ffmpeg_path, st.ffprobe_path = self.ffmpeg.get().strip(), self.ffprobe.get().strip()
        st.vieneu_precision = self.vieneu.get()
        st.loudness_target = float(self.loudness.get())
        st.theme = T.THEME_NAME
        st.show_waveform = self.waveform.get()
        st.translate_model = self.tr_model.get().strip() or DEFAULT_MODEL
        st.gemini_api_keys = "\n".join(st.split_keys(self._key_text()))
        st.deepseek_api_key = self.ds_key.get().strip()
        st.glossary = self.glossary.get("1.0", "end").strip()
        out = self.out_var.get().strip().strip('"')
        if out and out != self.app_state.output_dir:
            try:                                       # ổ đã rút / gõ sai / không có quyền → báo ngay, không lưu
                Path(out).mkdir(parents=True, exist_ok=True)
            except OSError:
                messagebox.showerror("Cài đặt", f"Không tạo được thư mục xuất:\n{out}\n\nỔ đĩa đã rút, đường dẫn sai "
                                                "hoặc không có quyền ghi — chọn thư mục khác.", parent=self)
                self.show_page("export")
                return
            self.app_state.output_dir = out
            self.app_state.mark_dirty()                # thư mục xuất nằm trong cấu hình làm việc → tự lưu
        if not self.app_state.save_settings():
            messagebox.showerror("Cài đặt", "Không lưu được Cài đặt (ổ đĩa đầy hoặc file settings.json đang bị khoá — "
                                            "OneDrive / phần mềm diệt virus?). Thử lại sau.", parent=self)
            return
        self._initial = self._values()             # đã lưu → đóng không hỏi nữa
        self._badge("cloud", "" if st.translate_keys() else "!")
        if silent:
            return
        self.window.apply_settings()
        self.window.refresh_modules()
        if st.deepseek_api_key and (st.deepseek_api_key != before_ds
                                    or (st.uses_deepseek() and st.translate_model != before[1])):
            from app.services.translate_router import check_deepseek_key

            self.ds_status.configure(text="Đang thử key DeepSeek…", text_color=T.TEXT_MUTED)
            key = st.deepseek_api_key
            self.window.run_bg(lambda: check_deepseek_key(key), self._ds_checked)
        if (st.gemini_api_keys, st.translate_model) != before and st.gemini_keys() and not st.uses_deepseek():
            # vừa đổi key / model → gọi thử ngay (không tốn lượt dịch); cửa sổ còn mở thì ghi kết quả dưới ô model
            self.models_status.configure(text="Đang thử key…", text_color=T.TEXT_MUTED)
            self.window.check_gemini_keys(on_done=self._key_checked)
        if close:
            self.destroy()

    def _key_checked(self, summary: str) -> None:
        if self.winfo_exists():
            self.models_status.configure(text=summary, text_color=T.WARNING if summary.startswith("⚠") else T.TEXT_MUTED)
