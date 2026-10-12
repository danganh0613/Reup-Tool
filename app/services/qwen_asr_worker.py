"""Chạy Qwen3-ASR trong môi trường riêng (models/qwen_asr_env — torch 2.8 + qwen-asr 0.0.6 + transformers 4.57 + spaCy
3.8): KHÔNG import gì của app (môi trường đó không có thư viện của app). app/services/qwen_asr.py gọi:

    <env>/python -I qwen_asr_worker.py --audio a.wav --out r.json --language zh
    <env>/python -I qwen_asr_worker.py --jobs jobs.json     (nhiều video: [{"audio", "out", "language"}, …])

Nhiều video: nạp Qwen3-ASR MỘT lần, nghe hết các video, giải phóng rồi nạp bộ căn mốc MỘT lần, căn mốc hết — không
nạp lại model cho từng video. Kết quả nghe của từng video lưu <out>.pieces.json (bị dừng giữa chừng thì lần chạy lại
không phải nghe lại); có file <out>.skip thì bỏ qua video đó (người dùng bấm Bỏ qua lúc chưa tới lượt).

Làm theo VideoLingo bản 01f8434 (core/asr_backend/qwen_asr_local.py, speech_edges.py, spacy_utils/*; Apache-2.0):
Silero VAD chỉ cắt biên ngoài (đệm 1 s) · cửa sổ ≤ 180 s cắt ở 100 ms lặng nhất trong 30 s cuối · Qwen3-ASR-1.7B, ngân
sách token 13/giây + 32 · Qwen3-ForcedAligner-0.6B → mốc từng chữ, gắn dấu câu · spaCy tách câu theo dấu → dấu phẩy →
liên từ → câu > 60 token. Edit Studio thêm: căn mốc «sụp» (một chữ bị kéo > 1 s rồi ≥ 3 chữ dồn < 0,05 s/chữ — gặp ở
video thử: «保» 3 s rồi 15 chữ trong 0,08 s; «这个能抓回家养吗» 8 chữ trong 0,6 s sau khoảng trống giả 2 s) → chia đều lại vùng đó;
căn mốc 2 lượt: lượt 1 cả cửa sổ (biết chỗ hết câu) → lượt 2 căn lại từng đoạn ≤ 20 s cắt ở cuối câu (đo ở video thử: lệch
trung bình 0,40 → 0,06 s, hết câu bị dồn), đoạn nào lượt 2 lệch chữ / dồn nhiều hơn thì giữ lượt 1; cửa sổ đọc lặp →
chia đôi đọc lại một lần.
In ra stdout: «PROGRESS k x» (video thứ k, 0..1) · «JOB k» (bắt đầu làm video k) · «DONE k» · «FAIL k lý do» ·
«SKIP k» · dòng log «[ 12s] …». Kết quả JSON mỗi video: language, text, words[{word,start,end}], sentences[{text,tokens}]."""

import argparse
import itertools
import json
import math
import os
import re
import string
import sys
import threading
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
CHUNK_SECONDS = 20                  # căn mốc lượt 2: đoạn ≤ 20 s (căn cả cửa sổ dài một lượt dễ «sụp»)
_SENT_END = ("。", "？", "！", "?", "!", ".", "；", ";", "…")
COLLAPSED = 0.05                    # < 0,05 s/chữ = căn mốc sụp (bộ căn mốc chia khung 0,08 s; người nói nhanh ~0,12 s/chữ)
NEED_VRAM_GB = 5.0                  # Qwen3-ASR-1.7B bf16 + bộ nhớ chạy (đo trên RTX 3060 Ti: ~4,7 GB)
_OUT = threading.Lock()
_STEP = {"name": "khởi động", "since": T0, "last": T0, "p0": 0.0, "p1": 0.03, "expect": 15.0, "job": 0, "done": {}}


def _emit(line: str) -> None:
    with _OUT:                      # luồng nhịp tim cũng in → không để hai dòng dính nhau
        print(line, flush=True)


def say(msg: str) -> None:
    _STEP["last"] = time.perf_counter()
    _emit(f"[{_STEP['last'] - T0:5.0f}s] {msg}")


