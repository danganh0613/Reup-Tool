"""Chạy Qwen3-ASR trong môi trường riêng (models/qwen_asr_env — torch 2.8 + qwen-asr 0.0.6 + transformers 4.57 + spaCy
3.8): KHÔNG import gì của app (môi trường đó không có thư viện của app). app/services/qwen_asr.py gọi:

    <env>/python -I qwen_asr_worker.py --audio a.wav --out r.json --language zh

Làm theo VideoLingo bản 01f8434 (core/asr_backend/qwen_asr_local.py, speech_edges.py, spacy_utils/*; Apache-2.0):
Silero VAD chỉ cắt biên ngoài (đệm 1 s) · cửa sổ ≤ 180 s cắt ở 100 ms lặng nhất trong 30 s cuối · Qwen3-ASR-1.7B, ngân
sách token 13/giây + 32 · Qwen3-ForcedAligner-0.6B → mốc từng chữ, gắn dấu câu · spaCy tách câu theo dấu → dấu phẩy →
liên từ → câu > 60 token. Edit Studio thêm: căn mốc «sụp» (một chữ bị kéo > 1 s rồi ≥ 3 chữ dồn < 0,03 s/chữ — gặp ở
video thử: «保» 3 s rồi 15 chữ trong 0,08 s) → chia đều lại vùng đó; cửa sổ đọc lặp → chia đôi đọc lại một lần.
In «PROGRESS x» (0..1) và dòng log ra stdout; kết quả JSON: language, text, words[{word,start,end}], sentences[{text,tokens}]."""

import argparse
import itertools
import json
import math
import re
import string
import sys
import time
import unicodedata

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
SR = 16000
WINDOW_SECONDS, CUT_SEARCH_SECONDS = 180, 30
TOKENS_PER_SECOND, TOKEN_MARGIN, MAX_NEW_TOKENS = 13, 32, 2400
ISO_TO_QWEN = {"zh": "Chinese", "en": "English", "yue": "Cantonese", "ja": "Japanese", "ko": "Korean", "vi": "Vietnamese",
               "fr": "French", "de": "German", "es": "Spanish", "ru": "Russian", "th": "Thai", "id": "Indonesian",
               "pt": "Portuguese", "it": "Italian"}
QWEN_TO_ISO = {v: k for k, v in ISO_TO_QWEN.items()}
QWEN_TO_ISO["Cantonese"] = "zh"
_REPEATED = re.compile(r"(.{1,40}?)\1{3,}", re.S)
T0 = time.perf_counter()


def say(msg: str) -> None:
    print(f"[{time.perf_counter() - T0:5.0f}s] {msg}", flush=True)


def progress(x: float) -> None:
    print(f"PROGRESS {max(0.0, min(1.0, x)):.3f}", flush=True)


def split_windows(n: int) -> list[tuple[int, int]]:
    """Cửa sổ ≤ 180 s; mỗi lần cắt ở khung 100 ms lặng nhất trong 30 s cuối cửa sổ."""
    out, a = [], 0
    while n - a > WINDOW_SECONDS * SR:
        lo, hi = a + (WINDOW_SECONDS - CUT_SEARCH_SECONDS) * SR, a + WINDOW_SECONDS * SR
        frame = SR // 10
        seg = AUDIO[lo:hi]
        k = len(seg) // frame
        energy = np.sqrt(np.mean(seg[:k * frame].reshape(k, frame) ** 2, axis=1))
        cut = lo + int(np.argmin(energy)) * frame + frame // 2
        out.append((a, cut))
        a = cut
    out.append((a, n))
    return [(x, y) for x, y in out if y - x >= int(0.1 * SR)]


def degenerate(text: str) -> bool:
    letters = "".join(ch for ch in text if unicodedata.category(ch)[0] in "LN")
    if len(letters) < 40:
        return False
    m = max((len(mm.group(0)) for mm in _REPEATED.finditer(letters)), default=0)
    return m >= 0.5 * len(letters)


def _is_token_char(ch):
    return ch == "'" or unicodedata.category(ch)[0] in "LN"


