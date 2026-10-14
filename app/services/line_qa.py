"""Soát lỗi từng câu — để người dùng chỉ phải nghe các câu cần xem thay vì cả video:

- giọng đọc chưa chuẩn: Whisper nghe lại vẫn lệch sau khi đã đọc lại (``loi_qc`` trong báo cáo từng câu của track
  giọng ``.cues.csv``), hoặc câu phải đọc tạm bằng giọng dự phòng;
- đọc quá nhanh (nén gần trần cho vừa khung) / vẫn tràn khung;
- câu dịch còn chữ gốc (Hán / Kana / Hangul), chưa dịch (y nguyên lời gốc) hoặc trống;
- xưng hô lệch hồ sơ kịch bản (vd hồ sơ chốt «tao – mày» mà câu dùng «tôi» / «bạn»);
- giọng đọc xong sớm, để trống trong lúc người nói gốc còn nói (``loose_lines`` — nói đầy đủ hơn cho kín; chỗ video
  gốc tự ngừng nói thì không tính: giữ nhịp video);
- câu vừa sửa (giọng chưa đọc lại) ước lượng dài quá khung — ``FitModel``: tốc độ đọc đo từ track giọng của chính
  video, khung từng câu như lúc tạo giọng; cũng cho giới hạn ký tự khi nhờ AI dịch lại / rút gọn.

Chỉ đọc file có sẵn (SRT, ``.cues.csv`` cạnh WAV giọng, hồ sơ kịch bản) — không gọi AI, không tốn lượt."""

from __future__ import annotations

import csv
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

#: tên dòng ở tab Duyệt (bấm «Sửa ›» → tab Lời thoại chỉ hiện các câu này)
QA_TITLE = "Câu cần nghe lại"
FAST = 1.2                       # nén ≥ ×1,2 (gần trần ×1,25) → có thể nghe dồn, nên nghe lại
OVER = 0.15                      # vẫn tràn khung quá 0,15 s
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
#: đại từ gần như luôn là xưng hô; «bạn» / «cậu» bỏ qua khi là danh từ (bạn mày, bạn bè, cậu ấy…)
_PRONOUNS = ("tao", "mày", "tôi", "bạn", "tớ", "cậu")
_NOUN_AFTER = {"bạn": re.compile(r"(?:mày|tao|tôi|tớ|cậu|nó|bè|thân|gái|trai|học|cùng|đời|cũ|của|ấy|kia|đó)(?!\w)"),
               "cậu": re.compile(r"(?:ấy|ta|bé|út|chủ|mợ)(?!\w)")}
#: chưa có giọng để đo: ký tự / giây ở tốc độ gốc của giọng (VieNeu đo được ~20–21) — để thấp cho chắc
DEFAULT_CPS = 19.0
TEMPO_CAP = 1.25                 # = ``generator.MAX_TOTAL_TEMPO`` (không nạp generator: kéo theo numpy ~1 s)
#: giọng đọc xong trước khi người nói gốc hết câu quá ngần này (giây) → nói đầy đủ hơn cho kín (đo video chó: câu 11, 14
#: xong sớm 1,1 s); chừa ``KEEP_PAUSE`` nghỉ tự nhiên trước câu sau
LOOSE = 0.6
KEEP_PAUSE = 0.45
MIN_SPOKEN = 0.55                # câu ngắn nhất cũng đọc mất chừng này (đo VieNeu: «Thế con» 0,7 s)
_PERSON = re.compile(r"^-\s*(?P<name>[^(:]+?)\s*(?:\([^)]*\))?\s*:(?P<rest>.*)$")
_QUOTED = re.compile(r"«([^»]+)»")


@dataclass(frozen=True)
class Flag:
    kind: str        # voice · fast · src · pronoun · empty · gap
    short: str       # chữ ngắn trên dòng câu
    detail: str      # giải thích (tooltip / báo cáo)
    budget: int = 0  # gap: số ký tự nên có cho kín chỗ


