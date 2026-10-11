"""Thư viện nhạc nền: file trong app/assets/music (bạn tự bỏ nhạc có quyền dùng vào)."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from app.config import theme as T
from app.ui.widgets.ui import button
from app.ui.icons import EMOJI_ICONS, icon
from app.config.paths import ASSETS_DIR
from app.core.platform import open_path

MUSIC_LIB = ASSETS_DIR / "music"
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}


class MusicLibraryDialog(ctk.CTkToplevel):
    def __init__(self, window, module) -> None:
        super().__init__(window)
        self.window = window
        self.module = module
        self.title("Thư viện nhạc nền")
        self.geometry("900x640")
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=8)
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=" Thư viện nhạc nền", image=icon(EMOJI_ICONS["🎵"], 14, dy=1), compound="left", font=T.font(15, "bold"), anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        self.search = ctk.CTkEntry(head, placeholder_text="Tìm…", width=220, height=32)
        self.search.grid(row=0, column=1, padx=12, pady=(10, 0))
        self.search.bind("<KeyRelease>", lambda _e: self.refresh())
        ctk.CTkLabel(head, text=f"Bỏ file nhạc bạn có quyền dùng vào {MUSIC_LIB} — Nghe thử & 1-click thêm vào video",
                     font=T.font(11), text_color=T.TEXT_DIM, anchor="w").grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 10))
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew", padx=10)
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        self.count = ctk.CTkLabel(foot, text="", font=T.font(11), text_color=T.TEXT_MUTED)
        self.count.pack(side="left")
        ctk.CTkButton(foot, text="Đóng", width=90, height=32, command=self.destroy, **T.secondary_button()).pack(side="right")
        button(foot, "Dừng nghe", icon_name="stop", size="sm", command=self._stop).pack(side="right", padx=6)
        ctk.CTkButton(foot, text="Làm mới", width=90, height=32, command=self.refresh, **T.secondary_button()).pack(side="right", padx=6)
        button(foot, "Mở thư mục nhạc", icon_name="folder", size="sm", command=self._open_dir).pack(side="right", padx=6)
        self.refresh()

    def refresh(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        MUSIC_LIB.mkdir(parents=True, exist_ok=True)
        q = self.search.get().strip().lower()
        files = sorted([p for p in MUSIC_LIB.rglob("*") if p.suffix.lower() in AUDIO_EXTS and (not q or q in p.name.lower())],
                       key=lambda p: p.name.lower())
        self.count.configure(text=f"Có {len(files)} bài nhạc trong thư viện")
        if not files:
            ctk.CTkLabel(self.list, text="Thư viện trống. Bấm «Mở thư mục nhạc» và bỏ file .mp3/.wav của bạn vào.",
                         font=T.font(12), text_color=T.TEXT_DIM).pack(pady=30)
            return
        have = {str(Path(c.get("path", ""))).lower() for c in self.module._clips() if isinstance(c, dict)}
        for p in files:
            card = ctk.CTkFrame(self.list, fg_color=T.BG_CARD, corner_radius=8)
            card.pack(fill="x", pady=3)
            size = p.stat().st_size / 1_048_576
            ctk.CTkLabel(card, text=f"🎵 {p.stem}", font=T.font(12, "bold"), anchor="w").pack(side="left", padx=10, pady=8)
            ctk.CTkLabel(card, text=f"{p.suffix[1:].upper()} · {size:.1f} MB", font=T.font(10), text_color=T.TEXT_MUTED).pack(side="left")
            used = str(p).lower() in have
            use = button(card, "Đã thêm ✓" if used else "Thêm vào video", icon_name="check", variant="secondary" if used else "primary",
                         size="sm", command=lambda f=p: self._use(f), state="disabled" if used else "normal")
            use.pack(side="right", padx=8)
            button(card, "Nghe", icon_name="play", size="sm", command=lambda f=p: self.module.play_preview(str(f))).pack(side="right")

    def _use(self, p: Path) -> None:
        if self.module.add_paths([str(p)]):
            self.window.log(f"Đã thêm nhạc: {p.name} (phát từ đầu video)", notice=True)
        self.refresh()

    def _stop(self) -> None:
        try:
            self.window.player.stop()
        except Exception:                          # noqa: BLE001
            pass

    def destroy(self) -> None:
        self._stop()                               # đóng thư viện thì thôi phát bài đang nghe thử
        super().destroy()

    def _open_dir(self) -> None:
        MUSIC_LIB.mkdir(parents=True, exist_ok=True)
        open_path(str(MUSIC_LIB))