def attach_words(text, items, offset=0.0, limit=None):
    """Như VideoLingo attach_words: token của bộ căn mốc → chữ của bản nhận diện, giữ dấu câu."""
    def clamp(v):
        v = max(0.0, float(v))
        return min(v, limit) if limit is not None else v
    normalized, original_ends = [], []
    for index, char in enumerate(text):
        part = unicodedata.normalize("NFKD", char)
        normalized.extend(part)
        original_ends.extend([index + 1] * len(part))
    words, pos, cursor, n = [], 0, 0, len(text)
    for token, start, end in items:
        if not token:
            continue
        k = cursor
        for ch in unicodedata.normalize("NFKD", token):
            while k < len(normalized) and normalized[k] != ch:
                k += 1
            if k >= len(normalized):
                break
            k += 1
        else:
            cursor = k
            j = original_ends[k - 1]
            if j <= pos and words:
                words[-1]["end"] = max(words[-1]["end"], round(offset + clamp(end), 3))
                continue
            while j < n and not text[j].isspace() and not _is_token_char(text[j]):
                j += 1
            surface = text[pos:j].strip()
            pos = j
            if len(surface) > 30:
                surface = token
            start, end = clamp(start), clamp(end)
            words.append({"word": surface, "start": round(offset + start, 3), "end": round(offset + max(start, end), 3)})
            continue
        start, end = clamp(start), clamp(end)
        words.append({"word": token, "start": round(offset + start, 3), "end": round(offset + max(start, end), 3)})
    if words and pos < n:
        tail = text[pos:].strip()
        if tail and len(words[-1]["word"] + tail) <= 30:
            words[-1]["word"] += tail
    return words


def repair_collapsed(words: list[dict]) -> int:
    """Căn mốc «sụp»: ≥ 3 chữ dồn (< 0,03 s/chữ — không ai nói nhanh vậy). Lùi ≤ 10 chữ tìm chỗ thời gian bị «nuốt»:
    chữ bị kéo > 1 s hoặc khoảng trống > 1 s giữa hai chữ (gặp cả hai kiểu ở video thử) → chia đều lại từ đó tới hết
    loạt dồn. Không thấy thì mượn khoảng lặng ngay trước loạt dồn nếu đủ ~0,1 s/chữ."""
    def chars(w):
        return max(1, len(re.sub(r"[^\w]", "", w["word"])))
    fixed, i = 0, 0
    while i < len(words):
        j = i
        while j < len(words) and (words[j]["end"] - words[j]["start"]) / chars(words[j]) < 0.03:
            j += 1
        if j - i >= 3:
            a_idx = next((k for k in range(i - 1, max(-1, i - 11), -1)
                          if words[k]["end"] - words[k]["start"] > 1.0 or words[k + 1]["start"] - words[k]["end"] > 1.0),
                         None)
            if a_idx is None and i > 0:
                n_run = sum(chars(w) for w in words[i:j])
                if words[j - 1]["end"] - words[i - 1]["end"] >= 0.1 * n_run:
                    a_idx = i                                # mượn khoảng lặng trước loạt dồn
                    words[i]["start"] = words[i - 1]["end"]
            if a_idx is not None:
                a, b = words[a_idx]["start"], words[j - 1]["end"]
                n = sum(chars(w) for w in words[a_idx:j])
                t = a
                for w in words[a_idx:j]:
                    d = (b - a) * chars(w) / n
                    w["start"], w["end"] = round(t, 3), round(t + d, 3)
                    t += d
                fixed += 1
            i = j
            continue
        i += 1
    return fixed


def _join(tokens, joiner: str) -> str:
    """Tiếng Trung / Nhật ghép liền, nhưng giữ dấu cách giữa hai chữ Latin (video Trung xen tiếng Anh:
    «我们 use the app» không thành «我们usetheapp»)."""
    if joiner:
        return joiner.join(tokens)
    out = ""
    for t in tokens:
        a, b = out[-1:], t[:1]
        if a.isascii() and b.isascii() and (a.isalpha() and b.isalnum() or a.isdigit() and b.isalpha()):
            out += " "                                  # chữ–chữ, chữ–số, số–chữ; số–số vẫn liền («20»«24»)
        out += t
    return out


