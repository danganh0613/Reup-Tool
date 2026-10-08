"""Đọc phụ đề in sẵn trên video (hardsub — Douyin rất hay có) bằng PaddleOCR → phụ đề gốc có mốc thời gian.

OCR ra đúng từng chữ creator gõ, kể cả tên riêng / tên con vật hiếm mà ASR hay nghe sai (đo trên video mẫu:
SenseVoice nghe 双角犀鸟 thành «双脚新娘», OCR đọc đúng). Vùng chữ dùng lại ``hardsub.ensure_region``.

- Chỉ OCR khi CHỮ đổi: so mặt nạ «điểm giống chữ» (sáng có viền tối — ``hardsub._text_mask``) giữa các khung, không so
  cả dải (nền video sau chữ đổi liên tục: so cả dải thì phải OCR 877/1187 khung, mất 6 phút cho video 2 phút).
- Chữ phồn thể (豬 …) → giản thể (OpenCC t2s).
- Mốc: chữ hiện trên màn hình trễ / sớm hơn tiếng nói 0,1–0,6 s → khớp chữ OCR với chữ SenseVoice (mốc từng chữ)
  rồi lấy mốc TIẾNG NÓI cho từng câu (cùng kiểu khớp chữ như tách đoạn đọc gộp Gemini). Không có FunASR → giữ mốc OCR.
Model PP-OCRv5 mobile (~22 MB) tải về models/paddlex.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import subprocess
import threading
from collections.abc import Callable

import numpy as np

from app.config.paths import MODELS_DIR
from app.core.platform import NO_WINDOW
from app.core.state import VideoItem
from app.services.ffmpeg import FFmpeg, RenderCancelled
from app.services.srt_utils import Cue, wrap_lines
from app.services.whisper_stt import STTResult

logger = logging.getLogger(__name__)

FPS = 10
CHANGE = 0.30            # mặt nạ chữ khác nhau ≥ 30 % (Jaccard) → chữ đổi → OCR lại
RECHECK = 20             # dù không thấy đổi, cứ 2 s OCR lại một lần cho chắc (chữ không có viền tối)
MIN_SCORE = 0.6          # bỏ dòng OCR không chắc
SAME = 0.8               # hai chữ giống ≥ 80 % → cùng một câu (OCR dao động một ký tự)
MIN_DUR = 0.3
_HAN = re.compile(r"[一-鿿]")
_ocr = None
_cc = None
_lock = threading.Lock()


def _load(log: Callable[[str], None] | None = None):
    global _ocr
    with _lock:
        if _ocr is None:
            os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(MODELS_DIR / "paddlex"))
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import PaddleOCR

            if log:
                log("Nạp PaddleOCR (PP-OCRv5 mobile)… lần đầu tải model ~22 MB")
            # enable_mkldnn=False: Paddle 3.x trên Windows CPU bật oneDNN thì lỗi «ConvertPirAttribute2RuntimeAttribute»
            _ocr = PaddleOCR(lang="ch", text_detection_model_name="PP-OCRv5_mobile_det",
                             text_recognition_model_name="PP-OCRv5_mobile_rec", use_doc_orientation_classify=False,
                             use_doc_unwarping=False, use_textline_orientation=False, device="cpu",
                             enable_mkldnn=False)
        return _ocr


def to_simplified(text: str) -> str:
    global _cc
    try:
        if _cc is None:
            from opencc import OpenCC

            _cc = OpenCC("t2s")
        return _cc.convert(text)
    except Exception:                         # noqa: BLE001 — thiếu OpenCC thì giữ nguyên
        return text


def _read(ocr, img: np.ndarray) -> str:
    lines = []
    for r in ocr.predict(img):
        for tx, sc in zip(r.get("rec_texts") or [], r.get("rec_scores") or []):
            if float(sc) >= MIN_SCORE and tx.strip():
                lines.append(tx.strip())
    return to_simplified(" ".join(lines))


def merge(frames: list[tuple[float, str]], dt: float) -> list[list]:
    """(giây, chữ) theo thứ tự → đoạn liền nhau cùng chữ (giống ≥ ``SAME`` coi như một — OCR dao động một ký tự)."""
    segs: list[list] = []
    for t, text in frames:
        if segs and (segs[-1][0] == text or (text and segs[-1][0]
                                             and difflib.SequenceMatcher(None, segs[-1][0], text).ratio() >= SAME)):
            segs[-1][2] = t + dt
            if len(text) > len(segs[-1][0]):          # giữ bản đọc dài / đủ hơn
                segs[-1][0] = text
        else:
            segs.append([text, t, t + dt])
    return segs


def segments_to_cues(segs: list[list]) -> list[Cue]:
    """[[chữ, giây đầu, giây cuối], …] (chữ rỗng = không có phụ đề) → Cue, bỏ đoạn quá ngắn."""
    cues: list[Cue] = []
    for text, s, e in segs:
        if text and e - s >= MIN_DUR:
            cues.append(Cue(len(cues) + 1, s, e, wrap_lines(text)))
    return cues


def retime(cues: list[Cue], tokens: list[tuple[str, float, float]], tail: float = 0.25) -> tuple[list[Cue], int]:
    """Lấy mốc TIẾNG NÓI cho từng câu OCR: khớp chữ Hán của OCR với chữ SenseVoice (mốc từng chữ); câu có ≥ 1/3 số
    chữ khớp được → đầu = chữ khớp đầu, cuối = chữ khớp cuối + ``tail``. Câu không khớp giữ mốc OCR. → (câu, số câu
    đã dời)."""
    oc = [(ch, k) for k, c in enumerate(cues) for ch in c.plain if _HAN.match(ch)]
    sv = [(ch, s, e) for ch, s, e in tokens if _HAN.match(ch)]
    if not oc or not sv:
        return cues, 0
    hit: dict[int, list[tuple[float, float]]] = {}
    sm = difflib.SequenceMatcher(None, [x[0] for x in oc], [x[0] for x in sv], autojunk=False)
    for blk in sm.get_matching_blocks():
        for j in range(blk.size):
            _ch, s, e = sv[blk.b + j]
            hit.setdefault(oc[blk.a + j][1], []).append((s, e))
    out: list[Cue] = []
    moved = 0
    for k, c in enumerate(cues):
        n = sum(1 for ch in c.plain if _HAN.match(ch))
        h = hit.get(k, [])
        if n and len(h) >= max(1, n / 3):
            out.append(Cue(c.index, min(s for s, _e in h), max(e for _s, e in h) + tail, c.text, dict(c.meta)))
            moved += 1
        else:
            out.append(Cue(c.index, c.start, c.end, c.text, dict(c.meta)))
    for a, b in zip(out, out[1:]):                    # giữ thứ tự, không chồng câu sau
        if b.start < a.start:
            b.start = a.start + 0.05
        a.end = max(a.start + 0.2, min(a.end, b.start - 0.02))
    return out, moved


def transcribe(item: VideoItem, ffmpeg: FFmpeg, *, duration: float, speech_timing: bool = True,
               on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] | None = None,
               cancel: threading.Event | None = None) -> STTResult:
    from app.services import hardsub

    info = ffmpeg.probe(item.path)
    reg = hardsub.ensure_region(item, ffmpeg, duration, log=log)
    if not reg:
        raise RuntimeError("Không thấy phụ đề in sẵn trên video — chọn SenseVoice / Whisper để nhận diện giọng nói.")
    W, H = info.width, info.height
    pad = 0.01                                          # nới dải chữ một chút trên / dưới
    y0 = max(0, int((reg["y"] - pad) * H))
    h = min(H - y0, int((reg["h"] + 2 * pad) * H)) // 2 * 2
    x0, w = int(reg["x"] * W), int(reg["w"] * W) // 2 * 2
    scale = min(1.0, 1280 / max(1, w))
    ow, oh = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    ocr = _load(log)
    cmd = [ffmpeg.ffmpeg, "-v", "error", "-i", item.path, "-vf",
           f"crop={w}:{h}:{x0}:{y0},scale={ow}:{oh},fps={FPS}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    size = ow * oh * 3
    frames: list[tuple[float, str]] = []
    last_mask = None
    since = 0
    text = ""
    calls = 0
    i = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise RenderCancelled("Đã huỷ đọc phụ đề")
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            img = np.frombuffer(buf, np.uint8).reshape(oh, ow, 3)
            mask = hardsub._text_mask(img.mean(axis=2)[::2, ::2])
            if last_mask is None:
                changed = True
            else:
                union = int((mask | last_mask).sum())
                changed = union > 0 and int((mask ^ last_mask).sum()) / union >= CHANGE
            if changed or since >= RECHECK:
                text = _read(ocr, img) if mask.any() or changed else ""
                last_mask, since = mask, 0
                calls += 1
            else:
                since += 1
            frames.append((i / FPS, text))
            i += 1
            if on_progress and duration > 0 and i % FPS == 0:
                on_progress(min(0.85, 0.85 * i / FPS / duration))
    finally:
        proc.stdout.close()
        proc.wait()
    cues = segments_to_cues(merge(frames, 1.0 / FPS))
    if log:
        log(f"OCR phụ đề in sẵn: {len(cues)} câu · {calls} lượt OCR / {i} khung")
    if speech_timing and cues:
        try:
            from app.services.asr_funasr import recognize_tokens

            tokens, _lang, _total = recognize_tokens(item.path, ffmpeg, language="zh", log=log, cancel=cancel)
            cues, moved = retime(cues, tokens)
            if log:
                log(f"Mốc theo tiếng nói (SenseVoice): dời {moved}/{len(cues)} câu")
        except RenderCancelled:
            raise
        except Exception as exc:                    # noqa: BLE001 — thiếu FunASR / lỗi → giữ mốc OCR
            logger.warning("Không lấy được mốc tiếng nói cho OCR: %s", exc)
    if on_progress:
        on_progress(1.0)
    return STTResult(cues=cues, language="zh", language_probability=1.0)
