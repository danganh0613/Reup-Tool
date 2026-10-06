"""Đọc/ghi SRT và các thao tác trên danh sách cue."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_TS_RE = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")
_TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")


@dataclass
class Cue:
    index: int
    start: float
    end: float
    text: str
    meta: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def plain(self) -> str:
        """Văn bản một dòng, bỏ thẻ HTML/ASS — dùng cho TTS và dịch."""
        return " ".join(_TAG_RE.sub("", self.text).split())


def parse_ts(s: str) -> float:
    m = _TS_RE.search(s)
    if not m:
        raise ValueError(f"Mốc thời gian sai: {s!r}")
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")) / 1000


def format_ts(t: float, sep: str = ",") -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    mi, ms = divmod(ms, 60_000)
    se, ms = divmod(ms, 1000)
    return f"{h:02d}:{mi:02d}:{se:02d}{sep}{ms:03d}"


def parse_srt(text: str) -> list[Cue]:
    """Parser rộng lượng: chấp nhận thiếu số thứ tự, CRLF, BOM, dòng trống thừa."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    blocks = re.split(r"\n{2,}", text.strip())
    for block in blocks:
        lines = [ln for ln in block.split("\n")]
        if not lines:
            continue
        # tìm dòng thời gian
        ti = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ti is None:
            continue
        try:
            left, right = lines[ti].split("-->", 1)
            start = parse_ts(left)
            end = parse_ts(right)
        except ValueError:
            continue
        body = "\n".join(ln.rstrip() for ln in lines[ti + 1:]).strip()
        if end < start:
            end = start
        cues.append(Cue(index=len(cues) + 1, start=start, end=end, text=body))
    return cues


def load_srt(path: str | Path) -> list[Cue]:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1258", "cp1252"):
        try:
            return parse_srt(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return parse_srt(raw.decode("utf-8", "replace"))


def to_srt(cues: list[Cue]) -> str:
    out: list[str] = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{format_ts(c.start)} --> {format_ts(c.end)}\n{c.text.strip()}\n")
    return "\n".join(out) + ("\n" if out else "")


def save_srt(cues: list[Cue], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_srt(cues), encoding="utf-8")
    return p


def shift(cues: list[Cue], delay: float) -> list[Cue]:
    if abs(delay) < 1e-6:
        return cues
    out: list[Cue] = []
    for c in cues:
        s, e = c.start + delay, c.end + delay
        if e <= 0:
            continue
        out.append(Cue(c.index, max(0.0, s), max(0.0, e), c.text, dict(c.meta)))
    return out


def merge_bilingual(primary: list[Cue], secondary: list[Cue]) -> list[Cue]:
    """Ghép 2 danh sách cùng thời gian: dòng chính trên, dòng phụ dưới (đánh dấu bằng meta)."""
    sec_by_start = {round(c.start, 2): c for c in secondary}
    out: list[Cue] = []
    for i, c in enumerate(primary):
        s = sec_by_start.get(round(c.start, 2))
        if s is None and i < len(secondary):
            s = secondary[i]
        out.append(Cue(c.index, c.start, c.end, c.text, {"secondary": s.text if s else ""}))
    return out


LINE_CHARS = 42


_CJK_RE = re.compile(r"[\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3100-\u312f\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")
_CJK_BREAK_AFTER = "，。、；：？！…—」』）》,.;:?!)"


def is_cjk(text: str) -> bool:
    """Chủ yếu là chữ Trung/Nhật/Hàn (không dùng khoảng trắng tách từ)."""
    letters = [c for c in text if c.isalnum() or _CJK_RE.match(c)]
    return bool(letters) and sum(1 for c in letters if _CJK_RE.match(c)) >= len(letters) * 0.5


def display_width(text: str) -> int:
    """Độ rộng hiển thị ước lượng: chữ CJK chiếm ~2 ô so với chữ Latin."""
    return sum(2 if _CJK_RE.match(c) else 1 for c in text)


def wrap_lines(text: str, line_chars: int = LINE_CHARS) -> str:
    """Chia văn bản một dòng thành tối đa 2 dòng cân đối: ngắt tại khoảng trắng gần giữa; chữ CJK (không có
    khoảng trắng) ngắt sau dấu câu gần giữa hoặc đúng giữa."""
    text = " ".join(text.split())
    if display_width(text) <= line_chars:
        return text
    if is_cjk(text) and " " not in text:
        mid = len(text) // 2
        cands = [i + 1 for i, ch in enumerate(text[:-1]) if ch in _CJK_BREAK_AFTER]
        cut = min(cands, key=lambda i: abs(i - mid)) if cands and min(abs(i - mid) for i in cands) <= len(text) // 4 else mid
        return text[:cut].strip() + "\n" + text[cut:].strip()
    mid = len(text) // 2
    best = -1
    for i, ch in enumerate(text):
        if ch == " " and (best < 0 or abs(i - mid) < abs(best - mid)):
            best = i
    if best <= 0:
        return text
    return text[:best].strip() + "\n" + text[best + 1:].strip()


def ensure_min_duration(cues: list[Cue], min_dur: float = 1.0, gap: float = 0.05, lead: float = 0.3) -> list[Cue]:
    """Câu hiện quá ngắn (phụ đề dời theo giọng: câu ngắn chỉ hiện ~0,5 s) → kéo dài tới ``min_dur`` giây,
    nhưng không chồng sang câu sau (cách ≥ ``gap``) và không bao giờ rút ngắn câu. Câu sau sát quá thì cho hiện
    sớm hơn tối đa ``lead`` giây (không chồng câu trước)."""
    out: list[Cue] = []
    for i, c in enumerate(cues):
        start, end = c.start, c.end
        if end - start < min_dur:
            limit = cues[i + 1].start - gap if i + 1 < len(cues) else float("inf")
            end = max(end, min(start + min_dur, limit))
            if end - start < min_dur:
                floor = out[-1].end + gap if out else 0.0
                start = min(start, max(end - min_dur, start - lead, floor))
        out.append(Cue(c.index, start, end, c.text, dict(c.meta)))
    return out


def cue_at(cues: list[Cue], t: float) -> Cue | None:
    for c in cues:
        if c.start <= t <= c.end:
            return c
    return None


def stats(cues: list[Cue]) -> str:
    if not cues:
        return "0 cue"
    total = sum(len(c.plain) for c in cues)
    return f"{len(cues)} cue · {total} ký tự · {format_ts(cues[-1].end, '.')[:8]}"
