"""Đạo diễn âm thanh: chọn giọng + bánh xe cảm xúc + 4 thanh sắc thái + kiểu nói → «direction» cho từng đoạn.

Dùng được ở 2 nơi: cửa sổ riêng (``open_studio``) để thử giọng, và panel nhúng trong timeline lồng tiếng
(mốc 5). Mọi việc tạo giọng chạy luồng nền, kết quả trả về qua hàng đợi + ``after`` (không gọi Tk từ luồng nền).
"""

from __future__ import annotations

import copy
import queue
import threading
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk

from app.config import theme as T
from app.services.direction import compile_direction, emotions, emotions_map, speaking_styles
from app.services.tts.gemini_tts import VOICES as GEMINI_VOICES
from app.ui.widgets.common import ButtonRow, OptionRow, SliderRow, StatusLabel, hint
from app.ui.widgets.emotion_wheel import EmotionWheel
from app.ui.widgets.ui import button

_GENDER_VI = {"Female": "Nữ", "Male": "Nam"}
_MODES = ["Độc thoại", "Hội thoại"]


def _default_direction(voice: str = "Leda", theta: float = 27.69) -> dict:
    return {"voice_id": voice, "emotion": {"theta": theta, "level": 3, "overrides": {}},
            "speaking_style": {"a": "tan_phiem", "b": "", "mix": 0.0}, "special": "", "temperature": None}