def progress(x: float) -> None:
    k, done = _STEP["job"], _STEP["done"]
    done[k] = max(done.get(k, 0.0), x)                  # mốc thật — nhịp tim không kéo thanh lùi xuống dưới
    _emit(f"PROGRESS {k} {max(0.0, min(1.0, x)):.3f}")


def phase(name: str, p0: float, p1: float, expect: float, job: int | None = None) -> None:
    """Bước dài (nạp model, nghe một đoạn…) của video ``job``: thanh tiến độ tự nhích dần trong [p0, p1] theo thời gian
    ước lượng ``expect`` giây (không vượt 90 % khoảng), 30 s không có dòng mới thì báo «… vẫn đang …» — biết máy còn
    chạy."""
    _STEP.update(name=name, since=time.perf_counter(), p0=p0, p1=p1, expect=max(1.0, expect),
                 job=_STEP["job"] if job is None else job)
    progress(p0)
    _STEP["last"] = time.perf_counter()


def _heartbeat() -> None:
    while True:
        time.sleep(3)
        st, now = dict(_STEP), time.perf_counter()
        t = now - st["since"]
        guess = st["p0"] + (st["p1"] - st["p0"]) * 0.9 * (1 - math.exp(-t / st["expect"]))
        _emit(f"PROGRESS {st['job']} {min(1.0, max(guess, st['done'].get(st['job'], 0.0))):.3f}")
        if now - st["last"] >= 30:
            say(f"… vẫn đang {st['name']} (đã {t:.0f}s){_vram_note()}")


def _vram_note() -> str:
    """Card đã đầy giữa chừng (vd game vừa mở lại) → ghi rõ vì sao chậm."""
    probe = _STEP.get("vram")
    try:
        if probe and probe()[0] < 0.4 * 2 ** 30:
            return " — GPU đã đầy (game / ứng dụng khác đang dùng card) nên rất chậm; đóng bớt để nhanh hơn"
    except Exception:                                    # noqa: BLE001
        pass
    return ""


threading.Thread(target=_heartbeat, daemon=True).start()


def split_windows(audio) -> list[tuple[int, int]]:
    """Cửa sổ ≤ 180 s; mỗi lần cắt ở khung 100 ms lặng nhất trong 30 s cuối cửa sổ."""
    out, a, n = [], 0, len(audio)
    while n - a > WINDOW_SECONDS * SR:
        lo, hi = a + (WINDOW_SECONDS - CUT_SEARCH_SECONDS) * SR, a + WINDOW_SECONDS * SR
        frame = SR // 10
        seg = audio[lo:hi]
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


def _chars(w) -> int:
    return max(1, len(re.sub(r"[^\w]", "", w["word"])))


def _rate(w) -> float:
    """Số giây / chữ của một mục căn mốc."""
    return (w["end"] - w["start"]) / _chars(w)


def collapsed_words(words: list[dict]) -> int:
    return sum(1 for w in words if _rate(w) < COLLAPSED)


def repair_collapsed(words: list[dict]) -> int:
    """Căn mốc «sụp»: ≥ 3 chữ dồn (< ``COLLAPSED`` s/chữ — không ai nói nhanh vậy). Lùi ≤ 10 chữ tìm chỗ thời gian bị «nuốt»:
    chữ bị kéo > 1 s hoặc khoảng trống > 1 s giữa hai chữ (gặp cả hai kiểu ở video thử) → chia đều lại từ đó tới hết
    loạt dồn. Không thấy thì mượn khoảng lặng ngay trước loạt dồn nếu đủ ~0,1 s/chữ."""
    fixed, i = 0, 0
    while i < len(words):
        j = i
        while j < len(words) and _rate(words[j]) < COLLAPSED:
            j += 1
        if j - i >= 3:
            a_idx = next((k for k in range(i - 1, max(-1, i - 11), -1)
                          if words[k]["end"] - words[k]["start"] > 1.0 or words[k + 1]["start"] - words[k]["end"] > 1.0),
                         None)
            if a_idx is None and i > 0:
                n_run = sum(_chars(w) for w in words[i:j])
                if words[j - 1]["end"] - words[i - 1]["end"] >= 0.1 * n_run:
                    a_idx = i                                # mượn khoảng lặng trước loạt dồn
                    words[i]["start"] = words[i - 1]["end"]
            if a_idx is not None:
                a, b = words[a_idx]["start"], words[j - 1]["end"]
                n = sum(_chars(w) for w in words[a_idx:j])
                t = a
                for w in words[a_idx:j]:
                    d = (b - a) * _chars(w) / n
                    w["start"], w["end"] = round(t, 3), round(t + d, 3)
                    t += d
                fixed += 1
            i = j
            continue
        i += 1
    return fixed


