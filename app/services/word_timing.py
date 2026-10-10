"""Mốc từng chữ của câu lồng tiếng — để chữ nổi (kiểu CapCut) sáng đúng chữ đang đọc.

Tính một lần cho mỗi track giọng, lưu cạnh track (``<track>.words.json``):
- có bộ căn chữ (Cài đặt › AI trên máy): căn ép đúng chữ bằng CTC tiếng Việt (``ctc_align``) — lệch ~20 ms;
- chưa có: chia theo số chữ trong đúng đoạn giọng của câu, dấu câu bám khoảng lặng thật — lệch TB ~40 ms.
Cách cũ chia đều cả khung phụ đề (đã kéo dài cho đủ 1 s): lệch TB ~90 ms, chỉ ~62% thời gian sáng đúng chữ đang đọc
(đo trên 4 track VieNeu, 1 464 chữ — audit_out/word_sync).

Cue phụ đề mang mốc dưới dạng tỉ lệ (``meta["speech"]`` theo khung câu, ``meta["words"]`` theo đoạn giọng) → dời /
co theo tốc độ video vẫn đúng, không phải tính lại."""

from __future__ import annotations

import csv
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from app.services import ctc_align
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.srt_utils import Cue, load_srt
from app.services.subtitle_style import word_spans, words_of

logger = logging.getLogger(__name__)

VERSION = 1
SR = ctc_align.SR
_lock = threading.Lock()


def sidecar(voice: Path) -> Path:
    return Path(voice).with_suffix(".words.json")


def _key(voice: Path) -> str:
    st, srt = Path(voice).stat(), Path(voice).with_suffix(".srt").stat()
    return f"{VERSION}|{st.st_mtime_ns}|{st.st_size}|{srt.st_mtime_ns}"


def load(voice: Path) -> dict | None:
    try:
        data = json.loads(sidecar(voice).read_text(encoding="utf-8"))
        return data if data.get("key") == _key(voice) else None
    except (OSError, ValueError):
        return None


def speech_span(c: Cue) -> tuple[float, float]:
    """(bắt đầu, thời lượng) đoạn giọng thật của câu — cùng hệ thời gian với cue; chưa có mốc thì là cả khung câu."""
    sp = c.meta.get("speech") if isinstance(c.meta, dict) else None
    d = c.end - c.start
    return (c.start + sp[0] * d, sp[1] * d) if sp else (c.start, d)


# ── Ước lượng không cần model ────────────────────────────────────────────
def estimate(a: np.ndarray, words: list[str], gate_db: float = -32.0, min_pause: float = 0.06) -> list[tuple[float, float]]:
    """Chia theo số chữ trong phần có tiếng; mỗi dấu câu ứng với khoảng lặng thật gần nhất (≥ 60 ms)."""
    dur = len(a) / SR
    n = len(a) // 160                                   # khung 10 ms
    if n == 0 or not words:
        return word_spans(words, max(0.05, dur))
    env = np.sqrt(np.mean(a[:n * 160].reshape(n, 160) ** 2, axis=1) + 1e-12)
    thr = env.max() * 10 ** (gate_db / 20)
    on = np.nonzero(env > thr)[0]
    if len(on) == 0:
        return word_spans(words, max(0.05, dur))
    s0, s1 = on[0] * 0.01, (on[-1] + 1) * 0.01
    pauses: list[tuple[float, float]] = []
    quiet = env[on[0]:on[-1] + 1] <= thr
    i = 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            if (j - i) * 0.01 >= min_pause:
                pauses.append((s0 + i * 0.01, s0 + j * 0.01))
            i = j
        else:
            i += 1
    groups: list[list[int]] = [[]]                      # cụm chữ ngăn bởi dấu câu
    for k, w in enumerate(words):
        groups[-1].append(k)
        if w[-1:] in ",;:.?!…" and k < len(words) - 1:
            groups.append([])
    est = word_spans(words, s1 - s0)
    bounds = [s0]
    used: set[int] = set()
    for g in groups[:-1]:
        t = s0 + (est[g[-1]][1] + est[g[-1] + 1][0]) / 2
        best = min((p for p in range(len(pauses)) if p not in used),
                   key=lambda p: abs(sum(pauses[p]) / 2 - t), default=None)
        if best is not None and abs(sum(pauses[best]) / 2 - t) < 0.35 and pauses[best][0] > bounds[-1]:
            used.add(best)
            bounds += list(pauses[best])
        else:
            bounds += [max(bounds[-1], s0 + est[g[-1]][1]), max(bounds[-1], s0 + est[g[-1] + 1][0])]
    bounds.append(s1)
    out: list[tuple[float, float]] = [(0.0, 0.0)] * len(words)
    for gi, g in enumerate(groups):
        a0, a1 = bounds[2 * gi], max(bounds[2 * gi] + 0.05, bounds[2 * gi + 1])
        for k, (x0, x1) in zip(g, word_spans([words[k] for k in g], a1 - a0)):
            out[k] = (a0 + x0, a0 + x1)
    return out


