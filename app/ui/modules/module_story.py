"""Module Story: ý tưởng → kịch bản AI → giọng → ảnh/video AI → ghép video → đưa vào hàng đợi render."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import button
from app.core.state import STORY_RATIOS, STORY_RESOLUTIONS
from app.services.gemini_models import image_models, text_models, video_models
from app.services.story.engine import StoryEngine
from app.services.story.project import StoryProject
from app.services.story.prompts import LANGUAGES, LENGTHS, MASTER_PROMPTS, TONES, load_competitors, master_prompt_text
from app.ui.modules.base_module import BaseModule
from app.ui.widgets.common import (ButtonRow, ComboRow, EntryRow, OptionRow, Section, SliderRow, StatusLabel,
                                   SwitchRow, hint, pack_rows)
from app.ui.widgets.voice_picker import VoicePicker
from app.core.platform import open_path

_LENGTH_LABELS = {v[0]: k for k, v in LENGTHS.items()}
_PROMPT_LABELS = {v[0]: k for k, v in MASTER_PROMPTS.items()}


class StoryModule(BaseModule):
    key = "story"
    title = "Dựng Truyện"
    icon = "📖"
    section = "story"

    def build(self) -> None:
        cfg = self.state.story
        s = self.setter
        self.engine = StoryEngine(self.state, self.window.ffmpeg)
        self.project: StoryProject | None = None
        self.editor = None

        # ── Dự án ────────────────────────────────────────────────────────
        sec = Section(self, "Dự án")
        sec.pack(fill="x", pady=4)
        self.name = EntryRow(sec.body, "Tên", placeholder="Tên câu chuyện", on_change=self._name_changed)
        self.name.pack(fill="x", pady=3)
        ButtonRow(sec.body, [
            ("＋ Mới", self._new_project, T.primary_button()),
            ("📂 Mở…", self._open_project, None),
            ("💾 Lưu", self._save_project, None),
            ("📁 Thư mục", self._open_folder, None),
        ]).pack(fill="x", pady=(2, 2))
        self.proj_status = StatusLabel(sec.body, "Chưa mở dự án.")
        self.proj_status.pack(fill="x")

        # ── Ý tưởng ──────────────────────────────────────────────────────
        sec = Section(self, "Ý tưởng & kịch bản")
        sec.pack(fill="x", pady=4)
        ctk.CTkLabel(sec.body, text="Chủ đề / ý tưởng (để trống = AI tự chọn theo phong cách kênh)",
                     font=T.font(11), text_color=T.TEXT_MUTED, anchor="w").pack(fill="x")
        self.topic = ctk.CTkTextbox(sec.body, height=90, font=T.font(12), wrap="word")
        self.topic.pack(fill="x", pady=(2, 4))
        self.topic.bind("<FocusOut>", lambda _e: self._topic_changed())
        self.language = OptionRow(sec.body, "Ngôn ngữ", LANGUAGES, on_change=s(cfg, "language", preview=False))
        self.length = OptionRow(sec.body, "Độ dài", [v[0] for v in LENGTHS.values()], on_change=self._length_changed)
        self.tone = OptionRow(sec.body, "Giọng văn", TONES, on_change=s(cfg, "tone", preview=False))
        self.genre = EntryRow(sec.body, "Thể loại", placeholder="vd: chuyện đời, trinh thám, cổ tích…",
                              on_change=s(cfg, "genre", preview=False))
        self.competitor = OptionRow(sec.body, "Phong cách kênh", ["(không)"], on_change=self._competitor_changed)
        self.text_model = ComboRow(sec.body, "Model viết", text_models(), on_change=s(cfg, "text_model", preview=False))
        pack_rows(sec.body, [self.language, self.length, self.tone, self.genre, self.competitor, self.text_model])
        ButtonRow(sec.body, [("🏷 Quản lý phong cách kênh…", self._manage_competitors, None)]).pack(fill="x", pady=(4, 0))

        # ── Hình ảnh ─────────────────────────────────────────────────────
        sec = Section(self, "Hình ảnh")
        sec.pack(fill="x", pady=4)
        self.prompt_preset = OptionRow(sec.body, "Phong cách ảnh", [v[0] for v in MASTER_PROMPTS.values()],
                                       on_change=self._prompt_preset_changed)
        self.prompt_custom = EntryRow(sec.body, "Thêm vào prompt", placeholder="vd: Vietnamese village, 1990s",
                                      on_change=s(cfg, "master_prompt_custom", preview=False))
        self.image_model = ComboRow(sec.body, "Model ảnh", image_models(), on_change=s(cfg, "image_model", preview=False))
        self.ratio = OptionRow(sec.body, "Khung hình", STORY_RATIOS, on_change=s(cfg, "ratio", preview=False))
        self.resolution = OptionRow(sec.body, "Độ phân giải", STORY_RESOLUTIONS, on_change=s(cfg, "resolution", preview=False))
        self.kenburns = SliderRow(sec.body, "Chuyển động ảnh", from_=0, to=25, step=1, suffix="%",
                                  on_change=s(cfg, "kenburns_amount", cast=float, preview=False))
        self.gap = SliderRow(sec.body, "Nghỉ giữa cảnh", from_=0, to=2, step=0.1, fmt="{:.1f}", suffix="s",
                             on_change=s(cfg, "gap", cast=float, preview=False))
        pack_rows(sec.body, [self.prompt_preset, self.prompt_custom, self.image_model, self.ratio, self.resolution,
                             self.kenburns, self.gap])
        self.prompt_preview = hint(sec.body, "")
        self.prompt_preview.pack(fill="x", pady=(2, 0))

        sec = Section(self, "Video AI (Veo)", subtitle="tuỳ chọn, tốn phí API")
        sec.pack(fill="x", pady=4)
        self.use_video = SwitchRow(sec.body, "Tạo clip AI thay cho ảnh tĩnh", on_change=s(cfg, "use_ai_video", preview=False))
        self.video_model = ComboRow(sec.body, "Model video", video_models(), on_change=s(cfg, "video_model", preview=False))
        self.video_dur = SliderRow(sec.body, "Thời lượng clip", from_=4, to=8, step=1, suffix="s",
                                   on_change=s(cfg, "video_duration", cast=int, preview=False))
        pack_rows(sec.body, [self.use_video, self.video_model, self.video_dur])
        hint(sec.body, "Clip AI được tạo từ ảnh của cảnh (image-to-video) và lặp cho đủ thời lượng lời kể.").pack(fill="x")

        # ── Giọng kể ─────────────────────────────────────────────────────
        sec = Section(self, "Giọng kể mặc định", subtitle="nhân vật có giọng riêng: xem Phân vai")
        sec.pack(fill="x", pady=4)
        self.voice = VoicePicker(sec.body, self.window, get=self._voice_get, set_=self._voice_set,
                                 speed=lambda: self.state.story.speed)
        self.voice.pack(fill="x")
        self.speed = SliderRow(sec.body, "Tốc độ", from_=0.5, to=2.0, step=0.05, fmt="{:.2f}", suffix="×",
                               on_change=s(cfg, "speed", cast=float, preview=False))
        self.speed.pack(fill="x", pady=3)

        # ── Thực hiện ────────────────────────────────────────────────────
        sec = Section(self, "Thực hiện")
        sec.pack(fill="x", pady=4)
        steps = [
            ("1 · Viết kịch bản (AI)", self._step_script),
            ("2 · Tạo giọng đọc", self._step_voices),
            ("3 · Tạo ảnh (AI)", self._step_images),
            ("3b · Tạo clip AI cho cảnh chưa có", self._step_videos),
            ("4 · Ghép thành video", self._step_assemble),
            ("5 · Metadata YouTube (AI)", self._step_metadata),
        ]
        for text, cmd in steps:
            ctk.CTkButton(sec.body, text=text, command=cmd, height=32, anchor="w", **T.secondary_button()).pack(
                fill="x", pady=2)
        button(sec.body, "Chạy tất cả (1 → 4)", icon_name="sparkle", command=self._run_all, fg_color=T.SUCCESS,
               hover_color=T.SUCCESS_HOVER, text_color="#0B1220", icon_color="#0B1220").pack(fill="x", pady=(6, 2))
        ButtonRow(sec.body, [
            ("📝 Trình soạn kịch bản", self.open_editor, T.primary_button()),
            ("➕ Đưa vào hàng đợi", self._to_queue, None),
        ]).pack(fill="x", pady=(2, 2))
        self.auto_queue = SwitchRow(sec.body, "Tự đưa vào hàng đợi sau khi ghép", on_change=s(cfg, "auto_queue", preview=False))
        self.auto_queue.pack(fill="x", pady=2)
        self.run_status = StatusLabel(sec.body, "")
        self.run_status.pack(fill="x")
        hint(sec.body, "Video ghép xong được đưa vào hàng đợi cùng SRT — dùng các module Phụ đề, Nhạc nền, "
                       "Logo, Màu sắc rồi bấm Render như video thường.").pack(fill="x", pady=(2, 0))

        self._load_last_project()

    # ── State → widget ────────────────────────────────────────────────────
    def refresh(self) -> None:
        cfg = self.state.story
        self._loading = True
        try:
            self.language.set(cfg.language)
            self.length.set(LENGTHS.get(cfg.length, LENGTHS["medium"])[0])
            self.tone.set(cfg.tone if cfg.tone in TONES else TONES[0])
            self.genre.set(cfg.genre)
            comps = ["(không)"] + list(load_competitors())
            self.competitor.set_values(comps, keep=False)
            self.competitor.set(cfg.competitor if cfg.competitor in comps else "(không)")
            self.text_model.set(cfg.text_model)
            self.prompt_preset.set(MASTER_PROMPTS.get(cfg.master_prompt_preset, MASTER_PROMPTS["cinematic_hyperrealism"])[0])
            self.prompt_custom.set(cfg.master_prompt_custom)
            self.image_model.set(cfg.image_model)
            self.ratio.set(cfg.ratio)
            self.resolution.set(cfg.resolution)
            self.kenburns.set(cfg.kenburns_amount)
            self.gap.set(cfg.gap)
            self.use_video.set(cfg.use_ai_video)
            self.video_model.set(cfg.video_model)
            self.video_dur.set(cfg.video_duration)
            self.speed.set(cfg.speed)
            self.auto_queue.set(cfg.auto_queue)
        finally:
            self._loading = False
        self._update_prompt_preview()
        self._refresh_project_status()

    def _update_prompt_preview(self) -> None:
        cfg = self.state.story
        self.prompt_preview.configure(text="Prompt: " + master_prompt_text(cfg.master_prompt_preset, cfg.master_prompt_custom)[:220])

    def _voice_get(self):
        cfg = self.state.story
        return cfg.provider, cfg.voice_language, cfg.voice_id

    def _voice_set(self, provider: str, locale: str, vid: str, name: str) -> None:
        cfg = self.state.story
        cfg.provider, cfg.voice_language, cfg.voice_id, cfg.voice_name = provider, locale, vid, name
        self.state.config_changed("story")

    def _length_changed(self, label: str) -> None:
        self.set_value(self.state.story, "length", _LENGTH_LABELS.get(label, "medium"), preview=False)

    def _prompt_preset_changed(self, label: str) -> None:
        self.set_value(self.state.story, "master_prompt_preset", _PROMPT_LABELS.get(label, "cinematic_hyperrealism"),
                       preview=False)
        self._update_prompt_preview()

    def _competitor_changed(self, v: str) -> None:
        self.set_value(self.state.story, "competitor", "" if v == "(không)" else v, preview=False)
        c = load_competitors().get(v)
        if c:
            # gợi ý theo preset kênh
            cfg = self.state.story
            if c.get("language") in LANGUAGES:
                cfg.language = c["language"]
            if c.get("master_prompt_preset") in MASTER_PROMPTS:
                cfg.master_prompt_preset = c["master_prompt_preset"]
            if c.get("master_prompt_custom"):
                cfg.master_prompt_custom = c["master_prompt_custom"]
            if c.get("voice_id"):
                cfg.provider = c.get("voice_provider", cfg.provider)
                cfg.voice_id = c["voice_id"]
                cfg.voice_language = "-".join(c["voice_id"].split("-")[:2]) if c["voice_id"].count("-") >= 2 else cfg.voice_language
            if c.get("voice_speed"):
                cfg.speed = float(c["voice_speed"])
            self.state.config_changed("story")
            self.refresh()
            self.voice.load()

    def _manage_competitors(self) -> None:
        from app.ui.widgets.story_dialogs import CompetitorDialog

        CompetitorDialog(self.window, on_close=self.refresh)

    # ── Dự án ────────────────────────────────────────────────────────────
    def _load_last_project(self) -> None:
        p = self.state.story.last_project_path
        if p and Path(p).exists():
            try:
                self.project = StoryProject.load(p)
            except Exception as exc:
                self.window.log(f"Không mở được dự án cũ: {exc}")
        self._sync_project_to_ui()

    def _sync_project_to_ui(self) -> None:
        pr = self.project
        self._loading = True
        try:
            self.name.set(pr.name if pr else "")
            self.topic.delete("1.0", "end")
            if pr:
                self.topic.insert("1.0", pr.topic)
        finally:
            self._loading = False
        self._refresh_project_status()
        if self.editor is not None and self.editor.winfo_exists():
            self.editor.set_project(pr)

    def _refresh_project_status(self) -> None:
        pr = self.project
        if pr is None:
            self.proj_status.info("Chưa mở dự án — bấm “Mới”.")
        else:
            self.proj_status.info(f"{pr.name} · {pr.progress_summary()}")

    def _require_project(self, create: bool = True) -> StoryProject | None:
        if self.project is None and create:
            self._new_project(silent=True)
        return self.project

    def _apply_settings_to_project(self, pr: StoryProject) -> None:
        cfg = self.state.story
        pr.topic = self.topic.get("1.0", "end").strip()
        pr.language, pr.length, pr.tone, pr.genre = cfg.language, cfg.length, cfg.tone, cfg.genre
        pr.competitor = cfg.competitor
        pr.master_prompt_preset, pr.master_prompt_custom = cfg.master_prompt_preset, cfg.master_prompt_custom
        pr.ratio = cfg.ratio

    def _new_project(self, silent: bool = False) -> None:
        name = self.name.get().strip() or "Câu chuyện mới"
        pr = StoryProject(name=name)
        self._apply_settings_to_project(pr)
        pr.save()
        self.project = pr
        self.state.story.last_project_path = pr.path
        self.state.config_changed("story")
        self._sync_project_to_ui()
        if not silent:
            self.window.log(f"Dự án mới: {pr.dir}")

    def _open_project(self) -> None:
        from app.services.story.project import PROJECTS_DIR

        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        p = filedialog.askopenfilename(title="Mở dự án Story", initialdir=str(PROJECTS_DIR),
                                       filetypes=[("Dự án", "project.json"), ("JSON", "*.json")])
        if not p:
            return
        try:
            self.project = StoryProject.load(p)
        except Exception as exc:
            messagebox.showerror("Edit Studio", f"Không mở được: {exc}")
            return
        self.state.story.last_project_path = self.project.path
        self.state.config_changed("story")
        self._sync_project_to_ui()

    def _save_project(self) -> None:
        pr = self._require_project()
        if pr:
            self._apply_settings_to_project(pr)
            pr.save()
            self._refresh_project_status()
            self.window.log(f"Đã lưu dự án: {pr.dir}")

    def _open_folder(self) -> None:
        if self.project:
            self.project.dir.mkdir(parents=True, exist_ok=True)
            open_path(str(self.project.dir))

    def _name_changed(self, v: str) -> None:
        if self.project and v.strip():
            self.project.name = v.strip()
            self.project.save()
            self._refresh_project_status()

    def _topic_changed(self) -> None:
        if self.project:
            self.project.topic = self.topic.get("1.0", "end").strip()

    # ── Các bước ─────────────────────────────────────────────────────────
    def _run_step(self, name: str, fn, on_done=None) -> None:
        pr = self._require_project()
        if pr is None:
            return
        self._apply_settings_to_project(pr)
        self.run_status.warn(f"{name}…")

        def work(cancel):
            return fn(pr, cancel)

        def done(result, error):
            if error:
                self.run_status.error(f"{name}: {error}")
            else:
                self.run_status.ok(f"{name}: xong.")
            self._refresh_project_status()
            if self.editor is not None and self.editor.winfo_exists():
                self.editor.set_project(pr)
            if on_done and not error:
                on_done(result)

        self.window.run_task(name, work, done)

    def _step_script(self) -> None:
        self._run_step("Viết kịch bản", lambda pr, c: self.engine.write_script(
            pr, on_progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=c),
            on_done=lambda _r: self.open_editor())

    def _step_voices(self) -> None:
        self._run_step("Tạo giọng", lambda pr, c: self.engine.generate_voices(
            pr, on_progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=c))

    def _step_images(self) -> None:
        self._run_step("Tạo ảnh", lambda pr, c: self.engine.generate_images(
            pr, on_progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=c))

    def _step_videos(self) -> None:
        if not messagebox.askyesno("Edit Studio", "Tạo clip Veo cho mọi cảnh chưa có video? Việc này tốn phí API và "
                                                  "khá lâu (mỗi clip ~1–3 phút)."):
            return
        self._run_step("Tạo video AI", lambda pr, c: self.engine.generate_videos(
            pr, on_progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=c))

    def _step_assemble(self) -> None:
        def after(result):
            if self.state.story.auto_queue:
                self._to_queue()

        self._run_step("Ghép video", lambda pr, c: self.engine.assemble(
            pr, on_progress=self.window.set_busy_progress, log=self.window.log_bg, cancel=c), on_done=after)

    def _step_metadata(self) -> None:
        self._run_step("Metadata", lambda pr, c: self.engine.metadata(pr, log=self.window.log_bg),
                       on_done=lambda _r: self.open_editor(tab="Metadata YouTube"))

    def _run_all(self) -> None:
        def chain(pr, cancel):
            log = self.window.log_bg
            self.engine.write_script(pr, on_progress=self.window.set_busy_progress, log=log, cancel=cancel)
            self.engine.generate_voices(pr, on_progress=self.window.set_busy_progress, log=log, cancel=cancel)
            self.engine.generate_images(pr, on_progress=self.window.set_busy_progress, log=log, cancel=cancel)
            if self.state.story.use_ai_video:
                self.engine.generate_videos(pr, on_progress=self.window.set_busy_progress, log=log, cancel=cancel)
            return self.engine.assemble(pr, on_progress=self.window.set_busy_progress, log=log, cancel=cancel)

        def after(result):
            if self.state.story.auto_queue:
                self._to_queue()

        self._run_step("Story: chạy tất cả", chain, on_done=after)

    def _to_queue(self) -> None:
        pr = self.project
        if pr is None or not pr.assembled_path or not Path(pr.assembled_path).exists():
            messagebox.showinfo("Edit Studio", "Chưa có video ghép — chạy bước 4 trước.")
            return
        added = self.state.add_videos([pr.assembled_path])
        items = added or [v for v in self.state.videos if v.path == pr.assembled_path]
        for it in items:
            it.srt_path = pr.srt_path if pr.srt_path and Path(pr.srt_path).exists() else None
        self.state.emit("videos_changed")
        self.state.set_active(self.state.videos.index(items[0]) if items else 0)
        self.state.emit("active_changed")
        self.window.log(f"Đã đưa {Path(pr.assembled_path).name} vào hàng đợi (kèm SRT).")

    def open_editor(self, tab: str | None = None) -> None:
        from app.ui.widgets.story_editor import StoryEditor

        pr = self._require_project()
        if self.editor is None or not self.editor.winfo_exists():
            self.editor = StoryEditor(self.window, self)
        self.editor.set_project(pr)
        self.editor.lift()
        self.editor.focus_force()
        if tab:
            self.editor.show_tab(tab)