_KIND_NAMES = {"voice": "giọng đọc", "fast": "đọc nhanh", "src": "chưa dịch", "pronoun": "xưng hô", "empty": "trống",
               "gap": "xong sớm"}
#: lỗi giọng đọc do CHỮ (đổi cách nói thì đỡ) — khác lỗi âm thanh («vào đột ngột», «chữ đầu nghe thành…»: Whisper hay
#: nghe nhầm), sửa chữ không giúp gì
_WORD_PROBLEMS = ("đọc sai chữ", "đọc lặp", "không nghe ra lời", "nuốt chữ")


@dataclass(frozen=True)
class FitModel:
    """Ước lượng câu có đọc vừa khung không mà không cần tạo giọng: khung từng câu như lúc tạo giọng, tốc độ đọc đo
    từ track giọng của chính video (chưa có giọng: ``DEFAULT_CPS``)."""
    slots: list[float]       # khung từng câu (giây): tới câu sau trừ khoảng nghỉ
    cps: float               # ký tự / giây ở tốc độ gốc của giọng (×1)
    floor: float             # câu ngắn mấy cũng đọc mất chừng này
    cap: float               # nén tối đa (×)
    fast: float              # từ mức nén này coi là đọc nhanh
    measured: bool           # tốc độ đo từ giọng thật (False = mặc định, chỉ báo câu tràn hẳn)
    #: tốc độ đọc đo riêng từng câu đã có giọng: {chỉ số: (chữ đã đọc, ký tự / giây)} — câu nhiều dấu phẩy đọc chậm
    #: hơn trung bình (đo: 17 so với 21), dùng khi chữ chưa đổi
    lines: dict[int, tuple[str, float]] = field(default_factory=dict)

    def rate(self, i: int, text: str = "") -> float:
        got = self.lines.get(i)
        return got[1] if got and (not text or got[0] == _norm(text)) else self.cps

    def spoken(self, text: str, i: int = -1) -> float:
        return max(self.floor, len(_norm(text)) / self.rate(i, text))

    def need(self, i: int, text: str) -> float:
        """Mức nén cần để câu vừa khung (×)."""
        return self.spoken(text, i) / max(0.1, self.slots[i])

    def over(self, i: int, text: str) -> float:
        """Giây còn tràn khi đã nén tối đa."""
        return max(0.0, self.spoken(text, i) / self.cap - self.slots[i])

    def budget(self, i: int, text: str = "") -> int:
        """Số ký tự tối đa để đọc vừa khung mà chưa phải đọc nhanh (``text``: chữ đang có — chưa đổi từ lúc đọc thì
        theo tốc độ đọc đo được của chính câu đó)."""
        return int(self.rate(i, text) * self.slots[i] * (self.fast - 0.03))

    def too_short(self, i: int) -> bool:
        """Khung quá ngắn: một chữ cũng không đọc kịp (vd câu 0,3 s bị tách đôi) — rút gọn kiểu gì cũng không vừa."""
        return self.floor / self.cap > self.slots[i] + OVER


def loose_lines(report: dict[int, dict] | None, texts: list[str] | None = None) -> dict[int, tuple[float, int]]:
    """Câu giọng đọc xong sớm, để trống trong lúc người nói gốc còn nói → {chỉ số: (giây trống, số ký tự nên có)}. Trống
    tính tới khi người nói gốc hết câu (``phu_de_het`` trong báo cáo), không quá mốc câu sau trừ ``KEEP_PAUSE`` — chỗ
    video gốc tự ngừng nói không tính. Nói đầy đủ hơn tối đa +40 %, ít nhất +8 ký tự mới đáng sửa. ``texts``: chỉ tính
    câu giọng đọc đúng chữ đang thấy."""
    rows = sorted((report or {}).items(), key=lambda x: float(x[1].get("bat_dau") or 0))
    out: dict[int, tuple[float, int]] = {}
    for k, (i, r) in enumerate(rows):
        try:
            s, e, end = float(r["bat_dau"]), float(r["ket_thuc"]), float(r["phu_de_het"])
            nxt = float(rows[k + 1][1]["bat_dau"]) - KEEP_PAUSE if k + 1 < len(rows) else end
        except (KeyError, TypeError, ValueError):
            continue
        chu = _norm(r.get("chu", ""))
        if texts is not None and (i >= len(texts) or _norm(texts[i]) != chu):
            continue
        early, dur = min(end, nxt) - e, e - s
        if early < LOOSE or dur <= 0.3 or len(chu.split()) < 4:
            continue
        want = min(int(len(chu) * 1.4), int(len(chu) * (dur + early) / dur))
        if want - len(chu) >= 8:
            out[i] = (round(early, 2), want)
    return out


