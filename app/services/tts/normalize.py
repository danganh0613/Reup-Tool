"""Chuẩn hoá chữ trước khi đưa vào TTS.

Chạy cho mọi engine (gọi từ ``synthesize_cached``):
- mọi ngôn ngữ: bỏ markdown / emoji / ký hiệu trang trí, gộp dấu câu lặp («??», «?!», «!!!», «...»), từ điển phát âm;
- chữ tiếng Việt: sửa vài cách viết bộ đọc số của VieNeu / Gemini hay đọc sai («99k», «1080p», «-30%», «#1», «1.5tr»…).
Số, ngày, %, đơn vị thông thường (199.000đ, 5/10/2026, 20x30cm, 36h…) VieNeu tự đọc đúng → không đụng.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from pathlib import Path

from app.config.paths import PRON_FILE

logger = logging.getLogger(__name__)

#: câu dài hơn mức này thì engine không tự chia câu (Gemini) sẽ được tách ra đọc từng đoạn
MAX_CHARS = 200

_VI_CHARS = re.compile(r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0000FE0F\U0000200D"
                    "\U0001F1E6-\U0001F1FF\U000020E3\U0000E000-\U0000F8FF]")
_MD = [
    (re.compile(r"\[([^\]\n]+)\]\((?:https?://|www\.)[^)\s]*\)"), r"\1"),     # [chữ](link) → chữ
    (re.compile(r"(\*\*|__|~~)(.+?)\1"), r"\2"),                              # **đậm** __đậm__ ~~gạch~~
    (re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])"), r"\1"),  # *nghiêng*
    (re.compile(r"`+([^`\n]*)`+"), r"\1"),                                     # `mã`
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.M), ""),                              # # tiêu đề
    (re.compile(r"^\s*[-*•●▪►]\s+", re.M), ""),                                # gạch đầu dòng
]
_PUNCT = [
    (re.compile(r"\.{3,}"), "…"),
    (re.compile(r"[?!]*\?[?!]*"), "?"),          # «??», «?!», «!?» → «?» (giữ ngữ điệu hỏi)
    (re.compile(r"!{2,}"), "!"),
    (re.compile(r",{2,}"), ","),
    (re.compile(r"…{2,}"), "…"),
    (re.compile(r"\s+([,.;:!?…])"), r"\1"),
    (re.compile(r"([,;:])(?=[^\s\d])"), r"\1 "),
]
# tiếng Việt: chỉ những chỗ bộ chuẩn hoá của VieNeu đọc sai (đã thử từng mẫu)
_VI = [
    (re.compile(r"(?<![\w.,])#(\d+)\b"), r"số \1"),                                       # top #1 → số 1 (không phải «thăng»)
    (re.compile(r"(?<![\w.,])#(?=\w)"), ""),                                               # #review → review
    (re.compile(r"(?<![\w.,])@(?=\w)"), ""),                                               # @kenh → kenh
    (re.compile(r"\b(tăng|giảm)\s*[+-]\s?(?=\d)", re.I), r"\1 "),                           # «giảm -30%» → bỏ dấu thừa
    (re.compile(r"(?<![\w.,\d])-\s?(\d+(?:[.,]\d+)?)\s?%"), r"giảm \1%"),                # sale -30% → giảm 30% (không «âm»)
    (re.compile(r"(?<![\w.,\d])\+\s?(\d+(?:[.,]\d+)?)\s?%"), r"tăng \1%"),               # +15% → tăng 15% (không «cộng»)
    (re.compile(r"\b(\d+)[.,](\d+)\s?(?:tr|TR)\b"), r"\1 phẩy \2 triệu"),                 # 1.5tr → một phẩy năm triệu
    (re.compile(r"\b(\d+)\s?(?:tr|TR)\b(?!\d)"), r"\1 triệu"),                            # 2tr → hai triệu
    (re.compile(r"\b(\d{2,}|\d+[.,]\d+)\s?[kK]\b"), r"\1 nghìn"),                         # 99k / 50K → nghìn (không «ca»)
    (re.compile(r"\b(\d{3,4})[pP]\b"), r"\1 pê"),                                          # 1080p → … pê (không «phút»)
    (re.compile(r"(?<![\w])x(\d+)\b"), r"nhân \1"),                                        # x2 tốc độ → nhân 2 (X5 viết hoa = tên máy, giữ)
    (re.compile(r"\b(\d+)[xX](?![\w])"), r"\1 lần"),                                       # zoom 5x → 5 lần
]

_pron_lock = threading.Lock()
_pron_cache: tuple[float, dict[str, str], re.Pattern | None] = (-1.0, {}, None)


def is_vietnamese(text: str) -> bool:
    """Có chữ cái tiếng Việt (dấu / ă â đ ê ô ơ ư) → coi là tiếng Việt."""
    return bool(_VI_CHARS.search(text or ""))


def load_pronunciations(path: Path | None = None) -> tuple[dict[str, str], re.Pattern | None]:
    """Từ điển phát âm JSON {"từ": "cách đọc"} (khoá bắt đầu bằng «_» là ghi chú). Đọc lại khi file đổi."""
    global _pron_cache
    p = Path(path or PRON_FILE)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}, None
    with _pron_lock:
        if _pron_cache[0] == mtime:
            return _pron_cache[1], _pron_cache[2]
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Từ điển phát âm %s lỗi: %s", p, exc)
            raw = {}
        words = {str(k).strip(): str(v).strip() for k, v in (raw.items() if isinstance(raw, dict) else [])
                 if str(k).strip() and not str(k).startswith("_") and str(v).strip()}
        pat = None
        if words:
            keys = sorted(words, key=len, reverse=True)           # khớp cụm dài trước («Redmi Buds» trước «Redmi»)
            pat = re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])", re.I)
        _pron_cache = (mtime, {k.lower(): v for k, v in words.items()}, pat)
        return _pron_cache[1], _pron_cache[2]


def apply_pronunciations(text: str, path: Path | None = None) -> str:
    words, pat = load_pronunciations(path)
    if not pat:
        return text
    return pat.sub(lambda m: words.get(m.group(1).lower(), m.group(1)), text)


def clean_text(text: str) -> str:
    """Phần dùng cho mọi ngôn ngữ: markdown, emoji, dấu câu lặp, khoảng trắng."""
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("\r", " ").replace(" ", " ")
    for pat, rep in _MD:
        t = pat.sub(rep, t)
    t = _EMOJI.sub(" ", t)
    for pat, rep in _PUNCT:
        t = pat.sub(rep, t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", t)).strip()


def normalize_vi_text(text: str, pron_path: Path | None = None) -> str:
    """Chữ đưa vào TTS: từ điển phát âm → làm sạch → (tiếng Việt) sửa cách viết hay bị đọc sai."""
    t = clean_text(apply_pronunciations(text or "", pron_path))
    if is_vietnamese(t):
        for pat, rep in _VI:
            t = pat.sub(rep, t)
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t or (text or "").strip()


def spoken(text: str) -> str:
    """Chữ «đọc thành tiếng» (số → chữ…) bằng bộ chuẩn hoá của VieNeu nếu có — để đếm số tiếng."""
    try:
        from vieneu_utils.phonemize_text import _get_normalizer

        return _get_normalizer().normalize(text)
    except Exception:
        return text


def syllable_count(text: str) -> int:
    """Số tiếng (âm tiết) sẽ đọc: tiếng Việt mỗi chữ một tiếng; số chưa đổi được thì ước 3 tiếng / cụm số."""
    t = spoken(normalize_vi_text(text)) if is_vietnamese(text) else text
    n = 0
    for w in re.findall(r"[^\W_]+", t):
        n += 3 if w.isdigit() else (max(1, len(re.findall(r"[aeiouyăâêôơư]+", w.lower()))) if not is_vietnamese(t) else 1)
    return n


def split_long(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Tách câu dài thành đoạn ≤ ``max_chars``: ưu tiên ranh giới câu, rồi dấu phẩy, cuối cùng khoảng trắng."""
    t = (text or "").strip()
    if len(t) <= max_chars:
        return [t] if t else []
    out: list[str] = []
    for sent in re.split(r"(?<=[.!?…])\s+", t):
        while len(sent) > max_chars:
            cut = max((sent.rfind(c, 0, max_chars) for c in ",;:"), default=-1)
            if cut < max_chars // 3:
                cut = sent.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            out.append(sent[:cut + 1].strip())
            sent = sent[cut + 1:].strip()
        if sent:
            if out and len(out[-1]) + 1 + len(sent) <= max_chars:
                out[-1] = f"{out[-1]} {sent}"
            else:
                out.append(sent)
    return [s for s in out if s]