def _quiet(audio, t0: float, t1: float) -> int:
    """Mẫu giữa khung 20 ms lặng nhất trong [t0 − 0,1 s, t1 + 0,1 s] — chỗ cắt đoạn cho lượt căn mốc 2."""
    lo, hi = max(0, int((t0 - 0.1) * SR)), min(len(audio), int((t1 + 0.1) * SR))
    frame = SR // 50
    k = (hi - lo) // frame
    if k < 1:
        return (lo + hi) // 2
    energy = np.sqrt(np.mean(audio[lo:lo + k * frame].reshape(k, frame) ** 2, axis=1))
    return lo + int(np.argmin(energy)) * frame + frame // 2


def chunk_spans(words: list[dict], audio, a: int, b: int) -> list[tuple[int, int, int, int]]:
    """Cửa sổ [a, b) đã căn lượt 1 → đoạn ≤ ``CHUNK_SECONDS`` cắt ở cuối câu: [(mẫu đầu, mẫu cuối, chữ đầu, chữ cuối + 1)].
    Chỉ cắt ở chỗ lượt 1 tin được (hai chữ quanh chỗ cắt không «dồn», nghỉ < 1 s), tại khung lặng nhất ở đó."""
    out: list[tuple[int, int, int, int]] = []
    first, start, cut = 0, a, None
    for k in range(len(words) - 1):
        w, nx = words[k], words[k + 1]
        if (w["word"].rstrip().endswith(_SENT_END) and min(_rate(w), _rate(nx)) >= COLLAPSED
                and 0 <= nx["start"] - w["end"] < 1.0):
            cut = k
        if cut is not None and nx["end"] - start / SR > CHUNK_SECONDS:
            pos = _quiet(audio, words[cut]["end"], words[cut + 1]["start"])
            if start < pos < b:
                out.append((start, pos, first, cut + 1))
                first, start = cut + 1, pos
            cut = None
    out.append((start, b, first, len(words)))
    return out


def realign(aligner, audio, words: list[dict], spans, qlang: str, joiner: str) -> tuple[list[dict], int]:
    """Căn mốc lượt 2 từng đoạn ngắn. Đoạn lỗi / ra khác chữ / dồn nhiều hơn lượt 1 → giữ lượt 1. → (chữ, số đoạn dùng)."""
    out: list[dict] = []
    used = 0
    for s0, s1, i, j in spans:
        old = words[i:j]
        new = None
        if old:
            text = _join([w["word"] for w in old], joiner)
            try:
                items = [(it.text, it.start_time, it.end_time)
                         for it in aligner.align(audio=(audio[s0:s1], SR), text=text, language=qlang)[0]]
                new = attach_words(text, items, s0 / SR, limit=(s1 - s0) / SR)
            except Exception:                            # noqa: BLE001 — lượt 2 chỉ để chỉnh, lỗi thì giữ lượt 1
                new = None
        if new and [w["word"] for w in new] == [w["word"] for w in old] and collapsed_words(new) <= collapsed_words(old):
            out += new
            used += 1
        else:
            out += old
    return out, used


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
ap.add_argument("--audio")
ap.add_argument("--out")
ap.add_argument("--language", default="auto")
ap.add_argument("--jobs", help="file JSON: nhiều video [{audio, out, language}]")
args = ap.parse_args()
if not args.jobs and not (args.audio and args.out):
    ap.error("cần --jobs hoặc --audio + --out")
JOBS = (json.load(open(args.jobs, encoding="utf-8-sig")) if args.jobs
        else [{"audio": args.audio, "out": args.out, "language": args.language}])