def fit_model(cues: list, report: dict[int, dict] | None = None, *, min_gap: float = 0.1, cap: float = 1.25,
              fast: float = FAST) -> FitModel:
    """``cues``: câu đang lồng tiếng (có ``start`` / ``duration``) · ``report``: ``voice_report`` của track giọng (khung
    thật của câu đã đọc + tốc độ đọc đo được)."""
    report = report or {}
    rates: list[float] = []
    shorts: list[float] = []
    lines: dict[int, tuple[str, float]] = {}
    for i, r in report.items():
        try:
            raw = (float(r["ket_thuc"]) - float(r["bat_dau"])) * float(r.get("tempo") or 1.0)  # thời lượng ở ×1
        except (KeyError, TypeError, ValueError):
            continue
        chu = _norm(r.get("chu", ""))
        if raw <= 0 or not chu:
            continue
        if len(chu) >= 20:
            rates.append(len(chu) / raw)
            lines[i] = (chu, len(chu) / raw)
        elif len(chu) <= 10:
            shorts.append(raw)
    measured = len(rates) >= 3
    slots: list[float] = []
    for i, c in enumerate(cues):
        try:
            k = float(report[i]["khung"]) if i in report else None
        except (KeyError, TypeError, ValueError):
            k = None
        if k is None:                                    # như ``generator.slot_for``: tới câu sau trừ khoảng nghỉ
            k = cues[i + 1].start - c.start - min_gap if i + 1 < len(cues) else c.duration
        slots.append(max(0.35, k))
    return FitModel(slots, statistics.median(rates) if measured else DEFAULT_CPS,
                    statistics.median(shorts) if shorts else MIN_SPOKEN, cap, fast, measured, lines)


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def voice_report(voice: str | Path | None) -> dict[int, dict]:
    """Báo cáo từng câu của track giọng (``.cues.csv`` cạnh WAV) → {chỉ số câu trong SRT đã đọc (từ 0): hàng}."""
    if not voice:
        return {}
    try:
        with open(Path(voice).with_suffix(".cues.csv"), encoding="utf-8-sig", newline="") as f:
            return {int(r["cau"]) - 1: r for r in csv.DictReader(f) if str(r.get("cau", "")).strip().isdigit()}
    except (OSError, ValueError, KeyError, csv.Error):
        return {}


def brief_pronouns(brief: str) -> dict[str, set[str]]:
    """Hồ sơ kịch bản → {người nói: đại từ được dùng}, từ dòng «- Người 1 (…): xưng «tao», gọi người nghe «mày»»."""
    out: dict[str, set[str]] = {}
    people = False
    for ln in (brief or "").splitlines():
        t = ln.strip()
        if not t:
            continue
        if not t.startswith("-"):
            people = t.startswith("Người nói")
            continue
        m = _PERSON.match(t) if people else None
        if not m:
            continue
        words = {w.strip().lower() for q in _QUOTED.findall(m.group("rest")) for w in re.split(r"[/,;]| hoặc ", q)
                 if w.strip()}
        if words:
            out[m.group("name").strip()] = words
    return out


def pronouns_in(text: str) -> set[str]:
    """Đại từ xưng hô (``_PRONOUNS``) có trong câu — bỏ chỗ là danh từ («bạn mày», «bạn bè», «cậu ấy»)."""
    low = (text or "").lower()
    found: set[str] = set()
    for w in _PRONOUNS:
        for m in re.finditer(rf"(?<!\w){w}(?!\w)", low):
            after = _NOUN_AFTER.get(w)
            if after and after.match(low[m.end():].lstrip()):
                continue
            found.add(w)
            break
    return found


