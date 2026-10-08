"""QA âm thanh cho video / audio ĐÃ XUẤT: in PASS / FAIL theo tiêu chuẩn đầu ra, tìm clip, lặng bất thường, câu bị cụt.

    python tools/qa_audio.py output/videos/a_review.mp4
    python tools/qa_audio.py a.mp4 --subs output/jobs/<video>/sub.ass     # phụ đề đã cháy vào video (ASS/SRT, mốc theo video)
    python tools/qa_audio.py a.mp4 --target -14 --json audit_out/qa/a.json

Ngưỡng mặc định:
  · Loudness tích hợp −14 ±1 LUFS (ffmpeg loudnorm print_format=json, đối chiếu ebur128)
  · True peak ≤ −1.0 dBTP · số mẫu clip = 0 · DC offset ≈ 0
  · Lệch thời lượng audio / video < 50 ms
  · Không lặng > 1,5 s giữa các câu thoại (có phụ đề mà không có giọng = mất câu → FAIL)
  · Độ to từng câu lệch ≤ 2 LU so với trung vị (BS.1770, K-weighting)
  · Giọng to hơn nền ≥ 12 dB (khi có nhạc nền / âm gốc)
  · Không câu nào ra đột ngột (cụt) hay có cụm âm lạ ở cuối («rẹt»): > 1 chỗ = FAIL. Vào đột ngột (tách): phụ âm bật
    (đ, b, c, k) cũng vào nhanh → ≤ 3 chỗ (≤ 6 %) = WARN kèm mốc để nghe lại
  · AAC 48 kHz (hoặc 44,1 kHz); app đặt 192k — trung bình 128–190 kbps (bản nhiều đoạn lặng) = WARN
  · (có --subs) phụ đề ≤ 2 dòng, ≤ 42 ký tự/dòng, hiện ≥ 1 s, không chồng nhau
Thoát mã 1 nếu có FAIL. Chỉ đọc file, không sửa gì.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
_bin = ROOT / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
FFMPEG = str(_bin) if _bin.exists() else (shutil.which("ffmpeg") or "ffmpeg")
FFPROBE = str(_bin.with_name(_bin.name.replace("ffmpeg", "ffprobe"))) if _bin.exists() else (shutil.which("ffprobe") or "ffprobe")

SR = 48000
FRAME = 0.005                     # khung phân tích 5 ms
MERGE_GAP = 0.08                  # lặng < 80 ms trong câu (phụ âm, ngắt hơi) vẫn tính là một câu

# K-weighting BS.1770-4 @ 48 kHz (high shelf + high pass RLB)
_KW = [([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585]),
       ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621])]


# ── Đọc file ─────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def probe(path: Path) -> dict:
    out = _run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    data = json.loads(out.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    def dur(s):
        try:
            return float(s.get("duration")) if s and s.get("duration") else None
        except (TypeError, ValueError):
            return None
    return {"video": v, "audio": a, "v_dur": dur(v), "a_dur": dur(a),
            "format_dur": float((data.get("format") or {}).get("duration") or 0)}


def loudnorm_scan(path: Path, target: float) -> dict:
    """Quét loudness bằng loudnorm (chỉ đo, không ghi file)."""
    out = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-sn", "-dn",
                "-af", f"loudnorm=I={target}:TP=-1:LRA=11:print_format=json", "-f", "null", "-"])
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", out.stderr, re.S)
    return {k: float(v) for k, v in json.loads(m[-1]).items() if _isnum(v)} if m else {}


def ebur128_scan(path: Path) -> dict:
    out = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-sn", "-dn",
                "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = out.stderr[out.stderr.rfind("Summary:"):]
    res = {}
    for key, pat in (("I", r"I:\s*(-?[\d.]+|-inf)\s*LUFS"), ("LRA", r"LRA:\s*(-?[\d.]+)\s*LU"),
                     ("TP", r"True peak:\s*Peak:\s*(-?[\d.]+|-inf)")):
        m = re.search(pat, tail, re.S)
        if m:
            res[key] = float(m.group(1)) if m.group(1) != "-inf" else float("-inf")
    return res


def silencedetect(path: Path, noise_db: float = -50.0, min_dur: float = 1.5) -> list[tuple[float, float]]:
    out = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-sn", "-dn",
                "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", out.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", out.stderr)]
    return list(zip(starts, ends + [float("nan")] * (len(starts) - len(ends))))


def decode(path: Path, channels: int) -> np.ndarray:
    """Âm thanh float32 (n, kênh) ở tần số gốc của file (không đổi tần số, không trộn kênh)."""
    out = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-sn", "-dn", "-ac", str(max(1, channels)),
                          "-f", "f32le", "-acodec", "pcm_f32le", "-"], capture_output=True, timeout=900)
    a = np.frombuffer(out.stdout, dtype=np.float32)
    return a[: len(a) // max(1, channels) * max(1, channels)].reshape(-1, max(1, channels))


def _isnum(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# ── Phụ đề ───────────────────────────────────────────────────────────────

@dataclass
class Sub:
    start: float
    end: float
    lines: list[str]


def _ts(s: str) -> float:
    s = s.strip().replace(",", ".")
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def load_subs(path: Path, scale: float = 1.0) -> list[Sub]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    subs: list[Sub] = []
    if path.suffix.lower() in (".ass", ".ssa"):
        for ln in text.splitlines():
            if not ln.startswith("Dialogue:"):
                continue
            parts = ln.split(":", 1)[1].split(",", 9)
            if len(parts) < 10:
                continue
            body = re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\h", " ")
            lines = [x.strip() for x in re.split(r"\\[Nn]", body) if x.strip()]
            subs.append(Sub(_ts(parts[1]) * scale, _ts(parts[2]) * scale, lines))
    else:
        for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
            rows = [r for r in block.strip().split("\n") if r.strip()]
            t = next((i for i, r in enumerate(rows) if "-->" in r), None)
            if t is None:
                continue
            a, b = rows[t].split("-->")
            lines = [re.sub(r"<[^>]+>", "", r).strip() for r in rows[t + 1:]]
            subs.append(Sub(_ts(a) * scale, _ts(b.split()[0]) * scale, [x for x in lines if x]))
    return sorted((s for s in subs if s.lines), key=lambda s: s.start)


# ── Đo ───────────────────────────────────────────────────────────────────

def k_weight(x: np.ndarray) -> np.ndarray:
    from scipy.signal import lfilter

    y = x.astype(np.float64)
    for b, a in _KW:
        y = lfilter(b, a, y, axis=0)
    return y


def lufs(kw: np.ndarray) -> float:
    if len(kw) == 0:
        return float("-inf")
    ms = float(np.sum(np.mean(kw ** 2, axis=0)))
    return -0.691 + 10 * np.log10(ms) if ms > 0 else float("-inf")


def frame_db(mono: np.ndarray, sr: int) -> np.ndarray:
    w = max(1, int(sr * FRAME))
    n = len(mono) // w
    rms = np.sqrt(np.mean(mono[: n * w].reshape(n, w).astype(np.float64) ** 2, axis=1))
    return 20 * np.log10(rms + 1e-12)


@dataclass
class Island:
    s: int                          # khung đầu / cuối (5 ms), cuối = khung cuối + 1
    e: int
    level: float = 0.0              # dBFS RMS phần có tiếng
    flags: list[str] = field(default_factory=list)


def islands(db: np.ndarray, gate: float) -> list[Island]:
    on = db > gate
    out: list[Island] = []
    i, n = 0, len(on)
    merge = int(MERGE_GAP / FRAME)
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        while j < n and on[j]:
            j += 1
        if out and i - out[-1].e < merge:
            out[-1].e = j
        else:
            out.append(Island(i, j))
        i = j
    return [x for x in out if (x.e - x.s) * FRAME >= 0.06]


def check_edges(isl: Island, db: np.ndarray, gate: float, env1: np.ndarray) -> None:
    """Vào / ra đột ngột: đường bao 1 ms đi từ «mức đầy −30 dB» tới «mức đầy −10 dB» trong ≤ 1 ms = bước nhảy
    (câu bị cắt ngang / không fade). Giọng tự nhiên và fade ≥ 10 ms đều cần vài ms trở lên. Hiệu chỉnh trên file AAC đã
    biết đáp án (audit_out/dub_audit/calib_edges.py): track cũ 16/21 câu vào đột ngột · 15/17 ra đột ngột bị bắt;
    track mới (có fade) 1 / 0."""
    seg = db[isl.s:isl.e]
    isl.level = float(10 * np.log10(np.mean(10 ** (seg[seg > gate] / 10)))) if np.any(seg > gate) else gate
    s_ms, e_ms = int(isl.s * FRAME * 1000), int(isl.e * FRAME * 1000)
    e = env1[s_ms:e_ms]
    if len(e) < 20:
        return
    ref = float(np.percentile(e, 90))
    on = s_ms + int(np.argmax(e > ref - 30))
    up = np.where(env1[on:on + 60] > ref - 10)[0]
    off = e_ms - 1 - int(np.argmax(e[::-1] > ref - 30))
    dn = np.where(env1[max(s_ms, off - 60):off + 1][::-1] > ref - 10)[0]
    if len(up) and up[0] <= 1:
        isl.flags.append("vào đột ngột")
    if len(dn) and dn[0] <= 1:
        isl.flags.append("ra đột ngột (cụt)")


def env_1ms(mono: np.ndarray, sr: int) -> np.ndarray:
    w = max(1, int(sr * 0.001))
    n = len(mono) // w
    return 20 * np.log10(np.sqrt(np.mean(mono[: n * w].reshape(n, w).astype(np.float64) ** 2, axis=1)) + 1e-12)


@dataclass
class Result:
    name: str
    status: str                     # PASS / FAIL / WARN / N/A
    value: str
    detail: list[str] = field(default_factory=list)


def qa(path: Path, *, target: float = -14.0, tol: float = 1.0, tp_max: float = -1.0, subs_path: Path | None = None,
       subs_scale: float = 1.0) -> tuple[list[Result], dict]:
    R: list[Result] = []
    info = probe(path)
    a_st = info["audio"]
    if a_st is None:
        return [Result("Âm thanh", "FAIL", "file không có luồng âm thanh")], {}
    sr_native = int(a_st.get("sample_rate") or 0)
    ch = int(a_st.get("channels") or 1)
    br = int(a_st.get("bit_rate") or 0)
    codec = a_st.get("codec_name", "?")
    # app đặt AAC 192k; bản chỉ có giọng (nhiều đoạn lặng) bộ mã hoá AAC của ffmpeg tiêu ít bit hơn → trung bình
    # 130–190 kbps là bình thường (WARN), dưới 128 kbps mới là lỗi
    rate_ok = sr_native in (44100, 48000)
    st_fmt = "FAIL" if not rate_ok or (codec == "aac" and br and br < 128_000) else \
        ("WARN" if codec == "aac" and br and br < 190_000 else "PASS")
    R.append(Result("Định dạng", st_fmt, f"{codec} · {sr_native} Hz · {ch} kênh · {br // 1000 if br else '?'} kbps",
                    [] if st_fmt == "PASS" else ["cần 48 kHz (hoặc 44,1 kHz)" if not rate_ok else
                                                 "AAC đặt 192k, trung bình thấp hơn do nhiều đoạn lặng" if st_fmt == "WARN"
                                                 else "AAC dưới 128 kbps"]))

    ln = loudnorm_scan(path, target)
    eb = ebur128_scan(path)
    I, TP, LRA = ln.get("input_i"), ln.get("input_tp"), ln.get("input_lra")
    if I is not None:
        R.append(Result("Loudness tích hợp", "PASS" if abs(I - target) <= tol else "FAIL",
                        f"{I:.1f} LUFS (mục tiêu {target:.0f} ±{tol:.0f}) · ebur128 {eb.get('I', float('nan')):.1f}"))
        R.append(Result("True peak", "PASS" if TP <= tp_max else "FAIL",
                        f"{TP:.1f} dBTP (≤ {tp_max:.1f}) · ebur128 {eb.get('TP', float('nan')):.1f}"))
        R.append(Result("Dải động (LRA)", "PASS" if LRA <= 11 else "WARN", f"{LRA:.1f} LU (≤ 11 cho video ngắn)"))

    x = decode(path, ch)
    if sr_native and sr_native != SR:
        import soxr

        x48 = soxr.resample(x, sr_native, SR).astype(np.float32)
    else:
        x48 = x
    clip = int(np.sum(np.abs(x) >= 0.9999))
    peak = float(np.abs(x).max()) if len(x) else 0.0
    R.append(Result("Clip", "PASS" if clip == 0 else "FAIL",
                    f"{clip} mẫu chạm trần · đỉnh mẫu {20 * np.log10(peak + 1e-12):.2f} dBFS"))
    dc = [float(np.mean(x[:, c])) for c in range(x.shape[1])] if len(x) else [0.0]
    R.append(Result("DC offset", "PASS" if max(abs(d) for d in dc) < 0.001 else "WARN",
                    " · ".join(f"{d:+.5f}" for d in dc)))

    v_dur, a_dur = info["v_dur"], info["a_dur"]
    if v_dur and a_dur:
        diff = (a_dur - v_dur) * 1000
        R.append(Result("Lệch thời lượng", "PASS" if abs(diff) < 50 else "FAIL",
                        f"audio {a_dur:.3f}s · video {v_dur:.3f}s · lệch {diff:+.0f} ms"))
    elif info["v_dur"] is None:
        R.append(Result("Lệch thời lượng", "N/A", "file không có hình"))

    mono = x48.mean(axis=1)
    db = frame_db(mono, SR)
    ref = float(np.percentile(db, 95)) if len(db) else -120.0
    gate = max(-60.0, ref - 40.0)
    isl = islands(db, gate)
    subs = load_subs(subs_path, subs_scale) if subs_path else []
    # nền: mức các khung KHÔNG nằm trong câu nào (giữa các câu)
    mask = np.ones(len(db), bool)
    for it in isl:
        mask[it.s:it.e] = False
    if subs:
        for s in subs:
            mask[int(s.start / FRAME):int(s.end / FRAME) + 1] = False
    bg = db[mask]
    bg_level = float(np.median(bg)) if len(bg) else -120.0
    # «chỉ có giọng» khi giữa các câu thật sự lặng VÀ khoảng lặng chiếm phần đáng kể (≥ 8 % thời lượng);
    # tiếng liên tục cả bài (nhạc nền / âm gốc) thì không tách riêng được giọng
    silent_bg = bg_level < -65 and len(bg) >= 0.08 * len(db)
    kw = k_weight(x48)

    # câu: theo phụ đề nếu có (chỉ lấy phần có tiếng trong khung), không thì theo các đoạn có tiếng (nền im lặng)
    w = int(SR * FRAME)
    segments: list[tuple[int, int]] = [(int(s.start / FRAME), int(s.end / FRAME)) for s in subs] if subs else \
        ([(i.s, i.e) for i in isl] if silent_bg else [])

    def voiced(a: int, b: int) -> np.ndarray:
        fr = np.arange(a, min(b, len(db)))
        if len(fr):
            # có nền: vẫn chỉ lấy các khung gần mức to nhất của câu (phần có giọng) — khung phụ đề được kéo ≥ 1 s nên câu
            # ngắn («Không bắt» 0,5 s) gồm nửa khung là nền đã duck → đo nhỏ hơn thật 2–3 LU
            fr = fr[db[fr] > (gate if silent_bg else max(gate, float(db[fr].max()) - 20.0))]
        return np.concatenate([kw[f * w:(f + 1) * w] for f in fr]) if len(fr) else kw[:0]

    # câu có < 0,8 s giọng («Gà rừng») đo LUFS trên vài khung 100 ms + nền đã duck → lệch 2–2,6 LU dù track giọng riêng
    # đều ±1,5 LU (audit_out/m7/kweight_probe.py) → không tính vào độ đều
    seg_v = [voiced(a, b) for a, b in segments if (b - a) * FRAME >= 0.4]
    seg_l = [lufs(v) for v in seg_v if len(v) >= 0.8 * SR]
    seg_l = [v for v in seg_l if np.isfinite(v)]
    if seg_l:
        med = float(np.median(seg_l))
        out = [(k, v) for k, v in enumerate(seg_l) if abs(v - med) > 2.0]
        R.append(Result("Độ đều giữa các câu", "PASS" if not out else ("WARN" if len(out) <= max(1, len(seg_l) // 20) else "FAIL"),
                        f"{len(seg_l)} câu · trung vị {med:.1f} LUFS · p5–p95 {np.percentile(seg_l, 5):.1f} … "
                        f"{np.percentile(seg_l, 95):.1f} · {len(out)} câu lệch > 2 LU",
                        [f"câu {k + 1}: {v:.1f} LUFS ({v - med:+.1f})" for k, v in out[:10]]))
    else:
        R.append(Result("Độ đều giữa các câu", "N/A", "không tách được câu (có nền mà không có --subs)"))

    if silent_bg:
        R.append(Result("Giọng / nền", "N/A", f"không có nhạc nền / âm gốc (giữa các câu {bg_level:.0f} dBFS)"))
    elif subs:
        v_l = lufs(np.concatenate([kw[a * w:b * w] for a, b in segments])) if segments else float("-inf")
        sm = np.zeros(len(kw), bool)
        sm[: len(mask) * w] = np.repeat(mask, w)[: len(kw)]
        b_l = lufs(kw[sm]) if sm.any() else float("-inf")
        diff = v_l - b_l
        R.append(Result("Giọng / nền", "PASS" if diff >= 12 else "FAIL",
                        f"đoạn có giọng {v_l:.1f} LUFS · nền lúc không thoại {b_l:.1f} LUFS · chênh {diff:.1f} dB (≥ 12)",
                        ["ước lượng: nền lúc có thoại đã bị ducking nên chênh thật còn lớn hơn"]))
    else:
        R.append(Result("Giọng / nền", "N/A", "có nền nhưng không có --subs để biết chỗ nào là thoại"))

    # câu vào / ra đột ngột, cụm âm lạ cuối câu (chỉ đo được khi nền im lặng)
    if silent_bg and isl:
        env1 = env_1ms(mono, SR)
        for it in isl:
            check_edges(it, db, gate, env1)
        for k in range(1, len(isl)):
            a, b = isl[k - 1], isl[k]
            gap = (b.s - a.e) * FRAME
            if (b.e - b.s) * FRAME <= 0.45 and gap <= 0.45 and "ra đột ngột (cụt)" in b.flags:
                b.flags.append(f"cụm âm lạ sau câu trước {gap * 1000:.0f} ms («rẹt»)")
        bad = [it for it in isl if it.flags]
        n_in = sum("vào đột ngột" in it.flags for it in isl)
        n_out = sum("ra đột ngột (cụt)" in it.flags for it in isl)
        # giọng tự nhiên không tắt trong ≤ 1 ms (cuối câu luôn có đuôi) → ra đột ngột / «rẹt» là lỗi thật. VÀO nhanh thì
        # phụ âm bật (đ, b, c, k…) ở đầu câu / sau chỗ ngắt cũng có: bản xuất đã sửa đo được 0–3 câu / 58–79, track lỗi
        # cũ 10 vào + 18 ra / 96 (audit_out/fix2/edge_compare.py, onset_isolate.py)
        fail = n_out > max(1, 0.02 * len(isl)) or n_in > max(3, 0.06 * len(isl))
        R.append(Result("Câu bị cụt / tách / «rẹt»", "PASS" if not bad else ("FAIL" if fail else "WARN"),
                        f"{len(bad)}/{len(isl)} câu có dấu hiệu (vào đột ngột {n_in} · ra đột ngột {n_out})",
                        [f"{it.s * FRAME:7.2f}–{it.e * FRAME:6.2f}s: {', '.join(it.flags)}" for it in bad[:20]]
                        + ([] if fail or not bad else ["ít chỗ vào nhanh — thường là phụ âm bật (đ, b, c, k) ở đầu câu "
                                                        "hoặc sau chỗ ngắt; nghe lại để chắc"])))
    else:
        R.append(Result("Câu bị cụt / tách / «rẹt»", "N/A", "có nền: không tách riêng được giọng"))

    # lặng bất thường giữa các câu thoại
    sil = [(s, e) for s, e in silencedetect(path, -50.0, 1.5) if np.isfinite(e)]
    first = isl[0].s * FRAME if isl else 0.0
    last = isl[-1].e * FRAME if isl else 0.0
    inner = [(s, e) for s, e in sil if s > first + 0.05 and e < last - 0.05]
    missing = [(s, e, sb) for s, e in inner for sb in subs if min(e, sb.end) - max(s, sb.start) > 0.5]
    detail = [f"{s:.2f}–{e:.2f}s ({e - s:.1f}s)" for s, e in inner[:15]]
    detail += [f"phụ đề «{' '.join(sb.lines)[:40]}» ({sb.start:.2f}s) không có giọng" for s, e, sb in missing[:10]]
    R.append(Result("Lặng > 1,5 s giữa câu", "FAIL" if missing else ("WARN" if inner else "PASS"),
                    f"{len(inner)} khoảng lặng giữa thoại · {len(missing)} chỗ có phụ đề mà không có giọng", detail))

    if subs:
        long_lines = [s for s in subs if any(len(ln) > 42 for ln in s.lines)]
        many = [s for s in subs if len(s.lines) > 2]
        short = [s for s in subs if s.end - s.start < 1.0]
        overlap = [(a, b) for a, b in zip(subs, subs[1:]) if b.start < a.end - 0.001]
        ok = not (long_lines or many or short or overlap)
        R.append(Result("Phụ đề", "PASS" if ok else "FAIL",
                        f"{len(subs)} câu · >2 dòng {len(many)} · dòng >42 ký tự {len(long_lines)} · hiện <1 s {len(short)} · "
                        f"chồng nhau {len(overlap)}",
                        [f"{s.start:.2f}s hiện {s.end - s.start:.2f}s: «{' / '.join(s.lines)[:50]}»" for s in short[:8]]
                        + [f"{s.start:.2f}s dòng dài: «{max(s.lines, key=len)[:60]}» ({len(max(s.lines, key=len))})"
                           for s in long_lines[:5]]
                        + [f"{a.end:.2f}s > {b.start:.2f}s: «{' '.join(a.lines)[:25]}» chồng «{' '.join(b.lines)[:25]}»"
                           for a, b in overlap[:5]]))

    raw = {"file": str(path), "probe": {"codec": codec, "sample_rate": sr_native, "channels": ch, "bit_rate": br,
                                        "audio_duration": a_dur, "video_duration": v_dur},
           "loudnorm": ln, "ebur128": eb, "clipped_samples": clip, "sample_peak_db": 20 * np.log10(peak + 1e-12),
           "dc_offset": dc, "gate_db": gate, "background_db": bg_level, "segments_lufs": seg_l,
           "islands": [{"start": i.s * FRAME, "end": i.e * FRAME, "level_db": i.level, "flags": i.flags} for i in isl],
           "silences": sil, "results": [r.__dict__ for r in R]}
    return R, raw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="video / audio đã xuất")
    ap.add_argument("--subs", help="phụ đề đúng mốc của file (ASS trong thư mục job, hoặc SRT)")
    ap.add_argument("--subs-scale", type=float, default=1.0,
                    help="nhân mốc phụ đề (vd. SRT theo thời gian gốc + video tăng tốc 105%% → 1/1.05)")
    ap.add_argument("--target", type=float, default=-14.0, help="loudness mục tiêu (LUFS)")
    ap.add_argument("--json", help="ghi kết quả chi tiết ra file JSON")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    path = Path(a.file)
    R, raw = qa(path, target=a.target, subs_path=Path(a.subs) if a.subs else None, subs_scale=a.subs_scale)
    print(f"QA âm thanh: {path.name}")
    for r in R:
        print(f"  [{r.status:4}] {r.name:26} {r.value}")
        for d in r.detail:
            print(f"           · {d}")
    fails = [r.name for r in R if r.status == "FAIL"]
    print(f"KẾT LUẬN: {'ĐẠT' if not fails else 'KHÔNG ĐẠT'} · {sum(r.status == 'PASS' for r in R)} PASS · "
          f"{len(fails)} FAIL" + (f" ({', '.join(fails)})" if fails else "")
          + f" · {sum(r.status == 'WARN' for r in R)} WARN")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(raw, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
