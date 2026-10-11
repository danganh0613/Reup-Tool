"""Dịch / rà soát phụ đề bằng AI bên ngoài (ChatGPT, Gemini, Claude, DeepSeek… trên web hoặc API riêng của bạn):
xuất SRT kèm lời dặn → AI ngoài dịch / sửa → nhập lại SRT bản dịch.

Không lệch video: lúc nhập luôn dùng MỐC THỜI GIAN GỐC của từng câu — AI đổi mốc, gộp / tách / bỏ sót câu cũng không
làm phụ đề trôi. Ghép câu theo thứ tự (đủ số câu) → theo số thứ tự SRT → theo mốc thời gian; câu thiếu giữ bản dịch cũ
(chưa có thì để lời gốc) và báo rõ câu nào. File xuất là UTF-8 có BOM — mở bằng Notepad / tải lên web vẫn đọc đúng chữ Hán.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.srt_utils import Cue, format_ts, parse_ts

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_CJK_TARGETS = ("中文", "日本語", "한국어")
BOM = "﻿"


_VI_NAME = {"vi": "tiếng Việt", "en": "tiếng Anh", "zh": "tiếng Trung", "ja": "tiếng Nhật", "ko": "tiếng Hàn",
            "fr": "tiếng Pháp", "de": "tiếng Đức", "es": "tiếng Tây Ban Nha", "th": "tiếng Thái", "id": "tiếng Indonesia",
            "ru": "tiếng Nga"}
_TARGET_CODE = {"Tiếng Việt": "vi", "English": "en", "中文": "zh", "日本語": "ja", "한국어": "ko", "Français": "fr",
                "Deutsch": "de", "Español": "es", "ไทย": "th", "Bahasa Indonesia": "id", "Русский": "ru"}


def source_name(code: str, sample: str = "") -> str:
    """Tên ngôn ngữ gốc cho lời dặn: theo «Ngôn ngữ video»; «tự nhận diện» → đoán theo chữ (Kana / Hangul / Hán)."""
    if code in _VI_NAME:
        return _VI_NAME[code]
    if re.search(r"[぀-ヿ]", sample):
        return "tiếng Nhật"
    if re.search(r"[가-힯]", sample):
        return "tiếng Hàn"
    return "tiếng Trung" if _CJK.search(sample) else "gốc"


def target_name(target: str) -> str:
    return _VI_NAME.get(_TARGET_CODE.get(target, ""), target)


def _srt(rows: list[tuple[float, float, str]]) -> str:
    return BOM + "\n".join(f"{i}\n{format_ts(a)} --> {format_ts(b)}\n{t.strip()}\n"
                           for i, (a, b, t) in enumerate(rows, 1))


def export_source(src: list[Cue]) -> str:
    """SRT lời gốc (để AI ngoài dịch)."""
    return _srt([(c.start, c.end, c.plain) for c in src])


def export_bilingual(src: list[Cue], tr: list[Cue]) -> str:
    """SRT song ngữ (để AI ngoài rà soát bản dịch): mỗi câu dòng 1 lời gốc, dòng 2 bản dịch — AI thấy cả chữ gốc để
    kiểm nghĩa. Bản dịch ghép với lời gốc theo thứ tự (cùng số câu) hoặc theo mốc thời gian."""
    texts = [c.plain for c in tr] if len(tr) == len(src) else [_by_time(c, tr) for c in src]
    return _srt([(c.start, c.end, f"{c.plain}\n{t}") for c, t in zip(src, texts)])


def _by_time(c: Cue, tr: list[Cue]) -> str:
    best = max(tr, key=lambda t: min(c.end, t.end) - max(c.start, t.start), default=None)
    return best.plain if best is not None and min(c.end, best.end) > max(c.start, best.start) else ""


def prompt(kind: str, n: int, src_lang: str, tgt_lang: str, style: str = "", glossary: str = "") -> str:
    """Lời dặn dán cho AI ngoài cùng file. ``kind``: "translate" | "review"."""
    keep = (f"- Giữ NGUYÊN số thứ tự và mốc thời gian của từng câu. Không gộp, không tách, không thêm, không bỏ câu nào "
            f"— trả về đủ {n} câu (để phụ đề khớp video).\n"
            "- Mỗi câu dịch đủ ý, độ dài xấp xỉ câu gốc (được đọc lồng tiếng đúng thời gian của câu).\n"
            "- Trả về đúng định dạng SRT, toàn bộ trong MỘT khối code, không giải thích gì thêm.\n")
    if kind == "review":
        head = (f"File SRT đính kèm là phụ đề video, {n} câu. Mỗi câu có 2 dòng: dòng 1 là lời gốc {src_lang}, dòng 2 là "
                f"bản dịch {tgt_lang}.\nHãy rà soát bản dịch: sửa câu sai nghĩa so với lời gốc, sai chính tả, lỗi kỹ "
                f"thuật, xưng hô / tên gọi không nhất quán, câu đọc chưa tự nhiên. Câu đã ổn thì giữ nguyên.\n"
                f"Trả về SRT chỉ còn dòng bản dịch {tgt_lang} (bỏ dòng lời gốc).\nYêu cầu bắt buộc:\n")
    else:
        head = (f"File SRT đính kèm là phụ đề video, {n} câu, lời gốc {src_lang}. Hãy dịch sang {tgt_lang}.\n"
                "Yêu cầu bắt buộc:\n")
    gl = "\n".join(ln.strip() for ln in (glossary or "").splitlines() if "=" in ln)
    return (head + keep
            + (f"\nPhong cách dịch:\n{style.strip()}\n" if style.strip() else "")
            + (f"\nTên riêng phải dịch đúng như sau (gốc=dịch):\n{gl}\n" if gl else ""))


# ── Nhập lại ──────────────────────────────────────────────────────────────

@dataclass
class Imported:
    texts: list[str]                                # đủ số câu gốc
    got: int = 0                                    # số câu AI trả về
    matched: int = 0                                # số câu gốc nhận được bản dịch
    how: str = ""                                   # ghép theo thứ tự / số thứ tự / mốc thời gian
    missing: list[int] = field(default_factory=list)          # câu (1-based) không có bản dịch
    untranslated: list[int] = field(default_factory=list)     # câu còn chữ Hán / Nhật / Hàn
    retimed: int = 0                                # câu AI đổi mốc thời gian (đã bỏ qua, giữ mốc gốc)

    def summary(self) -> str:
        bits = [f"nhập {self.matched}/{len(self.texts)} câu ({self.how}), giữ mốc thời gian gốc"]
        if self.retimed:
            bits.append(f"AI đổi mốc ở {self.retimed} câu — đã bỏ qua")
        if self.missing:
            bits.append(f"thiếu câu {_nums(self.missing)} (giữ bản cũ / lời gốc)")
        if self.untranslated:
            bits.append(f"câu {_nums(self.untranslated)} còn chữ gốc chưa dịch")
        return " · ".join(bits)


def _nums(xs: list[int]) -> str:
    return ", ".join(str(x) for x in xs[:12]) + ("…" if len(xs) > 12 else "")


def _blocks(text: str) -> list[tuple[int | None, float | None, float | None, str]]:
    """Câu AI trả về: SRT (lấy cả số thứ tự — parse_srt của app đánh số lại) hoặc danh sách «1. câu» / «1| câu»."""
    text = text.lstrip(BOM).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("```"))
    out: list[tuple[int | None, float | None, float | None, str]] = []
    if "-->" in text:
        for block in re.split(r"\n\s*\n", text.strip()):
            lines = [ln.strip() for ln in block.split("\n")]
            ti = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
            if ti is None:
                continue
            try:
                a, b = (parse_ts(x) for x in lines[ti].split("-->", 1))
            except ValueError:
                continue
            num = int(lines[ti - 1]) if ti > 0 and lines[ti - 1].isdigit() else None
            out.append((num, a, b, "\n".join(ln for ln in lines[ti + 1:] if ln)))
        return out
    for ln in text.split("\n"):
        m = re.match(r"^\s*\[?(\d+)\]?\s*[.:|)\]-]\s*(.+)$", ln)
        if m:
            out.append((int(m.group(1)), None, None, m.group(2).strip()))
    return out


def _clean(text: str, target: str) -> str:
    """Bỏ dòng lời gốc (AI trả cả song ngữ) khi dịch sang ngôn ngữ không dùng chữ Hán / Nhật / Hàn."""
    if target in _CJK_TARGETS:
        return text.strip()
    lines = [ln for ln in text.split("\n") if ln.strip()]
    keep = [ln for ln in lines if not _CJK.search(ln)]
    return " ".join(keep or lines).strip()


def import_translation(src: list[Cue], text: str, target: str, previous: list[str] | None = None) -> Imported:
    """Bản dịch AI ngoài → đúng số câu gốc, mốc thời gian gốc. ``previous``: bản dịch đang có (câu thiếu giữ bản này)."""
    got = _blocks(text)
    n = len(src)
    pick: dict[int, list[str]] = {}
    nums = [g[0] for g in got]
    timed = bool(got) and all(g[1] is not None for g in got)
    numbered = bool(got) and all(x is not None for x in nums) and len(set(nums)) == len(nums) and \
        all(1 <= x <= n for x in nums)
    if numbered and timed:          # AI gộp / bỏ câu rồi đánh số lại → số thứ tự không còn khớp mốc thời gian
        numbered = sum(abs(g[1] - src[g[0] - 1].start) < 1.0 for g in got) >= 0.8 * len(got)
    if len(got) == n:
        how = "theo thứ tự"
        pairs = list(enumerate(got))
    elif numbered:
        how = "theo số thứ tự"
        pairs = [(g[0] - 1, g) for g in got]
    elif timed:
        how = "theo mốc thời gian"
        pairs = []
        for g in got:
            a, b = g[1], g[2]
            k = max(range(n), key=lambda i: min(b, src[i].end) - max(a, src[i].start))
            if min(b, src[k].end) - max(a, src[k].start) > -0.3:
                pairs.append((k, g))
    else:
        how = "theo thứ tự (thiếu câu ở cuối)"
        pairs = list(enumerate(got[:n]))
    retimed = 0
    for k, (_num, a, _b, t) in pairs:
        t = _clean(t, target)
        if t:
            pick.setdefault(k, []).append(t)
        if a is not None and abs(a - src[k].start) > 0.3 and how != "theo mốc thời gian":
            retimed += 1
    texts, missing, untranslated = [], [], []
    for i, c in enumerate(src):
        if i in pick:
            t = " ".join(pick[i])
            if target not in _CJK_TARGETS and _CJK.search(t):
                untranslated.append(i + 1)
        else:
            missing.append(i + 1)
            t = (previous[i] if previous and i < len(previous) and previous[i].strip() else c.plain)
        texts.append(t)
    return Imported(texts, len(got), n - len(missing), how, missing, untranslated, retimed)