def segment(words: list[dict], lang: str) -> list[dict]:
    """spaCy như VideoLingo: tách theo dấu câu → dấu phẩy → liên từ → câu > 60 token (tiếng không có pipeline: chỉ theo dấu)."""
    import spacy

    joiner = "" if lang in ("zh", "ja", "yue") else " "
    try:
        nlp = spacy.load("zh_core_web_md") if lang in ("zh", "yue") else None
    except OSError:
        nlp = None
    if nlp is None:
        nlp = spacy.blank("xx")
        nlp.add_pipe("sentencizer")
    doc = nlp(_join([w["word"] for w in words], joiner))
    by_mark, cur = [], []
    for s in (x.text.strip() for x in doc.sents):
        if cur and (s.startswith("-") or s.startswith("...") or cur[-1].endswith("-") or cur[-1].endswith("...")):
            cur.append(s)
        else:
            if cur:
                by_mark.append(" ".join(cur))
            cur = [s]
    if cur:
        by_mark.append(" ".join(cur))
    merged = []
    for s in by_mark:
        if merged and s.strip() in [",", ".", "，", "。", "？", "！"]:
            merged[-1] += s
        else:
            merged.append(s)
    tagged = nlp.has_pipe("parser") or nlp.has_pipe("tagger")

    def valid_phrase(phrase):
        return (any(t.dep_ in ("nsubj", "nsubjpass") or t.pos_ == "PRON" for t in phrase)
                and any(t.pos_ in ("VERB", "AUX") for t in phrase))

    def by_comma(text):
        d = nlp(text)
        out, start = [], 0
        for tok in d:
            if tok.text in (",", "，"):
                right = d[tok.i + 1:min(len(d), tok.i + 10)]
                ok = valid_phrase(right)
                left_words = [t for t in d[max(start, tok.i - 9):tok.i] if not t.is_punct]
                right_words = list(itertools.takewhile(lambda t: not t.is_punct, right))
                if len(left_words) <= 3 or len(right_words) <= 3:
                    ok = False
                if ok:
                    out.append(d[start:tok.i].text.strip())
                    start = tok.i + 1
        out.append(d[start:].text.strip())
        return out

    connectors = ["因为", "所以", "但是", "而且", "虽然", "如果", "即使", "尽管"] if lang in ("zh", "yue") else []

    def by_connector(text, ctx=5):
        sentences = [text]
        while True:
            split, new = False, []
            for sent in sentences:
                d = nlp(sent)
                start = 0
                for tok in d:
                    before = tok.text.lower() in connectors and not (tok.dep_ in ("det", "pron") and tok.head.pos_ in ("NOUN", "PROPN"))
                    lw = [w.text for w in d[max(0, tok.i - ctx):tok.i] if not w.is_punct]
                    rw = [w.text for w in d[tok.i + 1:min(len(d), tok.i + ctx + 1)] if not w.is_punct]
                    if len(lw) >= ctx and len(rw) >= ctx and before:
                        new.append(d[start:tok.i].text.strip())
                        start = tok.i
                        split = True
                        break
                if start < len(d):
                    new.append(d[start:].text.strip())
            if not split:
                return sentences
            sentences = new

    def by_root(d):
        tokens = [t.text for t in d]
        n = len(tokens)
        dp, prev = [float("inf")] * (n + 1), [0] * (n + 1)
        dp[0] = 0
        for i in range(1, n + 1):
            for j in range(max(0, i - 100), i):
                if i - j >= 30:
                    tok = d[i - 1]
                    if j == 0 or tok.is_sent_end or tok.pos_ in ("VERB", "AUX") or tok.dep_ == "ROOT":
                        if dp[j] + 1 < dp[i]:
                            dp[i], prev[i] = dp[j] + 1, j
        out, i = [], n
        while i > 0:
            j = prev[i]
            out.append(d[j:i].text.strip())          # giữ đúng khoảng trắng gốc (chữ Latin xen chữ Hán)
            i = j
        return out[::-1]

    final = []
    for s in merged:
        for c in (by_comma(s.strip()) if tagged else [s.strip()]):
            for k in (by_connector(c) if tagged else [c]):
                d = nlp(k)
                final.extend(by_root(d) if len(d) > 60 and tagged else [k])
    punct = string.punctuation + "'\""
    out = []
    for s in final:
        st = s.strip()
        if not st or all(ch in punct + "，。！？、；：" for ch in st):
            if out:
                out[-1]["text"] += s
            continue
        out.append({"text": s, "tokens": len(nlp(s))})
    return out


