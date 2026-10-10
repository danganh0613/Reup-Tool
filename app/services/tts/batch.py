"""Đọc liền nhiều câu rồi tách lại từng câu — VieNeu đọc vài câu kề nhau một hơi cho câu ngắn có ngữ cảnh (trước đây
dùng để né giới hạn lượt gọi mỗi phút của Gemini TTS, engine đó đã gỡ 10/2026).

Tách: Whisper nghe cả đoạn (mốc từng từ) → khớp chữ với từng câu → cắt ở khoảng lặng dài nhất giữa chữ cuối câu trước
và chữ đầu câu sau. Câu khớp kém / dài bất thường thì bỏ, không ghi cache — câu đó tự đọc riêng như cũ, nên tách hỏng
không làm hỏng track. Không có Whisper trên máy → không gộp.
"""

from __future__ import annotations

import difflib
import logging
import re

import numpy as np

from app.services.tts import postprocess, quality
from app.services.tts.normalize import is_vietnamese, normalize_vi_text, spoken

logger = logging.getLogger(__name__)

MAX_LINES = 25              # số câu tối đa mỗi lượt
MAX_CHARS = 1500            # tổng ký tự tối đa mỗi lượt (đoạn quá dài model dễ lạc, đọc sót)
MIN_LINES = 2               # ít hơn thì đọc từng câu như cũ (cảm xúc từng câu chia nhóm nhỏ: 2 câu gộp vẫn đỡ 1 lượt)
_SILENCE_DB = -40.0         # dưới đỉnh bấy nhiêu dB coi là lặng (đường bao 10 ms)
_MIN_CUT = 8                # khoảng lặng ≥ 80 ms mới được cắt
_EDGE_CUT = 15              # đầu / cuối đoạn: lặng ≥ 150 ms (khe giữa hai tiếng trong một từ có thể ~80 ms)


def available() -> bool:
    """Tách được không: cần model Whisper có sẵn trên máy (không tự tải; CPU cũng được, chỉ chậm hơn)."""
    return quality.asr_available(require_gpu=False)


def chunks(texts: list[str], max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS) -> list[list[str]]:
    """Chia danh sách câu (giữ thứ tự) thành các lô ≤ ``max_lines`` câu và ≤ ``max_chars`` ký tự."""
    out: list[list[str]] = []
    for t in texts:
        if not out or len(out[-1]) >= max_lines or sum(len(x) for x in out[-1]) + len(t) > max_chars:
            out.append([])
        out[-1].append(t)
    return out


def _tokens(text: str, vi: bool) -> list[str]:
    """Chữ để so khớp: tiếng Việt đổi số → chữ ở cả hai phía («3» → «ba») như phần kiểm chất lượng."""
    t = spoken(normalize_vi_text(text)) if vi else text
    return re.findall(r"[^\W_]+", t.lower())


