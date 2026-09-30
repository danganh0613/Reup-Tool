"""Widget chọn giọng (engine → ngôn ngữ → giọng) + nghe thử, dùng cho Story và phân vai."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import customtkinter as ctk

from app.config import theme as T
from app.core.state import TTS_PROVIDERS
from app.services.tts.base import Voice
from app.services.tts.generator import make_engine, preview_text, synthesize_cached
from app.ui.widgets.common import ButtonRow, OptionRow, StatusLabel

VoiceSel = tuple[str, str, str]          # (provider, locale, voice_id)


class VoicePicker(ctk.CTkFrame):
    def __init__(self, master, window, *, get: Callable[[], VoiceSel],
                 set_: Callable[[str, str, str, str], None], speed: Callable[[], float] = lambda: 1.0,
                 compact: bool = False) -> None:
        super().__init__(master, fg_color="transparent")
        self.window = window
        self.get_sel = get
        self.set_sel = set_
        self.get_speed = speed
        self._voices: list[Voice] = []
        self._by_label: dict[str, Voice] = {}
        self._engine = None
        self._loading = False

        self.provider = OptionRow(self, "Engine", TTS_PROVIDERS, on_change=self._provider_changed)
        self.language = OptionRow(self, "Ngôn ngữ", ["vi-VN"], on_change=self._language_changed)
        self.voice = OptionRow(self, "Giọng", ["(đang tải…)"], on_change=self._voice_changed)
        for r in (self.provider, self.language, self.voice):
            r.pack(fill="x", pady=2)
        ButtonRow(self, [("▶ Nghe thử", self.preview, T.primary_button()),
                         ("↻ Tải lại", lambda: self.load(force=True), None)]).pack(fill="x", pady=(4, 2))
        self.status = StatusLabel(self, "")
        self.status.pack(fill="x")
        self.load()

    # ── Nạp danh sách ────────────────────────────────────────────────────
    def _cfg(self):
        provider, locale, vid = self.get_sel()
        cfg = replace(self.window.app_state.tts)
        cfg.tts_provider = provider
        cfg.edge_language = cfg.google_language = locale
        cfg.edge_voice = cfg.google_voice = cfg.eleven_voice_id = cfg.vieneu_voice = vid
        cfg.tts_speed = self.get_speed()
        return cfg

    def load(self, force: bool = False) -> None:
        provider, _, _ = self.get_sel()
        self._loading = True
        self.provider.set(provider)
        self._loading = False
        self.status.info("Đang tải danh sách giọng…")
        try:
            engine = make_engine(self._cfg(), self.window.app_state.settings)
        except Exception as exc:
            self.status.error(str(exc))
            return
        if force and hasattr(engine, "_voices"):
            engine._voices = None
        self._engine = engine
        self.window.run_bg(engine.list_voices, self._loaded)

    def _loaded(self, result, error) -> None:
        if error:
            self.status.error(f"Không tải được giọng: {error}")
            self.voice.set_values(["(lỗi)"], keep=False)
            return
        self._voices = result or []
        self._fill()
        self.status.info(f"{len(self._voices)} giọng.")

    def _fill(self) -> None:
        provider, locale, vid = self.get_sel()
        self._loading = True
        try:
            if provider == "ElevenLabs" or provider.startswith("VieNeu"):
                self.language.pack_forget()
                subset = self._voices
            else:
                if not self.language.winfo_ismapped():
                    self.language.pack(fill="x", pady=2, before=self.voice)
                locales: dict[str, None] = {}
                for v in self._voices:
                    locales.setdefault(v.locale, None)
                loc_list = list(locales) or [locale]
                if locale not in loc_list:
                    locale = loc_list[0]
                self.language.set_values(loc_list, keep=False)
                self.language.set(locale)
                subset = [v for v in self._voices if v.locale == locale]
            self._by_label = {v.label: v for v in subset}
            labels = list(self._by_label) or ["(không có giọng)"]
            self.voice.set_values(labels, keep=False)
            cur = next((v.label for v in subset if v.id == vid), None)
            if cur is None and subset:
                cur = subset[0].label
                self.set_sel(provider, subset[0].locale, subset[0].id, subset[0].name)
            self.voice.set(cur or labels[0])
        finally:
            self._loading = False

    # ── Sự kiện ──────────────────────────────────────────────────────────
    def _provider_changed(self, p: str) -> None:
        if self._loading:
            return
        _, locale, vid = self.get_sel()
        self.set_sel(p, locale, "", "")
        self.load()

    def _language_changed(self, loc: str) -> None:
        if self._loading:
            return
        provider, _, _ = self.get_sel()
        self.set_sel(provider, loc, "", "")
        self._fill()

    def _voice_changed(self, label: str) -> None:
        if self._loading:
            return
        v = self._by_label.get(label)
        if v:
            provider, _, _ = self.get_sel()
            self.set_sel(provider, v.locale, v.id, v.name)

    def preview(self) -> None:
        if self._engine is None:
            return
        provider, locale, vid = self.get_sel()
        engine, speed = self._engine, self.get_speed()
        text = preview_text(locale)
        self.status.info("Đang tạo mẫu…")

        def work():
            return synthesize_cached(engine, vid, text, speed=speed, pitch=0)

        def done(result, error):
            if error:
                self.status.error(f"Lỗi: {error}")
                return
            self.status.info("Đang phát…")
            try:
                self.window.player.play(result)
            except Exception as exc:
                self.status.error(str(exc))

        self.window.run_bg(work, done)
