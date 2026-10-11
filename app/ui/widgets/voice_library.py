"""Thư viện voice đã tạo / quản lý dự án TTS: danh sách track trong output/voice kèm thông tin nguồn."""

from __future__ import annotations

import json
import time
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import button
from app.config.paths import VOICE_DIR
from app.services.srt_utils import format_ts
from app.core.platform import open_path

SIDE_FILES = (".json", ".srt", ".cues.csv", ".words.json")    # file đi kèm track: ghi chú · phụ đề dời · báo cáo · mốc chữ


def list_voices() -> list[dict]:
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in VOICE_DIR.glob("*.wav"):
        if p.name.endswith(".part.wav"):
            continue
        meta: dict = {}
        mp = p.with_suffix(".json")
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        out.append({"path": p, "meta": meta, "mtime": p.stat().st_mtime, "size": p.stat().st_size})
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


class VoiceLibraryDialog(ctk.CTkToplevel):
    def __init__(self, window, module) -> None:
        super().__init__(window)
        self.window = window
        self.module = module
        self.selected: dict | None = None
        self.title("Giọng đã tạo")
        self.geometry("1000x740")
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=8)
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        ctk.CTkLabel(head, text="Chọn một giọng đã tạo để dùng lại", font=T.font(14, "bold"), anchor="w").pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(head, text="Xuất video sẽ dùng thẳng file giọng này, không đọc lại — để đổi nhạc, phụ đề hay hiệu ứng "
                                "mà giữ nguyên giọng.", font=T.font(11), text_color=T.TEXT_DIM, anchor="w", wraplength=940,
                     justify="left").pack(fill="x", padx=12, pady=(2, 4))
        self.use_srt = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(head, text="Dùng luôn bản SRT đã tạo ra giọng này (tránh lệch phụ đề)", variable=self.use_srt,
                        font=T.font(12)).pack(anchor="w", padx=12, pady=(0, 10))

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew", padx=10)
        self.rows: list[ctk.CTkFrame] = []

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        self.status = ctk.CTkLabel(foot, text="", font=T.font(11), text_color=T.TEXT_MUTED)
        self.status.pack(side="left")
        ctk.CTkButton(foot, text="Đóng", width=90, height=32, command=self.destroy, **T.secondary_button()).pack(side="right")
        self.use_btn = ctk.CTkButton(foot, text="Dùng giọng này", width=130, height=32, command=self._use, **T.primary_button())
        self.use_btn.pack(side="right", padx=6)
        ctk.CTkButton(foot, text="Mở thư mục", width=100, height=32, command=lambda: open_path(str(VOICE_DIR)), **T.secondary_button()).pack(side="right", padx=6)
        button(foot, "Nghe", icon_name="play", size="sm", command=self._play).pack(side="right", padx=6)
        ctk.CTkButton(foot, text="Xoá tất cả…", width=100, height=32, command=self._delete_all, **T.danger_button()).pack(side="right", padx=6)
        button(foot, "Xoá giọng này", icon_name="trash", variant="destructive", size="sm", command=self._delete).pack(side="right", padx=6)
        self.refresh()

    def refresh(self) -> None:
        for r in self.rows:
            r.destroy()
        self.rows.clear()
        items = list_voices()
        self.status.configure(text=f"{len(items)} track" if items else "Lịch sử trống.")
        if not items:
            ctk.CTkLabel(self.list, text="Chưa có giọng nào.\nXuất / Chuẩn bị một video có bật lồng tiếng, hoặc bấm "
                                         "«Tạo giọng cho video này» (tab Lồng tiếng) là giọng sẽ hiện ở đây.",
                         font=T.font(12), text_color=T.TEXT_DIM, justify="left").pack(pady=30)
            return
        for it in items:
            m = it["meta"]
            card = ctk.CTkFrame(self.list, fg_color=T.BG_CARD, corner_radius=8, border_width=1, border_color=T.BORDER_SOFT)
            card.pack(fill="x", pady=3)
            card.bind("<Button-1>", lambda _e, i=it, c=card: self._select(i, c))
            info = [f"{m.get('provider', '?')} · {m.get('voice', '')}"]
            if m.get("duration"):
                info.append(f"{format_ts(float(m['duration']), '.')[:8]}")
            if m.get("cues"):
                info.append(f"{m['cues']} cue")
            info.append(time.strftime("%d/%m %H:%M", time.localtime(it["mtime"])))
            info.append(f"{it['size'] / 1_048_576:.1f} MB")
            ctk.CTkLabel(card, text=it["path"].name, font=T.font(12, "bold"), anchor="w").pack(fill="x", padx=10, pady=(8, 0))
            src = Path(m.get("video", "")).name if m.get("video") else "?"
            ctk.CTkLabel(card, text=f"Video: {src} · " + " · ".join(info), font=T.font(11), text_color=T.TEXT_DIM,
                         anchor="w").pack(fill="x", padx=10, pady=(0, 8))
            for w in card.winfo_children():
                w.bind("<Button-1>", lambda _e, i=it, c=card: self._select(i, c))
            self.rows.append(card)

    def _select(self, it: dict, card) -> None:
        self.selected = it
        for r in self.rows:
            r.configure(border_color=T.ACCENT if r is card else T.BORDER_SOFT, border_width=2 if r is card else 1)

    def _use(self) -> None:
        if not self.selected:
            messagebox.showinfo("Edit Studio", "Chọn một giọng trong danh sách.")
            return
        self.module.use_voice(str(self.selected["path"]), use_srt=self.use_srt.get())
        self.destroy()

    def _play(self) -> None:
        if self.selected:
            try:
                self.window.player.play(self.selected["path"])
            except Exception as exc:
                self.status.configure(text=str(exc))

    def _delete(self) -> None:
        if not self.selected:
            return
        p = self.selected["path"]
        if messagebox.askyesno("Xoá giọng", f"Xoá {p.name}?"):
            for f in (p, *(p.with_suffix(x) for x in SIDE_FILES)):
                try:
                    f.unlink()
                except OSError:
                    pass
            self.selected = None
            self.refresh()

    def _delete_all(self) -> None:
        if messagebox.askyesno("Xoá tất cả", "Xoá toàn bộ giọng đã tạo trong output/voice?"):
            for it in list_voices():
                for f in (it["path"], *(it["path"].with_suffix(x) for x in SIDE_FILES)):
                    try:
                        f.unlink()
                    except OSError:
                        pass
            self.refresh()