def split(pcm: bytes, rate: int, lines: list[str], compute: str = "auto") -> list[bytes | None]:
    """Âm thanh đọc liền ``lines`` → PCM từng câu (None = câu tách không chắc, để đọc riêng)."""
    a = np.frombuffer(pcm, "<i2").astype(np.float32) / 32768
    n = len(lines)
    if n == 0 or len(a) < rate // 2:
        return [None] * n
    vi = is_vietnamese(" ".join(lines))
    from app.services.whisper_stt import load_model

    with quality._asr_lock:                                  # model dùng chung với phần kiểm chất lượng
        model = load_model(quality.local_whisper(), "auto", compute)
        segs, _info = model.transcribe(quality._to16k(a, rate), language="vi" if vi else None, beam_size=5,
                                       word_timestamps=True, vad_filter=False, condition_on_previous_text=False)
        hyp = [(tok, w.start, w.end) for s in segs for w in (s.words or []) for tok in _tokens(w.word, vi)]
    ref = [(tok, k) for k, ln in enumerate(lines) for tok in _tokens(ln, vi)]
    total = [0] * n
    for _tok, k in ref:
        total[k] += 1
    # Whisper hay nhét khoảng lặng giữa hai câu vào chữ kề bên (chữ đầu câu sau «bắt đầu» sớm từ lúc lặng) → tìm chỗ cắt
    # trong khoảng [đầu chữ cuối câu trước, cuối chữ đầu câu sau], không chỉ giữa hai mốc
    first: dict[int, float] = {}            # cuối chữ đầu tiên khớp được của câu
    last: dict[int, float] = {}             # đầu chữ cuối cùng khớp được của câu
    head: float | None = None               # đầu chữ khớp đầu tiên của cả đoạn
    tail: float | None = None               # cuối chữ khớp cuối cùng của cả đoạn
    hits = [0] * n
    sm = difflib.SequenceMatcher(None, [x[0] for x in ref], [x[0] for x in hyp], autojunk=False)
    for blk in sm.get_matching_blocks():
        for j in range(blk.size):
            k = ref[blk.a + j][1]
            _tok, ws, we = hyp[blk.b + j]
            first.setdefault(k, we)
            last[k] = ws
            hits[k] += 1
            head = ws if head is None else head
            tail = we

    fr = rate // 100
    env = 20 * np.log10(np.sqrt(np.mean(a[: len(a) // fr * fr].reshape(-1, fr) ** 2, axis=1)) + 1e-9)
    quiet = env < env.max() + _SILENCE_DB
    dur = len(a) / rate

    def cut_between(t0: float, t1: float) -> float | None:
        """Giữa khoảng lặng dài nhất trong [t0, t1] (giây); không có lặng đủ dài → None."""
        i0, i1 = max(0, int(t0 * 100)), min(len(quiet), int(t1 * 100) + 1)
        best_len, best_at, run, start = 0, None, 0, i0
        for i in range(i0, i1):
            run = run + 1 if quiet[i] else 0
            if run == 1:
                start = i
            if run > best_len:
                best_len, best_at = run, (start + i + 1) / 200
        return best_at if best_len >= _MIN_CUT else None

    def edge_cut(t0: float, t1: float, *, latest: bool) -> float | None:
        """Giữa khoảng lặng đủ dài GẦN chữ nhất trong [t0, t1]: ``latest`` = khoảng cuối cùng (trước chữ đầu đoạn),
        không thì khoảng đầu tiên (sau chữ cuối đoạn) — không lấy khoảng dài nhất kẻo ôm luôn phần đọc thừa."""
        i0, i1 = max(0, int(t0 * 100)), min(len(quiet), int(t1 * 100) + 1)
        runs, run = [], 0
        for i in range(i0, i1):
            run = run + 1 if quiet[i] else 0
            if run == _EDGE_CUT:
                runs.append([i - _EDGE_CUT + 1, i + 1])
            elif run > _EDGE_CUT:
                runs[-1][1] = i + 1
        if not runs:
            return None
        s, e = runs[-1] if latest else runs[0]
        return (s + e) / 200

    # câu Whisper khớp kém → đọc riêng. Model đọc thêm gì đó (vd cả lời dặn) thì câu đầu / cuối dài bất thường → bị
    # loại ở bước kiểm độ dài bên dưới
    bad = {k for k in range(n) if total[k] == 0 or hits[k] < max(1, 0.6 * total[k])}
    # đầu / cuối đoạn: cắt ở chỗ lặng ngay trước chữ khớp đầu tiên / sau chữ khớp cuối cùng — model (lite) đôi khi đọc to
    # cả lời dặn trước lời thoại: trước đây câu đầu dính cả đoạn đó → «dài bất thường» → bỏ, phải gọi đọc riêng
    start = edge_cut(max(0.0, head - 3.0), head + 0.05, latest=True) if head is not None and 0 not in bad else None
    end = edge_cut(tail - 0.05, tail + 3.0, latest=False) if tail is not None and n - 1 not in bad else None
    cuts = [start or 0.0]
    for k in range(n - 1):
        a0, b0 = last.get(k), first.get(k + 1)
        c = None
        if a0 is not None and b0 is not None:
            c = cut_between(min(a0, b0), max(a0, b0))
        elif a0 is not None:
            c = cut_between(a0, a0 + 3.0)
        elif b0 is not None:
            c = cut_between(b0 - 3.0, b0)
        if c is None or c <= cuts[-1]:
            bad.update((k, k + 1))                           # không tìm được chỗ lặng giữa hai câu → cả hai đọc riêng
            guess = [x for x in (a0, b0) if x is not None]
            c = max(cuts[-1], sum(guess) / len(guess) if guess else cuts[-1])
        cuts.append(c)
    cuts.append(end if end is not None and end > cuts[-1] else max(dur, cuts[-1]))

    out: list[bytes | None] = []
    for k, ln in enumerate(lines):
        seg = a[int(cuts[k] * rate):int(cuts[k + 1] * rate)]
        sh = postprocess.shape(seg, rate) if len(seg) else None
        speech = (sh.last - sh.first + 1) * postprocess.FRAME if sh else 0.0
        exp = quality.expected_duration(ln)
        if k in bad or sh is None or speech > 1.7 * exp + 1.0 or (exp >= 1.0 and speech < 0.35 * exp):
            out.append(None)
            continue
        out.append((np.clip(seg, -1, 1) * 32767).astype("<i2").tobytes())
    ok = sum(x is not None for x in out)
    logger.info("Tách đoạn đọc gộp: %d/%d câu (Whisper khớp %d/%d chữ)", ok, n, sum(hits), len(ref))
    return out
