"""Soát lỗi từng câu — để người dùng chỉ phải nghe các câu cần xem thay vì cả video:

- giọng đọc chưa chuẩn: Whisper nghe lại vẫn lệch sau khi đã đọc lại (``loi_qc`` trong báo cáo từng câu của track
  giọng ``.cues.csv``), hoặc câu phải đọc tạm bằng giọng dự phòng;
- đọc quá nhanh (nén gần trần cho vừa khung) / vẫn tràn khung;
- câu dịch còn chữ gốc (Hán / Kana / Hangul), chưa dịch (y nguyên lời gốc) hoặc trống;
- xưng hô lệch hồ sơ kịch bản (vd hồ sơ chốt «tao – mày» mà câu dùng «tôi» / «bạn»).

Chỉ đọc file có sẵn (SRT, ``.cues.csv`` cạnh WAV giọng, hồ sơ kịch bản) — không gọi AI, không tốn lượt."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
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
_PERSON = re.compile(r"^-\s*(?P<name>[^(:]+?)\s*(?:\([^)]*\))?\s*:(?P<rest>.*)$")
_QUOTED = re.compile(r"«([^»]+)»")


@dataclass(frozen=True)
class Flag:
    kind: str        # voice · fast · src · pronoun · empty
    short: str       # chữ ngắn trên dòng câu
    detail: str      # giải thích (tooltip / báo cáo)


_KIND_NAMES = {"voice": "giọng đọc", "fast": "đọc nhanh", "src": "chưa dịch", "pronoun": "xưng hô", "empty": "trống"}


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
                 base_speed: float = 1.0) -> dict[int, list[Flag]]:
    """Câu cần nghe lại → {chỉ số câu: [lý do]}. ``texts``: chữ đang dùng (bản dịch, hoặc lời gốc khi chưa dịch) ·
    ``sources``: lời gốc cùng số câu (chỉ khi ``texts`` là bản dịch) · ``cjk_ok``: ngôn ngữ đích dùng chữ Hán / Kana /
    Hangul · ``report``: ``voice_report`` của track giọng · ``speakers``: nhãn «Người 1 · Nam» từng câu."""
    pron = brief_pronouns(brief)
    every = set().union(*pron.values()) if pron else set()
    fast = max(FAST, base_speed * 1.08)
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
        if r and t and _norm(r.get("chu", "")) == t:          # giọng đọc đúng chữ đang thấy (chưa sửa sau khi đọc)
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
