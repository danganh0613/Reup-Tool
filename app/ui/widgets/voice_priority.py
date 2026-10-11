"""Giọng ưu tiên theo giới (lưu theo mẫu): Nam 1 → 4, Nữ 1 → 4. Video có nhiều người nói: người nói nhiều nhất nhận
giọng 1 của giới mình, người kế nhận giọng 2…; hết giọng ưu tiên thì máy tự chọn thêm giọng cùng giới."""

from __future__ import annotations

import customtkinter as ctk

from app.config import theme as T
from app.services.tts.generator import (config_with_voice, main_locale, make_engine, preview_text,
                                        synthesize_cached)
from app.ui.progress_panel import friendly_error
from app.ui.widgets.common import StatusLabel, info_icon
from app.ui.widgets.ui import Select, button

MAX_SLOTS = 4
GROUPS = (("Nam", "male_voices", "Giọng nam"), ("Nữ", "female_voices", "Giọng nữ"))


def _gender(v) -> str:
    x = (v.gender or "").lower()
    return "Nam" if x.startswith("m") else "Nữ" if x.startswith("f") else ""


def _label(name: str) -> str:
    """Tên giọng trong ô chọn — bỏ «— Nam / — Nữ» (đã chia nhóm theo giới)."""
    return name.replace(" — Nam ·", " ·").replace(" — Nữ ·", " ·")