# ── Tính cho cả track ────────────────────────────────────────────────────
def _bounds(voice: Path, cues: list[Cue]) -> list[tuple[float, float]]:
    """Đoạn giọng thật của từng câu: theo báo cáo từng câu (.cues.csv); thiếu thì lấy khung phụ đề (không chồng câu sau)."""
    try:
        with open(Path(voice).with_suffix(".cues.csv"), encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if len(rows) == len(cues):
            return [(float(r["bat_dau"]), float(r["ket_thuc"])) for r in rows]
    except (OSError, KeyError, ValueError):
        pass
    return [(c.start, min(c.end, cues[i + 1].start) if i + 1 < len(cues) else c.end) for i, c in enumerate(cues)]


def compute(voice: Path, ffmpeg: FFmpeg, *, use_model: bool, log: Callable[[str], None] | None = None,
            cancel: threading.Event | None = None) -> dict | None:
    srt = Path(voice).with_suffix(".srt")
    if not srt.exists():
        return None
    cues = load_srt(srt)
    if not cues:
        return None
    key = _key(voice)
    pcm = np.frombuffer(ffmpeg.decode_pcm(voice, sample_rate=SR), dtype="<i2").astype(np.float32) / 32768
    aligner = None
    if use_model and ctc_align.model_ready():
        try:
            aligner = ctc_align.Aligner()
        except Exception as exc:
            logger.exception("Không nạp được bộ căn chữ")
            if log:
                log(f"⚠ Không nạp được bộ căn chữ ({exc}) — chữ nổi chia theo khoảng lặng.")
    from app.services.tts.normalize import is_vietnamese, normalize_vi_text, spoken

    if log:
        log(f"Chữ nổi: căn mốc từng chữ theo giọng ({len(cues)} câu"
            + (", căn ép đúng chữ)…" if aligner else ", chia theo khoảng lặng — cài «Căn chữ nổi» để khớp hơn)…"))
    # từng câu riêng (chạy theo khối nhiều câu chậm hơn: phải tính cả khoảng lặng giữa câu — audit_out/word_sync)
    out = []
    for n, (c, (a0, a1)) in enumerate(zip(cues, _bounds(voice, cues))):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled()
        words = words_of(c.text)
        clip = pcm[max(0, int(a0 * SR)):max(0, int(a1 * SR))]
        spans = None
        if aligner is not None and words and is_vietnamese(c.text):
            try:
                spans = aligner.align(clip, words, [spoken(normalize_vi_text(w)) for w in words])
            except Exception:
                logger.exception("Căn chữ lỗi ở câu %s", c.index)
        if spans is None:
            spans = estimate(clip, words)
        out.append([round(a0, 3), round(a1, 3), [[round(a0 + x0, 3), round(a0 + x1, 3)] for x0, x1 in spans]])
        if log and aligner is not None and n + 1 < len(cues) and (n + 1) % 25 == 0:
            log(f"Chữ nổi: đã căn {n + 1}/{len(cues)} câu…")
    return {"key": key, "method": "ctc" if aligner is not None else "pause", "cues": out}


def ensure(voice: Path | None, ffmpeg: FFmpeg, *, use_model: bool, log: Callable[[str], None] | None = None,
           cancel: threading.Event | None = None, wait: bool = True) -> dict | None:
    """Mốc chữ đã lưu của track (tính lại nếu track đổi, hoặc lần trước chưa có model mà giờ đã có và ``use_model``).
    ``wait=False`` (khung xem trước): luồng khác đang tính thì dùng bản đã lưu, không chờ."""
    if voice is None or not Path(voice).exists() or not Path(voice).with_suffix(".srt").exists():
        return None
    if not _lock.acquire(blocking=wait):
        return load(voice)
    try:
        data = load(voice)
        if data is not None and (data.get("method") == "ctc" or not (use_model and ctc_align.model_ready())):
            return data
        data = compute(Path(voice), ffmpeg, use_model=use_model, log=log, cancel=cancel)
        if data is not None:
            part = sidecar(voice).with_suffix(".part")
            part.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            part.replace(sidecar(voice))
        return data
    finally:
        _lock.release()


def attach(cues: list[Cue], voice: Path | None, ffmpeg: FFmpeg, *, use_model: bool = False,
           log: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> list[Cue]:
    """Gắn mốc giọng thật vào cue phụ đề đã dời theo giọng (đọc từ ``<track>.srt``, chưa dời / co).
    ``use_model=False`` (xem trước): không chạy model, không chờ luồng đang tính."""
    try:
        data = ensure(voice, ffmpeg, use_model=use_model, log=log, cancel=cancel, wait=use_model)
    except RenderCancelled:
        raise
    except Exception:
        logger.exception("Không tính được mốc chữ nổi")
        return cues
    if not data or len(data.get("cues") or []) != len(cues):
        return cues
    for c, (a0, a1, ws) in zip(cues, data["cues"]):
        d, sd = c.end - c.start, a1 - a0
        if d <= 0 or sd <= 0:
            continue
        c.meta["speech"] = ((a0 - c.start) / d, sd / d)
        if len(ws) == len(words_of(c.text)):
            c.meta["words"] = [((s - a0) / sd, (e - a0) / sd) for s, e in ws]
    return cues
