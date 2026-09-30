"""Cửa sổ soạn kịch bản Story: danh sách cảnh · chi tiết cảnh · phân vai · metadata YouTube."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image

from app.config import theme as T
from app.services.story.ai import SFX_LIST
from app.services.story.project import Character, Scene, StoryProject
from app.ui.widgets.ui import button
from app.ui.widgets.common import ButtonRow, ComboRow, EntryRow, OptionRow, SliderRow, StatusLabel, hint
from app.ui.widgets.voice_picker import VoicePicker
from app.core.platform import open_path

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v")


class StoryEditor(ctk.CTkToplevel):
    def __init__(self, window, module) -> None:
        super().__init__(window)
        self.window = window
        self.module = module
        self.project: StoryProject | None = None
        self.scene: Scene | None = None
        self._rows: list[ctk.CTkButton] = []
        self._row_state: list[tuple[str, bool]] = []     # (chữ, đang chọn) đã vẽ của từng dòng → chỉ configure khi đổi
        self._preview_img: ctk.CTkImage | None = None
        self.title("Trình soạn kịch bản — Edit Studio")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1400, sw - 80)}x{min(900, sh - 120)}+40+40")
        self.minsize(1000, 640)
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(self, fg_color=T.BG_PANEL, segmented_button_selected_color=T.ACCENT,
                                   segmented_button_selected_hover_color=T.ACCENT_HOVER, text_color=T.TEXT)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=(6, 4))
        self._build_scenes_tab(self.tabs.add("Cảnh"))
        self._build_casting_tab(self.tabs.add("Nhân vật & giọng"))
        self._build_meta_tab(self.tabs.add("Metadata YouTube"))
        self.tabs.set("Cảnh")

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.status = StatusLabel(foot, "")
        self.status.pack(side="left", fill="x", expand=True)
        button(foot, "Lưu dự án", icon_name="save", variant="primary", size="sm", command=self._save).pack(side="right")

    def show_tab(self, name: str) -> None:
        try:
            self.tabs.set(name)
        except Exception:
            pass

    # ── Tab Cảnh ─────────────────────────────────────────────────────────
    def _build_scenes_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(tab, fg_color="transparent", width=300)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        left.grid_propagate(False)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        self.summary = ctk.CTkLabel(left, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w", wraplength=280)
        self.summary.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.list = ctk.CTkScrollableFrame(left, fg_color=T.BG_CARD, corner_radius=8)
        self.list.grid(row=1, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)
        ButtonRow(left, [("＋ Cảnh", self._add_scene, T.primary_button()), ("↑", self._move_up, None),
                         ("↓", self._move_down, None), ("✕", self._delete_scene, T.danger_button())]).grid(
            row=2, column=0, sticky="ew", pady=(6, 0))

        right = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        self.scene_title = ctk.CTkLabel(right, text="Chọn một cảnh", font=T.font(15, "bold"), anchor="w")
        self.scene_title.pack(fill="x", pady=(0, 4))
        row = ctk.CTkFrame(right, fg_color="transparent")
        row.pack(fill="x")
        self.speaker = OptionRow(row, "Người nói", ["Người kể"], on_change=lambda v: self._set("speaker", v))
        self.speaker.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.sfx = ComboRow(row, "SFX", SFX_LIST, on_change=lambda v: self._set("sfx", v))
        self.sfx.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(right, text="Lời kể", font=T.font(12, "bold"), anchor="w").pack(fill="x", pady=(8, 0))
        self.narration = ctk.CTkTextbox(right, height=150, font=T.font(13), wrap="word")
        self.narration.pack(fill="x", pady=(2, 0))
        self.narration.bind("<FocusOut>", lambda _e: self._text_changed())
        ctk.CTkLabel(right, text="Prompt ảnh (tiếng Anh)", font=T.font(12, "bold"), anchor="w").pack(fill="x", pady=(8, 0))
        self.prompt = ctk.CTkTextbox(right, height=80, font=T.font(12), wrap="word")
        self.prompt.pack(fill="x", pady=(2, 0))
        self.prompt.bind("<FocusOut>", lambda _e: self._text_changed())

        rewrite = ctk.CTkFrame(right, fg_color="transparent")
        rewrite.pack(fill="x", pady=(8, 0))
        self.instruction = EntryRow(rewrite, "", placeholder="Yêu cầu viết lại: vd 'ngắn hơn, thêm kịch tính'…", label_width=0)
        self.instruction.pack(side="left", fill="x", expand=True, padx=(0, 6))
        button(rewrite, "Viết lại (AI)", icon_name="sparkle", size="sm", command=self._rewrite).pack(side="left")

        media = ctk.CTkFrame(right, fg_color=T.BG_CARD, corner_radius=8)
        media.pack(fill="x", pady=(10, 0))
        self.preview = ctk.CTkLabel(media, text="Chưa có hình", text_color=T.TEXT_MUTED, height=280)
        self.preview.pack(fill="x", padx=8, pady=8)
        self.media_info = ctk.CTkLabel(media, text="", font=T.font(11), text_color=T.TEXT_MUTED, anchor="w")
        self.media_info.pack(fill="x", padx=8)
        ButtonRow(media, [
            ("🖼 Tạo ảnh (AI)", self._gen_image, T.primary_button()),
            ("📂 Chọn ảnh/video…", self._pick_media, None),
            ("🎬 Tạo clip AI (Veo)", self._gen_video, None),
            ("🗑 Bỏ clip", self._clear_video, None),
        ]).pack(fill="x", padx=8, pady=(4, 8))
        ButtonRow(right, [
            ("🔊 Tạo giọng cảnh này", self._gen_voice, T.primary_button()),
            ("▶ Nghe", self._play_voice, None),
            ("📁 Mở thư mục dự án", self._open_dir, None),
        ]).pack(fill="x", pady=(8, 0))
        hint(right, "Sửa lời kể xong hãy bấm “Tạo giọng cảnh này” để giọng khớp với chữ. "
                    "Ảnh/clip có thể thay bằng file của bạn.").pack(fill="x", pady=(4, 0))

    # ── Tab Phân vai ─────────────────────────────────────────────────────
    def _build_casting_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(top, text="Mỗi nhân vật có thể dùng giọng riêng; cảnh nào có “Người nói” là nhân vật sẽ dùng giọng đó.",
                     font=T.font(11), text_color=T.TEXT_MUTED, anchor="w").pack(side="left", fill="x", expand=True)
        button(top, "Thêm nhân vật", icon_name="add", variant="primary", size="sm", command=self._add_character).pack(side="right")
        self.cast_list = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.cast_list.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.cast_list.grid_columnconfigure(0, weight=1)

    def _rebuild_casting(self) -> None:
        for w in self.cast_list.winfo_children():
            w.destroy()
        pr = self.project
        if pr is None:
            return
        for ch in pr.characters:
            self._character_card(ch)

    def _character_card(self, ch: Character) -> None:
        card = ctk.CTkFrame(self.cast_list, fg_color=T.BG_CARD, corner_radius=8)
        card.pack(fill="x", pady=4)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 0))
        name = EntryRow(head, "Tên", value=ch.name, on_change=lambda v: self._rename_character(ch, v), label_width=50)
        name.pack(side="left", fill="x", expand=True, padx=(0, 6))
        button(head, "", icon_name="close", variant="ghost", size="sm", command=lambda: self._remove_character(ch), icon_color=T.TEXT_DIM,
               tip="Xoá nhân vật").pack(side="right")
        desc = EntryRow(card, "Mô tả", value=ch.description, on_change=lambda v: setattr(ch, "description", v),
                        placeholder="ngoại hình, tuổi, tính cách (dùng cho prompt ảnh)")
        desc.pack(fill="x", padx=10, pady=4)

        def get_sel(c=ch):
            return (c.voice_provider or "Edge TTS",
                    "-".join(c.voice_id.split("-")[:2]) if c.voice_id.count("-") >= 2 else self.window.app_state.story.voice_language,
                    c.voice_id)

        def set_sel(provider, locale, vid, vname, c=ch):
            c.voice_provider, c.voice_id, c.voice_name = provider, vid, vname

        picker = VoicePicker(card, self.window, get=get_sel, set_=set_sel, speed=lambda c=ch: c.speed)
        picker.pack(fill="x", padx=10)
        SliderRow(card, "Tốc độ", from_=0.5, to=2.0, step=0.05, fmt="{:.2f}", suffix="×", value=ch.speed,
                  on_change=lambda v, c=ch: setattr(c, "speed", float(v))).pack(fill="x", padx=10, pady=(2, 8))

    def _add_character(self) -> None:
        if self.project is None:
            return
        self.project.characters.append(Character(name=f"Nhân vật {len(self.project.characters) + 1}"))
        self._rebuild_casting()
        self._refresh_speakers()

    def _remove_character(self, ch: Character) -> None:
        if self.project and ch in self.project.characters:
            self.project.characters.remove(ch)
            self._rebuild_casting()
            self._refresh_speakers()

    def _rename_character(self, ch: Character, name: str) -> None:
        old = ch.name
        ch.name = name.strip() or old
        if self.project:
            for sc in self.project.scenes:
                if sc.speaker == old:
                    sc.speaker = ch.name
        self._refresh_speakers()

    # ── Tab Metadata ─────────────────────────────────────────────────────
    def _build_meta_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self.title_pick = OptionRow(body, "Gợi ý tiêu đề", ["(chưa tạo)"], on_change=self._pick_title)
        self.title_pick.pack(fill="x", pady=3)
        self.title_entry = EntryRow(body, "Tiêu đề", on_change=lambda v: self._meta_set("title", v))
        self.title_entry.pack(fill="x", pady=3)
        ctk.CTkLabel(body, text="Mô tả", font=T.font(12, "bold"), anchor="w").pack(fill="x", pady=(6, 0))
        self.desc = ctk.CTkTextbox(body, height=160, font=T.font(12), wrap="word")
        self.desc.pack(fill="x")
        self.desc.bind("<FocusOut>", lambda _e: self._meta_set("description", self.desc.get("1.0", "end").strip()))
        self.tags = EntryRow(body, "Tags", placeholder="cách nhau bằng dấu phẩy",
                             on_change=lambda v: self._meta_set("tags", [t.strip() for t in v.split(",") if t.strip()]))
        self.tags.pack(fill="x", pady=3)
        ctk.CTkLabel(body, text="Chapters", font=T.font(12, "bold"), anchor="w").pack(fill="x", pady=(6, 0))
        self.chapters = ctk.CTkTextbox(body, height=120, font=T.mono(12), wrap="none")
        self.chapters.pack(fill="x")
        self.chapters.bind("<FocusOut>", lambda _e: self._meta_set("chapters", self.chapters.get("1.0", "end").strip()))
        self.thumb_prompt = EntryRow(body, "Prompt thumbnail", on_change=lambda v: self._meta_set("thumbnail_prompt", v))
        self.thumb_prompt.pack(fill="x", pady=3)
        ButtonRow(body, [
            ("✨ Tạo metadata (AI)", self.module._step_metadata, T.primary_button()),
            ("🖼 Tạo thumbnail (AI)", self._gen_thumbnail, None),
            ("📋 Copy tiêu đề + mô tả", self._copy_meta, None),
        ]).pack(fill="x", pady=(8, 0))
        self.thumb_preview = ctk.CTkLabel(body, text="", height=10)
        self.thumb_preview.pack(fill="x", pady=(8, 0))
        self._titles: list[str] = []

    def _pick_title(self, v: str) -> None:
        if self.project and v and v != "(chưa tạo)":
            self.project.title = v
            self.title_entry.set(v)

    def _meta_set(self, key: str, value) -> None:
        if self.project is not None:
            setattr(self.project, key, value)

    def _fill_meta(self) -> None:
        pr = self.project
        if pr is None:
            return
        self.title_entry.set(pr.title)
        self.desc.delete("1.0", "end")
        self.desc.insert("1.0", pr.description)
        self.tags.set(", ".join(pr.tags))
        self.chapters.delete("1.0", "end")
        self.chapters.insert("1.0", pr.chapters)
        self.thumb_prompt.set(pr.thumbnail_prompt)
        thumb = pr.dir / "images" / "thumbnail.png"
        if thumb.exists():
            self._show_thumb(thumb)

    def _show_thumb(self, path: Path) -> None:
        try:
            img = Image.open(path)
            img.thumbnail((480, 270))
            self._thumb_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.thumb_preview.configure(image=self._thumb_img, text="")
        except Exception:
            pass

    def _gen_thumbnail(self) -> None:
        pr = self.project
        if pr is None:
            return
        self.status.warn("Đang tạo thumbnail…")

        def done(result, error):
            if error:
                self.status.error(f"Thumbnail: {error}")
            else:
                self.status.ok("Thumbnail xong.")
                self._show_thumb(Path(result))

        self.window.run_task("Thumbnail", lambda c: self.module.engine.thumbnail(pr, log=self.window.log_bg), done)

    def _copy_meta(self) -> None:
        pr = self.project
        if pr is None:
            return
        text = f"{pr.title}\n\n{pr.description}\n\n{pr.chapters}\n\nTags: {', '.join(pr.tags)}"
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.ok("Đã copy vào clipboard.")

    # ── Dự án ────────────────────────────────────────────────────────────
    def set_project(self, pr: StoryProject | None) -> None:
        self.project = pr
        self.title(f"Trình soạn kịch bản — {pr.name if pr else 'Edit Studio'}")
        self._rebuild_list()
        self._rebuild_casting()
        self._fill_meta()
        titles = getattr(pr, "_titles", None) if pr else None
        if pr and pr.title:
            self.title_pick.set_values([pr.title] + [t for t in (titles or []) if t != pr.title], keep=False)
        if pr and pr.scenes:
            self.select_scene(0 if self.scene not in pr.scenes else pr.scenes.index(self.scene))
        else:
            self.scene = None
            self._fill_scene()

    @staticmethod
    def _row_text(i: int, sc: Scene) -> str:
        flags = ("🔊" if sc.audio_path and Path(sc.audio_path).exists() else "·") + \
                ("🎬" if sc.video_path and Path(sc.video_path).exists() else
                 ("🖼" if sc.image_path and Path(sc.image_path).exists() else "·"))
        return f"{i + 1:>3} {flags} {sc.narration[:38]}{'…' if len(sc.narration) > 38 else ''}"

    def _set_row(self, i: int, text: str, active: bool) -> None:
        if self._row_state[i] != (text, active):
            self._row_state[i] = (text, active)
            self._rows[i].configure(text=text, fg_color=T.ACCENT_SOFT if active else "transparent")

    def _rebuild_list(self) -> None:
        """Dùng lại các nút đã có (chỉ đổi chữ/màu dòng thay đổi), chỉ tạo/xoá phần chênh lệch số cảnh."""
        pr = self.project
        scenes = pr.scenes if pr is not None else []
        for b in self._rows[len(scenes):]:
            b.destroy()
        del self._rows[len(scenes):]
        del self._row_state[len(scenes):]
        if pr is None:
            self.summary.configure(text="Chưa có dự án.")
            return
        self.summary.configure(text=pr.progress_summary())
        for i, sc in enumerate(scenes):
            text, active = self._row_text(i, sc), sc is self.scene
            if i < len(self._rows):
                self._set_row(i, text, active)
                continue
            b = ctk.CTkButton(self.list, text=text, anchor="w", height=32, font=T.font(11),
                              fg_color=T.ACCENT_SOFT if active else "transparent", hover_color=T.BG_HOVER,
                              text_color=T.TEXT, command=lambda i=i: self.select_scene(i))
            b.grid(row=i, column=0, sticky="ew", pady=1)
            self._rows.append(b)
            self._row_state.append((text, active))

    def _refresh_row(self, sc: Scene) -> None:
        """Sửa một cảnh → chỉ cập nhật dòng của cảnh đó (+ dòng tóm tắt tiến độ)."""
        pr = self.project
        if pr is None or sc not in pr.scenes:
            return
        self.summary.configure(text=pr.progress_summary())
        i = pr.scenes.index(sc)
        if i < len(self._rows):
            self._set_row(i, self._row_text(i, sc), sc is self.scene)
        else:
            self._rebuild_list()

    def _refresh_speakers(self) -> None:
        if self.project:
            self.speaker.set_values(self.project.speakers())
            if self.scene:
                self.speaker.set(self.scene.speaker)

    def select_scene(self, index: int) -> None:
        pr = self.project
        if pr is None or not (0 <= index < len(pr.scenes)):
            return
        self._text_changed()
        self.scene = pr.scenes[index]
        for i in range(len(self._rows)):
            text, active = self._row_state[i]
            if active != (i == index):
                self._set_row(i, text, i == index)
        self._fill_scene()

    def _fill_scene(self) -> None:
        sc = self.scene
        self._refresh_speakers()
        self.narration.delete("1.0", "end")
        self.prompt.delete("1.0", "end")
        if sc is None:
            self.scene_title.configure(text="Chọn một cảnh")
            self.preview.configure(image=None, text="Chưa có hình")
            self.media_info.configure(text="")
            return
        idx = self.project.scenes.index(sc) if self.project else 0
        self.scene_title.configure(text=f"Cảnh {idx + 1} / {len(self.project.scenes) if self.project else 0}")
        self.speaker.set(sc.speaker)
        self.sfx.set(sc.sfx)
        self.narration.insert("1.0", sc.narration)
        self.prompt.insert("1.0", sc.image_prompt)
        self._show_media(sc)

    def _show_media(self, sc: Scene) -> None:
        info = []
        if sc.audio_path and Path(sc.audio_path).exists():
            info.append(f"giọng {sc.duration:.1f}s")
        if sc.video_path and Path(sc.video_path).exists():
            info.append(f"clip: {Path(sc.video_path).name}")
        if sc.image_path and Path(sc.image_path).exists():
            info.append(f"ảnh: {Path(sc.image_path).name}")
        self.media_info.configure(text=" · ".join(info) if info else "Chưa có giọng / hình.")
        src = sc.video_path if sc.video_path and Path(sc.video_path).exists() else sc.image_path
        if not src or not Path(src).exists():
            self.preview.configure(image=None, text="Chưa có hình")
            return

        def work():
            if Path(src).suffix.lower() in VIDEO_EXTS:
                return self.window.ffmpeg.extract_frame(src, 0.5, max_width=640)
            img = Image.open(src).convert("RGB")
            img.thumbnail((640, 360))
            return img

        def done(result, error):
            if error or result is None or self.scene is not sc:
                return
            img = result
            img.thumbnail((640, 280))
            self._preview_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.preview.configure(image=self._preview_img, text="")

        self.window.run_bg(work, done)

    # ── Sửa cảnh ─────────────────────────────────────────────────────────
    def _set(self, key: str, value) -> None:
        if self.scene is not None and getattr(self.scene, key) != value:
            setattr(self.scene, key, value)
            if key == "speaker":
                self.scene.audio_path = ""
            self._refresh_row(self.scene)

    def _text_changed(self) -> None:
        sc = self.scene
        if sc is None:
            return
        narration = self.narration.get("1.0", "end").strip()
        prompt = self.prompt.get("1.0", "end").strip()
        changed = False
        if narration and narration != sc.narration:
            sc.narration = narration
            sc.audio_path = ""       # giọng cũ không còn khớp
            changed = True
        if prompt != sc.image_prompt:
            sc.image_prompt = prompt
            changed = True
        if changed:
            self._refresh_row(sc)

    def _add_scene(self) -> None:
        pr = self.project
        if pr is None:
            return
        idx = pr.scenes.index(self.scene) + 1 if self.scene in pr.scenes else len(pr.scenes)
        pr.scenes.insert(idx, Scene(narration="(lời kể mới)", speaker="Người kể"))
        self._rebuild_list()
        self.select_scene(idx)

    def _delete_scene(self) -> None:
        pr = self.project
        if pr is None or self.scene is None:
            return
        idx = pr.scenes.index(self.scene)
        pr.scenes.remove(self.scene)
        self.scene = None
        self._rebuild_list()
        if pr.scenes:
            self.select_scene(min(idx, len(pr.scenes) - 1))
        else:
            self._fill_scene()

    def _move(self, delta: int) -> None:
        pr = self.project
        if pr is None or self.scene is None:
            return
        i = pr.scenes.index(self.scene)
        j = i + delta
        if 0 <= j < len(pr.scenes):
            pr.scenes[i], pr.scenes[j] = pr.scenes[j], pr.scenes[i]
            self._rebuild_list()
            self.select_scene(j)

    def _move_up(self) -> None:
        self._move(-1)

    def _move_down(self) -> None:
        self._move(1)

    # ── Hành động AI / media ─────────────────────────────────────────────
    def _task(self, name: str, fn, refresh_media: bool = True) -> None:
        pr, sc = self.project, self.scene
        if pr is None or sc is None:
            return
        self._text_changed()
        self.status.warn(f"{name}…")

        def done(result, error):
            if error:
                self.status.error(f"{name}: {error}")
            else:
                self.status.ok(f"{name}: xong.")
            pr.save()
            self._rebuild_list()
            if self.scene is sc:
                self._fill_scene()
            self.module._refresh_project_status()

        self.window.run_task(name, lambda c: fn(pr, sc, c), done)

    def _gen_image(self) -> None:
        self._task("Tạo ảnh", lambda pr, sc, c: self.module.engine.generate_images(
            pr, only_missing=False, scenes=[sc], log=self.window.log_bg, cancel=c))

    def _gen_video(self) -> None:
        self._task("Tạo clip AI", lambda pr, sc, c: self.module.engine.generate_videos(
            pr, scenes=[sc], log=self.window.log_bg, cancel=c))

    def _gen_voice(self) -> None:
        self._task("Tạo giọng", lambda pr, sc, c: self.module.engine.generate_voices(
            pr, only_missing=False, scenes=[sc], log=self.window.log_bg, cancel=c))

    def _rewrite(self) -> None:
        instr = self.instruction.get().strip()
        self._task("Viết lại cảnh", lambda pr, sc, c: self.module.engine.rewrite_scene(pr, sc, instr))

    def _pick_media(self) -> None:
        sc = self.scene
        if sc is None:
            return
        p = filedialog.askopenfilename(title="Chọn ảnh hoặc video cho cảnh",
                                       filetypes=[("Ảnh/Video", "*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.mkv *.webm"),
                                                  ("Tất cả", "*.*")])
        if not p:
            return
        if Path(p).suffix.lower() in VIDEO_EXTS:
            sc.video_path = p
        else:
            sc.image_path = p
            sc.video_path = ""
        self._rebuild_list()
        self._show_media(sc)

    def _clear_video(self) -> None:
        if self.scene:
            self.scene.video_path = ""
            self._rebuild_list()
            self._show_media(self.scene)

    def _play_voice(self) -> None:
        sc = self.scene
        if sc and sc.audio_path and Path(sc.audio_path).exists():
            try:
                self.window.player.play(sc.audio_path)
            except Exception as exc:
                self.status.error(str(exc))
        else:
            self.status.warn("Cảnh chưa có giọng.")

    def _open_dir(self) -> None:
        if self.project:
            self.project.dir.mkdir(parents=True, exist_ok=True)
            open_path(str(self.project.dir))

    def _save(self) -> None:
        self._text_changed()
        if self.project:
            self.project.save()
            self.status.ok(f"Đã lưu {self.project.dir / 'project.json'}")
            self.module._refresh_project_status()