ap = argparse.ArgumentParser()
ap.add_argument("--audio", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--language", default="auto")
args = ap.parse_args()
import soundfile as sf  # noqa: E402
import torch  # noqa: E402

AUDIO, sr = sf.read(args.audio, dtype="float32")
if AUDIO.ndim > 1:
    AUDIO = AUDIO.mean(axis=1)
assert sr == SR, f"cần WAV {SR} Hz"
say(f"Âm thanh {len(AUDIO) / SR:.1f}s")
cuda = torch.cuda.is_available()
device, dtype = ("cuda:0", torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16) if cuda else ("cpu", torch.float32)
windows = split_windows(len(AUDIO))
# Silero VAD chỉ cắt biên ngoài mỗi cửa sổ, đệm 1 s (cửa sổ không thấy tiếng thì giữ nguyên)
threads = torch.get_num_threads()
try:
    torch.set_num_threads(1)
    from silero_vad import get_speech_timestamps, load_silero_vad

    regions = get_speech_timestamps(torch.from_numpy(AUDIO), load_silero_vad(), sampling_rate=SR, threshold=0.35,
                                    min_speech_duration_ms=100, speech_pad_ms=0)
finally:
    torch.set_num_threads(threads)
trimmed = []
for a, b in windows:
    sp = [r for r in regions if r["end"] > a and r["start"] < b]
    trimmed.append((max(a, sp[0]["start"] - SR), min(b, sp[-1]["end"] + SR)) if sp else (a, b))
windows = trimmed
progress(0.05)
from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner  # noqa: E402

say(f"Nạp Qwen3-ASR-1.7B ({device})…")
ASR = Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B", dtype=dtype, device_map=device,
                                      max_inference_batch_size=1, max_new_tokens=MAX_NEW_TOKENS)
forced = ISO_TO_QWEN.get(args.language) if args.language not in ("", "auto") else None


def listen(a: int, b: int):
    clip = AUDIO[a:b]
    ASR.max_new_tokens = min(MAX_NEW_TOKENS, TOKEN_MARGIN + math.ceil(len(clip) / SR * TOKENS_PER_SECOND))
    r = ASR.transcribe(audio=(clip, SR), language=forced)[0]
    return (forced or r.language or ""), r.text.strip()


pieces = []
for k, (a, b) in enumerate(windows):
    lang, text = listen(a, b)
    if degenerate(text) and b - a > 20 * SR:          # đọc lặp → chia đôi đọc lại một lần
        say(f"Cửa sổ {a / SR:.0f}–{b / SR:.0f}s đọc lặp → chia đôi đọc lại")
        mid = (a + b) // 2
        for x, y in ((a, mid), (mid, b)):
            pieces.append((x, y, *listen(x, y)))
    else:
        pieces.append((a, b, lang, text))
    progress(0.05 + 0.55 * (k + 1) / len(windows))
ASR = None                                           # giải phóng VRAM trước khi nạp bộ căn mốc
if cuda:
    torch.cuda.empty_cache()
votes: dict[str, float] = {}
for a, b, lang, text in pieces:
    if text:
        iso = QWEN_TO_ISO.get(str(lang).split(",")[0].strip(), "")
        votes[iso] = votes.get(iso, 0) + (b - a)
lang_iso = args.language if args.language not in ("", "auto") else (max(votes, key=votes.get) if votes else "zh")
say(f"Nhận diện xong ({lang_iso}) — căn mốc từng chữ…")
aligner = Qwen3ForcedAligner.from_pretrained("Qwen/Qwen3-ForcedAligner-0.6B", dtype=dtype, device_map=device)
words: list[dict] = []
todo = [p for p in pieces if p[3]]
for k, (a, b, lang, text) in enumerate(todo):
    qlang = ISO_TO_QWEN.get(lang_iso) or str(lang).split(",")[0].strip() or "Chinese"
    items = [(it.text, it.start_time, it.end_time) for it in aligner.align(audio=(AUDIO[a:b], SR), text=text, language=qlang)[0]]
    words += attach_words(text, items, a / SR, limit=(b - a) / SR)
    progress(0.6 + 0.3 * (k + 1) / len(todo))
del aligner
words = [w for w in words if len(w["word"]) <= 30]
fixed = repair_collapsed(words)
if fixed:
    say(f"Sửa {fixed} vùng căn mốc sụp")
sentences = segment(words, lang_iso)
progress(1.0)
json.dump({"language": lang_iso, "text": "".join(p[3] for p in pieces), "words": words, "sentences": sentences,
           "repaired": fixed}, open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
say(f"Xong: {len(sentences)} câu")