import soundfile as sf  # noqa: E402
import torch  # noqa: E402

cuda = torch.cuda.is_available()
device, dtype = ("cuda:0", torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16) if cuda else ("cpu", torch.float32)
_STEP["vram"] = torch.cuda.mem_get_info if cuda else None
if cuda:
    # card bị game / ứng dụng khác chiếm → model tràn sang RAM thường, chậm gấp nhiều lần: báo ngay từ đầu
    free, total = torch.cuda.mem_get_info()
    if free < NEED_VRAM_GB * 2 ** 30:
        say(f"⚠ GPU chỉ còn trống {free / 2 ** 30:.1f}/{total / 2 ** 30:.0f} GB (cần ~{NEED_VRAM_GB:.0f} GB) — game / "
            "ứng dụng khác đang dùng card nên nhận diện sẽ chậm hơn nhiều. Đóng bớt rồi chạy lại để nhanh hơn.")
if not cuda:
    try:
        import psutil  # noqa: E402

        ram = psutil.virtual_memory().available / 2 ** 30
    except Exception:                                    # noqa: BLE001 — không có psutil thì bỏ qua bước kiểm
        ram = None
    if ram is not None and ram < 8:
        say(f"⚠ Máy không có card NVIDIA và chỉ còn {ram:.1f} GB RAM trống — nhận diện trên CPU cần ~8 GB, có thể rất "
            "chậm hoặc lỗi. Đóng bớt ứng dụng rồi thử lại.")
    else:
        say("Máy không có card NVIDIA — nhận diện chạy bằng CPU (chậm hơn nhiều, video 2 phút có thể mất 10–20 phút).")
VAD = None


def load_audio(path: str):
    audio, sr = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    assert sr == SR, f"cần WAV {SR} Hz"
    return audio


def speech_windows(audio) -> list[tuple[int, int]]:
    """Cửa sổ ≤ 180 s; Silero VAD chỉ cắt biên ngoài mỗi cửa sổ, đệm 1 s (cửa sổ không thấy tiếng thì giữ nguyên)."""
    global VAD
    windows = split_windows(audio)
    threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        from silero_vad import get_speech_timestamps, load_silero_vad

        VAD = VAD or load_silero_vad()
        regions = get_speech_timestamps(torch.from_numpy(audio), VAD, sampling_rate=SR, threshold=0.35,
                                        min_speech_duration_ms=100, speech_pad_ms=0)
    finally:
        torch.set_num_threads(threads)
    trimmed = []
    for a, b in windows:
        sp = [r for r in regions if r["end"] > a and r["start"] < b]
        if sp:                                           # cửa sổ không thấy tiếng nói nào (chỉ nhạc / im) → bỏ
            trimmed.append((max(a, sp[0]["start"] - SR), min(b, sp[-1]["end"] + SR)))
    if len(trimmed) < len(windows):
        say(f"Bỏ {len(windows) - len(trimmed)} đoạn không có tiếng nói")
    return trimmed


def skipped(k: int) -> bool:
    """Người dùng bấm Bỏ qua video k lúc chưa tới lượt (app tạo file <out>.skip)."""
    if os.path.exists(JOBS[k]["out"] + ".skip"):
        _emit(f"SKIP {k}")
        return True
    return False


