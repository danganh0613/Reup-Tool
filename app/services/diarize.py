"""Nhận diện người nói (ai nói câu nào) để lồng tiếng mỗi người một giọng.

Chạy local, không cần mạng sau lần tải model đầu (~28 MB):

1. Mỗi câu phụ đề → cắt đúng đoạn audio → đặc trưng fbank 80 chiều (giống Kaldi) → model CAM++ (3D-Speaker,
   ONNX, chạy bằng ``onnxruntime`` — thư viện đã có sẵn theo faster-whisper) → vector đặc trưng giọng.
2. Gom nhóm các vector (average-linkage, độ giống cosine): tự đoán số người, hoặc theo số người do người dùng chọn.
3. Ước lượng cao độ + khoảng cách formant (độ dài khoang họng) mỗi người → đoán Nam / Nữ để gán giọng đọc.

Kết quả lưu cạnh phụ đề: ``<video>.speakers.json`` = danh sách [đầu, cuối, tên người nói] theo thời gian video gốc,
nên dùng chung được cho phụ đề gốc lẫn bản dịch (cùng mốc thời gian).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config.paths import MODELS_DIR
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.srt_utils import Cue

logger = logging.getLogger(__name__)

SR = 16000
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx")
MODEL_PATH = MODELS_DIR / "speaker" / "campplus_sv_zh_en_16k.onnx"
MODEL_SIZE_MB = 28

#: tên người nói mặc định: "Người 1", "Người 2"…
SPEAKER_PREFIX = "Người"
#: độ giống trung bình tối thiểu để gộp 2 nhóm (chế độ tự đoán số người)
DEFAULT_THRESHOLD = 0.42
MIN_EMBED_SEC = 0.6          # câu ngắn hơn: không dùng để gom nhóm, gán theo nhóm gần nhất
WINDOW_SEC = 3.0             # câu dài: lấy trung bình nhiều cửa sổ
MAX_SPEAKERS = 12


def speaker_name(n: int) -> str:
    return f"{SPEAKER_PREFIX} {n}"


def is_question(text: str | None) -> bool:
    """Câu hỏi (để chia vai hỏi – đáp): dấu «?» / «？», hoặc câu gốc tiếng Trung kết thúc bằng 吗 / 呢 / 嘛 (OCR / ASR hay
    bỏ dấu hỏi)."""
    t = (text or "").strip().rstrip("。.!！… ")
    return t.endswith(("?", "？", "吗", "呢", "嘛"))


@dataclass
class SpeakerInfo:
    name: str
    count: int = 0
    seconds: float = 0.0
    f0: float = 0.0
    gender: str = ""            # "Nam" / "Nữ" / ""
    df: float = 0.0             # khoảng cách formant (Hz) — khoang họng dài (nam) → nhỏ


@dataclass
class SpeakerMap:
    """Ai nói đoạn nào, theo thời gian video gốc. ``voices``: giọng người dùng chọn riêng cho video này (tên người nói
    → mục giọng) — đè lên giọng ưu tiên của mẫu."""
    segments: list[tuple[float, float, str]] = field(default_factory=list)
    speakers: dict[str, SpeakerInfo] = field(default_factory=dict)
    voices: dict[str, dict] = field(default_factory=dict)

    # ── Lưu / đọc ─────────────────────────────────────────────────────────
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1,
                "segments": [[round(a, 3), round(b, 3), s] for a, b, s in self.segments],
                "speakers": {k: {"count": v.count, "seconds": round(v.seconds, 1), "f0": round(v.f0, 1),
                                 "df": round(v.df), "gender": v.gender} for k, v in self.speakers.items()}}
        if self.voices:
            data["voices"] = self.voices
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path | None) -> SpeakerMap | None:
        if not path or not Path(path).exists():
            return None
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            segs = [(float(a), float(b), str(s)) for a, b, s in data.get("segments", [])]
            spk = {str(k): SpeakerInfo(str(k), int(v.get("count", 0)), float(v.get("seconds", 0)),
                                       float(v.get("f0", 0)), str(v.get("gender", "")), float(v.get("df", 0)))
                   for k, v in (data.get("speakers") or {}).items()}
            voices = {str(k): dict(v) for k, v in (data.get("voices") or {}).items() if isinstance(v, dict)}
            m = cls(segs, spk, voices)
            m.recount()
            return m
        except Exception:
            logger.warning("Không đọc được %s", path, exc_info=True)
            return None

    # ── Tra cứu ───────────────────────────────────────────────────────────
    def names(self) -> list[str]:
        """Tên người nói: «Người 1, 2, 3…» theo số; tên khác theo thứ tự xuất hiện (kể cả người thêm tay chưa có câu)."""
        out: list[str] = []
        for _a, _b, s in sorted(self.segments):
            if s not in out:
                out.append(s)
        out += [s for s in self.speakers if s not in out]

        def key(name: str):
            num = name[len(SPEAKER_PREFIX):].strip() if name.startswith(SPEAKER_PREFIX) else ""
            return (0, int(num), 0) if num.isdigit() else (1, 0, out.index(name))
        return sorted(out, key=key)

    def speaker_at(self, start: float, end: float) -> str:
        """Người nói đoạn [start, end]: đoạn đã lưu trùng nhiều nhất; không trùng → đoạn gần nhất (≤ 1 giây)."""
        best, best_ov = "", 0.0
        near, near_d = "", 1.0
        for a, b, s in self.segments:
            ov = min(end, b) - max(start, a)
            if ov > best_ov:
                best, best_ov = s, ov
            elif ov <= 0:
                d = max(a - end, start - b)
                if d < near_d:
                    near, near_d = s, d
        return best or near

    def annotate(self, cues: list[Cue]) -> int:
        """Ghi ``meta["speaker"]`` vào từng cue; trả về số cue đã gán."""
        n = 0
        for c in cues:
            s = self.speaker_at(c.start, c.end)
            if s:
                c.meta["speaker"] = s
                n += 1
        return n

    def recount(self) -> None:
        for info in self.speakers.values():
            info.count, info.seconds = 0, 0.0
        for a, b, s in self.segments:
            info = self.speakers.setdefault(s, SpeakerInfo(s))
            info.count += 1
            info.seconds += max(0.0, b - a)

    @classmethod
    def from_cues(cls, cues: list[Cue], labels: list[str], base: SpeakerMap | None = None) -> SpeakerMap:
        m = cls([(c.start, c.end, s) for c, s in zip(cues, labels) if s],
                {k: SpeakerInfo(k, f0=v.f0, gender=v.gender, df=v.df) for k, v in (base.speakers if base else {}).items()},
                dict(base.voices) if base else {})
        m.recount()
        m.voices = {k: v for k, v in m.voices.items() if k in m.speakers}    # người bị xoá → bỏ giọng chọn riêng
        return m


# ── Model ────────────────────────────────────────────────────────────────

def onnx_available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def ensure_model(log: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> Path:
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 1_000_000:
        return MODEL_PATH
    import requests

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if log:
        log(f"Tải model nhận diện người nói (~{MODEL_SIZE_MB} MB, chỉ lần đầu)…")
    tmp = MODEL_PATH.with_suffix(".part")
    with requests.get(MODEL_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if cancel is not None and cancel.is_set():
                    raise RenderCancelled("Đã huỷ")
                f.write(chunk)
    tmp.replace(MODEL_PATH)
    return MODEL_PATH


class _Embedder:
    def __init__(self, model: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.sess = ort.InferenceSession(str(model), sess_options=opts, providers=["CPUExecutionProvider"])
        self.input = self.sess.get_inputs()[0].name
        meta = self.sess.get_modelmeta().custom_metadata_map
        self.scale = 1.0 if meta.get("normalize_samples", "1") == "1" else 32768.0

    def __call__(self, wav):
        import numpy as np

        feat = fbank(wav * self.scale)
        feat = feat - feat.mean(axis=0, keepdims=True)
        emb = self.sess.run(None, {self.input: feat[None].astype(np.float32)})[0][0]
        return emb / (np.linalg.norm(emb) + 1e-9)


_MEL_CACHE: dict = {}


def _mel_banks(n_mels: int = 80, nfft: int = 512, sr: int = SR, low: float = 20.0):
    import numpy as np

    key = (n_mels, nfft, sr, low)
    if key in _MEL_CACHE:
        return _MEL_CACHE[key]

    def mel(f):
        return 1127.0 * np.log(1.0 + f / 700.0)

    lo, hi = mel(low), mel(sr / 2)
    delta = (hi - lo) / (n_mels + 1)
    bins = mel(np.arange(nfft // 2) * sr / nfft)
    fb = np.zeros((n_mels, nfft // 2 + 1))
    for m in range(n_mels):
        left, center, right = lo + m * delta, lo + (m + 1) * delta, lo + (m + 2) * delta
        up = (bins - left) / (center - left)
        down = (right - bins) / (right - center)
        w = np.where(bins <= center, up, down)
        w[(bins <= left) | (bins >= right)] = 0.0
        fb[m, :nfft // 2] = w
    _MEL_CACHE[key] = fb.T
    return fb.T


def fbank(wav, sr: int = SR, n_mels: int = 80):
    """Log mel filterbank kiểu Kaldi (25 ms / 10 ms, Povey, preemphasis 0.97) — đầu vào của model CAM++."""
    import numpy as np

    flen, shift, nfft = int(0.025 * sr), int(0.010 * sr), 512
    wav = np.asarray(wav, dtype=np.float64)
    if len(wav) < flen:
        wav = np.pad(wav, (0, flen - len(wav)))
    n = 1 + (len(wav) - flen) // shift
    idx = np.arange(flen)[None, :] + shift * np.arange(n)[:, None]
    frames = wav[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    pre = frames.copy()
    pre[:, 1:] -= 0.97 * frames[:, :-1]
    pre[:, 0] -= 0.97 * frames[:, 0]
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(flen) / (flen - 1))) ** 0.85
    spec = np.abs(np.fft.rfft(pre * win, nfft)) ** 2
    return np.log(np.maximum(spec @ _mel_banks(n_mels, nfft, sr), np.finfo(np.float32).eps))


def _pitch(wav) -> float:
    """Cao độ trung vị (Hz) của đoạn có tiếng — tự tương quan; 0 nếu không đo được."""
    import numpy as np

    flen, shift = 960, 320                       # 60 ms / 20 ms (đủ 3 chu kỳ ở 60 Hz)
    lo, hi = SR // 400, SR // 60                 # 60–400 Hz
    if len(wav) < flen * 2:
        return 0.0
    n = 1 + (len(wav) - flen) // shift
    idx = np.arange(flen)[None, :] + shift * np.arange(n)[:, None]
    fr = wav[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)
    energy = (fr ** 2).sum(axis=1)
    keep = energy > max(1e-6, np.percentile(energy, 60) * 0.5)
    win = np.hanning(flen)
    fr = fr[keep] * win
    if not len(fr):
        return 0.0

    def autocorr(x):
        a = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * flen)) ** 2)[..., :hi + 1]
        return a / (a[..., :1] + 1e-12)

    # chia cho tự tương quan của cửa sổ (Boersma) — không thì độ trễ lớn bị thiệt, giọng nam bị đo thành giọng nữ
    ac = autocorr(fr) / np.maximum(autocorr(win), 1e-3)
    seg = ac[:, lo - 1:hi + 2]
    mid = seg[:, 1:-1]
    is_peak = (mid > seg[:, :-2]) & (mid >= seg[:, 2:])
    cand = np.where(is_peak, mid, -np.inf)
    top = cand.max(axis=1, keepdims=True)
    # đỉnh có độ trễ nhỏ nhất mà gần bằng đỉnh cao nhất — tránh nhận nhầm 2 chu kỳ (hụt một quãng tám)
    lag = lo + (cand >= 0.85 * top).argmax(axis=1)
    peak = ac[np.arange(len(ac)), lag]
    f0 = SR / lag[peak > 0.5]
    return float(np.median(f0)) if len(f0) >= 5 else 0.0


def _levinson(r, order: int):
    import numpy as np

    a = np.zeros(order + 1)
    a[0], err = 1.0, r[0]
    for i in range(1, order + 1):
        k = -(r[i] + a[1:i] @ r[i - 1:0:-1]) / err
        a[1:i + 1] = a[1:i + 1] + k * np.r_[a[i - 1:0:-1], 1.0]
        err *= 1 - k * k
        if err <= 0:
            break
    return a


def _dispersion(wav, order: int = 14) -> float:
    """Khoảng cách formant ΔF (Hz, trung vị các khung có tiếng; F2 ≈ 1,5·ΔF, F3 ≈ 2,5·ΔF) — tỉ lệ nghịch với độ dài
    khoang họng: nam ~1000–1150, nữ ~1150–1650. Không đổi khi nam nói giọng cao (cao độ thì đổi). 0 nếu không đo được."""
    import numpy as np

    flen, shift = 512, 256
    n = 1 + (len(wav) - flen) // shift
    if n < 5:
        return 0.0
    idx = np.arange(flen)[None, :] + shift * np.arange(n)[:, None]
    fr = wav[idx] - wav[idx].mean(axis=1, keepdims=True)
    energy = (fr ** 2).sum(axis=1)
    win = np.hamming(flen)
    out = []
    for x in fr[energy > np.percentile(energy, 50)]:
        y = np.append(x[0], x[1:] - 0.97 * x[:-1]) * win
        r = np.correlate(y, y, "full")[flen - 1:flen + order]
        if r[0] <= 0:
            continue
        roots = np.roots(_levinson(r, order))
        roots = roots[np.imag(roots) > 0]
        f = np.angle(roots) * SR / (2 * np.pi)
        bw = -SR / np.pi * np.log(np.abs(roots) + 1e-12)
        f = np.sort(f[(f > 250) & (f < 5000) & (bw < 400)])
        if len(f) >= 3:
            out.append((f[1] / 1.5 + f[2] / 2.5) / 2)
    return float(np.median(out)) if len(out) >= 5 else 0.0


def _cluster(emb, n_speakers: int, threshold: float) -> list[int]:
    """Average-linkage theo độ giống cosine. ``n_speakers`` > 0: gộp tới đúng số đó; 0: gộp tới khi < ngưỡng."""
    import numpy as np

    n = len(emb)
    if n == 0:
        return []
    if n == 1:
        return [0]
    sim = emb @ emb.T
    np.fill_diagonal(sim, -np.inf)
    size = np.ones(n)
    active = np.ones(n, dtype=bool)
    parent = list(range(n))
    count = n
    while count > 1:
        flat = int(np.argmax(sim))
        i, j = divmod(flat, n)
        best = sim[i, j]
        if n_speakers > 0:
            if count <= n_speakers:
                break
        elif best < threshold:
            break
        # gộp j vào i
        row = (size[i] * sim[i] + size[j] * sim[j]) / (size[i] + size[j])
        sim[i, :] = row
        sim[:, i] = row
        sim[i, i] = -np.inf
        sim[j, :] = -np.inf
        sim[:, j] = -np.inf
        size[i] += size[j]
        active[j] = False
        parent[j] = i
        count -= 1

    def root(k: int) -> int:
        while parent[k] != k:
            k = parent[k]
        return k

    return [root(k) for k in range(n)]


def diarize(media: str | Path, cues: list[Cue], ffmpeg: FFmpeg, *, n_speakers: int = 0,
            threshold: float = DEFAULT_THRESHOLD,
            on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] | None = None,
            cancel: threading.Event | None = None) -> SpeakerMap:
    """Gán người nói cho từng cue (theo audio của ``media``). ``n_speakers`` = 0: tự đoán."""
    import numpy as np

    if not onnx_available():
        raise RuntimeError("Thiếu thư viện onnxruntime — bản mã nguồn: chạy «pip install onnxruntime»; bản .exe: cài lại bản mới.")
    if not cues:
        raise RuntimeError("Chưa có phụ đề — nhận diện lời thoại (Lời thoại › «Nguồn & dịch») hoặc chọn SRT trước.")

    def check() -> None:
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Đã huỷ nhận diện người nói")

    model = ensure_model(log, cancel)
    check()
    if log:
        log("Nhận diện người nói: đọc audio…")
    pcm = ffmpeg.decode_pcm(media, sample_rate=SR)
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if not len(audio):
        raise RuntimeError("File không có tiếng.")
    embedder = _Embedder(model)

    def clip(c: Cue):
        a, b = int(max(0.0, c.start) * SR), int(max(0.0, c.end) * SR)
        return audio[a:min(b, len(audio))]

    # ── 1. Vector giọng mỗi câu ───────────────────────────────────────────
    embs: dict[int, np.ndarray] = {}
    long_ok: list[int] = []
    win, hop = int(WINDOW_SEC * SR), int(WINDOW_SEC * SR / 2)
    for k, c in enumerate(cues):
        check()
        wav = clip(c)
        if len(wav) >= int(0.3 * SR):
            if len(wav) > win * 1.5:
                parts = [embedder(wav[s:s + win]) for s in range(0, len(wav) - win // 2, hop)]
                e = np.mean(parts, axis=0)
                embs[k] = e / (np.linalg.norm(e) + 1e-9)
            else:
                embs[k] = embedder(wav)
            if len(wav) >= MIN_EMBED_SEC * SR:
                long_ok.append(k)
        if on_progress:
            on_progress(0.9 * (k + 1) / len(cues))
    if not embs:
        raise RuntimeError("Các câu quá ngắn — không đủ tiếng để nhận diện người nói.")
    base = long_ok if len(long_ok) >= 2 else list(embs)

    # ── 2. Gom nhóm ───────────────────────────────────────────────────────
    X = np.stack([embs[k] for k in base])
    want = min(n_speakers, len(base)) if n_speakers > 0 else 0
    raw = _cluster(X, want, threshold)
    groups: dict[int, list[int]] = {}
    for k, g in zip(base, raw):
        groups.setdefault(g, []).append(k)
    if not want:
        # nhóm quá nhỏ (≤ 1 câu, < 2 giây) thường là tiếng ồn / câu lẫn 2 người → nhập vào nhóm gần nhất
        total = {g: sum(cues[k].duration for k in ks) for g, ks in groups.items()}
        big = [g for g, ks in groups.items() if len(ks) >= 2 or total[g] >= 2.0]
        if big and len(big) < len(groups):
            cent_big = {g: _centroid([embs[k] for k in groups[g]]) for g in big}
            for g in [g for g in groups if g not in big]:
                for k in groups.pop(g):
                    tgt = max(cent_big, key=lambda b: float(embs[k] @ cent_big[b]))
                    groups[tgt].append(k)
    if len(groups) > MAX_SPEAKERS:
        keep = sorted(groups, key=lambda g: -len(groups[g]))[:MAX_SPEAKERS]
        cent = {g: _centroid([embs[k] for k in groups[g]]) for g in keep}
        for g in [g for g in groups if g not in keep]:
            for k in groups.pop(g):
                groups[max(cent, key=lambda b: float(embs[k] @ cent[b]))].append(k)
    cent = {g: _centroid([embs[k] for k in ks]) for g, ks in groups.items()}

    # ── 3. Gán mọi câu (kể cả câu ngắn) ───────────────────────────────────
    label_of: dict[int, int] = {k: g for g, ks in groups.items() for k in ks}
    for k, e in embs.items():
        if k not in label_of:
            label_of[k] = max(cent, key=lambda g: float(e @ cent[g]))
    labels: list[int | None] = [label_of.get(k) for k in range(len(cues))]
    for k in range(len(cues)):                    # câu không đủ tiếng: theo câu liền trước (hoặc liền sau)
        if labels[k] is None:
            labels[k] = labels[k - 1] if k > 0 and labels[k - 1] is not None else None
    for k in range(len(cues) - 1, -1, -1):
        if labels[k] is None:
            labels[k] = labels[k + 1] if k + 1 < len(cues) else next(iter(cent))

    # tên theo thứ tự xuất hiện
    order: dict[int, str] = {}
    for g in labels:
        if g not in order:
            order[g] = speaker_name(len(order) + 1)
    names = [order[g] for g in labels]

    # ── 4. Cao độ → đoán Nam / Nữ ─────────────────────────────────────────
    smap = SpeakerMap.from_cues(cues, names)
    for g, name in order.items():
        check()
        ks = sorted(groups.get(g, [k for k, x in enumerate(labels) if x == g]), key=lambda k: -cues[k].duration)
        buf, total = [], 0
        for k in ks:
            w = clip(cues[k])
            buf.append(w)
            total += len(w)
            if total > 25 * SR:
                break
        wav = np.concatenate(buf) if buf else np.zeros(0, dtype=np.float32)
        info = smap.speakers[name]
        info.f0, info.df = _pitch(wav), _dispersion(wav)
        info.gender = gender_of(info.f0, info.df)
    if on_progress:
        on_progress(1.0)
    if log:
        desc = ", ".join(f"{s.name} ({s.gender or '?'}, {s.count} câu)" for s in smap.speakers.values())
        log(f"Nhận diện người nói xong: {len(smap.speakers)} người — {desc}")
    return smap


#: khoảng cách formant dưới mức này = khoang họng dài → giọng nam (kể cả nam nói giọng cao, cao độ > 210 Hz). Đo trên
#: 150 giọng mẫu: chưa có giọng nữ nào < 1100; nam nói cao trong video thật: 1038 (audit_out/speaker_gender)
MALE_DF = 1100.0
FEMALE_DF = 1250.0


def gender_of(f0: float, df: float = 0.0) -> str:
    """Đoán Nam / Nữ: cao độ rõ ràng trầm (< 165 Hz) → Nam; cao (≥ 210 Hz) → Nữ, TRỪ KHI khoang họng dài hẳn (ΔF <
    ``MALE_DF``) → nam nói giọng cao. Vùng giữa (165–210 Hz) → theo ΔF, chưa rõ thì không đoán (người dùng chọn)."""
    if 0 < f0 < 165:
        return "Nam"
    if 0 < df < MALE_DF:
        return "Nam"
    if f0 >= 210:
        return "Nữ"
    if df > FEMALE_DF:
        return "Nữ"
    return ""


def guess_gender(info: SpeakerInfo) -> str:
    """Giới để gán giọng: đã biết (máy đoán / người dùng chọn) → theo đó; chưa rõ → nghiêng theo cao độ."""
    if info.gender:
        return info.gender
    if info.f0 > 0:
        return "Nam" if info.f0 < 187 else "Nữ"
    return ""


def assign_voices(smap: SpeakerMap | None, male: list, female: list) -> list[dict]:
    """Giọng của từng người nói trong video NÀY: giọng chọn riêng cho video (``smap.voices``) trước; còn lại theo
    Nam / Nữ — người nói nhiều nhất nhận giọng ưu tiên 1 của giới mình, người kế nhận giọng 2… (mỗi giọng một người).
    Hết giọng ưu tiên / không rõ giới → chưa gán (``suggest_voices`` tự chọn giọng khác cùng giới)."""
    if smap is None or not smap.speakers:
        return []
    got: dict[str, dict] = {nm: {**e, "name": nm} for nm, e in smap.voices.items()
                            if nm in smap.speakers and e.get("voice_id")}
    used = {e["voice_id"] for e in got.values()}
    queue = {"Nam": [e for e in male or [] if isinstance(e, dict) and e.get("voice_id")],
             "Nữ": [e for e in female or [] if isinstance(e, dict) and e.get("voice_id")]}
    for info in sorted(smap.speakers.values(), key=lambda i: -i.seconds):
        if info.name in got:
            continue
        pick = next((e for e in queue.get(guess_gender(info), []) if e["voice_id"] not in used), None)
        if pick is not None:
            got[info.name] = {"speed": 0.0, **pick, "name": info.name}
            used.add(pick["voice_id"])
    return [got[nm] for nm in smap.names() if nm in got]


def priority_slot(entry: dict, male: list, female: list) -> str:
    """«Nam 1» / «Nữ 2»… nếu giọng nằm trong danh sách ưu tiên, «» nếu không."""
    for label, lst in (("Nam", male), ("Nữ", female)):
        for k, e in enumerate(lst or [], 1):
            if isinstance(e, dict) and e.get("voice_id") == entry.get("voice_id") and \
                    (e.get("provider") or "") == (entry.get("provider") or ""):
                return f"{label} {k}"
    return ""


def video_voices(smap: SpeakerMap | None, male: list, female: list, pool: list | None, *, provider: str, locale: str,
                 main_voice: str) -> list[dict]:
    """Giọng cả video (lúc dựng và trên bảng người nói — cùng một cách): giọng chọn riêng + giọng ưu tiên
    (``assign_voices``), người còn thiếu → máy chọn giọng cùng giới chưa ai dùng (``pool``: danh sách giọng của engine)."""
    voices = assign_voices(smap, male, female)
    if smap is not None and pool and len(voices) < len(smap.speakers):
        voices = suggest_voices([smap.speakers[n] for n in smap.names()], pool, provider=provider, locale=locale,
                                main_voice=main_voice, existing=voices)
    return voices


def suggest_voices(speakers: list[SpeakerInfo], voices: list, *, provider: str, locale: str, main_voice: str,
                   existing: list[dict] | None = None) -> list[dict]:
    """Gợi ý giọng cho người nói chưa có giọng: đúng ngôn ngữ của giọng chính, Nam → giọng nam, Nữ → giọng nữ,
    mỗi người một giọng khác nhau; hết giọng (vd. Edge tiếng Việt chỉ có 2) thì dùng lại nhưng đổi cao độ cho dễ phân biệt.
    Trả về danh sách mục ``speaker_voices`` đầy đủ (giữ nguyên mục người dùng đã chọn)."""
    out = [dict(sv) for sv in (existing or []) if isinstance(sv, dict) and sv.get("name")]
    have = {sv["name"] for sv in out if sv.get("voice_id")}
    lang = (locale or "").split("-")[0].lower()
    if provider == "Edge TTS" and locale:
        pool = [v for v in voices if v.locale == locale] or [v for v in voices if v.locale.lower().startswith(lang)]
    else:
        pool = list(voices)

    def g(v) -> str:
        x = (v.gender or "").lower()
        return "Nam" if x.startswith("m") else "Nữ" if x.startswith("f") else ""

    main = next((v for v in pool if v.id == main_voice), None)
    use_count: dict[str, int] = {}
    for sv in out:
        if sv.get("voice_id") and (sv.get("provider") or provider) == provider:
            use_count[sv["voice_id"]] = use_count.get(sv["voice_id"], 0) + 1
    alt = 0
    for sp in speakers:
        if sp.name in have:
            continue
        known = guess_gender(sp)
        want = known
        if not want:                      # không đoán được: xen kẽ nam / nữ
            want = ("Nữ", "Nam")[alt % 2]
            alt += 1
        same = [v for v in pool if g(v) == want]
        cands = ([main] if main is not None and g(main) in (want, "") else []) + same + pool
        uniq: dict[str, object] = {}
        for v in cands:                           # bỏ trùng, giữ thứ tự ưu tiên
            uniq.setdefault(v.id, v)
        cands = list(uniq.values())
        if not cands:
            continue
        rank = {v.id: k for k, v in enumerate(cands)}
        # đúng giới tính trước (nếu đoán được), rồi giọng ít người dùng nhất — trùng giọng thì đổi cao độ bên dưới
        pick = min(cands, key=lambda v: (bool(known) and g(v) != known, use_count.get(v.id, 0), rank[v.id]))
        n = use_count.get(pick.id, 0)
        use_count[pick.id] = n + 1
        entry = {"name": sp.name, "provider": provider, "locale": pick.locale or locale, "voice_id": pick.id,
                 "voice_name": pick.name, "speed": 0.0}
        if n and provider == "Edge TTS":
            entry["pitch"] = (15, -15, 30, -30)[(n - 1) % 4]     # trùng giọng → đổi cao độ
        out = [sv for sv in out if sv.get("name") != sp.name] + [entry]
    return out


def _centroid(vs):
    import numpy as np

    c = np.mean(vs, axis=0)
    return c / (np.linalg.norm(c) + 1e-9)
