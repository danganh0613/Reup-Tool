"""Hộp thoại phụ cho Story: quản lý preset phong cách kênh."""

from __future__ import annotations

from collections.abc import Callable
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.core.state import TTS_LABELS, TTS_PROVIDERS, provider_or_default
from app.services.story.prompts import LANGUAGES, MASTER_PROMPTS, load_competitors, save_competitors
from app.ui.widgets.ui import button
from app.ui.widgets.common import ButtonRow, EntryRow, OptionRow, Section, pack_rows

_FIELDS = [
    ("genre_topic", "Thể loại / chủ đề"),
    ("target_audience", "Khán giả mục tiêu"),
    ("character_lead_1", "Nhân vật chính 1"),
    ("character_lead_2", "Nhân vật chính 2"),
    ("character_antagonist", "Đối thủ / xung đột"),
    ("setting_location", "Bối cảnh"),
    ("bgm_style", "Phong cách nhạc nền"),
    ("voice_id", "Voice ID mặc định"),
    ("url", "Link kênh tham khảo"),
]
_PROMPT_LABELS = {v[0]: k for k, v in MASTER_PROMPTS.items()}


class CompetitorDialog(ctk.CTkToplevel):
    def __init__(self, window, on_close: Callable[[], None] | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.on_close = on_close
        self.data = load_competitors()
        self.current: str | None = None
        self.title("Phong cách kênh — Edit Studio")
        self.geometry("720x760")
        self.minsize(600, 600)
        self.transient(window)
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        body.grid_columnconfigure(0, weight=1)

        sec = Section(body, "Preset", subtitle="mô tả kênh mẫu để AI viết theo đúng gu")
        sec.pack(fill="x", pady=4)
        self.picker = OptionRow(sec.body, "Chọn preset", list(self.data) or ["(trống)"], on_change=self._pick)
        self.picker.pack(fill="x", pady=3)
        ButtonRow(sec.body, [("＋ Mới", self._new, T.primary_button()), ("✕ Xoá", self._delete, T.danger_button())]).pack(
            fill="x", pady=(2, 0))

        sec = Section(body, "Thông tin")
        sec.pack(fill="x", pady=4)
        self.name = EntryRow(sec.body, "Tên preset")
        self.language = OptionRow(sec.body, "Ngôn ngữ", LANGUAGES)
        self.style = OptionRow(sec.body, "Phong cách ảnh", [v[0] for v in MASTER_PROMPTS.values()])
        self.style_custom = EntryRow(sec.body, "Prompt thêm")
        self.provider = OptionRow(sec.body, "Engine giọng", TTS_PROVIDERS, labels=TTS_LABELS)
        self.speed = EntryRow(sec.body, "Tốc độ giọng", placeholder="0.95")
        rows = [self.name, self.language, self.style, self.style_custom, self.provider, self.speed]
        self.fields: dict[str, EntryRow] = {}
        for key, label in _FIELDS:
            self.fields[key] = EntryRow(sec.body, label)
            rows.append(self.fields[key])
        pack_rows(sec.body, rows)
        ctk.CTkLabel(sec.body, text="Ghi chú cho AI (nhịp kể, cấu trúc, điều cần tránh…)", font=T.font(11),
                     text_color=T.TEXT_MUTED, anchor="w").pack(fill="x", pady=(4, 0))
        self.notes = ctk.CTkTextbox(sec.body, height=90, font=T.font(12), wrap="word")
        self.notes.pack(fill="x", pady=(2, 0))

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        button(foot, "Lưu preset", icon_name="save", variant="primary", command=self._save).pack(side="right")
        ctk.CTkButton(foot, text="Đóng", width=90, height=36, command=self._close, **T.secondary_button()).pack(
            side="right", padx=(0, 6))
        self.protocol("WM_DELETE_WINDOW", self._close)
        if self.data:
            self._pick(next(iter(self.data)))

    def _pick(self, name: str) -> None:
        c = self.data.get(name)
        if not c:
            return
        self.current = name
        self.name.set(c.get("name", name))
        self.language.set(c.get("language", LANGUAGES[0]) if c.get("language") in LANGUAGES else LANGUAGES[0])
        self.style.set(MASTER_PROMPTS.get(c.get("master_prompt_preset", ""), MASTER_PROMPTS["cinematic_hyperrealism"])[0])
        self.style_custom.set(c.get("master_prompt_custom", ""))
        self.provider.set(provider_or_default(c.get("voice_provider", "Edge TTS")))
        self.speed.set(str(c.get("voice_speed", 1.0)))
        for key, _ in _FIELDS:
            self.fields[key].set(str(c.get(key, "")))
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", c.get("notes", ""))

    def _collect(self) -> dict:
        try:
            speed = float(self.speed.get().replace(",", ".") or 1.0)
        except ValueError:
            speed = 1.0
        d = {"name": self.name.get().strip() or "Preset", "language": self.language.get(),
             "master_prompt_preset": _PROMPT_LABELS.get(self.style.get(), "cinematic_hyperrealism"),
             "master_prompt_custom": self.style_custom.get().strip(), "voice_provider": self.provider.get(),
             "voice_speed": speed, "notes": self.notes.get("1.0", "end").strip()}
        for key, _ in _FIELDS:
            d[key] = self.fields[key].get().strip()
        return d

    def _new(self) -> None:
        self.current = None
        self.name.set("Kênh mới")
        for key, _ in _FIELDS:
            self.fields[key].set("")
        self.notes.delete("1.0", "end")

    def _save(self) -> None:
        d = self._collect()
        if self.current and self.current != d["name"]:
            self.data.pop(self.current, None)
        self.data[d["name"]] = d
        save_competitors(self.data)
        self.current = d["name"]
        self.picker.set_values(list(self.data), keep=False)
        self.picker.set(d["name"])
        self.window.log(f"Đã lưu phong cách kênh “{d['name']}”.")

    def _delete(self) -> None:
        if not self.current or not messagebox.askyesno("Xoá", f"Xoá preset “{self.current}”?"):
            return
        self.data.pop(self.current, None)
        save_competitors(self.data)
        self.picker.set_values(list(self.data) or ["(trống)"], keep=False)
        self.current = None
        if self.data:
            self._pick(next(iter(self.data)))

    def _close(self) -> None:
        if self.on_close:
            self.on_close()
        self.destroy()
