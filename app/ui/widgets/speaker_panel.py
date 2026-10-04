"""Lồng tiếng nhiều giọng: danh sách người nói của video đang chọn (mỗi người một giọng) + bảng gán người nói từng câu."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.config import theme as T
from app.config.paths import CACHE_DIR
from app.core.state import VideoItem
from app.services.diarize import SpeakerInfo, SpeakerMap, speaker_name
from app.services.srt_utils import format_ts, load_srt
from app.ui.widgets.common import SliderRow, StatusLabel
from app.ui.widgets.ui import Select, button
from app.ui.widgets.voice_picker import VoicePicker

CLIP_DIR = CACHE_DIR / "speaker_clips"


def main_locale(cfg) -> str:
    if cfg.tts_provider == "Edge TTS":
        return cfg.edge_language
    if cfg.tts_provider == "Google Cloud TTS":
        return cfg.google_language
    if cfg.tts_provider.startswith("VieNeu") or cfg.tts_provider.startswith("FPT"):
        return "vi-VN"
    return ""


def play_segment(window, item: VideoItem, start: float, end: float, on_error: Callable[[str], None] | None = None) -> None:
    """Phát đoạn audio gốc [start, end] của video — để nghe xem đó là ai."""
    end = min(end, start + 12.0)
    key = hashlib.sha1(f"{item.path}|{start:.2f}|{end:.2f}".encode("utf-8")).hexdigest()[:16]
    out = CLIP_DIR / f"{key}.wav"

    def work():
        if not out.exists():
            CLIP_DIR.mkdir(parents=True, exist_ok=True)
            res = window.ffmpeg.run_simple(["-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.3, end - start):.3f}",
                                            "-i", item.path, "-vn", "-ac", "1", "-ar", "24000", str(out)], timeout=60)
            if res.returncode != 0 or not out.exists():
                raise RuntimeError(res.stderr.decode("utf-8", "replace")[-200:] or "không cắt được audio")
        return out

    def done(result, error):
        if error:
            if on_error:
                on_error(f"Không phát được: {error}")
            return
        window.player.play(result)

    window.run_bg(work, done)


class SpeakerPanel(ctk.CTkFrame):
    """Mỗi người nói một thẻ: thông tin (Nam/Nữ, số câu) · nghe giọng gốc · chọn giọng lồng tiếng · cao độ."""

    def __init__(self, master, module) -> None:
        super().__init__(master, fg_color="transparent")
        self.module = module
        self.window = module.window
        self.state = module.state
        self._sig = None
        self.empty = StatusLabel(self, "")
        self.empty.pack(fill="x")
        self.cards = ctk.CTkFrame(self, fg_color="transparent")
        self.cards.pack(fill="x")

    @property
    def pipeline(self):
        return self.window.pipeline

    # ── Mục giọng của một người nói (trong cfg.speaker_voices) ────────────
    def _entry(self, name: str) -> dict:
        cfg = self.state.tts
        for sv in cfg.speaker_voices:
            if isinstance(sv, dict) and sv.get("name") == name:
                return sv
        sv = {"name": name, "provider": cfg.tts_provider, "locale": main_locale(cfg), "voice_id": "", "voice_name": "",
              "speed": 0.0}
        cfg.speaker_voices.append(sv)
        return sv

    def refresh(self, force: bool = False) -> None:
        item = self.state.active
        path = self.pipeline.speakers_path(item) if item else None
        try:
            mtime = path.stat().st_mtime_ns if path else 0
        except OSError:
            mtime = 0
        sig = (item.path if item else "", mtime, id(self.state.tts))     # nạp preset thay cả object cấu hình
        if sig == self._sig and not force:
            return
        self._sig = sig
        for w in self.cards.winfo_children():
            w.destroy()
        if item is None:
            self.empty.info("Chọn một video trong hàng đợi.")
            return
        smap = SpeakerMap.load(path)
        if smap is None or not smap.speakers:
            self.empty.info("Video này chưa nhận diện người nói — bấm «Nhận diện người nói», hoặc «Gán từng câu…» "
                            "để tự chia. Khi render, máy sẽ tự nhận diện nếu chưa có.")
            return
        names = smap.names()
        self.empty.info(f"{len(names)} người nói · mỗi người chọn một giọng (người chưa chọn dùng giọng chính).")
        missing = [n for n in names if not self._entry(n).get("voice_id")]
        if missing:
            self._suggest_then_build(smap)
        else:
            self._build(smap)

    def _suggest_then_build(self, smap: SpeakerMap) -> None:
        from app.services.diarize import suggest_voices
        from app.services.tts.generator import make_engine, voice_id_for

        cfg = self.state.tts
        engine = make_engine(cfg, self.state.settings)

        def done(result, error):
            if not error and result:
                cfg.speaker_voices = suggest_voices(
                    [smap.speakers[n] for n in smap.names()], result, provider=cfg.tts_provider,
                    locale=main_locale(cfg), main_voice=voice_id_for(cfg),
                    existing=[sv for sv in cfg.speaker_voices if sv.get("voice_id")])
                self.state.config_changed("tts", auto=True)      # gợi ý tự động, không phải người dùng sửa
            self._build(smap)

        self.window.run_bg(engine.list_voices, done)

    def _build(self, smap: SpeakerMap) -> None:
        for w in self.cards.winfo_children():
            w.destroy()
        item = self.state.active
        for name in smap.names():
            self._card(item, smap, smap.speakers.get(name) or SpeakerInfo(name))

    def _card(self, item: VideoItem, smap: SpeakerMap, info: SpeakerInfo) -> None:
        cfg = self.state.tts
        e = self._entry(info.name)
        card = ctk.CTkFrame(self.cards, fg_color=T.BG_INPUT, corner_radius=8)
        card.pack(fill="x", pady=4)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(head, text=info.name, font=T.font(13, "bold"), text_color=T.TEXT, anchor="w").pack(side="left")
        bits = [info.gender or "chưa rõ Nam/Nữ", f"{info.count} câu", f"{info.seconds:.0f}s"]
        ctk.CTkLabel(head, text="  ·  ".join(bits), font=T.font(11), text_color=T.TEXT_MUTED, anchor="w").pack(
            side="left", padx=(8, 0))
        status = StatusLabel(card, "")
        segs = sorted([s for s in smap.segments if s[2] == info.name], key=lambda s: -(s[1] - s[0]))
        if segs and item is not None:
            a, b, _n = segs[0]
            button(head, "Nghe giọng gốc", icon_name="play", size="sm",
                   command=lambda: play_segment(self.window, item, a, b, status.error),
                   tip="Phát câu dài nhất của người này trong video gốc").pack(side="right")

        def get_sel():
            return (e.get("provider") or cfg.tts_provider, e.get("locale") or main_locale(cfg), e.get("voice_id", ""))

        def set_sel(provider: str, locale: str, vid: str, vname: str) -> None:
            e.update(provider=provider, locale=locale, voice_id=vid, voice_name=vname)
            self.state.config_changed("tts", auto=picker.auto)
            pitch.slider.configure(state="normal" if provider == "Edge TTS" else "disabled")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=10, pady=(0, 8))
        picker = VoicePicker(body, self.window, get=get_sel, set_=set_sel,
                             speed=lambda: float(e.get("speed") or cfg.tts_speed))
        picker.pack(fill="x")

        def set_pitch(v: float) -> None:
            e["pitch"] = int(v)
            self.state.config_changed("tts")

        pitch = SliderRow(body, "Cao độ (Edge)", from_=-50, to=50, step=5, fmt="{:+.0f}", suffix="Hz",
                          on_change=set_pitch)
        pitch.set(int(e.get("pitch", cfg.tts_pitch) or 0))
        pitch.pack(fill="x", pady=(2, 0))
        pitch.slider.configure(state="normal" if (e.get("provider") or cfg.tts_provider) == "Edge TTS" else "disabled")
        status.pack(fill="x", padx=10, pady=(0, 6))


class SpeakerAssignDialog(ctk.CTkToplevel):
    """Gán người nói cho từng câu (sửa kết quả tự nhận diện, hoặc tự chia khi không nhận diện được).

    Chia trang ``PAGE`` câu như «Dịch thủ công»; dữ liệu thật nằm ở ``labels``.
    """

    PAGE = 50

    def __init__(self, window, item: VideoItem, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.item = item
        self.on_saved = on_saved
        self.title("Gán người nói")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1100, sw - 60)}x{min(800, sh - 100)}+50+40")
        self.after(50, self.lift)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        pipe = window.pipeline
        self.path = pipe.speakers_path(item)
        self.cues = load_srt(item.srt_path)
        self.smap = SpeakerMap.load(self.path)
        tr: list = []
        if item.srt_translated_path and Path(item.srt_translated_path).exists():
            tr = load_srt(item.srt_translated_path)
        self.translated = [c.plain for c in tr[:len(self.cues)]] + [""] * max(0, len(self.cues) - len(tr))
        if self.smap is not None:
            self.labels = [self.smap.speaker_at(c.start, c.end) or speaker_name(1) for c in self.cues]
            self.names = self.smap.names()
        else:
            self.labels = [speaker_name(1)] * len(self.cues)
            self.names = [speaker_name(1), speaker_name(2)]
        for n in self.labels:
            if n not in self.names:
                self.names.append(n)
        self.checked = [False] * len(self.cues)
        self.page = 0
        self.pages = max(1, (len(self.cues) + self.PAGE - 1) // self.PAGE)

        head = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=8)
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        ctk.CTkLabel(head, text=f"{item.name} · {len(self.cues)} câu — chọn người nói cho từng câu (▶ để nghe)",
                     font=T.font(12), anchor="w").pack(side="left", padx=10, pady=8)
        button(head, "Thêm người nói", icon_name="add", size="sm", command=self._add_speaker).pack(side="right", padx=4)
        button(head, "Gán cho câu đã tick", size="sm", command=self._apply_checked).pack(side="right", padx=4)
        self.bulk = Select(head, self.names, value=self.names[0], width=130, height=32)
        self.bulk.pack(side="right", padx=4)

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew", padx=10)
        self.list.grid_columnconfigure(2, weight=1)
        self._rows: list[dict] = []
        for r in range(min(self.PAGE, len(self.cues))):
            var = ctk.BooleanVar(value=False)
            chk = ctk.CTkCheckBox(self.list, text="", variable=var, width=24)
            idx = ctk.CTkLabel(self.list, text="", font=T.mono(10), text_color=T.TEXT_MUTED, width=80, anchor="w",
                               justify="left")
            txt = ctk.CTkLabel(self.list, text="", font=T.font(11), anchor="w", justify="left", wraplength=560)
            sel = Select(self.list, self.names, width=130, height=32)
            play = button(self.list, "", icon_name="play", size="icon", tip="Nghe câu này trong video gốc",
                          command=lambda r=r: self._play_row(r))
            self._rows.append({"var": var, "idx": idx, "txt": txt, "sel": sel,
                               "widgets": [(chk, dict(row=r, column=0, padx=(0, 4), pady=2)),
                                           (idx, dict(row=r, column=1, sticky="w", pady=2)),
                                           (txt, dict(row=r, column=2, sticky="ew", padx=(0, 8), pady=2)),
                                           (sel, dict(row=r, column=3, padx=(0, 6), pady=2)),
                                           (play, dict(row=r, column=4, pady=2))]})

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        nav = ctk.CTkFrame(foot, fg_color="transparent")
        nav.pack(side="left")
        self.prev_btn = ctk.CTkButton(nav, text="‹ Trước", width=80, height=32, command=lambda: self._show_page(self.page - 1),
                                      **T.secondary_button())
        self.prev_btn.pack(side="left")
        self.page_label = ctk.CTkLabel(nav, text="", font=T.font(11), width=170)
        self.page_label.pack(side="left", padx=6)
        self.next_btn = ctk.CTkButton(nav, text="Sau ›", width=80, height=32, command=lambda: self._show_page(self.page + 1),
                                      **T.secondary_button())
        self.next_btn.pack(side="left")
        if self.pages <= 1:
            nav.pack_forget()
        self.status = StatusLabel(foot, "")
        self.status.pack(side="left", fill="x", expand=True, padx=(10, 0))
        button(foot, "Lưu", icon_name="save", variant="primary", command=self._save).pack(side="right")
        ctk.CTkButton(foot, text="Đóng", width=90, height=36, command=self.destroy, **T.secondary_button()).pack(
            side="right", padx=8)
        self._show_page(0, sync=False)

    # ── Trang ─────────────────────────────────────────────────────────────
    def _page_range(self) -> range:
        a = self.page * self.PAGE
        return range(a, min(a + self.PAGE, len(self.cues)))

    def _sync_page(self) -> None:
        for row, i in zip(self._rows, self._page_range()):
            self.labels[i] = row["sel"].get()
            self.checked[i] = bool(row["var"].get())

    def _show_page(self, page: int, *, sync: bool = True) -> None:
        if sync:
            self._sync_page()
        self.page = max(0, min(self.pages - 1, page))
        idxs = self._page_range()
        for r, row in enumerate(self._rows):
            if r < len(idxs):
                i = idxs[r]
                c = self.cues[i]
                row["var"].set(self.checked[i])
                row["idx"].configure(text=f"{i + 1}\n{format_ts(c.start, '.')[:8]}")
                text = c.plain
                if self.translated[i]:
                    text += f"\n→ {self.translated[i]}"
                row["txt"].configure(text=text)
                row["sel"].set(self.labels[i])
                for w, kw in row["widgets"]:
                    w.grid(**kw)
            else:
                for w, _kw in row["widgets"]:
                    w.grid_remove()
        if idxs:
            self.page_label.configure(text=f"Trang {self.page + 1}/{self.pages} · câu {idxs[0] + 1}–{idxs[-1] + 1}")
        self.prev_btn.configure(state="normal" if self.page > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.page < self.pages - 1 else "disabled")
        try:
            self.list._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    # ── Hành động ────────────────────────────────────────────────────────
    def _set_names(self) -> None:
        self.bulk.set_values(self.names)
        for row in self._rows:
            row["sel"].set_values(self.names)

    def _add_speaker(self) -> None:
        n = len(self.names) + 1
        while speaker_name(n) in self.names:
            n += 1
        self.names.append(speaker_name(n))
        self._set_names()
        self.bulk.set(speaker_name(n))
        self.status.info(f"Đã thêm {speaker_name(n)} — tick các câu của người này rồi bấm «Gán cho câu đã tick».")

    def _apply_checked(self) -> None:
        self._sync_page()
        who = self.bulk.get()
        n = 0
        for i, on in enumerate(self.checked):
            if on:
                self.labels[i] = who
                self.checked[i] = False
                n += 1
        self._show_page(self.page, sync=False)
        self.status.info(f"Đã gán {n} câu cho {who}." if n else "Chưa tick câu nào.")

    def _play_row(self, r: int) -> None:
        idxs = self._page_range()
        if r < len(idxs):
            c = self.cues[idxs[r]]
            play_segment(self.window, self.item, c.start, c.end, self.status.error)

    def _save(self) -> None:
        self._sync_page()
        smap = SpeakerMap.from_cues(self.cues, self.labels, base=self.smap)
        for n in self.names:                      # giữ người vừa thêm dù chưa gán câu nào
            smap.speakers.setdefault(n, SpeakerInfo(n))
        try:
            smap.save(self.path)
        except OSError as exc:
            messagebox.showerror("Edit Studio", f"Không lưu được: {exc}", parent=self)
            return
        used = sorted({n for n in self.labels}, key=self.names.index)
        self.status.ok(f"Đã lưu: {len(used)} người nói.")
        self.window.log(f"Gán người nói: {self.item.name} · " + ", ".join(
            f"{n} ({self.labels.count(n)} câu)" for n in used))
        if self.on_saved:
            self.on_saved()
