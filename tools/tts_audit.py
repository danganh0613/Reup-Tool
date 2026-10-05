"""Audit file lồng tiếng: định dạng, đuôi câu rè, click ở chỗ nối, im lặng / cắt cụt / chồng tiếng, (tuỳ chọn) nhận dạng lại.

    python tools/tts_audit.py                       # 4 track giọng mới nhất trong output/voice (kèm .srt đồng bộ)
    python tools/tts_audit.py a.wav b.mp4 --srt x.srt
    python tools/tts_audit.py --asr                 # thêm nhận dạng lại bằng faster-whisper, so với chữ gốc
    python tools/tts_audit.py --out audit_out/before --samples audit_out/before/samples.json

Chỉ ĐỌC file nguồn; kết quả (bảng .md, .csv, .json) ghi vào thư mục --out (mặc định audit_out/).
Mỗi câu (cue) được đo trên đúng đoạn của nó trong track (theo .srt đồng bộ mà tool ghi cạnh .wav):
  · đuôi 200 ms cuối giọng: mức đỉnh/RMS, tỉ lệ năng lượng tần cao, độ «phẳng» phổ → tiếng rè/«rẹt» cuối câu
  · biên đầu/cuối đoạn: biên độ khi bắt đầu/kết thúc (không fade → «tách»), nhảy biên độ đột ngột (click/pop)
  · lặng bất thường giữa câu, câu bị cắt khi còn đang kêu, câu chồng lên câu sau
  · tốc độ đọc (ký tự/giây) → câu bị tăng tốc quá mức
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.services.srt_utils import load_srt  # noqa: E402

FFMPEG = str(ROOT / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"))
FFPROBE = str(ROOT / "bin" / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe"))
if not Path(FFMPEG).exists():
    FFMPEG, FFPROBE = "ffmpeg", "ffprobe"

FRAME = 0.010                    # khung 10 ms
TAIL = 0.200                     # 200 ms cuối giọng


# ── Đọc file ─────────────────────────────────────────────────────────────

def probe(path: Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=sample_rate,channels,sample_fmt,codec_name,bits_per_sample:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        d = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        d = {}
    st = (d.get("streams") or [{}])[0]
    return {"codec": st.get("codec_name", "?"), "sample_rate": int(st.get("sample_rate") or 0),
            "channels": int(st.get("channels") or 0), "sample_fmt": st.get("sample_fmt", "?"),
            "bits": int(st.get("bits_per_sample") or 0), "duration": float((d.get("format") or {}).get("duration") or 0)}


def load_mono(path: Path, rate: int) -> np.ndarray:
    """Âm thanh mono float32 [-1, 1] ở ``rate`` (giữ tần số gốc để thấy đúng nhiễu cao tần)."""
    out = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                         capture_output=True)
    return np.frombuffer(out.stdout, dtype=np.float32).copy()


# ── Đo ───────────────────────────────────────────────────────────────────

def db(x: float) -> float:
    return float(20 * np.log10(max(x, 1e-9)))


def frame_rms(a: np.ndarray, rate: int) -> np.ndarray:
    w = max(1, int(rate * FRAME))
    n = len(a) // w
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt(np.mean(a[: n * w].reshape(n, w) ** 2, axis=1))


def spectral(a: np.ndarray, rate: int) -> tuple[float, float]:
    """(tỉ lệ năng lượng > 4 kHz, độ phẳng phổ 0..1) — tiếng rè / xì có cả hai đều cao."""
    if len(a) < 64:
        return 0.0, 0.0
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a)))) ** 2 + 1e-12
    freqs = np.fft.rfftfreq(len(a), 1 / rate)
    hf = float(spec[freqs > 4000].sum() / spec.sum())
    band = spec[(freqs > 300) & (freqs < min(11000, rate / 2 - 100))]
    flat = float(np.exp(np.mean(np.log(band))) / np.mean(band)) if len(band) else 0.0
    return hf, flat


def clicks(a: np.ndarray, rate: int, t0: float = 0.0) -> list[float]:
    """Thời điểm có bước nhảy biên độ đột ngột (vượt hẳn nền quanh đó) — click/pop."""
    if len(a) < 10:
        return []
    d = np.abs(np.diff(a))
    w = max(8, int(rate * 0.002))
    k = np.ones(w * 2 + 1, dtype=np.float32) / (w * 2 + 1)
    local = np.convolve(d, k, mode="same") + 1e-4
    hit = np.where((d > 0.12) & (d > 7.0 * local))[0]
    out: list[float] = []
    for i in hit:
        t = t0 + i / rate
        if not out or t - out[-1] > 0.005:
            out.append(t)
    return out


@dataclass
class CueStat:
    idx: int
    text: str
    start: float
    end: float
    src_start: float | None = None
    src_slot: float | None = None
    speech: float = 0.0               # thời lượng có tiếng thật trong đoạn
    cps: float = 0.0                  # ký tự / giây
    tail_rms_db: float = -120.0
    tail_peak_db: float = -120.0
    tail_hf: float = 0.0
    body_hf: float = 0.0
    tail_flat: float = 0.0
    start_amp: float = 0.0            # biên độ ở mẫu đầu / cuối đoạn (lớn = vào/ra đột ngột, không fade)
    end_amp: float = 0.0
    end_decay_db: float = 0.0         # 20 ms cuối so với 200 ms trước đó (gần 0 = bị cắt khi đang kêu)
    lead: float = 0.0                 # lặng trước / sau phần có tiếng (trong phần dữ liệu khác 0)
    trail: float = 0.0
    tail_burst: float = 0.0           # cụm âm lạ SAU khi đã đọc xong (cách phần chính một khoảng lặng), giây
    max_gap: float = 0.0              # khoảng lặng dài nhất giữa câu
    overlap_next: float = 0.0
    clicks: list[float] = field(default_factory=list)
    asr: str = ""
    asr_ratio: float | None = None
    flags: list[str] = field(default_factory=list)


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s.lower())
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def analyse_cue(a: np.ndarray, rate: int, c, nxt_start: float | None) -> CueStat:
    st = CueStat(c.index, c.plain, c.start, c.end)
    # lùi 30 ms: mốc .srt làm tròn ms + tool làm tròn số mẫu lặng → đầu câu thật có thể sớm hơn mốc vài ms
    s0 = max(0, int((c.start - 0.03) * rate))
    s1 = min(len(a), int(c.end * rate) + int(0.25 * rate))
    if nxt_start is not None:
        s1 = min(s1, int((nxt_start - 0.03) * rate))
    seg = a[s0:s1]
    if len(seg) < int(0.05 * rate):
        st.flags.append("đoạn rỗng")
        return st
    rms = frame_rms(seg, rate)
    peak = float(rms.max()) if len(rms) else 0.0
    thr = max(peak * 10 ** (-40 / 20), 10 ** (-55 / 20))
    on = np.where(rms > thr)[0]
    if len(on) == 0:
        st.flags.append("im lặng hoàn toàn")
        return st
    f0, f1 = int(on[0]), int(on[-1]) + 1
    st.speech = (f1 - f0) * FRAME
    letters = len(re.sub(r"\W", "", st.text))
    st.cps = letters / st.speech if st.speech > 0 else 0.0
    # lặng dài nhất giữa câu
    gaps, run = [], 0
    for v in rms[f0:f1] > thr:
        run = 0 if v else run + 1
        gaps.append(run)
    st.max_gap = max(gaps) * FRAME if gaps else 0.0
    # đuôi 200 ms cuối phần có tiếng
    w = int(rate * FRAME)
    e = f1 * w
    tail = seg[max(0, e - int(TAIL * rate)): e]
    body = seg[f0 * w: max(f0 * w + 1, e - int(TAIL * rate))]
    st.tail_rms_db = db(float(np.sqrt(np.mean(tail ** 2)))) if len(tail) else -120.0
    st.tail_peak_db = db(float(np.abs(tail).max())) if len(tail) else -120.0
    st.tail_hf, st.tail_flat = spectral(tail, rate)
    if len(body) > int(0.1 * rate):
        hfs = [spectral(body[i: i + int(0.05 * rate)], rate)[0] for i in range(0, len(body) - int(0.05 * rate), int(0.05 * rate))]
        st.body_hf = float(np.median(hfs)) if hfs else 0.0
    # biên: mẫu đầu tiên / cuối cùng KHÔNG phải số 0 của đoạn (tool ghi lặng = 0 → vào/ra đột ngột thì biên độ lớn)
    nz = np.where(np.abs(seg) > 1e-4)[0]
    if len(nz):
        st.start_amp = float(np.abs(seg[nz[0]: nz[0] + max(1, int(0.002 * rate))]).max())
        st.end_amp = float(np.abs(seg[max(0, nz[-1] - int(0.002 * rate)): nz[-1] + 1]).max())
        st.lead = max(0.0, f0 * FRAME - nz[0] / rate)
        st.trail = max(0.0, (nz[-1] + 1) / rate - f1 * FRAME)
    # cụm âm lạ cuối câu: lặng ≥ 50 ms trong 450 ms cuối rồi lại có tiếng tới hết → «rẹt»/«nói thêm» sau khi đọc xong
    act = rms[f0:f1] > thr
    k = len(act) - 1
    while k >= 0 and act[k]:
        k -= 1
    g_end = k
    while k >= 0 and not act[k]:
        k -= 1
    gap_frames = g_end - k
    burst = (len(act) - 1 - g_end) * FRAME
    if gap_frames >= 5 and k >= 0 and 0 < burst <= 0.45:
        bseg = seg[(f0 + g_end + 1) * int(rate * FRAME): f1 * int(rate * FRAME)]
        _hf, flat = spectral(bseg, rate)
        if st.end_amp > 0.05 or st.trail < 0.02 or flat > 0.3:
            st.tail_burst = burst
    last20 = seg[max(0, e - int(0.02 * rate)): e]
    prev = seg[max(0, e - int(0.22 * rate)): max(0, e - int(0.02 * rate))]
    if len(last20) and len(prev):
        st.end_decay_db = db(float(np.sqrt(np.mean(last20 ** 2)))) - db(float(np.sqrt(np.mean(prev ** 2))))
    st.clicks = [round(t, 3) for t in clicks(seg, rate, s0 / rate)]
    if nxt_start is not None:
        st.overlap_next = max(0.0, c.end - nxt_start)
    # gắn cờ
    if st.tail_hf > max(0.30, 2.2 * st.body_hf + 0.05) and st.tail_rms_db > -45:
        st.flags.append("đuôi rè (cao tần)")
    if st.tail_flat > 0.35 and st.tail_rms_db > -45:
        st.flags.append("đuôi như nhiễu")
    if st.start_amp > 0.05:
        st.flags.append("vào đột ngột")
    if st.end_amp > 0.05:
        st.flags.append("ra đột ngột")
    if st.end_decay_db > -3 and st.tail_rms_db > -35:
        st.flags.append("cắt cụt")
    if st.tail_burst:
        st.flags.append(f"cụm âm lạ cuối câu {st.tail_burst * 1000:.0f} ms")
    if st.max_gap > 0.8:
        st.flags.append(f"lặng {st.max_gap:.1f}s giữa câu")
    if st.cps > 21:
        st.flags.append(f"đọc quá nhanh {st.cps:.0f} kt/s")
    if st.overlap_next > 0.02:
        st.flags.append(f"chồng câu sau {st.overlap_next:.2f}s")
    if st.clicks:
        st.flags.append(f"{len(st.clicks)} click")
    return st


def asr_compare(stats: list[CueStat], a: np.ndarray, rate: int, model_size: str) -> None:
    """Nhận dạng lại từng câu bằng faster-whisper (tiếng Việt) và so với chữ gốc."""
    try:
        model = _whisper(model_size)
    except Exception as exc:
        print("  (bỏ qua nhận dạng lại:", exc, ")")
        return
    a16 = a if rate == 16000 else _resample(a, rate, 16000)
    for st in stats:
        s0, s1 = int(max(0.0, st.start - 0.05) * 16000), int((st.end + 0.15) * 16000)
        seg = a16[s0:s1]
        if len(seg) < 1600:
            continue
        segs, _info = model.transcribe(seg, language="vi", beam_size=5, vad_filter=False, condition_on_previous_text=False)
        st.asr = " ".join(s.text.strip() for s in segs).strip()
        # so trên chữ đã chuẩn hoá (số → chữ) ở CẢ hai phía: «199.000đ» và «một trăm chín mươi chín nghìn đồng» là một
        ref_s, hyp_s = norm_text(spoken(st.text)), norm_text(spoken(st.asr))
        st.asr_ratio = difflib.SequenceMatcher(None, ref_s, hyp_s).ratio()
        ref, hyp = ref_s.split(), hyp_s.split()
        if st.asr_ratio < 0.7:
            if len(hyp) < 0.7 * len(ref):
                st.flags.append(f"đọc sót? (khớp {st.asr_ratio:.0%})")
            elif len(hyp) > 1.3 * len(ref):
                st.flags.append(f"đọc lặp/thừa? (khớp {st.asr_ratio:.0%})")
            else:
                st.flags.append(f"đọc sai chữ? (khớp {st.asr_ratio:.0%})")


_W = {}


def _whisper(size: str):
    if size not in _W:
        from app.services.whisper_stt import load_model

        _W[size] = load_model(size, "auto", "auto")
    return _W[size]


def spoken(text: str) -> str:
    """Chữ «đọc thành tiếng» (số, %, đơn vị → chữ) bằng bộ chuẩn hoá của VieNeu nếu có; không có thì giữ nguyên."""
    try:
        from vieneu_utils.phonemize_text import _get_normalizer

        return _get_normalizer().normalize(text)
    except Exception:
        return text


def _resample(a: np.ndarray, src: int, dst: int) -> np.ndarray:
    n = int(len(a) * dst / src)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)


# ── Một file ─────────────────────────────────────────────────────────────

def audit_file(path: Path, srt: Path | None, src_srt: Path | None, asr: str | None) -> dict:
    info = probe(path)
    rate = info["sample_rate"] or 24000
    a = load_mono(path, rate)
    res = {"file": str(path), **info, "samples": int(len(a)), "peak_db": db(float(np.abs(a).max()) if len(a) else 0.0),
           "rms_db": db(float(np.sqrt(np.mean(a ** 2))) if len(a) else 0.0)}
    clip = int(np.sum(np.abs(a) >= 0.999))
    res["clipped_samples"] = clip
    res["all_clicks"] = [round(t, 3) for t in clicks(a, rate)]
    stats: list[CueStat] = []
    if srt and srt.exists():
        cues = [c for c in load_srt(srt) if c.plain]
        src = [c for c in load_srt(src_srt) if c.plain] if src_srt and src_srt.exists() else []
        j = 0                                     # ghép câu đồng bộ ↔ câu gốc theo chữ (câu lỗi bị bỏ thì lệch số thứ tự)
        for i, c in enumerate(cues):
            st = analyse_cue(a, rate, c, cues[i + 1].start if i + 1 < len(cues) else None)
            k = next((x for x in range(j, len(src)) if src[x].plain == c.plain), None)
            if k is not None:
                j = k + 1
                st.src_start = src[k].start
                st.src_slot = (src[k + 1].start - src[k].start) if k + 1 < len(src) else (src[k].end - src[k].start)
                if st.speech > st.src_slot + 0.15:
                    st.flags.append(f"dài hơn khung gốc {st.speech - st.src_slot:+.2f}s")
                if c.start - src[k].start > 0.5:
                    st.flags.append(f"trễ {c.start - src[k].start:.2f}s so với phụ đề gốc")
            stats.append(st)
        if asr:
            asr_compare(stats, a, rate, asr)
    res["cues"] = [st.__dict__ for st in stats]
    return res


def latest_tracks(n: int) -> list[tuple[Path, Path | None, Path | None]]:
    vdir = ROOT / "output" / "voice"
    wavs = sorted(vdir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    out = []
    for w in wavs:
        meta = w.with_suffix(".json")
        src = None
        if meta.exists():
            try:
                src = Path(json.loads(meta.read_text(encoding="utf-8")).get("srt") or "") or None
            except Exception:
                src = None
        out.append((w, w.with_suffix(".srt") if w.with_suffix(".srt").exists() else None, src))
    return out


# ── Báo cáo ──────────────────────────────────────────────────────────────

def report(results: list[dict], out_dir: Path, compact: bool = False) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Kết quả tts_audit", ""]
    rows = []
    all_results = results
    if compact:
        # bộ câu test: mỗi file một câu → một bảng gọn
        lines += ["| file | Hz | dài (s) | lặng đầu/cuối (s) | vào/ra | cụm lạ cuối | click | khớp ASR | nghe lại được | cờ |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in results:
            for c in r["cues"]:
                asr = f"{c['asr_ratio']:.0%}" if c["asr_ratio"] is not None else "–"
                p = Path(r["file"])
                lines.append(f"| {p.relative_to(out_dir).as_posix() if p.is_relative_to(out_dir) else p.name} "
                             f"| {r['sample_rate']} | {r['duration']:.2f} | {c['lead']:.2f}/{c['trail']:.2f} "
                             f"| {c['start_amp']:.2f}/{c['end_amp']:.2f} | {c['tail_burst'] * 1000:.0f} ms | {len(c['clicks'])} "
                             f"| {asr} | {c['asr'][:60]} | {'; '.join(c['flags'])} |")
                rows.append({"file": Path(r["file"]).name, "sample_rate": r["sample_rate"], "duration": r["duration"],
                             **{k: (v if not isinstance(v, list) else "; ".join(map(str, v))) for k, v in c.items()}})
        results = []
    for r in results:
        cues = r["cues"]
        flagged = [c for c in cues if c["flags"]]
        lines += [f"## {Path(r['file']).name}", "",
                  f"- Định dạng: {r['codec']} · {r['sample_rate']} Hz · {r['channels']} kênh · {r['sample_fmt']} "
                  f"· {r['duration']:.2f} s · đỉnh {r['peak_db']:.1f} dBFS · RMS {r['rms_db']:.1f} dBFS "
                  f"· mẫu bị chạm trần {r['clipped_samples']}",
                  f"- Click/pop cả track: {len(r['all_clicks'])}" + (f" (giây: {', '.join(str(t) for t in r['all_clicks'][:12])}"
                                                                   + (" …" if len(r['all_clicks']) > 12 else "") + ")" if r["all_clicks"] else ""),
                  f"- Câu có vấn đề: {len(flagged)}/{len(cues)}", ""]
        if cues:
            lines += ["| # | giây | chữ | tiếng (s) | khung gốc (s) | kt/s | đuôi RMS/đỉnh dB | HF đuôi/thân | phẳng | vào/ra | khớp ASR | cờ |",
                      "|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for c in cues:
                if not c["flags"]:
                    continue
                txt = c["text"][:38] + ("…" if len(c["text"]) > 38 else "")
                asr = f"{c['asr_ratio']:.0%}" if c["asr_ratio"] is not None else "–"
                slot = f"{c['src_slot']:.2f}" if c["src_slot"] is not None else "–"
                lines.append(f"| {c['idx']} | {c['start']:.2f}–{c['end']:.2f} | {txt} | {c['speech']:.2f} | {slot} | {c['cps']:.0f} | "
                             f"{c['tail_rms_db']:.0f}/{c['tail_peak_db']:.0f} | {c['tail_hf']:.2f}/{c['body_hf']:.2f} | "
                             f"{c['tail_flat']:.2f} | {c['start_amp']:.2f}/{c['end_amp']:.2f} | {asr} | {'; '.join(c['flags'])} |")
            lines.append("")
        for c in cues:
            rows.append({"file": Path(r["file"]).name, **{k: (v if not isinstance(v, list) else "; ".join(map(str, v)))
                                                          for k, v in c.items()}})
    md = "\n".join(lines)
    (out_dir / "tts_audit.md").write_text(md, encoding="utf-8")
    (out_dir / "tts_audit.json").write_text(json.dumps(all_results, ensure_ascii=False, indent=1), encoding="utf-8")
    if rows:
        with open(out_dir / "tts_audit.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return md


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="wav/mp3/m4a/mp4; bỏ trống = các track giọng mới nhất")
    ap.add_argument("--srt", help="SRT cho file (khi chỉ có 1 file)")
    ap.add_argument("--src-srt", help="SRT gốc (trước khi dời theo giọng) để so khung thời gian")
    ap.add_argument("--latest", type=int, default=4, help="số track mới nhất khi không chỉ file")
    ap.add_argument("--asr", nargs="?", const="large-v3-turbo", default=None, help="nhận dạng lại (model faster-whisper)")
    ap.add_argument("--samples", help="JSON danh sách {file, text} (bộ câu test) — mỗi file là một câu")
    ap.add_argument("--out", default=str(ROOT / "audit_out"))
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    jobs: list[tuple[Path, Path | None, Path | None]] = []
    tmp_srts: list[Path] = []
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if a.samples:
        # mỗi file = 1 câu → tạo SRT tạm 1 cue phủ cả file
        for it in json.loads(Path(a.samples).read_text(encoding="utf-8")):
            f = Path(it["file"])
            dur = probe(f)["duration"]
            s = out_dir / f"_tmp_{f.parent.name}_{f.stem}.srt"
            end = f"{int(dur // 3600):02d}:{int(dur % 3600 // 60):02d}:{dur % 60:06.3f}".replace(".", ",")
            s.write_text(f"1\n00:00:00,000 --> {end}\n{it['text']}\n", encoding="utf-8")
            tmp_srts.append(s)
            jobs.append((f, s, None))
    elif a.files:
        for f in a.files:
            p = Path(f)
            jobs.append((p, Path(a.srt) if a.srt else (p.with_suffix(".srt") if p.with_suffix(".srt").exists() else None),
                         Path(a.src_srt) if a.src_srt else None))
    else:
        jobs = latest_tracks(a.latest)
    results = []
    for f, s, src in jobs:
        print(f"→ {f.name}" + (f" (srt: {s.name})" if s else " (không có srt)"), flush=True)
        results.append(audit_file(f, s, src, a.asr))
    md = report(results, out_dir, compact=bool(a.samples))
    for s in tmp_srts:
        s.unlink(missing_ok=True)
    print(md)
    print(f"\nĐã ghi: {out_dir / 'tts_audit.md'} · .csv · .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