def review_flags(texts: list[str], sources: list[str] | None = None, *, cjk_ok: bool = True,
                 report: dict[int, dict] | None = None, brief: str = "", speakers: list[str] | None = None,
                 base_speed: float = 1.0, fit: FitModel | None = None) -> dict[int, list[Flag]]:
    """Câu cần nghe lại → {chỉ số câu: [lý do]}. ``texts``: chữ đang dùng (bản dịch, hoặc lời gốc khi chưa dịch) ·
    ``sources``: lời gốc cùng số câu (chỉ khi ``texts`` là bản dịch) · ``cjk_ok``: ngôn ngữ đích dùng chữ Hán / Kana /
    Hangul · ``report``: ``voice_report`` của track giọng · ``speakers``: nhãn «Người 1 · Nam» từng câu · ``fit``: có
    lồng tiếng — câu chưa đọc theo chữ đang thấy thì ước lượng có vừa khung không."""
    pron = brief_pronouns(brief)
    every = set().union(*pron.values()) if pron else set()
    fast = max(FAST, base_speed * 1.08)
    loose = loose_lines(report, texts) if report else {}
    out: dict[int, list[Flag]] = {}
    for i, raw in enumerate(texts):
        t = _norm(raw)
        src = _norm(sources[i]) if sources and i < len(sources) else ""
        flags: list[Flag] = []
        if not t:
            if src:
                flags.append(Flag("empty", "trống", "Câu dịch trống — câu này không có phụ đề, không có giọng."))
        elif sources is not None and not cjk_ok and _CJK.search(t):
            flags.append(Flag("src", "còn chữ gốc", "Bản dịch còn chữ gốc (Hán / Kana / Hangul) — giọng đọc bỏ qua chỗ đó, "
                                                   "phụ đề vẫn hiện."))
        elif src and t == src:
            flags.append(Flag("src", "chưa dịch", "Câu này còn y nguyên lời gốc — AI chưa dịch được."))
        if pron and t:
            who = speakers[i].split(" · ")[0].strip() if speakers and i < len(speakers) and speakers[i] else ""
            allowed = pron.get(who) or every
            bad = sorted(w for w in pronouns_in(t) if w not in allowed)
            if bad:
                flags.append(Flag("pronoun", f"xưng «{bad[0]}»",
                                  f"Xưng hô «{', '.join(bad)}» khác hồ sơ kịch bản ({who or 'cả video'}: "
                                  f"{' / '.join(sorted(allowed))})."))
        r = (report or {}).get(i)
        voiced = bool(r and t and _norm(r.get("chu", "")) == t)
        if fit is not None and t and not voiced and i < len(fit.slots) and not fit.too_short(i):
            # câu vừa sửa / chưa có giọng: ước lượng theo tốc độ đọc của video (chưa đo được thì chỉ báo câu tràn hẳn)
            over, need = fit.over(i, t), fit.need(i, t)
            if over > OVER:
                flags.append(Flag("fast", f"dài ~+{over:.1f}s",
                                  f"Ước lượng (giọng chưa đọc theo chữ này): dài hơn khung ~{over:.1f}s dù nén tối đa — "
                                  f"bấm ✂ để AI rút gọn (≤ {fit.budget(i, t)} ký tự), hoặc tạo lại giọng để biết chính "
                                  "xác."))
            elif fit.measured and need >= fit.fast:
                flags.append(Flag("fast", "sẽ đọc nhanh",
                                  f"Ước lượng: phải đọc nhanh ~×{need:.2f} cho vừa khung — bấm ✂ để AI rút gọn "
                                  f"(≤ {fit.budget(i, t)} ký tự)."))
        if i in loose:
            early, want = loose[i]
            flags.append(Flag("gap", f"xong sớm {early:.1f}s",
                              f"Giọng đọc xong trước khi người nói gốc hết câu {early:.1f}s (để trống) — bấm «Tự sửa» để AI "
                              f"nói đầy đủ hơn (~{want} ký tự) cho kín chỗ.", want))
        if voiced and r:                                       # giọng đọc đúng chữ đang thấy (chưa sửa sau khi đọc)
            problems = str(r.get("loi_qc") or "").strip()
            tries = int(float(r.get("so_lan_doc") or 1))
            if problems:
                flags.append(Flag("voice", "đọc sai", f"Giọng đọc chưa chuẩn sau {tries} lần đọc: {problems}."))
            if str(r.get("du_phong", "")).strip().lower() == "true":
                flags.append(Flag("voice", "giọng dự phòng", "Giọng chính lỗi ở câu này — đang đọc tạm bằng giọng dự phòng."))
            tempo, over = float(r.get("tempo") or 0), float(r.get("tran") or 0)
            if over > OVER:
                flags.append(Flag("fast", f"tràn +{over:.1f}s",
                                  f"Đọc dài hơn khung {over:.1f}s dù đã nén ×{tempo:.2f} — nên rút gọn câu."))
            elif tempo >= fast:
                flags.append(Flag("fast", f"đọc nhanh ×{tempo:.2f}",
                                  f"Phải đọc nhanh ×{tempo:.2f} cho vừa khung — nghe xem còn rõ không, hoặc rút gọn câu."))
        if flags:
            out[i] = flags
    return out


