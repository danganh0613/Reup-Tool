"""Kiểm chất lượng từng câu TTS vừa đọc → engine đọc lại câu hỏng (VieNeu chạy local nên đọc lại không tốn tiền).

Ngưỡng hiệu chỉnh trên 276 lần đọc VieNeu có nhãn Whisper (audit_out/qc_calib): câu đọc đúng mất 0,15–0,26 s/tiếng;
dài hơn 1,7 × dự kiến + 0,3 s thì 95% là đọc lặp / nói thêm; câu bắt đầu đột ngột (không có lặng đầu) sai 31%
(vào êm: 9%); câu hết file khi còn đang kêu thì sai cả 3/3. Whisper nghe lại (tuỳ chọn) bắt thêm chữ đọc méo.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.services.tts import postprocess
from app.services.tts.normalize import is_vietnamese, normalize_vi_text, spoken, syllable_count

logger = logging.getLogger(__name__)

QC_VERSION = "qc1"                  # đổi khi đổi cách kiểm → file cache được kiểm lại
SEC_PER_SYL = 0.20
SEC_PER_PAUSE = 0.25
HARD = 3                            # điểm ≥ HARD = hỏng thật (đọc lặp, cắt cụt, sai chữ…); dưới = lỗi nhẹ hậu kỳ tự sửa
_HALLU = re.compile(r"subscribe|đăng ký kênh|kênh .{0,30}(để|đừng) (không )?bỏ lỡ|video hấp dẫn", re.I)


@dataclass
class Verdict:
    score: int = 0
    problems: list[str] = field(default_factory=list)
    speech: float = 0.0             # giây có tiếng
    expected: float = 0.0           # giây ước lượng theo số tiếng
    heard: str = ""                 # Whisper nghe được (nếu bật)

    @property
    def ok(self) -> bool:
        return self.score == 0

    def add(self, points: int, what: str) -> None:
        self.score += points
        self.problems.append(what)


def expected_duration(text: str, speed: float = 1.0) -> float:
    """Thời lượng đọc ước lượng (giây, chưa tính lặng đầu/cuối) ở tốc độ gốc của engine."""
    pauses = len(re.findall(r"[,;:]", text)) + len(re.findall(r"[.!?…](?=\s*\S)", text))
    return (SEC_PER_SYL * syllable_count(text) + SEC_PER_PAUSE * pauses) / max(0.5, speed)


def analyse(a: np.ndarray, rate: int, text: str, *, speed: float = 1.0) -> Verdict:
    """Các phép kiểm nhanh (không cần model): dài/ngắn bất thường, cụm âm lạ cuối câu, vào/ra đột ngột, lặng dài."""
    v = Verdict(expected=expected_duration(text, speed))
    sh = postprocess.shape(a, rate)
    if sh is None:
        v.add(HARD, "không có tiếng")
        return v
    last = sh.burst_from - 1 if sh.burst_from is not None else sh.last
    v.speech = (last - sh.first + 1) * postprocess.FRAME
    n5 = max(1, int(0.005 * rate))
    lead, trail = sh.first * postprocess.FRAME, (sh.n - 1 - last) * postprocess.FRAME
    if v.speech > 1.7 * v.expected + 0.3:
        v.add(HARD, f"dài bất thường {v.speech:.1f}s (dự kiến ~{v.expected:.1f}s): đọc lặp / nói thêm?")
    elif v.speech < 0.35 * v.expected - 0.1:
        v.add(HARD, f"ngắn bất thường {v.speech:.1f}s (dự kiến ~{v.expected:.1f}s): nuốt chữ?")
    if sh.burst_from is None and trail < 0.02 and float(np.abs(a[-n5:]).max()) > 0.05:
        v.add(HARD, "hết file khi còn đang đọc (cắt cụt)")
    # nhiều câu / nhiều đoạn (Dựng truyện): VieNeu tự nghỉ 0,5–0,7 s giữa câu → nới ngưỡng
    if sh.max_gap > (1.5 if re.search(r"[.!?…]\s+\S|\n", text) else 0.9):
        v.add(HARD, f"lặng {sh.max_gap:.1f}s giữa câu")
    if sh.burst_from is not None:
        v.add(1, f"cụm âm lạ cuối câu {sh.burst_len * 1000:.0f} ms")
    if lead < 0.01 and float(np.abs(a[:n5]).max()) > 0.05:
        v.add(1, "vào đột ngột (dễ mất chữ đầu)")
    return v


# ── Whisper nghe lại ─────────────────────────────────────────────────────

_asr_lock = threading.Lock()
_asr_sizes = ("large-v3-turbo", "small", "base")


def _local_whisper() -> str | None:
    from app.config.paths import MODELS_DIR

    for size in _asr_sizes:
        if any(MODELS_DIR.glob(f"models--*faster-whisper-{size}*")):
            return size
    return None


def asr_available(require_gpu: bool = True) -> bool:
    """Có model Whisper sẵn trên máy (không tải mới) và — nếu ``require_gpu`` — có GPU CUDA."""
    if _local_whisper() is None:
        return False
    if not require_gpu:
        return True
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _to16k(a: np.ndarray, rate: int) -> np.ndarray:
    if rate == 16000:
        return a.astype(np.float32)
    try:
        import soxr

        return soxr.resample(a, rate, 16000).astype(np.float32)
    except Exception:
        n = int(len(a) * 16000 / rate)
        return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)


def _words(text: str) -> str:
    t = spoken(normalize_vi_text(text)) if is_vietnamese(text) else text
    return " ".join(re.findall(r"[^\W_]+", t.lower()))


def asr_check(a: np.ndarray, rate: int, text: str, v: Verdict, compute: str = "auto") -> None:
    """Whisper nghe lại câu vừa đọc, so với chữ gốc (đã đổi số → chữ ở cả hai phía)."""
    size = _local_whisper()
    if size is None:
        return
    from app.services.whisper_stt import load_model

    lang = "vi" if is_vietnamese(text) else None
    with _asr_lock:                                    # một model dùng chung giữa các luồng đọc
        model = load_model(size, "auto", compute)
        segs, _info = model.transcribe(_to16k(a, rate), language=lang, beam_size=5, vad_filter=False,
                                       condition_on_previous_text=False)
        v.heard = " ".join(s.text.strip() for s in segs).strip()
    ref, hyp = _words(text), _words(v.heard)
    ratio = difflib.SequenceMatcher(None, ref, hyp).ratio()
    if _HALLU.search(v.heard):
        v.add(HARD, f"Whisper không nghe ra lời (méo / lạ): «{v.heard[:40]}»")
    elif len(hyp.split()) >= 1.6 * max(1, len(ref.split())) and ratio < 0.85:
        v.add(HARD, f"đọc lặp / thêm chữ: «{v.heard[:60]}»")
    elif ratio < 0.8:
        v.add(HARD, f"đọc sai chữ (khớp {ratio:.0%}): «{v.heard[:60]}»")
    elif ref and hyp and ref.split()[0] != hyp.split()[0]:
        v.add(1, f"chữ đầu nghe thành «{hyp.split()[0]}»")      # câu vào đột ngột hay méo chữ đầu («Bắt là» → «Tất là»)


# ── File ─────────────────────────────────────────────────────────────────

def check_file(path: Path, text: str, *, speed: float = 1.0, asr: bool = False, compute: str = "auto") -> Verdict | None:
    """Kiểm một file câu (WAV). Không đọc được (mp3…) → None (không kiểm)."""
    r = postprocess.read_wav_mono16(Path(path))
    if r is None:
        return None
    pcm, rate = r
    a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
    v = analyse(a, rate, text, speed=speed)
    if asr and v.score < HARD:                         # hỏng rõ rồi thì khỏi tốn công nghe
        try:
            asr_check(a, rate, text, v, compute)
        except Exception as exc:
            logger.info("Whisper nghe lại lỗi (bỏ qua): %s", exc)
    return v


def _mark_path(path: Path) -> Path:
    return path.with_name(path.name + ".qc")


def mark(path: Path, v: Verdict | None, *, qc: bool, asr: bool, tries: int = 1) -> None:
    """Ghi chú bên cạnh file cache: đã làm sạch / đã kiểm (kiểu gì) / kết quả — lần sau khỏi kiểm lại."""
    data = {"v": QC_VERSION, "qc": qc, "asr": asr, "tries": tries,
            "score": v.score if v else 0, "problems": v.problems if v else [], "heard": v.heard if v else ""}
    try:
        _mark_path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def read_mark(path: Path) -> dict | None:
    try:
        d = json.loads(_mark_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("v") == QC_VERSION else None
