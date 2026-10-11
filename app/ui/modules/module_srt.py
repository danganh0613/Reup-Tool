"""Module Lời thoại — hai tab trên cùng:
- «Lời thoại»: sửa từng câu (+ Nhờ AI sửa), dịch AI, tệp phụ đề (chọn / sửa thời gian / xuất / dán bản dịch ngoài);
- «Phụ đề»: chữ hiện trên video — bật, kiểu dựng sẵn, chữ hiện; chỉnh tay (chữ, nền & chữ nổi, khung, thời gian) bấm
  mới mở."""

from __future__ import annotations

import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.config.paths import SUBS_DIR, VOICE_DIR
from app.core.state import SRT_MODE_LABELS, SRT_MODES, TRANSLATE_TARGETS
from app.services.srt_utils import load_srt, stats
from app.services.subtitle_presets import CUSTOM as SUB_CUSTOM, STYLES as SUB_STYLES, apply_style, refit, style_of
from app.services.translate_router import RETRANSLATE_PRESETS
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.common import ButtonRow, OptionRow, Section, StatusLabel, SwitchRow, hint, pack_rows, row_label
from app.ui.widgets.ui import button, button_width, set_variant
from app.core.platform import open_path
from app.ui.progress_panel import friendly_error

MODE_LABELS = SRT_MODE_LABELS
TAB_TEXT, TAB_SUB = "Lời thoại", "Phụ đề"
NO_KEY = "Chưa có API key của model dịch đang chọn — nhập ở Cài đặt › AI dùng API key."