class VoicePriority(ctk.CTkFrame):
    def __init__(self, master, module, on_change=None) -> None:
        super().__init__(master, fg_color="transparent")
        self.module = module
        self.window = module.window
        self.state = module.state
        self.on_change = on_change
        self._pool: dict[str, list] = {"Nam": [], "Nữ": []}
        self._loaded_for = None
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", pady=(6, 2))
        ctk.CTkLabel(head, text="Giọng ưu tiên", font=T.font(12, "medium"), text_color=T.TEXT, anchor="w").pack(
            side="left")
        info_icon(head, "Lưu cùng mẫu. Video có nhiều người nói: người nói nhiều nhất nhận giọng số 1 của giới mình "
                        "(Nam / Nữ do máy đoán, sửa được ở thẻ từng người), người kế nhận số 2… Hết giọng ưu tiên thì "
                        "máy tự chọn thêm giọng cùng giới chưa ai dùng.").pack(side="left", padx=(4, 0))
        self.groups: dict[str, ctk.CTkFrame] = {}
        for gender, _key, title in GROUPS:
            ctk.CTkLabel(self, text=title, font=T.font(11), text_color=T.TEXT_DIM, anchor="w").pack(fill="x",
                                                                                                    pady=(4, 0))
            box = ctk.CTkFrame(self, fg_color="transparent")
            box.pack(fill="x")
            self.groups[gender] = box
        self.status = StatusLabel(self, "")
        self.status.pack(fill="x")

    # ── Dữ liệu ──────────────────────────────────────────────────────────
    def _list(self, gender: str) -> list:
        key = dict((g, k) for g, k, _t in GROUPS)[gender]
        return getattr(self.state.tts, key)

    def _store(self, gender: str, entries: list) -> None:
        key = dict((g, k) for g, k, _t in GROUPS)[gender]
        setattr(self.state.tts, key, entries)
        self.state.config_changed("tts")
        self._paint()
        if self.on_change:
            self.on_change()

    def refresh(self) -> None:
        """Nạp danh sách giọng của engine đang chọn (đổi engine / áp mẫu → nạp lại) rồi vẽ các ô."""
        cfg = self.state.tts
        want = (cfg.tts_provider, main_locale(cfg))
        if want == self._loaded_for:
            self._paint()
            return
        self._loaded_for = want
        self._paint()
        engine = make_engine(cfg, self.state.settings)

        def done(result, error):
            if not self.winfo_exists() or self._loaded_for != want:
                return
            if error:
                self.status.error(f"Không tải được danh sách giọng: {friendly_error(str(error))}")
                return
            locale = want[1]
            pool = [v for v in result or [] if not locale or not v.locale or v.locale == locale] or list(result or [])
            self._pool = {g: [v for v in pool if _gender(v) == g] + [v for v in pool if not _gender(v)]
                          for g in ("Nam", "Nữ")}
            self.status.configure(text="")
            self._paint()

        self.window.run_bg(engine.list_voices, done)

    # ── Giao diện ────────────────────────────────────────────────────────
    def _paint(self) -> None:
        cfg = self.state.tts
        for gender, box in self.groups.items():
            for w in box.winfo_children():
                w.destroy()
            entries = [e for e in self._list(gender) if isinstance(e, dict) and e.get("voice_id")]
            by_label = {_label(v.name): v for v in self._pool.get(gender, [])}
            for k in range(min(MAX_SLOTS, len(entries) + 1)):         # các giọng đã xếp + 1 ô trống để thêm
                e = entries[k] if k < len(entries) else None
                labels = dict(by_label)
                cur = ""
                if e is not None:
                    cur = _label(str(e.get("voice_name") or e["voice_id"]))
                    if (e.get("provider") or cfg.tts_provider) != cfg.tts_provider:
                        cur += f" ({str(e.get('provider')).split(' (')[0]})"
                    labels.setdefault(cur, None)
                row = ctk.CTkFrame(box, fg_color="transparent")
                row.pack(fill="x", pady=2)
                ctk.CTkLabel(row, text=str(k + 1), width=18, font=T.font(12, "medium"), text_color=T.TEXT_DIM).pack(
                    side="left")
                sel = Select(row, list(labels), value=cur, height=32, placeholder="Thêm giọng…",
                             command=lambda lab, g=gender, i=k, m=labels: self._picked(g, i, m.get(lab)))
                sel.pack(side="left", fill="x", expand=True, padx=(4, 0))
                if e is not None:
                    button(row, "", icon_name="play", size="sm", width=32, tip="Nghe thử giọng này",
                           command=lambda en=e: self._preview(en)).pack(side="left", padx=(4, 0))
                    button(row, "", icon_name="close", size="sm", width=32, variant="ghost", tip="Bỏ giọng này",
                           command=lambda g=gender, i=k: self._remove(g, i)).pack(side="left", padx=(2, 0))

    def _picked(self, gender: str, k: int, v) -> None:
        if v is None:                                    # giọng của engine khác đang giữ chỗ — không đổi
            return
        cfg = self.state.tts
        entry = {"provider": cfg.tts_provider, "locale": v.locale or main_locale(cfg), "voice_id": v.id,
                 "voice_name": v.name}
        entries = [e for e in self._list(gender) if isinstance(e, dict) and e.get("voice_id")]
        if k < len(entries):
            entries[k] = entry
        else:
            entries.append(entry)
            k = len(entries) - 1
        entries = [e for i, e in enumerate(entries) if i == k or e.get("voice_id") != v.id]    # một giọng một chỗ
        self._store(gender, entries[:MAX_SLOTS])

    def _remove(self, gender: str, k: int) -> None:
        entries = [e for e in self._list(gender) if isinstance(e, dict) and e.get("voice_id")]
        if k < len(entries):
            del entries[k]
            self._store(gender, entries)

    def _preview(self, e: dict) -> None:
        cfg = config_with_voice(self.state.tts, str(e.get("provider") or ""), str(e["voice_id"]),
                                str(e.get("locale") or ""))
        engine = make_engine(cfg, self.state.settings)
        self.status.info("Đang tạo mẫu…")

        def work():
            return synthesize_cached(engine, str(e["voice_id"]), preview_text(main_locale(cfg)), speed=cfg.tts_speed,
                                     pitch=0)

        def done(result, error):
            if error:
                self.status.error(f"Lỗi: {friendly_error(str(error))}")
                return
            self.status.configure(text="")
            self.window.player.play(result)

        self.window.run_bg(work, done)