def fix_tasks(flags: dict[int, list[Flag]], texts: list[str], fit: FitModel | None = None,
              has_source: bool = True) -> tuple[dict[int, tuple[list[str], int | None]], list[int]]:
    """Câu ⚠ AI sửa được → ({chỉ số: ([việc], giới hạn ký tự)}, [câu bỏ qua: khung quá ngắn]). Việc: ``short`` (rút
    gọn cho vừa khung) · ``long`` (giọng xong sớm: nói đầy đủ hơn, tới ``budget`` ký tự) · ``translate`` (còn chữ gốc /
    chưa dịch / trống) · ``pronoun:<lý do>`` · ``voice`` (giọng đọc sai chữ — đổi cách nói). Lỗi âm thanh thuần (vào đột
    ngột, giọng dự phòng) bỏ qua: sửa chữ không giúp gì."""
    tasks: dict[int, tuple[list[str], int | None]] = {}
    skipped: list[int] = []
    for i, fs in sorted(flags.items()):
        todo: list[str] = []
        budget = None
        for f in fs:
            if f.kind == "fast" and fit is not None and i < len(fit.slots) and "short" not in todo:
                t = _norm(texts[i] if i < len(texts) else "")
                b = fit.budget(i, t)
                if fit.too_short(i) or b < 6 or b >= len(t):
                    skipped.append(i)                    # khung quá ngắn / câu đã ngắn hơn giới hạn: rút nữa mất nghĩa
                    continue
                todo.append("short")
                budget = b
            elif f.kind == "gap" and f.budget and "long" not in todo and "short" not in todo:
                todo.append("long")
                budget = f.budget
            elif f.kind in ("src", "empty") and has_source and "translate" not in todo:
                todo.append("translate")
            elif f.kind == "pronoun":
                todo.append(f"pronoun:{f.detail}")
            elif f.kind == "voice" and any(p in f.detail for p in _WORD_PROBLEMS) and "voice" not in todo:
                todo.append("voice")
        if todo:
            tasks[i] = (todo, budget)
    return tasks, sorted(set(skipped) - set(tasks))


def summary(flags: dict[int, list[Flag]]) -> str:
    """«6 câu cần nghe lại: 3 giọng đọc, 2 đọc nhanh, 1 xưng hô»."""
    if not flags:
        return ""
    count: dict[str, int] = {}
    for fs in flags.values():
        for k in dict.fromkeys(f.kind for f in fs):
            count[k] = count.get(k, 0) + 1
    parts = [f"{n} {_KIND_NAMES[k]}" for k, n in sorted(count.items(), key=lambda x: -x[1])]
    return f"{len(flags)} câu cần nghe lại: " + ", ".join(parts)