def fail_text(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return (lines[0] if lines else type(exc).__name__)[:300]


def listen_job(k: int, asr) -> list:
    """Nghe video k → [(a, b, ngôn ngữ, chữ)] theo từng cửa sổ (đọc lặp → chia đôi đọc lại một lần)."""
    job = JOBS[k]
    audio = load_audio(job["audio"])
    cache = job["out"] + ".pieces.json"
    try:                                                 # lần chạy trước đã nghe xong video này (bị dừng ở bước sau)
        old = json.load(open(cache, encoding="utf-8"))
        if old.get("samples") == len(audio):
            say(f"Dùng lại kết quả nghe lần trước ({len(audio) / SR:.1f}s)")
            return [tuple(x) for x in old["pieces"]]
    except (OSError, ValueError):
        pass
    say(f"Âm thanh {len(audio) / SR:.1f}s")
    phase("dò đoạn có tiếng", 0.03, 0.05, 10, job=k)
    windows = speech_windows(audio)
    lang_arg = job.get("language") or "auto"
    forced = ISO_TO_QWEN.get(lang_arg) if lang_arg != "auto" else None

    def listen(a: int, b: int):
        clip = audio[a:b]
        asr.max_new_tokens = min(MAX_NEW_TOKENS, TOKEN_MARGIN + math.ceil(len(clip) / SR * TOKENS_PER_SECOND))
        r = asr.transcribe(audio=(clip, SR), language=forced)[0]
        return (forced or r.language or ""), r.text.strip()

    pieces = []
    for w, (a, b) in enumerate(windows):
        p0, p1 = 0.12 + 0.48 * w / len(windows), 0.12 + 0.48 * (w + 1) / len(windows)
        part = f"đoạn {w + 1}/{len(windows)} " if len(windows) > 1 else ""
        phase(f"nghe {part or 'lời thoại '}({a / SR:.0f}–{b / SR:.0f}s)", p0, p1, max(8.0, (b - a) / SR * 0.15), job=k)
        say(f"Nghe {part}{a / SR:.0f}–{b / SR:.0f}s…")
        lang, text = listen(a, b)
        if degenerate(text) and b - a > 20 * SR:          # đọc lặp → chia đôi đọc lại một lần
            say(f"Cửa sổ {a / SR:.0f}–{b / SR:.0f}s đọc lặp → chia đôi đọc lại")
            mid = (a + b) // 2
            for x, y in ((a, mid), (mid, b)):
                pieces.append((x, y, *listen(x, y)))
        else:
            pieces.append((a, b, lang, text))
        progress(p1)
    json.dump({"samples": len(audio), "pieces": pieces}, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return pieces


def align_job(k: int, aligner, pieces: list) -> None:
    """Căn mốc từng chữ + tách câu cho video k, ghi JSON kết quả."""
    job = JOBS[k]
    audio = load_audio(job["audio"])
    votes: dict[str, float] = {}
    for a, b, lang, text in pieces:
        if text:
            iso = QWEN_TO_ISO.get(str(lang).split(",")[0].strip(), "")
            votes[iso] = votes.get(iso, 0) + (b - a)
    lang_arg = job.get("language") or "auto"
    lang_iso = lang_arg if lang_arg != "auto" else (max(votes, key=votes.get) if votes else "zh")
    say(f"Nhận diện xong ({lang_iso}) — căn mốc từng chữ…")
    words: list[dict] = []
    todo = [p for p in pieces if p[3]]
    joiner = "" if lang_iso in ("zh", "ja", "yue") else " "
    chunks = used = 0
    for w, (a, b, lang, text) in enumerate(todo):
        phase("căn mốc từng chữ", 0.63 + 0.29 * w / len(todo), 0.63 + 0.29 * (w + 1) / len(todo),
              max(5.0, (b - a) / SR * 0.08), job=k)
        own = str(lang).split(",")[0].strip()
        qlang = own if own in QWEN_TO_ISO else (ISO_TO_QWEN.get(lang_iso) or "Chinese")
        items = [(it.text, it.start_time, it.end_time)
                 for it in aligner.align(audio=(audio[a:b], SR), text=text, language=qlang)[0]]
        part = [x for x in attach_words(text, items, a / SR, limit=(b - a) / SR) if len(x["word"]) <= 30]
        spans = chunk_spans(part, audio, a, b)
        if len(spans) > 1:                               # lượt 2: căn lại từng đoạn ngắn
            part, n_used = realign(aligner, audio, part, spans, qlang, joiner)
            chunks, used = chunks + len(spans), used + n_used
        words += part
        progress(0.63 + 0.29 * (w + 1) / len(todo))
    if chunks:
        say(f"Căn mốc lượt 2: {used}/{chunks} đoạn ≤ {CHUNK_SECONDS} s")
    fixed = repair_collapsed(words)
    if fixed:
        say(f"Sửa {fixed} vùng căn mốc sụp")
    phase("tách câu", 0.92, 1.0, 10, job=k)
    sentences = segment(words, lang_iso)
    progress(1.0)
    json.dump({"language": lang_iso, "text": "".join(p[3] for p in pieces), "words": words, "sentences": sentences,
               "repaired": fixed, "chunks": chunks, "realigned": used},
              open(job["out"], "w", encoding="utf-8"), ensure_ascii=False)
    say(f"Xong: {len(sentences)} câu")


def cached(repo: str) -> bool:
    """Model đã có trong thư mục HF_HOME (lần đầu phải tải vài GB → báo trước, khỏi tưởng máy treo)."""
    home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    snap = os.path.join(home, "hub", "models--" + repo.replace("/", "--"), "snapshots")
    return os.path.isdir(snap) and any(os.scandir(snap))


def load_model(cls, repo: str, size: str, **kw):
    """Nạp model; card hết bộ nhớ → thử lại trên CPU (chậm hơn nhưng chạy được); lỗi khác → báo từng video rồi dừng."""
    global device, dtype
    if not cached(repo):
        say(f"Đang tải model {repo.split('/')[-1]} lần đầu (~{size}) — vài phút tới vài chục phút tuỳ mạng…")
    try:
        return cls.from_pretrained(repo, dtype=dtype, device_map=device, **kw)
    except Exception as exc:                             # noqa: BLE001
        oom = "out of memory" in str(exc).lower()
        if cuda and oom and device != "cpu":
            say("⚠ GPU hết bộ nhớ khi nạp model — chạy trên CPU (chậm hơn nhiều). Đóng game / ứng dụng nặng rồi chạy "
                "lại để nhanh hơn.")
            torch.cuda.empty_cache()
            device, dtype = "cpu", torch.float32
            return cls.from_pretrained(repo, dtype=dtype, device_map=device, **kw)
        why = ("GPU hết bộ nhớ" if oom else "mất mạng khi tải model" if any(
            k in str(exc).lower() for k in ("connection", "timed out", "getaddrinfo", "ssl")) else fail_text(exc))
        for k in range(len(JOBS)):
            _emit(f"FAIL {k} Không nạp được {repo.split('/')[-1]}: {why}")
        sys.exit(3)


# 1) nghe hết các video với MỘT lần nạp Qwen3-ASR
phase("nạp Qwen3-ASR-1.7B", 0.05, 0.12, 25 if cached("Qwen/Qwen3-ASR-1.7B") else 600, job=0)
from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner  # noqa: E402

say(f"Nạp Qwen3-ASR-1.7B ({device})…")
t_load = time.perf_counter()
ASR = load_model(Qwen3ASRModel, "Qwen/Qwen3-ASR-1.7B", "4,7 GB", max_inference_batch_size=1,
                 max_new_tokens=MAX_NEW_TOKENS)
say(f"Đã nạp model ({time.perf_counter() - t_load:.0f}s)")
heard: dict[int, list] = {}
for k in range(len(JOBS)):
    if skipped(k):
        continue
    _STEP["job"] = k
    _emit(f"JOB {k}")
    try:
        heard[k] = listen_job(k, ASR)
    except Exception as exc:                             # noqa: BLE001 — một video hỏng không dừng cả lượt
        _emit(f"FAIL {k} {fail_text(exc)}")
ASR = None                                               # giải phóng VRAM trước khi nạp bộ căn mốc
if cuda:
    torch.cuda.empty_cache()
# 2) căn mốc + tách câu từng video với MỘT lần nạp bộ căn mốc
if heard:
    phase("nạp bộ căn mốc", 0.6, 0.63, 10 if cached("Qwen/Qwen3-ForcedAligner-0.6B") else 240, job=min(heard))
    aligner = load_model(Qwen3ForcedAligner, "Qwen/Qwen3-ForcedAligner-0.6B", "1,2 GB")
    for k, pieces in heard.items():
        if skipped(k):
            continue
        _STEP["job"] = k
        _emit(f"JOB {k}")
        try:
            align_job(k, aligner, pieces)
            _emit(f"DONE {k}")
        except Exception as exc:                         # noqa: BLE001
            _emit(f"FAIL {k} {fail_text(exc)}")