class DirectionStudio(ctk.CTkFrame):
    def __init__(self, master, *, settings, play: Callable[[Path], None] | None = None,
                 on_generate: Callable[[str, dict, Path], None] | None = None, wheel_size: int = 400) -> None:
        super().__init__(master, fg_color="transparent")
        self.settings = settings
        self.play = play
        self.on_generate = on_generate
        self._dirs = {"A": _default_direction("Leda"), "B": _default_direction("Charon", 152.31)}
        self._spk = "A"
        self._nuance_ids: list[str] = []
        self._loading = False
        self._q: queue.Queue = queue.Queue()
        self._build(wheel_size)
        self._load(self._dirs["A"])
        self.after(120, self._poll)

    # ── dựng giao diện ────────────────────────────────────────────────────
    def _build(self, wheel_size: int) -> None:
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", pady=(0, 6))
        self.mode = OptionRow(top, "Chế độ", _MODES, value=_MODES[0], on_change=lambda _v: self._mode_changed())
        self.mode.pack(fill="x", pady=2)
        self.speaker = OptionRow(top, "Người nói", ["A", "B"], value="A",
                                 labels={"A": "Người nói A", "B": "Người nói B"},
                                 on_change=lambda v: self._switch_speaker(v))
        self.gender = OptionRow(top, "Lọc giọng", ["Tất cả", "Female", "Male"], value="Tất cả",
                                labels={"Female": "Nữ", "Male": "Nam"}, on_change=lambda _v: self._fill_voices())
        self.gender.pack(fill="x", pady=2)
        vrow = ctk.CTkFrame(top, fg_color="transparent")
        vrow.pack(fill="x", pady=2)
        self.voice = OptionRow(vrow, "Giọng", [v[0] for v in GEMINI_VOICES],
                               labels={n: f"{n} — {d} ({_GENDER_VI[g]})" for n, g, d in GEMINI_VOICES},
                               on_change=lambda _v: self._changed())
        self.voice.pack(side="left", fill="x", expand=True)
        button(vrow, "▶ Nghe mẫu", size="sm", command=self._preview_voice).pack(side="left", padx=(6, 0))

        mid = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=10)
        mid.pack(fill="x", pady=4)
        self.wheel = EmotionWheel(mid, emotions(), size=wheel_size, on_change=lambda _t, _l: self._wheel_moved())
        self.wheel.pack(side="left", padx=8, pady=8)
        nuance = ctk.CTkFrame(mid, fg_color="transparent")
        nuance.pack(side="left", fill="both", expand=True, padx=(4, 10), pady=10)
        ctk.CTkLabel(nuance, text="MỨC SẮC THÁI", font=T.font(11, "bold"), text_color=T.TEXT_DIM).pack(anchor="w")
        cols = ctk.CTkFrame(nuance, fg_color="transparent")
        cols.pack(fill="both", expand=True, pady=(6, 0))
        self._sliders: list[tuple[ctk.CTkLabel, ctk.CTkSlider, ctk.CTkLabel]] = []
        for i in range(4):
            cols.grid_columnconfigure(i, weight=1)
            val = ctk.CTkLabel(cols, text="0", font=T.font(12, "bold"), text_color=T.TEXT)
            val.grid(row=0, column=i)
            sl = ctk.CTkSlider(cols, from_=0, to=100, number_of_steps=100, orientation="vertical", height=260,
                               command=lambda v, k=i: self._nuance_moved(k, v))
            sl.grid(row=1, column=i, pady=4)
            name = ctk.CTkLabel(cols, text="", font=T.font(11), text_color=T.TEXT_DIM, wraplength=70)
            name.grid(row=2, column=i)
            self._sliders.append((val, sl, name))

        self.desc = ctk.CTkLabel(self, text="", font=T.font(12), text_color=T.TEXT, anchor="w", justify="left",
                                 wraplength=640)
        self.desc.pack(fill="x", pady=(6, 2))
        self.prompt_hint = hint(self, "")
        self.prompt_hint.configure(wraplength=640)
        self.prompt_hint.pack(fill="x")

        sty = speaking_styles()
        labels = {s["id"]: f"{s['group']} · {s['vi']}" for s in sty}
        self.style_a = OptionRow(self, "Kiểu nói", [s["id"] for s in sty], labels=labels,
                                 on_change=lambda _v: self._changed())
        self.style_b = OptionRow(self, "Pha thêm", ["none"] + [s["id"] for s in sty], labels={"none": "— không —", **labels},
                                 on_change=lambda _v: self._changed())
        self.mix = SliderRow(self, "Mức pha", from_=0, to=100, step=5, value=0, suffix=" %",
                             on_change=lambda _v: self._changed())
        for r in (self.style_a, self.style_b, self.mix):
            r.pack(fill="x", pady=2)

        ctk.CTkLabel(self, text="Văn bản (hội thoại: mỗi dòng bắt đầu «A:» hoặc «B:»)", font=T.font(12),
                     text_color=T.TEXT_DIM, anchor="w").pack(fill="x", pady=(8, 0))
        self.text = ctk.CTkTextbox(self, height=90, font=T.font(13))
        self.text.pack(fill="x")
        self.text.bind("<KeyRelease>", lambda _e: self._count())
        self.count = ctk.CTkLabel(self, text="0 ký tự", font=T.font(11), text_color=T.TEXT_MUTED, anchor="e")
        self.count.pack(fill="x")
        self.special = ctk.CTkEntry(self, placeholder_text="Yêu cầu đặc biệt (vd: nhấn mạnh tên sản phẩm)",
                                    height=34, font=T.font(12))
        self.special.pack(fill="x", pady=(2, 4))
        self.special.bind("<KeyRelease>", lambda _e: self._changed())
        self.lang = OptionRow(self, "Ngôn ngữ đọc", ["vi"], value="vi", labels={"vi": "Tiếng Việt"})
        self.lang.pack(fill="x", pady=2)
        ButtonRow(self, [("Tạo giọng nói", self._generate, T.primary_button()),
                         ("Đặt lại", self._reset, None)]).pack(fill="x", pady=(6, 2))
        self.status = StatusLabel(self, "Kéo kim trên bánh xe: góc = cảm xúc, khoảng cách từ tâm = bậc 1–5.")
        self.status.configure(wraplength=640)
        self.status.pack(fill="x")

    # ── nạp / đọc direction ───────────────────────────────────────────────
    def _fill_voices(self) -> None:
        g = self.gender.get()
        names = [n for n, gd, _d in GEMINI_VOICES if g == "Tất cả" or gd == g]
        self.voice.set_values(names or [v[0] for v in GEMINI_VOICES])

    def _load(self, d: dict) -> None:
        self._loading = True
        try:
            self.voice.set(d.get("voice_id") or "Leda")
            emo = d.get("emotion") or {}
            self.wheel.set(emo.get("theta", 0.0), emo.get("level", 3))
            self._overrides = dict(emo.get("overrides") or {})
            sp = d.get("speaking_style") or {}
            self.style_a.set(sp.get("a") or "tan_phiem")
            self.style_b.set(sp.get("b") or "none")
            self.mix.set(round(float(sp.get("mix") or 0.0) * 100))
            self.special.delete(0, "end")
            if d.get("special"):
                self.special.insert(0, d["special"])
        finally:
            self._loading = False
        self._refresh(update_sliders=True)

    def get_direction(self) -> dict:
        theta, level = self.wheel.get()
        mix = float(self.mix.get()) / 100.0
        return {"voice_id": self.voice.get(),
                "emotion": {"theta": round(theta, 2), "level": level, "overrides": dict(self._overrides)},
                "speaking_style": {"a": self.style_a.get(), "b": "" if self.style_b.get() == "none" else self.style_b.get(),
                                   "mix": round(mix, 2)},
                "special": self.special.get().strip(), "temperature": None}

    def set_direction(self, d: dict) -> None:
        self._dirs[self._spk] = copy.deepcopy(d)
        self._load(d)

    # ── sự kiện ───────────────────────────────────────────────────────────
    def _wheel_moved(self) -> None:
        self._overrides = {}                       # kim dời → sắc thái tính lại từ vị trí mới
        self._changed(update_sliders=True)

    def _nuance_moved(self, k: int, v: float) -> None:
        if self._loading or k >= len(self._nuance_ids):
            return
        self._overrides[self._nuance_ids[k]] = int(round(v))
        self._sliders[k][0].configure(text=str(int(round(v))))
        self._changed(update_sliders=False)

    def _changed(self, update_sliders: bool = False) -> None:
        if self._loading:
            return
        self._dirs[self._spk] = self.get_direction()
        self._refresh(update_sliders)

    def _refresh(self, update_sliders: bool = True) -> None:
        c = compile_direction(self.get_direction())
        self.desc.configure(text=c.description_vi)
        self.prompt_hint.configure(text=f"Lời dặn gửi Gemini: «{c.style_en}» · temperature {c.temperature}")
        if update_sliders:
            em = emotions_map()
            ordered = sorted(c.weights.items(), key=lambda x: x[1], reverse=True)[:4]
            self._nuance_ids = [eid for eid, _w in ordered]
            self._loading = True
            try:
                for (val, sl, name), (eid, w) in zip(self._sliders, ordered):
                    name.configure(text=em[eid]["vi"])
                    val.configure(text=str(w))
                    sl.set(w)
            finally:
                self._loading = False

    def _mode_changed(self) -> None:
        if self.mode.get() == _MODES[1]:
            self.speaker.pack(fill="x", pady=2, after=self.mode)
        else:
            self.speaker.pack_forget()
            self._switch_speaker("A")

    def _switch_speaker(self, spk: str) -> None:
        self._dirs[self._spk] = self.get_direction()
        self._spk = spk
        self.speaker.set(spk)
        self._load(self._dirs[spk])

    def _count(self) -> None:
        self.count.configure(text=f"{len(self.text.get('1.0', 'end').strip())} ký tự")

    def _reset(self) -> None:
        self._dirs = {"A": _default_direction("Leda"), "B": _default_direction("Charon", 152.31)}
        self._spk = "A"
        self.speaker.set("A")
        self._load(self._dirs["A"])
        self.status.info("Đã đặt lại hướng đọc.")

    # ── tạo giọng (luồng nền) ─────────────────────────────────────────────
    def _gender_of(self, voice: str) -> str:
        return next((g for n, g, _d in GEMINI_VOICES if n == voice), "Female")

    def _run(self, fn, done) -> None:
        def work():
            try:
                self._q.put((done, fn(), None))
            except Exception as exc:              # noqa: BLE001 — báo lỗi lên giao diện, không làm sập
                self._q.put((done, None, exc))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                done, res, err = self._q.get_nowait()
                done(res, err)
        except queue.Empty:
            pass
        try:
            self.after(120, self._poll)
        except Exception:                         # widget đã đóng
            pass

    def _log(self, msg: str) -> None:
        self._q.put((lambda _r, _e: self.status.warn(msg), None, None))

    def _preview_voice(self) -> None:
        voice = self.voice.get()
        d = _default_direction(voice)
        sample = "Xin chào, đây là giọng mẫu để bạn nghe thử."
        self.status.info(f"Đang tạo mẫu giọng {voice}… (có cache, nghe lại không tốn lượt)")
        from app.services.direction.speak import speak
        self._run(lambda: speak(sample, voice_id=voice, compiled=compile_direction(d), settings=self.settings,
                                gender=self._gender_of(voice), log=self._log), self._played)

    def _generate(self) -> None:
        text = self.text.get("1.0", "end").strip()
        if not text:
            self.status.warn("Nhập văn bản cần đọc trước.")
            return
        self._dirs[self._spk] = self.get_direction()
        from app.services.direction.speak import speak, speak_dialogue

        if self.mode.get() == _MODES[1]:
            lines = []
            for ln in text.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                spk = ln[0].upper() if len(ln) > 1 and ln[1] == ":" and ln[0].upper() in "AB" else self._spk
                lines.append((spk, ln[2:].strip() if ln[1:2] == ":" else ln))
            dirs = copy.deepcopy(self._dirs)
            genders = {k: self._gender_of(v["voice_id"]) for k, v in dirs.items()}
            self.status.info(f"Đang tạo hội thoại {len(lines)} lượt nói…")
            self._run(lambda: speak_dialogue(lines, dirs, settings=self.settings, genders=genders, log=self._log),
                      lambda r, e: self._done_dialogue(r, e, text, dirs))
            return
        d = copy.deepcopy(self._dirs[self._spk])
        self.status.info("Đang tạo giọng…")
        self._run(lambda: speak(text, voice_id=d["voice_id"], compiled=compile_direction(d), settings=self.settings,
                                gender=self._gender_of(d["voice_id"]), log=self._log),
                  lambda r, e: self._done_single(r, e, text, d))

    def _played(self, res, err) -> None:
        if err:
            self.status.error(f"Không tạo được mẫu: {err}")
            return
        path, eng, fb = res
        self.status.ok(f"Mẫu giọng ({eng}{' — dự phòng' if fb else ''})")
        if self.play:
            self.play(path)

    def _done_single(self, res, err, text: str, d: dict) -> None:
        if err:
            self.status.error(f"Không tạo được giọng: {err}")
            return
        path, eng, fb = res
        self.status.ok(f"Xong — {eng}{' (dự phòng vì Gemini lỗi / hết lượt)' if fb else ''} · {path.name}")
        if self.play:
            self.play(path)
        if self.on_generate:
            self.on_generate(text, d, path)

    def _done_dialogue(self, res, err, text: str, dirs: dict) -> None:
        if err:
            self.status.error(f"Không tạo được hội thoại: {err}")
            return
        path, engines, fb = res
        self.status.ok(f"Xong hội thoại {len(engines)} lượt" + (" — có lượt dùng dự phòng" if fb else "") + f" · {path.name}")
        if self.play:
            self.play(path)
        if self.on_generate:
            self.on_generate(text, dirs, path)


def open_studio(master, settings, play: Callable[[Path], None] | None = None) -> ctk.CTkToplevel:
    """Cửa sổ riêng để thử giọng (mốc 3: «trang độc lập»)."""
    win = ctk.CTkToplevel(master)
    win.title("Đạo diễn âm thanh")
    win.geometry("720x980")
    win.configure(fg_color=T.BG)
    body = ctk.CTkScrollableFrame(win, fg_color=T.BG)
    body.pack(fill="both", expand=True, padx=12, pady=12)
    DirectionStudio(body, settings=settings, play=play).pack(fill="both", expand=True)
    return win