class SRTModule(BaseModule):
    key = "srt"
    title = "Lời thoại"
    icon = "📄"
    section = "subtitle"

    def build(self) -> None:
        cfg = self.state.subtitle
        s = self.setter

        self.tab_var = tk.StringVar(value=TAB_TEXT)
        self.tabs = ctk.CTkSegmentedButton(self, values=[TAB_TEXT, TAB_SUB], variable=self.tab_var,
                                           command=self.show_tab, font=T.font(12))
        self.tabs.pack(fill="x", pady=(0, 6))
        self.pages = {k: ctk.CTkFrame(self, fg_color="transparent") for k in (TAB_TEXT, TAB_SUB)}

        # ══ Tab «Lời thoại» ═════════════════════════════════════════════════
        page = self.pages[TAB_TEXT]
        # lời thoại của video đang chọn: sửa ngay, gắn với khung xem trước (nơi DUY NHẤT sửa từng câu)
        from app.ui.widgets.cue_editor import CueEditor

        sec = Section(page, "Lời thoại của video đang chọn")
        sec.pack(fill="x", pady=4)
        self.editor = CueEditor(sec.body, self.window, on_saved=lambda it: (self.on_active_changed(),
                                                                             self.window.queue_update(it)))
        self.editor.pack(fill="x")

        sec = Section(page, "Dịch", info="AI Gemini dịch 2 bước: dịch sát nghĩa rồi tự đọc lại cho tự nhiên; có lồng "
                                         "tiếng thì tự rút gọn câu đọc không kịp. Lỗi giữa chừng thì «Thử lại» dịch "
                                         "tiếp. Key và model: Cài đặt › AI dùng API key.")
        sec.pack(fill="x", pady=4)
        self.target = OptionRow(sec.body, "Dịch sang", TRANSLATE_TARGETS, on_change=s(cfg, "translate_target", preview=False))
        self.target.pack(fill="x", pady=3)
        # ngôn ngữ gốc: một ô duy nhất «Ngôn ngữ video» ở tab Nhận diện (trước: hai ô ở hai tab, dễ lệch)
        self.src_hint = hint(sec.body, "")
        self.src_hint.pack(fill="x")
        # một ô lời dặn cho AI dịch (dùng chung với tab Duyệt / bảng dịch từng câu) — nhiều dòng: mẫu có thể kèm cả
        # đoạn prompt (vd mẫu «Review phim dọc» dặn dịch phóng tác cho người xem Việt)
        row = ctk.CTkFrame(sec.body, fg_color="transparent")
        row.pack(fill="x", pady=(3, 0))
        row_label(row, "Lời dặn cho AI dịch", "Giọng văn, cách xưng hô AI phải theo khi dịch — vd «xưng anh – em, văn "
                                              "nói». Mẫu có thể điền sẵn. Sửa rồi bấm «Dịch phụ đề» để dịch lại.").pack(side="left")
        button(row, "Gợi ý", icon_name="sparkle", size="sm", command=self._style_menu,
               tip="Chèn nhanh một câu dặn hay dùng").pack(side="right")
        self.style = ctk.CTkTextbox(sec.body, height=96, font=T.font(12), wrap="word")
        self.style.pack(fill="x", pady=(2, 3))
        self.style.bind("<FocusOut>", lambda _e: self._style_changed())
        self.tr_btn = button(sec.body, "Dịch phụ đề", icon_name="translate", command=self._translate_now,
                             min_width=button_width(sec.body, "Dịch phụ đề", icon=True, font=T.font(13, "medium")))
        self.tr_btn.pack(fill="x", pady=(6, 2))
        self.tr_enable = SwitchRow(sec.body, "Tự dịch khi chuẩn bị video", on_change=s(cfg, "translate_enable", preview=False),
                                   info="Bước «Chuẩn bị» tự dịch khi phụ đề hoặc lồng tiếng cần bản dịch.")
        self.tr_enable.pack(fill="x", pady=(4, 0))
        self.tr_status = StatusLabel(sec.body, "")
        self.tr_status.pack(fill="x")

        sec = Section(page, "Tệp phụ đề", collapsed=True)
        sec.pack(fill="x", pady=4)
        self.file_section = sec
        ButtonRow(sec.body, [("Chọn file SRT", self._pick_srt, None), ("Xuất SRT", self._export_srt, None)]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("Sửa thời gian…", self.open_editor, None), ("Dán bản dịch ngoài…", self._manual_translate, None)]).pack(fill="x", pady=2)
        ButtonRow(sec.body, [("Mở bằng app khác", self._open_srt, None),
                             ("Xoá phụ đề & giọng của video…", self._clear_cache, T.danger_button())]).pack(fill="x", pady=2)
        self.file_status = StatusLabel(sec.body, "")
        self.file_status.pack(fill="x", pady=(4, 0))

        # ══ Tab «Phụ đề»: bật / kiểu / chữ hiện; chỉnh tay ở các khối thu gọn bên dưới ═══════════════════
        page = self.pages[TAB_SUB]
        sec = Section(page, "Phụ đề trên video", info="Mặc định kiểu TikTok: chữ đậm trắng viền đen, tối đa 2 dòng, "
                                                      "nằm trên vùng nút của TikTok. Vị trí và cỡ chữ chỉnh nhanh trên "
                                                      "thanh của khung video.")
        sec.pack(fill="x", pady=4)
        self.sub_section = sec
        self.sub_on = SwitchRow(sec.body, "Hiện phụ đề", on_change=self._sub_on_changed)
        self.sub_style = OptionRow(sec.body, "Kiểu", [*SUB_STYLES, SUB_CUSTOM], on_change=self._style_picked)
        self.sub_mode = OptionRow(sec.body, "Chữ hiện", SRT_MODES, labels=MODE_LABELS, on_change=s(cfg, "srt_mode"))
        pack_rows(sec.body, [self.sub_on, self.sub_style, self.sub_mode])
        self.style_box = ctk.CTkFrame(page, fg_color="transparent")
        self.style_box.pack(fill="x")
        self.style_panel = None                  # chỉnh tay: dựng lần đầu mở tab «Phụ đề» (~40 widget)
        self.state.on("config_changed", self._config_changed)
        self.show_tab(TAB_TEXT)

    def refresh(self) -> None:
        cfg = self.state.subtitle
        self._loading = True
        try:
            self.src_hint.configure(text=f"Ngôn ngữ gốc: {cfg.source_hint} (đổi ở tab Nhận diện › «Ngôn ngữ video»)")
            self.target.set(cfg.translate_target)
            self.style.delete("1.0", "end")
            self.style.insert("1.0", cfg.translate_style or "")
            self.tr_enable.set(cfg.translate_enable)
            self.refresh_subtitle()
        finally:
            self._loading = False
        self.on_active_changed()

    def refresh_subtitle(self) -> None:
        """Đổ lại phần «Phụ đề trên video» (đổi trên thanh khung video / kéo khung cũng gọi tới đây)."""
        cfg = self.state.subtitle
        loading, self._loading = self._loading, True
        try:
            self.sub_on.set(cfg.enable_subtitle)
            self.sub_style.set(style_of(cfg))
            self.sub_mode.set(cfg.srt_mode if cfg.srt_mode in SRT_MODES else SRT_MODES[0])
            if self.style_panel is not None:
                self.style_panel.refresh()
        finally:
            self._loading = loading

    # ── Hai tab ──────────────────────────────────────────────────────────
    def show_tab(self, name: str) -> None:
        """«Lời thoại» · «Phụ đề» (bảng chỉnh tay phụ đề dựng lần đầu mở tab)."""
        name = name if name in self.pages else TAB_TEXT
        if self.tab_var.get() != name:
            self.tab_var.set(name)
        if name == TAB_SUB and self.style_panel is None:
            from app.ui.widgets.subtitle_panel import SubtitleStylePanel

            loading, self._loading = self._loading, True
            try:
                self.style_panel = SubtitleStylePanel(self.style_box, self)
                self.style_panel.pack(fill="x")
                self.style_panel.refresh()
            finally:
                self._loading = loading
        for k, page in self.pages.items():
            if k != name:
                page.pack_forget()
        self.pages[name].pack(fill="x")
        if name == TAB_TEXT:
            self.editor.start_tick()               # tô câu đang phát (tự dừng khi tab bị ẩn)
        self.scroll_top()

    def open_style(self) -> None:
        """Từ nơi khác (tab Duyệt «Sửa ›», mục cũ «Phụ đề»): mở tab «Phụ đề»."""
        self.show_tab(TAB_SUB)

    def _config_changed(self, section: str = "") -> None:
        """Phụ đề đổi ở chỗ khác (bảng chi tiết, thanh khung video, kéo khung) → ô «Hiện phụ đề» / «Kiểu» theo ngay."""
        if section != "subtitle" or self._loading:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:                        # noqa: BLE001 — giao diện đã dựng lại (đổi theme)
            return
        cfg = self.state.subtitle
        loading, self._loading = self._loading, True
        try:
            self.sub_on.set(cfg.enable_subtitle)
            self.sub_style.set(style_of(cfg))
            if self.style_panel is not None:
                self.style_panel.summarize()
        finally:
            self._loading = loading

    def _sub_on_changed(self, v: bool) -> None:
        self.set_value(self.state.subtitle, "enable_subtitle", bool(v))
        self.window.preview.refresh_sub_chips()

    def _style_picked(self, name: str) -> None:
        if self._loading:
            return
        if name == SUB_CUSTOM:                  # «Tuỳ chỉnh» = mở khối chỉnh chữ bên dưới
            self.sub_style.set(style_of(self.state.subtitle))
            self.show_tab(TAB_SUB)
            self.style_panel.text_sec.set_open(True)
            return
        cfg = self.state.subtitle
        if not apply_style(cfg, name):
            return
        refit(cfg, *self.window.preview.sub_frame_size())
        self.state.config_changed("subtitle")
        self.refresh_subtitle()
        self.window.preview.refresh_sub_chips()
        self.window.preview.schedule_refresh(0)

    def on_shown(self) -> None:
        super().on_shown()
        self.editor.reload_if_stale()
        if self.tab_var.get() == TAB_TEXT:
            self.editor.start_tick()

    def on_hidden(self) -> None:
        self.editor.stop_tick()
        self._ask_save()

    def _ask_save(self) -> None:
        if self.editor.dirty() and messagebox.askyesno("Edit Studio", "Lưu các câu lời thoại vừa sửa?"):
            self.editor.save()

    def on_active_changed(self) -> None:
        it = self.state.active
        if self.editor.item is not it:
            self._ask_save()
            self.editor.load(it)
        else:
            self.editor.reload_if_stale()
        if self.style_panel is not None:
            self.style_panel.fill_pixels()
            self.style_panel.follow_status()
        has_tr = bool(it and it.srt_translated_path and Path(it.srt_translated_path).exists())
        set_variant(self.tr_btn, "secondary" if has_tr else "primary")     # đã dịch → nút chính là «Lưu» ở trên
        no_key = not self.state.settings.translate_keys()
        if it is None:
            self.file_status.info("Chưa có video được chọn.")
            self.file_section.set_subtitle("")
            if no_key:
                self.tr_status.warn(NO_KEY)
            else:
                self.tr_status.info("")
            return
        if it.srt_path and Path(it.srt_path).exists():
            try:
                cues = load_srt(it.srt_path)
            except Exception:
                cues = None
            name = Path(it.srt_path).name
            if cues == []:                          # sai bảng mã / file hỏng → báo, không để «0 cue» màu xanh
                self.file_status.warn(f"{name}: không đọc được câu nào — mở bằng Notepad, lưu lại dạng UTF-8.")
            else:
                self.file_status.ok(f"{name} · {stats(cues)}" if cues else name)
            self.file_section.set_subtitle(name)
        else:
            self.file_status.info("Chưa có phụ đề — chọn file SRT hoặc tạo ở tab Nhận diện.")
            self.file_section.set_subtitle("chưa có")
        if has_tr:
            self.tr_status.ok(f"Đã dịch: {Path(it.srt_translated_path).name}")
        elif no_key:
            self.tr_status.warn(NO_KEY)
        else:
            self.tr_status.info("Chưa dịch.")

    # ── Tệp ──────────────────────────────────────────────────────────────
    def _need_item(self):
        it = self.state.active
        if it is None:
            messagebox.showinfo("Edit Studio", "Hãy nạp và chọn một video trước.")
        return it

    def _pick_srt(self) -> None:
        it = self._need_item()
        if it is None:
            return
        p = filedialog.askopenfilename(filetypes=[("Phụ đề", "*.srt *.vtt *.txt"), ("Tất cả", "*.*")],
                                       initialdir=str(Path(it.path).parent))
        if not p:
            return
        self.window.pipeline.attach_srt(it, p)      # bản dịch / người nói theo SRT cũ bỏ đi
        self.state.mark_dirty()
        self._after_change(it)

    def _after_change(self, it) -> None:
        self.window.mark_changed(it)                # video đã xuất mà đổi lời thoại → «Cần xuất lại»
        if it is self.state.active and not self.editor.dirty():
            self.editor.load(it)                    # file SRT / bản dịch đổi → nạp lại danh sách câu
        self.on_active_changed()
        self.window.queue_update(it)
        self.window.preview.schedule_refresh(0)
        for k in ("stt", "tts"):
            if k in self.window.modules:
                self.window.modules[k].on_active_changed()

    def open_editor(self) -> None:
        it = self._need_item()
        if it is None:
            return
        from app.ui.widgets.srt_editor import SRTEditorDialog

        SRTEditorDialog(self.window, it, on_saved=lambda: self._after_change(it))

    def _export_srt(self) -> None:
        it = self._need_item()
        if it is None:
            return
        src = it.srt_translated_path if it.srt_translated_path and Path(it.srt_translated_path).exists() else it.srt_path
        if not src or not Path(src).exists():
            messagebox.showinfo("Edit Studio", "Chưa có SRT để xuất.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".srt", initialfile=f"{it.stem}.srt",
                                         filetypes=[("SRT", "*.srt")])
        if p:
            shutil.copyfile(src, p)
            self.window.log(f"Đã xuất {p}")

    def _open_srt(self) -> None:
        """Mở file đang sửa: bản dịch nếu có (cùng file với «Xuất SRT»), không thì lời gốc."""
        it = self.state.active
        for p in ((it.srt_translated_path, it.srt_path) if it else ()):
            if p and Path(p).exists():
                open_path(p)
                return

    def _clear_cache(self) -> None:
        it = self._need_item()
        if it is None:
            return
        if not messagebox.askyesno("Xoá phụ đề & giọng", "Xoá lời thoại, bản dịch (cả chỗ đã sửa tay) và giọng lồng của video này "
                                                   "(trong output/subs, output/voice)?"):
            return
        removed = 0
        files = [it.srt_path, it.srt_translated_path]
        if it.voice_path:                           # track giọng + file đi kèm (.json .srt .cues.csv .words.json)
            files += [str(Path(it.voice_path).with_suffix(ext)) for ext in (".wav", ".json", ".srt", ".cues.csv",
                                                                             ".words.json")]
        files.append(str(self.window.pipeline.noread_path(it)))
        for p in files:
            if p and Path(p).exists() and (Path(p).parent in (SUBS_DIR, VOICE_DIR)):
                try:
                    Path(p).unlink()
                    removed += 1
                except OSError:
                    pass
        it.srt_path = it.srt_translated_path = it.voice_path = None
        it.voice_locked = False
        self.state.mark_dirty()
        self._after_change(it)
        self.window.log(f"Đã xoá {removed} file cache của {it.name}.")

    # ── Dịch ─────────────────────────────────────────────────────────────
    def _translate_now(self) -> None:
        it = self._need_item()
        if it is None:
            return
        if not it.srt_path or not Path(it.srt_path).exists():
            messagebox.showinfo("Edit Studio", "Chưa có phụ đề gốc — nhận diện (tab Nhận diện) hoặc chọn file SRT trước.")
            return
        old = it.srt_translated_path if it.srt_translated_path and Path(it.srt_translated_path).exists() else None
        if old:
            if not messagebox.askyesno("Dịch lại", "Video này đã có bản dịch (có thể đã sửa tay). Dịch lại sẽ thay bản "
                                                   "hiện tại — bản cũ giữ lại thành file .bak cạnh bản dịch.\n\nDịch lại?"):
                return
            if self.editor.item is it and self.editor.dirty():
                self.editor.save(regen=False)       # chữ đang sửa dở cũng vào bản .bak + bộ nhớ dịch
            try:
                shutil.copyfile(old, Path(old).with_suffix(".srt.bak"))
            except OSError:
                pass
        pipe = self.window.pipeline_for(it)
        self.tr_status.warn("Đang dịch… (xem log)")

        def work(cancel):
            return pipe.translate(it, progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=cancel)

        def done(result, error):
            if error:
                self.tr_status.error(f"Lỗi: {friendly_error(str(error))}")
            else:
                self.state.mark_dirty()
                self._after_change(it)
                self.window.add_notification("Dịch xong", f"{it.name} → {self.state.subtitle.translate_target}", "ok")

        self.window.run_task("Dịch phụ đề", work, done)

    def _style_menu(self) -> None:
        """Chèn một câu dặn hay dùng vào cuối ô lời dặn."""
        m = tk.Menu(self, tearoff=0, bg=T.BG_ELEVATED, fg=T.TEXT, activebackground=T.BG_HOVER,
                    activeforeground=T.TEXT, bd=0)
        for p in RETRANSLATE_PRESETS:
            m.add_command(label=p, command=lambda p=p: self._style_add(p))
        m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _style_add(self, text: str) -> None:
        cur = self.style.get("1.0", "end-1c").rstrip()
        self.style.delete("1.0", "end")
        self.style.insert("1.0", (cur + "\n" if cur else "") + text)
        self._style_changed()

    def _style_changed(self) -> None:
        if self._loading:
            return
        v = self.style.get("1.0", "end-1c").strip()
        if v != self.state.subtitle.translate_style:
            self.set_value(self.state.subtitle, "translate_style", v, preview=False)

    def _manual_translate(self) -> None:
        it = self._need_item()
        if it is None:
            return
        if not it.srt_path or not Path(it.srt_path).exists():
            messagebox.showinfo("Dịch thủ công", "Chưa có SRT để dịch.\nHãy chọn file SRT, hoặc tạo phụ đề ở tab Nhận diện trước.")
            return
        from app.ui.widgets.srt_editor import ManualTranslateDialog

        ManualTranslateDialog(self.window, it, on_saved=lambda: self._after_change(it))
